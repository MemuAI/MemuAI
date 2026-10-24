# -*- coding: utf-8 -*-
"""Round 29: backend of the table editor (pgstatic/tables.js).

* ``POST /api/tables/xlsx`` — read an Excel workbook (.xlsx) the user imports into the composer's table editor.
  Body: ``{"data": "data:…;base64,…" | "<base64>", "name": "book.xlsx"}``.
  Answer: ``{"sheets": [{"name": "Sheet1", "rows": [["A1", "B1"], …]}], "truncated": bool}``.

The workbook is parsed with :mod:`zipfile` + :mod:`xml.etree` (no third-party dependency): shared strings,
inline strings, booleans, numbers, formulas' cached values and dates (by the cell's number format). Guards: the
upload size, the unpacked size of every part (zip bombs), the number of sheets, rows and columns. Nothing is
written to disk and nothing leaves the machine.
"""
from __future__ import annotations

import base64
import binascii
import datetime as _dt
import io
import logging
import posixpath
import re
import zipfile
from typing import Any, Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("memuai.tables")

MAX_UPLOAD = 15 * 1024 * 1024          # bytes of the .xlsx file itself
MAX_PART = 80 * 1024 * 1024            # unpacked size of one XML part
MAX_TOTAL = 200 * 1024 * 1024          # unpacked size of all read parts
MAX_SHEETS = 20
MAX_ROWS = 2000
MAX_COLS = 60
MAX_CELL = 4000                        # characters per cell

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
      "rel": "http://schemas.openxmlformats.org/package/2006/relationships"}
_DATE_FORMAT_IDS = set(range(14, 23)) | set(range(45, 48)) | {27, 30, 36, 50, 57}
_REF = re.compile(r"^([A-Z]+)(\d+)$")


RU = {
    "The workbook is too large to import": "Книга слишком большая для импорта",
    "The workbook contains unsupported XML": "В книге неподдерживаемый XML",
    "The workbook is damaged": "Книга повреждена",
    "The file is larger than 15 MB": "Файл больше 15 МБ",
    "This is not an .xlsx workbook": "Это не книга .xlsx",
    "This is not an .xlsx workbook (no xl/workbook.xml)": "Это не книга .xlsx (нет xl/workbook.xml)",
    "The file could not be decoded": "Не удалось прочитать файл",
    "The workbook could not be read": "Не удалось прочитать книгу",
    "Bad request": "Неверный запрос",
}


class XlsxError(ValueError):
    """A user-facing reason the workbook cannot be read (English; ``RU`` has the Russian twin)."""


def _error(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message, "error_ru": RU.get(message, message)}, status)


def _col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _read(zf: zipfile.ZipFile, name: str, budget: List[int]) -> Optional[bytes]:
    try:
        info = zf.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_PART or info.file_size > budget[0]:
        raise XlsxError("The workbook is too large to import")
    budget[0] -= info.file_size
    with zf.open(info) as handle:
        data = handle.read(MAX_PART + 1)
    if len(data) > MAX_PART:
        raise XlsxError("The workbook is too large to import")
    return data


def _xml(data: Optional[bytes]) -> Optional[ET.Element]:
    if data is None:
        return None
    if b"<!DOCTYPE" in data[:2048] or b"<!ENTITY" in data[:4096]:
        raise XlsxError("The workbook contains unsupported XML")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise XlsxError("The workbook is damaged") from exc


def _text(node: Optional[ET.Element]) -> str:
    if node is None:
        return ""
    # <si>/<is> hold either <t> or rich-text runs <r><t>…</t></r>; phonetic <rPh> is skipped
    parts = []
    for child in node:
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "t":
            parts.append(child.text or "")
        elif tag == "r":
            for t in child.iter("{%s}t" % NS["m"]):
                parts.append(t.text or "")
    return "".join(parts)


def _date_styles(styles: Optional[ET.Element]) -> set:
    """Indexes of cellXfs that format a number as a date/time."""
    if styles is None:
        return set()
    custom: Dict[int, str] = {}
    fmts = styles.find("m:numFmts", NS)
    if fmts is not None:
        for f in fmts.findall("m:numFmt", NS):
            try:
                custom[int(f.get("numFmtId", "0"))] = f.get("formatCode", "")
            except ValueError:
                continue
    out = set()
    xfs = styles.find("m:cellXfs", NS)
    if xfs is None:
        return out
    for index, xf in enumerate(xfs.findall("m:xf", NS)):
        try:
            fid = int(xf.get("numFmtId", "0"))
        except ValueError:
            continue
        code = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', "", custom.get(fid, "")).lower()
        if fid in _DATE_FORMAT_IDS or (code and re.search(r"[dmyhs]", code) and not re.search(r"[0#?]", code)):
            out.add(index)
    return out


def _excel_date(serial: float, date1904: bool) -> str:
    base = _dt.datetime(1904, 1, 1) if date1904 else _dt.datetime(1899, 12, 30)
    try:
        value = base + _dt.timedelta(days=serial)
    except (OverflowError, ValueError):
        return str(serial)
    if value.hour == value.minute == value.second == 0:
        return value.strftime("%Y-%m-%d")
    if serial < 1:
        return value.strftime("%H:%M:%S" if value.second else "%H:%M")
    return value.strftime("%Y-%m-%d %H:%M")


def _number(raw: str) -> str:
    try:
        value = float(raw)
    except ValueError:
        return raw
    if value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    return repr(round(value, 10)).rstrip("0").rstrip(".") if "e" not in repr(value) else raw


def parse_xlsx(data: bytes) -> Dict[str, Any]:
    if len(data) > MAX_UPLOAD:
        raise XlsxError("The file is larger than 15 MB")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError) as exc:
        raise XlsxError("This is not an .xlsx workbook") from exc
    budget = [MAX_TOTAL]
    with zf:
        workbook = _xml(_read(zf, "xl/workbook.xml", budget))
        if workbook is None:
            raise XlsxError("This is not an .xlsx workbook (no xl/workbook.xml)")
        pr = workbook.find("m:workbookPr", NS)
        date1904 = pr is not None and str(pr.get("date1904", "")).lower() in ("1", "true")
        rels_root = _xml(_read(zf, "xl/_rels/workbook.xml.rels", budget))
        targets: Dict[str, str] = {}
        if rels_root is not None:
            for rel in rels_root.findall("rel:Relationship", NS):
                target = rel.get("Target", "")
                path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
                targets[rel.get("Id", "")] = path
        shared: List[str] = []
        sst = _xml(_read(zf, "xl/sharedStrings.xml", budget))
        if sst is not None:
            shared = [_text(si) for si in sst.findall("m:si", NS)]
        date_styles = _date_styles(_xml(_read(zf, "xl/styles.xml", budget)))
        sheets_out = []
        truncated = False
        sheets = workbook.find("m:sheets", NS)
        entries = sheets.findall("m:sheet", NS) if sheets is not None else []
        for index, sheet in enumerate(entries):
            if len(sheets_out) >= MAX_SHEETS:
                truncated = True
                break
            if sheet.get("state") in ("hidden", "veryHidden"):
                continue
            rid = sheet.get("{%s}id" % NS["r"], "")
            path = targets.get(rid) or f"xl/worksheets/sheet{index + 1}.xml"
            root = _xml(_read(zf, path, budget))
            if root is None:
                continue
            rows, cut = _sheet_rows(root, shared, date_styles, date1904)
            truncated = truncated or cut
            if rows:
                sheets_out.append({"name": str(sheet.get("name") or f"Sheet{index + 1}")[:64], "rows": rows})
    return {"sheets": sheets_out, "truncated": truncated}


def _sheet_rows(root: ET.Element, shared: List[str], date_styles: set, date1904: bool) -> Tuple[List[List[str]], bool]:
    data = root.find("m:sheetData", NS)
    if data is None:
        return [], False
    grid: Dict[int, Dict[int, str]] = {}
    truncated = False
    next_row = 0
    for row in data.findall("m:row", NS):
        try:
            r_index = int(row.get("r", "0")) - 1 if row.get("r") else next_row
        except ValueError:
            r_index = next_row
        next_row = r_index + 1
        if r_index >= MAX_ROWS:
            truncated = True
            break
        next_col = 0
        for cell in row.findall("m:c", NS):
            ref = _REF.match(cell.get("r", "") or "")
            c_index = _col_index(ref.group(1)) if ref else next_col
            next_col = c_index + 1
            if c_index >= MAX_COLS:
                truncated = True
                continue
            kind = cell.get("t", "n")
            v = cell.find("m:v", NS)
            raw = v.text if v is not None and v.text is not None else ""
            if kind == "s":
                try:
                    value = shared[int(raw)]
                except (ValueError, IndexError):
                    value = ""
            elif kind == "inlineStr":
                value = _text(cell.find("m:is", NS))
            elif kind == "b":
                value = "TRUE" if raw == "1" else "FALSE"
            elif kind in ("str", "e"):
                value = raw
            else:
                value = raw
                if raw:
                    try:
                        style = int(cell.get("s", "0"))
                    except ValueError:
                        style = 0
                    if style in date_styles:
                        try:
                            value = _excel_date(float(raw), date1904)
                        except ValueError:
                            value = raw
                    else:
                        value = _number(raw)
            if value:
                grid.setdefault(r_index, {})[c_index] = value[:MAX_CELL]
    if not grid:
        return [], truncated
    width = max(max(cols) for cols in grid.values()) + 1
    last = max(grid)
    rows = [[grid.get(r, {}).get(c, "") for c in range(width)] for r in range(last + 1)]
    # leading empty rows/columns are dropped (a table often starts at B2)
    while rows and not any(rows[0]):
        rows.pop(0)
    first_col = min((next((i for i, v in enumerate(r) if v), width) for r in rows), default=0)
    rows = [r[first_col:] for r in rows]
    return rows, truncated


def _decode(payload: Any) -> bytes:
    text = str(payload or "")
    if text.startswith("data:"):
        text = text.split(",", 1)[1] if "," in text else ""
    if len(text) > MAX_UPLOAD * 4 // 3 + 16:
        raise XlsxError("The file is larger than 15 MB")
    try:
        return base64.b64decode(text, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise XlsxError("The file could not be decoded") from exc


def register(app: FastAPI) -> None:
    @app.post("/api/tables/xlsx")
    async def tables_xlsx(request: Request):
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return _error("Bad request")
        if not isinstance(body, dict):
            return _error("Bad request")
        try:
            import asyncio
            result = await asyncio.to_thread(parse_xlsx, _decode(body.get("data")))
        except XlsxError as exc:
            return _error(str(exc))
        except Exception as exc:  # noqa: BLE001
            log.info("tables: xlsx import failed (%s)", type(exc).__name__)
            return _error("The workbook could not be read")
        return result
