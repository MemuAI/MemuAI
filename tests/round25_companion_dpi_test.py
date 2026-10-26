"""Round 25 (a4-companion-3) — the companion's global cursor feed on monitors with different scales.

The stage used to place the physical cursor with ``point / devicePixelRatio − window.screenX``. ``screenX`` is in
screen DIPs, which are not physical px / dpr when monitors have different scales (a 2560 px primary at 150 % and
the companion on a 100 % secondary put the gaze ~850 px off). Now the host measures the cursor from the client
area of the window that shows the stage (``ClientToScreen`` under per-monitor-v2) and the stage only divides by
its dpr. Win32 is mocked: these run on any OS.
"""
from __future__ import annotations

import ctypes
import re
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from app import companion_avatar_api as api
from app import companion_window as cw

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "app" / "pgstatic" / "companion" / "stage.js"

class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

def fake_win32(cursor=(2700, 400), origins=None, dpi_calls=None):
    """A ``_Win32`` without user32: ``origins`` maps HWND → client origin (physical px)."""
    origins = dict(origins or {})
    win = cw._Win32.__new__(cw._Win32)
    win.ctypes = ctypes
    win.POINT = _Point
    win.IsWindow = lambda hwnd: hwnd in origins

    def client_to_screen(hwnd, ref):
        point = ref._obj
        if hwnd not in origins:
            return 0
        point.x += origins[hwnd][0]
        point.y += origins[hwnd][1]
        return 1

    def set_context(value):
        if dpi_calls is not None:
            dpi_calls.append(value)
        return 0x11 if value == cw.DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 else 0

    win.ClientToScreen = client_to_screen
    win.SetThreadDpiAwarenessContext = set_context
    win.physical_cursor = lambda: cursor
    return win

class ClientOriginTests(unittest.TestCase):
    def test_origin_is_read_per_monitor_v2_and_the_thread_context_is_restored(self) -> None:
        calls = []
        win = fake_win32(origins={42: (2600, 300)}, dpi_calls=calls)
        self.assertEqual(win.client_origin(42), (2600, 300))
        self.assertEqual(calls, [cw.DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2, 0x11])

    def test_gone_or_unknown_window_has_no_origin(self) -> None:
        win = fake_win32(origins={42: (0, 0)})
        self.assertIsNone(win.client_origin(0))
        self.assertIsNone(win.client_origin(7))
        win.ClientToScreen = None
        self.assertIsNone(win.client_origin(42))

class CursorReaderTests(unittest.TestCase):
    def reader(self, win, hwnd_of):
        with mock.patch.object(cw, "_CURSOR", win), mock.patch.object(cw, "_CURSOR_FAILED", False), \
                mock.patch.object(cw.sys, "platform", "win32"):
            return cw.cursor_reader(hwnd_of)

    def test_mixed_dpi_point_is_relative_to_the_client_area(self) -> None:

        win = fake_win32(cursor=(2700, 400), origins={42: (2600, 300)})
        read = self.reader(win, lambda: 42)
        self.assertEqual(read(), (100, 100, True))

    def test_window_lookup_happens_on_every_read(self) -> None:
        hwnd = {"value": 42}
        win = fake_win32(cursor=(2700, 400), origins={42: (2600, 300), 43: (0, 0)})
        read = self.reader(win, lambda: hwnd["value"])
        self.assertEqual(read(), (100, 100, True))
        hwnd["value"] = 43
        self.assertEqual(read(), (2700, 400, True))

    def test_unknown_window_falls_back_to_screen_coordinates(self) -> None:
        win = fake_win32(cursor=(2700, 400), origins={})
        self.assertEqual(self.reader(win, lambda: 0)(), (2700, 400, False))
        self.assertEqual(self.reader(win, None)(), (2700, 400, False))
        self.assertEqual(self.reader(win, lambda: 99)(), (2700, 400, False), "a gone HWND")

        def broken():
            raise RuntimeError("manager gone")
        self.assertEqual(self.reader(win, broken)(), (2700, 400, False))

    def test_no_cursor_is_skipped(self) -> None:
        win = fake_win32(cursor=None, origins={42: (0, 0)})
        self.assertIsNone(self.reader(win, lambda: 42)())

    def test_relative_cursor(self) -> None:
        self.assertEqual(cw.relative_cursor((5, 6), (10, 20)), (-5, -14, True))
        self.assertEqual(cw.relative_cursor((5, 6), None), (5, 6, False))
        self.assertIsNone(cw.relative_cursor(None, (0, 0)))

class HostWindowTests(unittest.TestCase):
    def test_each_surface_maps_to_its_window(self) -> None:
        state = types.SimpleNamespace(companion_window=types.SimpleNamespace(hwnd=11),
                                      quick_ask=types.SimpleNamespace(hwnd=22), native_hwnd=33)
        self.assertEqual(api.host_hwnd(state, "desktop"), 11)
        self.assertEqual(api.host_hwnd(state, "quick"), 22)
        self.assertEqual(api.host_hwnd(state, "main"), 33)
        self.assertEqual(api.host_hwnd(state, ""), 0)
        self.assertEqual(api.host_hwnd(types.SimpleNamespace(), "quick"), 0, "Quick Access not running")
        self.assertEqual(api.host_hwnd(types.SimpleNamespace(companion_window=None), "desktop"), 0)

    def test_sse_marks_relative_points(self) -> None:
        import asyncio
        samples = iter([(1, 2, True), (3, 4, False), (5, 6)])
        state = {"n": 0}

        async def gone():
            state["n"] += 1
            return state["n"] > 3

        async def sleep(_dt):
            return None

        async def collect():
            return [line async for line in api.cursor_events(lambda: next(samples), hz=60, is_disconnected=gone,
                                                              sleep=sleep, clock=lambda: 0.0)]
        lines = asyncio.run(collect())
        self.assertEqual([l for l in lines if l.startswith("data:")],
                         ["data: 1,2,c\n\n", "data: 3,4\n\n", "data: 5,6\n\n"])

    def test_stream_route_passes_the_host_window(self) -> None:
        from starlette.testclient import TestClient
        from app.playground_api import create_app
        seen = []

        def reader(hwnd_of=None):
            seen.append(hwnd_of() if hwnd_of else None)
            return None
        app = create_app()
        app.state.native_hwnd = 77
        client = TestClient(app, base_url="http://127.0.0.1")
        with mock.patch.object(cw, "cursor_reader", reader):
            self.assertEqual(client.get("/api/companion/cursor/stream?host=main").status_code, 404)
            client.get("/api/companion/cursor?host=bogus")
        self.assertEqual(seen, [77, None])

class StageSourceTests(unittest.TestCase):
    def test_stage_sends_its_host_and_uses_client_relative_points(self) -> None:
        source = STAGE.read_text(encoding="utf-8")
        self.assertIn('"&host=" + cursorHost()', source)
        self.assertRegex(source, r'if \(SURFACE === "desktop"\) return "desktop";')

        branch = re.search(r'if \(parts\[2\] === "c"\) \{(.*?)\} else \{', source, re.S)
        self.assertIsNotNone(branch)
        self.assertNotIn("screenX", branch.group(1))
        self.assertIn("frameOffset()", branch.group(1))

if __name__ == "__main__":
    unittest.main()
