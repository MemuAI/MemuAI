"""Round 22: the frameless installer window can be dragged.

* ``Bridge.drag_window`` delegates to the native starter installed by ``main.run_window``
  (``native_drag`` → ``winplat.begin_window_drag`` on the GUI thread) and reports False
  when there is none, so the page falls back to ``Bridge.move_window``.
* ``winplat.begin_window_drag`` sends WM_NCLBUTTONDOWN/HTCAPTION with the cursor position
  (checked against a fake ``user32``) and does nothing when the button is already up.
* In headless Chromium (mock bridge): pressing the titlebar, the background and the
  panel's empty surface starts a drag and moves the window by the pointer delta;
  buttons, the language switch, edition cards, option rows, inputs, panel text and
  dialogs never do; a double click starts nothing; clicks still work.
"""
from __future__ import annotations

import asyncio
import ctypes
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from installer import winplat  # noqa: E402
from installer.bridge import Bridge  # noqa: E402

UI = ROOT / "installer" / "ui" / "index.html"
CHROME = [os.environ.get("MEMUAI_CHROMIUM", ""), "/usr/bin/chromium", "/usr/bin/chromium-browser",
          "/workspace/work/chromium/chrome"]


def _chrome() -> str:
    return next((c for c in CHROME if c and os.path.exists(c)), "")


class _Engine:
    cancel_event = None


class BridgeDrag(unittest.TestCase):
    def make(self):
        return Bridge(_Engine(), "install", "ru")

    def test_without_native_starter_page_falls_back(self):
        self.assertFalse(self.make().drag_window())

    def test_native_starter_result_is_returned(self):
        bridge = self.make()
        bridge._begin_drag = lambda: True
        self.assertTrue(bridge.drag_window())
        bridge._begin_drag = lambda: 0
        self.assertFalse(bridge.drag_window())

    def test_native_starter_failure_never_raises(self):
        bridge = self.make()

        def boom():
            raise OSError("no GUI thread")
        bridge._begin_drag = boom
        self.assertFalse(bridge.drag_window())

    def test_move_window(self):
        bridge = self.make()
        self.assertFalse(bridge.move_window(10, 20))       # no window yet
        window = mock.Mock()
        bridge._window = window
        self.assertTrue(bridge.move_window(10.6, "20"))
        window.move.assert_called_once_with(11, 20)
        window.move.side_effect = RuntimeError("closed")
        self.assertFalse(bridge.move_window(1, 2))

    def test_internal_attributes_are_not_exposed_to_the_page(self):
        public = [n for n in vars(self.make()) if not n.startswith("_")]
        self.assertEqual(public, [])

    def test_native_drag_is_off_outside_windows(self):
        from installer import main
        if os.name != "nt":
            self.assertFalse(main.native_drag(mock.Mock()))


class _Fn:
    def __init__(self, result=1, effect=None):
        self.calls, self.result, self.effect = [], result, effect

    def __call__(self, *args):
        self.calls.append(args)
        if self.effect:
            self.effect(*args)
        return self.result


class FakeUser32(types.SimpleNamespace):
    def __init__(self, pressed=True, cursor=(-1500, 300)):
        def put_cursor(ptr):
            ptr._obj.x, ptr._obj.y = cursor
        super().__init__(
            GetSystemMetrics=_Fn(0),
            GetAsyncKeyState=_Fn(-32768 if pressed else 0),
            GetCursorPos=_Fn(1, put_cursor),
            ReleaseCapture=_Fn(1),
            SendMessageW=_Fn(0),
        )


class NativeDrag(unittest.TestCase):
    def run_with(self, user32, hwnd=0x1234):
        fake = types.SimpleNamespace(user32=user32)
        with mock.patch.object(ctypes, "windll", fake, create=True), \
                mock.patch.object(winplat.wintypes, "POINT", winplat.wintypes.POINT):
            return winplat.begin_window_drag(hwnd)

    def test_sends_caption_press_with_cursor_position(self):
        user32 = FakeUser32()
        self.assertTrue(self.run_with(user32))
        self.assertEqual(len(user32.ReleaseCapture.calls), 1)
        (hwnd, msg, wparam, lparam), = user32.SendMessageW.calls
        self.assertEqual((hwnd, msg, wparam), (0x1234, 0x00A1, 2))   # WM_NCLBUTTONDOWN, HTCAPTION
        self.assertEqual(lparam & 0xFFFF, (-1500) & 0xFFFF)          # x on a monitor left of the primary
        self.assertEqual(lparam >> 16, 300)
        self.assertEqual(user32.SendMessageW.restype, ctypes.c_ssize_t)
        self.assertEqual(len(user32.SendMessageW.argtypes), 4)

    def test_button_already_up_does_nothing(self):
        user32 = FakeUser32(pressed=False)
        self.assertFalse(self.run_with(user32))
        self.assertEqual(user32.SendMessageW.calls, [])

    def test_swapped_buttons_check_the_right_physical_button(self):
        user32 = FakeUser32()
        user32.GetSystemMetrics.result = 1
        self.run_with(user32)
        self.assertEqual(user32.GetAsyncKeyState.calls[-1], (0x02,))

    def test_no_window_and_no_win32_never_raise(self):
        self.assertFalse(self.run_with(FakeUser32(), hwnd=0))
        if os.name != "nt":
            self.assertFalse(winplat.begin_window_drag(0x1234))   # ctypes.windll missing → False

    def test_pack_point(self):
        self.assertEqual(winplat._pack_point(1, 2), (2 << 16) | 1)
        self.assertEqual(winplat._pack_point(-1, -1), 0xFFFFFFFF)


def _playwright_ok() -> bool:
    try:
        import playwright.async_api  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return bool(_chrome())


ZONES_JS = """() => {
  const ui = window.__memuUi; const r = {};
  const pick = (name, sel) => { const el = document.querySelector(sel); r[name] = el ? ui.isDragZone(el, null) : 'missing'; };
  pick('titlebar', '.titlebar'); pick('brand', '.brand'); pick('brandName', '.brand-name');
  pick('stage', '#stage'); pick('heroCaption', '.hero-caption'); pick('wordmark', '.wordmark');
  pick('panel', '#panel'); pick('screen', '.screen.active') ; pick('stepper', '#stepper');
  pick('min', '#btn-min'); pick('close', '#btn-close'); pick('lang', '.seg button');
  pick('title', '#welcome-title'); pick('primary', '#btn-start'); pick('express', '#btn-express');
  return r;
}"""


@unittest.skipUnless(_playwright_ok(), "Playwright/Chromium not available")
class PageDragZones(unittest.TestCase):
    def test_drag_zones_and_fallback_move(self):
        asyncio.run(self._run())

    async def _run(self):
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(executable_path=_chrome(), args=["--no-sandbox", "--disable-webgl"])
            page = await browser.new_page(viewport={"width": 960, "height": 620})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.goto(f"{UI.as_uri()}?mock=1&shot=1&lang=ru", wait_until="load")
            for _ in range(60):
                await page.wait_for_timeout(50)
                if await page.evaluate("() => !!window.__memuUi"):
                    break
            self.assertTrue(await page.evaluate("() => !!window.__memuUi"), "ui helpers not exposed")
            await page.wait_for_timeout(500)
            zones = await page.evaluate(ZONES_JS)
            for name in ("titlebar", "brand", "brandName", "stage", "heroCaption", "wordmark", "panel", "screen", "stepper"):
                self.assertIs(zones[name], True, f"{name} should start a drag: {zones}")
            for name in ("min", "close", "lang", "title", "primary", "express"):
                self.assertIs(zones[name], False, f"{name} must not start a drag: {zones}")

            # Press the titlebar (left of the brand) and move: the mock bridge has no native
            # drag, so the page moves the window itself by the pointer delta.
            await page.evaluate("() => { window.__memuDrag = []; window.addEventListener('mousemove', e => {"
                                " window.__off = [e.screenX - e.clientX, e.screenY - e.clientY]; }); }")
            box = await page.evaluate("() => { const r = document.querySelector('.titlebar').getBoundingClientRect();"
                                      " const c = document.querySelector('.chrome').getBoundingClientRect();"
                                      " return {x: (r.left + c.left) / 2 + 40, y: r.top + r.height / 2}; }")
            await page.mouse.move(box["x"], box["y"])
            await page.mouse.down()
            await page.wait_for_timeout(60)
            await page.mouse.move(box["x"] + 30, box["y"] + 12, steps=3)
            await page.wait_for_timeout(120)
            await page.mouse.up()
            calls = await page.evaluate("() => window.__memuDrag")
            self.assertEqual(calls[0], ["drag"])
            moves = [c for c in calls if c[0] == "move"]
            self.assertTrue(moves, calls)
            # window origin = screen pointer − client offset of the press
            off = await page.evaluate("() => window.__off")
            self.assertAlmostEqual(moves[-1][1], off[0] + 30, delta=1)
            self.assertAlmostEqual(moves[-1][2], off[1] + 12, delta=1)
            # after release nothing moves any more
            await page.evaluate("() => { window.__memuDrag = []; }")
            await page.mouse.move(box["x"] + 90, box["y"] + 40, steps=3)
            await page.wait_for_timeout(80)
            self.assertEqual(await page.evaluate("() => window.__memuDrag"), [])

            # A button press does not drag, and the click still works (language switch).
            btn = await page.evaluate("() => { const r = document.querySelector('.seg button[data-lang=\"en\"]').getBoundingClientRect();"
                                      " return {x: r.left + r.width / 2, y: r.top + r.height / 2}; }")
            await page.mouse.click(btn["x"], btn["y"])
            await page.wait_for_timeout(120)
            self.assertEqual(await page.evaluate("() => window.__memuDrag"), [])
            self.assertEqual(await page.evaluate("() => document.documentElement.lang"), "en")

            # Double click on the titlebar: at most the first press drags, never a second one.
            await page.mouse.dblclick(box["x"], box["y"])
            await page.wait_for_timeout(120)
            drags = [c for c in await page.evaluate("() => window.__memuDrag") if c[0] == "drag"]
            self.assertLessEqual(len(drags), 1)
            self.assertEqual(errors, [])
            await browser.close()


if __name__ == "__main__":
    unittest.main()
