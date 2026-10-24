"""Round 27 (r27-selection): actions on the text selected in any program — app/quick_selection.py + the window.

Capture policy with a fake backend (no Windows here): UI Automation first; the Ctrl+C fallback only with a complete
clipboard snapshot, never for MemuAI's own windows / terminals / the desktop, an unchanged clipboard sequence number
means «nothing selected» (the old clipboard is never taken for a selection), the snapshot is restored unless somebody
else changed the clipboard. Actions: prompts, translation direction, own actions with {text}, preferences + migration,
/api/quick/ask with selection_action, «replace». The headless window run is tests/round27_selection_shots.py.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import quick_selection as qs  # noqa: E402
from app import quick_ask_api  # noqa: E402
from app import quick_ask_core as core  # noqa: E402


class FakeBackend(qs.Backend):
    supported = True

    def __init__(self, *, uia=None, clipboard="old", copies=None, window=None, snapshot="ok", pid=4242):
        self.uia = uia                    # None | {"text", "editable"} | Exception
        self.clipboard = clipboard        # current CF_UNICODETEXT
        self.copies = copies              # what Ctrl+C puts on the clipboard (None = the program copies nothing)
        self.win = window or {"pid": 777, "class": "Chrome_WidgetWin_1", "exe": r"C:\\Program Files\\App\\chrome.exe", "title": "Mail"}
        self.snap = snapshot
        self.pid = pid
        self.seq = 10
        self.log = []
        self.windows = {123}
        self.after_read = None            # a callable run after read_text (someone else copies meanwhile)

    def own_pid(self):
        return self.pid

    def window(self, hwnd):
        return dict(self.win)

    def uia_selection(self, hwnd, own_pid):
        self.log.append("uia")
        if isinstance(self.uia, Exception):
            raise self.uia
        return self.uia

    def snapshot(self):
        self.log.append("snapshot")
        if self.snap == "busy":
            return None
        if self.snap == "lost":
            return qs.Snapshot([(13, b"x")], lost=[2])
        return qs.Snapshot([(13, (self.clipboard + "\0").encode("utf-16-le"))])

    def restore(self, snapshot):
        self.log.append("restore")
        self.clipboard = snapshot.items[0][1].decode("utf-16-le").rstrip("\0") if snapshot.items else ""
        self.seq += 1
        return True

    def sequence(self):
        return self.seq

    def read_text(self):
        self.log.append("read")
        text = self.clipboard
        if self.after_read:
            self.after_read()
        return text

    def set_text(self, text):
        self.log.append(("set", text))
        self.clipboard = text
        self.seq += 1
        return True

    def send_copy(self):
        self.log.append("copy")
        if self.copies is not None:
            self.clipboard = self.copies
            self.seq += 1
        return True

    def send_paste(self):
        self.log.append("paste")
        return True

    def is_window(self, hwnd):
        return hwnd in self.windows


PREFS = {"quick_ask_selection": True, "quick_ask_selection_clipboard": True}


class CaptureTests(unittest.TestCase):
    def tearDown(self):
        qs.set_backend(None)

    def test_uia_selection_wins_and_has_no_side_effects(self):
        api = FakeBackend(uia={"text": "  Hello\r\nworld  ", "editable": True})
        result = qs.capture(123, PREFS, api=api)
        self.assertEqual(result["text"], "  Hello\nworld")
        self.assertEqual(result["source"], "uia")
        self.assertTrue(result["editable"])
        self.assertEqual(api.log, ["uia"], "no clipboard, no keys when UI Automation answered")
        self.assertEqual(result["app"], "chrome.exe")
        self.assertEqual(qs.public(result)["chars"], len("  Hello\nworld"))
        self.assertNotIn("hwnd", qs.public(result))

    def test_uia_says_nothing_selected_is_trusted(self):
        api = FakeBackend(uia={"text": "", "editable": True}, copies="the whole line")
        result = qs.capture(123, PREFS, api=api)
        self.assertEqual((result["text"], result["reason"]), ("", "empty"))
        self.assertNotIn("copy", api.log, "Ctrl+C would copy a whole line in some editors")

    def test_clipboard_fallback_copies_and_restores(self):
        api = FakeBackend(uia=None, clipboard="my precious clipboard", copies="selected words")
        result = qs.capture(123, PREFS, api=api)
        self.assertEqual(result["text"], "selected words")
        self.assertEqual(result["source"], "clipboard")
        self.assertTrue(result["restored"])
        self.assertEqual(api.clipboard, "my precious clipboard")
        self.assertEqual([x for x in api.log if isinstance(x, str)], ["uia", "snapshot", "copy", "read", "restore"])
        self.assertTrue(qs.clipboard_busy(), "clipboard watchers are told to skip our own copy/restore")

    def test_unchanged_sequence_number_means_no_selection(self):
        api = FakeBackend(uia=None, clipboard="old text that is NOT a selection", copies=None)
        started = time.monotonic()
        result = qs.capture(123, PREFS, api=api)
        self.assertEqual((result["text"], result["reason"]), ("", "empty"))
        self.assertNotIn("read", api.log)
        self.assertNotIn("restore", api.log, "nothing changed — nothing to restore")
        self.assertLess(time.monotonic() - started, qs.COPY_TIMEOUT + 0.2)

    def test_uia_failure_falls_back(self):
        api = FakeBackend(uia=RuntimeError("COM"), copies="x y")
        self.assertEqual(qs.capture(123, PREFS, api=api)["text"], "x y")

    def test_incomplete_or_busy_clipboard_is_never_touched(self):
        for snap, reason in (("lost", "clipboard_unsafe"), ("busy", "clipboard_busy")):
            api = FakeBackend(uia=None, copies="sel", snapshot=snap)
            result = qs.capture(123, PREFS, api=api)
            self.assertEqual((result["text"], result["reason"]), ("", reason))
            self.assertNotIn("copy", api.log)

    def test_someone_else_changed_the_clipboard_after_the_copy(self):
        api = FakeBackend(uia=None, clipboard="orig", copies="sel")

        def user_copies():
            api.clipboard, api.seq = "the user's new copy", api.seq + 1
        api.after_read = user_copies
        result = qs.capture(123, PREFS, api=api)
        self.assertNotIn("restore", api.log, "the newer clipboard content must win")
        self.assertFalse(result["restored"])

    def test_copied_but_not_text(self):
        api = FakeBackend(uia=None, copies="")
        result = qs.capture(123, PREFS, api=api)
        self.assertEqual((result["text"], result["reason"]), ("", "no_text"))
        self.assertIn("restore", api.log, "files copied by Ctrl+C in Explorer are replaced by the user's clipboard again")

    def test_own_windows_terminals_desktop_and_switches(self):
        cases = [
            ({"pid": 4242, "class": "WindowsForms10", "exe": "MemuAI.exe"}, {}, "own"),
            ({"pid": 1, "class": "CASCADIA_HOSTING_WINDOW_CLASS", "exe": "WindowsTerminal.exe"}, {}, "terminal"),
            ({"pid": 1, "class": "Chrome_WidgetWin_1", "exe": r"C:\x\Code.exe"}, {}, "terminal"),
            ({"pid": 1, "class": "TscShellContainerClass", "exe": "mstsc.exe"}, {}, "terminal"),
            ({"pid": 1, "class": "Progman", "exe": "explorer.exe"}, {}, "shell"),
            ({"pid": 1, "class": "Notepad", "exe": "notepad.exe"}, {"quick_ask_selection_clipboard": False}, "clipboard_off"),
            ({"pid": 1, "class": "Notepad", "exe": "notepad.exe"}, {"quick_ask_selection": False}, "off"),
        ]
        for window, prefs, reason in cases:
            api = FakeBackend(uia=None, copies="sel", window=window)
            result = qs.capture(123, dict(PREFS, **prefs), api=api)
            self.assertEqual((result["text"], result["reason"]), ("", reason), window)
            self.assertNotIn("copy", api.log, window)
        # a terminal that exposes its selection through UI Automation still works (no Ctrl+C needed)
        api = FakeBackend(uia={"text": "ls -la"}, window={"pid": 1, "class": "ConsoleWindowClass", "exe": "conhost.exe"})
        self.assertEqual(qs.capture(123, PREFS, api=api)["text"], "ls -la")

    def test_unsupported_platform_and_no_window(self):
        self.assertEqual(qs.capture(123, PREFS, api=qs.Backend())["reason"], "unsupported")
        self.assertEqual(qs.capture(0, PREFS, api=FakeBackend())["reason"], "no_window")
        if sys.platform != "win32":
            qs.set_backend(None)
            self.assertFalse(qs.available())
            self.assertEqual(qs.capture(123, PREFS)["reason"], "unsupported")
            self.assertFalse(qs.status({})["available"])

    def test_long_selection_is_cut_and_marked(self):
        api = FakeBackend(uia={"text": "a" * (qs.MAX_TEXT + 50)})
        result = qs.capture(123, PREFS, api=api)
        self.assertEqual(len(result["text"]), qs.MAX_TEXT)
        self.assertTrue(result["truncated"])

    def test_text_cleanup(self):
        self.assertEqual(qs.clean_text("a\x0bb\x07c\r\nd\x00  \n"), ("a\nbc\nd", False), "Word line breaks and cell marks")
        self.assertEqual(qs.clean_text(" \r\n\t "), ("", False))

    def test_snapshot_plan(self):
        save, lost = qs.plan_snapshot([13, 1, 7, 16, 49161, 8, 2, 17, 9])
        self.assertEqual(save, [13, 16, 49161, 17], "synthesized text/bitmap/palette/DIB are re-created by Windows")
        self.assertEqual(lost, [])
        self.assertEqual(qs.plan_snapshot([2])[1], [2], "a bare HBITMAP cannot be restored")
        self.assertEqual(qs.plan_snapshot([3, 14]), ([14], []))
        self.assertEqual(qs.plan_snapshot([0x80, 0x205, 0x305])[1], [0x80, 0x205, 0x305])
        self.assertEqual(qs.plan_snapshot([1]), ([1], []), "ANSI-only text is kept as is")


class ReplaceTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeBackend(uia={"text": "teh text", "editable": True}, clipboard="user clip")
        self.result = qs.capture(123, PREFS, api=self.api)
        self.manager = mock.Mock(previous_hwnd=0)

    def tearDown(self):
        qs.set_backend(None)

    def test_replace_pastes_into_the_source_and_gives_the_clipboard_back(self):
        with mock.patch.object(qs, "RESTORE_AFTER_PASTE", 0.01):
            out = qs.replace(self.manager, "the text", self.result["id"], api=self.api)
            time.sleep(0.15)
        self.assertEqual(out, {"ok": True})
        self.assertEqual(self.manager.previous_hwnd, 123)
        self.manager.hide.assert_called_once_with(restore_focus=True)
        self.assertIn(("set", "the text"), self.api.log)
        self.assertIn("paste", self.api.log)
        self.assertEqual(self.api.clipboard, "user clip")
        self.assertEqual(qs.replace(self.manager, "again", self.result["id"], api=self.api)["code"], "stale",
                         "the selection is gone after one replace")

    def test_refusals(self):
        self.assertEqual(qs.replace(self.manager, "x", "other-id", api=self.api)["code"], "stale")
        self.assertEqual(qs.replace(self.manager, "  ", self.result["id"], api=self.api)["code"], "empty")
        self.api.windows = set()
        self.assertEqual(qs.replace(self.manager, "x", self.result["id"], api=self.api)["code"], "gone")
        self.assertEqual(qs.replace(self.manager, "x", self.result["id"], api=qs.Backend())["code"], "unsupported")
        self.assertNotIn("paste", self.api.log)


class ActionTests(unittest.TestCase):
    def test_language_guess_and_direction(self):
        self.assertEqual(qs.detect_lang("Привет, как дела?"), "ru")
        self.assertEqual(qs.detect_lang("Привіт, як справи? Їжак"), "uk")
        self.assertEqual(qs.detect_lang("This is the plan for the week"), "en")
        self.assertEqual(qs.detect_lang("Das ist der Plan für die Woche, mit Grüßen"), "")
        self.assertEqual(qs.detect_lang("12345"), "")
        self.assertEqual(qs.translate_target("Hello and welcome", {}, "ru"), "ru")
        self.assertEqual(qs.translate_target("Привет, мир", {}, "ru"), "en")
        self.assertEqual(qs.translate_target("Привет, мир", {"quick_ask_selection_second_lang": "de"}, "ru"), "de")
        self.assertEqual(qs.translate_target("This is it", {}, "en"), "ru")
        self.assertEqual(qs.translate_target("Bonjour à tous", {}, "en"), "en")

    def test_js_mirror_of_the_language_guess(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        js = (ROOT / "app" / "pgstatic" / "quick" / "quick-selection.js").read_text("utf-8")
        start = js.index("function guessLang(")
        end = js.index("\n  }\n", start) + 4
        samples = ["Привет, как дела?", "Привіт, як справи? Їжак", "This is the plan for the week",
                   "Das ist der Plan für die Woche, mit Grüßen", "12345", "OK", "Ünïcödé façade"]
        script = js[start:end] + "\nconsole.log(JSON.stringify(%s.map(guessLang)));" % __import__("json").dumps(samples)
        out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=20)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(__import__("json").loads(out.stdout), [qs.detect_lang(s) for s in samples])

    def test_builtin_prompts(self):
        for lang in ("ru", "en"):
            for action in ("translate", "explain", "fix", "summarize", "reply"):
                out = qs.resolve_action({"id": action}, {}, lang=lang, selection="Some text here")
                self.assertTrue(out["text"])
                self.assertEqual(out["selection"], "Some text here")
                self.assertFalse(out.get("error"))
        fix = qs.resolve_action({"id": "fix"}, {}, lang="ru", selection="x")
        self.assertEqual(fix["settings"], {"quick_ask_style": "detailed", "quick_ask_web_search": False})
        self.assertTrue(fix["replace"])
        self.assertEqual(qs.resolve_action({"id": "explain"}, {}, lang="ru", selection="x")["settings"], {})
        tr = qs.resolve_action({"id": "translate", "target": "de"}, {}, lang="ru", selection="Hello there")
        self.assertIn("на немецкий", tr["text"])
        self.assertIn("на русский", tr["text"], "already German → the interface language")
        auto = qs.resolve_action({"id": "translate"}, {}, lang="en", selection="Привет, мир")
        self.assertIn("into English", auto["text"])
        bad = qs.resolve_action({"id": "translate", "target": "xx"}, {}, lang="ru", selection="Hello and bye")
        self.assertIn("на русский", bad["text"])
        note = qs.resolve_action({"id": "fix", "note": "  официальнее  "}, {}, lang="ru", selection="x")
        self.assertTrue(note["text"].endswith("Дополнительно: официальнее"))
        self.assertIsNone(qs.resolve_action({"id": "ask"}, {}, lang="ru", selection="x"))
        self.assertIsNone(qs.resolve_action(None, {}, lang="ru", selection="x"))
        self.assertIn("Нет выделенного", qs.resolve_action({"id": "fix"}, {}, lang="ru", selection="  ")["error"])
        self.assertIn("no selected", qs.resolve_action({"id": "fix"}, {}, lang="en", selection="")["error"])

    def test_prompts_never_trigger_web_prefetch_or_the_page(self):
        from app import websearch
        for lang in ("ru", "en"):
            for action in ("translate", "explain", "fix", "summarize", "reply"):
                text = qs.resolve_action({"id": action}, {}, lang=lang, selection="x")["text"]
                self.assertEqual(websearch.prefetch_query(text), "", (lang, action))
                self.assertFalse(quick_ask_api.names_page(text), (lang, action))

    def test_custom_actions(self):
        prefs = {"quick_ask_selection_custom": [
            {"id": "c_abcd1234", "name": "Официальнее", "prompt": "Перепиши официально:\n{text}", "replace": True},
            {"id": "c_noplace1", "name": "Шутка", "prompt": "Пошути про это"}]}
        out = qs.resolve_action({"id": "c_abcd1234"}, prefs, lang="ru", selection="привет всем")
        self.assertEqual(out["text"], "Перепиши официально:\nпривет всем")
        self.assertEqual(out["selection"], "", "{text} embedded the selection — not sent twice")
        self.assertTrue(out["replace"])
        out = qs.resolve_action({"id": "c_noplace1"}, prefs, lang="ru", selection="привет")
        self.assertIn("<selected_text>", out["text"])
        self.assertEqual(out["selection"], "привет")
        self.assertIn("больше нет", qs.resolve_action({"id": "c_gone0000"}, prefs, lang="ru", selection="x")["error"])


class PreferenceTests(unittest.TestCase):
    def test_defaults_registered_and_migration(self):
        from app.pgdata import DEFAULT_PREFERENCES, _normalize_preferences
        for key, value in qs.PREF_DEFAULTS.items():
            self.assertEqual(core.PREF_DEFAULTS[key], value)
            self.assertEqual(DEFAULT_PREFERENCES[key], value)
        prefs = _normalize_preferences({})
        self.assertTrue(prefs["quick_ask_selection"])
        self.assertEqual(prefs["quick_ask_selection_actions"], qs.DEFAULT_ACTIONS)
        # before Round 27 the switch was an opt-in nobody saw: switched on once
        self.assertTrue(_normalize_preferences({"quick_ask_selection": False})["quick_ask_selection"])
        # after that the user's «off» stays off
        self.assertFalse(_normalize_preferences({"quick_ask_selection": False, "quick_ask_selection_v": 2})["quick_ask_selection"])

    def test_normalization(self):
        out = qs.normalize_prefs({
            "quick_ask_selection_custom": [
                {"id": "bad id", "name": "  Мой   шаг ", "prompt": "Сделай {text}", "replace": "yes"},
                {"name": "", "prompt": "x"}, {"name": "x", "prompt": ""}, "junk",
            ] + [{"name": f"n{i}", "prompt": "p"} for i in range(20)],
            "quick_ask_selection_actions": ["fix", "fix", "nope", "translate", 5],
            "quick_ask_selection_second_lang": "klingon", "quick_ask_selection_clipboard": "false"}, {"quick_ask_selection_v": 2})
        custom = out["quick_ask_selection_custom"]
        self.assertEqual(len(custom), qs.MAX_CUSTOM)
        self.assertRegex(custom[0]["id"], r"^c_[a-z0-9]{8}$")
        self.assertEqual(custom[0]["name"], "Мой шаг")
        self.assertTrue(custom[0]["replace"])
        self.assertEqual(out["quick_ask_selection_actions"], ["fix", "translate"])
        self.assertEqual(out["quick_ask_selection_second_lang"], "")
        self.assertFalse(out["quick_ask_selection_clipboard"])
        self.assertEqual(qs.normalize_prefs({"quick_ask_selection_actions": "x"})["quick_ask_selection_actions"], qs.DEFAULT_ACTIONS)


class AskRouteTests(unittest.TestCase):
    """/api/quick/ask with selection_action: the instruction replaces the label, overrides apply, the page is not read."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="r27sel-")
        self.env = mock.patch.dict(os.environ, {"MEMUAI_DATA_DIR": self.tmp})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ask(self, body):
        from starlette.testclient import TestClient
        from app.playground_api import create_app
        seen = {}

        async def fake_events(**kw):
            seen.update(kw)
            yield "data: {}\n\n"
        with mock.patch.dict(quick_ask_api.EXTRA_TOOLS):
            app = create_app()
        with mock.patch.object(quick_ask_api, "ask_events", fake_events):
            response = TestClient(app, base_url="http://127.0.0.1").post("/api/quick/ask", json=dict({"model": "m"}, **body))
        return response, seen

    def test_action_builds_the_instruction(self):
        response, seen = self.ask({"text": "Исправить", "selection": "teh txet", "lang": "ru",
                                   "selection_action": {"id": "fix", "replace": True, "selId": "s1"}})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Исправь орфографию", seen["text"])
        self.assertEqual(seen["selection"], "teh txet")
        self.assertEqual(seen["settings"]["quick_ask_style"], "detailed")
        self.assertFalse(seen["settings"]["quick_ask_web_search"])
        self.assertIsNone(seen["page"])

    def test_action_without_selection_is_a_clear_error(self):
        response, seen = self.ask({"text": "Fix", "lang": "en", "selection_action": {"id": "fix"}})
        self.assertEqual(response.status_code, 400)
        self.assertIn("no selected text", response.json()["error"])
        self.assertEqual(seen, {})

    def test_status_route(self):
        from starlette.testclient import TestClient
        from app.playground_api import create_app
        with mock.patch.dict(quick_ask_api.EXTRA_TOOLS):
            app = create_app()
        data = TestClient(app, base_url="http://127.0.0.1").get("/api/quick/selection/status").json()
        self.assertEqual(data["available"], sys.platform == "win32")
        self.assertTrue(data["enabled"])

    def test_replace_window_action_without_native_window(self):
        from starlette.testclient import TestClient
        from app.playground_api import create_app
        with mock.patch.dict(quick_ask_api.EXTRA_TOOLS):
            app = create_app()
        data = TestClient(app, base_url="http://127.0.0.1").post("/api/quick/window", json={"action": "replace_selection", "text": "x"}).json()
        self.assertEqual(data, {"ok": False, "native": False})


class NativeWiringTests(unittest.TestCase):
    """QuickAskManager hands the hotkey's foreground window to quick_selection and passes the result to the page."""

    def test_show_and_toggle_use_capture(self):
        src = (ROOT / "app" / "quick_ask.py").read_text("utf-8")
        self.assertIn('capture = self.grab_selection(foreground, prefs) if source == "hotkey" else quick_selection.Capture({})', src)
        self.assertIn('"selection_info": quick_selection.public(selection)', src)
        self.assertIn("QuickAsk.onSelection", src)
        from app.quick_ask import QuickAskManager
        manager = QuickAskManager("http://x", load_prefs=lambda: {})
        with mock.patch.object(qs, "start", side_effect=RuntimeError("boom")):
            capture = manager.grab_selection(5, {})
            self.assertTrue(capture.done, "a capture failure never breaks showing the window")
            self.assertEqual(capture.result, {})

    def test_page_wiring(self):
        html = (ROOT / "app" / "pgstatic" / "quick" / "index.html").read_text("utf-8")
        self.assertLess(html.index("quick.js"), html.index("quick-selection.js"))
        self.assertIn("quick-selection.css", html)
        node = shutil.which("node")
        if node:
            for name in ("quick.js", "quick-selection.js"):
                result = subprocess.run([node, "--check", str(ROOT / "app" / "pgstatic" / "quick" / name)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)


class WindowShots(unittest.TestCase):
    """tests/round27_selection_shots.py (the window) and round27_selection_settings_shots.py (Settings) in headless
    Chromium (skipped without playwright/Chromium)."""

    def run_script(self, name):
        out = Path(tempfile.mkdtemp(prefix="r27sel-shots-"))
        try:
            env = dict(os.environ, PYTHONUTF8="1", NO_PROXY="*")
            result = subprocess.run([sys.executable, str(ROOT / "tests" / name), str(out)],
                                    capture_output=True, text=True, timeout=240, env=env)
            self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-3000:])
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def setUp(self):
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("playwright is not installed")
        chrome = os.environ.get("MEMU_TEST_CHROMIUM") or "/workspace/work/chromium/chrome"
        if not os.path.exists(chrome) and not os.path.exists("/usr/bin/chromium"):
            self.skipTest("no Chromium")

    def test_headless_window(self):
        self.run_script("round27_selection_shots.py")

    def test_headless_settings(self):
        self.run_script("round27_selection_settings_shots.py")


if __name__ == "__main__":
    unittest.main()
