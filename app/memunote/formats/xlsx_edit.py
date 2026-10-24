"""Round 40 (r40-note): edit cells of an ``.xlsx`` in place — only the touched cells change.

The same principle as the Round 32 ``.pptx`` writer: the package is copied entry by entry, and only the parts that
really change are rewritten — the worksheets with edited cells (string surgery on the ``<sheetData>`` rows that
hold them; every other row, ``<cols>``, merges, conditional formats … stay as they were, byte for byte), and,
when needed, ``workbook.xml`` (a new sheet, ``fullCalcOnLoad`` after formula changes), its rels,
``[Content_Types].xml`` and ``calcChain.xml`` (dropped when formulas change: a stale chain makes Excel "repair"
the file). A cell keeps its style index ``s`` (fonts, fills, borders, number formats stay); new text goes in as an
inline string, so ``sharedStrings.xml`` is never rewritten.

``changes`` = ``{"new_sheets": [{"name"}], "sheets": [{"name", "cells": {"B2": {"v": "12"}, "C2": {"f": "A2*B2",
"v": "24"}, "D9": {"v": ""}}}]}`` — ``v`` is the value as the user typed it (numbers become numbers, TRUE/FALSE
booleans, ``YYYY-MM-DD`` in a date-formatted cell a date serial, the rest text); ``f`` a formula without ``=``
with ``v`` its computed value (cached, Excel recomputes on load).
"""
from __future__ import annotations

import datetime as _dt
import io
import re
import zipfile
from typing import Any, Dict, List, Optional, Tuple
from xml.sax.saxutils import escape

from .sheets import _DATE_IDS, _NS_MAIN, _NS_PKG_REL, _NS_REL, safe_fromstring

_REF_RE = re.compile(r"^([A-Za-z]{1,3})(\d{1,7})$")
_NUM_RE = re.compile(r"^[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$")
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?$")
# a cell reference inside a formula: not part of a name / function call / sheet name, not inside "strings"
_FREF_RE = re.compile(r"(?<![A-Za-z0-9_.'$])(\$?)([A-Z]{1,3})(\$?)(\d{1,7})(?![A-Za-z0-9_(])")
MAX_SHEET_NAME = 31
_BAD_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")


class SheetEditError(ValueError):
    pass


def col_index(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + ord(ch) - 64
    return n - 1


def col_name(index: int) -> str:
    s, c = "", index + 1
    while c > 0:
        m = (c - 1) % 26
        s = chr(65 + m) + s
        c = (c - 1) // 26
    return s


def parse_ref(ref: str) -> Tuple[int, int]:
    m = _REF_RE.match(ref.strip())
    if not m:
        raise SheetEditError(f"bad cell reference: {ref!r}")
    return int(m.group(2)) - 1, col_index(m.group(1))


def shift_formula(formula: str, drow: int, dcol: int) -> str:
    """Move the relative references of a formula by (drow, dcol) — the rule of a copied / shared formula."""
    out, pos = [], 0
    for m in re.finditer(r'"(?:[^"]|"")*"', formula):   # string literals are left alone
        out.append(_shift_part(formula[pos:m.start()], drow, dcol))
        out.append(m.group(0))
        pos = m.end()
    out.append(_shift_part(formula[pos:], drow, dcol))
    return "".join(out)


def _shift_part(part: str, drow: int, dcol: int) -> str:
    def repl(m):
        c_abs, letters, r_abs, digits = m.groups()
        col = col_index(letters) + (0 if c_abs else dcol)
        row = int(digits) + (0 if r_abs else drow)
        if col < 0 or row < 1:
            return "#REF!"
        return f"{c_abs}{col_name(col)}{r_abs}{row}"
    return _FREF_RE.sub(repl, part)


def clean_sheet_name(name: str, taken: List[str]) -> str:
    base = _BAD_SHEET_CHARS.sub(" ", str(name or "")).strip().strip("'")[:MAX_SHEET_NAME] or "Sheet"
    lower = {t.lower() for t in taken}
    candidate, n = base, 2
    while candidate.lower() in lower:
        suffix = f" ({n})"
        candidate = base[:MAX_SHEET_NAME - len(suffix)] + suffix
        n += 1
    return candidate


def _attr(tag: str, name: str) -> Optional[str]:
    m = re.search(rf'\s{name}\s*=\s*"([^"]*)"', tag) or re.search(rf"\s{name}\s*=\s*'([^']*)'", tag)
    return m.group(1) if m else None


def _set_attr(tag: str, name: str, value: Optional[str]) -> str:
    """``tag`` is an opening tag (``<x a="1">`` or ``<x/>``); value None removes the attribute."""
    tag = re.sub(rf'\s{name}\s*=\s*(?:"[^"]*"|\'[^\']*\')', "", tag)
    if value is None:
        return tag
    end = len(tag) - (2 if tag.endswith("/>") else 1)
    return tag[:end] + f' {name}="{escape(value, {chr(34): "&quot;"})}"' + tag[end:]


class _Styles:
    """Which style indexes are date formats (a typed ``2026-03-01`` there becomes a date serial)."""

    def __init__(self, xml: Optional[bytes]) -> None:
        self.dates: List[bool] = []
        if not xml:
            return
        root = safe_fromstring(xml)
        custom = {}
        for fmt in root.iter(_NS_MAIN + "numFmt"):
            code = re.sub(r'"[^"]*"|\[[^\]]*\]', "", fmt.get("formatCode", "")).lower()
            custom[int(fmt.get("numFmtId", "0"))] = bool(re.search(r"[dy]|m{1,2}[^a-z]*[dy]", code)) and "0.0" not in code
        xfs = root.find(_NS_MAIN + "cellXfs")
        for xf in (xfs if xfs is not None else []):
            fid = int(xf.get("numFmtId", "0"))
            self.dates.append(fid in _DATE_IDS or custom.get(fid, False))

    def is_date(self, s: Optional[str]) -> bool:
        try:
            i = int(s or 0)
        except ValueError:
            return False
        return 0 <= i < len(self.dates) and self.dates[i]


def _date_serial(text: str, system1904: bool) -> Optional[str]:
    m = _DATE_RE.match(text.strip())
    if not m:
        return None
    try:
        moment = _dt.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4) or 0), int(m.group(5) or 0))
    except ValueError:
        return None
    base = _dt.datetime(1904, 1, 1) if system1904 else _dt.datetime(1899, 12, 30)
    days = (moment - base).total_seconds() / 86400
    return str(int(days)) if days == int(days) else ("%.10f" % days).rstrip("0")


def _cell_xml(p: str, old_open: str, ref: str, spec: Dict[str, Any], styles: _Styles, system1904: bool) -> Tuple[str, bool]:
    """New XML of one cell (keeps the old cell's attributes, style first of all) + whether it holds a formula."""
    open_tag = old_open if old_open else f"<{p}c>"
    open_tag = open_tag[:-2] + ">" if open_tag.endswith("/>") else open_tag
    open_tag = _set_attr(_set_attr(open_tag, "t", None), "cm", None)
    if _attr(open_tag, "r") != ref:
        open_tag = _set_attr(open_tag, "r", ref)
    value = str(spec.get("v") if spec.get("v") is not None else "")
    formula = str(spec.get("f") or "").lstrip("=")
    close = f"</{p}c>"
    if formula:
        f_xml = f"<{p}f>{escape(formula)}</{p}f>"
        if value == "" or value.startswith("#"):
            return (_set_attr(open_tag, "t", "e") + f_xml + f"<{p}v>{escape(value)}</{p}v>" + close if value.startswith("#")
                    else open_tag + f_xml + close), True
        if _NUM_RE.match(value):
            return open_tag + f_xml + f"<{p}v>{value}</{p}v>" + close, True
        if value in ("TRUE", "FALSE"):
            return _set_attr(open_tag, "t", "b") + f_xml + f"<{p}v>{1 if value == 'TRUE' else 0}</{p}v>" + close, True
        return _set_attr(open_tag, "t", "str") + f_xml + f"<{p}v>{escape(value)}</{p}v>" + close, True
    if value == "":
        stripped = open_tag[:-1] + "/>"
        return (stripped if _attr(open_tag, "s") else ""), False
    if _NUM_RE.match(value.strip()):
        return open_tag + f"<{p}v>{value.strip()}</{p}v>" + close, False
    if value.upper() in ("TRUE", "FALSE"):
        return _set_attr(open_tag, "t", "b") + f"<{p}v>{1 if value.upper() == 'TRUE' else 0}</{p}v>" + close, False
    if styles.is_date(_attr(open_tag, "s")):
        serial = _date_serial(value, system1904)
        if serial is not None:
            return open_tag + f"<{p}v>{serial}</{p}v>" + close, False
    space = ' xml:space="preserve"' if value != value.strip() or "\n" in value else ""
    return (_set_attr(open_tag, "t", "inlineStr") + f"<{p}is><{p}t{space}>{escape(value)}</{p}t></{p}is>" + close), False


def _iter_elems(text: str, p: str, tag: str):
    """(start, end, open_tag, ref_or_None) of each ``<tag …/>`` / ``<tag …>…</tag>`` at this level."""
    pattern = re.compile(rf"<{re.escape(p)}{tag}(?=[\s/>])[^>]*?(/>|>)", re.S)
    close = f"</{p}{tag}>"
    pos = 0
    while True:
        m = pattern.search(text, pos)
        if not m:
            return
        if m.group(1) == "/>":
            end = m.end()
        else:
            j = text.find(close, m.end())
            if j < 0:
                raise SheetEditError(f"unterminated <{tag}>")
            end = j + len(close)
        yield m.start(), end, m.group(0)
        pos = end


def patch_sheet_xml(xml: str, cells: Dict[str, Dict[str, Any]], styles: _Styles, system1904: bool) -> Tuple[str, bool]:
    """Apply ``cells`` to one worksheet XML; returns (new xml, formulas touched)."""
    m = re.search(r"<(\w+:)?worksheet\b", xml)
    if not m:
        raise SheetEditError("not a worksheet")
    p = m.group(1) or ""
    wanted: Dict[int, Dict[int, Tuple[str, Dict[str, Any]]]] = {}
    for ref, spec in cells.items():
        r, c = parse_ref(ref)
        wanted.setdefault(r + 1, {})[c] = (col_name(c) + str(r + 1), spec if isinstance(spec, dict) else {"v": spec})
    sd = re.search(rf"<{re.escape(p)}sheetData\s*/>|<{re.escape(p)}sheetData\b[^>]*>(.*?)</{re.escape(p)}sheetData>", xml, re.S)
    if not sd:
        raise SheetEditError("no sheetData")
    if sd.group(0).endswith("/>"):
        body, body_start, body_end = "", None, None
    else:
        body, body_start, body_end = sd.group(1), sd.start(1), sd.end(1)
    rows: List[Tuple[int, int, int, str]] = []     # (rnum, start, end, open_tag)
    last = 0
    for start, end, open_tag in _iter_elems(body, p, "row"):
        try:
            rnum = int(_attr(open_tag, "r") or 0)
        except ValueError:
            rnum = 0
        rnum = rnum or last + 1
        last = rnum
        rows.append((rnum, start, end, open_tag))
    formulas = False
    # shared formula masters about to be replaced: their dependents get the formula written out
    masters: Dict[str, Tuple[str, int, int]] = {}
    for rnum, start, end, _open in rows:
        if rnum not in wanted:
            continue
        for cs, ce, copen in _iter_elems(body[start:end], p, "c"):
            cref = _attr(copen, "r")
            if not cref:
                continue
            rr, cc = parse_ref(cref)
            if cc in wanted[rnum]:
                cell = body[start + cs:start + ce]
                fm = re.search(rf"<{re.escape(p)}f\b([^>]*)>(.*?)</{re.escape(p)}f>", cell, re.S)
                if fm and _attr("<f" + fm.group(1) + ">", "t") == "shared" and _attr("<f" + fm.group(1) + ">", "ref"):
                    masters[_attr("<f" + fm.group(1) + ">", "si") or ""] = (fm.group(2), rr, cc)
    pieces: List[str] = []
    pos = 0
    pending = sorted(wanted)

    def new_row(rnum: int) -> str:
        cells_xml = []
        for c in sorted(wanted[rnum]):
            ref, spec = wanted[rnum][c]
            x, isf = _cell_xml(p, "", ref, spec, styles, system1904)
            nonlocal formulas
            formulas = formulas or isf
            cells_xml.append(x)
        inner = "".join(cells_xml)
        return f'<{p}row r="{rnum}">{inner}</{p}row>' if inner else ""

    for rnum, start, end, open_tag in rows:
        while pending and pending[0] < rnum:
            pieces.append(new_row(pending.pop(0)))
        row_text = body[start:end]
        if masters:
            row_text = _expand_dependents(row_text, p, masters, rnum, wanted.get(rnum, {}))
        if pending and pending[0] == rnum:
            pending.pop(0)
            row_text, isf = _patch_row(row_text, p, open_tag, wanted[rnum], styles, system1904)
            formulas = formulas or isf
        pieces.append(body[pos:start])
        pieces.append(row_text)
        pos = end
    pieces.append(body[pos:])
    for rnum in pending:
        pieces.append(new_row(rnum))
    new_body = "".join(pieces)
    if masters:
        formulas = True
    if body_start is None:
        new_xml = xml[:sd.start()] + f"<{p}sheetData>{new_body}</{p}sheetData>" + xml[sd.end():]
    else:
        new_xml = xml[:body_start] + new_body + xml[body_end:]
    return _update_dimension(new_xml, p, wanted), formulas


def _expand_dependents(row_text: str, p: str, masters: Dict[str, Tuple[str, int, int]], rnum: int,
                       replaced: Dict[int, Any]) -> str:
    def repl(m):
        cell = m.group(0)
        fm = re.search(rf"<{re.escape(p)}f\b([^>]*?)(/>|>\s*</{re.escape(p)}f>)", cell)
        if not fm:
            return cell
        f_open = "<f" + fm.group(1) + ">"
        si = _attr(f_open, "si")
        if _attr(f_open, "t") != "shared" or si not in masters or _attr(f_open, "ref"):
            return cell
        cref = _attr(cell[:cell.find(">") + 1], "r")
        if not cref:
            return cell
        rr, cc = parse_ref(cref)
        if rr + 1 == rnum and cc in replaced:
            return cell
        text, mr, mc = masters[si]
        return cell[:fm.start()] + f"<{p}f>{shift_formula(_unescape(text), rr - mr, cc - mc).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')}</{p}f>" + cell[fm.end():]
    return re.sub(rf"<{re.escape(p)}c(?=[\s/>])[^>]*?(?:/>|>.*?</{re.escape(p)}c>)", repl, row_text, flags=re.S)


def _unescape(text: str) -> str:
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'").replace("&amp;", "&")


def _patch_row(row_text: str, p: str, open_tag: str, cells: Dict[int, Tuple[str, Dict[str, Any]]],
               styles: _Styles, system1904: bool) -> Tuple[str, bool]:
    inner_start = len(open_tag)
    if open_tag.endswith("/>"):
        inner, close = "", f"</{p}row>"
        open_tag = open_tag[:-2].rstrip() + ">"
    else:
        close = f"</{p}row>"
        inner = row_text[inner_start:len(row_text) - len(close)]
    # rows without a style keep their own; "spans" is only a hint and would be wrong after new cells
    open_tag = _set_attr(open_tag, "spans", None)
    todo = dict(cells)
    out: List[str] = []
    pos, col, formulas = 0, -1, False
    for cs, ce, copen in _iter_elems(inner, p, "c"):
        cref = _attr(copen, "r")
        col = parse_ref(cref)[1] if cref else col + 1
        for c in sorted(k for k in todo if k < col):
            x, isf = _cell_xml(p, "", todo[c][0], todo.pop(c)[1], styles, system1904)
            out.append(x)
            formulas = formulas or isf
        out.append(inner[pos:cs])
        old = inner[cs:ce]
        if col in todo:
            ref, spec = todo.pop(col)
            if re.search(rf"<{re.escape(p)}f\b", old):
                formulas = True
            x, isf = _cell_xml(p, copen, ref, spec, styles, system1904)
            out.append(x)
            formulas = formulas or isf
        else:
            out.append(old)
        pos = ce
    tail = inner[pos:]
    rest = []
    for c in sorted(todo):
        x, isf = _cell_xml(p, "", todo[c][0], todo[c][1], styles, system1904)
        rest.append(x)
        formulas = formulas or isf
    ext = re.search(rf"<{re.escape(p)}extLst\b", tail)
    if ext:
        tail = tail[:ext.start()] + "".join(rest) + tail[ext.start():]
    else:
        tail = tail + "".join(rest)
    return open_tag + "".join(out) + tail + close, formulas


def _update_dimension(xml: str, p: str, wanted: Dict[int, Dict[int, Any]]) -> str:
    m = re.search(rf'<{re.escape(p)}dimension\b[^>]*\bref="([^"]*)"[^>]*/>', xml)
    if not m or not wanted:
        return xml
    max_r = max(wanted)
    max_c = max(max(cols) for cols in wanted.values())
    ref = m.group(1)
    parts = ref.split(":")
    try:
        r1, c1 = parse_ref(parts[0])
        r2, c2 = parse_ref(parts[-1])
    except SheetEditError:
        return xml
    nr, nc = max(r2, max_r - 1), max(c2, max_c)
    if (nr, nc) == (r2, c2):
        return xml
    new_ref = f"{col_name(c1)}{r1 + 1}:{col_name(nc)}{nr + 1}"
    return xml[:m.start(1)] + new_ref + xml[m.end(1):]


_EMPTY_SHEET = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<dimension ref="A1"/><sheetViews><sheetView workbookViewId="0"/></sheetViews>'
                '<sheetFormatPr defaultRowHeight="15"/><sheetData/>'
                '<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" footer="0.3"/></worksheet>')


def patch_xlsx(data: bytes, changes: Dict[str, Any]) -> bytes:
    """The edited package (see the module docstring); parts that do not change are copied as they were."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        infos = z.infolist()
        parts: Dict[str, bytes] = {i.filename: z.read(i.filename) for i in infos}
    names = set(parts)
    if "xl/workbook.xml" not in names:
        raise SheetEditError("not an Excel workbook")
    workbook = parts["xl/workbook.xml"].decode("utf-8")
    rels_name = "xl/_rels/workbook.xml.rels"
    rels_xml = parts.get(rels_name, b"").decode("utf-8")
    ct_xml = parts.get("[Content_Types].xml", b"").decode("utf-8")
    wb_root = safe_fromstring(parts["xl/workbook.xml"])
    pr = wb_root.find(_NS_MAIN + "workbookPr")
    system1904 = pr is not None and pr.get("date1904") in ("1", "true")
    rel_targets: Dict[str, str] = {}
    if rels_xml:
        for rel in safe_fromstring(rels_xml.encode("utf-8")).iter(_NS_PKG_REL + "Relationship"):
            target = rel.get("Target", "")
            rel_targets[rel.get("Id", "")] = target.lstrip("/") if target.startswith("/") else "xl/" + target
    sheet_parts: Dict[str, str] = {}
    sheet_ids: List[int] = []
    for sheet in wb_root.iter(_NS_MAIN + "sheet"):
        sheet_parts[sheet.get("name", "")] = rel_targets.get(sheet.get(_NS_REL + "id", ""), "")
        try:
            sheet_ids.append(int(sheet.get("sheetId", "0")))
        except ValueError:
            pass
    workbook_changed = rels_changed = ct_changed = False
    added: Dict[str, bytes] = {}
    for new in changes.get("new_sheets") or []:
        name = clean_sheet_name((new or {}).get("name", ""), list(sheet_parts))
        n = 1
        while f"xl/worksheets/sheet{n}.xml" in names or f"xl/worksheets/sheet{n}.xml" in added:
            n += 1
        part = f"xl/worksheets/sheet{n}.xml"
        rid_n = 1
        while f'Id="rId{rid_n}"' in rels_xml:
            rid_n += 1
        rid = f"rId{rid_n}"
        rel = (f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
               f'Target="worksheets/sheet{n}.xml"/>')
        rels_xml = rels_xml.replace("</Relationships>", rel + "</Relationships>")
        rel_prefix = re.search(r'xmlns:(\w+)="http://schemas\.openxmlformats\.org/officeDocument/2006/relationships"', workbook)
        main_prefix = (re.search(r"<(\w+:)?workbook\b", workbook).group(1) or "")
        sheet_id = max(sheet_ids or [0]) + 1
        sheet_ids.append(sheet_id)
        if rel_prefix:
            rattr = f'{rel_prefix.group(1)}:id="{rid}"'
        else:
            rattr = f'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" r:id="{rid}"'
        el = f'<{main_prefix}sheet name="{escape(name, {chr(34): "&quot;"})}" sheetId="{sheet_id}" {rattr}/>'
        workbook = workbook.replace(f"</{main_prefix}sheets>", el + f"</{main_prefix}sheets>", 1)
        ct_xml = ct_xml.replace("</Types>", f'<Override PartName="/{part}" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        added[part] = _EMPTY_SHEET.encode("utf-8")
        sheet_parts[name] = part
        new["name"] = name
        workbook_changed = rels_changed = ct_changed = True
    styles = _Styles(parts.get("xl/styles.xml"))
    formulas = False
    changed_parts: Dict[str, bytes] = {}
    for sheet in changes.get("sheets") or []:
        cells = (sheet or {}).get("cells") or {}
        if not cells:
            continue
        part = sheet_parts.get(str(sheet.get("name", "")))
        if not part:
            raise SheetEditError(f"no sheet named {sheet.get('name')!r}")
        source = (changed_parts.get(part) or added.get(part) or parts[part]).decode("utf-8")
        xml, touched = patch_sheet_xml(source, cells, styles, system1904)
        formulas = formulas or touched
        changed_parts[part] = xml.encode("utf-8")
    if formulas:
        calc = re.search(r"<(\w+:)?calcPr\b[^>]*/?>", workbook)
        if calc:
            workbook = workbook[:calc.start()] + _set_attr(calc.group(0), "fullCalcOnLoad", "1") + workbook[calc.end():]
        else:
            pfx = re.search(r"<(\w+:)?workbook\b", workbook).group(1) or ""
            # CT_Workbook order: sheets, functionGroups, externalReferences, definedNames, calcPr, …
            at = -1
            for tag in ("sheets", "functionGroups", "externalReferences", "definedNames"):
                end = workbook.find(f"</{pfx}{tag}>")
                if end >= 0:
                    at = max(at, end + len(f"</{pfx}{tag}>"))
            if at >= 0:
                workbook = workbook[:at] + f'<{pfx}calcPr fullCalcOnLoad="1"/>' + workbook[at:]
        workbook_changed = True
        if "xl/calcChain.xml" in names:
            parts.pop("xl/calcChain.xml")
            rels_xml = re.sub(r'<Relationship\b[^>]*Target="/?(?:xl/)?calcChain\.xml"[^>]*/>', "", rels_xml)
            ct_xml = re.sub(r'<Override\b[^>]*PartName="/xl/calcChain\.xml"[^>]*/>', "", ct_xml)
            rels_changed = ct_changed = True
    if workbook_changed:
        changed_parts["xl/workbook.xml"] = workbook.encode("utf-8")
    if rels_changed:
        changed_parts[rels_name] = rels_xml.encode("utf-8")
    if ct_changed:
        changed_parts["[Content_Types].xml"] = ct_xml.encode("utf-8")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in infos:
            if info.filename not in parts:
                continue
            zout.writestr(info, changed_parts.get(info.filename, parts[info.filename]), compress_type=info.compress_type)
        for part, raw in added.items():
            zout.writestr(part, changed_parts.get(part, raw))
    return out.getvalue()
