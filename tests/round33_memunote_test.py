"""Round 33 — MemuNote (#9: run files, code autocomplete, spell checking, live Markdown) and shortcuts in any keyboard
layout everywhere (#8: app/pgstatic/memu-keys.js and its copies).

Headless tests run everywhere; the browser tests (real MemuNote server + Chromium) skip without Playwright/Chromium.
Run: NO_PROXY=127.0.0.1,localhost python3 -m pytest -q tests/round33_memunote_test.py"""
from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
NOTE = APP / "memunote"
JS = NOTE / "static" / "js"
CHROME = "/usr/bin/chromium" if os.path.exists("/usr/bin/chromium") else "/workspace/work/chromium/chrome"

def _node():
    return shutil.which("node")

class KeysHelperTest(unittest.TestCase):
    COPIES = [APP / "static" / "memu-keys.js", NOTE / "static" / "js" / "memu-keys.js"]

    def test_copies_are_identical(self) -> None:
        canon = (APP / "pgstatic" / "memu-keys.js").read_bytes()
        for p in self.COPIES:
            self.assertEqual(p.read_bytes(), canon, f"{p} differs from pgstatic/memu-keys.js — copy it again")

    def test_proxy_copy_is_proxy_only_safe(self) -> None:

        import re
        text = (APP / "static" / "memu-keys.js").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"\bMemuAI\b(?!\s+Proxy)", text))

    def test_every_page_loads_it_first(self) -> None:
        pages = {APP / "pgstatic" / "index.html": "/memu-keys.js", APP / "pgstatic" / "quick" / "index.html": "/memu-keys.js",
                 APP / "pgstatic" / "quick" / "layer.html": "/memu-keys.js", APP / "pgstatic" / "quick" / "sticker.html": "/memu-keys.js",
                 APP / "pgstatic" / "quick" / "region.html": "/memu-keys.js", APP / "pgstatic" / "companion" / "stage.html": "/memu-keys.js",
                 APP / "static" / "index.html": "/memu-keys.js", APP / "tgapp" / "index.html": "shared/memu-keys.js",
                 NOTE / "static" / "index.html": "js/memu-keys.js"}
        for page, src in pages.items():
            html = page.read_text(encoding="utf-8")
            self.assertIn(src, html, page)
            first_script = re.search(r'<script[^>]*src="([^"]+)"', html)
            if page.name == "index.html" and page.parent.name == "pgstatic":
                self.assertIn("memu-keys", first_script.group(1), "must run before every other script")
        tg = (APP / "tgapp_api.py").read_text(encoding="utf-8")
        self.assertIn('"memu-keys.js": "text/javascript"', tg)

    def test_modules_get_it_inline(self) -> None:
        from app import modules_host
        js = modules_host._memu_keys_js()
        self.assertIn("MemuKeys", js)
        self.assertNotIn("</script", js.lower())
        src = (APP / "modules_host.py").read_text(encoding="utf-8")
        self.assertIn("_memu_keys_js()", src)

    @unittest.skipUnless(_node(), "node not installed")
    def test_logic_in_node(self) -> None:
        script = r"""
const K = require(process.argv[1]);
const ev = (o) => Object.assign({ getModifierState: (m) => !!(o.altgr && m === "AltGraph") }, o);
const out = {};
let e = ev({ key: "ы", code: "KeyS", ctrlKey: true }); out.ru = [K.normalize(e), e.key, e.originalKey];
e = ev({ key: "Ы", code: "KeyS", ctrlKey: true, shiftKey: true }); K.normalize(e); out.ruShift = e.key;
e = ev({ key: "o", code: "KeyS", ctrlKey: true }); out.dvorak = [K.normalize(e), e.key];        // Latin layouts keep their key
e = ev({ key: "ś", code: "KeyS", ctrlKey: true, altKey: true, altgr: true }); out.altgr = [K.normalize(e), e.key];
e = ev({ key: "ы", code: "KeyS" }); out.typing = [K.normalize(e), e.key];                       // no modifier: typing untouched
e = ev({ key: "б", code: "Comma", ctrlKey: true }); K.normalize(e); out.comma = e.key;
e = ev({ key: "№", code: "Digit3", ctrlKey: true, shiftKey: true }); K.normalize(e); out.num = e.key;
out.latin = [K.latin({ key: "и", code: "KeyB" }), K.latin({ key: "Enter", code: "Enter" }), K.latin({ key: "х", code: "BracketLeft" })];
out.combo = K.combo(ev({ key: "я", code: "KeyZ", ctrlKey: true, shiftKey: true }));
out.is = K.is({ key: "ф", code: "KeyA" }, "a");
console.log(JSON.stringify(out));
"""
        r = subprocess.run([_node(), "-e", script, str(APP / "pgstatic" / "memu-keys.js")], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["ru"], [True, "s", "ы"])
        self.assertEqual(out["ruShift"], "S")
        self.assertEqual(out["dvorak"], [False, "o"])
        self.assertEqual(out["altgr"], [False, "ś"])
        self.assertEqual(out["typing"], [False, "ы"])
        self.assertEqual(out["comma"], ",")
        self.assertEqual(out["num"], "#")
        self.assertEqual(out["latin"], ["b", "Enter", "["])
        self.assertEqual(out["combo"], "Ctrl+Shift+Z")
        self.assertTrue(out["is"])

    def test_single_key_tool_shortcuts_use_physical_keys(self) -> None:
        for rel in ("photo/editor.js", "photo/commands.js", "slides/present.js"):
            self.assertIn("MemuKeys.latin(e)", (JS / rel).read_text(encoding="utf-8"), rel)
        for rel in ("pgstatic/docs.js", "pgstatic/settings-kit.js", "pgstatic/skills-ui.js", "static/settings.js", "tgapp/device.js", "pgstatic/memucode.js"):
            self.assertIn("MemuKeys.latin(event)", (APP / rel).read_text(encoding="utf-8"), rel)

    def test_global_hotkey_accepts_russian_letters(self) -> None:
        from app.quick_ask_core import parse_hotkey
        self.assertEqual(parse_hotkey("Alt+Ы"), parse_hotkey("Alt+S"))
        self.assertEqual(parse_hotkey("Ctrl+Alt+й")[2], "Ctrl+Alt+Q")
        self.assertEqual(parse_hotkey("Win+Shift+ж")[2], parse_hotkey("Win+Shift+;")[2])

    def test_memucode_tui_alt_letters_in_russian_layout(self) -> None:
        src = (APP / "cli" / "tui" / "app.py").read_text(encoding="utf-8")
        for cyr in "мзке":
            self.assertIn(f'@kb.add("escape", "{cyr}", filter=no_dialog)', src)

class _Server(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="r33mn-")
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
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read() or b"{}")

class RunnerTest(_Server):
    def script(self, name, text):
        p = self.dir / name
        p.write_text(text, encoding="utf-8")
        self.app.allow(str(p))
        return str(p)

    def poll_all(self, rid, limit=20):
        out, since, t0 = [], 0, time.time()
        while time.time() - t0 < limit:
            st, r = self.call("GET", f"/api/run/poll?id={rid}&since={since}")
            self.assertEqual(st, 200)
            out += r["chunks"]
            since = r["seq"]
            if r["done"]:
                return out, r["code"]
        self.fail("run did not finish")

    def test_only_open_files_and_confirmation(self) -> None:
        p = self.dir / "x.py"
        p.write_text("print(1)", encoding="utf-8")
        st, r = self.call("POST", "/api/run/start", {"path": str(p)})
        self.assertEqual(st, 403, r)
        self.app.allow(str(p))
        st, r = self.call("POST", "/api/run/start", {"path": str(p)})
        self.assertEqual((st, r["error"]), (409, "confirm_required"))
        st, info = self.call("GET", "/api/run/info?path=" + str(p))
        self.assertTrue(info["runnable"])
        self.assertFalse(info["trusted"])
        st, r = self.call("POST", "/api/run/start", {"path": str(p), "confirmed": True, "remember": True})
        self.assertEqual(st, 200, r)
        self.poll_all(r["id"])
        st, info = self.call("GET", "/api/run/info?path=" + str(p))
        self.assertTrue(info["trusted"])
        st, r = self.call("POST", "/api/run/start", {"path": str(p)})
        self.assertEqual(st, 200, r)

    def test_python_stdout_stderr_stdin_exit_code_and_cwd(self) -> None:
        p = self.script("io.py", "import os, sys\nprint('Привет', os.path.basename(os.getcwd()))\nname = input()\n"
                                  "print('name=' + name)\nprint('oops', file=sys.stderr)\nsys.exit(7)\n")
        st, r = self.call("POST", "/api/run/start", {"path": p, "confirmed": True})
        self.assertEqual(st, 200, r)
        self.assertIn("-u", r["cmd"])
        time.sleep(0.5)
        self.assertEqual(self.call("POST", "/api/run/input", {"id": r["id"], "text": "Мир\n"})[0], 200)
        chunks, code = self.poll_all(r["id"])
        out = "".join(c["t"] for c in chunks if c["s"] == "out")
        err = "".join(c["t"] for c in chunks if c["s"] == "err")
        self.assertIn("Привет " + self.dir.name, out)
        self.assertIn("name=Мир", out)
        self.assertIn("oops", err)
        self.assertEqual(code, 7)

    def test_stop_kills_a_running_program(self) -> None:
        p = self.script("loop.py", "import time\nwhile True:\n    print('t', flush=True)\n    time.sleep(0.1)\n")
        st, r = self.call("POST", "/api/run/start", {"path": p, "confirmed": True})
        time.sleep(0.8)
        self.call("POST", "/api/run/stop", {"id": r["id"]})
        chunks, code = self.poll_all(r["id"], limit=10)
        self.assertTrue(any("t" in c["t"] for c in chunks))
        self.assertNotEqual(code, 0)

    def test_args_are_never_given_to_a_shell(self) -> None:
        marker = self.dir / "pwned"
        p = self.script("args.py", "import sys\nprint(sys.argv[1:])\n")
        st, r = self.call("POST", "/api/run/start", {"path": p, "confirmed": True, "args": f'"a b" ; touch {marker} && echo x'})
        chunks, code = self.poll_all(r["id"])
        out = "".join(c["t"] for c in chunks)
        self.assertIn("'a b'", out)
        self.assertIn("'&&'", out)
        self.assertFalse(marker.exists())

    def test_resolution_and_batch_safety(self) -> None:
        from app.memunote import runner
        self.assertFalse(runner.resolve("x.txt")["runnable"])
        info = {"exe": "/usr/bin/cmd", "extra": [], "style": "batch"}
        with self.assertRaises(ValueError):
            runner.build_argv("C:\\a&b.bat", info, [])
        with self.assertRaises(ValueError):
            runner.build_argv("C:\\ok.bat", info, ["x|y"])
        self.assertEqual(runner.build_argv("C:\\ok.bat", info, ["1"]), ["/usr/bin/cmd", "/d", "/c", "call", "C:\\ok.bat", "1"])
        ps = runner.build_argv("C:\\s.ps1", {"exe": "pwsh", "style": "powershell"}, [])
        self.assertIn("-ExecutionPolicy", ps)
        self.assertEqual(runner.build_argv("/t/a.py", {"exe": "py.exe", "style": "python"}, []), ["py.exe", "-3", "-u", "/t/a.py"])
        self.assertEqual(runner.split_args('a "b c" d'), ["a", "b c", "d"])

    def test_oem_fallback_decoder(self) -> None:
        from app.memunote import runner
        d = runner._Decoder()
        with mock.patch.object(runner, "_oem_codec", return_value="cp866"):
            text = d.feed("ok ".encode()) + d.feed("Привет".encode("cp866"), True)
        self.assertEqual(text, "ok Привет")
        d = runner._Decoder()
        b = "Ёж".encode("utf-8")
        self.assertEqual(d.feed(b[:1]) + d.feed(b[1:], True), "Ёж")

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_node_exit_code(self) -> None:
        p = self.script("a.js", "console.log('hi'); process.exit(3)")
        st, r = self.call("POST", "/api/run/start", {"path": p, "confirmed": True})
        chunks, code = self.poll_all(r["id"])
        self.assertEqual(code, 3)
        self.assertIn("hi", "".join(c["t"] for c in chunks))

class SpellTest(_Server):
    def test_check_suggest_and_user_dictionary(self) -> None:
        from app.memunote import spell
        spell.ENGINE.ensure(wait=60)
        if spell.ENGINE.state != "ready":
            self.skipTest("spylls not installed: " + spell.ENGINE.error)
        st, r = self.call("POST", "/api/spell/check", {"words": ["привет", "ошибкка", "hello", "speling", "кто-то", "ещё", "Москва", "MemuNote"]})
        self.assertTrue(r["ready"])
        self.assertEqual(set(r["bad"]), {"ошибкка", "speling", "MemuNote"})
        st, r = self.call("POST", "/api/spell/suggest", {"word": "превет"})
        self.assertEqual(r["suggestions"][0], "привет")
        self.call("POST", "/api/spell/add", {"word": "MemuNote"})
        st, r = self.call("POST", "/api/spell/check", {"words": ["MemuNote"]})
        self.assertEqual(r["bad"], [])
        self.assertIn("MemuNote", (Path(os.environ["MEMUAI_DATA_DIR"]) / "memunote" / "dictionary.txt").read_text(encoding="utf-8"))
        self.call("POST", "/api/spell/remove", {"word": "MemuNote"})
        self.assertEqual(self.call("POST", "/api/spell/check", {"words": ["MemuNote"]})[1]["bad"], ["MemuNote"])
        self.assertEqual(self.call("POST", "/api/spell/add", {"word": "<script>"})[0], 400)

    def test_rerank_prefers_vowel_swaps(self) -> None:
        from app.memunote.spell import rerank
        self.assertEqual(rerank("превет", ["ревет", "преет", "привет"])[0], "привет")
        self.assertEqual(rerank("вообщем", ["во общем", "вообще"])[0], "вообще")
        self.assertEqual(rerank("карова", ["Сарова", "крова", "корова"])[0], "корова")

class SettingsAndWiringTest(unittest.TestCase):
    def test_settings(self) -> None:
        from app.memunote import settings
        n = settings.normalize({})
        self.assertEqual((n["markdown_preview"], n["spell_check"], n["code_autocomplete"]), ("live", True, True))
        self.assertEqual(settings.normalize({"markdown_preview": "split"})["markdown_preview"], "live")
        self.assertEqual(settings.normalize({"markdown_preview": "split", "md_live_migrated": True})["markdown_preview"], "split")
        self.assertEqual(settings.normalize({"markdown_preview": "bogus"})["markdown_preview"], "live")

    def test_spec_build_and_requirements(self) -> None:
        spec = (ROOT / "MemuNote.spec").read_text(encoding="utf-8")
        build = (ROOT / "build.py").read_text(encoding="utf-8")
        for mod in ("app.memunote.runner", "app.memunote.spell", "app.spellcheck"):
            self.assertIn(f"'{mod}'", spec)
            self.assertIn(f'"{mod}"', build)
        self.assertIn("collect_all('spylls')", spec)
        self.assertIn('"collect": ["spylls"]', build)
        self.assertIn("app/memunote/dicts", spec)
        self.assertIn("app/memunote/dicts;app/memunote/dicts", build)
        self.assertIn("spylls", (ROOT / "requirements.txt").read_text())
        for f in ("ru_RU.aff", "ru_RU.dic", "en_US.aff", "en_US.dic", "LICENSE-ru_RU.txt", "LICENSE-en_US.txt"):
            self.assertTrue((NOTE / "dicts" / f).is_file(), f)

    def test_frontend_wiring_and_i18n(self) -> None:
        html = (NOTE / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn("r33.css", html)
        app = (JS / "app.js").read_text(encoding="utf-8")
        for needle in ("./livemd.js", "./spell.js", "./codecomplete.js", "./runpanel.js", "./toolbar-fit.js", "r33Extensions(tab)", "new RunPanel(runHooks())"):
            self.assertIn(needle, app)
        self.assertIn("i18n-r33.js", (JS / "i18n.js").read_text(encoding="utf-8"))
        text = (JS / "i18n-r33.js").read_text(encoding="utf-8")
        en = text[text.index("  en: {"):]
        self.assertIsNone(re.search(r"[А-Яа-яЁё]", en), "English UI strings must not contain Cyrillic")
        ru_keys = set(re.findall(r"(?:^\s*|[{,]\s*)(\w+):\s*\"", text[:text.index("  en: {")], re.M))
        en_keys = set(re.findall(r"(?:^\s*|[{,]\s*)(\w+):\s*\"", en, re.M))
        self.assertEqual(ru_keys - {"ru"}, en_keys - {"en"})

    @unittest.skipUnless(_node(), "node not installed")
    def test_js_syntax(self) -> None:
        for f in ("livemd.js", "spell.js", "codecomplete.js", "runpanel.js", "i18n-r33.js", "app.js", "editor.js", "core.js", "toolbar-fit.js", "ai.js"):
            r = subprocess.run([_node(), "--check", str(JS / f)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f + r.stderr)

class GhostSuggestionTest(unittest.TestCase):
    """The ghost request asks the model to repeat the last words before the caret ("echo") so the page knows whether
    a space follows ("in" → "in the") or the word goes on ("rele" → "release"); reasoning is switched off ("effort")."""

    @unittest.skipUnless(_node(), "node not installed")
    def test_echo_and_next_line_logic_in_node(self) -> None:
        src = (JS / "ai.js").read_text(encoding="utf-8")
        start = src.index("export function ghostTail")
        helpers = src[start:src.index("export function requestGhost")].replace("export function", "function")
        script = helpers + r"""
const out = {
  tails: ["We fixed several bugs in", "    if n <", "on the rele", "хлеб, ", "", "x".repeat(70), "a\nb c"].map(ghostTail),
  strip: [["several bugs in the login flow.", "several bugs in"], ["on the release", "on the rele"], ["if n < 2:\n  return n", "if n <"],
          ["the login flow", "several bugs in"], ["  Список: хлеб, молоко", "Список: хлеб, "], ["x", ""]].map(([t, tail]) => stripEcho(t, tail)),
  lines: [["\n        return n\nmore", true], ["\n\n   \n    x = 1", true], [" 2:\n        return n", true], [" the end.\nNext", false], ["\nfoo", false]].map(([t, c]) => ghostLine(t, c)),
};
process.stdout.write(JSON.stringify(out));
"""
        r = subprocess.run([_node(), "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["tails"], ["several bugs in", "if n <", "on the rele", "хлеб, ", "", "", "b c"])
        self.assertEqual(out["strip"], [" the login flow.", "ase", " 2:\n  return n", None, "молоко", None])
        self.assertEqual(out["lines"], ["\n        return n", "\n    x = 1", " 2:", " the end.", ""])

    def test_prompt_asks_for_the_echo_only_when_it_matches(self) -> None:
        from app.memunote import prompts
        body = {"action": "autocomplete", "before": "We fixed several bugs in", "after": "", "echo": "several bugs in",
                "context": {"name": "a.txt", "kind": "text"}}
        msgs = prompts.build_messages(body, {}, "en", False)["messages"]
        system = " ".join(m["content"] for m in msgs if m["role"] == "system")
        self.assertIn('"several bugs in"', system)
        self.assertIn("repeating exactly", system)
        for echo in ("other words", "x\ny", "z" * 61):
            msgs = prompts.build_messages({**body, "echo": echo}, {}, "en", False)["messages"]
            self.assertNotIn("repeating exactly", " ".join(m["content"] for m in msgs if m["role"] == "system"))

    def test_effort_reaches_the_proxy(self) -> None:
        from app.memunote import ai
        sent = []

        class Conn:
            sock = None
            def request(self, method, path, body=None, headers=None):
                sent.append(json.loads(body))
            def getresponse(self):
                raise OSError("stop here")

        for effort, expected in (("off", "off"), ("HIGH", "high"), ("bogus", None), ("", None)):
            sent.clear()
            with mock.patch.object(ai, "_conn", lambda base, timeout: Conn()):
                with self.assertRaises(ai.AIError):
                    list(ai.stream_chat("http://127.0.0.1:9", {"model": "m", "messages": [], "effort": effort}, __import__("threading").Event()))
            self.assertEqual(sent[0].get("effort"), expected, effort)

    def test_server_passes_effort_from_the_page(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="r33mn-")
        self.addCleanup(tmp.cleanup)
        with mock.patch.dict(os.environ, {"MEMUAI_DATA_DIR": os.path.join(tmp.name, "data")}):
            from app.memunote import ai, server
            seen = []

            def fake_stream(base, payload, cancel, on_conn=None):
                seen.append(payload)
                yield {"t": "delta", "text": "ok"}

            app = server.NoteApp([])
            httpd, _ = server.start(app)
            try:
                with mock.patch.object(ai, "stream_chat", fake_stream), mock.patch.object(ai, "discover", lambda c="": "http://127.0.0.1:9"):
                    conn = http.client.HTTPConnection("127.0.0.1", app.port, timeout=30)
                    body = json.dumps({"action": "autocomplete", "before": "abc", "after": "", "model": "m", "effort": "off",
                                       "memory": False, "context": {"name": "a.txt", "kind": "text"}}).encode()
                    conn.request("POST", "/api/ai/chat", body=body, headers={"X-MN-Token": app.token, "Host": f"127.0.0.1:{app.port}",
                                                                             "Content-Type": "application/json"})
                    raw = conn.getresponse().read().decode("utf-8")
            finally:
                httpd.shutdown()
                httpd.server_close()
        self.assertIn('"t": "done"', raw)
        self.assertEqual(seen[0]["effort"], "off")
        self.assertIn('effort: "off"', (JS / "ai.js").read_text(encoding="utf-8"))

def _playwright():
    try:
        import playwright.sync_api  # noqa: F401
        return os.path.exists(CHROME)
    except Exception:  # noqa: BLE001
        return False

@unittest.skipUnless(_playwright(), "Playwright + Chromium not available")
class BrowserTest(_Server):
    def page(self, p, files, settings=None):
        self.call("POST", "/api/settings", {"language": "ru", "restore_session": False, "proxy_url": "http://127.0.0.1:9", **(settings or {})})
        b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
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

    def test_live_markdown_reveals_markup_only_at_the_caret(self) -> None:
        from playwright.sync_api import sync_playwright
        md = self.dir / "n.md"
        text = "# Title\n\nSome **bold** and [link](https://e.x) text.\n\n- [ ] task\n- item\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"
        md.write_text(text, encoding="utf-8")
        with sync_playwright() as p:
            b, pg = self.page(p, [md])
            try:
                self.assertTrue(pg.evaluate("document.querySelector('.cm-editor').classList.contains('mn-live')"))
                line = "() => [...document.querySelectorAll('.cm-line')].find(l => l.textContent.includes('bold')).textContent"
                self.assertEqual(pg.evaluate(line), "Some bold and link text.")
                self.assertEqual(pg.evaluate("document.querySelectorAll('.mn-lm-table table').length"), 1)
                pg.click("text=bold")
                time.sleep(0.3)
                self.assertEqual(pg.evaluate(line), "Some **bold** and link text.")
                pg.click(".mn-lm-check")
                time.sleep(0.2)
                self.assertIn("- [x] task", pg.evaluate("MN.panes[0].view.state.doc.toString()"))
                pg.keyboard.press("Control+z")
                pg.keyboard.press("Control+s")
                time.sleep(0.7)
                self.assertEqual(md.read_text(encoding="utf-8"), text)
                pg.click(".seg-btn[data-a='mdmode:off']")
                time.sleep(0.3)
                self.assertFalse(pg.evaluate("document.querySelector('.cm-editor').classList.contains('mn-live')"))
                self.assertEqual(self.errors, [])
            finally:
                b.close()

    def test_spell_underlines_and_suggests(self) -> None:
        from app.memunote import spell
        spell.ENGINE.ensure(wait=60)
        if spell.ENGINE.state != "ready":
            self.skipTest("spylls not installed")
        from playwright.sync_api import sync_playwright
        f = self.dir / "t.txt"
        f.write_text("Это превет и ошибкка, а https://ошибкка.рф и camelCase не трогаем. Also speling.\n", encoding="utf-8")
        with sync_playwright() as p:
            b, pg = self.page(p, [f])
            try:
                for _ in range(40):
                    if pg.evaluate("document.querySelectorAll('.mn-spell-err').length") >= 3:
                        break
                    time.sleep(0.25)
                self.assertEqual(sorted(e.text_content() for e in pg.query_selector_all(".mn-spell-err")), sorted(["ошибкка", "превет", "speling"]))
                bb = pg.query_selector(".mn-spell-err").bounding_box()
                pg.mouse.click(bb["x"] + 4, bb["y"] + 4, button="right")
                pg.wait_for_selector(".menu .spell-sugg", timeout=20000)
                pg.click(".menu .spell-sugg >> nth=0")
                time.sleep(0.2)
                self.assertIn("Это привет", pg.evaluate("MN.panes[0].view.state.doc.toString()"))
            finally:
                b.close()

    def test_run_panel_with_input_and_completion(self) -> None:
        from playwright.sync_api import sync_playwright
        f = self.dir / "r.py"
        f.write_text("name = input('?')\nprint('hi ' + name)\nraise SystemExit(2)\n", encoding="utf-8")
        with sync_playwright() as p:
            b, pg = self.page(p, [f])
            try:
                pg.keyboard.press("F5")
                pg.wait_for_selector(".modal .btn.primary")
                pg.click(".modal .btn.primary")
                pg.wait_for_selector(".mn-run-in:not([hidden]) input")
                time.sleep(0.4)
                pg.fill(".mn-run-in input", "Бо")
                pg.keyboard.press("Enter")
                pg.wait_for_selector(".mn-run-state.fail", timeout=15000)
                self.assertIn("hi Бо", pg.inner_text(".mn-run-out"))
                self.assertIn("2", pg.inner_text(".mn-run-state"))
                pg.click(".cm-content")
                pg.keyboard.press("Control+End")
                pg.keyboard.press("Enter")
                for ch in "ifm":
                    pg.keyboard.type(ch)
                    time.sleep(0.25)
                pg.wait_for_selector(".cm-tooltip-autocomplete")
                pg.keyboard.press("Tab")
                time.sleep(0.2)
                self.assertIn('if __name__ == "__main__":', pg.evaluate("MN.panes[0].view.state.doc.toString()"))
                self.assertEqual(self.errors, [])
            finally:
                b.close()

    def test_ctrl_s_in_the_russian_layout(self) -> None:
        from playwright.sync_api import sync_playwright
        f = self.dir / "k.txt"
        f.write_text("x", encoding="utf-8")
        with sync_playwright() as p:
            b, pg = self.page(p, [f])
            try:
                pg.click(".cm-content")
                pg.keyboard.press("End")
                pg.keyboard.type("Ж")
                pg.evaluate("""() => document.activeElement.dispatchEvent(new KeyboardEvent('keydown', {key: 'ы', code: 'KeyS', ctrlKey: true, bubbles: true, cancelable: true}))""")
                time.sleep(0.8)
                self.assertEqual(f.read_text(encoding="utf-8"), "xЖ")
            finally:
                b.close()

    def test_narrow_window_moves_format_buttons_into_more(self) -> None:
        from playwright.sync_api import sync_playwright
        md = self.dir / "w.md"
        md.write_text("# T\n\nword", encoding="utf-8")
        with sync_playwright() as p:
            b, pg = self.page(p, [md])
            try:
                self.assertFalse(pg.evaluate("!!document.querySelector('#toolbar .tb-overflow')"), "wide window: no More menu")
                pg.set_viewport_size({"width": 760, "height": 600})
                time.sleep(0.5)
                state = pg.evaluate("""() => { const b = document.getElementById('toolbar'); const r = (s) => b.querySelector(s).getBoundingClientRect();
                    return { fits: b.scrollWidth <= b.clientWidth + 1, hidden: b.querySelectorAll('[data-fit-hidden]').length,
                             more: !!b.querySelector('.tb-overflow'), split: r('[data-a="split"]').right <= innerWidth, ai: r('.ai-btn').right <= innerWidth }; }""")
                self.assertEqual(state, {**state, "fits": True, "more": True, "split": True, "ai": True})
                self.assertGreater(state["hidden"], 0)
                pg.click(".cm-content")
                pg.keyboard.press("Control+End")
                pg.keyboard.press("Shift+Home")
                pg.click("#toolbar .tb-overflow")
                pg.click(".menu .mi:has-text('Зачёркнутый')")
                time.sleep(0.3)
                self.assertIn("~~word~~", pg.evaluate("MN.panes[0].view.state.doc.toString()"))
                pg.set_viewport_size({"width": 1400, "height": 600})
                time.sleep(0.5)
                self.assertEqual(pg.evaluate("document.querySelectorAll('#toolbar [data-fit-hidden], #toolbar .tb-overflow').length"), 0)
                self.assertEqual(self.errors, [])
            finally:
                b.close()

if __name__ == "__main__":
    unittest.main()
