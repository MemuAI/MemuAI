# -*- coding: utf-8 -*-
"""Data utilities: a universal table converter (CSV, TSV, JSON, JSONL, XLSX, ODS, YAML, XML, Markdown, HTML, SQL,
Parquet), SQL over files (DuckDB, no file or network access from the query), JSONPath, cleaning, merging, splitting,
column statistics, pivot, filter, JSON flatten and JSON diff. Also the small XLSX writer other utilities use."""
from __future__ import annotations

import csv
import io
import json
import math
import re
import statistics
import zipfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from xml.sax.saxutils import escape as _xml_escape

from .core import O, UtilError, as_int, human_size, table, utility

DATA_ACCEPT = ".csv,.tsv,.txt,.json,.jsonl,.ndjson,.xlsx,.xlsm,.ods,.yaml,.yml,.xml,.parquet,.md,.html,.htm"
PREVIEW_ROWS = 300

Table = Tuple[List[str], List[List[Any]]]

# ── XLSX writer ──────────────────────────────────────────────────────────────────────────────────────────────────────
_BAD_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")

def _x(value: Any) -> str:
    return _xml_escape(_BAD_XML.sub("", str(value)), {'"': "&quot;"})

def _col(n: int) -> str:
    out = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out

def _sheet_name(name: str, used: set) -> str:
    base = re.sub(r"[\[\]:*?/\\]", "_", str(name or "Sheet")).strip("'")[:31] or "Sheet"
    name, n = base, 2
    while name.lower() in used:
        suffix = f" ({n})"
        name = base[:31 - len(suffix)] + suffix
        n += 1
    used.add(name.lower())
    return name

def write_xlsx(sheets: Dict[str, List[List[Any]]], header: bool = True) -> bytes:
    """A small, valid XLSX: one sheet per dict item, the first row bold and frozen with an autofilter,
    numbers and booleans typed, column widths fitted to the content."""
    used: set = set()
    names = [_sheet_name(n, used) for n in sheets]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                   + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, len(names) + 1))
                   + '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                   '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/></Relationships>')
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        z.writestr("docProps/core.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                   'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
                   f'<dc:creator>MemuUtils</dc:creator><dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created></cp:coreProperties>')
        z.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                   + "".join(f'<sheet name="{_x(n)}" sheetId="{i}" r:id="rId{i}"/>' for i, n in enumerate(names, 1)) + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(names) + 1))
                   + f'<Relationship Id="rId{len(names) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr("xl/styles.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
                   '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
                   '<fill><patternFill patternType="solid"><fgColor rgb="FFEEF2F7"/><bgColor indexed="64"/></patternFill></fill></fills>'
                   '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
                   '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
                   '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
                   '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
                   '<xf numFmtId="14" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>'
                   '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
        for i, rows in enumerate(sheets.values(), 1):
            rows = [list(r) for r in rows]
            ncols = max((len(r) for r in rows), default=0)
            widths = [8] * ncols
            out = []
            for r, row in enumerate(rows):
                cells = []
                for c, value in enumerate(row):
                    ref = f"{_col(c)}{r + 1}"
                    style = ' s="1"' if header and r == 0 else ""
                    if value is None or value == "":
                        continue
                    if isinstance(value, bool):
                        cells.append(f'<c r="{ref}" t="b"{style}><v>{int(value)}</v></c>')
                        shown = "TRUE"
                    elif isinstance(value, (int, float)) and not (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
                        cells.append(f'<c r="{ref}"{style}><v>{value!r}</v></c>' if isinstance(value, float) else f'<c r="{ref}"{style}><v>{value}</v></c>')
                        shown = str(value)
                    else:
                        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
                        text = text[:32767]
                        space = ' xml:space="preserve"' if text != text.strip() or "\n" in text else ""
                        cells.append(f'<c r="{ref}" t="inlineStr"{style}><is><t{space}>{_x(text)}</t></is></c>')
                        shown = max(text.split("\n"), key=len) if text else ""
                    widths[c] = min(60, max(widths[c], int(len(shown) * 1.15) + 2))
                out.append(f'<row r="{r + 1}">{"".join(cells)}</row>')
            cols = "".join(f'<col min="{c + 1}" max="{c + 1}" width="{w}" customWidth="1"/>' for c, w in enumerate(widths))
            frozen = ('<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
                      if header and len(rows) > 1 else '<sheetViews><sheetView workbookViewId="0"/></sheetViews>')
            auto = f'<autoFilter ref="A1:{_col(max(0, ncols - 1))}{len(rows)}"/>' if header and len(rows) > 1 and ncols else ""
            z.writestr(f"xl/worksheets/sheet{i}.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                       + frozen + (f"<cols>{cols}</cols>" if cols else "") + f'<sheetData>{"".join(out)}</sheetData>{auto}</worksheet>')
    return buf.getvalue()

# ── reading tables ───────────────────────────────────────────────────────────────────────────────────────────────────
class _Tables(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: List[List[List[str]]] = []
        self.row: Optional[List[str]] = None
        self.cell: Optional[List[str]] = None
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.depth += 1
            if self.depth == 1:
                self.tables.append([])
        elif tag == "tr" and self.depth == 1:
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []
        elif tag == "br" and self.cell is not None:
            self.cell.append("\n")

    def handle_endtag(self, tag):
        if tag == "table":
            self.depth -= 1
        elif tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.row.append(re.sub(r"[ \t]+", " ", "".join(self.cell)).strip())
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if self.tables:
                self.tables[-1].append(self.row)
            self.row = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

def records_to_table(records: Sequence[Any]) -> Table:
    if records and all(isinstance(r, (list, tuple)) for r in records):
        rows = [list(r) for r in records]
        return [str(c) for c in rows[0]], rows[1:]
    columns: List[str] = []
    seen = set()
    for rec in records:
        if isinstance(rec, dict):
            for key in rec:
                if key not in seen:
                    seen.add(key)
                    columns.append(str(key))
    if not columns:
        return ["value"], [[r] for r in records]
    rows = [[(rec.get(c) if isinstance(rec, dict) else None) for c in columns] for rec in records]
    return columns, rows

def _json_table(data: Any) -> Table:
    if isinstance(data, dict):
        lists = [k for k, v in data.items() if isinstance(v, list) and v and all(isinstance(x, dict) for x in v)]
        if len(lists) == 1:
            data = data[lists[0]]
        elif data and all(isinstance(v, list) for v in data.values()) and len({len(v) for v in data.values()}) == 1:
            keys = list(data)
            return keys, [list(r) for r in zip(*data.values())]
        else:
            data = [data]
    if not isinstance(data, list):
        data = [data]
    return records_to_table(data)

def _xml_table(text: str) -> Table:
    from ..memunote.formats.sheets import safe_fromstring
    try:
        root = safe_fromstring(text.encode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise UtilError(f"XML не читается: {exc}", f"Cannot read the XML: {exc}")
    children = list(root)
    if not children:
        raise UtilError("В XML нет повторяющихся элементов", "The XML has no repeating elements")
    tags: Dict[str, int] = {}
    for ch in children:
        tags[ch.tag] = tags.get(ch.tag, 0) + 1
    item = max(tags, key=tags.get)
    records = []
    for el in root.iter(item) if tags[item] == 1 and len(children[0]) else root.findall(item):
        rec: Dict[str, Any] = {f"@{k}": v for k, v in el.attrib.items()}
        for sub in el:
            name = re.sub(r"^\{.*?\}", "", sub.tag)
            rec[name] = (sub.text or "").strip() if len(sub) == 0 else "".join(sub.itertext()).strip()
        if not len(el) and (el.text or "").strip():
            rec["value"] = el.text.strip()
        records.append(rec)
    return records_to_table(records)

def _yaml():
    try:
        import yaml  # type: ignore
        return yaml
    except ImportError:
        raise UtilError("YAML не поддерживается в этой сборке (нет PyYAML)", "YAML is not supported in this build (no PyYAML)")

def sniff(text: str) -> str:
    s = text.lstrip("\ufeff \t\r\n")
    if s[:1] in "[{":
        try:
            json.loads(s)
            return "json"
        except json.JSONDecodeError:
            lines = [l for l in s.splitlines() if l.strip()]
            if lines and all(l.strip().startswith("{") for l in lines[:20]):
                return "jsonl"
            return "json"
    if s.startswith("<"):
        return "html" if re.search(r"<table\b", s[:20000], re.I) else "xml"
    if s.startswith("|"):
        return "md"
    if re.match(r"^(---\s*\n)?[\w\"' -]+:\s", s) and "," not in s.split("\n", 1)[0] and "\t" not in s.split("\n", 1)[0]:
        return "yaml"
    return "csv"

def parse_table(text: str, fmt: str = "auto", sheet: str = "") -> Table:
    from .t_dev import load_json_lenient, parse_delimited
    fmt = fmt if fmt and fmt != "auto" else sniff(text)
    if fmt == "json":
        data, _ = load_json_lenient(text)
        return _json_table(data)
    if fmt == "jsonl":
        rows = []
        for n, line in enumerate(text.splitlines(), 1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise UtilError(f"Строка {n}: не JSON ({exc.msg})", f"Line {n}: not JSON ({exc.msg})")
        return records_to_table(rows)
    if fmt == "yaml":
        return _json_table(_yaml().safe_load(text))
    if fmt == "xml":
        return _xml_table(text)
    if fmt == "html":
        parser = _Tables()
        parser.feed(text)
        tables = [t for t in parser.tables if t]
        if not tables:
            raise UtilError("В HTML нет таблиц", "No tables in the HTML")
        idx = min(len(tables) - 1, max(0, as_int(sheet, 1) - 1)) if sheet else 0
        rows = tables[idx]
        return rows[0], rows[1:]
    if fmt == "tsv":
        rows = [r for r in csv.reader(io.StringIO(text), delimiter="\t") if r]
    else:
        rows = parse_delimited(text)
    if not rows:
        raise UtilError("Таблица пуста", "The table is empty")
    width = max(len(r) for r in rows)
    head = [c.strip() or f"column{i + 1}" for i, c in enumerate(rows[0] + [""] * (width - len(rows[0])))]
    return head, [r + [""] * (width - len(r)) for r in rows[1:]]

def _duck():
    try:
        import duckdb  # type: ignore
        return duckdb
    except ImportError:
        raise UtilError("DuckDB недоступен в этой сборке", "DuckDB is not available in this build")

def load_file_table(f, sheet: str = "", fmt: str = "auto") -> Table:
    ext = f.ext
    if ext in ("xlsx", "xlsm", "ods"):
        from ..memunote.formats import sheets as sheet_formats
        book = sheet_formats.read_sheet(str(f.path()), f.data)
        all_sheets = book.get("sheets") or []
        if not all_sheets:
            raise UtilError("В книге нет листов", "The workbook has no sheets")
        pick = all_sheets[0]
        if sheet:
            pick = next((s for s in all_sheets if str(s.get("name")).lower() == str(sheet).lower()), None) or (
                all_sheets[as_int(sheet, 1) - 1] if str(sheet).isdigit() and 0 < int(sheet) <= len(all_sheets) else all_sheets[0])
        rows = [list(r) for r in pick.get("rows") or [] if any(str(c).strip() for c in r)]
        if not rows:
            raise UtilError("Лист пуст", "The sheet is empty")
        width = max(len(r) for r in rows)
        head = [str(c).strip() or f"column{i + 1}" for i, c in enumerate(list(rows[0]) + [""] * (width - len(rows[0])))]
        return head, [r + [""] * (width - len(r)) for r in rows[1:]]
    if ext == "xls":
        raise UtilError("Старый формат .xls не поддерживается — пересохраните в Excel как .xlsx", "The legacy .xls format is not supported — resave it as .xlsx")
    if ext == "parquet":
        con = _duck().connect()
        try:
            cur = con.execute("SELECT * FROM read_parquet(?)", [str(f.path())])
            cols = [d[0] for d in cur.description]
            return cols, [list(r) for r in cur.fetchall()]
        finally:
            con.close()
    guess = {"json": "json", "jsonl": "jsonl", "ndjson": "jsonl", "yaml": "yaml", "yml": "yaml", "xml": "xml", "html": "html", "htm": "html",
             "md": "md", "tsv": "tsv", "csv": "csv"}.get(ext, "auto")
    return parse_table(f.text(), fmt if fmt != "auto" else guess, sheet)

def input_table(ctx, sheet: str = "") -> Tuple[Table, str]:
    """The table of the first file, or of the pasted text; plus a base name for outputs."""
    fmt = ctx.o.get("from") or "auto"
    if ctx.files:
        f = ctx.files[0]
        return load_file_table(f, sheet or ctx.o.get("sheet") or "", fmt), f.stem
    return parse_table(ctx.need_text("Вставьте таблицу или добавьте файл", "Paste a table or add a file"), fmt, sheet), "data"

# ── values and writers ───────────────────────────────────────────────────────────────────────────────────────────────
_NUM = re.compile(r"^[+-]?(\d+([.,]\d*)?|[.,]\d+)([eE][+-]?\d+)?$")

def typed(value: Any) -> Any:
    """``"12"`` → 12, ``"1,5"`` → 1.5, ``"true"`` → True; anything else unchanged."""
    if not isinstance(value, str):
        return value
    s = value.strip()
    if not s:
        return value
    if _NUM.match(s) and not (len(s) > 1 and s[0] == "0" and s[1].isdigit()):
        s2 = s.replace(",", ".")
        try:
            return int(s2) if re.fullmatch(r"[+-]?\d+", s2) and len(s2) < 16 else float(s2)
        except ValueError:
            return value
    low = s.lower()
    if low in ("true", "false"):
        return low == "true"
    return value

def as_records(cols: List[str], rows: List[List[Any]], infer: bool = True) -> List[Dict[str, Any]]:
    return [{c: (typed(r[i]) if infer else r[i]) if i < len(r) else None for i, c in enumerate(cols)} for r in rows]

def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)

def to_csv_text(cols, rows, delimiter: str = ",") -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=delimiter, lineterminator="\r\n")
    w.writerow(cols)
    for r in rows:
        w.writerow([_cell(v) for v in r])
    return buf.getvalue()

def to_markdown_table(cols, rows) -> str:
    def esc(v):
        return _cell(v).replace("|", "\\|").replace("\n", "<br>")
    out = ["| " + " | ".join(esc(c) for c in cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    out += ["| " + " | ".join(esc(r[i] if i < len(r) else "") for i in range(len(cols))) + " |" for r in rows]
    return "\n".join(out)

def to_html_table(cols, rows) -> str:
    from html import escape
    head = "".join(f"<th>{escape(str(c))}</th>" for c in cols)
    body = "\n".join("  <tr>" + "".join(f"<td>{escape(_cell(r[i] if i < len(r) else ''))}</td>" for i in range(len(cols))) + "</tr>" for r in rows)
    return f"<table>\n  <thead><tr>{head}</tr></thead>\n  <tbody>\n{body}\n  </tbody>\n</table>"

def _xml_tag(name: str) -> str:
    tag = re.sub(r"[^\w.-]", "_", str(name or "field"))
    return tag if re.match(r"^[A-Za-z_]", tag) else f"_{tag}"

def to_xml(cols, rows, root: str = "rows", item: str = "row") -> str:
    out = ['<?xml version="1.0" encoding="UTF-8"?>', f"<{_xml_tag(root)}>"]
    for r in rows:
        out.append(f"  <{_xml_tag(item)}>" + "".join(f"<{_xml_tag(c)}>{_x(_cell(r[i] if i < len(r) else ''))}</{_xml_tag(c)}>" for i, c in enumerate(cols)) + f"</{_xml_tag(item)}>")
    out.append(f"</{_xml_tag(root)}>")
    return "\n".join(out)

def to_sql(cols, rows, name: str = "data", create: bool = True) -> str:
    def ident(s):
        return '"' + str(s).replace('"', '""') + '"'

    def lit(v):
        v = typed(v)
        if v is None or v == "":
            return "NULL"
        if isinstance(v, bool):
            return "TRUE" if v else "FALSE"
        if isinstance(v, (int, float)):
            return repr(v)
        return "'" + _cell(v).replace("'", "''") + "'"
    out = []
    if create:
        types = []
        for i, c in enumerate(cols):
            vals = [typed(r[i]) for r in rows if i < len(r) and r[i] not in (None, "")]
            kind = "TEXT"
            if vals and all(isinstance(v, bool) for v in vals):
                kind = "BOOLEAN"
            elif vals and all(isinstance(v, int) and not isinstance(v, bool) for v in vals):
                kind = "BIGINT" if any(abs(v) > 2 ** 31 for v in vals) else "INTEGER"
            elif vals and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
                kind = "DOUBLE PRECISION"
            types.append(f"  {ident(c)} {kind}")
        out.append(f"CREATE TABLE {ident(name)} (\n" + ",\n".join(types) + "\n);")
    for k in range(0, len(rows), 500):
        chunk = rows[k:k + 500]
        out.append(f"INSERT INTO {ident(name)} ({', '.join(ident(c) for c in cols)}) VALUES\n" +
                   ",\n".join("  (" + ", ".join(lit(r[i] if i < len(r) else None) for i in range(len(cols))) + ")" for r in chunk) + ";")
    return "\n\n".join(out)

def to_yaml(data: Any) -> str:
    try:
        import yaml  # type: ignore
        return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)
    except ImportError:
        pass

    def scalar(v):
        if v is None:
            return "null"
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return repr(v)
        s = str(v)
        if not s or re.search(r"[:#\[\]{}&*!|>'\"%@`,]|^\s|\s$|^[-?]", s) or s.lower() in ("null", "true", "false", "yes", "no", "~") or _NUM.match(s):
            return json.dumps(s, ensure_ascii=False)
        return s

    def emit(v, indent):
        pad = "  " * indent
        if isinstance(v, dict):
            if not v:
                return [pad + "{}"]
            out = []
            for k, x in v.items():
                if isinstance(x, (dict, list)) and x:
                    out.append(f"{pad}{scalar(k)}:")
                    out += emit(x, indent + 1)
                else:
                    out.append(f"{pad}{scalar(k)}: {scalar(x) if not isinstance(x, (dict, list)) else ('{}' if isinstance(x, dict) else '[]')}")
            return out
        if isinstance(v, list):
            if not v:
                return [pad + "[]"]
            out = []
            for x in v:
                if isinstance(x, (dict, list)) and x:
                    sub = emit(x, indent + 1)
                    out.append(pad + "- " + sub[0].lstrip())
                    out += sub[1:]
                else:
                    out.append(pad + "- " + scalar(x))
            return out
        return [pad + scalar(v)]
    return "\n".join(emit(data, 0)) + "\n"

def write_parquet(path: Path, cols, rows) -> None:
    tmp = path.with_suffix(".tmp.csv")
    con = _duck().connect()
    try:
        tmp.write_text(to_csv_text(cols, rows), encoding="utf-8", newline="")
        con.execute(f"COPY (SELECT * FROM read_csv_auto('{str(tmp).replace(chr(39), chr(39) * 2)}', header=true)) TO '{str(path).replace(chr(39), chr(39) * 2)}' (FORMAT PARQUET)")
    finally:
        con.close()
        tmp.unlink(missing_ok=True)

OUT_FORMATS = [("csv", "CSV", "CSV"), ("xlsx", "Excel (XLSX)", "Excel (XLSX)"), ("json", "JSON", "JSON"), ("jsonl", "JSON Lines", "JSON Lines"),
               ("tsv", "TSV", "TSV"), ("yaml", "YAML", "YAML"), ("xml", "XML", "XML"), ("md", "Markdown", "Markdown"), ("html", "HTML", "HTML"),
               ("sql", "SQL (INSERT)", "SQL (INSERT)"), ("parquet", "Parquet", "Parquet")]

def emit_table(ctx, cols, rows, fmt: str, name: str, *, force_file: bool = False, delimiter: str = ",", bom: bool = False,
               infer: bool = True, sql_name: str = "data") -> Dict[str, Any]:
    """Write the table as *fmt*; text formats are also shown (up to 200 000 characters)."""
    text = None
    lang = ""
    if fmt == "csv":
        text = to_csv_text(cols, rows, delimiter)
    elif fmt == "tsv":
        text = to_csv_text(cols, rows, "\t")
    elif fmt == "json":
        text = json.dumps(as_records(cols, rows, infer), ensure_ascii=False, indent=2, default=str)
        lang = "json"
    elif fmt == "jsonl":
        text = "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in as_records(cols, rows, infer)) + "\n"
        lang = "json"
    elif fmt == "yaml":
        text = to_yaml(as_records(cols, rows, infer))
        lang = "yaml"
    elif fmt == "xml":
        text = to_xml(cols, rows)
        lang = "xml"
    elif fmt == "md":
        text = to_markdown_table(cols, rows)
        lang = "markdown"
    elif fmt == "html":
        text = to_html_table(cols, rows)
        lang = "html"
    elif fmt == "sql":
        text = to_sql(cols, rows, sql_name or "data")
        lang = "sql"
    elif fmt == "xlsx":
        ctx.out_bytes(f"{name}.xlsx", write_xlsx({name[:31] or "Sheet1": [cols] + [[typed(v) if infer else v for v in r] for r in rows]}))
    elif fmt == "parquet":
        write_parquet(ctx.out_path(f"{name}.parquet"), cols, rows)
    else:
        raise UtilError("Неизвестный формат", "Unknown format")
    result: Dict[str, Any] = {}
    if text is not None:
        if force_file or len(text) > 200_000:
            data = text.encode("utf-8-sig" if bom and fmt in ("csv", "tsv") else "utf-8")
            ctx.out_bytes(f"{name}.{fmt}", data)
        result["text"] = text[:200_000]
        if lang:
            result["lang"] = lang
    else:
        result["table"] = table(cols, [[_cell(v) for v in r] for r in rows[:PREVIEW_ROWS]])
    return result

# ── utilities ────────────────────────────────────────────────────────────────────────────────────────────────────────
FROM_FORMATS = [("auto", "Определить", "Detect"), ("csv", "CSV", "CSV"), ("tsv", "TSV", "TSV"), ("json", "JSON", "JSON"), ("jsonl", "JSON Lines", "JSON Lines"),
                ("yaml", "YAML", "YAML"), ("xml", "XML", "XML"), ("md", "Markdown", "Markdown"), ("html", "HTML", "HTML")]

@utility("data.convert", "data", "Конвертер таблиц и данных", "Table and data converter",
         "CSV, TSV, Excel (XLSX/ODS), JSON, JSON Lines, YAML, XML, Markdown, HTML и Parquet — в любой из них, а ещё в SQL INSERT.",
         "CSV, TSV, Excel (XLSX/ODS), JSON, JSON Lines, YAML, XML, Markdown, HTML and Parquet — into any of them, plus SQL INSERT.",
         input="any", accept=DATA_ACCEPT, ai_default=True,
         opts=[O("to", "choice", "Во что", "To", "xlsx", choices=OUT_FORMATS), O("from", "choice", "Из чего", "From", "auto", choices=FROM_FORMATS),
               O("sheet", "text", "Лист Excel (имя или номер)", "Excel sheet (name or number)", ""),
               O("delimiter", "choice", "Разделитель CSV", "CSV delimiter", ",", choices=[(",", "Запятая", "Comma"), (";", "Точка с запятой (Excel RU)", "Semicolon (Excel EU)"), ("\t", "Табуляция", "Tab"), ("|", "|", "|")], show="to=csv"),
               O("bom", "bool", "BOM для Excel", "BOM for Excel", True, show="to=csv|tsv"), O("infer", "bool", "Числа и true/false как значения", "Numbers and true/false as values", True),
               O("table", "text", "Имя таблицы SQL", "SQL table name", "data", show="to=sql")],
         placeholder="name,age\nАнна,31\nИван,27", keys="конвертер csv excel xlsx json yaml xml parquet таблица convert table data")
def data_convert(ctx):
    (cols, rows), name = input_table(ctx)
    result = emit_table(ctx, cols, rows, ctx.o["to"], name, force_file=bool(ctx.files), delimiter=ctx.o["delimiter"], bom=ctx.o["bom"],
                        infer=ctx.o["infer"], sql_name=ctx.o["table"])
    result["note"] = ctx.tr(f"Строк: {len(rows)} · столбцов: {len(cols)}", f"Rows: {len(rows)} · columns: {len(cols)}")
    return result

@utility("data.sql", "data", "SQL-запрос к файлам", "SQL over files",
         "Выполняет SQL (DuckDB) над CSV, Excel, JSON и Parquet: файлы — таблицы t1, t2… (и по имени файла), вставленный текст — таблица data.",
         "Runs SQL (DuckDB) over CSV, Excel, JSON and Parquet: files become tables t1, t2… (and by file name), pasted text is the table data.",
         input="any", accept=DATA_ACCEPT, ai_default=True,
         opts=[O("query", "textarea", "Запрос", "Query", "SELECT * FROM t1 LIMIT 100", hint="Пример: SELECT city, count(*) FROM t1 GROUP BY 1 ORDER BY 2 DESC",
                 hint_en="E.g. SELECT city, count(*) FROM t1 GROUP BY 1 ORDER BY 2 DESC"),
               O("to", "choice", "Сохранить результат", "Save the result", "", choices=[("", "Не сохранять", "Don't save"), ("csv", "CSV", "CSV"), ("xlsx", "Excel", "Excel"), ("json", "JSON", "JSON")])],
         keys="sql duckdb запрос select group by выборка query csv excel")
def data_sql(ctx):
    duckdb = _duck()
    query = str(ctx.o["query"] or "").strip().rstrip(";")
    if not query:
        raise UtilError("Введите SQL-запрос", "Enter an SQL query")
    con = duckdb.connect(":memory:")
    names = []
    try:
        sources = list(enumerate(ctx.files, 1))
        for i, f in sources:
            tname = f"t{i}"
            path = str(f.path()).replace("'", "''")
            if f.ext == "parquet":
                con.execute(f"CREATE TABLE {tname} AS SELECT * FROM read_parquet('{path}')")
            elif f.ext == "csv" or f.ext == "tsv":
                try:
                    con.execute(f"CREATE TABLE {tname} AS SELECT * FROM read_csv_auto('{path}', header=true)")
                except Exception:  # noqa: BLE001 - odd encodings: go through our reader
                    cols, rows = load_file_table(f)
                    _load_rows(con, tname, cols, rows, ctx)
            elif f.ext in ("json", "jsonl", "ndjson"):
                try:
                    con.execute(f"CREATE TABLE {tname} AS SELECT * FROM read_json_auto('{path}')")
                except Exception:  # noqa: BLE001
                    cols, rows = load_file_table(f)
                    _load_rows(con, tname, cols, rows, ctx)
            else:
                cols, rows = load_file_table(f)
                _load_rows(con, tname, cols, rows, ctx)
            names.append(tname)
            alias = re.sub(r"\W+", "_", f.stem.lower()).strip("_")
            if alias and re.match(r"^[a-z_]", alias) and alias not in names:
                con.execute(f'CREATE VIEW "{alias}" AS SELECT * FROM {tname}')
                names.append(alias)
        if not ctx.files:
            cols, rows = parse_table(ctx.need_text("Вставьте таблицу или добавьте файл", "Paste a table or add a file"))
            _load_rows(con, "data", cols, rows, ctx)
            con.execute("CREATE VIEW t1 AS SELECT * FROM data")
            names += ["data", "t1"]
        # the query itself may not touch files, the network or extensions
        con.execute("SET enable_external_access = false")
        con.execute("SET lock_configuration = true")
        try:
            cur = con.execute(query)
        except Exception as exc:  # noqa: BLE001
            raise UtilError(f"Ошибка SQL: {exc}", f"SQL error: {exc}")
        if cur.description is None:
            return ctx.tr("Запрос выполнен", "The query ran")
        cols = [d[0] for d in cur.description]
        # F36: the old code pulled up to 100 000 wide rows into memory just
        # to show the first 1 000. Fetch only what is shown; the exact
        # total comes from a separate COUNT(*) over the same query.
        rows = [list(r) for r in cur.fetchmany(1000)]
        total = len(rows)
        try:
            total = int(con.execute(f"SELECT COUNT(*) FROM ({query})").fetchone()[0])
        except Exception:  # noqa: BLE001 - exotic queries: fall back to the fetched count
            total = len(rows) if len(rows) < 1000 else -1
    finally:
        con.close()
    shown = f"Строк: {total}" if total >= 0 else "Строк: 1000+"
    shown_en = f"Rows: {total}" if total >= 0 else "Rows: 1000+"
    if total > 1000:
        shown += " (показаны первые 1000)"
        shown_en += " (first 1000 shown)"
    result: Dict[str, Any] = {"table": table(cols, [[_cell(v) for v in r] for r in rows]),
                              "note": ctx.tr(f"{shown} · таблицы: {', '.join(names)}",
                                             f"{shown_en} · tables: {', '.join(names)}")}
    if ctx.o["to"]:
        # The export holds the same 1 000 shown rows (see the note).
        emit_table(ctx, cols, rows, ctx.o["to"], "query", force_file=True)
    return result

def _load_rows(con, name: str, cols: List[str], rows: List[List[Any]], ctx) -> None:
    path = ctx.work / f"{name}.csv"
    path.write_text(to_csv_text(cols, rows), encoding="utf-8", newline="")
    con.execute(f"CREATE TABLE {name} AS SELECT * FROM read_csv_auto('{str(path).replace(chr(39), chr(39) * 2)}', header=true)")

# JSONPath (the useful subset): $ . .. [n] [a:b] [*] ['key'] [?(@.x > 1)]
def _jp_tokens(path: str) -> List[Tuple[str, Any]]:
    s = path.strip()
    if s.startswith("$"):
        s = s[1:]
    tokens: List[Tuple[str, Any]] = []
    i = 0
    while i < len(s):
        if s.startswith("..", i):
            tokens.append(("deep", None))
            i += 2
            m = re.match(r"[\w$-]+|\*", s[i:])
            if m:
                tokens.append(("key", m.group(0)))
                i += len(m.group(0))
        elif s[i] == ".":
            i += 1
            m = re.match(r"[\w$-]+|\*", s[i:])
            if not m:
                raise UtilError(f"Ошибка в пути около «{s[i:i + 10]}»", f"Path error near “{s[i:i + 10]}”")
            tokens.append(("key", m.group(0)))
            i += len(m.group(0))
        elif s[i] == "[":
            depth, j = 0, i
            while j < len(s):
                if s[j] == "[":
                    depth += 1
                elif s[j] == "]":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            inner = s[i + 1:j].strip()
            i = j + 1
            if inner == "*":
                tokens.append(("key", "*"))
            elif inner.startswith("?"):
                tokens.append(("filter", inner[1:].strip().strip("()")))
            elif re.fullmatch(r"-?\d*:-?\d*", inner):
                a, b = inner.split(":")
                tokens.append(("slice", (int(a) if a else None, int(b) if b else None)))
            elif re.fullmatch(r"-?\d+(\s*,\s*-?\d+)*", inner):
                tokens.append(("index", [int(x) for x in inner.split(",")]))
            else:
                tokens.append(("keys", [k.strip().strip("'\"") for k in inner.split(",")]))
        else:
            m = re.match(r"[\w$-]+", s[i:])
            if not m:
                raise UtilError(f"Ошибка в пути около «{s[i:i + 10]}»", f"Path error near “{s[i:i + 10]}”")
            tokens.append(("key", m.group(0)))
            i += len(m.group(0))
    return tokens

def _jp_filter(item: Any, expr: str) -> bool:
    m = re.fullmatch(r"@\.?([\w.$-]*)\s*(==|!=|>=|<=|>|<|=~|in)?\s*(.*)", expr.strip())
    if not m:
        raise UtilError(f"Не понимаю фильтр «{expr}»", f"Cannot read the filter “{expr}”")
    field, op, raw = m.group(1), m.group(2), m.group(3).strip()
    value = item
    for part in [p for p in field.split(".") if p]:
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return False
    if not op:
        return bool(value)
    try:
        target = json.loads(raw.replace("'", '"')) if raw else None
    except json.JSONDecodeError:
        target = raw.strip("'\"")
    try:
        if op == "==":
            return value == target
        if op == "!=":
            return value != target
        if op == "=~":
            return re.search(str(target).strip("/"), str(value)) is not None
        if op == "in":
            return value in (target if isinstance(target, list) else [target])
        return {">": value > target, "<": value < target, ">=": value >= target, "<=": value <= target}[op]
    except TypeError:
        return False

def json_path(data: Any, path: str) -> List[Any]:
    nodes = [data]
    for kind, arg in _jp_tokens(path):
        out: List[Any] = []
        if kind == "deep":
            stack = list(nodes)
            while stack:
                node = stack.pop(0)
                out.append(node)
                if isinstance(node, dict):
                    stack += list(node.values())
                elif isinstance(node, list):
                    stack += node
        else:
            for node in nodes:
                if kind == "key":
                    if arg == "*":
                        out += list(node.values()) if isinstance(node, dict) else list(node) if isinstance(node, list) else []
                    elif isinstance(node, dict) and arg in node:
                        out.append(node[arg])
                    elif isinstance(node, list):
                        out += [x[arg] for x in node if isinstance(x, dict) and arg in x]
                elif kind == "keys" and isinstance(node, dict):
                    out += [node[k] for k in arg if k in node]
                elif kind == "index" and isinstance(node, list):
                    out += [node[k] for k in arg if -len(node) <= k < len(node)]
                elif kind == "slice" and isinstance(node, list):
                    out += node[arg[0]:arg[1]]
                elif kind == "filter":
                    items = node if isinstance(node, list) else list(node.values()) if isinstance(node, dict) else []
                    out += [x for x in items if _jp_filter(x, arg)]
        nodes = out
    return nodes

@utility("data.query", "data", "JSONPath: выборка из JSON", "JSONPath: query JSON",
         "Достаёт значения из большого JSON по пути: $.items[*].name, $..price, $.users[?(@.age > 30)], $.list[0:5].",
         "Picks values out of a big JSON by a path: $.items[*].name, $..price, $.users[?(@.age > 30)], $.list[0:5].",
         input="any", accept=".json,.jsonl,.txt", ai_default=True, live=True,
         opts=[O("path", "text", "Путь", "Path", "$..name"), O("as", "choice", "Вывод", "Output", "json", choices=[("json", "JSON", "JSON"), ("lines", "Строками", "Lines"), ("table", "Таблицей", "Table")])],
         placeholder='{"users":[{"name":"Анна","age":31},{"name":"Иван","age":27}]}', keys="jsonpath json path jq выборка поиск значений query")
def data_query(ctx):
    from .t_dev import load_json_lenient
    text = ctx.files[0].text() if ctx.files else ctx.need_text("Вставьте JSON", "Paste some JSON")
    data, _ = load_json_lenient(text)
    found = json_path(data, ctx.o["path"] or "$")
    note = ctx.tr(f"Найдено: {len(found)}", f"Found: {len(found)}")
    if ctx.o["as"] == "lines":
        return {"text": "\n".join(_cell(v) for v in found), "note": note}
    if ctx.o["as"] == "table" and found and all(isinstance(v, dict) for v in found):
        cols, rows = records_to_table(found)
        return {"table": table(cols, [[_cell(v) for v in r] for r in rows[:PREVIEW_ROWS]]), "note": note}
    return {"text": json.dumps(found[0] if len(found) == 1 else found, ensure_ascii=False, indent=2), "lang": "json", "note": note}

@utility("data.clean", "data", "Чистка таблицы", "Clean a table",
         "Убирает пробелы по краям, пустые строки и столбцы, дубликаты; приводит заголовки и числа «1 234,5» к порядку.",
         "Trims spaces, drops empty rows and columns and duplicates; tidies headers and numbers like “1 234,5”.",
         input="any", accept=DATA_ACCEPT, ai_default=True,
         opts=[O("trim", "bool", "Обрезать пробелы", "Trim spaces", True), O("empty", "bool", "Удалить пустые строки и столбцы", "Drop empty rows and columns", True),
               O("dupes", "bool", "Удалить дубликаты строк", "Drop duplicate rows", True), O("headers", "choice", "Заголовки", "Headers", "keep",
                                                                                             choices=[("keep", "Как есть", "Keep"), ("snake", "snake_case", "snake_case"), ("title", "С заглавной", "Title Case")]),
               O("numbers", "bool", "Числа к виду 1234.5", "Numbers as 1234.5", False), O("to", "choice", "Сохранить как", "Save as", "csv", choices=OUT_FORMATS[:4])],
         keys="очистить таблица дубликаты пустые пробелы clean dedupe trim csv")
def data_clean(ctx):
    from .t_text import translit
    (cols, rows), name = input_table(ctx)
    before = len(rows)
    if ctx.o["trim"]:
        rows = [[re.sub(r"\s+", " ", v).strip() if isinstance(v, str) else v for v in r] for r in rows]
        cols = [re.sub(r"\s+", " ", c).strip() for c in cols]
    if ctx.o["numbers"]:
        def num(v):
            if isinstance(v, str) and re.fullmatch(r"[+-]?\d{1,3}([  ']\d{3})+([.,]\d+)?|[+-]?\d+,\d+", v.strip()):
                return re.sub(r"[  ']", "", v.strip()).replace(",", ".")
            return v
        rows = [[num(v) for v in r] for r in rows]
    if ctx.o["empty"]:
        rows = [r for r in rows if any(str(v).strip() for v in r if v is not None)]
        keep = [i for i in range(len(cols)) if cols[i].strip() or any(i < len(r) and str(r[i] or "").strip() for r in rows)]
        keep = [i for i in keep if any(i < len(r) and str(r[i] or "").strip() for r in rows) or cols[i].strip()]
        cols = [cols[i] for i in keep]
        rows = [[r[i] if i < len(r) else "" for i in keep] for r in rows]
    dupes = 0
    if ctx.o["dupes"]:
        seen, out = set(), []
        for r in rows:
            key = tuple(_cell(v) for v in r)
            if key in seen:
                dupes += 1
                continue
            seen.add(key)
            out.append(r)
        rows = out
    if ctx.o["headers"] == "snake":
        cols = [re.sub(r"_+", "_", re.sub(r"\W", "_", translit(c).lower())).strip("_") or f"column{i + 1}" for i, c in enumerate(cols)]
    elif ctx.o["headers"] == "title":
        cols = [c[:1].upper() + c[1:] for c in cols]
    result = emit_table(ctx, cols, rows, ctx.o["to"], f"{name} — clean", force_file=bool(ctx.files))
    result["note"] = ctx.tr(f"Строк: {before} → {len(rows)} (дубликатов: {dupes})", f"Rows: {before} → {len(rows)} (duplicates: {dupes})")
    return result

@utility("data.merge", "data", "Объединить таблицы", "Merge tables",
         "Склеивает несколько CSV/Excel/JSON в одну таблицу (столбцы совмещаются по именам) или соединяет по ключевому столбцу (JOIN).",
         "Stacks several CSV/Excel/JSON files into one table (columns matched by name) or joins them on a key column (JOIN).",
         input="files", accept=DATA_ACCEPT,
         opts=[O("mode", "choice", "Как", "How", "stack", choices=[("stack", "Друг под другом", "Stack rows"), ("join", "Соединить по ключу", "Join on a key")]),
               O("key", "text", "Ключевой столбец", "Key column", "id", show="mode=join"),
               O("how", "choice", "Тип соединения", "Join type", "left", choices=[("left", "Все строки первой (LEFT)", "All of the first (LEFT)"), ("inner", "Только совпавшие (INNER)", "Matches only (INNER)"),
                                                                                   ("outer", "Все строки (FULL)", "All rows (FULL)")], show="mode=join"),
               O("source", "bool", "Столбец с именем файла", "Add a file name column", True, show="mode=stack"), O("to", "choice", "Сохранить как", "Save as", "xlsx", choices=OUT_FORMATS[:4])],
         keys="объединить склеить таблицы join merge stack union csv excel vlookup впр")
def data_merge(ctx):
    if len(ctx.files) < 2:
        raise UtilError("Добавьте хотя бы два файла", "Add at least two files")
    tables = [(f.stem, *load_file_table(f)) for f in ctx.files]
    if ctx.o["mode"] == "stack":
        cols: List[str] = []
        for _n, c, _r in tables:
            cols += [x for x in c if x not in cols]
        if ctx.o["source"]:
            cols = ["source"] + cols
        rows = []
        for n, c, r in tables:
            idx = {x: i for i, x in enumerate(c)}
            for row in r:
                vals = [row[idx[x]] if x in idx and idx[x] < len(row) else "" for x in (cols[1:] if ctx.o["source"] else cols)]
                rows.append(([n] if ctx.o["source"] else []) + vals)
    else:
        key = ctx.o["key"]
        for n, c, _r in tables:
            if key not in c:
                raise UtilError(f"В «{n}» нет столбца «{key}»", f"“{n}” has no column “{key}”")
        name0, cols, rows = tables[0]
        cols = list(cols)
        rows = [list(r) for r in rows]
        for n, c, r in tables[1:]:
            ki = c.index(key)
            extra = [x for x in c if x != key]
            index: Dict[str, List[Any]] = {}
            for row in r:
                index.setdefault(_cell(row[ki] if ki < len(row) else ""), row)
            k0 = cols.index(key)
            new_rows, matched = [], set()
            for row in rows:
                k = _cell(row[k0] if k0 < len(row) else "")
                other = index.get(k)
                if other is not None:
                    matched.add(k)
                    new_rows.append(row + [other[c.index(x)] if c.index(x) < len(other) else "" for x in extra])
                elif ctx.o["how"] != "inner":
                    new_rows.append(row + [""] * len(extra))
            if ctx.o["how"] == "outer":
                for k, other in index.items():
                    if k not in matched:
                        base = [""] * len(cols)
                        base[k0] = k
                        new_rows.append(base + [other[c.index(x)] if c.index(x) < len(other) else "" for x in extra])
            names = [x if x not in cols else f"{x} ({n})" for x in extra]
            cols = cols + names
            rows = new_rows
    result = emit_table(ctx, cols, rows, ctx.o["to"], "merged", force_file=True)
    result["note"] = ctx.tr(f"Строк: {len(rows)} · столбцов: {len(cols)}", f"Rows: {len(rows)} · columns: {len(cols)}")
    return result

@utility("data.split", "data", "Разделить таблицу", "Split a table",
         "Режет большую таблицу на файлы по N строк или по значениям столбца (например, отдельный файл на каждый город).",
         "Cuts a big table into files of N rows or by the values of a column (e.g. a file per city).",
         input="any", accept=DATA_ACCEPT,
         opts=[O("mode", "choice", "Как", "How", "rows", choices=[("rows", "По N строк", "Every N rows"), ("column", "По значениям столбца", "By column values")]),
               O("n", "int", "Строк в файле", "Rows per file", 1000, min=1, max=10_000_000, show="mode=rows"), O("column", "text", "Столбец", "Column", "", show="mode=column"),
               O("to", "choice", "Формат", "Format", "csv", choices=[("csv", "CSV", "CSV"), ("xlsx", "Excel", "Excel"), ("json", "JSON", "JSON")]),
               O("zip", "bool", "Упаковать в ZIP", "Pack into a ZIP", True)],
         keys="разделить разбить таблицу split csv excel chunks части")
def data_split(ctx):
    (cols, rows), name = input_table(ctx)
    groups: List[Tuple[str, List[List[Any]]]] = []
    if ctx.o["mode"] == "rows":
        n = max(1, ctx.o["n"])
        groups = [(f"{k // n + 1}", rows[k:k + n]) for k in range(0, len(rows), n)]
    else:
        col = ctx.o["column"]
        if col not in cols:
            raise UtilError(f"Нет столбца «{col}». Есть: {', '.join(cols[:20])}", f"No column “{col}”. Available: {', '.join(cols[:20])}")
        i = cols.index(col)
        buckets: Dict[str, List[List[Any]]] = {}
        for r in rows:
            buckets.setdefault(_cell(r[i] if i < len(r) else "") or "(empty)", []).append(r)
        groups = list(buckets.items())
    if len(groups) > 2000:
        raise UtilError("Получается больше 2000 файлов — увеличьте N или выберите другой столбец", "That makes over 2000 files — raise N or pick another column")
    files = []
    from .core import safe_name
    for label, chunk in groups:
        fname = safe_name(f"{name} — {label}")
        if ctx.o["to"] == "xlsx":
            files.append((f"{fname}.xlsx", write_xlsx({"Sheet1": [cols] + [[typed(v) for v in r] for r in chunk]})))
        elif ctx.o["to"] == "json":
            files.append((f"{fname}.json", json.dumps(as_records(cols, chunk), ensure_ascii=False, indent=2).encode("utf-8")))
        else:
            files.append((f"{fname}.csv", to_csv_text(cols, chunk).encode("utf-8-sig")))
    from .t_docs import _emit_many
    _emit_many(ctx, files, f"{name} — parts.zip", zip_over=0 if ctx.o["zip"] else 50)
    return ctx.tr(f"{len(rows)} строк → {len(files)} файлов", f"{len(rows)} rows → {len(files)} files")

def _num_or_none(v: Any) -> Optional[float]:
    t = typed(v)
    if isinstance(t, bool) or not isinstance(t, (int, float)):
        return None
    return float(t)

@utility("data.stats", "data", "Статистика по столбцам", "Column statistics",
         "Для каждого столбца: тип, заполненность, уникальные, минимум, максимум, среднее, медиана и самые частые значения.",
         "For every column: type, filled cells, unique values, min, max, mean, median and the most common values.",
         input="any", accept=DATA_ACCEPT, ai_default=True,
         keys="статистика столбцы профиль данных среднее медиана stats profile describe columns")
def data_stats(ctx):
    (cols, rows), _name = input_table(ctx)
    out = []
    for i, c in enumerate(cols):
        vals = [r[i] for r in rows if i < len(r)]
        filled = [v for v in vals if v not in (None, "") and str(v).strip()]
        nums = [x for x in (_num_or_none(v) for v in filled) if x is not None]
        counts: Dict[str, int] = {}
        for v in filled:
            counts[_cell(v)] = counts.get(_cell(v), 0) + 1
        top = sorted(counts.items(), key=lambda kv: -kv[1])[:3]
        kind = ctx.tr("число", "number") if filled and len(nums) == len(filled) else ctx.tr("дата", "date") if filled and all(re.match(r"^\d{4}-\d{2}-\d{2}|^\d{2}\.\d{2}\.\d{4}", str(v)) for v in filled[:200]) else ctx.tr("текст", "text")

        def fmt(x):
            return "" if x is None else (str(int(x)) if float(x).is_integer() else f"{x:.4g}")
        out.append([c, kind, f"{len(filled)}/{len(vals)}", str(len(counts)),
                    fmt(min(nums)) if nums else (min(map(_cell, filled), key=len) if filled and kind != ctx.tr("число", "number") else ""),
                    fmt(max(nums)) if nums else "", fmt(statistics.fmean(nums)) if nums else "", fmt(statistics.median(nums)) if nums else "",
                    "; ".join(f"{k[:30]} ×{n}" for k, n in top)])
    return {"table": table([ctx.tr("Столбец", "Column"), ctx.tr("Тип", "Type"), ctx.tr("Заполнено", "Filled"), ctx.tr("Уникальных", "Unique"), ctx.tr("Мин.", "Min"),
                            ctx.tr("Макс.", "Max"), ctx.tr("Среднее", "Mean"), ctx.tr("Медиана", "Median"), ctx.tr("Частые значения", "Top values")], out),
            "note": ctx.tr(f"Строк: {len(rows)} · столбцов: {len(cols)}", f"Rows: {len(rows)} · columns: {len(cols)}")}

AGGS = [("sum", "Сумма", "Sum"), ("count", "Количество", "Count"), ("avg", "Среднее", "Average"), ("min", "Минимум", "Min"), ("max", "Максимум", "Max"), ("distinct", "Уникальных", "Distinct")]

@utility("data.pivot", "data", "Сводная таблица", "Pivot table",
         "Группирует строки по столбцу (и, по желанию, раскладывает по второму) и считает сумму, количество, среднее, минимум или максимум.",
         "Groups rows by a column (optionally spreading by a second one) and computes the sum, count, average, min or max.",
         input="any", accept=DATA_ACCEPT, ai_default=True,
         opts=[O("rows", "text", "Группировать по", "Group by", ""), O("cols", "text", "Разложить по (необязательно)", "Spread by (optional)", ""),
               O("value", "text", "Считать столбец", "Value column", ""), O("agg", "choice", "Функция", "Function", "sum", choices=AGGS),
               O("to", "choice", "Сохранить", "Save", "", choices=[("", "Не сохранять", "Don't save"), ("xlsx", "Excel", "Excel"), ("csv", "CSV", "CSV")])],
         keys="сводная таблица pivot группировка сумма group by aggregate excel")
def data_pivot(ctx):
    (cols, rows), name = input_table(ctx)

    def col(key, required=True):
        v = str(ctx.o[key] or "").strip()
        if not v:
            if required:
                raise UtilError(f"Укажите столбец. Есть: {', '.join(cols[:20])}", f"Pick a column. Available: {', '.join(cols[:20])}")
            return None
        if v not in cols:
            match = next((c for c in cols if c.lower() == v.lower()), None)
            if not match:
                raise UtilError(f"Нет столбца «{v}». Есть: {', '.join(cols[:20])}", f"No column “{v}”. Available: {', '.join(cols[:20])}")
            v = match
        return cols.index(v)
    gi, si = col("rows"), col("cols", False)
    vi = col("value", ctx.o["agg"] not in ("count",))
    agg = ctx.o["agg"]
    cells: Dict[Tuple[str, str], List[Any]] = {}
    groups: List[str] = []
    spreads: List[str] = []
    for r in rows:
        g = _cell(r[gi] if gi < len(r) else "")
        s = _cell(r[si] if si is not None and si < len(r) else "") if si is not None else ""
        if g not in groups:
            groups.append(g)
        if s not in spreads:
            spreads.append(s)
        cells.setdefault((g, s), []).append(r[vi] if vi is not None and vi < len(r) else 1)

    def reduce(vals):
        if agg == "count":
            return len(vals)
        if agg == "distinct":
            return len({_cell(v) for v in vals})
        nums = [x for x in (_num_or_none(v) for v in vals) if x is not None]
        if not nums:
            return ""
        res = {"sum": sum(nums), "avg": statistics.fmean(nums), "min": min(nums), "max": max(nums)}[agg]
        return int(res) if float(res).is_integer() else round(res, 6)
    try:
        groups.sort(key=lambda x: (typed(x) if isinstance(typed(x), (int, float)) else float("inf"), x))
    except TypeError:
        pass
    if si is None:
        out_cols = [cols[gi], f"{agg}({cols[vi]})" if vi is not None else agg]
        out_rows = [[g, reduce(cells[(g, "")])] for g in groups]
    else:
        out_cols = [cols[gi]] + spreads + [ctx.tr("Итого", "Total")]
        out_rows = []
        for g in groups:
            line = [g] + [reduce(cells[(g, s)]) if (g, s) in cells else "" for s in spreads]
            line.append(reduce([v for s in spreads for v in cells.get((g, s), [])]))
            out_rows.append(line)
    result: Dict[str, Any] = {"table": table(out_cols, [[_cell(v) for v in r] for r in out_rows[:1000]])}
    if ctx.o["to"]:
        emit_table(ctx, out_cols, out_rows, ctx.o["to"], f"{name} — pivot", force_file=True)
    return result

FILTER_OPS = [("contains", "содержит", "contains"), ("=", "равно", "equals"), ("!=", "не равно", "not equal"), (">", "больше", "greater"),
              ("<", "меньше", "less"), ("empty", "пусто", "is empty"), ("notempty", "не пусто", "is not empty"), ("regex", "regex", "regex")]

@utility("data.filter", "data", "Фильтр и сортировка таблицы", "Filter and sort a table",
         "Оставляет строки по условию, сортирует по столбцу, выбирает нужные столбцы и первые N строк.",
         "Keeps the rows matching a condition, sorts by a column, picks the columns you need and the first N rows.",
         input="any", accept=DATA_ACCEPT, ai_default=True,
         opts=[O("where", "text", "Столбец для фильтра", "Filter column", ""), O("op", "choice", "Условие", "Condition", "contains", choices=FILTER_OPS),
               O("value", "text", "Значение", "Value", ""), O("sort", "text", "Сортировать по", "Sort by", ""),
               O("desc", "bool", "По убыванию", "Descending", False), O("columns", "text", "Столбцы (через запятую, пусто — все)", "Columns (comma-separated, empty = all)", ""),
               O("limit", "int", "Не больше строк (0 — все)", "At most rows (0 = all)", 0, min=0, max=10_000_000),
               O("to", "choice", "Сохранить", "Save", "", choices=[("", "Не сохранять", "Don't save")] + OUT_FORMATS[:4])],
         keys="фильтр сортировка отбор строк столбцы filter sort where order by")
def data_filter(ctx):
    (cols, rows), name = input_table(ctx)

    def find(c):
        if c in cols:
            return cols.index(c)
        low = [x.lower() for x in cols]
        if c.lower() in low:
            return low.index(c.lower())
        raise UtilError(f"Нет столбца «{c}». Есть: {', '.join(cols[:20])}", f"No column “{c}”. Available: {', '.join(cols[:20])}")
    if str(ctx.o["where"] or "").strip():
        i = find(ctx.o["where"].strip())
        op, val = ctx.o["op"], str(ctx.o["value"] or "")
        num = _num_or_none(val)

        def ok(r):
            v = r[i] if i < len(r) else ""
            s = _cell(v)
            if op == "contains":
                return val.lower() in s.lower()
            if op == "=":
                return s == val or (num is not None and _num_or_none(v) == num)
            if op == "!=":
                return not (s == val or (num is not None and _num_or_none(v) == num))
            if op in (">", "<"):
                a = _num_or_none(v)
                if a is not None and num is not None:
                    return a > num if op == ">" else a < num
                return s > val if op == ">" else s < val
            if op == "empty":
                return not s.strip()
            if op == "notempty":
                return bool(s.strip())
            try:
                return re.search(val, s) is not None
            except re.error as exc:
                raise UtilError(f"Ошибка в regex: {exc}", f"Regex error: {exc}")
        rows = [r for r in rows if ok(r)]
    if str(ctx.o["sort"] or "").strip():
        k = find(ctx.o["sort"].strip())

        def key(r):
            v = r[k] if k < len(r) else ""
            n = _num_or_none(v)
            return (0, n, "") if n is not None else (1, 0, _cell(v).lower())
        rows.sort(key=key, reverse=ctx.o["desc"])
    if str(ctx.o["columns"] or "").strip():
        pick = [find(c.strip()) for c in ctx.o["columns"].split(",") if c.strip()]
        cols = [cols[i] for i in pick]
        rows = [[r[i] if i < len(r) else "" for i in pick] for r in rows]
    if ctx.o["limit"]:
        rows = rows[:ctx.o["limit"]]
    result: Dict[str, Any] = {"table": table(cols, [[_cell(v) for v in r] for r in rows[:1000]]), "note": ctx.tr(f"Строк: {len(rows)}", f"Rows: {len(rows)}")}
    if ctx.o["to"]:
        emit_table(ctx, cols, rows, ctx.o["to"], f"{name} — filtered", force_file=True)
    return result

def flatten(data: Any, prefix: str = "", sep: str = ".", out: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out = {} if out is None else out
    if isinstance(data, dict) and data:
        for k, v in data.items():
            flatten(v, f"{prefix}{sep}{k}" if prefix else str(k), sep, out)
    elif isinstance(data, list) and data:
        for i, v in enumerate(data):
            flatten(v, f"{prefix}[{i}]" if prefix else f"[{i}]", sep, out)
    else:
        out[prefix or "value"] = data
    return out

def unflatten(flat: Dict[str, Any], sep: str = ".") -> Any:
    root: Any = {}
    for key, value in flat.items():
        parts: List[Any] = []
        for piece in str(key).split(sep):
            m = re.findall(r"([^\[\]]+)|\[(\d+)\]", piece)
            for name, idx in m:
                parts.append(int(idx) if idx else name)
        node = root
        for j, part in enumerate(parts):
            last = j == len(parts) - 1
            nxt: Any = value if last else ([] if isinstance(parts[j + 1], int) else {})
            if isinstance(part, int):
                if not isinstance(node, list):
                    break
                while len(node) <= part:
                    node.append(None)
                if node[part] is None or last:
                    node[part] = nxt
                node = node[part]
            else:
                if not isinstance(node, dict):
                    break
                if part not in node or last:
                    node[part] = nxt if part not in node or last else node[part]
                node = node[part]
    return root

@utility("data.flatten", "data", "JSON: развернуть в плоский вид и обратно", "JSON: flatten and unflatten",
         "Вложенный JSON → плоские ключи «user.address.city» (или таблица), и обратно из плоских ключей во вложенный.",
         "Nested JSON → flat keys “user.address.city” (or a table), and back from flat keys to nested.",
         input="any", accept=".json,.txt", ai_default=True, live=True,
         opts=[O("mode", "choice", "Направление", "Direction", "flat", choices=[("flat", "Развернуть", "Flatten"), ("nest", "Свернуть обратно", "Unflatten")]),
               O("sep", "text", "Разделитель", "Separator", "."), O("table", "bool", "Массив объектов — таблицей", "An array of objects as a table", False, show="mode=flat")],
         placeholder='{"user":{"name":"Анна","tags":["a","b"]}}', keys="json flatten плоский вложенный развернуть nested keys")
def data_flatten(ctx):
    from .t_dev import load_json_lenient
    text = ctx.files[0].text() if ctx.files else ctx.need_text("Вставьте JSON", "Paste some JSON")
    data, _ = load_json_lenient(text)
    sep = ctx.o["sep"] or "."
    if ctx.o["mode"] == "nest":
        if isinstance(data, list):
            return {"text": json.dumps([unflatten(x, sep) if isinstance(x, dict) else x for x in data], ensure_ascii=False, indent=2), "lang": "json"}
        if not isinstance(data, dict):
            raise UtilError("Нужен объект с плоскими ключами", "An object with flat keys is needed")
        return {"text": json.dumps(unflatten(data, sep), ensure_ascii=False, indent=2), "lang": "json"}
    if ctx.o["table"] and isinstance(data, list) and data and all(isinstance(x, dict) for x in data):
        cols, rows = records_to_table([flatten(x, sep=sep) for x in data])
        if ctx.files:
            ctx.out_text(f"{ctx.files[0].stem} — flat.csv", to_csv_text(cols, rows))
        return {"table": table(cols, [[_cell(v) for v in r] for r in rows[:PREVIEW_ROWS]])}
    if isinstance(data, list) and data and all(isinstance(x, dict) for x in data):
        return {"text": json.dumps([flatten(x, sep=sep) for x in data], ensure_ascii=False, indent=2), "lang": "json"}
    return {"text": json.dumps(flatten(data, sep=sep), ensure_ascii=False, indent=2), "lang": "json"}

def json_diff(a: Any, b: Any, path: str = "$", out: Optional[List[List[str]]] = None) -> List[List[str]]:
    out = [] if out is None else out

    def show(v):
        s = json.dumps(v, ensure_ascii=False)
        return s if len(s) <= 120 else s[:117] + "…"
    if isinstance(a, dict) and isinstance(b, dict):
        for k in a:
            if k not in b:
                out.append(["-", f"{path}.{k}", show(a[k]), ""])
            else:
                json_diff(a[k], b[k], f"{path}.{k}", out)
        for k in b:
            if k not in a:
                out.append(["+", f"{path}.{k}", "", show(b[k])])
    elif isinstance(a, list) and isinstance(b, list):
        for i in range(max(len(a), len(b))):
            if i >= len(a):
                out.append(["+", f"{path}[{i}]", "", show(b[i])])
            elif i >= len(b):
                out.append(["-", f"{path}[{i}]", show(a[i]), ""])
            else:
                json_diff(a[i], b[i], f"{path}[{i}]", out)
    elif a != b or type(a) is not type(b):
        out.append(["~", path, show(a), show(b)])
    return out

@utility("data.json_diff", "data", "Сравнить два JSON", "Compare two JSON documents",
         "Показывает, что добавлено, удалено и изменено между двумя JSON — по путям, без учёта порядка ключей и форматирования.",
         "Shows what was added, removed and changed between two JSON documents — by path, ignoring key order and formatting.",
         input="text2", live=True, label2="Второй JSON", label2_en="Second JSON", ai_default=True,
         opts=[O("order", "bool", "Массивы без учёта порядка", "Ignore array order", False)],
         placeholder='{"a":1,"b":[1,2]}', keys="сравнить json diff различия compare")
def data_json_diff(ctx):
    from .t_dev import load_json_lenient
    a, _ = load_json_lenient(ctx.need_text("Вставьте первый JSON", "Paste the first JSON"))
    if not str(ctx.text2 or "").strip():
        raise UtilError("Вставьте второй JSON", "Paste the second JSON")
    b, _ = load_json_lenient(ctx.text2)
    if ctx.o["order"]:
        def norm(v):
            if isinstance(v, dict):
                return {k: norm(x) for k, x in v.items()}
            if isinstance(v, list):
                return sorted((norm(x) for x in v), key=lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False))
            return v
        a, b = norm(a), norm(b)
    rows = json_diff(a, b)
    if not rows:
        return ctx.tr("Документы совпадают", "The documents are identical")
    counts = {k: sum(1 for r in rows if r[0] == k) for k in "+-~"}
    return {"table": table(["", ctx.tr("Путь", "Path"), ctx.tr("Было", "Before"), ctx.tr("Стало", "After")], rows[:2000]),
            "note": ctx.tr(f"Добавлено: {counts['+']} · удалено: {counts['-']} · изменено: {counts['~']}", f"Added: {counts['+']} · removed: {counts['-']} · changed: {counts['~']}")}
