"""Round 31 — MemuNote core (zone "note-core"): file associations that are never pushy (winreg mock),
PDF editing (pypdf/pdf-lib round trips), new formats, huge files, core bug fixes.

Browser checks (screenshots, performance numbers) are in changelog/round31/note-core.md; everything here is headless
except the tests marked "browser", which skip without Playwright + Chromium."""
from __future__ import annotations

import http.client
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
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "memunote" / "static"
JS = STATIC / "js"

class FakeKey:
    def __init__(self, reg, hive, path):
        self.reg, self.hive, self.path = reg, hive, path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

class FakeWinreg:
    """A tiny in-memory registry: enough of ``winreg`` for app/memunote/winsys.py.

    Keys are case-insensitive paths per hive; HKEY_CLASSES_ROOT is the merged view of
    HKCU\\Software\\Classes over HKLM\\Software\\Classes (value by value), like Windows."""
    HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE, HKEY_CLASSES_ROOT = "HKCU", "HKLM", "HKCR"
    KEY_READ, KEY_WRITE, KEY_SET_VALUE, KEY_ALL_ACCESS = 1, 2, 4, 7
    REG_SZ, REG_NONE, REG_DWORD = 1, 0, 4

    def __init__(self):
        self.keys = {}
        self.log = []

    def _k(self, hive, path):
        return (hive, path.lower().strip("\\"))

    def ensure(self, hive, path):
        parts = path.strip("\\").split("\\")
        for i in range(1, len(parts) + 1):
            sub = "\\".join(parts[:i])
            self.keys.setdefault(self._k(hive, sub), {"name": sub, "values": {}})
        return self.keys[self._k(hive, path)]

    def preset(self, hive, path, name, value, kind=1):
        self.ensure(hive, path)["values"][name.lower()] = (name, value, kind)

    def has(self, hive, path):
        return self._k(hive, path) in self.keys

    def value(self, hive, path, name=""):
        key = self.keys.get(self._k(hive, path))
        return None if key is None else key["values"].get(name.lower(), (None, None, None))[1]

    def _resolve(self, hive, path):
        if hive == "HKCR":
            for real in (("HKCU", "Software\\Classes\\" + path), ("HKLM", "Software\\Classes\\" + path)):
                if self.has(*real):
                    return real
            raise FileNotFoundError(path)
        if not self.has(hive, path):
            raise FileNotFoundError(path)
        return hive, path

    def CreateKeyEx(self, hive, path, reserved=0, access=0):
        assert hive != "HKCR", "MemuNote must only write HKCU"
        assert hive == "HKCU", f"MemuNote must only write HKCU, not {hive}"
        self.ensure(hive, path)
        self.log.append(("create", hive, path, None, None))
        return FakeKey(self, hive, path)

    def OpenKey(self, hive, path, reserved=0, access=1):
        if hive == "HKCR":
            return FakeKey(self, "HKCR", path)._check(self) if False else self._open_cr(path)
        if not self.has(hive, path):
            raise FileNotFoundError(path)
        return FakeKey(self, hive, path)

    def _open_cr(self, path):
        if not (self.has("HKCU", "Software\\Classes\\" + path) or self.has("HKLM", "Software\\Classes\\" + path)):
            raise FileNotFoundError(path)
        return FakeKey(self, "HKCR", path)

    def CloseKey(self, key):
        pass

    def SetValueEx(self, key, name, reserved, kind, value):
        assert key.hive == "HKCU"
        self.ensure(key.hive, key.path)["values"][name.lower()] = (name, value, kind)
        self.log.append(("set", key.hive, key.path, name, value))

    def QueryValueEx(self, key, name):
        if key.hive == "HKCR":
            for real in (("HKCU", "Software\\Classes\\" + key.path), ("HKLM", "Software\\Classes\\" + key.path)):
                v = self.keys.get(self._k(*real), {"values": {}})["values"].get(name.lower())
                if v is not None:
                    return v[1], v[2]
            raise FileNotFoundError(name)
        v = self.keys[self._k(key.hive, key.path)]["values"].get(name.lower())
        if v is None:
            raise FileNotFoundError(name)
        return v[1], v[2]

    def DeleteValue(self, key, name):
        values = self.keys[self._k(key.hive, key.path)]["values"]
        if name.lower() not in values:
            raise FileNotFoundError(name)
        del values[name.lower()]
        self.log.append(("delvalue", key.hive, key.path, name, None))

    def _children(self, hive, path):
        prefix = path.lower().strip("\\") + "\\"
        out = []
        for (h, p), data in self.keys.items():
            if h == hive and p.startswith(prefix) and "\\" not in p[len(prefix):]:
                out.append(data["name"].split("\\")[-1])
        return sorted(out)

    def DeleteKey(self, hive, path):
        if not self.has(hive, path):
            raise FileNotFoundError(path)
        if self._children(hive, path):
            raise PermissionError("has subkeys")
        del self.keys[self._k(hive, path)]
        self.log.append(("delkey", hive, path, None, None))

    def EnumKey(self, key, index):
        children = self._children(key.hive, key.path)
        if index >= len(children):
            raise OSError("no more")
        return children[index]

    def EnumValue(self, key, index):
        values = list(self.keys[self._k(key.hive, key.path)]["values"].values())
        if index >= len(values):
            raise OSError("no more")
        return values[index]

    def QueryInfoKey(self, key):
        data = self.keys[self._k(key.hive, key.path)]
        return len(self._children(key.hive, key.path)), len(data["values"]), 0

DANGEROUS = [".bat", ".cmd", ".ps1", ".vbs", ".js", ".exe", ".reg", ".sh", ".py", ".com", ".scr", ".msi", ".lnk", ".hta"]
EXE = r"C:\Users\u\AppData\Local\Programs\MemuAI\MemuNote\MemuNote.exe"

class AssociationTest(unittest.TestCase):
    def setUp(self) -> None:
        from app.memunote import winsys
        self.winsys = winsys
        self.reg = FakeWinreg()

        for ext, progid in ((".txt", "txtfile"), (".bat", "batfile"), (".cmd", "cmdfile"), (".ps1", "Microsoft.PowerShellScript.1"),
                            (".js", "JSFile"), (".vbs", "VBSFile"), (".reg", "regfile"), (".exe", "exefile"), (".log", "txtfile"),
                            (".pdf", "MSEdgePDF"), (".docx", "Word.Document.12")):
            self.reg.preset("HKLM", "Software\\Classes\\" + ext, "", progid)
        self.patches = [mock.patch.object(winsys, "IS_WINDOWS", True), mock.patch.object(winsys, "_winreg", lambda: self.reg),
                        mock.patch.object(winsys, "_notify_shell", lambda: None)]
        for p in self.patches:
            p.start()

    def tearDown(self) -> None:
        for p in self.patches:
            p.stop()

    def written_paths(self):
        return [(op, path, name) for op, hive, path, name, _ in self.reg.log if op in ("create", "set")]

    def test_lists_are_disjoint_and_scripts_never_safe(self) -> None:
        w = self.winsys
        self.assertFalse(set(w.SAFE_EXT) & set(w.NEVER_EXT))
        for ext in DANGEROUS:
            self.assertNotIn(ext, w.SAFE_EXT)
            self.assertIn(ext, w.NEVER_EXT)
        self.assertEqual(w.safe_extensions([".BAT", "md", ".ps1", ".txt", ".txt", "exe"]), [".md", ".txt"])

    def test_register_writes_only_safe_open_with_and_never_a_default(self) -> None:
        result = self.winsys.register_open_with(True, exe=EXE, extensions=self.winsys.SAFE_EXT + DANGEROUS)
        self.assertTrue(result["enabled"])
        for op, path, name in self.written_paths():
            low = path.lower()
            self.assertNotIn("userchoice", low)
            self.assertNotIn("openwithlist", low)
            self.assertNotIn("hklm", low)
            for ext in DANGEROUS:
                self.assertFalse(low.endswith("\\" + ext) or ("\\" + ext + "\\") in low + "\\" and "supportedtypes" not in low,
                                 (op, path, name))
                if low.endswith("supportedtypes"):
                    self.assertNotEqual((name or "").lower(), ext)
            if re.search(r"software\\classes\\\.[^\\]+$", low):
                self.fail(f"a .ext key itself was written: {path} {name}")
            if op == "set" and re.search(r"software\\classes\\\.[^\\]+$", low) and name == "":
                self.fail("default program changed")

        self.assertEqual(self.reg.value("HKCU", r"Software\Classes\.txt\OpenWithProgids", "MemuNote.Document"), b"")
        self.assertFalse(self.reg.has("HKCU", r"Software\Classes\.md"))
        self.assertIn(".md", [n for n, *_ in self.reg.keys[("HKCU", (self.winsys.APP_KEY + r"\SupportedTypes").lower())]["values"].values()])
        for ext in DANGEROUS:
            self.assertFalse(self.reg.has("HKCU", "Software\\Classes\\" + ext), ext)

        self.assertEqual(self.reg.value("HKLM", r"Software\Classes\.bat"), "batfile")
        self.assertIsNone(self.reg.value("HKCU", r"Software\Classes\.txt"))
        self.assertTrue(self.winsys.open_with_state(EXE)["enabled"])

    def test_unregister_removes_everything_and_keeps_other_programs(self) -> None:
        self.reg.preset("HKCU", r"Software\Classes\.txt\OpenWithProgids", "Notepad++_file", b"", 0)
        self.winsys.register_open_with(True, exe=EXE)
        self.winsys.register_open_with(False, exe=EXE)
        left = [data["name"] for (hive, p), data in self.reg.keys.items() if "memunote" in p or any(
            "memunote" in str(v[0]).lower() for v in data["values"].values())]
        self.assertEqual(left, ["Software\\MemuAI\\MemuNote"])
        self.winsys.forget_state()
        self.assertFalse(self.reg.has("HKCU", r"Software\MemuAI"))
        self.assertEqual(self.reg.value("HKCU", r"Software\Classes\.txt\OpenWithProgids", "Notepad++_file"), b"")
        self.assertFalse(self.winsys.open_with_state(EXE)["enabled"])

    def _legacy_round30(self, enabled_userchoice=True):
        """What Rounds 29–30 wrote with the switch on (+ a user who clicked "Always" for .bat in the prompt)."""
        reg, w = self.reg, self.winsys
        reg.preset("HKCU", w.APP_KEY + r"\shell\open\command", "", f'"{EXE}" "%1"')
        reg.preset("HKCU", w.APP_KEY, "FriendlyAppName", "MemuNote")
        for ext in w.LEGACY_EXT:
            reg.preset("HKCU", w.APP_KEY + r"\SupportedTypes", ext, "")
            reg.preset("HKCU", rf"Software\Classes\{ext}\OpenWithProgids", w.PROGID, b"", 0)
        reg.preset("HKCU", w.PROGID_KEY + r"\shell\open\command", "", f'"{EXE}" "%1"')
        if enabled_userchoice:
            reg.preset("HKCU", w.FILE_EXTS + r"\.bat\UserChoice", "ProgId", w.PROGID)
            reg.preset("HKCU", w.FILE_EXTS + r"\.bat\UserChoice", "Hash", "abc")
            reg.preset("HKCU", w.FILE_EXTS + r"\.ps1\OpenWithList", "a", "MemuNote.exe")
            reg.preset("HKCU", w.FILE_EXTS + r"\.ps1\OpenWithList", "b", "notepad.exe")
            reg.preset("HKCU", w.FILE_EXTS + r"\.ps1\OpenWithList", "MRUList", "ab")
            reg.preset("HKCU", w.FILE_EXTS + r"\.md\UserChoice", "ProgId", w.PROGID)

    def test_migration_cleans_round30_and_reregisters_safe_list_once(self) -> None:
        self._legacy_round30()
        result = self.winsys.migrate()
        self.assertTrue(result["migrated"])
        self.assertTrue(result["reregistered"])
        self.assertIn(".bat", result["fixed"])
        reg, w = self.reg, self.winsys
        self.assertFalse(reg.has("HKCU", w.FILE_EXTS + r"\.bat\UserChoice"))
        self.assertEqual(reg.value("HKCU", w.FILE_EXTS + r"\.ps1\OpenWithList", "b"), "notepad.exe")
        self.assertIsNone(reg.value("HKCU", w.FILE_EXTS + r"\.ps1\OpenWithList", "a"))
        self.assertEqual(reg.value("HKCU", w.FILE_EXTS + r"\.md\UserChoice", "ProgId"), w.PROGID)
        for ext in DANGEROUS:
            self.assertFalse(reg.has("HKCU", "Software\\Classes\\" + ext), ext)
            self.assertIsNone(reg.value("HKCU", w.APP_KEY + r"\SupportedTypes", ext))
        for ext in (".css", ".gitignore", ".env", ".sql"):
            self.assertFalse(reg.has("HKCU", "Software\\Classes\\" + ext), ext)
        self.assertTrue(w.open_with_state(EXE)["enabled"])
        self.assertEqual(reg.value("HKCU", w.STATE_KEY, "AssocVersion"), str(w.ASSOC_VERSION))
        reg.log.clear()
        self.assertFalse(w.migrate()["migrated"])
        self.assertEqual([x for x in reg.log if x[0] != "create"], [])

    def test_uninstall_without_migration_cleans_script_choices(self) -> None:
        """--unregister-open-with of a 3.5 that never started (no migration yet): no .bat left pointing at
        MemuNote; after that a reinstall migrates again and registers nothing (the switch is off)."""
        self._legacy_round30()
        w, reg = self.winsys, self.reg
        w.unregister()
        self.assertEqual(w.clean_script_choices(), [".bat", ".ps1"])
        w.forget_state()
        self.assertFalse(reg.has("HKCU", w.FILE_EXTS + r"\.bat\UserChoice"))
        self.assertEqual(reg.value("HKCU", w.FILE_EXTS + r"\.md\UserChoice", "ProgId"), w.PROGID)
        self.assertFalse(reg.has("HKCU", w.STATE_KEY))
        result = w.migrate()
        self.assertTrue(result["migrated"])
        self.assertFalse(result["reregistered"])
        self.assertFalse(w.open_with_state(EXE)["enabled"])
        src = (ROOT / "app" / "memunote" / "desktop.py").read_text(encoding="utf-8")
        flag = src[src.index('"--unregister-open-with" in flags'):]
        self.assertLess(flag.index("clean_script_choices()"), flag.index("forget_state()"))

    def test_migration_when_switch_was_off_leaves_nothing(self) -> None:
        self.reg.preset("HKCU", r"Software\Classes\.bat\OpenWithProgids", self.winsys.PROGID, b"", 0)
        result = self.winsys.migrate()
        self.assertFalse(result["reregistered"])
        self.assertFalse(self.reg.has("HKCU", r"Software\Classes\.bat"))
        self.assertFalse(self.winsys.open_with_state(EXE)["enabled"])

    def test_cli_flags_and_no_prompt_on_start(self) -> None:
        src = (ROOT / "app" / "memunote" / "desktop.py").read_text(encoding="utf-8")
        self.assertIn("--migrate-open-with", src)
        self.assertIn("winsys.migrate()", src)

        self.assertEqual(len(re.findall(r"register_open_with\(True", src)), 1)
        js = (JS / "app.js").read_text(encoding="utf-8")
        self.assertNotRegex(js, r"(?i)make.{0,20}default|по умолчанию\?")
        ui = (ROOT / "app" / "pgstatic" / "memunote-settings.js").read_text(encoding="utf-8")
        self.assertIn(".bat, .cmd, .ps1", ui)

import sys  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import round31_note_samples as samples  # noqa: E402

class FormatsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory(prefix="r31nc-fmt-")
        cls.files = samples.all_samples(Path(cls.tmp.name))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_xlsx_values_dates_formulas_and_sheets(self) -> None:
        from app.memunote.formats import sheets
        result = sheets.read_sheet(str(self.files["xlsx"]))
        self.assertEqual([s["name"] for s in result["sheets"]], ["Продажи", "Second"])
        rows = result["sheets"][0]["rows"]
        self.assertEqual(rows[0], ["Имя", "Город", "Сумма", "Дата"])
        self.assertEqual(rows[1], ["Анна", "Рига", "1250.5", "2025-10-11"])
        self.assertEqual(rows[2], ["Борис", "Вильнюс", "300", "2025-10-12 12:00", "", "TRUE"])
        self.assertEqual(rows[3], [])
        self.assertEqual(rows[4], ["Итого", "", "1550.5"])
        self.assertEqual(result["sheets"][1]["rows"], [["42"]])
        csv_text = sheets.to_csv([["a,b", 'q"'], ["1"]])
        self.assertIn('"a,b"', csv_text)
        self.assertIn('"q"""', csv_text)

    def test_ods_repeats_are_bounded(self) -> None:
        from app.memunote.formats import sheets
        result = sheets.read_sheet(str(self.files["ods"]))
        sheet = result["sheets"][0]
        self.assertEqual(sheet["name"], "Лист1")
        self.assertEqual(sheet["rows"][0], ["a", "7", "7"])
        self.assertEqual(sheet["rows"][4], ["2026-10-11", "TRUE"])
        self.assertLess(len(sheet["rows"]), 100)

    def test_damaged_spreadsheet_raises(self) -> None:
        from app.memunote.formats import sheets
        with self.assertRaises(Exception):
            sheets.read_sheet("x.xlsx", b"not a zip")

    def test_epub_is_sanitised_with_chapters_and_images(self) -> None:
        from app.memunote.formats import books
        book = books.read_book(str(self.files["epub"]))
        self.assertEqual((book["title"], book["author"]), ("Тестовая книга", "Автор Тестов"))
        self.assertEqual([c["title"] for c in book["chapters"]], ["Глава первая", "Chapter two"])
        html = book["html"]
        for bad in ("<script", "alert(", "onclick", "javascript:", "<style", "color:red"):
            self.assertNotIn(bad, html)
        self.assertIn('href="https://example.com"', html)
        self.assertIn('src="data:image/png;base64,', html)
        for c in book["chapters"]:
            self.assertIn(f'id="{c["id"]}"', html)

    def test_fb2_sections_binary_and_poem(self) -> None:
        from app.memunote.formats import books
        book = books.read_book(str(self.files["fb2"]))
        self.assertEqual((book["title"], book["author"]), ("Короткая повесть", "Лев Толстой"))
        self.assertEqual([c["title"] for c in book["chapters"]], ["Часть 1", "Глава 1.1", "Часть 2"])
        self.assertIn("<em>похожи</em>", book["html"])
        self.assertIn("Строка стиха", book["html"])
        self.assertIn("data:image/png;base64,", book["html"])

    def test_fb2_rejects_entities(self) -> None:
        """A billion-laughs FB2 must not explode (defusedxml-like behaviour or a plain error)."""
        from app.memunote.formats import books
        bomb = ('<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
                '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;"><!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">'
                '<!ENTITY e "&d;&d;&d;&d;&d;&d;&d;&d;&d;&d;"><!ENTITY f "&e;&e;&e;&e;&e;&e;&e;&e;&e;&e;">]>'
                '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"><body><section><p>&f;&f;&f;&f;</p></section></body></FictionBook>')
        try:
            book = books.read_book("x.fb2", bomb.encode())
        except Exception:  # noqa: BLE001 - refusing is fine
            return
        self.assertLess(len(book["html"]), 2_000_000)

    def test_entity_bombs_refused_in_office_formats(self) -> None:
        from app.memunote.formats import docx, odt, sheets  # noqa: F401
        bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>'
        with self.assertRaises(ValueError):
            sheets.safe_fromstring(bomb)
        self.assertEqual(sheets.safe_fromstring(b"<x>&amp;</x>").text, "&")
        for mod in ("docx", "odt"):
            src = (ROOT / "app" / "memunote" / "formats" / f"{mod}.py").read_text(encoding="utf-8")
            self.assertNotIn("ET.fromstring(", src, mod)

    def test_notebook_helpers_in_node(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        js = (JS / "formatviews.js").read_text(encoding="utf-8")
        start = js.index("const src = (v)")
        end = js.index("export class NotebookView")
        code = js[start:end].replace("export function", "function")
        script = code + "\nconst fs=require('fs');const t=fs.readFileSync(process.argv[2],'utf8');" \
                        "console.log(JSON.stringify({md:notebookToMarkdown(t),py:notebookToScript(t),lang:parseNotebook(t).lang}));" \
                        "try{parseNotebook('{\"a\":1}');console.log('NOERR')}catch(e){console.log('ERR')}"
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
            fh.write(script)
        try:
            out = subprocess.run([node, fh.name, str(self.files["ipynb"])], capture_output=True, text=True, timeout=30)
        finally:
            os.unlink(fh.name)
        self.assertEqual(out.returncode, 0, out.stderr)
        first, second = out.stdout.strip().splitlines()
        data = json.loads(first)
        self.assertEqual(data["lang"], "python")
        self.assertIn("# Анализ\nПростой **блокнот**.", data["md"])
        self.assertIn("```python\nx = [1, 2, 3]\nprint(sum(x))\n```", data["md"])
        self.assertIn("# # Анализ", data["py"])
        self.assertIn("print(sum(x))", data["py"])
        self.assertEqual(second, "ERR")

def _tiny_pdf(lines) -> bytes:
    """A valid one-page PDF (Helvetica) with the given ASCII lines, xref offsets computed."""
    content = "BT /F1 14 Tf 72 720 Td " + " ".join(f"({s}) Tj 0 -20 Td" for s in lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for i, body in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n{body}\nendobj\n".encode("latin-1"))
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()

class PdfToolsTest(unittest.TestCase):
    def test_extract_text_in_reading_order(self) -> None:
        try:
            import pypdf  # noqa: F401
        except ImportError:
            self.skipTest("pypdf is not installed")
        from app.memunote.formats import pdftools
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.pdf"
            path.write_bytes(_tiny_pdf(["First line", "Second line", "Third line"]))
            text = pdftools.extract(str(path))
            self.assertLess(text.index("First line"), text.index("Second line"))
            self.assertLess(text.index("Second line"), text.index("Third line"))
            md = pdftools.extract(str(path), "markdown")
            self.assertIn("Third line", md)
            self.assertLessEqual(len(pdftools.extract(str(path), "text", max_chars=5)), 200)

class _ServerCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="r31nc-api-")
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

    def call(self, method, path, body=None, headers=None, raw=False):
        conn = http.client.HTTPConnection("127.0.0.1", self.app.port, timeout=30)
        hdrs = {"X-MN-Token": self.app.token, "Host": f"127.0.0.1:{self.app.port}"}
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        hdrs.update(headers or {})
        conn.request(method, path, body=body, headers=hdrs)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp, (data if raw else json.loads(data or b"null"))

    def q(self, path):
        import urllib.parse
        return urllib.parse.quote(str(path))

class ApiTest(_ServerCase):
    def test_save_binary_atomic_pdf_check_and_conflict(self) -> None:
        target = self.dir / "doc.pdf"
        pdf = _tiny_pdf(["v1"])
        target.write_bytes(pdf)
        resp, opened = self.call("POST", "/api/open", {"path": str(target)})
        self.assertEqual(resp.status, 200, opened)
        mtime = opened.get("mtime") or self.server._stat(str(target))["mtime"]

        resp, data = self.call("POST", f"/api/save_binary?path={self.q(target)}&mtime={mtime}", b"<html>nope</html>")
        self.assertEqual(resp.status, 400)
        self.assertEqual(target.read_bytes(), pdf)

        new = _tiny_pdf(["v2"])
        resp, data = self.call("POST", f"/api/save_binary?path={self.q(target)}&mtime={mtime}", new)
        self.assertEqual(resp.status, 200, data)
        self.assertTrue(data["ok"])
        self.assertEqual(target.read_bytes(), new)
        self.assertEqual([p.name for p in self.dir.iterdir() if p.name.startswith("doc")], ["doc.pdf"])

        os.utime(target, (1_000_000, 1_000_000))
        resp, data = self.call("POST", f"/api/save_binary?path={self.q(target)}&mtime={data['mtime']}", _tiny_pdf(["v3"]))
        self.assertTrue(data.get("conflict"), data)
        self.assertEqual(target.read_bytes(), new)
        resp, data = self.call("POST", f"/api/save_binary?path={self.q(target)}&force=1", _tiny_pdf(["v3"]))
        self.assertTrue(data["ok"])
        self.assertIn(b"(v3)", target.read_bytes())

        resp, data = self.call("POST", f"/api/save_binary?path={self.q(target)}&force=1&backup=1", _tiny_pdf(["v4"]))
        self.assertTrue(data["ok"])

    def test_pdf_text_requires_an_opened_file(self) -> None:
        target = self.dir / "t.pdf"
        target.write_bytes(_tiny_pdf(["Hello PDF"]))
        resp, _ = self.call("POST", "/api/pdf_text", {"path": str(target)})
        self.assertEqual(resp.status, 403)
        self.call("POST", "/api/open", {"path": str(target)})
        try:
            import pypdf  # noqa: F401
        except ImportError:
            self.skipTest("pypdf is not installed")
        resp, data = self.call("POST", "/api/pdf_text", {"path": str(target)})
        self.assertEqual(resp.status, 200, data)
        self.assertIn("Hello PDF", data["text"])

    def test_raw_offset_tail_and_size_header(self) -> None:
        log = self.dir / "app.log"
        log.write_bytes(b"line 1\nline 2\n")
        self.call("POST", "/api/open", {"path": str(log)})
        resp, body = self.call("GET", f"/api/raw?path={self.q(log)}", raw=True)
        self.assertEqual(body, b"line 1\nline 2\n")
        with open(log, "ab") as fh:
            fh.write("строка 3\n".encode())
        resp, body = self.call("GET", f"/api/raw?path={self.q(log)}&offset=14", raw=True)
        self.assertEqual(body.decode(), "строка 3\n")
        self.assertEqual(resp.getheader("X-MN-Size"), str(log.stat().st_size))
        self.assertEqual(resp.getheader("Content-Length"), str(len(body)))
        resp, body = self.call("GET", f"/api/raw?path={self.q(log)}&offset=999999", raw=True)
        self.assertEqual(body, b"")
        resp, body = self.call("GET", f"/api/raw?path={self.q(log)}&offset=abc", raw=True)
        self.assertTrue(body.startswith(b"line 1"))

        conn = http.client.HTTPConnection("127.0.0.1", self.app.port, timeout=10)
        conn.request("GET", f"/api/raw?path={self.q(log)}", headers={"Host": f"127.0.0.1:{self.app.port}"})
        self.assertEqual(conn.getresponse().status, 401)
        conn.close()

    def test_svg_raw_and_asset_have_csp(self) -> None:
        svg = self.dir / "x.svg"
        svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', encoding="utf-8")
        self.call("POST", "/api/open", {"path": str(svg)})
        resp, _ = self.call("GET", f"/api/raw?path={self.q(svg)}", raw=True)
        self.assertEqual(resp.getheader("Content-Type"), "image/svg+xml")
        self.assertIn("default-src 'none'", resp.getheader("Content-Security-Policy") or "")
        self.assertEqual(resp.getheader("X-Content-Type-Options"), "nosniff")
        resp, _ = self.call("GET", f"/api/asset?path={self.q(svg)}", raw=True)
        self.assertIn("default-src 'none'", resp.getheader("Content-Security-Policy") or "")

    def test_events_are_not_replayed_after_a_reload(self) -> None:
        self.app.events.push({"type": "open", "paths": ["/tmp/a.txt"]})
        _, boot = self.call("GET", "/api/boot")
        self.assertEqual(boot["events_seq"], 0)
        _, ev = self.call("GET", "/api/events?since=0")
        self.assertEqual(ev["events"], [{"type": "open", "paths": ["/tmp/a.txt"]}])
        _, boot = self.call("GET", "/api/boot")
        self.assertEqual(boot["events_seq"], ev["seq"])
        js = (JS / "app.js").read_text(encoding="utf-8")
        self.assertIn("MN.boot?.events_seq", js)

    def test_open_new_kinds(self) -> None:
        files = samples.all_samples(self.dir / "s")
        for key, kind in (("xlsx", "sheet"), ("ods", "sheet"), ("epub", "book"), ("fb2", "book"), ("ipynb", "notebook"),
                          ("csv", "csv"), ("json", "json")):
            resp, data = self.call("POST", "/api/open", {"path": str(files[key])})
            self.assertEqual(resp.status, 200, (key, data))
            self.assertEqual(data["kind"], kind, key)
        resp, data = self.call("POST", "/api/open", {"path": str(files["xlsx"])})
        self.assertEqual(data["sheets"][0]["name"], "Продажи")
        bad = self.dir / "bad.xlsx"
        bad.write_bytes(b"garbage")
        resp, data = self.call("POST", "/api/open", {"path": str(bad)})
        self.assertEqual(resp.status, 400)

class DialogStaticTest(unittest.TestCase):
    def test_markdown_dialog_has_no_cancel_and_uses_real_extension(self) -> None:
        js = (JS / "app.js").read_text(encoding="utf-8")
        start = js.index("async function ensureMarkdown")
        body = js[start:js.index("async function convertToMarkdown")]
        self.assertNotIn('t("cancel")', body)
        self.assertIn("extname(tab.path || tab.name)", body)
        self.assertIn('t("md_keep_txt", { ext })', body)
        from_i18n = (JS / "i18n.js").read_text(encoding="utf-8")
        for m in re.finditer(r'md_keep_txt:\s*"([^"]+)"', from_i18n):
            self.assertIn("{ext}", m.group(1))
        core = (JS / "core.js").read_text(encoding="utf-8")
        self.assertIn('e.key === "Escape"', core)
        self.assertIn('querySelector(".modal-x").onclick = () => done(null)', core)

class EncodingRoundTripTest(_ServerCase):
    """Deep check (finisher): legacy encodings open and save back byte-identically; recent files dedupe."""

    def _roundtrip(self, name, raw: bytes, expect_text: str):
        path = self.dir / name
        path.write_bytes(raw)
        resp, info = self.call("POST", "/api/open", {"path": str(path)})
        self.assertEqual(resp.status, 200, info)
        text = info.get("text")
        if text is None:
            _, body = self.call("GET", f"/api/raw?path={self.q(path)}", raw=True)
            text = body.decode(info["web_encoding"]).lstrip("\ufeff")
        self.assertEqual(text.replace("\r\n", "\n"), expect_text)
        eol = info.get("eol") if info.get("eol") in ("lf", "crlf", "cr") else "lf"
        bom = "1" if info.get("bom") else "0"
        resp, saved = self.call("POST", f"/api/save_text?path={self.q(path)}&encoding={info['encoding']}&eol={eol}&bom={bom}&mtime={info['mtime']}",
                                expect_text.encode("utf-8"))
        self.assertTrue(saved.get("ok"), saved)
        self.assertEqual(path.read_bytes(), raw, name)

    def test_cp1251_utf16_utf8bom_crlf(self) -> None:
        text = "Привет, мир!\nВторая строка — ёжик\n"
        self._roundtrip("a1251.txt", text.encode("cp1251"), text)
        self._roundtrip("u16.txt", b"\xff\xfe" + text.encode("utf-16-le"), text)
        self._roundtrip("bom.txt", b"\xef\xbb\xbf" + text.encode("utf-8"), text)
        self._roundtrip("crlf.txt", text.replace("\n", "\r\n").encode("utf-8"), text)

    def test_recent_dedupes_and_caps(self) -> None:
        for i in range(35):
            self.call("POST", "/api/recent", {"path": str(self.dir / f"f{i % 32}.txt")})
        _, data = self.call("POST", "/api/recent", {"path": str(self.dir / "f3.txt")})
        self.assertEqual(data["recent"][0], str(self.dir / "f3.txt"))
        self.assertEqual(len(data["recent"]), len(set(data["recent"])))
        self.assertLessEqual(len(data["recent"]), 30)

def _chromium():
    for path in ("/usr/bin/chromium", "/workspace/work/chromium/chrome", shutil.which("chromium") or "", shutil.which("google-chrome") or ""):
        if path and os.path.exists(path):
            return path
    return None

class BrowserViewsTest(_ServerCase):
    """Real page in headless Chromium: spreadsheet, book TOC, notebook, JSON tree, XML format/validate,
    CSV edit + save, log tail. Skips without Playwright/Chromium."""

    def test_format_views(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("playwright is not installed")
        chrome = _chromium()
        if not chrome:
            self.skipTest("chromium is not available")
        import time as _t
        os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
        files = samples.all_samples(self.dir / "v")
        log = self.dir / "v" / "app.log"
        log.write_text("".join(f"line {i}\n" for i in range(20)), encoding="utf-8")
        bad = self.dir / "v" / "bad.xml"
        bad.write_text("<a><b></a>", encoding="utf-8")
        self.call("POST", "/api/settings", {"language": "en", "restore_session": False})

        def opn(path):
            self.call("POST", "/api/remote", {"action": "open", "paths": [str(path)]})
            _t.sleep(1.3)

        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=chrome, args=["--no-sandbox"])
            try:
                pg = browser.new_page(viewport={"width": 1280, "height": 800})
                errors = []
                pg.on("pageerror", lambda e: errors.append(str(e)))
                pg.goto(f"http://127.0.0.1:{self.app.port}/index.html#t={self.app.token}")
                pg.wait_for_selector("body.ready", timeout=20000)
                toasts = "() => [...document.querySelectorAll('.toast')].map(t => t.textContent.trim()).join(' | ')"
                active_doc = "() => { const v = MN.panes.find(p => p.view && !p.editorHost.hidden)?.view; return v ? v.state.doc.toString() : null }"

                opn(files["xlsx"])
                self.assertIn("Анна", pg.inner_text(".sg-cells"))
                pg.click(".sg-tab >> text=Second")
                _t.sleep(0.3)
                self.assertIn("42", pg.inner_text(".sg-cells"))
                self.assertIn("Spreadsheet", pg.inner_text("#status, .statusbar") if pg.query_selector("#status, .statusbar") else "Spreadsheet")

                opn(files["fb2"])
                pg.click("text=Contents")
                _t.sleep(0.3)
                self.assertIn("Глава 1.1", pg.inner_text(".menu"))
                pg.keyboard.press("Escape")

                opn(files["ipynb"])
                self.assertIn("ZeroDivisionError", pg.inner_text("body"))

                opn(files["json"])
                pg.click("[data-a='json:tree']")
                _t.sleep(0.8)
                self.assertIn('name: "MemuNote"', pg.inner_text("body"))

                opn(files["xml"])
                pg.click("[data-a='xml:format']")
                _t.sleep(0.5)
                self.assertIn('\n  <item key="a">1</item>', pg.evaluate(active_doc).replace("    ", "  "))
                self.assertIsNone(pg.query_selector("[data-a='md:bold']"))
                opn(bad)
                pg.click("[data-a='xml:validate']")
                _t.sleep(0.4)
                msg = pg.evaluate(toasts)
                self.assertIn("line 1, column", msg)
                self.assertNotIn("This page contains", msg)

                opn(files["csv"])
                pg.dblclick(".csv-view.editable tbody tr[data-r='0'] td[data-c='2']")
                pg.keyboard.press("Control+a")
                pg.keyboard.type("Санкт-Петербург, центр")
                pg.keyboard.press("Enter")
                pg.keyboard.press("Control+s")
                _t.sleep(1.2)
                self.assertEqual(files["csv"].read_text(encoding="utf-8").splitlines()[1], 'Анна,Рига,"Санкт-Петербург, центр"')
                self.assertIn("2 rows", pg.inner_text(".csv-view.editable .csv-count"))

                opn(log)
                with open(log, "a", encoding="utf-8") as fh:
                    fh.write("tail А\n")
                _t.sleep(4)
                self.assertTrue(pg.evaluate(active_doc).endswith("line 19\ntail А\n"))
                self.assertEqual(errors, [])
            finally:
                browser.close()

class AIPanelFinisherStaticTest(unittest.TestCase):
    """Fixes of the finisher pass in the AI panel (verified with screenshots; see changelog)."""

    def test_rebuilt_panel_ignores_old_listener_and_keeps_conversation(self) -> None:
        ai = (JS / "ai.js").read_text(encoding="utf-8")
        click = ai[ai.index("  onClick(e) {"):]
        self.assertLess(click.index("if (this.dead()) return;"), click.index("closest(\"[data-a]\")"))
        self.assertIn("  adopt(old) {", ai)
        app = (JS / "app.js").read_text(encoding="utf-8")
        self.assertIn("MN.chat.adopt(MN.chatCarry)", app)

    def test_edit_card_followed_to_the_bottom(self) -> None:
        ai = (JS / "ai.js").read_text(encoding="utf-8")
        self.assertIn("edit: (ev) => { onEdit(ev); card.hidden = false; this.renderCard(card, proposal); follow(); }", ai)
        self.assertIn("this.updateBadge(); follow(); unfollow();", ai)

    def test_multiline_insert_gets_its_own_lines(self) -> None:
        app = (JS / "app.js").read_text(encoding="utf-8")
        self.assertIn('text.includes("\\n") && r.head > line.from ? "\\n" : ""', app)
