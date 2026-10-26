"""Round 27 (r27-hunt-quicknew): regressions found by the review of the new Quick Ask features and of how they
work together (selection actions, wake word, region / clipboard history / snippets / stickers / documents, focus).

No network. Windows parts run against fakes that behave like pywebview / the Win32 clipboard.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from app import quick_features as qf
from app import quick_features_win as qw

ROOT = Path(__file__).resolve().parents[1]
QUICK = ROOT / "app" / "pgstatic" / "quick"

class TmpDir(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="r27hq-"))
        self.dir = mock.patch.object(qf, "_memu_dir", lambda: self.tmp)
        self.dir.start()
        qf.CLIPBOARD._rows = None

    def tearDown(self) -> None:
        self.dir.stop()
        qf.CLIPBOARD._rows = None

class Api:
    """The parts of quick_ask.Win32 the clipboard code uses; ``busy`` = times OpenClipboard still fails."""

    def __init__(self, text="hello", formats=(13,)) -> None:
        self.text, self.formats, self.busy, self.seq = text, set(formats), 0, 1
        self.sets: list = []
        self.keys: list = []

    def GetClipboardSequenceNumber(self):
        return self.seq

    def clipboard_formats(self):
        if self.busy:
            self.busy -= 1
            return {-1}
        return set(self.formats)

    def get_text(self):
        if self.busy:
            self.busy -= 1
            return None
        return self.text

    def set_text(self, text):
        self.sets.append(text)
        self.text = text
        return True

    def chord(self, *vks):
        self.keys.append(vks)

class Formats:
    def private(self):
        return False

    def owner_pid(self):
        return 42

class StickerWindow:
    """pywebview on WinForms: destroy() is Form.Close(), which raises ``closing``; a False answer cancels it."""

    def __init__(self) -> None:
        self.handlers: list = []
        self.destroyed = False

        class Events:
            pass
        self.events = Events()
        outer = self

        class Closing:
            def __iadd__(self, fn):
                outer.handlers.append(fn)
                return self
        self.events.closing = Closing()

    def destroy(self):
        if any(fn() is False for fn in self.handlers):
            return
        self.destroyed = True

class Manager:
    def __init__(self, api) -> None:
        self.api = api
        self.extra_windows = ()
        self.inserted: list = []

    def load_prefs(self):
        return {}

    def insert_text(self, text):
        self.inserted.append(text)
        self.api.text = text
        return True

class StickerCloseTests(TmpDir):
    def runtime_with(self, sticker_id: str) -> tuple:
        rt = qw.Runtime(Manager(Api()))
        window = StickerWindow()
        window.events.closing += lambda *_: rt._sticker_closing(sticker_id)
        rt.stickers[sticker_id] = {"window": window, "form": None, "hwnd": 7}
        return rt, window

    def test_close_and_delete_really_close_the_window(self) -> None:
        rt, window = self.runtime_with("k1")
        self.assertTrue(rt.close_sticker("k1"))
        self.assertTrue(window.destroyed, "closing must not cancel our own destroy()")
        self.assertEqual(rt.stickers, {})

    def test_alt_f4_still_only_closes_and_keeps_the_sticker(self) -> None:
        qf.mutate_stickers(lambda rows: rows.append(qf.normalize_sticker({"id": "k2", "text": "x", "open": True})))
        rt, window = self.runtime_with("k2")
        window.destroy()
        deadline = time.monotonic() + 8
        while not window.destroyed and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(window.destroyed)
        self.assertFalse(qf.load_stickers()[0]["open"])
        self.assertEqual(len(qf.load_stickers()), 1, "Alt+F4 closes, it does not delete")

class WatcherTests(TmpDir):
    def watcher(self, api) -> qw.ClipboardWatcher:
        watcher = qw.ClipboardWatcher(api)
        watcher.settings = qf.normalize_settings({"clipboard": True})
        watcher._formats = Formats()
        for patch in (mock.patch.object(qw, "process_name", lambda _pid: "notepad.exe"),
                      mock.patch.object(qw, "selection_busy", lambda: False)):
            patch.start()
            self.addCleanup(patch.stop)
        return watcher

    def test_busy_clipboard_is_retried_not_lost(self) -> None:
        api = Api("copied text")
        watcher = self.watcher(api)
        api.busy = 1
        self.assertIs(watcher.capture(own_pid=1), qw.BUSY)
        self.assertEqual(watcher.capture(own_pid=1)["text"], "copied text")

    def test_loop_retries_a_busy_copy_and_skips_our_own_paste(self) -> None:
        api = Api("first")
        watcher = self.watcher(api)
        with mock.patch.object(qw, "ClipboardFormats", lambda _api: Formats()), mock.patch.object(qw, "POLL_SECONDS", 0.02):
            watcher.start(watcher.settings)
            time.sleep(0.1)
            api.busy, api.seq = 2, 2
            deadline = time.monotonic() + 8
            while not qf.CLIPBOARD.rows() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual([r["text"] for r in qf.CLIPBOARD.rows()], ["first"])
            watcher.quiet(5)
            api.text, api.seq = "our snippet", 3
            time.sleep(0.3)
            watcher.stop()
        self.assertEqual([r["text"] for r in qf.CLIPBOARD.rows()], ["first"], "a paste by MemuAI is not a copy")

    def test_quick_off_on_never_leaves_a_dead_watcher(self) -> None:
        api = Api()
        watcher = self.watcher(api)
        with mock.patch.object(qw, "ClipboardFormats", lambda _api: Formats()), mock.patch.object(qw, "POLL_SECONDS", 0.05):
            watcher.start(watcher.settings)
            first = watcher._thread
            time.sleep(0.12)
            watcher.stop()
            time.sleep(0.06)
            watcher.start(watcher.settings)
            first.join(1)
            self.assertFalse(first.is_alive())
            time.sleep(0.15)
            self.assertTrue(watcher.running, "off → on must leave a thread watching")
            watcher.stop()

    def test_insert_never_wipes_a_clipboard_it_could_not_read(self) -> None:
        api = Api("user's text")
        rt = qw.Runtime(Manager(api))
        real = api.get_text
        api.get_text = lambda: None
        self.assertTrue(rt.insert("snippet", 0))
        api.get_text = real
        self.assertNotIn(None, api.sets, "an unreadable clipboard is left alone, not emptied")

class SelectionCaptureTests(unittest.TestCase):
    def tearDown(self) -> None:
        from app import quick_selection as qs
        qs.set_backend(None)

    def capture(self, exe: str, copies):
        from app import quick_selection as qs
        from tests.round27_selection_test import PREFS, FakeBackend
        api = FakeBackend(copies=copies, window={"pid": 777, "class": "PX_WINDOW_CLASS", "exe": "C:\\Apps\\" + exe, "title": "x"})
        return qs.capture(123, PREFS, api=api), api

    def test_editors_where_ctrl_c_is_a_command_are_never_sent_it(self) -> None:
        for exe in ("emacs.exe", "runemacs.exe", "gvim.exe"):
            result, api = self.capture(exe, "picked")
            self.assertEqual(result["reason"], "terminal", exe)
            self.assertNotIn("copy", api.log, exe)

    def test_a_snapshot_that_is_not_put_back_is_freed(self) -> None:
        from app import quick_selection as qs
        from tests.round27_selection_test import PREFS, FakeBackend

        class Counting(FakeBackend):
            discarded = 0

            def restore(self, snapshot):
                snapshot.restored = True
                return super().restore(snapshot)

            def discard(self, snapshot):
                if snapshot is not None and not snapshot.restored:
                    Counting.discarded += 1
        api = Counting(copies=None)
        self.assertEqual(qs.capture(123, PREFS, api=api)["reason"], "empty")
        self.assertEqual(Counting.discarded, 1, "the EMF copy of an untouched clipboard is freed")
        api = Counting(copies="picked")
        self.assertEqual(qs.capture(123, PREFS, api=api)["text"], "picked")
        self.assertEqual(Counting.discarded, 1, "a restored snapshot belongs to the clipboard")
        api = Counting(snapshot="lost")
        self.assertEqual(qs.capture(123, PREFS, api=api)["reason"], "clipboard_unsafe")
        self.assertEqual(Counting.discarded, 2)

    def test_line_copy_of_an_empty_selection_is_not_a_selection(self) -> None:
        result, api = self.capture("sublime_text.exe", "    return value\r\n")
        self.assertEqual(result["reason"], "empty")
        self.assertEqual(api.clipboard, "old", "the user's clipboard is back")
        result, _api = self.capture("sublime_text.exe", "two\nlines\n")
        self.assertEqual(result["text"], "two\nlines")
        result, _api = self.capture("notepad.exe", "whole line\r\n")
        self.assertEqual(result["text"], "whole line", "other programs copy only what is selected")

class HotkeyOwnerTests(TmpDir):
    def test_memuai_hotkeys_do_not_collide(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import assistant_commands as ac
        app = FastAPI()
        app.include_router(qf.router)
        client = TestClient(app)
        prefs = {"quick_ask_hotkey": "Alt+Space", "quick_ask_enabled": True}
        store = ac.Store(self.tmp / "assistant.json")
        store.update(lambda data: data.__setitem__("commands", [ac.validate_command(
            {"id": "c1", "name": "Музыка", "hotkey": "Ctrl+Alt+M", "steps": [{"type": "action", "action": "volume.set", "params": {"level": 20}}]})]))
        with mock.patch("app.pgdata.load_preferences", lambda: prefs), \
                mock.patch.object(ac, "Store", lambda path=None: store):
            bad = client.post("/api/quick/features/settings", json={"settings": {"region_hotkey": "Ctrl+Alt+M"}, "lang": "ru"})
            self.assertEqual(bad.status_code, 400)
            self.assertIn("Музыка", bad.json()["error"])
            bad = client.post("/api/quick/features/settings", json={"settings": {"clipboard_hotkey": "Alt+Space"}, "lang": "en"})
            self.assertIn("Quick Ask", bad.json()["error"])
            ok = client.post("/api/quick/features/settings", json={"settings": {"region": True, "region_hotkey": "Ctrl+Alt+R"}})
            self.assertEqual(ok.status_code, 200)
            self.assertIn("screen region", qf.hotkey_owner("ctrl+alt+r", "en"))
            self.assertEqual(qf.hotkey_owner("Ctrl+Alt+R", skip="region"), "", "its own hotkey is not a conflict")
            with self.assertRaises(ac.CommandError) as err:
                ac.hotkey_conflict({"id": "c2", "hotkey": "Ctrl+Alt+R", "enabled": True}, [], "ru")
            self.assertIn("область экрана", str(err.exception))

class PrefsTests(unittest.TestCase):
    def test_ai_snippet_prompt_is_kept_in_the_chat(self) -> None:
        from app import quick_ask_core as core
        chat = core.clean_chat({"id": "chat1234", "turns": [{"q": "Короче", "a": "ok", "prompt": "Сократи текст" * 3000}]})
        self.assertEqual(len(chat["turns"][0]["prompt"]), 20000)
        self.assertNotIn("prompt", core.clean_chat({"id": "chat1234", "turns": [{"q": "a", "a": "b"}]})["turns"][0])

class WindowShots(unittest.TestCase):
    def test_headless_window(self) -> None:
        chrome = os.environ.get("MEMU_TEST_CHROMIUM") or "/workspace/work/chromium/chrome"
        if not os.path.exists(chrome) and not os.path.exists("/usr/bin/chromium"):
            self.skipTest("no Chromium")
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("no playwright")
        out = tempfile.mkdtemp(prefix="r27hq-shots-")
        env = dict(os.environ, PYTHONUTF8="1", NO_PROXY="*")
        proc = subprocess.run([sys.executable, str(ROOT / "tests" / "round27_hunt_quick_shots.py"), out],
                              cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=600)
        shutil.rmtree(out, ignore_errors=True)
        self.assertEqual(proc.returncode, 0, proc.stdout[-3000:] + proc.stderr[-3000:])

if __name__ == "__main__":
    unittest.main()
