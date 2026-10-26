"""Round 40 (r40-note) — MemuNote: several tabs in one AI request + editable .xlsx with styles kept.

Covers (no network): the in-place cell patcher (app/memunote/formats/xlsx_edit.py: only touched cells/parts change,
style index kept, inline strings, formulas + fullCalcOnLoad + calcChain dropped, shared formulas written out, new
sheets registered), the reader additions (formulas, looks, cell styles, widths, merges), the edit protocol
(doc=, set_cells / add_column / add_sheet, new_document kind=csv), the multi-document prompt with its budget,
/api/sheet_save (save, conflict, Save as, .ods refused), the formula engine (static/js/formula.js in Node), i18n
and wiring, and a browser run on the real MemuNote server with a fake Proxy: grid editing → save → file; three tabs
→ the agent's edits routed to another tab and a new document; a spreadsheet edit preview → apply → undo.
"""
from __future__ import annotations

import http.client
import http.server
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
JS = ROOT / "app" / "memunote" / "static" / "js"
NODE = shutil.which("node")
CHROME = os.environ.get("MEMU_TEST_CHROMIUM", "/workspace/work/chromium/chrome")

from app.memunote import edits, prompts  # noqa: E402
from app.memunote.formats import sheets, xlsx_edit  # noqa: E402

NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'

def make_xlsx() -> bytes:
    """A small styled workbook written by hand (no openpyxl): header style, number format, widths, a merge,
    a shared formula, a second sheet, sharedStrings and a calcChain."""
    parts = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/><Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/><Override PartName="/xl/calcChain.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.calcChain+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<workbook {NS}><sheets><sheet name="Продажи" sheetId="1" r:id="rId1"/><sheet name="Справка" sheetId="2" r:id="rId2"/></sheets><calcPr calcId="191029"/></workbook>',
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/><Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/><Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/calcChain" Target="calcChain.xml"/></Relationships>',
        "xl/styles.xml": f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><numFmts count="1"><numFmt numFmtId="164" formatCode="#,##0.00 &quot;₽&quot;"/></numFmts><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF336699"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" applyFont="1" applyFill="1"><alignment horizontal="center"/></xf><xf numFmtId="164" fontId="0" fillId="0" borderId="0" applyNumberFormat="1"/><xf numFmtId="14" fontId="0" fillId="0" borderId="0" applyNumberFormat="1"/></cellXfs></styleSheet>',
        "xl/sharedStrings.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="6" uniqueCount="6"><si><t>Товар</t></si><si><t>Цена</t></si><si><t>Кол-во</t></si><si><t>Сумма</t></si><si><t>Яблоки</t></si><si><t>Груши</t></si></sst>',
        "xl/worksheets/sheet1.xml": f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<worksheet {NS}><dimension ref="A1:D3"/><sheetViews><sheetView workbookViewId="0"/></sheetViews><cols><col min="1" max="1" width="24" customWidth="1"/></cols><sheetData>'
            '<row r="1" spans="1:4"><c r="A1" s="1" t="s"><v>0</v></c><c r="B1" s="1" t="s"><v>1</v></c><c r="C1" s="1" t="s"><v>2</v></c><c r="D1" s="1" t="s"><v>3</v></c></row>'
            '<row r="2" spans="1:4"><c r="A2" t="s"><v>4</v></c><c r="B2" s="2"><v>120</v></c><c r="C2"><v>3</v></c><c r="D2" s="2"><f t="shared" ref="D2:D3" si="0">B2*C2</f><v>360</v></c></row>'
            '<row r="3" spans="1:4"><c r="A3" t="s"><v>5</v></c><c r="B3" s="2"><v>150</v></c><c r="C3"><v>2</v></c><c r="D3" s="2"><f t="shared" si="0"/><v>300</v></c></row>'
            '</sheetData><mergeCells count="1"><mergeCell ref="F1:G2"/></mergeCells><pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" footer="0.3"/></worksheet>',
        "xl/worksheets/sheet2.xml": f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<worksheet {NS}><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>не трогать</t></is></c><c r="B1" s="3"><v>46000</v></c></row></sheetData></worksheet>',
        "xl/calcChain.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<calcChain xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><c r="D2" i="1"/><c r="D3"/></calcChain>',
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in parts.items():
            z.writestr(name, text.encode("utf-8"))
    return out.getvalue()

def entries(data: bytes):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {n: z.read(n) for n in z.namelist()}

class PatcherTest(unittest.TestCase):
    def test_reader_gives_formulas_looks_styles_widths_merges(self) -> None:
        r = sheets.read_xlsx(make_xlsx())
        s = r["sheets"][0]
        self.assertTrue(r["editable"])
        self.assertEqual(s["formulas"], {"1,3": "B2*C2", "2,3": "B3*C3"})
        self.assertEqual(s["widths"], {"0": 173})
        self.assertEqual(s["merges"], ["F1:G2"])
        look = r["looks"][s["styles"]["0,0"]]
        self.assertEqual((look["b"], look["color"], look["fill"], look["align"]), ("1", "#FFFFFF", "#336699", "center"))
        self.assertEqual(r["looks"][s["styles"]["1,1"]]["fmt"], '#,##0.00 "₽"')
        self.assertNotIn("fmt", r["looks"][3])

    def test_only_touched_cells_change_and_styles_stay(self) -> None:
        data = make_xlsx()
        out = xlsx_edit.patch_xlsx(data, {"sheets": [{"name": "Продажи", "cells": {
            "B2": {"v": "130"}, "A4": {"v": "Итого"}, "D4": {"f": "SUM(D2:D3)", "v": "690"}, "E1": {"v": " с пробелом"}}}]})
        before, after = entries(data), entries(out)
        for name in ("xl/styles.xml", "xl/sharedStrings.xml", "xl/worksheets/sheet2.xml", "_rels/.rels"):
            self.assertEqual(before[name], after[name], name)
        self.assertNotIn("xl/calcChain.xml", after)
        self.assertNotIn("calcChain", after["xl/_rels/workbook.xml.rels"].decode())
        self.assertNotIn("calcChain", after["[Content_Types].xml"].decode())
        self.assertIn('fullCalcOnLoad="1"', after["xl/workbook.xml"].decode())
        sheet = after["xl/worksheets/sheet1.xml"].decode()
        self.assertIn('<c r="B2" s="2"><v>130</v></c>', sheet)
        self.assertIn('<row r="1"><c r="A1" s="1" t="s"><v>0</v></c>', sheet.replace(' spans="1:4"', ""))
        self.assertIn('<c r="E1" t="inlineStr"><is><t xml:space="preserve"> с пробелом</t></is></c>', sheet)
        self.assertIn('<row r="4"><c r="A4" t="inlineStr"><is><t>Итого</t></is></c><c r="D4"><f>SUM(D2:D3)</f><v>690</v></c></row>', sheet)
        self.assertIn('<dimension ref="A1:E4"/>', sheet)
        self.assertIn("<cols><col min=\"1\" max=\"1\" width=\"24\" customWidth=\"1\"/></cols>", sheet)
        self.assertIn('<mergeCell ref="F1:G2"/>', sheet)
        r = sheets.read_xlsx(out)["sheets"][0]
        self.assertEqual(r["rows"][1][1], "130")
        self.assertEqual(r["formulas"]["3,3"], "SUM(D2:D3)")
        self.assertEqual(r["rows"][3][3], "690")

    def test_untouched_package_without_formula_changes_keeps_calcchain(self) -> None:
        out = xlsx_edit.patch_xlsx(make_xlsx(), {"sheets": [{"name": "Справка", "cells": {"C1": {"v": "x"}}}]})
        after = entries(out)
        self.assertIn("xl/calcChain.xml", after)
        self.assertEqual(entries(make_xlsx())["xl/workbook.xml"], after["xl/workbook.xml"])
        self.assertEqual(entries(make_xlsx())["xl/worksheets/sheet1.xml"], after["xl/worksheets/sheet1.xml"])

    def test_editing_a_shared_formula_master_writes_out_its_dependents(self) -> None:
        out = xlsx_edit.patch_xlsx(make_xlsx(), {"sheets": [{"name": "Продажи", "cells": {"D2": {"f": "B2*C2*2", "v": "720"}}}]})
        sheet = entries(out)["xl/worksheets/sheet1.xml"].decode()
        self.assertIn('<c r="D3" s="2"><f>B3*C3</f><v>300</v></c>', sheet)
        self.assertNotIn('t="shared"', sheet)

    def test_date_cell_typed_as_iso_becomes_a_serial_and_clearing_keeps_the_style(self) -> None:
        out = xlsx_edit.patch_xlsx(make_xlsx(), {"sheets": [{"name": "Справка", "cells": {"B1": {"v": "2026-03-01"}}},
                                                            {"name": "Продажи", "cells": {"B3": {"v": ""}}}]})
        e = entries(out)
        self.assertIn('<c r="B1" s="3"><v>46082</v></c>', e["xl/worksheets/sheet2.xml"].decode())
        self.assertIn('<c r="B3" s="2"/>', e["xl/worksheets/sheet1.xml"].decode())
        self.assertEqual(sheets.read_xlsx(out)["sheets"][1]["rows"][0][1], "2026-03-01")

    def test_new_sheet_is_registered_everywhere(self) -> None:
        out = xlsx_edit.patch_xlsx(make_xlsx(), {"new_sheets": [{"name": "Справка"}], "sheets": [{"name": "Справка (2)", "cells": {"A1": {"v": "ok"}}}]})
        e = entries(out)
        self.assertIn('name="Справка (2)" sheetId="3"', e["xl/workbook.xml"].decode())
        self.assertIn("worksheets/sheet3.xml", e["xl/_rels/workbook.xml.rels"].decode())
        self.assertIn("/xl/worksheets/sheet3.xml", e["[Content_Types].xml"].decode())
        r = sheets.read_xlsx(out)
        self.assertEqual([s["name"] for s in r["sheets"]], ["Продажи", "Справка", "Справка (2)"])
        self.assertEqual(r["sheets"][2]["rows"], [["ok"]])

    def test_shift_formula_and_errors(self) -> None:
        self.assertEqual(xlsx_edit.shift_formula('SUM(B2:B9)+$A$1+A$2+\'X\'!C3&"B2"+LOG10(A1)', 1, 1), 'SUM(C3:C10)+$A$1+B$2+\'X\'!D4&"B2"+LOG10(B2)')
        self.assertEqual(xlsx_edit.shift_formula("A1", -1, 0), "#REF!")
        with self.assertRaises(xlsx_edit.SheetEditError):
            xlsx_edit.patch_xlsx(make_xlsx(), {"sheets": [{"name": "Нет", "cells": {"A1": {"v": "1"}}}]})
        with self.assertRaises(xlsx_edit.SheetEditError):
            xlsx_edit.patch_xlsx(make_xlsx(), {"sheets": [{"name": "Продажи", "cells": {"1A": {"v": "1"}}}]})

class ProtocolTest(unittest.TestCase):
    def test_sheet_ops_doc_attribute_and_csv_documents(self) -> None:
        _, found = edits.parse_all('Ок.\n<edit op="set_cells" doc="d1" sheet="Продажи" range="$D$5"><text>=SUM(D2:D4)</text></edit>\n'
                                   '<edit op="set_cells" range="Лист 2!E2:E4"><formula>D2*1.2</formula></edit>\n'
                                   '<edit op="add_column" sheet="S" header="НДС"><text>=D2*0.2</text></edit>\n'
                                   '<edit op="add_sheet" name="Итоги"></edit>\n<edit op="set_cells" range="zz"><text>1</text></edit>\n'
                                   '<edit op="new_document" kind="csv" title="Свод"><text>a,b</text></edit>\n'
                                   '<edit op="replace" doc="d2"><find>x</find><text>y</text></edit>')
        self.assertEqual(found[0], {"op": "set_cells", "text": "=SUM(D2:D4)", "sheet": "Продажи", "range": "D5", "doc": "d1", "index": 0})
        self.assertEqual((found[1]["sheet"], found[1]["range"], found[1]["formula"]), ("Лист 2", "E2:E4", "=D2*1.2"))
        self.assertEqual((found[2]["op"], found[2]["header"]), ("add_column", "НДС"))
        self.assertEqual((found[3]["op"], found[3]["name"]), ("add_sheet", "Итоги"))
        self.assertEqual(found[4]["kind"], "csv")
        self.assertEqual(found[5]["doc"], "d2")

    def test_prompt_has_every_document_with_ids_budget_and_protocols(self) -> None:
        body = {"action": "agent", "messages": [{"role": "user", "content": "сведи"}], "context": {"name": "a.md", "kind": "markdown", "text": "# A"},
                "docs": [{"id": "d1", "name": "b.xlsx", "kind": "sheet", "text": "## Sheet: S (2 rows × 2 columns)\n\tA\tB"},
                         {"id": "d2", "name": "big.txt", "kind": "text", "text": "x" * 300000, "total_chars": 300000}]}
        built = prompts.build_messages(body, {"ai_context_chars": 60000}, "ru", False)
        system = built["messages"][0]["content"]
        self.assertIn("SEVERAL DOCUMENTS", system)
        self.assertIn("SPREADSHEETS", system)
        joined = "\n".join(m["content"] for m in built["messages"])
        self.assertIn('<document id="d1" name="b.xlsx" kind="sheet"', joined)
        self.assertIn('<document id="d2" name="big.txt" kind="text" chars="300000" excerpt="true">', joined)
        self.assertIn('<document id="d0" name="a.md"', joined)
        self.assertLess(len(joined), 200000)
        chat = prompts.build_messages({**body, "action": "chat"}, {}, "ru", False)
        self.assertIn('<document id="d1"', "\n".join(m["content"] for m in chat["messages"]))
        single = prompts.build_messages({**body, "docs": []}, {}, "ru", False)
        self.assertNotIn("SEVERAL DOCUMENTS", single["messages"][0]["content"])
        self.assertNotIn('id="d0"', "\n".join(m["content"] for m in single["messages"]))

class ApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="r40note-")
        self.patch = mock.patch.dict(os.environ, {"MEMUAI_DATA_DIR": os.path.join(self.tmp.name, "data")})
        self.patch.start()
        from app.memunote import server
        self.server = server
        self.app = server.NoteApp([])
        self.httpd, _ = server.start(self.app)
        self.dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.patch.stop()
        self.tmp.cleanup()

    def call(self, path, body):
        conn = http.client.HTTPConnection("127.0.0.1", self.app.port, timeout=30)
        conn.request("POST", path, body=json.dumps(body).encode(), headers={"X-MN-Token": self.app.token, "Host": f"127.0.0.1:{self.app.port}", "Content-Type": "application/json"})
        resp = conn.getresponse()
        data = json.loads(resp.read() or b"null")
        conn.close()
        return resp.status, data

    def test_save_conflict_save_as_and_ods_refused(self) -> None:
        f = self.dir / "book.xlsx"
        f.write_bytes(make_xlsx())
        status, opened = self.call("/api/open", {"path": str(f)})
        self.assertEqual(status, 200, opened)
        self.assertTrue(opened.get("editable"))
        changes = {"sheets": [{"name": "Продажи", "cells": {"B2": {"v": "99"}}}]}
        status, data = self.call("/api/sheet_save", {"path": str(f), "mtime": 1, "changes": changes})
        self.assertTrue(data.get("conflict"), data)
        status, data = self.call("/api/sheet_save", {"path": str(f), "mtime": opened["mtime"], "changes": changes})
        self.assertTrue(data["ok"], data)
        self.assertEqual(data["sheets"][0]["rows"][1][1], "99")
        self.assertEqual(sheets.read_xlsx(f.read_bytes())["sheets"][0]["rows"][1][1], "99")
        copy = self.dir / "copy.xlsx"
        status, data = self.call("/api/sheet_save", {"path": str(f), "target": str(copy), "changes": {"sheets": [{"name": "Продажи", "cells": {"B2": {"v": "1"}}}]}})
        self.assertTrue(data["ok"], data)
        self.assertEqual(sheets.read_xlsx(copy.read_bytes())["sheets"][0]["rows"][1][1], "1")
        self.assertEqual(sheets.read_xlsx(f.read_bytes())["sheets"][0]["rows"][1][1], "99")
        ods = self.dir / "t.ods"
        ods.write_bytes(b"PK")
        status, data = self.call("/api/sheet_save", {"path": str(ods), "changes": changes})
        self.assertEqual((status, data.get("error")), (400, "not_editable"))
        other = self.dir / "never-opened.xlsx"
        other.write_bytes(make_xlsx())
        status, data = self.call("/api/sheet_save", {"path": str(other), "changes": changes})
        self.assertEqual(status, 403)

@unittest.skipUnless(NODE, "node is not installed")
class FormulaEngineTest(unittest.TestCase):
    def test_functions_errors_cycles_and_fill_rules(self) -> None:
        script = """
import { Workbook, shiftFormula, display } from %s;
const s1 = { name: "Продажи", rows: [["Товар","Цена","Кол-во","Сумма"],["Яблоки","120","3",""],["Груши","150","2",""],["Сливы","90","5",""],["Итого","","",""]], formulas: {"1,3":"B2*C2","2,3":"B3*C3","3,3":"B4*C4","4,3":"SUM(D2:D4)"} };
const f = {"0,0":"VLOOKUP(\\"Груши\\",'Продажи'!A:D,4,FALSE)","1,0":"IF(Продажи!D5>1000,\\"много\\",\\"мало\\")","2,0":"FOO(1)","3,0":"COUNTIF(Продажи!B2:B4,\\">100\\")&\\" шт\\"","4,0":"IFERROR(1/0,\\"err\\")","5,0":"ROUND(AVERAGE(Продажи!B2:B4),1)","6,0":"SUMIF(Продажи!A2:A4,\\"Г*\\",Продажи!D2:D4)","7,0":"A8","8,0":"1/0","9,0":"INDEX(Продажи!A1:D5,MATCH(\\"Сливы\\",Продажи!A1:A5,0),2)","10,0":"TEXT(0.256,\\"0.0%%\\")","11,0":"AND(TRUE,Продажи!B2>100)","12,0":"Продажи!B2:B3"};
const wb = new Workbook([s1, { name: "S2", rows: [["cached"]], formulas: f }]);
const out = []; for (let r = 0; r < 13; r++) out.push([wb.text("S2", r, 0), wb.cachedOnly("S2", r, 0)]);
console.log(JSON.stringify({ out, shift: shiftFormula("SUM(D2:D4)+$A$1+'X'!B3&\\"A1\\"", 1, 1) }));
""" % json.dumps((JS / "formula.js").as_uri())
        res = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr)
        data = json.loads(res.stdout)
        texts = [x[0] for x in data["out"]]
        self.assertEqual(texts[:2], ["300", "много"])
        self.assertEqual(data["out"][2], ["", True])
        self.assertEqual(texts[3:9], ["2 шт", "err", "120", "300", "#CYCLE!", "#DIV/0!"])
        self.assertEqual(texts[9:13], ["90", "25.6%", "TRUE", "#VALUE!"])
        self.assertEqual(data["shift"], "SUM(E3:E5)+$A$1+'X'!C4&\"A1\"")

class WiringTest(unittest.TestCase):
    def test_i18n_wiring_and_build(self) -> None:
        src = (JS / "i18n-r40.js").read_text(encoding="utf-8")
        ru = re.search(r"ru: \{(.*?)\n  \},", src, re.S).group(1)
        en = re.search(r"en: \{(.*?)\n  \},", src, re.S).group(1)
        keys = lambda block: set(re.findall(r"(?:^|[\s,{])(\w+): \"", block))
        self.assertEqual(keys(ru), keys(en))
        self.assertFalse(re.search(r"[А-Яа-яЁё]", en))
        self.assertIn('from "./i18n-r40.js"', (JS / "i18n.js").read_text(encoding="utf-8"))
        app = (JS / "app.js").read_text(encoding="utf-8")
        self.assertIn('import { SheetGrid, SheetData } from "./sheetgrid.js"', app)
        self.assertIn("new SheetGrid(box, tab", app)
        self.assertIn("tabDoc:", app)
        self.assertNotIn("SheetView", app)
        self.assertIn('from "./ai-multi.js"', (JS / "ai.js").read_text(encoding="utf-8"))
        for f in ("build.py", "MemuNote.spec"):
            text = (ROOT / f).read_text(encoding="utf-8")
            self.assertIn("app.memunote.sheets_api", text, f)
            self.assertIn("app.memunote.formats.xlsx_edit", text, f)
        used = set(re.findall(r'\bt\("((?:sg|ai_ctx|ai_op_set|ai_op_add|ai_hunk_no|ai_hunk_bad|ai_target|ai_open_tab|ai_more_cells|ai_hunk_read)[\w]*)"', "".join((JS / n).read_text(encoding="utf-8") for n in ("sheetgrid.js", "ai-multi.js", "ai.js"))))
        self.assertTrue(used <= keys(ru) | {"sheet_read_only"}, used - keys(ru))

    @unittest.skipUnless(NODE, "node is not installed")
    def test_js_syntax(self) -> None:
        for name in ("formula.js", "sheetgrid.js", "ai-multi.js", "ai.js", "ai-edit.js", "app.js", "formatviews.js", "i18n-r40.js"):
            res = subprocess.run([NODE, "--check", str(JS / name)], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, name + res.stderr)

class FakeProxy(http.server.BaseHTTPRequestHandler):
    answer = ""
    seen: list = []

    def log_message(self, *a):
        pass

    def _json(self, obj):
        raw = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/health":
            return self._json({"service": "memuai-proxy"})
        if self.path == "/v1/models":
            return self._json({"data": [{"id": "fake-model"}]})
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path != "/v1/chat/completions":
            self.send_response(404)
            self.end_headers()
            return
        FakeProxy.seen.append(body)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        text = FakeProxy.answer
        for i in range(0, len(text), 40):
            self.wfile.write(b"data: " + json.dumps({"choices": [{"delta": {"content": text[i:i + 40]}}]}).encode() + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")

def _playwright():
    try:
        import playwright.sync_api  # noqa: F401
        return os.path.exists(CHROME)
    except Exception:  # noqa: BLE001
        return False

@unittest.skipUnless(_playwright(), "Playwright + Chromium not available")
class BrowserTest(ApiTest):
    def setUp(self) -> None:
        super().setUp()
        self.proxy = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeProxy)
        threading.Thread(target=self.proxy.serve_forever, daemon=True).start()
        self.call("/api/settings", {"language": "ru", "restore_session": False, "proxy_url": f"http://127.0.0.1:{self.proxy.server_address[1]}",
                                    "ai_model": "fake-model", "ai_memory": False})

    def tearDown(self) -> None:
        self.proxy.shutdown()
        self.proxy.server_close()
        super().tearDown()

    def open(self, p, files):
        b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        pg = b.new_page(viewport={"width": 1360, "height": 860})
        self.errors = []
        pg.on("pageerror", lambda e: self.errors.append(str(e)))
        for f in files:
            self.app.allow(str(f))
        self.app.initial_files = [str(f) for f in files]
        pg.goto(f"http://127.0.0.1:{self.app.port}/index.html#t={self.app.token}")
        pg.wait_for_selector("body.ready", timeout=20000)
        time.sleep(1.0)
        return b, pg

    def wait_idle(self, pg, timeout=30):
        t0 = time.time()
        time.sleep(0.6)
        while time.time() - t0 < timeout and pg.query_selector(".aip.busy"):
            time.sleep(0.2)

    def goto_cell(self, pg, ref):
        pg.click(".sg-name")
        pg.fill(".sg-name", ref)
        pg.keyboard.press("Enter")

    def test_grid_edit_fill_undo_and_save_keep_the_file_styles(self) -> None:
        from playwright.sync_api import sync_playwright
        f = self.dir / "book.xlsx"
        f.write_bytes(make_xlsx())
        with sync_playwright() as p:
            b, pg = self.open(p, [f])
            try:
                pg.wait_for_selector(".sg-c")
                self.assertIn("120,00", pg.inner_text(".sg-cells"))
                self.assertIn("font-weight:650", pg.eval_on_selector(".sg-c", "e => e.getAttribute('style')"))
                self.goto_cell(pg, "B2")
                pg.keyboard.type("130")
                pg.keyboard.press("Enter")
                self.assertEqual(pg.input_value(".sg-name"), "B3")
                self.goto_cell(pg, "E2")
                pg.keyboard.type("=D2*0.2")
                pg.keyboard.press("Enter")
                self.goto_cell(pg, "E2")
                self.assertEqual(pg.input_value(".sg-input"), "=D2*0.2")
                box = pg.eval_on_selector(".sg-fill", "e => { const r = e.getBoundingClientRect(); return [r.x + 4, r.y + 4]; }")
                pg.mouse.move(*box)
                pg.mouse.down()
                pg.mouse.move(box[0], box[1] + 20, steps=4)
                pg.mouse.up()
                self.goto_cell(pg, "E3")
                self.assertEqual(pg.input_value(".sg-input"), "=D3*0.2")
                pg.focus(".sg-scroll")
                pg.keyboard.press("Control+z")
                self.assertEqual(pg.input_value(".sg-input"), "")
                pg.keyboard.press("Control+y")
                self.assertEqual(pg.input_value(".sg-input"), "=D3*0.2")
                self.assertIn("dirty", pg.eval_on_selector(".tab.active", "e => e.className"))
                pg.keyboard.press("Control+s")
                time.sleep(1.2)
                self.assertNotIn("dirty", pg.eval_on_selector(".tab.active", "e => e.className"))
                self.assertEqual(self.errors, [])
            finally:
                b.close()
        before, after = entries(make_xlsx()), entries(f.read_bytes())
        for name in ("xl/styles.xml", "xl/sharedStrings.xml", "xl/worksheets/sheet2.xml"):
            self.assertEqual(before[name], after[name], name)
        r = sheets.read_xlsx(f.read_bytes())["sheets"][0]
        self.assertEqual(r["rows"][1][1], "130")
        self.assertEqual((r["formulas"]["1,4"], r["formulas"]["2,4"]), ("D2*0.2", "D3*0.2"))
        self.assertEqual((r["rows"][1][4], r["rows"][2][4]), ("78", "60"))
        self.assertIn('<c r="B2" s="2"><v>130</v></c>', after["xl/worksheets/sheet1.xml"].decode())

    def test_three_tabs_edits_go_to_their_tabs_and_a_sheet_preview_applies_and_undoes(self) -> None:
        from playwright.sync_api import sync_playwright
        a, c, x = self.dir / "a.md", self.dir / "c.txt", self.dir / "book.xlsx"
        a.write_text("# Январь\n\nЯблоки: 120\n", encoding="utf-8")
        c.write_text("Февраль: яблоки 140\n", encoding="utf-8")
        x.write_bytes(make_xlsx())
        FakeProxy.answer = ('Сделаю.\n<edit op="replace" doc="d1"><find>Февраль: яблоки 140</find><text>Февраль: яблоки 150</text></edit>\n'
                            '<edit op="set_cells" doc="d2" sheet="Продажи" range="D4"><text>=SUM(D2:D3)</text></edit>\n'
                            '<edit op="new_document" title="Свод" kind="csv"><text>Месяц,Кг\nЯнварь,120\nФевраль,150</text></edit>')
        with sync_playwright() as p:
            b, pg = self.open(p, [c, x, a])
            try:
                pg.click(".tab[title$='a.md']")
                pg.keyboard.press("Control+Shift+L")
                pg.wait_for_selector(".aip textarea")
                time.sleep(0.8)
                pg.click(".aip [data-m=edit]")
                self.assertEqual(pg.eval_on_selector_all(".aip-tabchip", "els => els.map(e => e.innerText.trim())"), ["c.txt", "book.xlsx"])
                pg.click(".aip-tabchip >> nth=0")
                pg.fill(".aip textarea", "Сведи всё @book.xlsx")
                pg.keyboard.press("Escape")
                pg.focus(".aip textarea")
                pg.keyboard.press("End")
                pg.keyboard.press("Enter")
                self.wait_idle(pg)
                sent = FakeProxy.seen[-1]["messages"]
                joined = "\n".join(m["content"] for m in sent)
                self.assertIn('<document id="d1" name="c.txt"', joined)
                self.assertIn('<document id="d2" name="book.xlsx" kind="sheet"', joined)
                self.assertIn("=B2*C2 → 360", joined)
                cards = pg.eval_on_selector_all(".aip-card:not([hidden])", "els => els.map(e => e.innerText)")
                self.assertEqual(len(cards), 3, cards)
                self.assertIn("c.txt", cards[1])
                self.assertIn("Продажи!D4", cards[2])
                self.assertIn("=SUM(D2:D3) → 660", cards[2])
                pg.click(".aip-card:nth-of-type(2) [data-h=all]") if pg.query_selector(".aip-card:nth-of-type(2) [data-h=all]") else pg.click(".aip-card:nth-of-type(2) [data-h=accept]")
                time.sleep(0.5)
                self.assertIn("Февраль: яблоки 150", pg.inner_text(".cm-content"))
                self.assertIn("c.txt", pg.inner_text(".tab.active"))
                sheet_card = pg.query_selector_all(".aip-card:not([hidden])")[2]
                sheet_card.query_selector("[data-h=accept]").click()
                time.sleep(0.6)
                self.assertIn("book.xlsx", pg.inner_text(".tab.active"))
                self.goto_cell(pg, "D4")
                self.assertEqual(pg.input_value(".sg-input"), "=SUM(D2:D3)")
                self.assertIn("660", pg.inner_text(".sg-cells"))
                sheet_card = pg.query_selector_all(".aip-card:not([hidden])")[2]
                sheet_card.query_selector("[data-h=undo]").click()
                time.sleep(0.3)
                self.goto_cell(pg, "D4")
                self.assertEqual(pg.input_value(".sg-input"), "")
                first = pg.query_selector_all(".aip-card:not([hidden])")[0]
                first.query_selector("[data-h=accept]").click()
                time.sleep(0.6)
                self.assertIn("Свод.csv", pg.inner_text(".tab.active"))
                self.assertEqual(self.errors, [])
            finally:
                b.close()

if __name__ == "__main__":
    unittest.main()
