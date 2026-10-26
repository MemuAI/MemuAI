"""Round 26: Computer Use end to end without Windows, CDP without a proxy, DFY key names.

The user's report: Computer Use died on «Шаг 1/12: снимаю экран» with
``type object 'Image' has no attribute 'new'`` — every earlier test replaced
``_capture`` with a stand-in, so the real screenshot code never ran.  Here the
real ``_capture``/``_monitors``/``_list_windows``/input code runs against a
fake ``user32`` (``ctypes.windll``) and a fake ``ImageGrab``.
"""
from __future__ import annotations

import asyncio
import base64
import ctypes
import io
import json
import unittest
from ctypes import wintypes
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageGrab

from app import automation_runtime as ar
from tests import round24_dfy_test as r24
from tests.round25_dfy_test import FormBrowser

class _Fn:
    """A user32 function that accepts ``restype``/``argtypes`` like a ctypes one."""

    def __init__(self, func):
        self.func, self.restype, self.argtypes = func, None, None

    def __call__(self, *args):
        return self.func(*args)

class FakeUser32:
    def __init__(self, monitors, windows=()):
        self.monitors = list(monitors)
        self.windows = list(windows)
        self.cursor = (5, 6)
        self.cursor_path = []
        self.inputs = []
        self.escape = 0
        self.foreground = []
        self.SetThreadDpiAwarenessContext = _Fn(lambda value: 17)
        self.GetAsyncKeyState = _Fn(lambda vk: self.escape if vk == 0x1B else 0)

    def EnumDisplayMonitors(self, _dc, _clip, callback, _data):
        for rect in self.monitors:
            callback(1, 0, ctypes.pointer(wintypes.RECT(*rect)), 0)
        return 1

    def EnumWindows(self, callback, _data):
        for index in range(len(self.windows)):
            callback(101 + index, 0)
        return 1

    def _window(self, hwnd):
        return self.windows[hwnd - 101]

    def IsWindowVisible(self, hwnd):
        return 1

    def IsIconic(self, hwnd):
        return 0

    def GetWindowTextLengthW(self, hwnd):
        return len(self._window(hwnd)["title"])

    def GetWindowTextW(self, hwnd, buffer, size):
        buffer.value = self._window(hwnd)["title"][:size - 1]
        return 1

    def GetWindowThreadProcessId(self, hwnd, pid):
        if pid is not None:
            pid._obj.value = 4242
        return 1

    def GetWindowLongW(self, hwnd, index):
        return 0

    def GetWindowRect(self, hwnd, pointer):
        rect = pointer._obj
        rect.left, rect.top, rect.right, rect.bottom = self._window(hwnd)["rect"]
        return 1

    def GetCursorPos(self, pointer):
        pointer._obj.x, pointer._obj.y = self.cursor
        return 1

    def SetCursorPos(self, x, y):
        self.cursor = (x, y)
        self.cursor_path.append((x, y))
        return 1

    def GetDoubleClickTime(self):
        return 500

    def MapVirtualKeyW(self, vk, kind):
        return vk

    def SendInput(self, count, array, size):
        self.inputs.append([(item.type, item.mi.dwFlags if item.type == ar.INPUT_MOUSE else item.ki.wVk)
                            for item in array])
        return count

    def ShowWindow(self, hwnd, command):
        return 1

    def SetForegroundWindow(self, hwnd):
        self.foreground.append(hwnd)
        return 1

def desktop(size, color=(40, 40, 40)):
    image = Image.new("RGB", size, color)
    return image

class FakeWindows:
    """Patches os.name, ctypes.windll/WINFUNCTYPE and ImageGrab for the duration of a ``with``."""

    def __init__(self, user32, screen):
        self.user32 = user32
        self.screen = screen
        self.patches = [
            patch.object(ar.os, "name", "nt"),
            patch.object(ctypes, "windll", type("W", (), {"user32": user32, "kernel32": None})(), create=True),
            patch.object(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE, create=True),
            patch.object(ImageGrab, "grab", lambda **_kw: self.screen),

            patch.object(ar.ComputerAutomation, "_glow_worker", lambda self, stop=None: None),
            patch.object(ar.time, "sleep", lambda *_a: None),
        ]

    def __enter__(self):
        for item in self.patches:
            item.start()
        return self

    def __exit__(self, *_exc):
        for item in reversed(self.patches):
            item.stop()

def decode(data_url):
    return Image.open(io.BytesIO(base64.b64decode(data_url.split(",", 1)[1])))

class Script:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    async def __call__(self, model, messages):
        self.prompts.append(messages)
        reply = self.replies.pop(0) if self.replies else '{"action": "done", "report": "end"}'
        return reply(self) if callable(reply) else reply

class RulerAndCapture(unittest.TestCase):
    def test_ruler_draws_on_a_real_image_in_any_mode(self):
        for mode in ("RGB", "RGBA", "L", "P"):
            image = Image.new(mode, (640, 360))
            ruled = ar.ComputerAutomation._draw_ruler(image)
            self.assertEqual((ruled.mode, ruled.size), ("RGB", (640, 360)))
            self.assertNotEqual(ruled.getpixel((200, 100)), ruled.getpixel((300, 100)), mode)

    def test_capture_two_monitors_left_of_primary(self):
        user32 = FakeUser32([(0, 0, 2560, 1440), (-1920, 200, 0, 1280)])
        screen = desktop((4480, 1440))
        ImageDraw.Draw(screen).rectangle([0, 200, 1919, 1279], fill=(200, 10, 10))
        with FakeWindows(user32, screen):
            shot = ar.ComputerAutomation._capture()
        first, second = shot["screens"]
        self.assertEqual((first["left"], first["width"], first["height"], first["real_width"]), (0, 1400, 788, 2560))
        self.assertEqual((second["left"], second["top"], second["width"], second["real_width"]), (-1920, 200, 1400, 1920))
        image = decode(second["screenshot"])
        self.assertEqual(image.size, (1400, 788))
        self.assertGreater(image.getpixel((700, 400))[0], 150, "monitor 2 is cropped from its own area")
        self.assertEqual(len(first["signature"]), 32 * 18)

        self.assertEqual(ar.resolve_click_point({"monitor": 2, "x": 1399, "y": 0}, shot["screens"]), (-1, 200))

class ComputerLoop(unittest.TestCase):
    def run_agent(self, user32, screen, model, **kwargs):
        runtime = ar.ComputerAutomation()

        async def scenario():
            runtime.job = asyncio.create_task(ar.run_computer_agent(
                runtime, model, model="vision", task="Нажми Сохранить", **kwargs))
            approvals = []
            while not runtime.job.done():
                await asyncio.sleep(0.01)
                if runtime.pending_action and kwargs.get("ask_each_action"):
                    approvals.append(dict(runtime.pending_action))
                    runtime.approve(True)
            return approvals

        with FakeWindows(user32, screen), patch.object(ar.asyncio, "sleep", new=_short_sleep):
            approvals = asyncio.run(scenario())
        return runtime, approvals

    def test_real_capture_click_type_key_window_scroll_until_done(self):
        user32 = FakeUser32([(0, 0, 2560, 1440)], [{"title": "Telegram", "rect": [100, 100, 900, 700]}])
        model = Script([
            'Clicking: {"action":"click","monitor":1,"x":700,"y":394,"reason":"Save"}',
            '```json\n{"action":"type","text":"Привет 👋","submit":true}\n```',
            '{"action":"key","keys":"ctrl+s"}',
            '{"action":"focus_window","title":"telegram"}',
            '{"action":"scroll","monitor":1,"x":10,"y":10,"dy":-300}',
            '{"action":"done","report":"Сохранено"}'])
        runtime, _ = self.run_agent(user32, desktop((2560, 1440)), model, ask_each_action=False, lang="en")
        self.assertEqual((runtime.status, runtime.error, runtime.report), ("done", "", "Сохранено"))
        logs = [row["message"] for row in runtime.logs]
        self.assertIn("click: clicked (1280, 720) — Save", logs)
        self.assertIn("focus_window: focused window 'telegram'", logs)
        self.assertEqual(user32.foreground, [101])
        self.assertEqual(user32.inputs[0], [(ar.INPUT_MOUSE, 0x0002), (ar.INPUT_MOUSE, 0x0004)])
        self.assertEqual(user32.cursor, (5, 6), "the user's cursor is put back")
        user_turn = model.prompts[0][1]["content"]
        self.assertIn('"title": "Telegram"', user_turn[0]["text"])
        self.assertEqual(decode(user_turn[2]["image_url"]["url"]).size, (1400, 788))

    def test_coordinate_pair_is_used_and_missing_coordinates_do_not_click_the_corner(self):
        user32 = FakeUser32([(0, 0, 1280, 720)])
        model = Script(['{"action":"click","coordinate":[640,360]}',
                        '{"action":"click","reason":"no coordinates"}',
                        '{"action":"done","report":"ok"}'])
        runtime, _ = self.run_agent(user32, desktop((1280, 720)), model, ask_each_action=False)
        self.assertEqual(runtime.status, "done")
        self.assertIn((640, 360), user32.cursor_path)
        self.assertNotIn((0, 0), user32.cursor_path)
        self.assertEqual(len(user32.inputs), 1, "only the click with coordinates happened")
        self.assertIn("FAILED (Не указаны координаты", json.dumps(model.prompts[2], ensure_ascii=False))

    def test_approval_card_gets_a_readable_description(self):
        user32 = FakeUser32([(0, 0, 1280, 720)])
        model = Script(['{"action":"double_click","monitor":1,"x":100,"y":50}', '{"action":"done","report":"ok"}'])
        runtime, approvals = self.run_agent(user32, desktop((1280, 720)), model, ask_each_action=True, lang="en")
        self.assertEqual(runtime.status, "done")
        self.assertEqual(approvals[0]["description"], "double-click at (100, 50) on monitor 1")

    def test_escape_mid_run_cancels(self):
        user32 = FakeUser32([(0, 0, 1280, 720)])

        def press_escape(script):
            user32.escape = 0x8000
            return '{"action":"wait","seconds":2}'
        model = Script([press_escape] + ['{"action":"wait","seconds":2}'] * 20)
        with patch.object(ar, "_SYNTHETIC_ESCAPE_UNTIL", [0.0]):
            runtime, _ = self.run_agent(user32, desktop((1280, 720)), model, ask_each_action=False)
        self.assertEqual((runtime.status, runtime.error), ("cancelled", "Пользователь нажал Escape"))

    def test_step_limit_ends_the_run(self):
        user32 = FakeUser32([(0, 0, 1280, 720)])
        model = Script(['{"action":"wait","seconds":1}'] * 5)
        runtime, _ = self.run_agent(user32, desktop((1280, 720)), model, ask_each_action=False, max_steps=3)
        self.assertEqual(runtime.status, "done")
        self.assertTrue(runtime.report.startswith("Достигнут лимит шагов"))
        self.assertEqual(len(model.prompts), 3)

    def test_model_failure_is_an_error_with_its_message(self):
        user32 = FakeUser32([(0, 0, 1280, 720)])

        def broken(script):
            raise RuntimeError("Proxy: 502")
        runtime, _ = self.run_agent(user32, desktop((1280, 720)), Script([broken]), ask_each_action=False)
        self.assertEqual((runtime.status, runtime.error), ("error", "Proxy: 502"))
        self.assertIn("Proxy: 502", [row["message"] for row in runtime.logs])

_real_sleep = asyncio.sleep

async def _short_sleep(delay=0, *args, **kwargs):
    await _real_sleep(min(float(delay or 0), 0.01))

class CoordinateParsing(unittest.TestCase):
    SCREENS = [{"monitor": 1, "left": 0, "top": 0, "width": 700, "height": 394,
                "real_width": 1400, "real_height": 788, "scale": .5}]

    def test_forms(self):
        point = ar.resolve_click_point
        self.assertEqual(point({"x": "10", "y": 20.4}, self.SCREENS), (20, 40))
        self.assertEqual(point({"coordinate": [10, 20]}, self.SCREENS), (20, 40))
        self.assertEqual(point({"point": {"x": 10, "y": 20}}, self.SCREENS), (20, 40))
        with self.assertRaises(ValueError):
            point({"x": 10}, self.SCREENS)
        with self.assertRaises(ValueError):
            point({"monitor": "primary", "x": 1, "y": 1}, self.SCREENS)

    def test_drag_without_end_point_is_refused(self):
        computer = ar.ComputerAutomation()
        with self.assertRaises(ValueError):
            asyncio.run(computer.perform({"action": "drag", "x1": 1, "y1": 1}, {"screens": self.SCREENS}))

class CdpWithoutProxy(unittest.TestCase):
    def test_loopback_socket_never_uses_the_system_proxy(self):
        import websockets
        seen = {}

        def connect(url, *, proxy=True, max_size=None, open_timeout=None, ping_interval=None):
            seen.update(url=url, proxy=proxy, max_size=max_size)

            async def done():
                return "socket"
            return done()
        with patch.object(websockets, "connect", connect):
            self.assertEqual(asyncio.run(ar._connect_cdp("ws://127.0.0.1:9/devtools", max_size=5)), "socket")
        self.assertEqual(seen, {"url": "ws://127.0.0.1:9/devtools", "proxy": None, "max_size": 5})

    def test_both_browsers_use_it(self):
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent / "app"
        for name in ("automation_runtime.py", "dfy_browser.py"):
            source = (root / name).read_text(encoding="utf-8")
            self.assertNotIn("websockets.connect(", source.replace("await websockets.connect(url, **options)", ""),
                             name)

class DfyNavigateError(unittest.IsolatedAsyncioTestCase):
    async def test_failed_navigation_is_an_error_not_opened(self):
        from app import dfy_browser
        browser = dfy_browser.DFYBrowser(profile=None)
        answers = {"Page.navigate": {"errorText": "net::ERR_NAME_NOT_RESOLVED"}}

        async def command(method, params=None, timeout=30):
            return answers.get(method, {})

        async def nothing(*_a, **_kw):
            return None
        browser.command, browser.start, browser.wait_ready = command, nothing, nothing
        with patch.object(ar, "_safe_navigation_url", lambda url: url):
            with self.assertRaisesRegex(RuntimeError, "ERR_NAME_NOT_RESOLVED"):
                await browser.navigate("https://nowhere.example/")
            answers["Page.navigate"] = {"errorText": "net::ERR_ABORTED"}
            self.assertEqual(await browser.navigate("https://example.com/"), "https://example.com/")

class DfyKeyNames(r24.DFYBase):
    def setUp(self):
        super().setUp()
        self.browser = FormBrowser()
        self.pressed = []

        async def key(name):
            self.pressed.append(name)
            return f"pressed {name}"
        self.browser.key = key

    async def test_lower_case_enter_on_a_payment_form_asks_first(self):
        self.browser.submit_label = "Оплатить 120 €"
        model = r24.ScriptedModel([{"action": "key", "key": "enter"}, {"action": "done", "result": "ok"}])
        mgr = self.manager(model)
        task = await mgr.create("Купи")
        await self.wait_status(mgr, task, ("waiting_user",))
        self.assertIn("Оплатить", task.question["prompt"])
        await mgr.answer(task.id, {"approve": True})
        await self.wait_status(mgr, task, ("done",))
        self.assertEqual(self.pressed, ["Enter"])

    async def test_aliases(self):
        from app.dfy import canonical_key
        self.assertEqual([canonical_key(k) for k in ("esc", "page down", "down", "TAB", None)],
                         ["Escape", "PageDown", "ArrowDown", "Tab", "Enter"])

if __name__ == "__main__":
    unittest.main()
