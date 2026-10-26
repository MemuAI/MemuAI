"""Round 24: Browser (CDP) and Computer Use reliability — fake CDP and fake OS, no real browser/desktop."""
from __future__ import annotations

import asyncio
import ctypes
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import automation_runtime as ar

_real_sleep = asyncio.sleep

async def _no_sleep(*_args, **_kwargs):
    await _real_sleep(0)

def run(coro):
    return asyncio.run(coro)

class FakeEdge(ar.EdgeAutomation):
    """CDP is replaced: every command is recorded and answered from a script."""

    def __init__(self, *args, element=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls: list[tuple[str, dict]] = []
        self.evals: list[str] = []
        self.element = element or {"state": "ok", "x": 100, "y": 200, "hit": True, "tag": "input",
                                   "sensitive": False, "readonly": False, "editable": False}
        self.value = ""
        self.url = "https://example.com/"

    async def start(self):
        return None

    async def command(self, method, params=None, *, session_id=None, timeout=30):
        self.calls.append((method, dict(params or {})))
        if method == "Input.insertText":
            self.value = params["text"]
        if method == "Page.captureScreenshot":
            return {"data": "AAAA"}
        return {}

    async def evaluate(self, expression, *, timeout=15):
        self.evals.append(expression)
        if expression.strip() == "location.href":
            return self.url
        if expression.strip() == "scrollY":
            return 0
        if expression.strip() == "document.readyState":
            return "complete"
        if "elementFromPoint" in expression:
            return self.element
        if "isContentEditable ? e.innerText : e.value" in expression:
            return self.value
        if "no-option" in expression:
            return "no-option" if "zzz" in expression else "selected: Beta"
        if "const MARK" in expression:
            return json.dumps({"url": self.url, "title": "T", "text": "hello", "elements": [
                {"id": 1, "tag": "button", "text": "Go"}], "scroll": {"y": 0}})
        if "e.focus()" in expression:
            return "focused"
        return True

    def methods(self, name):
        return [params for method, params in self.calls if method == name]

class BrowserActions(unittest.TestCase):
    def test_click_uses_a_trusted_mouse_event_at_the_element_centre(self):
        edge = FakeEdge()
        self.assertIn("mouse", run(edge.perform({"action": "click", "element": 3})))
        events = edge.methods("Input.dispatchMouseEvent")
        self.assertEqual([e["type"] for e in events], ["mouseMoved", "mousePressed", "mouseReleased"])
        self.assertEqual((events[1]["x"], events[1]["y"]), (100.0, 200.0))

    def test_a_covered_element_falls_back_to_a_dom_click(self):
        edge = FakeEdge(element={"state": "ok", "x": 10, "y": 10, "hit": False, "tag": "button"})
        self.assertIn("dom", run(edge.perform({"action": "click", "element": 3})))
        self.assertFalse(edge.methods("Input.dispatchMouseEvent"))
        self.assertTrue(any("f.e.click()" in e for e in edge.evals))

    def test_missing_or_disabled_elements_are_recoverable_errors(self):
        for state in ("missing", "disabled"):
            edge = FakeEdge(element={"state": state})
            with self.assertRaises(ValueError):
                run(edge.perform({"action": "click", "element": 2}))

    def test_typing_replaces_text_through_insert_text_and_can_submit(self):
        edge = FakeEdge()
        outcome = run(edge.perform({"action": "type", "element": 4, "text": "Привет 🌍", "submit": True}))
        self.assertEqual(edge.methods("Input.insertText"), [{"text": "Привет 🌍"}])
        self.assertIn("Enter", outcome)
        keys = edge.methods("Input.dispatchKeyEvent")
        self.assertEqual(keys[0]["key"], "Enter")

        self.assertTrue(any("e.select()" in e for e in edge.evals))
        self.assertTrue(any("new Event('change'" in e for e in edge.evals))

    def test_typing_into_a_secret_field_is_refused(self):
        edge = FakeEdge(element={"state": "ok", "x": 1, "y": 1, "hit": True, "tag": "input", "sensitive": True})
        with self.assertRaises(PermissionError):
            run(edge.perform({"action": "type", "element": 4, "text": "x"}))
        self.assertFalse(edge.methods("Input.insertText"))

    def test_select_and_unknown_option(self):
        edge = FakeEdge(element={"state": "ok", "x": 1, "y": 1, "hit": True, "tag": "select"})
        self.assertIn("Beta", run(edge.perform({"action": "select", "element": 5, "option": "beta"})))
        with self.assertRaises(ValueError):
            run(edge.perform({"action": "select", "element": 5, "option": "zzz"}))

    def test_key_combos(self):
        edge = FakeEdge()
        run(edge.press_keys("ctrl+a"))
        down = edge.methods("Input.dispatchKeyEvent")[0]
        self.assertEqual((down["modifiers"], down["type"], "text" in down), (2, "rawKeyDown", False))
        with self.assertRaises(ValueError):
            run(edge.press_keys("ctrl+a+b"))
        with self.assertRaises(ValueError):
            run(edge.press_keys("hyper"))

    def test_scroll_uses_a_wheel_event_and_reports_when_nothing_moved(self):
        edge = FakeEdge()
        outcome = run(edge.perform({"action": "scroll", "dy": 500}))
        self.assertEqual(edge.methods("Input.dispatchMouseEvent")[0]["deltaY"], 500)
        self.assertIn("unchanged", outcome)

    def test_click_xy_is_limited_to_the_viewport(self):
        edge = FakeEdge()
        run(edge.perform({"action": "click_xy", "x": 640, "y": 400}))
        with self.assertRaises(ValueError):
            run(edge.perform({"action": "click_xy", "x": 5000, "y": 10}))

    def test_ui_polling_does_not_renumber_elements(self):
        edge = FakeEdge()
        edge.process = type("P", (), {"poll": lambda self: None})()
        edge.socket = object()
        run(edge.public_state())
        self.assertIn("const MARK = false", edge.evals[-1])
        run(edge.snapshot())
        self.assertIn("const MARK = true", edge.evals[-1])

    def test_snapshot_script_clears_old_marks_and_reads_frames(self):
        self.assertIn("removeAttribute('data-memuai-agent-id')", ar._SNAPSHOT_JS)
        self.assertIn("contentDocument", ar._FIND_JS)

class BrowserEvents(unittest.TestCase):
    def _edge(self):
        edge = FakeEdge()
        edge.socket = sock = object()
        return edge, sock

    def test_alert_is_accepted_and_confirm_is_dismissed(self):
        edge, sock = self._edge()
        run(edge._handle_event({"method": "Page.javascriptDialogOpening",
                                "params": {"type": "alert", "message": "hi"}}, sock))
        run(edge._handle_event({"method": "Page.javascriptDialogOpening",
                                "params": {"type": "confirm", "message": "Delete?"}}, sock))
        self.assertEqual([c["accept"] for c in edge.methods("Page.handleJavaScriptDialog")], [True, False])
        self.assertEqual(edge.dialogs[-1]["message"], "Delete?")

    def test_a_blocked_navigation_keeps_the_page_instead_of_an_error_page(self):
        edge, sock = self._edge()
        with patch.object(ar, "_safe_navigation_url", side_effect=ValueError("private")):
            run(edge._handle_event({"method": "Fetch.requestPaused", "sessionId": "s1", "params": {
                "requestId": "r1", "resourceType": "Document", "request": {"url": "http://10.0.0.1/"}}}, sock))
            run(edge._handle_event({"method": "Fetch.requestPaused", "sessionId": "s1", "params": {
                "requestId": "r2", "resourceType": "Script", "request": {"url": "http://10.0.0.1/a.js"}}}, sock))
        self.assertEqual(edge.methods("Fetch.fulfillRequest")[0]["responseCode"], 204)
        self.assertEqual(edge.methods("Fetch.failRequest")[0]["requestId"], "r2")
        self.assertEqual(edge.blocked_navigations[0]["url"], "http://10.0.0.1/")

    def test_data_urls_are_not_blocked(self):
        edge, sock = self._edge()
        with patch.object(ar, "_safe_navigation_url", side_effect=ValueError("no")):
            run(edge._handle_event({"method": "Fetch.requestPaused", "sessionId": "s", "params": {
                "requestId": "r", "resourceType": "Image", "request": {"url": "data:image/png;base64,AA"}}}, sock))
        self.assertEqual(edge.methods("Fetch.continueRequest"), [{"requestId": "r"}])

    def test_popups_are_closed(self):
        edge, sock = self._edge()
        run(edge._handle_event({"method": "Target.attachedToTarget", "params": {
            "sessionId": "child", "targetInfo": {"targetId": "T2", "type": "page"}}}, sock))
        self.assertEqual(edge.methods("Target.closeTarget"), [{"targetId": "T2"}])

class BrowserProfiles(unittest.TestCase):
    def test_persistent_profile_is_kept_and_reused(self):
        folder = Path(tempfile.mkdtemp()) / "session-persistent"
        edge = ar.EdgeAutomation(user_data_dir=folder)
        self.assertEqual(edge._new_profile(), folder)
        edge.profile = folder
        run(edge.close_browser())
        self.assertTrue(folder.is_dir())

    def test_throwaway_profile_is_removed(self):
        with tempfile.TemporaryDirectory() as data, patch.object(ar, "DATA_DIR", data):
            edge = ar.EdgeAutomation()
            profile = edge._new_profile()
            self.assertTrue(profile.name.startswith("session-"))
            edge.profile = profile
            run(edge.close_browser())
            self.assertFalse(profile.exists())

    def test_old_constructor_still_works(self):
        edge = ar.EdgeAutomation()
        self.assertIsNone(edge.user_data_dir)
        self.assertTrue(edge.headless)

    def test_executable_override(self):
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as handle:
            path = handle.name
        try:
            with patch.dict(os.environ, {"MEMUAI_BROWSER_EXECUTABLE": path}):
                self.assertEqual(ar._edge_executable(), path)
        finally:
            os.unlink(path)

class BrowserAgentLoop(unittest.TestCase):
    def test_repeating_an_ineffective_action_ends_the_run(self):
        edge = FakeEdge()

        async def completion(model, messages):
            return '{"action": "click", "element": 1, "reason": "again"}'

        run(ar.run_browser_agent(edge, completion, model="m", task="t", max_steps=10))
        self.assertEqual(edge.status, "error")
        self.assertIn("зациклился", edge.error)
        self.assertLessEqual(len(edge.methods("Input.dispatchMouseEvent")), 3 * 3)

    def test_a_warning_reaches_the_model_after_the_first_ineffective_repeat(self):
        edge = FakeEdge()
        prompts = []

        async def completion(model, messages):
            text = messages[1]["content"][0]["text"]
            prompts.append(text)
            if len(prompts) >= 4:
                return '{"action": "done", "report": "ok"}'
            return '{"action": "click", "element": 1}'

        run(ar.run_browser_agent(edge, completion, model="m", task="t", max_steps=10))
        self.assertEqual(edge.status, "done")
        self.assertIn("page did not change", prompts[2])
        self.assertIn("WARNING", prompts[3])

    def test_time_limit(self):
        edge = FakeEdge()

        async def completion(model, messages):
            return '{"action": "wait", "seconds": 0.1}'

        run(ar.run_browser_agent(edge, completion, model="m", task="t", max_steps=10, max_seconds=0))
        self.assertEqual(edge.status, "done")
        self.assertIn("лимит времени", edge.report)

class WindowsInput(unittest.TestCase):
    def test_input_struct_has_the_windows_size(self):
        types = ar._win_input_types()
        expected = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
        self.assertEqual(ctypes.sizeof(types["INPUT"]), expected)

    def test_unicode_typing_plan(self):
        self.assertEqual(ar._utf16_units("😀"), [0xD83D, 0xDE00])
        plan = ar._text_key_plan("Я\r\nb\tc😀\n")
        self.assertEqual(plan, [("unit", ord("Я")), ("vk", 0x0D), ("unit", ord("b")), ("vk", 0x09),
                                ("unit", ord("c")), ("unit", 0xD83D), ("unit", 0xDE00), ("vk", 0x0D)])

    def test_type_text_sends_complete_batches(self):
        sent = []
        with patch.object(ar, "_send_inputs", side_effect=lambda items: sent.append(len(items))):
            ar.ComputerAutomation._type_text("x" * 40 + "😀")
        self.assertEqual(sum(sent), 2 * 42)
        self.assertTrue(all(n % 2 == 0 for n in sent))

    def test_extended_keys_get_the_extended_flag(self):
        flags = ar.ComputerAutomation._key_flags
        self.assertTrue(flags(0x25, False) & ar.KEYEVENTF_EXTENDEDKEY)
        self.assertFalse(flags(0x41, False) & ar.KEYEVENTF_EXTENDEDKEY)
        self.assertTrue(flags(0x41, True) & ar.KEYEVENTF_KEYUP)

    def test_agent_escape_is_not_the_users_escape(self):
        computer = ar.ComputerAutomation()
        ar._SYNTHETIC_ESCAPE_UNTIL[0] = __import__("time").monotonic() + 5
        try:
            self.assertFalse(computer._escape_pressed(0x8000))
        finally:
            ar._SYNTHETIC_ESCAPE_UNTIL[0] = 0.0
        self.assertTrue(computer._escape_pressed(0x8000))
        self.assertFalse(computer._escape_pressed(0x0001))

    def test_escape_cancels_job_and_pending_approval(self):
        async def scenario():
            computer = ar.ComputerAutomation()
            computer._loop = asyncio.get_running_loop()
            approval = asyncio.create_task(computer.request_approval({"action": "click"}))
            await asyncio.sleep(0)
            computer.job = asyncio.create_task(asyncio.sleep(10))
            stop = computer._thread_stop
            computer._cancel_from_escape(stop)
            await asyncio.sleep(0.01)
            return computer, await approval, stop.is_set(), computer.job.cancelled()
        computer, allowed, stopped, cancelled = run(scenario())
        self.assertEqual((allowed, stopped, cancelled, computer._escape_cancelled), (False, True, True, True))

    def test_signature_change(self):
        self.assertEqual(ar._signature_change([1, 2], [1, 2]), 0.0)
        self.assertEqual(ar._signature_change(None, [1]), -1.0)
        self.assertGreater(ar._signature_change([0] * 4, [255] * 4), 100)

class ComputerStandin(ar.ComputerAutomation):
    def __init__(self, signatures):
        super().__init__()
        self.signatures = iter(signatures)
        self.performed = []

    def start_overlay(self):
        return None

    def _capture(self):
        return {"screens": [{"monitor": 1, "left": 0, "top": 0, "width": 100, "height": 100,
                             "real_width": 200, "real_height": 200, "scale": .5,
                             "signature": next(self.signatures), "screenshot": "data:image/jpeg;base64,AAAA"}]}

    def _list_windows(self):
        return []

class ComputerActions(unittest.TestCase):
    def test_actions_map_to_input_helpers(self):
        computer = ar.ComputerAutomation()
        screen = {"screens": [{"monitor": 1, "left": -1920, "top": 0, "width": 960, "height": 540,
                               "real_width": 1920, "real_height": 1080, "scale": .5}]}
        clicks, scrolls, moves = [], [], []
        with patch.object(ar.ComputerAutomation, "_click",
                          side_effect=lambda x, y, d=False, **kw: clicks.append((x, y, kw))), \
             patch.object(ar.ComputerAutomation, "_scroll_at", side_effect=lambda x, y, dy: scrolls.append((x, y, dy))), \
             patch.object(ar.ComputerAutomation, "_move_cursor_dpi", side_effect=lambda x, y: moves.append((x, y))), \
             patch.object(ar.asyncio, "sleep", new=_no_sleep):
            run(computer.perform({"action": "right_click", "x": 100, "y": 50}, screen))
            run(computer.perform({"action": "double_click", "x": 10, "y": 10}, screen))
            run(computer.perform({"action": "scroll", "dy": 600, "x": 480, "y": 270}, screen))
            run(computer.perform({"action": "scroll"}, screen))
            run(computer.perform({"action": "move", "x": 0, "y": 0}, screen))
        self.assertEqual(clicks[0], (-1920 + 200, 100, {"button": "right", "clicks": 1}))
        self.assertEqual(clicks[1][2], {"button": "left", "clicks": 2})
        self.assertEqual(scrolls, [(-1920 + 960, 540, 600), (None, None, 600)])
        self.assertEqual(moves, [(-1920, 0)])

    def test_unchanged_screen_is_reported_and_a_stuck_loop_ends(self):
        stand = ComputerStandin([[5] * 8] * 20)
        prompts = []

        async def completion(model, messages):
            prompts.append(messages[1]["content"][0]["text"])
            return '{"action": "click", "x": 10, "y": 10}'

        async def perform(action, screen):
            return "clicked"
        stand.perform = perform
        run(ar.run_computer_agent(stand, completion, model="m", task="t", ask_each_action=False, max_steps=10))
        self.assertEqual(stand.status, "error")
        self.assertIn("зациклился", stand.error)
        self.assertIn("NO visible change", prompts[1])
        self.assertNotIn("signature", prompts[0])

    def test_changed_screen_is_reported(self):
        stand = ComputerStandin([[0] * 8, [200] * 8, [200] * 8])
        prompts = []

        async def completion(model, messages):
            prompts.append(messages[1]["content"][0]["text"])
            return '{"action": "done", "report": "ok"}' if len(prompts) == 2 else '{"action": "key", "keys": "ctrl+s"}'

        async def perform(action, screen):
            return "pressed"
        stand.perform = perform
        run(ar.run_computer_agent(stand, completion, model="m", task="t", ask_each_action=False, max_steps=5))
        self.assertEqual(stand.status, "done")
        self.assertIn("[screen changed]", prompts[1])

    def test_describe_new_actions(self):
        text = ar._describe_action
        self.assertIn("right-click", text({"action": "right_click", "x": 1, "y": 2}))
        self.assertIn("scroll down 600", text({"action": "scroll", "dy": 600}))
        self.assertIn("select", text({"action": "select", "element": 2, "option": "Beta"}))

if __name__ == "__main__":
    unittest.main()

class LiveBrowser(unittest.TestCase):
    """Real headless Edge/Chromium (MEMUAI_BROWSER_EXECUTABLE or an installed Edge); skipped when none exists."""

    PAGE = """<!doctype html><html><head><meta charset=utf-8><title>T</title></head><body style="height:3000px">
<input id=n aria-label=name value="old"><div id=ce contenteditable=true aria-label=editor>prev</div>
<select id=s aria-label=city><option value=a>Alpha</option><option value=b>Beta</option></select>
<button id=b onpointerdown="this.dataset.md=1" onclick="o.textContent='Hi, '+n.value+(this.dataset.md||'')">Greet</button>
<button id=al onclick="alert('hello'); o2.textContent=confirm('sure?')?'yes':'no'">Dialog</button>
<a href="/two" target=_blank>NewTab</a><button onclick="window.open('/two')">WinOpen</button>
<a href="http://10.1.2.3/">Private</a><p id=o>nobody</p><p id=o2>-</p>
<iframe srcdoc="<button onclick=&quot;this.textContent='done'&quot;>InFrame</button>"></iframe>
</body></html>""".encode()

    def test_real_browser_end_to_end(self):
        try:
            ar._edge_executable()
        except FileNotFoundError:
            self.skipTest("no Edge/Chromium available")
        import http.server
        import threading
        page = self.PAGE

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = page if self.path == "/" else b"<title>Two</title><p>second</p>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                return None

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_address[1]}/"

        async def scenario():
            edge = ar.EdgeAutomation()
            try:
                await edge.navigate(url)
                state = await edge.snapshot()
                self.assertTrue(state["screenshot"].startswith("data:image/jpeg"))
                ids = {item["text"]: item["id"] for item in state["elements"]}
                self.assertEqual(next(i for i in state["elements"] if i["text"] == "InFrame").get("frame"), 1)
                await edge.perform({"action": "type", "element": ids["old"], "text": "Мир 🌍"})
                await edge.perform({"action": "type", "element": ids["prev"], "text": "новый"})
                await edge.perform({"action": "select", "element": ids["Alpha"], "option": "beta"})
                await edge.perform({"action": "click", "element": ids["Greet"]})
                await edge.perform({"action": "click", "element": ids["Dialog"]})
                await edge.perform({"action": "click", "element": ids["InFrame"]})
                values = await edge.evaluate("[o.textContent,o2.textContent,ce.innerText,s.value,"
                                             "document.querySelector('iframe').contentDocument.body.innerText]")
                self.assertEqual(values, ["Hi, Мир 🌍1", "no", "новый", "b", "done"])
                self.assertEqual([d["accepted"] for d in edge.dialogs], [True, False])
                await edge.perform({"action": "click", "element": ids["NewTab"]})
                self.assertTrue((await edge.evaluate("location.href")).endswith("/two"))
                await edge.perform({"action": "back"})
                state = await edge.snapshot(screenshot=False)
                ids = {item["text"]: item["id"] for item in state["elements"]}
                await edge.perform({"action": "click", "element": ids["Private"]})
                self.assertEqual(await edge.evaluate("location.href"), url)
                self.assertTrue(edge.blocked_navigations)
                targets = await edge.command("Target.getTargets", session_id="")
                self.assertEqual([t["type"] for t in targets["targetInfos"] if t["type"] == "page"], ["page"])
                profile = edge.profile
            finally:
                await edge.close_browser()
            self.assertFalse(profile.exists())

        try:
            asyncio.run(scenario())
        finally:
            server.shutdown()

class FakeUser32:
    def __init__(self):
        self.cursor = []

    def MapVirtualKeyW(self, vk, kind):
        return vk + 1000

    def GetCursorPos(self, pointer):
        pointer._obj.x, pointer._obj.y = 5, 6
        return 1

    def SetCursorPos(self, x, y):
        self.cursor.append((x, y))
        return 1

    def GetDoubleClickTime(self):
        return 500

class FakeOsInput(unittest.TestCase):
    def _run(self, func):
        user32 = FakeUser32()
        sent = []
        fake_windll = type("W", (), {"user32": user32})()
        with patch.object(ctypes, "windll", fake_windll, create=True), \
             patch.object(ar, "_send_inputs", side_effect=lambda items: sent.append(list(items))), \
             patch.object(ar.time, "sleep", lambda *_: None):
            func()
        return user32, sent

    def test_key_combo_presses_then_releases_in_reverse_with_extended_flags(self):
        _, sent = self._run(lambda: ar.ComputerAutomation._send_key_combo("ctrl+shift+left"))
        down, up = sent
        self.assertEqual([i.ki.wVk for i in down], [0x11, 0x10, 0x25])
        self.assertEqual([i.ki.wVk for i in up], [0x25, 0x10, 0x11])
        self.assertTrue(down[2].ki.dwFlags & ar.KEYEVENTF_EXTENDEDKEY)
        self.assertTrue(all(i.ki.dwFlags & ar.KEYEVENTF_KEYUP for i in up))
        self.assertEqual(down[0].ki.wScan, 0x11 + 1000)

    def test_agent_esc_opens_the_synthetic_window(self):
        ar._SYNTHETIC_ESCAPE_UNTIL[0] = 0.0
        self._run(lambda: ar.ComputerAutomation._send_key_combo("esc"))
        self.assertGreater(ar._SYNTHETIC_ESCAPE_UNTIL[0], 0)
        ar._SYNTHETIC_ESCAPE_UNTIL[0] = 0.0

    def test_keys_are_released_even_if_pressing_fails(self):
        user32 = FakeUser32()
        sent = []

        def flaky(items):
            sent.append(items)
            if len(sent) == 1:
                raise RuntimeError("blocked")
        with patch.object(ctypes, "windll", type("W", (), {"user32": user32})(), create=True), \
             patch.object(ar, "_send_inputs", side_effect=flaky):
            with self.assertRaises(RuntimeError):
                ar.ComputerAutomation._send_key_combo("ctrl+c")
        self.assertEqual(len(sent), 2)

    def test_right_double_click_and_cursor_restore(self):
        user32, sent = self._run(lambda: ar.ComputerAutomation._click(100, 200, button="right", clicks=2))
        self.assertEqual(len(sent), 2)
        self.assertEqual([i.mi.dwFlags for i in sent[0]], [0x0008, 0x0010])
        self.assertIn((100, 200), user32.cursor)
        self.assertEqual(user32.cursor[-1], (5, 6))

    def test_scroll_down_sends_negative_wheel_over_the_point(self):
        user32, sent = self._run(lambda: ar.ComputerAutomation._scroll_at(50, 60, 900))
        wheel = [item for batch in sent for item in batch]
        self.assertEqual(sum(ctypes.c_int32(i.mi.mouseData).value for i in wheel), -900)
        self.assertEqual(user32.cursor[0], (50, 60))
        self.assertEqual(user32.cursor[-1], (5, 6))

    def test_drag_always_releases_the_button(self):
        user32, sent = self._run(lambda: ar.ComputerAutomation._drag(1, 2, 30, 40))
        flags = [i.mi.dwFlags for batch in sent for i in batch]
        self.assertEqual(flags, [0x0002, 0x0004])
        self.assertIn((30, 40), user32.cursor)
