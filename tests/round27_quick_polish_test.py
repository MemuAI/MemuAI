"""Round 27 (r27-quick-polish): the Quick Ask window does not wait for the Ctrl+C selection read; snippets and AI
templates follow the interface language until the user edits them.

Selection (app/quick_selection.py + QuickAskManager.show/toggle, fake backend and a fake native window): the order
of events (snapshot → Ctrl+C → input flushed → the window shown and focused → the copied text read → the clipboard
restored → the selection pushed to the open window as ``late``), no blocking of show(), a selection that arrives after
the window was hidden is not pushed (the clipboard is still restored), a second hotkey press waits for the copy in
flight before snapshotting, settle() before the focus moves when delivery was not confirmed.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import quick_selection as qs  # noqa: E402
from tests.round27_selection_test import PREFS, FakeBackend  # noqa: E402


class SlowCopyBackend(FakeBackend):
    """An Electron-like program: Ctrl+C puts the text on the clipboard ``delay`` seconds later, in its own thread."""

    def __init__(self, *, delay=0.15, flushed=True, events=None, **kw):
        super().__init__(**kw)
        self.delay = delay
        self.flushed = flushed
        self.events = events if events is not None else []
        self.lock = threading.Lock()

    def note(self, what):
        with self.lock:
            self.events.append(what)

    def snapshot(self):
        self.note("snapshot")
        return super().snapshot()

    def send_copy(self):
        self.note("copy")
        if self.copies is not None:
            def later():
                time.sleep(self.delay)
                with self.lock:
                    self.clipboard = self.copies
                    self.seq += 1
            threading.Thread(target=later, daemon=True).start()
        return True

    def flush_input(self):
        self.note("flush")
        return self.flushed

    def read_text(self):
        self.note("read")
        return super().read_text()

    def restore(self, snapshot):
        self.note("restore")
        snapshot.restored = True
        return super().restore(snapshot)


class StartTests(unittest.TestCase):
    def tearDown(self):
        qs.set_backend(None)

    def test_start_returns_right_after_ctrl_c_and_finishes_in_the_background(self):
        api = SlowCopyBackend(delay=0.2, clipboard="user clip", copies="picked words")
        began = time.monotonic()
        capture = qs.start(123, PREFS, api=api)
        self.assertLess(time.monotonic() - began, 0.1, "the window must not wait for the program to copy")
        self.assertFalse(capture.done)
        self.assertEqual(api.events, ["snapshot", "copy", "flush"], "Ctrl+C is delivered BEFORE start() returns")
        got = []
        capture.then(got.append)
        result = capture.wait(2)
        time.sleep(0.01)
        self.assertEqual(result["text"], "picked words")
        self.assertEqual(got and got[0]["text"], "picked words")
        self.assertEqual(api.events[-2:], ["read", "restore"])
        self.assertEqual(api.clipboard, "user clip", "the user's clipboard is back")
        self.assertEqual(qs.last()["text"], "picked words", "«Заменить» knows the selection")

    def test_nothing_selected_finishes_without_reading_or_restoring(self):
        api = SlowCopyBackend(copies=None)
        with mock.patch.object(qs, "COPY_TIMEOUT", 0.08):
            capture = qs.start(123, PREFS, api=api)
            self.assertFalse(capture.done)
            self.assertEqual(capture.wait(1)["reason"], "empty")
        self.assertNotIn("read", api.events)
        self.assertNotIn("restore", api.events)

    def test_uia_and_policy_answers_are_done_at_once(self):
        capture = qs.start(123, PREFS, api=FakeBackend(uia={"text": "from uia"}))
        self.assertTrue(capture.done)
        self.assertEqual(capture.result["text"], "from uia")
        got = []
        capture.then(got.append)
        self.assertEqual(got[0]["text"], "from uia", "then() on a finished capture calls back at once")
        terminal = FakeBackend(window={"pid": 1, "class": "ConsoleWindowClass", "exe": "cmd.exe"})
        self.assertEqual(qs.start(123, PREFS, api=terminal).result["reason"], "terminal")

    def test_second_press_waits_for_the_copy_in_flight(self):
        api = SlowCopyBackend(delay=0.12, clipboard="user clip", copies="first")
        first = qs.start(123, PREFS, api=api)
        second = qs.start(123, PREFS, api=api)          # blocks until the first one restored the clipboard
        self.assertTrue(first.done)
        self.assertEqual(first.result["text"], "first")
        snaps = [i for i, e in enumerate(api.events) if e == "snapshot"]
        self.assertGreater(snaps[1], api.events.index("restore"),
                           "the second snapshot is taken only after the first copy put the user's clipboard back")
        self.assertEqual(second.wait(2)["text"], "first")    # still selected in the program
        self.assertEqual(api.clipboard, "user clip")

    def test_a_copy_that_never_finishes_is_not_snapshotted_over(self):
        stuck = qs.Capture()
        with mock.patch.dict(qs._state, {"pending": stuck}), mock.patch.object(qs, "COPY_TIMEOUT", 0.0):
            api = SlowCopyBackend(copies="x")
            with mock.patch.object(stuck, "wait", return_value=qs._result(reason="pending")):
                result = qs.start(123, PREFS, api=api).result
        self.assertEqual(result["reason"], "clipboard_busy")
        self.assertEqual(api.events, [], "the clipboard is not touched")

    def test_settle_only_when_delivery_was_not_confirmed(self):
        api = SlowCopyBackend(copies="x", flushed=False)
        capture = qs.start(123, PREFS, api=api)
        self.assertFalse(capture.flushed)
        began = time.monotonic()
        capture.settle()
        self.assertGreaterEqual(time.monotonic() - capture.copied_at, qs.SETTLE_GAP - 0.002)
        self.assertLess(time.monotonic() - began, qs.SETTLE_GAP + 0.02)
        capture.wait(2)
        quick = qs.start(123, PREFS, api=SlowCopyBackend(copies="x"))
        began = time.monotonic()
        quick.settle()
        self.assertLess(time.monotonic() - began, 0.005, "confirmed delivery: no wait at all")
        quick.wait(2)

    def test_blocking_capture_is_still_available(self):
        api = SlowCopyBackend(delay=0.05, copies="for canvas")
        self.assertEqual(qs.capture(123, PREFS, api=api)["text"], "for canvas")

    def test_failure_in_the_background_frees_the_snapshot_and_finishes(self):
        class Broken(SlowCopyBackend):
            freed = 0

            def read_text(self):
                raise OSError("boom")

            def discard(self, snapshot):
                if snapshot is not None and not snapshot.restored:
                    Broken.freed += 1
        api = Broken(delay=0.0, copies="x")
        result = qs.start(123, PREFS, api=api).wait(2)
        self.assertEqual(result["reason"], "error")
        self.assertEqual(Broken.freed, 1)

    def test_blocking_waits_are_small(self):
        self.assertLessEqual(qs.UIA_TIMEOUT, 0.2)
        self.assertLessEqual(qs.SNAPSHOT_BUDGET, 0.25)
        self.assertLessEqual(qs.MODIFIER_WAIT, 0.05)
        self.assertLessEqual(qs.INPUT_FLUSH, 0.05)


class FakeWinApi:
    def __init__(self, events, foreground=555):
        self.events = events
        self.foreground = foreground

    def GetForegroundWindow(self):
        return self.foreground

    def take_foreground(self, hwnd):
        self.events.append("focus")
        self.foreground = hwnd
        return True

    def IsWindow(self, hwnd):
        return True

    def IsIconic(self, hwnd):
        return False

    def SetWindowPos(self, *args):
        return True


class FakeForm:
    def __init__(self, events):
        self.events = events

    def Show(self):
        self.events.append("show")

    def Hide(self):
        self.events.append("hide")


def make_manager(events):
    from app.quick_ask import QuickAskManager
    manager = QuickAskManager("http://x", load_prefs=lambda: {"quick_ask_browser": False})
    manager.native_ok = True
    manager.api = FakeWinApi(events)
    manager.form = FakeForm(events)
    manager.hwnd = 999
    manager.pushes = []

    def push(script):
        manager.pushes.append(script)
        return 0
    geometry = {"x": 0, "y": 0, "w": 600, "h": 120, "scale": 1.0, "max_h": 600, "monitor": "m", "anchor": "top"}
    manager._push = push
    manager.ensure_window = lambda: True
    manager._on_ui = lambda fn: fn()
    manager._initial_geometry = lambda prefs: dict(geometry)
    manager._place = lambda *a, **k: None
    manager._round_region = lambda *a, **k: None
    manager._arm_escape = lambda: None
    manager._disarm_escape = lambda: None
    manager._focus_browser = lambda: None
    manager._begin_page = lambda *a: None
    manager.raise_to_top = lambda: True
    return manager


def late_pushes(manager):
    out = []
    for script in manager.pushes:
        if "QuickAsk.onSelection(" in script:
            out.append(json.loads(script.split("QuickAsk.onSelection(", 1)[1][:-1]))
    return out


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.events = []

    def tearDown(self):
        qs.set_backend(None)

    def test_show_does_not_wait_and_the_selection_arrives_in_the_open_window(self):
        api = SlowCopyBackend(delay=0.25, clipboard="user clip", copies="hello from discord", events=self.events)
        qs.set_backend(api)
        manager = make_manager(self.events)
        began = time.monotonic()
        self.assertTrue(manager.show("hotkey"))
        took = time.monotonic() - began
        self.assertLess(took, 0.15, f"show() took {took:.3f} s")
        self.assertEqual(self.events[:5], ["snapshot", "copy", "flush", "show", "focus"],
                         "Ctrl+C reaches the program BEFORE our window is shown and takes the focus")
        on_show = next(s for s in manager.pushes if "QuickAsk.onShow(" in s)
        payload = json.loads(on_show.split("QuickAsk.onShow(", 1)[1][:-1])
        self.assertEqual(payload["selection"], "")
        self.assertTrue(payload["selection_pending"])
        deadline = time.monotonic() + 2
        while not late_pushes(manager) and time.monotonic() < deadline:
            time.sleep(0.01)
        pushed = late_pushes(manager)
        self.assertEqual(len(pushed), 1)
        self.assertEqual(pushed[0]["text"], "hello from discord")
        self.assertTrue(pushed[0]["late"])
        self.assertNotIn("hwnd", pushed[0]["info"])
        self.assertEqual(api.clipboard, "user clip")
        self.assertLess(self.events.index("show"), self.events.index("read"))

    def test_uia_selection_is_in_the_show_payload(self):
        qs.set_backend(SlowCopyBackend(uia={"text": "word doc", "editable": True}, events=self.events))
        manager = make_manager(self.events)
        manager.show("hotkey")
        payload = json.loads(next(s for s in manager.pushes if "QuickAsk.onShow(" in s).split("QuickAsk.onShow(", 1)[1][:-1])
        self.assertEqual(payload["selection"], "word doc")
        self.assertFalse(payload["selection_pending"])
        time.sleep(0.05)
        self.assertEqual(late_pushes(manager), [])

    def test_hidden_before_the_copy_arrived_gets_nothing_but_the_clipboard_is_restored(self):
        api = SlowCopyBackend(delay=0.15, clipboard="user clip", copies="too late", events=self.events)
        qs.set_backend(api)
        manager = make_manager(self.events)
        manager.show("hotkey")
        manager.hide(restore_focus=False)
        deadline = time.monotonic() + 2
        while "restore" not in self.events and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.02)
        self.assertEqual(late_pushes(manager), [], "a closed window does not get the old selection")
        self.assertEqual(api.clipboard, "user clip")

    def test_reopened_window_does_not_get_the_previous_opening_selection(self):
        api = SlowCopyBackend(delay=0.15, clipboard="user clip", copies="first", events=self.events)
        qs.set_backend(api)
        manager = make_manager(self.events)
        manager.show("hotkey")
        manager.hide(restore_focus=False)
        api.copies = None                           # nothing selected the second time
        with mock.patch.object(qs, "COPY_TIMEOUT", 0.05):
            manager.show("hotkey")                  # waits for the first copy (in flight), then its own
            time.sleep(0.2)
        self.assertEqual(late_pushes(manager), [])

    def test_toggle_over_a_pinned_window_raises_at_once(self):
        api = SlowCopyBackend(delay=0.2, copies="pinned sel", events=self.events)
        qs.set_backend(api)
        manager = make_manager(self.events)
        manager.visible = True
        began = time.monotonic()
        manager.toggle()
        self.assertLess(time.monotonic() - began, 0.15)
        self.assertTrue(any("QuickAsk.selectionPending()" in s for s in manager.pushes))
        self.assertLess(self.events.index("flush"), self.events.index("focus"))
        deadline = time.monotonic() + 2
        while not late_pushes(manager) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(late_pushes(manager)[0]["text"], "pinned sel")
        self.assertEqual(manager.previous_hwnd, 555)

    def test_page_handles_late_selection_quietly(self):
        src = (ROOT / "app" / "pgstatic" / "quick" / "quick.js").read_text("utf-8")
        self.assertIn("selection_pending", src)
        self.assertIn("selectionPending()", src)
        body = src[src.index("onSelection(payload = {}) {"):]
        body = body[:body.index("\n    },")]
        self.assertIn("S.turns.length !== wait.turns", body)
        self.assertNotIn("focusInput", body, "a late selection never moves the focus or the caret")


class DefaultSnippetLanguageTests(unittest.TestCase):
    def setUp(self):
        import shutil
        import tempfile
        from app import quick_features as qf
        self.qf = qf
        self.tmp = Path(tempfile.mkdtemp(prefix="r27qp-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        patcher = mock.patch.object(qf, "_memu_dir", lambda: self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)

    def names(self, lang):
        return [row["name"] for row in self.qf.load_config(lang)["snippets"]]

    def test_untouched_defaults_follow_the_interface_language(self):
        qf = self.qf
        self.assertIn("Today's date", self.names("en"))       # the first load happened in English
        ru = qf.load_config("ru")["snippets"]
        self.assertEqual([r["name"] for r in ru], [r["name"] for r in qf.default_snippets("ru")])
        self.assertEqual([r["text"] for r in ru], [r["text"] for r in qf.default_snippets("ru")], "AI templates too")
        self.assertIn("спасибо", [r["keyword"] for r in ru])
        self.assertIn("Fix mistakes", self.names("en"), "and back")

    def test_an_edited_default_is_the_users(self):
        qf = self.qf
        rows = qf.load_config("en")["snippets"]
        rows[1] = dict(rows[1], text="Thanks a lot! {cursor}")     # «Thank-you reply» edited
        rows[5] = dict(rows[5], name="Fix my English")
        qf.save_config(snippets=rows)
        ru = qf.load_config("ru")["snippets"]
        self.assertEqual(ru[1]["text"], "Thanks a lot! {cursor}")
        self.assertEqual(ru[1]["name"], "Thank-you reply", "a row the user touched is not half-translated")
        self.assertEqual(ru[5]["name"], "Fix my English")
        self.assertEqual(ru[0]["name"], "Сегодняшняя дата", "the untouched ones still follow the language")
        self.assertEqual(ru[2]["name"], "Вежливо отказать")

    def test_a_keyword_taken_by_the_users_own_snippet_is_not_stolen(self):
        qf = self.qf
        rows = qf.load_config("ru")["snippets"]
        rows.append({"id": "mine", "keyword": "thanks", "name": "My thanks", "kind": "text", "text": "Cheers!"})
        qf.save_config(snippets=rows)
        en = qf.load_config("en")["snippets"]
        self.assertEqual(en[1]["keyword"], "спасибо", "the default keeps its Russian keyword rather than take «thanks»")
        self.assertEqual(next(r for r in en if r["id"] == "mine")["keyword"], "thanks")
        self.assertEqual(en[0]["name"], "Today's date")

    def test_settings_save_keeps_defaults_language_neutral(self):
        qf = self.qf
        qf.load_config("en")
        qf.save_config(settings={"clipboard": True})              # save_config loads without a language
        self.assertIn("Today's date", self.names("en"))
        self.assertIn("Сегодняшняя дата", self.names("ru"))

    def test_commands_settings_strings(self):
        src = (ROOT / "app" / "pgstatic" / "quick" / "plugins" / "commands-settings.js").read_text("utf-8")
        self.assertIn('["2gis", t("2ГИС", "2GIS")]', src)
        self.assertIn('["yandex_go", t("Яндекс Go", "Yandex Go")]', src)


if __name__ == "__main__":
    unittest.main()
