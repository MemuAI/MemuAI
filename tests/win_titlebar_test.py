"""Round 20: custom title bar — pure logic and the subclass controller.

Windows is not available in CI, so the Win32 boundary is replaced by a fake
that records every call; the controller's decisions (NCCALCSIZE math, hit
testing, drag/system-menu messages, fallbacks) are verified against it.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import win_titlebar as tb

ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------ fakes
class FakeRect:
    def __init__(self, left, top, right, bottom):
        self.left, self.top, self.right, self.bottom = left, top, right, bottom


class FakeApi:
    """Records Win32 calls; simulates DefWindowProc for NCCALCSIZE."""

    ORIGINAL = 0x1111

    def __init__(self):
        self.calls = []
        self.procs = {}           # hwnd -> current wndproc address
        self.zoomed = False
        self.iconic = False
        self.style_value = tb.WS_CAPTION | tb.WS_THICKFRAME
        self.button_down = True
        self.cursor = (500, 300)
        self.rect = (100, 50, 1100, 850)
        self.frame = 8            # SM_CYFRAME 4 + padded 4 at 96 DPI ~ 8
        self.edges = ()
        self.menu_result = tb.SC_MAXIMIZE
        self.nc_rects = {}
        self.fail_set = False
        self.client_offset = 0    # how far the client starts below the window top
        self.default_hit = tb.HTCLIENT
        self.in_client = True
        self.foreground = True
        self.raise_in = None

    # plumbing
    def make_wndproc(self, fn):
        self.fn = fn
        return ("proc", fn)

    def proc_address(self, proc):
        return 0xBEEF

    def get_wndproc(self, hwnd):
        return self.procs.get(hwnd, self.ORIGINAL)

    def set_wndproc(self, hwnd, address):
        self.calls.append(("set_wndproc", hwnd, address))
        if self.fail_set:
            return 0
        previous = self.procs.get(hwnd, self.ORIGINAL)
        self.procs[hwnd] = address
        return previous

    def call_proc(self, original, hwnd, msg, wparam, lparam):
        self.calls.append(("call_proc", original, msg))
        if msg == tb.WM_NCCALCSIZE and wparam:
            rect = self.nc_rects[lparam]
            # DefWindowProc: frame 8 on the sides/bottom, caption 31 + frame on top
            rect.left += 8; rect.right -= 8; rect.bottom -= 8; rect.top += 31 + 8
            return 0
        if msg == tb.WM_NCHITTEST:
            return self.default_hit
        return 0

    def style(self, hwnd):
        return self.style_value

    def is_window(self, hwnd):
        return bool(hwnd)

    def is_zoomed(self, hwnd):
        return self.zoomed

    def is_iconic(self, hwnd):
        return self.iconic

    def window_rect(self, hwnd):
        return self.rect

    def client_origin(self, hwnd):
        inset = self.frame if self.zoomed else 0
        return self.rect[0] + 8, self.rect[1] + inset + self.client_offset

    def point_in_client(self, hwnd, x, y):
        return self.in_client

    def nccalc_rect(self, lparam):
        return self.nc_rects[lparam]

    def dpi(self, hwnd):
        return 96

    def frame_thickness(self, hwnd):
        return self.frame

    def autohide_edges(self, hwnd):
        return self.edges

    def primary_button_down(self):
        return self.button_down

    def cursor_pos(self):
        return self.cursor

    def is_foreground(self, hwnd):
        return self.foreground

    def double_click_ms(self):
        return 430

    def refresh_frame(self, hwnd):
        self.calls.append(("refresh_frame", hwnd))
        if self.raise_in == "refresh_frame":
            raise OSError("boom")
        return True

    def extend_frame(self, hwnd, top):
        self.calls.append(("extend_frame", hwnd, top))
        return True

    def release_capture(self):
        self.calls.append(("release_capture",))

    def send(self, hwnd, msg, wparam, lparam):
        self.calls.append(("send", hwnd, msg, wparam, lparam))
        return 0

    def post(self, hwnd, msg, wparam, lparam):
        self.calls.append(("post", hwnd, msg, wparam, lparam))
        return True

    def track_system_menu(self, hwnd, x, y, states):
        self.calls.append(("menu", hwnd, x, y, dict(states)))
        return self.menu_result


def make_controller(api=None, **kwargs):
    api = api or FakeApi()
    params = dict(preference="custom", platform="win32", build=22631, api_factory=lambda: api)
    params.update(kwargs)
    return tb.TitlebarController(**params), api


HWND = 0x5000


# ------------------------------------------------------------- pure logic
class ModeDecisionTest(unittest.TestCase):
    def test_default_is_custom_on_modern_windows(self):
        self.assertEqual(tb.decide_mode("custom", platform="win32", build=17763), ("custom", "preference"))
        self.assertEqual(tb.decide_mode(None, platform="win32", build=22631), ("custom", "preference"))

    def test_preference_native(self):
        self.assertEqual(tb.decide_mode("native", platform="win32", build=22631), ("native", "preference"))
        self.assertEqual(tb.decide_mode(" NATIVE ", platform="win32", build=22631)[0], "native")

    def test_recovery_switches_win_over_preference(self):
        self.assertEqual(tb.decide_mode("custom", platform="win32", build=22631, cli_native=True), ("native", "cli"))
        for value in ("1", "true", "YES", "on", "native"):
            self.assertEqual(tb.decide_mode("custom", platform="win32", build=22631, env_value=value), ("native", "env"))
        for value in ("", "0", "false", None):
            self.assertEqual(tb.decide_mode("custom", platform="win32", build=22631, env_value=value)[0], "custom")

    def test_old_windows_and_other_platforms_stay_native(self):
        self.assertEqual(tb.decide_mode("custom", platform="win32", build=17134), ("native", "old-windows"))
        self.assertEqual(tb.decide_mode("custom", platform="win32", build=None), ("native", "old-windows"))
        self.assertEqual(tb.decide_mode("custom", platform="linux", build=None), ("native", "unsupported-platform"))
        self.assertEqual(tb.decide_mode("custom", platform="darwin", build=None)[0], "native")

    def test_normalize_mode(self):
        self.assertEqual(tb.normalize_mode("native"), "native")
        for value in ("custom", "", None, 3, "weird"):
            self.assertEqual(tb.normalize_mode(value), "custom")


class GeometryTest(unittest.TestCase):
    def test_normal_window_keeps_side_frames_and_drops_caption(self):
        # DefWindowProc proposal for a window at (100,50)-(1100,850)
        proposed = (108, 50 + 39, 1092, 842)
        self.assertEqual(tb.nccalcsize_rect(proposed, 50, maximized=False, frame_thickness=8),
                         (108, 50, 1092, 842))

    def test_maximized_window_is_inset_by_frame_thickness(self):
        # A maximized window hangs 8 px off a 1920x1040 work area.
        proposed = (0, -8 + 39, 1920, 1040)
        self.assertEqual(tb.nccalcsize_rect(proposed, -8, maximized=True, frame_thickness=8),
                         (0, 0, 1920, 1040))

    def test_maximized_with_autohide_taskbar_leaves_gap(self):
        proposed = (0, 31, 1920, 1080)
        self.assertEqual(tb.nccalcsize_rect(proposed, -8, maximized=True, frame_thickness=8,
                                            autohide_edges=("bottom",)), (0, 0, 1920, 1078))
        self.assertEqual(tb.nccalcsize_rect(proposed, -8, maximized=True, frame_thickness=8,
                                            autohide_edges=("top", "left", "right")), (2, 2, 1918, 1080))
        # Not maximized: auto-hide edges are irrelevant.
        self.assertEqual(tb.nccalcsize_rect((8, 39, 100, 100), 0, maximized=False, frame_thickness=8,
                                            autohide_edges=("bottom",)), (8, 0, 100, 100))

    def test_degenerate_rect_never_inverts(self):
        self.assertEqual(tb.nccalcsize_rect((10, 50, 5, 20), 60, maximized=False, frame_thickness=0),
                         (10, 60, 10, 60))

    def test_frame_inset_and_dpi_scaling(self):
        self.assertEqual(tb.frame_inset(4, 4), 8)
        self.assertEqual(tb.frame_inset(-1, 4), 4)
        self.assertEqual(tb.scale_for_dpi(6, 96), 6)
        self.assertEqual(tb.scale_for_dpi(6, 144), 9)
        self.assertEqual(tb.scale_for_dpi(6, 192), 12)
        self.assertEqual(tb.scale_for_dpi(6, 0), 6)

    def test_top_hit_test(self):
        rect = (100, 50, 1100, 850)
        kw = dict(maximized=False, border=6, corner=16)
        self.assertEqual(tb.top_hit_test(500, 50, rect, **kw), tb.HTTOP)
        self.assertEqual(tb.top_hit_test(500, 55, rect, **kw), tb.HTTOP)
        self.assertIsNone(tb.top_hit_test(500, 56, rect, **kw))
        self.assertEqual(tb.top_hit_test(105, 52, rect, **kw), tb.HTTOPLEFT)
        self.assertEqual(tb.top_hit_test(1090, 52, rect, **kw), tb.HTTOPRIGHT)
        self.assertIsNone(tb.top_hit_test(500, 40, rect, **kw))      # above the window
        self.assertIsNone(tb.top_hit_test(1200, 52, rect, **kw))     # outside
        self.assertIsNone(tb.top_hit_test(500, 50, rect, maximized=True, border=6, corner=16))

    def test_point_packing_round_trip_with_negative_monitors(self):
        for x, y in ((0, 0), (500, 300), (-1920, 20), (-5, -1080), (32767, -32768)):
            self.assertEqual(tb.unpack_point(tb.pack_point(x, y)), (x, y))
        self.assertEqual(tb.pack_point(1, 2), (2 << 16) | 1)

    def test_edges_and_commands(self):
        self.assertEqual(tb.edge_to_hit("caption"), tb.HTCAPTION)
        self.assertEqual(tb.edge_to_hit(None), tb.HTCAPTION)
        self.assertEqual(tb.edge_to_hit("TOP"), tb.HTTOP)
        self.assertEqual(tb.edge_to_hit("topleft"), tb.HTTOPLEFT)
        self.assertEqual(tb.edge_to_hit("topright"), tb.HTTOPRIGHT)
        self.assertIsNone(tb.edge_to_hit("bottom"))
        self.assertEqual(tb.command_to_syscommand("toggle", False), tb.SC_MAXIMIZE)
        self.assertEqual(tb.command_to_syscommand("toggle", True), tb.SC_RESTORE)
        self.assertEqual(tb.command_to_syscommand("minimize", True), tb.SC_MINIMIZE)
        self.assertEqual(tb.command_to_syscommand("close", False), tb.SC_CLOSE)
        self.assertIsNone(tb.command_to_syscommand("explode", False))

    def test_system_menu_states_follow_maximize(self):
        normal = tb.system_menu_states(False)
        self.assertFalse(normal[tb.SC_RESTORE])
        self.assertTrue(normal[tb.SC_MOVE] and normal[tb.SC_SIZE] and normal[tb.SC_MAXIMIZE])
        maxed = tb.system_menu_states(True)
        self.assertTrue(maxed[tb.SC_RESTORE])
        self.assertFalse(maxed[tb.SC_MOVE] or maxed[tb.SC_SIZE] or maxed[tb.SC_MAXIMIZE])
        self.assertTrue(maxed[tb.SC_CLOSE] and maxed[tb.SC_MINIMIZE])

    def test_client_check(self):
        self.assertTrue(tb.client_matches_hook((0, 50, 10, 10), (8, 50), maximized=False, frame_thickness=8))
        self.assertFalse(tb.client_matches_hook((0, 50, 10, 10), (8, 81), maximized=False, frame_thickness=8))
        self.assertTrue(tb.client_matches_hook((0, -8, 10, 10), (0, 0), maximized=True, frame_thickness=8))


# ------------------------------------------------------------- controller
class ControllerInstallTest(unittest.TestCase):
    def test_install_subclasses_extends_frame_and_refreshes(self):
        ctl, api = make_controller()
        self.assertTrue(ctl.install(HWND))
        self.assertTrue(ctl.installed)
        self.assertIn(("set_wndproc", HWND, 0xBEEF), api.calls)
        self.assertIn(("extend_frame", HWND, 1), api.calls)
        self.assertIn(("refresh_frame", HWND), api.calls)
        self.assertEqual(ctl._orig, FakeApi.ORIGINAL)
        # idempotent
        api.calls.clear()
        self.assertTrue(ctl.install(HWND))
        self.assertEqual(api.calls, [])

    def test_install_refuses_unsupported_or_wrong_style(self):
        ctl, _api = make_controller(build=17134)
        self.assertFalse(ctl.install(HWND))
        ctl, api = make_controller()
        api.style_value = 0   # FormBorderStyle.None / full screen
        self.assertFalse(ctl.install(HWND))
        self.assertEqual(ctl.error, "window-style")
        self.assertFalse(any(c[0] == "set_wndproc" for c in api.calls))
        ctl, _api = make_controller()
        self.assertFalse(ctl.install(0))

    def test_failed_subclass_is_reported(self):
        ctl, api = make_controller()
        api.fail_set = True
        self.assertFalse(ctl.install(HWND))
        self.assertFalse(ctl.installed)
        self.assertIn("SetWindowLongPtrW", ctl.error)

    def test_client_mismatch_falls_back_to_native(self):
        ctl, api = make_controller()
        api.client_offset = 31   # caption still there: WM_NCCALCSIZE was ignored
        self.assertFalse(ctl.install(HWND))
        self.assertEqual(ctl.error, "client-area-mismatch")
        self.assertFalse(ctl.installed)
        self.assertEqual(api.procs[HWND], FakeApi.ORIGINAL)   # unlinked again
        self.assertIn(("extend_frame", HWND, 0), api.calls)

    def test_exception_during_install_uninstalls(self):
        ctl, api = make_controller()
        api.raise_in = "refresh_frame"
        self.assertFalse(ctl.install(HWND))
        self.assertFalse(ctl.installed)
        self.assertIn("boom", ctl.error)

    def test_api_factory_failure_is_contained(self):
        def broken():
            raise OSError("no user32")
        ctl = tb.TitlebarController(preference="custom", platform="win32", build=22631, api_factory=broken)
        self.assertFalse(ctl.install(HWND))
        self.assertIn("no user32", ctl.error)

    def test_uninstall_restores_original_procedure(self):
        ctl, api = make_controller()
        ctl.install(HWND)
        ctl.uninstall()
        self.assertFalse(ctl.installed)
        self.assertEqual(api.procs[HWND], FakeApi.ORIGINAL)
        self.assertEqual(ctl._orig, FakeApi.ORIGINAL, "stray calls must still chain to WinForms")
        # re-install links again exactly once
        api.calls.clear()
        self.assertTrue(ctl.install(HWND))
        self.assertEqual([c for c in api.calls if c[0] == "set_wndproc"], [("set_wndproc", HWND, 0xBEEF)])

    def test_uninstall_when_someone_subclassed_after_us(self):
        ctl, api = make_controller()
        ctl.install(HWND)
        api.procs[HWND] = 0xCAFE        # a later subclasser
        ctl.uninstall()
        self.assertTrue(ctl._passthrough)
        self.assertEqual(api.procs[HWND], 0xCAFE)
        # Re-enabling must not link us a second time (that would loop).
        api.calls.clear()
        self.assertTrue(ctl.install(HWND))
        self.assertFalse(any(c[0] == "set_wndproc" for c in api.calls))
        self.assertFalse(ctl._passthrough)


class WindowProcedureTest(unittest.TestCase):
    def setUp(self):
        self.ctl, self.api = make_controller()
        self.assertTrue(self.ctl.install(HWND))
        self.api.calls.clear()

    def nccalc(self, window_top=50):
        rect = FakeRect(100, window_top, 1100, 850)
        self.api.nc_rects[777] = rect
        self.ctl._wndproc(HWND, tb.WM_NCCALCSIZE, 1, 777)
        return rect

    def test_nccalcsize_removes_only_the_caption(self):
        rect = self.nccalc()
        self.assertEqual((rect.left, rect.top, rect.right, rect.bottom), (108, 50, 1092, 842))

    def test_nccalcsize_maximized_inset(self):
        self.api.zoomed = True
        rect = self.nccalc(window_top=-8)
        self.assertEqual(rect.top, 0)
        self.api.edges = ("bottom",)
        rect = self.nccalc(window_top=-8)
        self.assertEqual(rect.bottom, 842 - 2)

    def test_nccalcsize_full_screen_passes_through(self):
        self.api.style_value = 0
        rect = self.nccalc()
        self.assertEqual(rect.top, 50 + 39)  # DefWindowProc result untouched

    def test_nccalcsize_wparam_false_is_default(self):
        self.ctl._wndproc(HWND, tb.WM_NCCALCSIZE, 0, 777)
        self.assertEqual(self.api.calls, [("call_proc", FakeApi.ORIGINAL, tb.WM_NCCALCSIZE)])

    def test_nchittest_top_band_and_caption_remap(self):
        lparam = tb.pack_point(500, 52)
        self.assertEqual(self.ctl._wndproc(HWND, tb.WM_NCHITTEST, 0, lparam), tb.HTTOP)
        self.assertEqual(self.ctl._wndproc(HWND, tb.WM_NCHITTEST, 0, tb.pack_point(102, 51)), tb.HTTOPLEFT)
        # DefWindowProc thinks the old caption is still there.
        self.api.default_hit = tb.HTCLOSE
        self.assertEqual(self.ctl._wndproc(HWND, tb.WM_NCHITTEST, 0, tb.pack_point(1080, 70)), tb.HTCLIENT)
        # Real borders are left alone.
        self.api.default_hit = tb.HTLEFT
        self.assertEqual(self.ctl._wndproc(HWND, tb.WM_NCHITTEST, 0, tb.pack_point(101, 400)), tb.HTLEFT)
        # Maximized: no top resize.
        self.api.zoomed = True
        self.api.default_hit = tb.HTCLIENT
        self.assertEqual(self.ctl._wndproc(HWND, tb.WM_NCHITTEST, 0, lparam), tb.HTCLIENT)

    def test_drag_message_starts_native_move_loop(self):
        self.ctl._wndproc(HWND, tb.WM_APP_TITLEBAR, tb.APP_DRAG, tb.HTCAPTION)
        self.assertIn(("release_capture",), self.api.calls)
        self.assertIn(("send", HWND, tb.WM_NCLBUTTONDOWN, tb.HTCAPTION, tb.pack_point(500, 300)), self.api.calls)
        self.assertFalse(any(c == ("call_proc", FakeApi.ORIGINAL, tb.WM_APP_TITLEBAR) for c in self.api.calls))

    def test_drag_ignored_when_button_already_released(self):
        self.api.button_down = False
        self.ctl._wndproc(HWND, tb.WM_APP_TITLEBAR, tb.APP_DRAG, tb.HTCAPTION)
        self.assertFalse(any(c[0] in ("send", "release_capture") for c in self.api.calls))

    def test_top_resize_only_when_not_maximized(self):
        self.ctl._wndproc(HWND, tb.WM_APP_TITLEBAR, tb.APP_DRAG, tb.HTTOPRIGHT)
        self.assertIn(("send", HWND, tb.WM_NCLBUTTONDOWN, tb.HTTOPRIGHT, tb.pack_point(500, 300)), self.api.calls)
        self.api.calls.clear()
        self.api.zoomed = True
        self.ctl._wndproc(HWND, tb.WM_APP_TITLEBAR, tb.APP_DRAG, tb.HTTOP)
        self.assertFalse(any(c[0] == "send" for c in self.api.calls))
        # ...but dragging a maximized window by the caption is allowed (drag-to-restore).
        self.ctl._wndproc(HWND, tb.WM_APP_TITLEBAR, tb.APP_DRAG, tb.HTCAPTION)
        self.assertTrue(any(c[0] == "send" for c in self.api.calls))

    def test_system_menu_at_cursor_and_from_keyboard(self):
        self.ctl._wndproc(HWND, tb.WM_APP_TITLEBAR, tb.APP_SYSMENU, 0)
        menu = [c for c in self.api.calls if c[0] == "menu"][0]
        self.assertEqual(menu[2:4], (500, 300))
        self.assertFalse(menu[4][tb.SC_RESTORE])
        self.assertIn(("post", HWND, tb.WM_SYSCOMMAND, tb.SC_MAXIMIZE, 0), self.api.calls)
        self.api.calls.clear()
        self.api.menu_result = 0   # dismissed
        self.ctl._wndproc(HWND, tb.WM_APP_TITLEBAR, tb.APP_SYSMENU, 1)
        menu = [c for c in self.api.calls if c[0] == "menu"][0]
        self.assertEqual(menu[2:4], (100 + 8, 50 + 32))
        self.assertFalse(any(c[0] == "post" for c in self.api.calls))

    def test_other_messages_chain_to_original(self):
        self.ctl._wndproc(HWND, tb.WM_SIZE, 0, 0)
        self.assertEqual(self.api.calls, [("call_proc", FakeApi.ORIGINAL, tb.WM_SIZE)])

    def test_ncdestroy_unlinks_and_marks_lost(self):
        self.ctl._wndproc(HWND, tb.WM_NCDESTROY, 0, 0)
        self.assertEqual(self.api.procs[HWND], FakeApi.ORIGINAL)
        self.assertFalse(self.ctl.installed)
        self.assertTrue(self.ctl._lost)
        self.assertEqual(self.api.calls[-1], ("call_proc", FakeApi.ORIGINAL, tb.WM_NCDESTROY))

    def test_repeated_errors_switch_to_passthrough(self):
        def explode(_lparam):
            raise ValueError("bad pointer")
        self.api.nccalc_rect = explode
        for _ in range(tb.MAX_WNDPROC_ERRORS):
            self.ctl._wndproc(HWND, tb.WM_NCCALCSIZE, 1, 1)
        self.assertTrue(self.ctl._passthrough)
        self.assertIn(("post", HWND, tb.WM_APP_TITLEBAR, tb.APP_UNINSTALL, 0), self.api.calls)
        # every failing call still reached the original procedure
        self.assertEqual(sum(1 for c in self.api.calls if c[0] == "call_proc"), tb.MAX_WNDPROC_ERRORS)
        self.assertFalse(self.ctl.state()["installed"])

    def test_passthrough_ignores_everything(self):
        self.ctl._passthrough = True
        rect = self.nccalc()
        self.assertEqual(rect.top, 50 + 39)


class BridgeTest(unittest.TestCase):
    def test_attach_installs_through_invoke(self):
        ctl, api = make_controller()
        ran = []

        def invoke(fn):
            ran.append(True)
            return fn()
        self.assertTrue(ctl.attach(lambda: HWND, invoke))
        self.assertTrue(ran)
        self.assertTrue(ctl.state()["installed"])

    def test_attach_native_mode_does_nothing(self):
        ctl, api = make_controller(preference="native")
        self.assertFalse(ctl.attach(lambda: HWND))
        self.assertEqual(api.calls, [])
        self.assertFalse(ctl.state()["installed"])
        self.assertFalse(ctl.state()["pending"])

    def test_state_before_attach_is_pending(self):
        ctl, _api = make_controller()
        state = ctl.state()
        self.assertTrue(state["pending"])
        self.assertFalse(state["installed"])
        self.assertEqual(state["os"], "win11")
        ctl10, _ = make_controller(build=19045)
        self.assertEqual(ctl10.state()["os"], "win10")

    def test_state_fields_after_install(self):
        ctl, api = make_controller()
        ctl.attach(lambda: HWND)
        api.zoomed = True
        api.foreground = False
        state = ctl.state()
        self.assertEqual((state["maximized"], state["active"], state["fullscreen"]), (True, False, False))
        self.assertEqual(state["doubleClickMs"], 430)
        api.style_value = 0
        self.assertTrue(ctl.state()["fullscreen"])

    def test_live_mode_switch(self):
        ctl, api = make_controller()
        ctl.attach(lambda: HWND)
        state = ctl.set_mode("native")
        self.assertFalse(state["installed"])
        self.assertEqual(state["mode"], "native")
        self.assertEqual(api.procs[HWND], FakeApi.ORIGINAL)
        state = ctl.set_mode("custom")
        self.assertTrue(state["installed"])

    def test_forced_native_cannot_be_switched_on(self):
        ctl, api = make_controller(cli_native=True)
        self.assertTrue(ctl.state()["forced"])
        ctl.attach(lambda: HWND)
        self.assertFalse(ctl.set_mode("custom")["installed"])
        self.assertEqual(api.calls, [])

    def test_drag_and_commands_post_messages(self):
        ctl, api = make_controller()
        bridge = tb.TitlebarBridge(ctl)
        self.assertFalse(bridge.titlebar_drag())     # not installed yet
        ctl.attach(lambda: HWND)
        api.calls.clear()
        self.assertTrue(bridge.titlebar_drag())
        self.assertTrue(bridge.titlebar_drag("topleft"))
        self.assertFalse(bridge.titlebar_drag("nonsense"))
        self.assertTrue(bridge.titlebar_command("toggle"))
        self.assertTrue(bridge.titlebar_command("close"))
        self.assertFalse(bridge.titlebar_command("nope"))
        self.assertTrue(bridge.titlebar_system_menu("keyboard"))
        self.assertEqual(api.calls, [
            ("post", HWND, tb.WM_APP_TITLEBAR, tb.APP_DRAG, tb.HTCAPTION),
            ("post", HWND, tb.WM_APP_TITLEBAR, tb.APP_DRAG, tb.HTTOPLEFT),
            ("post", HWND, tb.WM_SYSCOMMAND, tb.SC_MAXIMIZE, 0),
            ("post", HWND, tb.WM_SYSCOMMAND, tb.SC_CLOSE, 0),
            ("post", HWND, tb.WM_APP_TITLEBAR, tb.APP_SYSMENU, 1),
        ])

    def test_bridge_exposes_only_titlebar_methods(self):
        bridge = tb.TitlebarBridge(make_controller()[0])
        public = sorted(name for name in dir(bridge) if not name.startswith("_"))
        # Round 30: + the window material (app/win_backdrop.py)
        self.assertEqual(public, ["titlebar_command", "titlebar_drag", "titlebar_set_mode",
                                  "titlebar_state", "titlebar_system_menu", "window_backdrop", "window_backdrop_state"])

    def test_reattach_after_handle_recreation(self):
        ctl, api = make_controller()
        handles = [HWND]
        ctl.attach(lambda: handles[0])
        ctl._wndproc(HWND, tb.WM_NCDESTROY, 0, 0)
        self.assertFalse(ctl.state()["installed"])     # same handle → nothing to do
        handles[0] = HWND + 4
        self.assertTrue(ctl.state()["installed"])
        self.assertEqual(ctl.hwnd, HWND + 4)

    def test_invoke_failure_is_contained(self):
        ctl, _api = make_controller()

        def broken(_fn):
            raise RuntimeError("ui thread gone")
        self.assertFalse(ctl.attach(lambda: HWND, broken))
        self.assertIn("ui thread gone", ctl.state()["error"])


class StartupTest(unittest.TestCase):
    def test_create_controller_reads_preference_cli_and_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefs = Path(tmp) / "preferences.json"
            prefs.write_text(json.dumps({"ui_titlebar": "native"}), encoding="utf-8")
            self.assertEqual(tb.read_preference(prefs), "native")
            prefs.write_text("{broken", encoding="utf-8")
            self.assertEqual(tb.read_preference(prefs), "custom")
            self.assertEqual(tb.read_preference(Path(tmp) / "missing.json"), "custom")
            with patch.object(tb.sys, "platform", "win32"), patch.object(tb, "windows_build", return_value=22631):
                self.assertEqual(tb.create_controller(prefs, [], {}).mode, "custom")
                ctl = tb.create_controller(prefs, ["--native-titlebar"], {})
                self.assertEqual((ctl.mode, ctl.reason), ("native", "cli"))
                ctl = tb.create_controller(prefs, [], {tb.ENV_VAR: "1"})
                self.assertEqual((ctl.mode, ctl.reason), ("native", "env"))

    @unittest.skipIf(sys.platform == "win32", "checks the non-Windows fallback")
    def test_non_windows_controller_is_inert(self):
        ctl = tb.TitlebarController(preference="custom")
        self.assertEqual(ctl.mode, "native")
        self.assertFalse(ctl.attach(lambda: HWND))
        self.assertFalse(ctl.start_drag())
        self.assertFalse(ctl.command("close"))
        self.assertFalse(ctl.state()["supported"])

    def test_memuai_helpers_never_raise(self):
        import memuai
        ctl = memuai._create_titlebar(True)
        self.assertIsNotNone(ctl)
        self.assertEqual(ctl.mode, "native")
        self.assertIsInstance(memuai._titlebar_bridge(ctl), tb.TitlebarBridge)
        self.assertIsNone(memuai._titlebar_bridge(None))
        self.assertFalse(memuai._attach_titlebar(None, object()))
        with patch("app.win_titlebar.create_controller", side_effect=RuntimeError("broken")):
            self.assertIsNone(memuai._create_titlebar(False))

    def test_ui_invoker_runs_inline_without_invoke_required(self):
        native = SimpleNamespace(InvokeRequired=False)
        self.assertEqual(tb.winforms_ui_invoker(native)(lambda: 42), 42)


class DesktopShellTest(unittest.TestCase):
    def _run(self, **kwargs):
        from app import desktop
        created = {}

        class FakeWindow:
            def __init__(self):
                self.events = SimpleNamespace(closing=self, shown=self)

            def __iadd__(self, _handler):
                return self

        def create_window(*_args, **kw):
            created.update(kw)
            return FakeWindow()
        fake_webview = SimpleNamespace(create_window=create_window, start=lambda **_kw: None)
        shell = desktop.DesktopShell("http://127.0.0.1", show_tray=False, **kwargs)
        with patch.dict(sys.modules, {"webview": fake_webview}):
            shell.run()
        return created

    def test_js_api_is_passed_only_when_given(self):
        self.assertNotIn("js_api", self._run())
        marker = object()
        created = self._run(js_api=marker)
        self.assertIs(created["js_api"], marker)
        # The frame contract of earlier rounds is unchanged.
        self.assertFalse(created["frameless"])
        self.assertFalse(created["easy_drag"])
        self.assertTrue(created["resizable"])


class StaticContractTest(unittest.TestCase):
    def test_page_wiring(self):
        html = (ROOT / "app" / "pgstatic" / "index.html").read_text(encoding="utf-8")
        self.assertIn('<link rel="stylesheet" href="/titlebar.css', html)
        self.assertIn('<script src="/titlebar.js', html)
        # titlebar.css must load after every design so equal-specificity rules win.
        self.assertGreater(html.index("/titlebar.css"), html.index("/designs/glass.css"))
        js = (ROOT / "app" / "pgstatic" / "titlebar.js").read_text(encoding="utf-8")
        self.assertIn("titlebar_state", js)
        self.assertIn('root.dataset.titlebar = "custom"', js)
        css = (ROOT / "app" / "pgstatic" / "titlebar.css").read_text(encoding="utf-8")
        for design in ("classic", "aurora", "focus", "grid", "glass"):
            self.assertIn(f'[data-design="{design}"]', css)
        appearance = (ROOT / "app" / "pgstatic" / "appearance.js").read_text(encoding="utf-8")
        self.assertIn('name="appearance-titlebar"', appearance)
        self.assertIn("ui_titlebar", appearance)

    def test_preference_default_and_normalization(self):
        from app import pgdata
        self.assertEqual(pgdata.DEFAULT_PREFERENCES["ui_titlebar"], "custom")
        self.assertEqual(pgdata._normalize_preferences({"ui_titlebar": "native"})["ui_titlebar"], "native")
        self.assertEqual(pgdata._normalize_preferences({"ui_titlebar": "bogus"})["ui_titlebar"], "custom")

    def test_win32_layer_declares_64bit_safe_signatures(self):
        source = (ROOT / "app" / "win_titlebar.py").read_text(encoding="utf-8")
        for needle in ('"SetWindowLongPtrW", ctypes.c_void_p', '"CallWindowProcW", LRESULT',
                       'ctypes.WINFUNCTYPE(LRESULT, HWND, ctypes.c_uint, WPARAM, LPARAM)',
                       'ctypes.WinDLL("user32", use_last_error=True)', "_KEEPALIVE.append"):
            self.assertIn(needle, source)
        self.assertNotIn("ctypes.windll.user32.SetWindowLong", source)


if __name__ == "__main__":
    unittest.main()
