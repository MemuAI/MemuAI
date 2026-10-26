"""Round 29 (r29-tables-diagrams), item 8 «Tables»: streaming placeholder, rich rendered tables, the table editor,
exports (Markdown / TSV / CSV / HTML / JSON / XLSX / SVG / PNG presets) and the .xlsx import route.

The JS of pgstatic/tables.js runs in node (tests/round29_tables_runner.cjs, tiny DOM shim); SVG output is
validated here with ElementTree, the XLSX written by the browser code is read back by app/tables_api.py.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-r29-tables-")

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "pgstatic"
_CACHE: dict = {}

def run_js() -> dict:
    if "out" not in _CACHE:
        proc = subprocess.run(["node", str(ROOT / "tests" / "round29_tables_runner.cjs")], cwd=ROOT,
                              capture_output=True, text=True, timeout=120)
        if proc.returncode:
            raise AssertionError(proc.stderr[-3000:])
        _CACHE["out"] = json.loads(proc.stdout)
    return _CACHE["out"]

@unittest.skipUnless(shutil.which("node"), "node is not available")
class TablesJsTest(unittest.TestCase):
    def test_markdown_parse_and_round_trip(self) -> None:
        t = run_js()["tables"]
        self.assertEqual(t["parsed"]["headers"], ["Name", "Price", "Note"])
        self.assertEqual(t["parsed"]["align"], ["left", "right", "center"])
        self.assertEqual(t["parsed"]["title"], "Prices")
        self.assertEqual(t["parsed"]["rows"][1][0], "Pear | x", "escaped pipe stays in the cell")
        self.assertEqual(t["parsed"]["rows"][0][2], "`a|b`", "a pipe inside `code` is not a border")
        self.assertEqual(t["reparsed"]["rows"], t["parsed"]["rows"])
        self.assertEqual(t["reparsed"]["align"], t["parsed"]["align"])

    def test_text_formats(self) -> None:
        t = run_js()["tables"]
        self.assertEqual(t["csv"].split("\r\n")[1], '"x,y","say ""hi"""')
        self.assertIn('"multi\nline"', t["csv"])
        self.assertIn('" pad "', t["csv"], "leading/trailing spaces are quoted")
        self.assertEqual(t["tsv"], 'a\tb\r\n1\t"tab\there"')
        self.assertEqual(t["json"][0], {"n": 12, "column_2": "x", "flag": True})
        self.assertNotIn("<b>", t["html"])
        self.assertIn("&lt;b&gt;", t["html"])

    def test_paste_from_excel_and_csv(self) -> None:
        t = run_js()["tables"]
        self.assertEqual(t["excel"]["rows"], [["A", "1"], ["B\nC", "2"]])
        self.assertEqual(t["semicolon"]["headers"], ["a", "b", "c"])
        self.assertEqual(t["csvQuoted"]["rows"], [["x, y", 'z "q"']])

    def test_numbers_and_sorting(self) -> None:
        t = run_js()["tables"]
        self.assertEqual(t["numbers"], [1234.5, 1234.5, 12, -3, 1200, None, None, 1.5, 2500000, 10])
        self.assertEqual(t["kinds"], ["text", "number", "date"])
        self.assertEqual(t["sorted"], ["9", "10", "100", ""], "numeric sort, empty cells last")

    def test_streaming_placeholder(self) -> None:
        mask = run_js()["tables"]["mask"]
        self.assertIn("```memuai-table-pending\n2 2\n```", mask["partialRow"])
        self.assertTrue(mask["partialRow"].startswith("Intro"))
        self.assertIn("memuai-table-pending\n0 2", mask["headerOnly"])
        self.assertIn("memuai-table-pending\n2 2", mask["twoRowsNewline"], "a row may still follow the newline")
        self.assertNotIn("pending", mask["closedByText"], "a following text line closes the table")
        self.assertNotIn("pending", mask["closedByBlank"], "a blank line closes the table")
        self.assertNotIn("pending", mask["inCode"], "pipes inside a code block are code")
        self.assertEqual(mask["noTable"], "plain | text")
        html = run_js()["tables"]["pendingHtml"]
        self.assertIn("Building the table", html)
        self.assertIn("2 rows", html)
        self.assertEqual(run_js()["tables"]["pendingOther"], "")

    def test_picture_svg_is_valid_for_every_preset(self) -> None:
        t = run_js()["tables"]
        self.assertGreaterEqual(t["presets"], 18)
        for svg in [t["svg"], *t["svgAll"]]:
            root = ET.fromstring(svg)
            self.assertTrue(root.tag.endswith("svg"))
            self.assertGreater(float(root.get("width")), 50)
        self.assertIn("Prices", t["svg"])
        self.assertIn("linearGradient", t["svg"], "the glass preset paints a gradient")

    def test_xlsx_written_by_js_is_a_valid_workbook(self) -> None:
        data = base64.b64decode(run_js()["tables"]["xlsxB64"])
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            self.assertIsNone(zf.testzip())
            names = set(zf.namelist())
            self.assertTrue({"[Content_Types].xml", "xl/workbook.xml", "xl/worksheets/sheet1.xml", "xl/styles.xml"} <= names)
            for name in names:
                if name.endswith(".xml") or name.endswith(".rels"):
                    ET.fromstring(zf.read(name))
            sheet = zf.read("xl/worksheets/sheet1.xml").decode()
            self.assertIn("<v>10</v>", sheet, "numbers are numeric cells")
            self.assertIn("Pear &lt;&amp;&gt;", sheet)
        from app import tables_api
        book = tables_api.parse_xlsx(data)
        self.assertEqual(book["sheets"][0]["name"], "Fruit")
        self.assertEqual(book["sheets"][0]["rows"], [["Name", "Qty", "Price"], ["Apple", "3", "1.5"], ["Pear <&>", "10", "2"]])

def _xlsx(sheet_xml: str, shared: list | None = None, styles: str = "", extra_sheet: bool = False) -> bytes:
    buf = io.BytesIO()
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        sheets = '<sheet name="Data" sheetId="1" r:id="rId1"/>' + ('<sheet name="Hidden" sheetId="2" state="hidden" r:id="rId2"/>' if extra_sheet else "")
        zf.writestr("xl/workbook.xml", f"<workbook {ns}><sheets>{sheets}</sheets></workbook>")
        zf.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    '<Relationship Id="rId1" Type="x" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="x" Target="/xl/worksheets/sheet2.xml"/></Relationships>')
        zf.writestr("xl/worksheets/sheet1.xml", f"<worksheet {ns}><sheetData>{sheet_xml}</sheetData></worksheet>")
        if extra_sheet:
            zf.writestr("xl/worksheets/sheet2.xml", f'<worksheet {ns}><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>secret</t></is></c></row></sheetData></worksheet>')
        if shared is not None:
            zf.writestr("xl/sharedStrings.xml", f"<sst {ns}>" + "".join(f"<si><t>{s}</t></si>" for s in shared) + "</sst>")
        if styles:
            zf.writestr("xl/styles.xml", f"<styleSheet {ns}>{styles}</styleSheet>")
    return buf.getvalue()

class XlsxImportTest(unittest.TestCase):
    def test_shared_strings_dates_booleans_offsets(self) -> None:
        from app import tables_api
        styles = '<numFmts count="1"><numFmt numFmtId="164" formatCode="dd/mm/yyyy"/></numFmts><cellXfs count="3"><xf numFmtId="0"/><xf numFmtId="164"/><xf numFmtId="14"/></cellXfs>'
        sheet = ('<row r="2"><c r="B2" t="s"><v>0</v></c><c r="C2" t="s"><v>1</v></c><c r="D2" t="s"><v>2</v></c></row>'
                 '<row r="3"><c r="B3" t="inlineStr"><is><r><t>Rich </t></r><r><t>text</t></r></is></c><c r="C3" s="1"><v>45292</v></c><c r="D3" t="b"><v>1</v></c></row>'
                 '<row r="4"><c r="B4"><v>3.0</v></c><c r="C4" s="2"><v>45292.5</v></c><c r="D4" t="str"><f>A1</f><v>calc</v></c></row>')
        book = tables_api.parse_xlsx(_xlsx(sheet, ["Name", "Date", "Ok"], styles, extra_sheet=True))
        self.assertEqual(len(book["sheets"]), 1, "hidden sheets are skipped")
        self.assertEqual(book["sheets"][0]["rows"], [["Name", "Date", "Ok"], ["Rich text", "2024-01-01", "TRUE"], ["3", "2024-01-01 12:00", "calc"]])

    def test_limits_and_garbage(self) -> None:
        from app import tables_api
        with self.assertRaises(tables_api.XlsxError):
            tables_api.parse_xlsx(b"not a zip")
        rows = "".join(f'<row r="{i}"><c r="A{i}"><v>{i}</v></c><c r="BZ{i}"><v>1</v></c></row>' for i in range(1, tables_api.MAX_ROWS + 50))
        book = tables_api.parse_xlsx(_xlsx(rows))
        self.assertTrue(book["truncated"])
        self.assertEqual(len(book["sheets"][0]["rows"]), tables_api.MAX_ROWS)
        self.assertEqual(len(book["sheets"][0]["rows"][0]), 1, "columns past the limit are dropped")
        doc = io.BytesIO()
        with zipfile.ZipFile(doc, "w") as zf:
            zf.writestr("xl/workbook.xml", '<!DOCTYPE x [<!ENTITY a "aaaa">]><workbook/>')
        with self.assertRaises(tables_api.XlsxError):
            tables_api.parse_xlsx(doc.getvalue())

    def test_route(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import tables_api
        app = FastAPI()
        tables_api.register(app)
        client = TestClient(app)
        data = _xlsx('<row r="1"><c r="A1" t="inlineStr"><is><t>Hi</t></is></c></row>')
        resp = client.post("/api/tables/xlsx", json={"data": "data:application/octet-stream;base64," + base64.b64encode(data).decode(), "name": "a.xlsx"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["sheets"][0]["rows"], [["Hi"]])
        bad = client.post("/api/tables/xlsx", json={"data": base64.b64encode(b"nope").decode()})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("error_ru", bad.json())
        self.assertEqual(client.post("/api/tables/xlsx", json=[1]).status_code, 400)

class WiringTest(unittest.TestCase):
    def test_files_tags_and_hooks(self) -> None:
        index = (STATIC / "index.html").read_text(encoding="utf-8")
        quick = (STATIC / "quick" / "index.html").read_text(encoding="utf-8")
        for page in (index, quick):
            for tag in ('src="/tables.js?v=1.0-r29"', 'src="/diagrams.js?v=1.2-r36"', 'href="/tables.css?v=1.1-r30"', 'href="/diagrams.css?v=1.1-r30"',
                        'src="/diagram-zoom.js?v=1.1-r36"'):
                self.assertIn(tag, page)
        self.assertLess(index.index("/tables.js"), index.index("/app.js"), "loaded before app.js (md() uses it)")
        self.assertLess(quick.index("/tables.js"), quick.index("/quick/quick-md.js"))
        for name in ("tables.js", "tables.css", "diagrams.js", "diagrams.css", "vendor/mermaid/mermaid.min.js", "vendor/mermaid/LICENSE"):
            self.assertTrue((STATIC / name).is_file(), name)
        app_js = (STATIC / "app.js").read_text(encoding="utf-8")
        for hook in ("MemuTables.maskPending(visible)", "MemuTables.hydrate(box)", "MemuDiagrams.hydrate(box)", "MemuDiagrams.prompt()",
                     "kit.block(l, cleanCode", "MemuDiagrams.settingsHtml", "MemuDiagrams.bindSettings"):
            self.assertIn(hook, app_js)
        self.assertIn("MemuTables.openEditor()", (STATIC / "tools.js").read_text(encoding="utf-8"))
        quick_md = (STATIC / "quick" / "quick-md.js").read_text(encoding="utf-8")
        self.assertIn("MemuTables.maskPending(raw)", quick_md)
        self.assertIn("MemuTables.hydrate(box", (STATIC / "quick" / "quick.js").read_text(encoding="utf-8"))
        self.assertIn("tables_api.register(app)", (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8"))
        self.assertIn("'app.tables_api'", (ROOT / "MemuAI.spec").read_text(encoding="utf-8"))
        self.assertIn('"app.tables_api"', (ROOT / "build.py").read_text(encoding="utf-8"))

    def test_english_strings_have_no_cyrillic(self) -> None:

        for name in ("tables.js", "diagrams.js"):
            text = (STATIC / name).read_text(encoding="utf-8")
            for match in re.finditer(r'\b(?:t|L2)\(\s*(["`])(?:\\.|(?!\1).)*\1\s*,\s*(["`])((?:\\.|(?!\2).)*)\2', text):
                self.assertIsNone(re.search(r"[А-Яа-яЁё]", match.group(3)), f"{name}: {match.group(0)[:120]}")

if __name__ == "__main__":
    unittest.main()
