"""Spreadsheets for MemuNote (Round 31): ``.xlsx``/``.xlsm`` and ``.ods`` → rows of strings (Round 40: ``.xlsx`` also
formulas, column widths and merges for the editable grid; writing is formats/xlsx_edit.py, ``.ods`` stays read-only).

Pure stdlib (``zipfile`` + ``xml.etree.iterparse``): no openpyxl. Values come out as the text
a user would expect to see: shared/inline strings, numbers without a trailing ``.0``, booleans
as TRUE/FALSE, date-formatted serials as ``YYYY-MM-DD`` (``YYYY-MM-DD HH:MM`` with a time part),
formula cells as their cached value. Big sheets are capped (:data:`MAX_CELLS`), with ``truncated``.
"""
from __future__ import annotations

import datetime as _dt
import io
import re
import zipfile
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree as ET

MAX_CELLS = 2_000_000
MAX_COLS = 2000
MAX_SHEETS = 64

_NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_NS_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_DATE_IDS = set(range(14, 23)) | {27, 30, 36, 45, 46, 47, 50, 57}


def _check_no_entities(data: bytes) -> bytes:
    """Refuse <!ENTITY> declarations: a crafted .xlsx/.ods/.epub/.fb2 ("billion laughs") would expand to gigabytes
    in memory. Declarations can only sit in the DOCTYPE before the root element. Round 31 note-core finisher."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    head = data[:1 << 20].replace(b"\x00", b"")
    if b"<!DOCTYPE" in head.upper() and b"<!ENTITY" in head.upper():
        raise ValueError("XML entity declarations are not allowed")
    return data


def safe_iterparse(data: bytes):
    return ET.iterparse(io.BytesIO(_check_no_entities(data)))


def safe_fromstring(data):
    return ET.fromstring(_check_no_entities(data))


def _col_index(ref: str) -> int:
    n = 0
    for ch in ref:
        if "A" <= ch <= "Z":
            n = n * 26 + ord(ch) - 64
        elif "a" <= ch <= "z":
            n = n * 26 + ord(ch) - 96
        else:
            break
    return n - 1


def _num(text: str) -> str:
    try:
        value = float(text)
    except ValueError:
        return text
    if value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    return repr(value) if "e" in repr(value) else ("%.10f" % value).rstrip("0").rstrip(".")


def _date(serial: str, system1904: bool = False) -> str:
    try:
        value = float(serial)
    except ValueError:
        return serial
    base = _dt.datetime(1904, 1, 1) if system1904 else _dt.datetime(1899, 12, 30)
    try:
        moment = base + _dt.timedelta(days=value)
    except OverflowError:
        return serial
    if abs(value - int(value)) < 1e-9:
        return moment.strftime("%Y-%m-%d")
    return moment.strftime("%Y-%m-%d %H:%M")


def _text_of(el) -> str:
    """Concatenated <t> text of a shared/inline string (rich runs included, phonetic runs skipped)."""
    parts = []
    for node in el.iter():
        if node.tag == _NS_MAIN + "rPh":
            continue
        if node.tag == _NS_MAIN + "t" and node.text:
            parts.append(node.text)
    return "".join(parts)


def read_xlsx(data: bytes) -> Dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        shared: List[str] = []
        if "xl/sharedStrings.xml" in names:
            for _ev, el in safe_iterparse(z.read("xl/sharedStrings.xml")):
                if el.tag == _NS_MAIN + "si":
                    shared.append(_text_of(el))
                    el.clear()
        date_styles: List[bool] = []
        looks: List[Dict[str, str]] = []
        if "xl/styles.xml" in names:
            root = safe_fromstring(z.read("xl/styles.xml"))
            custom = {}
            for fmt in root.iter(_NS_MAIN + "numFmt"):
                code = re.sub(r'"[^"]*"|\[[^\]]*\]', "", fmt.get("formatCode", "")).lower()
                custom[int(fmt.get("numFmtId", "0"))] = bool(re.search(r"[dy]|m{1,2}[^a-z]*[dy]", code)) and "0.0" not in code
            xfs = root.find(_NS_MAIN + "cellXfs")
            for xf in (xfs if xfs is not None else []):
                fid = int(xf.get("numFmtId", "0"))
                date_styles.append(fid in _DATE_IDS or custom.get(fid, False))
            looks = _looks(root, xfs)
        workbook = safe_fromstring(z.read("xl/workbook.xml"))
        pr = workbook.find(_NS_MAIN + "workbookPr")
        system1904 = pr is not None and pr.get("date1904") in ("1", "true")
        rels = {}
        if "xl/_rels/workbook.xml.rels" in names:
            for rel in safe_fromstring(z.read("xl/_rels/workbook.xml.rels")).iter(_NS_PKG_REL + "Relationship"):
                target = rel.get("Target", "")
                target = target.lstrip("/") if target.startswith("/") else "xl/" + target
                rels[rel.get("Id")] = target
        sheets = []
        budget = MAX_CELLS
        for sheet in workbook.iter(_NS_MAIN + "sheet"):
            if len(sheets) >= MAX_SHEETS:
                break
            name = sheet.get("name", f"Sheet{len(sheets) + 1}")
            target = rels.get(sheet.get(_NS_REL + "id"), "")
            if target not in names:
                continue
            rows: List[List[str]] = []
            truncated = False
            formulas: Dict[str, str] = {}       # Round 40 (r40-note): "row,col" → formula (no "="), for the editor
            shared_f: Dict[str, Any] = {}       # si → (formula, row, col) of a shared formula's master
            widths: Dict[int, int] = {}
            merges: List[str] = []
            cell_styles: Dict[str, int] = {}    # "row,col" → index into the workbook's "looks" (display only)
            for _ev, el in safe_iterparse(z.read(target)):
                if el.tag == _NS_MAIN + "col":
                    try:
                        px = int(float(el.get("width", "0")) * 7 + 5)
                        for ci in range(int(el.get("min", "1")) - 1, min(int(el.get("max", "1")), MAX_COLS)):
                            widths[ci] = px
                    except ValueError:
                        pass
                    continue
                if el.tag == _NS_MAIN + "mergeCell" and len(merges) < 2000:
                    merges.append(el.get("ref", ""))
                    continue
                if el.tag != _NS_MAIN + "row":
                    continue
                if budget <= 0:
                    truncated = True
                    el.clear()
                    break
                try:
                    r_index = int(el.get("r", "0")) - 1
                except ValueError:
                    r_index = -1
                while r_index > len(rows) and len(rows) < 1_000_000:
                    rows.append([])
                row: List[str] = []
                ri = len(rows)                  # where this row lands (rows without r= / out of order included)
                for c in el.findall(_NS_MAIN + "c"):
                    col = _col_index(c.get("r", "")) if c.get("r") else len(row)
                    if col >= MAX_COLS:
                        continue
                    s_idx = c.get("s")
                    if s_idx and s_idx != "0" and len(cell_styles) < 300_000 and s_idx.isdigit() and int(s_idx) < len(looks) and looks[int(s_idx)]:
                        cell_styles[f"{ri},{col}"] = int(s_idx)
                    kind = c.get("t", "n")
                    f = c.find(_NS_MAIN + "f")
                    if f is not None:
                        ftext = f.text or ""
                        if f.get("t") == "shared" and f.get("si") is not None:
                            if ftext:
                                shared_f[f.get("si")] = (ftext, ri, col)
                            elif f.get("si") in shared_f:
                                from .xlsx_edit import shift_formula
                                base_f, br, bc = shared_f[f.get("si")]
                                ftext = shift_formula(base_f, ri - br, col - bc)
                        if ftext:
                            formulas[f"{ri},{col}"] = ftext
                    v = c.find(_NS_MAIN + "v")
                    raw = v.text if v is not None and v.text is not None else ""
                    if kind == "s":
                        value = shared[int(raw)] if raw.isdigit() and int(raw) < len(shared) else ""
                    elif kind == "inlineStr":
                        node = c.find(_NS_MAIN + "is")
                        value = _text_of(node) if node is not None else ""
                    elif kind == "b":
                        value = "TRUE" if raw == "1" else "FALSE"
                    elif kind in ("str", "e"):
                        value = raw
                    else:
                        style = int(c.get("s", "0") or 0)
                        is_date = raw and style < len(date_styles) and date_styles[style]
                        value = _date(raw, system1904) if is_date else _num(raw) if raw else ""
                    while len(row) < col:
                        row.append("")
                    row.append(value)
                budget -= max(1, len(row))
                rows.append(row)
                el.clear()
            while rows and not any(rows[-1]):
                rows.pop()
            sheets.append({"name": name, "rows": rows, "truncated": truncated, "formulas": formulas,
                           "widths": {str(k): v for k, v in widths.items() if v > 0}, "merges": merges,
                           "styles": cell_styles})
    return {"sheets": sheets, "editable": True, "looks": looks}


_BUILTIN_FMT = {1: "0", 2: "0.00", 3: "#,##0", 4: "#,##0.00", 9: "0%", 10: "0.00%", 11: "0.00E+00"}


def _rgb(el) -> str:
    """``<color rgb="FF336699"/>`` → ``#336699`` (theme / indexed colours are not resolved: left to the grid's theme)."""
    if el is None:
        return ""
    rgb = el.get("rgb") or ""
    return "#" + rgb[-6:] if re.fullmatch(r"[0-9A-Fa-f]{6,8}", rgb) else ""


def _looks(root, xfs) -> List[Dict[str, str]]:
    """Round 40 (r40-note): how each cell style looks in the grid — bold / italic / colour / fill / alignment / the
    number format code. Only what changes the picture; the file's styles themselves are never rewritten."""
    fmts = dict(_BUILTIN_FMT)
    for fmt in root.iter(_NS_MAIN + "numFmt"):
        try:
            fmts[int(fmt.get("numFmtId", "0"))] = fmt.get("formatCode", "")
        except ValueError:
            pass
    fonts, fills = [], []
    node = root.find(_NS_MAIN + "fonts")
    for font in (node if node is not None else []):
        fonts.append({"b": font.find(_NS_MAIN + "b") is not None and (font.find(_NS_MAIN + "b").get("val") or "1") not in ("0", "false"),
                      "i": font.find(_NS_MAIN + "i") is not None and (font.find(_NS_MAIN + "i").get("val") or "1") not in ("0", "false"),
                      "color": _rgb(font.find(_NS_MAIN + "color"))})
    node = root.find(_NS_MAIN + "fills")
    for fill in (node if node is not None else []):
        pattern = fill.find(_NS_MAIN + "patternFill")
        fills.append(_rgb(pattern.find(_NS_MAIN + "fgColor")) if pattern is not None and pattern.get("patternType") == "solid" else "")
    out: List[Dict[str, str]] = []
    for xf in (xfs if xfs is not None else []):
        look: Dict[str, str] = {}
        try:
            font = fonts[int(xf.get("fontId", "0"))] if fonts else {}
            fill = fills[int(xf.get("fillId", "0"))] if fills else ""
            fid = int(xf.get("numFmtId", "0"))
        except (ValueError, IndexError):
            out.append(look)
            continue
        if font.get("b"):
            look["b"] = "1"
        if font.get("i"):
            look["i"] = "1"
        if font.get("color") and font["color"].upper() != "#000000":
            look["color"] = font["color"]
        if fill:
            look["fill"] = fill
        align = xf.find(_NS_MAIN + "alignment")
        if align is not None and align.get("horizontal") in ("left", "center", "right"):
            look["align"] = align.get("horizontal")
        if fid in fmts and fid not in _DATE_IDS and fmts[fid] not in ("General", ""):
            look["fmt"] = fmts[fid]
        out.append(look)
    return out


_NS_TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
_NS_OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
_NS_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"


def _ods_cell_text(cell) -> str:
    kind = cell.get(_NS_OFFICE + "value-type", "")
    if kind in ("float", "percentage", "currency"):
        value = cell.get(_NS_OFFICE + "value")
        if value is not None:
            return _num(value)
    if kind == "date":
        value = cell.get(_NS_OFFICE + "date-value", "")
        return value.replace("T", " ")[:16] if "T" in value and not value.endswith("T00:00:00") else value[:10]
    if kind == "boolean":
        return "TRUE" if cell.get(_NS_OFFICE + "boolean-value") == "true" else "FALSE"
    lines = []
    for p in cell.findall(_NS_TEXT + "p"):
        lines.append("".join(p.itertext()))
    return "\n".join(lines)


def read_ods(data: bytes) -> Dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = safe_fromstring(z.read("content.xml"))
    sheets = []
    budget = MAX_CELLS
    for table in root.iter(_NS_TABLE + "table"):
        if len(sheets) >= MAX_SHEETS:
            break
        rows: List[List[str]] = []
        truncated = False
        for row_el in table.iter(_NS_TABLE + "table-row"):
            repeat_rows = min(int(row_el.get(_NS_TABLE + "number-rows-repeated", "1")), 10000)
            row: List[str] = []
            for cell in row_el:
                if cell.tag not in (_NS_TABLE + "table-cell", _NS_TABLE + "covered-table-cell"):
                    continue
                repeat = min(int(cell.get(_NS_TABLE + "number-columns-repeated", "1")), MAX_COLS)
                text = _ods_cell_text(cell)
                for _ in range(repeat):
                    if len(row) >= MAX_COLS:
                        break
                    row.append(text)
            while row and not row[-1]:
                row.pop()
            for _ in range(repeat_rows if row else min(repeat_rows, 1000)):   # trailing empty rows are dropped below
                if budget <= 0:
                    truncated = True
                    break
                rows.append(list(row))
                budget -= max(1, len(row))
            if truncated:
                break
        while rows and not any(rows[-1]):
            rows.pop()
        sheets.append({"name": table.get(_NS_TABLE + "name", f"Sheet{len(sheets) + 1}"), "rows": rows, "truncated": truncated})
    return {"sheets": sheets}


def read_sheet(path: str, data: Optional[bytes] = None) -> Dict[str, Any]:
    data = data if data is not None else open(path, "rb").read()
    if path.lower().endswith(".ods"):
        return read_ods(data)
    return read_xlsx(data)


def to_csv(rows: List[List[str]], delimiter: str = ",") -> str:
    import csv
    out = io.StringIO()
    writer = csv.writer(out, delimiter=delimiter, lineterminator="\n")
    for row in rows:
        writer.writerow(row)
    return out.getvalue()
