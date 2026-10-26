"""Round 34 — MemuNote and modules: binary files are never wiped by Ctrl+S, mixed line endings keep the majority,
saves of one tab are queued, damaged UTF-8 opens as UTF-8 and is saved byte for byte, the run panel survives an
output flood, Markdown Ctrl+B on several lines, modules pick MemuAI's default model.

Headless tests run everywhere; the browser tests (real MemuNote server + Chromium) skip without Playwright/Chromium.
Run: NO_PROXY=127.0.0.1,localhost python3 -m pytest -q tests/round34_note_modules_test.py"""
from __future__ import annotations

import http.client
import json
import os
import re
import tempfile
import time
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
NOTE = APP / "memunote"
JS = NOTE / "static" / "js"
CHROME = next((p for p in (os.environ.get("MEMU_TEST_CHROMIUM", ""), "/usr/bin/chromium", "/workspace/work/chromium/chrome")
               if p and os.path.exists(p)), "")

class _Server(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="r34mn-")
        self.patch = mock.patch.dict(os.environ, {"MEMUAI_DATA_DIR": os.path.join(self.tmp.name, "data")})
        self.patch.start()
        from app.memunote import server
        self.app = server.NoteApp([])
        self.httpd, _ = server.start(self.app)
        self.dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        from app.memunote import runner
        runner.stop_all()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.patch.stop()
        self.tmp.cleanup()

    def call(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.app.port, timeout=40)
        hdrs = {"X-MN-Token": self.app.token, "Host": f"127.0.0.1:{self.app.port}"}
        data = body
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read() or b"{}")

    def file(self, name: str, data: bytes) -> Path:
        p = self.dir / name
        p.write_bytes(data)
        self.app.allow(str(p))
        return p

    def save(self, path: Path, text: str, **q):
        params = {"path": str(path), "encoding": "utf-8", "eol": "lf", "bom": "0", "mtime": "0", "force": "1", **q}
        return self.call("POST", "/api/save_text?" + urllib.parse.urlencode(params), text.encode("utf-8"))

class MajorityEolTest(_Server):
    def test_majority_eol_function(self) -> None:
        from app.memunote.formats import textio
        self.assertEqual(textio.majority_eol(b"a\r\nb\r\nc\nd\r\n"), "crlf")
        self.assertEqual(textio.majority_eol("a\nb\nc\r\nd\n"), "lf")
        self.assertEqual(textio.majority_eol(b"a\rb\rc\r\nd"), "cr")
        self.assertEqual(textio.majority_eol(b"no newline"), "")

    def test_open_reports_eol_major_for_mixed_files(self) -> None:
        cases = {"crlf.txt": (b"a\r\nb\r\nc\r\nd\ne\r\n", "crlf"), "lf.txt": (b"a\nb\nc\nd\r\ne\n", "lf"),
                 "u16.txt": ("a\r\nb\r\nc\nd\r\n".encode("utf-16"), "crlf")}
        for name, (data, want) in cases.items():
            st, info = self.call("POST", "/api/open", {"path": str(self.file(name, data))})
            self.assertEqual(st, 200, info)
            self.assertEqual(info["eol"], "mixed", name)
            self.assertEqual(info["eol_major"], want, name)
        st, info = self.call("POST", "/api/open", {"path": str(self.file("plain.txt", b"a\r\nb\r\n"))})
        self.assertEqual(info["eol"], "crlf")
        self.assertNotIn("eol_major", info)

    def test_the_page_saves_with_the_majority(self) -> None:
        src = (JS / "app.js").read_text(encoding="utf-8")
        self.assertIn('info.eol === "mixed" ? info.eol_major || MN.settings.default_eol', src)
        self.assertIn("tab.mixedEol && !tab.dirty", src)

class BinaryAndQueueTest(_Server):
    def test_binary_is_not_a_text_kind(self) -> None:
        src = (JS / "app.js").read_text(encoding="utf-8")
        kinds = re.search(r"const TEXT_KINDS = new Set\(\[([^\]]*)\]\)", src).group(1)
        self.assertNotIn('"binary"', kinds)
        self.assertIn('"text"', kinds)
        self.assertIn("if (!snapshot && tab.initialText == null) return true;", src)

    def test_save_queue(self) -> None:
        src = (JS / "app.js").read_text(encoding="utf-8")
        body = src[src.index("async function saveTab("):src.index("async function saveTabNow(")]
        self.assertIn("while (tab.saving) await tab.saving", body)
        self.assertIn("if (!tab || opts.force) return saveTabNow(tab, opts)", body)
        self.assertIn("tab.saving = job", body)

    def test_binary_file_opens_as_binary_and_stays_intact(self) -> None:
        data = bytes(range(256)) * 40
        p = self.file("blob.dat", data)
        st, info = self.call("POST", "/api/open", {"path": str(p)})
        self.assertEqual(st, 200, info)
        self.assertEqual(info["kind"], "binary")
        self.assertNotIn("text", info)
        self.assertEqual(p.read_bytes(), data)

class DamagedUtf8Test(_Server):
    GOOD = "Привет, мир! Это обычный текст — «кавычки» и ёжики.\n".encode("utf-8") * 6

    def test_detected_as_utf8_not_cp1252(self) -> None:
        from app.memunote.formats import textio
        damaged = self.GOOD[:20] + b"\xff" + self.GOOD[20:]
        info = textio.detect_bytes(damaged)
        self.assertEqual((info.encoding, info.damaged), ("utf-8", 1))

        self.assertEqual(textio.detect_bytes(("Привет мир это текст на русском\n" * 8).encode("cp1251")).encoding, "cp1251")
        self.assertEqual(textio.detect_bytes(("Привет мир это текст на русском\n" * 8).encode("koi8-r")).encoding, "koi8-r")
        self.assertEqual(textio.detect_bytes("Café crème brûlée, naïve façade".encode("cp1252")).encoding, "cp1252")
        self.assertEqual(textio.detect_bytes(self.GOOD).damaged, 0)

    def test_open_and_save_keep_the_broken_bytes(self) -> None:
        damaged = b"real \xef\xbf\xbd mark, " + self.GOOD[:21] + b"\xc3(" + self.GOOD[21:] + b"\xfe\xfe end\n"
        p = self.file("d.txt", damaged)
        st, info = self.call("POST", "/api/open", {"path": str(p)})
        self.assertEqual((info["encoding"], info["web_encoding"]), ("utf-8", "utf-8"))
        self.assertEqual(info["damaged"], 3)
        shown = damaged.decode("utf-8", errors="replace")
        st, r = self.save(p, shown)
        self.assertTrue(r["ok"], r)
        self.assertEqual(p.read_bytes(), damaged)
        st, r = self.save(p, "Ж" + shown.replace("end", "конец"))
        self.assertEqual(p.read_bytes(), "Ж".encode() + damaged.replace(b"end", "конец".encode()))
        st, r = self.save(p, shown.replace("\ufffd", "", 1))
        self.assertNotIn(b"\xc3(", p.read_bytes())

    def test_page_shows_a_notice(self) -> None:
        self.assertIn('if (info.damaged) toast(t("utf8_damaged"', (JS / "app.js").read_text(encoding="utf-8"))
        i18n = (JS / "i18n.js").read_text(encoding="utf-8")
        self.assertEqual(i18n.count("utf8_damaged:"), 2)

class StaticTest(unittest.TestCase):
    def test_runpanel_flood_guards(self) -> None:
        src = (JS / "runpanel.js").read_text(encoding="utf-8")
        self.assertIn("this.writeMany(r.chunks)", src)
        self.assertIn("const LINE_CAP = 100000", src)
        self.assertIn("if ((this.curLen || 0) > 2000) return;", src)
        i18n = (JS / "i18n-r33.js").read_text(encoding="utf-8")
        self.assertEqual(i18n.count("run_line_cut:"), 2)

    def test_h2_rule_follows_the_text_column(self) -> None:
        css = (NOTE / "static" / "r33.css").read_text(encoding="utf-8")
        rule = re.search(r"\.mn-lm-h2:not\(\.cm-activeLine\) \{([^}]*)\}", css).group(1)
        self.assertIn("content-box", rule)
        self.assertNotIn("box-shadow", rule)

    def test_modules_prefer_the_default_model(self) -> None:
        for name in ("translator", "design"):
            src = (APP / "module_builtins" / name / "main.js").read_text(encoding="utf-8")
            self.assertIn("usable.find((item) => item.default)", src, name)
        self.assertIn("chat.find((item) => item.default)", (APP / "module_builtins" / "studio" / "main.js").read_text(encoding="utf-8"))
        self.assertIn("modelNames[el.model.value]", (APP / "module_builtins" / "translator" / "main.js").read_text(encoding="utf-8"))

    def test_characters_search_failure_is_shown(self) -> None:
        src = (APP / "module_builtins" / "characters" / "ai-create.js").read_text(encoding="utf-8")
        self.assertIn("if (failed.length && failed.length === queries.length) throw failed[0];", src)

def _browser_ok() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
        return bool(CHROME)
    except Exception:  # noqa: BLE001
        return False

@unittest.skipUnless(_browser_ok(), "Playwright + Chromium not available")
class BrowserTest(_Server):
    def page(self, p, files, settings=None):
        self.call("POST", "/api/settings", {"language": "ru", "restore_session": False, "proxy_url": "http://127.0.0.1:9", **(settings or {})})
        try:
            b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        except Exception as exc:  # noqa: BLE001 - missing system libraries
            self.skipTest(f"Chromium does not start: {exc}")
        pg = b.new_page(viewport={"width": 1280, "height": 820})
        self.errors = []
        pg.on("pageerror", lambda e: self.errors.append(str(e)))
        for f in files:
            self.app.allow(str(f))
        self.app.initial_files = [str(f) for f in files]
        pg.goto(f"http://127.0.0.1:{self.app.port}/index.html#t={self.app.token}")
        pg.wait_for_selector("body.ready", timeout=20000)
        time.sleep(1.2)
        return b, pg

    def test_markdown_ctrl_b_on_several_lines(self) -> None:
        from playwright.sync_api import sync_playwright
        md = self.dir / "b.md"
        md.write_text("hello world\nsecond line\n", encoding="utf-8")
        with sync_playwright() as p:
            b, pg = self.page(p, [md], {"markdown_preview": "off"})
            try:
                pg.click(".cm-content")
                pg.keyboard.press("Control+a")
                pg.keyboard.press("Control+b")
                time.sleep(0.2)
                doc = "MN.panes[0].view.state.doc.toString()"
                self.assertEqual(pg.evaluate(doc), "**hello world**\n**second line**\n")
                pg.keyboard.press("Control+b")
                time.sleep(0.2)
                self.assertEqual(pg.evaluate(doc), "hello world\nsecond line\n")
                pg.keyboard.press("Control+End")
                pg.keyboard.type("x")
                pg.keyboard.press("Shift+Home")
                pg.keyboard.press("Control+b")
                time.sleep(0.2)
                self.assertTrue(pg.evaluate(doc).endswith("**x**"))
                self.assertEqual(self.errors, [])
            finally:
                b.close()

    def test_run_panel_survives_an_output_flood(self) -> None:
        from playwright.sync_api import sync_playwright
        f = self.dir / "flood.py"
        f.write_text("import sys\nfor i in range(60000):\n    print('line', i)\nsys.stdout.write('x' * 300000)\nprint()\nprint('END')\n", encoding="utf-8")
        with sync_playwright() as p:
            b, pg = self.page(p, [f])
            try:
                pg.keyboard.press("F5")
                pg.wait_for_selector(".modal .btn.primary")
                pg.click(".modal .btn.primary")
                pg.wait_for_selector(".mn-run-state.ok", timeout=60000)
                t0 = time.time()
                self.assertTrue(pg.evaluate("1 + 1 === 2"))
                self.assertLess(time.time() - t0, 5)
                pg.wait_for_function("document.querySelector('.mn-run-out').textContent.includes('END')", timeout=20000)
                lines = pg.evaluate("document.querySelectorAll('.mn-run-out .ln').length")
                self.assertLessEqual(lines, 20000)
                self.assertTrue(pg.evaluate("document.querySelector('.mn-run-out').textContent.includes('line 59999')"))
                cut = pg.evaluate("[...document.querySelectorAll('.mn-run-out .ln')].map(l => l.textContent.length).reduce((a, b) => Math.max(a, b), 0)")
                self.assertLess(cut, 100200)
                self.assertIn("слишком длинная", pg.inner_text(".mn-run-out"))
                self.assertEqual(self.errors, [])
            finally:
                b.close()

if __name__ == "__main__":
    unittest.main()
