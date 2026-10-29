"""Round 24 (quick-window): Quick Ask closes only by Escape/explicit ways, global Escape, the companion never
gets lost while dragged, the bubble is never clipped, DFY in the window.

    cd /workspace/work/memu && PYTHONUTF8=1 python3 -m unittest tests.round24_quick_window_test
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUICK = ROOT / "app" / "pgstatic" / "quick"

def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")

class GlobalEscape(unittest.TestCase):
    def test_decision(self) -> None:
        from app.win_hotkey import escape_should_close, VK_ESCAPE, WM_KEYDOWN, WM_SYSKEYDOWN, LLKHF_INJECTED
        base = dict(visible=True, foreground=500, own_windows=(100,), pinned=False)
        self.assertTrue(escape_should_close(VK_ESCAPE, WM_KEYDOWN, 0, **base), "Escape in another program closes the popup")
        self.assertTrue(escape_should_close(VK_ESCAPE, WM_SYSKEYDOWN, 0, **base))
        self.assertFalse(escape_should_close(0x41, WM_KEYDOWN, 0, **base), "other keys never")
        self.assertFalse(escape_should_close(VK_ESCAPE, 0x0101, 0, **base), "key-up never")
        self.assertFalse(escape_should_close(VK_ESCAPE, WM_KEYDOWN, LLKHF_INJECTED, **base), "our own injected keys never")
        self.assertFalse(escape_should_close(VK_ESCAPE, WM_KEYDOWN, 0, **dict(base, visible=False)), "only while visible")
        self.assertFalse(escape_should_close(VK_ESCAPE, WM_KEYDOWN, 0, **dict(base, pinned=True)), "a pinned popup ignores outside Escape")
        self.assertFalse(escape_should_close(VK_ESCAPE, WM_KEYDOWN, 0, **dict(base, foreground=100)),
                         "inside the popup the page handles Escape level by level")

    def test_watcher_is_inert_off_windows(self) -> None:
        from app.win_hotkey import EscapeWatcher
        watcher = EscapeWatcher(lambda: None, lambda *a: True)
        if sys.platform != "win32":
            self.assertFalse(watcher.arm())
        watcher.disarm()
        self.assertFalse(watcher.armed)

    def test_hook_never_swallows_and_is_tied_to_visibility(self) -> None:
        src = read(ROOT / "app" / "win_hotkey.py")
        self.assertIn("return user32.CallNextHookEx(hook, code, wparam, lparam)", src)
        self.assertNotIn("RegisterHotKey(None, ESC", src)
        qa = read(ROOT / "app" / "quick_ask.py")
        self.assertIn("self._arm_escape()", qa)
        self.assertIn("self._disarm_escape()", qa)

class NoHideOnBlur(unittest.TestCase):
    def test_manager_blur_never_hides(self) -> None:
        from app.quick_ask import QuickAskManager

        class Fake:
            pinned = False
            _busy_until = 0
            shown_at = 0

            def hide(self, *a, **k):
                raise AssertionError("blur must not hide")
        self.assertFalse(QuickAskManager.blur(Fake()))

    def test_default_and_page(self) -> None:
        from app import quick_ask_core as core
        self.assertFalse(core.PREF_DEFAULTS["quick_ask_hide_on_blur"])
        js = read(QUICK / "quick.js")
        self.assertNotIn('windowAction("blur")', js, "the page no longer asks to hide on focus loss")

        block = js[js.index('if (e.key === "Escape") {', js.index("function bind()")):]
        block = block[:block.index("closeWindow();")]
        self.assertIn('emit("escape"', block)
        self.assertIn("if (S.busy) { stop(); return; }", block)

class CompanionNeverLost(unittest.TestCase):
    def test_contain_rect(self) -> None:
        from app import companion_window as cw
        work = (0, 0, 1920, 1040)
        self.assertEqual(cw.contain_rect((5000, -300, 300, 440), work), (1620, 0, 300, 440))
        self.assertEqual(cw.contain_rect((-900, 900, 300, 440), work), (0, 600, 300, 440))
        self.assertEqual(cw.contain_rect((10, 10, 300, 440), (0, 0, 200, 300)), (0, 0, 200, 300), "shrinks on a tiny area")
        self.assertEqual(cw.contain_rect((2000, 100, 300, 440), (1920, 0, 3840, 1040)), (2000, 100, 300, 440))

    def test_hit_region_contains_the_bubble(self) -> None:
        from app import companion_window as cw
        model = {"x": 120, "y": 120, "w": 150, "h": 300}
        without = cw.hit_region(model, 1, (300, 440))
        with_bubble = cw.hit_region(model, 1, (300, 440), {"x": 8, "y": 8, "w": 284, "h": 70})
        self.assertGreater(without[0], 8, "before: the region began at the model — the bubble was cut on the left")
        self.assertLessEqual(with_bubble[0], 8, "the bubble's left edge is inside the region")
        self.assertGreaterEqual(with_bubble[2], 292)
        self.assertEqual(cw.hit_region(model, 1, (300, 440), {"x": "bad"}), without, "a bad bubble box is ignored")

    def _manager(self, cursor_path, quick_rect=None):
        from app import companion_window as cw
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        m = cw.CompanionWindowManager("http://x", state_root=lambda: tmp)

        class Api:
            def __init__(self):
                self.rect = [100, 100, 300, 440]
                self.path = list(cursor_path)
                self.moves = []

            def window_rect(self, hwnd):
                return tuple(self.rect)

            def cursor(self):
                return self.path[0] if len(self.path) == 1 else self.path.pop(0)

            def button_down(self):
                return len(self.path) > 1

            def work_area_at(self, x, y):
                return ((0, 0, 1920, 1040), 96)

            def SetWindowPos(self, hwnd, after, x, y, w, h, flags):
                self.rect[0], self.rect[1] = x, y
                if w and h:
                    self.rect[2], self.rect[3] = w, h
                self.moves.append((x, y))
                return True

            def quick_rect(self):
                return quick_rect

            def IsWindow(self, hwnd):
                return True

            def ShowWindow(self, hwnd, cmd):
                return True

            def SetWindowRgn(self, *a):
                return True
        m.api = Api()
        m.hwnd = 1
        m.visible = True
        return m

    def test_drag_keeps_the_window_on_screen(self) -> None:
        from app import companion_window as cw
        m = self._manager([(150, 150), (400, 400), (5000, 3000), (5000, 3000)])
        result = m.follow((50, 50))
        self.assertTrue(result["moved"])
        self.assertFalse(result["docked"])
        x, y, w, h = m.api.rect
        self.assertEqual(cw.contain_rect((x, y, w, h), (0, 0, 1920, 1040)), (x, y, w, h), "never off-screen")
        for mx, my in m.api.moves:
            self.assertTrue(0 <= mx <= 1920 - 300 and 0 <= my <= 1040 - 440, "every drag step stays visible")

    def test_click_is_not_a_drag_and_never_docks(self) -> None:
        m = self._manager([(150, 150), (151, 150), (151, 150)], quick_rect=(0, 0, 1920, 1040))
        result = m.follow((50, 50))
        self.assertFalse(result["moved"], "a click → the stage opens Quick Ask")
        self.assertFalse(result["docked"], "a click over Quick Ask does not dock")

    def test_pinned_companion_is_restored_after_restart(self) -> None:
        from app import companion_window as cw
        on = {"companion_quick_enabled": True}
        self.assertTrue(cw.should_restore_pinned({"pinned": True, "place": "desktop"}, on))
        self.assertFalse(cw.should_restore_pinned({"pinned": False, "place": "desktop"}, on))
        self.assertFalse(cw.should_restore_pinned({"pinned": True, "place": "desktop"}, {}))
        self.assertFalse(cw.should_restore_pinned({"pinned": True, "place": "desktop"}, dict(on, quick_ask_enabled=False)))
        self.assertTrue(cw.should_restore_pinned({"pinned": True}, dict(on, companion_quick_mode="desktop")))
        self.assertIn("restore_pinned", read(ROOT / "app" / "quick_ask.py"))

    def test_real_drag_onto_quick_docks(self) -> None:
        m = self._manager([(150, 150), (600, 500), (600, 500)], quick_rect=(500, 400, 300, 300))
        self.assertTrue(m.follow((50, 50))["docked"])

class Frontend(unittest.TestCase):
    def test_wiring(self) -> None:
        html = read(QUICK / "index.html")
        self.assertIn('/quick/quick-dfy.js', html)
        self.assertIn('/quick/quick-dfy.css', html)
        self.assertLess(html.index("/quick/quick.js"), html.index("/quick/quick-dfy.js"), "MemuQuick exists before the DFY module")
        js = read(QUICK / "quick.js")
        self.assertIn("window.QuickDFY.run()", js)
        self.assertIn('e.shiftKey && e.key === "Enter"', js, "open in MemuAI moved to Ctrl+Shift+Enter")
        dfy = read(QUICK / "quick-dfy.js")
        for route in ("/api/dfy/status", "/api/dfy/tasks", "/events", "/answer", "/cancel"):
            self.assertIn(route, dfy)
        self.assertIn('source: "quick"', dfy)
        self.assertIn('type="${pw ? "password" : "text"}"', dfy, "secret answers are password fields")
        stage = read(ROOT / "app" / "pgstatic" / "companion" / "stage.js")
        self.assertIn("bubble: bubbleBox()", stage)
        self.assertIn('action: "show"', stage, "a click on the desktop companion opens Quick Ask")
        css = read(QUICK / "plugins" / "companion.css")
        self.assertNotIn("radial-gradient", css, "no translucent accent halo (the «blue circle»)")

    def test_action_detector(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node missing")
        src = read(QUICK / "quick-dfy.js")
        start = src.index("const ACTION_RE")
        end = src.index("async function json(")
        script = src[start:end] + """
const yes = ["забронируй отель в Казани", "Закажи пиццу домой", "купи билет до Москвы на пятницу", "book a table for two at 7", "please order a taxi"];
const no = ["как забронировать отель?", "что такое DFY", "переведи текст", "закажи?", "hi"];
const bad = yes.filter((s) => !isActionLike(s)).concat(no.filter((s) => isActionLike(s)));
if (bad.length) { console.log(JSON.stringify(bad)); process.exit(1); }
"""
        result = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_node_check(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node missing")
        for path in (QUICK / "quick.js", QUICK / "quick-dfy.js", QUICK / "plugins" / "companion.js",
                     ROOT / "app" / "pgstatic" / "companion" / "stage.js"):
            result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, f"{path.name}: {result.stderr}")

    def test_headless_flow(self) -> None:
        """The Playwright checks (tests/round24_quick_window_shots.py) when Chromium is available."""
        from tests.round25_chat_test import _chromium
        chrome = _chromium()
        try:
            import playwright  # noqa: F401
        except Exception:
            self.skipTest("playwright missing")
        if not os.path.exists(chrome):
            self.skipTest("chromium missing")
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out, True)
        env = dict(os.environ, NO_PROXY="*", no_proxy="*", PYTHONUTF8="1")
        result = subprocess.run([sys.executable, str(ROOT / "tests" / "round24_quick_window_shots.py"), out],
                                capture_output=True, text=True, timeout=150, env=env, cwd=str(ROOT))
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])

class ReviewFixes(unittest.TestCase):
    """Independent review of Round 24 (quick access)."""

    def test_quick_computer_use_sends_source_quick(self) -> None:
        js = (ROOT / "app" / "pgstatic" / "quick" / "plugins" / "commands.js").read_text(encoding="utf-8")
        call = js[js.index('"/api/work/computer/run"'):][:300]
        self.assertIn('source: "quick"', call)

    def test_computer_use_does_not_require_a_work_project(self) -> None:
        src = (ROOT / "app" / "assistant_r24.py").read_text(encoding="utf-8")
        self.assertNotIn('"no_project")', src)

    def test_escape_watcher_lifecycle_off_windows(self) -> None:
        sys.path.insert(0, str(ROOT))
        from app import win_hotkey
        w = win_hotkey.EscapeWatcher(lambda: None, lambda *a: True)
        w.disarm()
        if sys.platform != "win32":
            self.assertFalse(w.arm())
        w.disarm()
        self.assertFalse(w.armed)
        src = (ROOT / "app" / "win_hotkey.py").read_text(encoding="utf-8")
        hook = src[src.index("class EscapeWatcher"):]

        self.assertIn("ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t)", hook)
        self.assertIn("CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t]", hook)
        self.assertIn("self._callback = callback", hook)
        self.assertLess(hook.index("PeekMessageW(ctypes.byref(msg)"), hook.index("SetWindowsHookExW(WH_KEYBOARD_LL"))
        self.assertIn("UnhookWindowsHookEx(hook)", hook)
        self.assertIn("_memu_ready", hook)

    def test_escape_decision(self) -> None:
        sys.path.insert(0, str(ROOT))
        from app.win_hotkey import escape_should_close as d
        self.assertTrue(d(0x1B, 0x100, 0, visible=True, foreground=5, own_windows=(7,)))
        self.assertFalse(d(0x1B, 0x100, 0, visible=True, foreground=7, own_windows=(7,)))
        self.assertFalse(d(0x1B, 0x100, 0x10, visible=True, foreground=5, own_windows=(7,)))
        self.assertFalse(d(0x1B, 0x101, 0, visible=True, foreground=5, own_windows=(7,)))
        self.assertFalse(d(0x1B, 0x100, 0, visible=False, foreground=5, own_windows=(7,)))

if __name__ == "__main__":
    unittest.main()
