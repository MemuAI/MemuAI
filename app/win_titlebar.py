"""Custom title bar for the MemuAI desktop window (Windows 10 1809+ / 11).

Why this exists, and why it is not "frameless"
----------------------------------------------
Earlier attempts removed the caption by making the WinForms window
*borderless* and grafting a WndProc on top.  DWM cannot render that hybrid
reliably: resizing, Snap, the shadow and Windows 11 rounded corners broke.

This module uses the technique of Chromium/Electron ``titleBarStyle:
"hidden"`` and Windows Terminal instead:

* the window keeps its normal ``WS_OVERLAPPEDWINDOW`` styles, so Windows
  itself still owns resizing, Aero Snap, Aero Shake, min/max animations, the
  shadow and the rounded corners;
* the top-level HWND is *subclassed* (``SetWindowLongPtrW(GWLP_WNDPROC)``) and
  ``WM_NCCALCSIZE`` is answered so that only the caption disappears: the
  default left/right/bottom frame (the invisible resize borders) stays, the
  client area simply starts at the very top of the window.  A maximized
  window is inset by the frame thickness so nothing is clipped off-screen;
* the WebView2 child covers the client area and receives all mouse input, so
  the page tells us when the user presses its own title-bar area.  We then
  start the *native* move/size loop with ``WM_NCLBUTTONDOWN`` + ``HTCAPTION``
  (or ``HTTOP*`` for the top resize edge) on the UI thread.  That gives the
  real Windows behaviour: moving, Snap to screen edges, drag-down-to-restore.

Every Win32 call is wrapped: if anything fails the hook removes itself and
the window keeps its standard caption.  Recovery switches:

* Settings → Appearance → "Window title bar: system";
* command line ``MemuAI.exe --native-titlebar``;
* environment variable ``MEMUAI_NATIVE_TITLEBAR=1``.

The pure helpers at the top (mode decision, NCCALCSIZE math, hit-testing,
point packing) have no Win32 dependency and are unit-tested on any OS.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from typing import Any, Callable, Iterable, Optional, Sequence, Tuple

log = logging.getLogger("memuai.titlebar")

MODE_CUSTOM = "custom"
MODE_NATIVE = "native"
PREF_KEY = "ui_titlebar"
ENV_VAR = "MEMUAI_NATIVE_TITLEBAR"
CLI_FLAG = "--native-titlebar"
#: Windows 10 1809 (October 2018 Update): first build where the dark-mode
#: frame attributes and the per-monitor DPI APIs used here are all present.
MIN_WINDOWS_BUILD = 17763
WINDOWS_11_BUILD = 22000

# ---------------------------------------------------------------- Win32 ids
WM_SIZE = 0x0005
WM_NCDESTROY = 0x0082
WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
WM_NCLBUTTONDOWN = 0x00A1
WM_SYSCOMMAND = 0x0112
WM_DWMCOMPOSITIONCHANGED = 0x031E
WM_APP = 0x8000
#: Private message posted by the JS bridge threads; handled on the UI thread.
WM_APP_TITLEBAR = WM_APP + 0x4D31

APP_DRAG = 1          # lParam = hit-test code (HTCAPTION / HTTOP…)
APP_SYSMENU = 2       # lParam = 0 at the cursor, 1 under the title bar (keyboard)
APP_UNINSTALL = 3     # remove the hook (fallback after repeated errors)

HTCLIENT = 1
HTCAPTION = 2
HTSYSMENU = 3
HTMINBUTTON = 8
HTMAXBUTTON = 9
HTLEFT = 10
HTRIGHT = 11
HTTOP = 12
HTTOPLEFT = 13
HTTOPRIGHT = 14
HTBOTTOM = 15
HTBOTTOMLEFT = 16
HTBOTTOMRIGHT = 17
HTCLOSE = 20

SC_SIZE = 0xF000
SC_MOVE = 0xF010
SC_MINIMIZE = 0xF020
SC_MAXIMIZE = 0xF030
SC_CLOSE = 0xF060
SC_RESTORE = 0xF120

GWL_STYLE = -16
GWLP_WNDPROC = -4
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_NOOWNERZORDER = 0x0200
SWP_REFRESH_FRAME = (SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
                     | SWP_NOOWNERZORDER | SWP_NOACTIVATE)

SM_CXFRAME = 32
SM_CYFRAME = 33
SM_SWAPBUTTON = 23
SM_CXPADDEDBORDER = 92
VK_LBUTTON = 0x01
VK_RBUTTON = 0x02

MF_BYCOMMAND = 0x0000
MF_ENABLED = 0x0000
MF_GRAYED = 0x0001
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100
TPM_NONOTIFY = 0x0080

ABM_GETSTATE = 0x04
ABM_GETAUTOHIDEBAREX = 0x0B
ABS_AUTOHIDE = 0x01
ABE_LEFT, ABE_TOP, ABE_RIGHT, ABE_BOTTOM = 0, 1, 2, 3
MONITOR_DEFAULTTONEAREST = 2

#: Pixels left free on a monitor edge that hosts an auto-hide taskbar, so a
#: maximized window does not look "full screen" to the shell (Windows
#: Terminal uses the same 2 px).
AUTOHIDE_GAP = 2
#: Height of the top resize band at 96 DPI (Windows uses ~ the frame size).
TOP_RESIZE_BORDER_96 = 6
TOP_RESIZE_CORNER_96 = 16
CAPTION_HEIGHT_96 = 32

#: After this many exceptions inside the window procedure the hook turns
#: itself into a pure pass-through and asks to be removed.
MAX_WNDPROC_ERRORS = 12

Rect = Tuple[int, int, int, int]


# ============================================================ pure helpers
def normalize_mode(value: Any) -> str:
    """Stored preference → ``custom`` (default) or ``native``."""
    return MODE_NATIVE if str(value or "").strip().lower() == MODE_NATIVE else MODE_CUSTOM


def env_forces_native(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on", "native")


def decide_mode(preference: Any, *, platform: str, build: Optional[int],
                cli_native: bool = False, env_value: Optional[str] = None) -> Tuple[str, str]:
    """Return ``(mode, reason)`` for the startup title bar.

    The recovery switches always win over the stored preference, so a user
    whose custom title bar misbehaves can get the system one back without
    opening the UI.
    """
    if platform != "win32":
        return MODE_NATIVE, "unsupported-platform"
    if cli_native:
        return MODE_NATIVE, "cli"
    if env_forces_native(env_value):
        return MODE_NATIVE, "env"
    if build is None or build < MIN_WINDOWS_BUILD:
        return MODE_NATIVE, "old-windows"
    mode = normalize_mode(preference)
    return mode, "preference"


def windows_build() -> Optional[int]:
    if sys.platform != "win32":
        return None
    try:
        return int(sys.getwindowsversion().build)  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        import platform
        return int(platform.version().split(".")[-1])
    except Exception:
        return None


def frame_inset(frame: int, padded_border: int) -> int:
    """Thickness a maximized window hangs off the monitor on each side."""
    return max(0, int(frame)) + max(0, int(padded_border))


def nccalcsize_rect(proposed: Rect, original_top: int, *, maximized: bool,
                    frame_thickness: int, autohide_edges: Iterable[str] = ()) -> Rect:
    """Client rectangle after removing only the caption.

    ``proposed`` is what DefWindowProc computed (frame on every side plus the
    caption on top); ``original_top`` is the window's top edge before it ran.
    Left/right/bottom keep the default frame, so Windows' invisible resize
    borders keep working.  A maximized window extends past the monitor by the
    frame thickness, so its top is pushed back on screen by that amount.  An
    auto-hide taskbar edge keeps a 2 px gap so the taskbar can still appear.
    """
    left, _top, right, bottom = (int(v) for v in proposed)
    top = int(original_top) + (int(frame_thickness) if maximized else 0)
    if maximized:
        edges = set(autohide_edges or ())
        if "top" in edges:
            top += AUTOHIDE_GAP
        if "bottom" in edges:
            bottom -= AUTOHIDE_GAP
        if "left" in edges:
            left += AUTOHIDE_GAP
        if "right" in edges:
            right -= AUTOHIDE_GAP
    if bottom < top:
        bottom = top
    if right < left:
        right = left
    return left, top, right, bottom


def scale_for_dpi(value_96: int, dpi: int) -> int:
    dpi = int(dpi or 96)
    return max(1, int(round(value_96 * dpi / 96.0)))


def top_hit_test(x: int, y: int, window_rect: Rect, *, maximized: bool,
                 border: int, corner: int) -> Optional[int]:
    """HTTOP/HTTOPLEFT/HTTOPRIGHT for a screen point in the top resize band.

    Only meaningful when the parent window itself is hit-tested (the WebView2
    child normally covers the client area; the page then sends the same codes
    through :meth:`TitlebarController.start_drag`).
    """
    if maximized:
        return None
    left, top, right, bottom = window_rect
    if not (left <= x < right and top <= y < bottom):
        return None
    if y >= top + max(1, border):
        return None
    if x < left + corner:
        return HTTOPLEFT
    if x >= right - corner:
        return HTTOPRIGHT
    return HTTOP


_EDGE_HIT = {
    "caption": HTCAPTION, "top": HTTOP, "topleft": HTTOPLEFT, "topright": HTTOPRIGHT,
}


def edge_to_hit(edge: Any) -> Optional[int]:
    return _EDGE_HIT.get(str(edge or "caption").strip().lower())


def pack_point(x: int, y: int) -> int:
    """MAKELPARAM for screen coordinates (negative on left/upper monitors)."""
    return ((int(y) & 0xFFFF) << 16) | (int(x) & 0xFFFF)


def unpack_point(lparam: int) -> Tuple[int, int]:
    """GET_X_LPARAM / GET_Y_LPARAM with sign extension."""
    value = int(lparam) & 0xFFFFFFFF
    x = value & 0xFFFF
    y = (value >> 16) & 0xFFFF
    if x >= 0x8000:
        x -= 0x10000
    if y >= 0x8000:
        y -= 0x10000
    return x, y


def system_menu_states(maximized: bool) -> dict:
    """Which system-menu items are enabled, like the standard caption menu."""
    return {
        SC_RESTORE: bool(maximized),
        SC_MOVE: not maximized,
        SC_SIZE: not maximized,
        SC_MINIMIZE: True,
        SC_MAXIMIZE: not maximized,
        SC_CLOSE: True,
    }


def command_to_syscommand(command: Any, maximized: bool) -> Optional[int]:
    command = str(command or "").strip().lower()
    if command == "minimize":
        return SC_MINIMIZE
    if command == "maximize":
        return SC_MAXIMIZE
    if command == "restore":
        return SC_RESTORE
    if command == "toggle":
        return SC_RESTORE if maximized else SC_MAXIMIZE
    if command == "close":
        return SC_CLOSE
    return None


def client_matches_hook(window_rect: Rect, client_origin: Tuple[int, int], *,
                        maximized: bool, frame_thickness: int, tolerance: int = 2) -> bool:
    """Post-install sanity check: the client must start at the window top.

    If WinForms (or anything else) ignored our WM_NCCALCSIZE answer the
    caption is still there and the custom UI would be drawn under it, so the
    caller falls back to the native title bar.
    """
    expected = window_rect[1] + (frame_thickness if maximized else 0)
    return abs(client_origin[1] - expected) <= tolerance + (AUTOHIDE_GAP if maximized else 0)


# ======================================================== Win32 boundary
class Win32Api:
    """Thin ctypes layer with explicit 64-bit-safe signatures.

    Private ``WinDLL`` instances are used so the argtypes set here never leak
    into ``ctypes.windll.user32``, which the rest of the app calls with plain
    integers.
    """

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.wintypes = wintypes
        LRESULT = ctypes.c_ssize_t
        WPARAM = ctypes.c_size_t
        LPARAM = ctypes.c_ssize_t
        HWND = ctypes.c_void_p
        self.WNDPROC = ctypes.WINFUNCTYPE(LRESULT, HWND, ctypes.c_uint, WPARAM, LPARAM)

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        class MARGINS(ctypes.Structure):
            _fields_ = [("cxLeftWidth", ctypes.c_int), ("cxRightWidth", ctypes.c_int),
                        ("cyTopHeight", ctypes.c_int), ("cyBottomHeight", ctypes.c_int)]

        class APPBARDATA(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", HWND),
                        ("uCallbackMessage", ctypes.c_uint), ("uEdge", ctypes.c_uint),
                        ("rc", RECT), ("lParam", LPARAM)]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                        ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]

        self.RECT, self.POINT, self.MARGINS = RECT, POINT, MARGINS
        self.APPBARDATA, self.MONITORINFO = APPBARDATA, MONITORINFO

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32 = user32

        def sig(dll, name, restype, *argtypes, optional=False):
            try:
                fn = getattr(dll, name)
            except AttributeError:
                if optional:
                    return None
                raise
            fn.restype = restype
            fn.argtypes = list(argtypes)
            return fn

        if ctypes.sizeof(ctypes.c_void_p) == 8:
            self._get_long = sig(user32, "GetWindowLongPtrW", ctypes.c_ssize_t, HWND, ctypes.c_int)
            self._set_long = sig(user32, "SetWindowLongPtrW", ctypes.c_void_p, HWND, ctypes.c_int, ctypes.c_void_p)
        else:  # 32-bit Python: the *Ptr names are macros there
            self._get_long = sig(user32, "GetWindowLongW", ctypes.c_long, HWND, ctypes.c_int)
            self._set_long = sig(user32, "SetWindowLongW", ctypes.c_void_p, HWND, ctypes.c_int, ctypes.c_void_p)
        self._call_proc = sig(user32, "CallWindowProcW", LRESULT, ctypes.c_void_p, HWND, ctypes.c_uint, WPARAM, LPARAM)
        self._send = sig(user32, "SendMessageW", LRESULT, HWND, ctypes.c_uint, WPARAM, LPARAM)
        self._post = sig(user32, "PostMessageW", wintypes.BOOL, HWND, ctypes.c_uint, WPARAM, LPARAM)
        self._is_window = sig(user32, "IsWindow", wintypes.BOOL, HWND)
        self._is_zoomed = sig(user32, "IsZoomed", wintypes.BOOL, HWND)
        self._is_iconic = sig(user32, "IsIconic", wintypes.BOOL, HWND)
        self._set_pos = sig(user32, "SetWindowPos", wintypes.BOOL, HWND, HWND, ctypes.c_int,
                            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint)
        self._window_rect = sig(user32, "GetWindowRect", wintypes.BOOL, HWND, ctypes.POINTER(RECT))
        self._client_to_screen = sig(user32, "ClientToScreen", wintypes.BOOL, HWND, ctypes.POINTER(POINT))
        self._screen_to_client = sig(user32, "ScreenToClient", wintypes.BOOL, HWND, ctypes.POINTER(POINT))
        self._client_rect = sig(user32, "GetClientRect", wintypes.BOOL, HWND, ctypes.POINTER(RECT))
        self._release_capture = sig(user32, "ReleaseCapture", wintypes.BOOL)
        self._cursor_pos = sig(user32, "GetCursorPos", wintypes.BOOL, ctypes.POINTER(POINT))
        self._async_key = sig(user32, "GetAsyncKeyState", ctypes.c_short, ctypes.c_int)
        self._metrics = sig(user32, "GetSystemMetrics", ctypes.c_int, ctypes.c_int)
        self._metrics_dpi = sig(user32, "GetSystemMetricsForDpi", ctypes.c_int, ctypes.c_int,
                                ctypes.c_uint, optional=True)
        self._dpi_for_window = sig(user32, "GetDpiForWindow", ctypes.c_uint, HWND, optional=True)
        self._foreground = sig(user32, "GetForegroundWindow", HWND)
        self._dblclick = sig(user32, "GetDoubleClickTime", ctypes.c_uint)
        self._system_menu = sig(user32, "GetSystemMenu", ctypes.c_void_p, HWND, wintypes.BOOL)
        self._enable_item = sig(user32, "EnableMenuItem", wintypes.BOOL, ctypes.c_void_p,
                                ctypes.c_uint, ctypes.c_uint)
        self._default_item = sig(user32, "SetMenuDefaultItem", wintypes.BOOL, ctypes.c_void_p,
                                 ctypes.c_uint, ctypes.c_uint)
        self._track_menu = sig(user32, "TrackPopupMenu", wintypes.BOOL, ctypes.c_void_p,
                               ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_int, HWND,
                               ctypes.c_void_p)
        self._monitor_from_window = sig(user32, "MonitorFromWindow", ctypes.c_void_p, HWND, wintypes.DWORD)
        self._monitor_info = sig(user32, "GetMonitorInfoW", wintypes.BOOL, ctypes.c_void_p,
                                 ctypes.POINTER(MONITORINFO))
        self._def_proc = sig(user32, "DefWindowProcW", LRESULT, HWND, ctypes.c_uint, WPARAM, LPARAM)
        try:
            dwmapi = ctypes.WinDLL("dwmapi")
            self._dwm_extend = sig(dwmapi, "DwmExtendFrameIntoClientArea", ctypes.c_long, HWND,
                                   ctypes.POINTER(MARGINS))
        except OSError:
            self._dwm_extend = None
        try:
            shell32 = ctypes.WinDLL("shell32")
            self._appbar = sig(shell32, "SHAppBarMessage", ctypes.c_size_t, wintypes.DWORD,
                               ctypes.POINTER(APPBARDATA))
        except OSError:
            self._appbar = None

    # ---- window procedure plumbing
    def make_wndproc(self, fn):
        return self.WNDPROC(fn)

    def proc_address(self, proc) -> int:
        return int(self.ctypes.cast(proc, self.ctypes.c_void_p).value or 0)

    def get_wndproc(self, hwnd) -> int:
        value = int(self._get_long(hwnd, GWLP_WNDPROC) or 0)
        return value & 0xFFFFFFFF if self.ctypes.sizeof(self.ctypes.c_void_p) == 4 else value & 0xFFFFFFFFFFFFFFFF

    def set_wndproc(self, hwnd, address: int) -> int:
        self.ctypes.set_last_error(0)
        return int(self._set_long(hwnd, GWLP_WNDPROC, self.ctypes.c_void_p(address)) or 0)

    def call_proc(self, original: int, hwnd, msg, wparam, lparam) -> int:
        if not original:
            return int(self._def_proc(hwnd, msg, wparam, lparam))
        return int(self._call_proc(self.ctypes.c_void_p(original), hwnd, msg, wparam, lparam))

    def style(self, hwnd) -> int:  # noqa: D401 - GWL_STYLE as unsigned
        return int(self._get_long(hwnd, GWL_STYLE) or 0) & 0xFFFFFFFF

    # ---- queries
    def is_window(self, hwnd) -> bool:
        return bool(hwnd) and bool(self._is_window(hwnd))

    def is_zoomed(self, hwnd) -> bool:
        return bool(self._is_zoomed(hwnd))

    def is_iconic(self, hwnd) -> bool:
        return bool(self._is_iconic(hwnd))

    def window_rect(self, hwnd) -> Rect:
        rect = self.RECT()
        self._window_rect(hwnd, self.ctypes.byref(rect))
        return rect.left, rect.top, rect.right, rect.bottom

    def client_origin(self, hwnd) -> Tuple[int, int]:
        point = self.POINT(0, 0)
        self._client_to_screen(hwnd, self.ctypes.byref(point))
        return point.x, point.y

    def point_in_client(self, hwnd, x: int, y: int) -> bool:
        point = self.POINT(x, y)
        self._screen_to_client(hwnd, self.ctypes.byref(point))
        rect = self.RECT()
        self._client_rect(hwnd, self.ctypes.byref(rect))
        return rect.left <= point.x < rect.right and rect.top <= point.y < rect.bottom

    def nccalc_rect(self, lparam):
        """rgrc[0] of NCCALCSIZE_PARAMS (also the RECT for wParam=FALSE)."""
        return self.RECT.from_address(int(lparam))

    def dpi(self, hwnd) -> int:
        try:
            if self._dpi_for_window is not None:
                value = int(self._dpi_for_window(hwnd))
                if value > 0:
                    return value
        except Exception:
            pass
        return 96

    def metric(self, index: int, dpi: int) -> int:
        try:
            if self._metrics_dpi is not None:
                return int(self._metrics_dpi(index, dpi))
        except Exception:
            pass
        return int(self._metrics(index))

    def frame_thickness(self, hwnd) -> int:
        dpi = self.dpi(hwnd)
        return frame_inset(self.metric(SM_CYFRAME, dpi), self.metric(SM_CXPADDEDBORDER, dpi))

    def autohide_edges(self, hwnd) -> Tuple[str, ...]:
        if self._appbar is None:
            return ()
        try:
            data = self.APPBARDATA()
            data.cbSize = self.ctypes.sizeof(self.APPBARDATA)
            if not (int(self._appbar(ABM_GETSTATE, self.ctypes.byref(data))) & ABS_AUTOHIDE):
                return ()
            monitor = self._monitor_from_window(hwnd, MONITOR_DEFAULTTONEAREST)
            info = self.MONITORINFO()
            info.cbSize = self.ctypes.sizeof(self.MONITORINFO)
            if not monitor or not self._monitor_info(monitor, self.ctypes.byref(info)):
                return ()
            edges = []
            for edge, name in ((ABE_TOP, "top"), (ABE_BOTTOM, "bottom"),
                               (ABE_LEFT, "left"), (ABE_RIGHT, "right")):
                probe = self.APPBARDATA()
                probe.cbSize = self.ctypes.sizeof(self.APPBARDATA)
                probe.uEdge = edge
                probe.rc = info.rcMonitor
                if self._appbar(ABM_GETAUTOHIDEBAREX, self.ctypes.byref(probe)):
                    edges.append(name)
            return tuple(edges)
        except Exception:
            return ()

    def primary_button_down(self) -> bool:
        swapped = bool(self._metrics(SM_SWAPBUTTON))
        return bool(int(self._async_key(VK_RBUTTON if swapped else VK_LBUTTON)) & 0x8000)

    def cursor_pos(self) -> Tuple[int, int]:
        point = self.POINT()
        self._cursor_pos(self.ctypes.byref(point))
        return point.x, point.y

    def is_foreground(self, hwnd) -> bool:
        try:
            return int(self._foreground() or 0) == int(hwnd)
        except Exception:
            return True

    def double_click_ms(self) -> int:
        try:
            return int(self._dblclick()) or 500
        except Exception:
            return 500

    # ---- actions
    def refresh_frame(self, hwnd) -> bool:
        return bool(self._set_pos(hwnd, None, 0, 0, 0, 0, SWP_REFRESH_FRAME))

    def extend_frame(self, hwnd, top: int) -> bool:
        if self._dwm_extend is None:
            return False
        margins = self.MARGINS(0, 0, int(top), 0)
        return int(self._dwm_extend(hwnd, self.ctypes.byref(margins))) == 0

    def release_capture(self) -> None:
        self._release_capture()

    def send(self, hwnd, msg, wparam, lparam) -> int:
        return int(self._send(hwnd, msg, wparam, lparam))

    def post(self, hwnd, msg, wparam, lparam) -> bool:
        return bool(self._post(hwnd, msg, wparam, lparam))

    def track_system_menu(self, hwnd, x: int, y: int, states: dict) -> int:
        menu = self._system_menu(hwnd, False)
        if not menu:
            return 0
        for command, enabled in states.items():
            self._enable_item(menu, command, MF_BYCOMMAND | (MF_ENABLED if enabled else MF_GRAYED))
        self._default_item(menu, SC_CLOSE, 0)
        return int(self._track_menu(menu, TPM_RETURNCMD | TPM_RIGHTBUTTON | TPM_NONOTIFY,
                                    int(x), int(y), 0, hwnd, None) or 0)


# ======================================================= the controller
#: Callbacks handed to Windows must outlive every possible call; the hook is
#: never garbage-collected even after uninstall (another subclasser may still
#: chain to it).
_KEEPALIVE: list = []


class TitlebarController:
    """Owns the subclass hook of one top-level window.

    Thread model: :meth:`install`/:meth:`uninstall` run through ``invoke`` on
    the WinForms UI thread; the JS bridge methods only *post* messages, which
    are then handled inside the window procedure on that same UI thread.
    """

    def __init__(self, *, preference: Any = MODE_CUSTOM, platform: Optional[str] = None,
                 build: Optional[int] = None, cli_native: bool = False,
                 env_value: Optional[str] = None, api_factory: Callable[[], Any] = Win32Api) -> None:
        platform = sys.platform if platform is None else platform
        self.platform = platform
        self.build = build if build is not None else windows_build()
        self.mode, self.reason = decide_mode(preference, platform=platform, build=self.build,
                                             cli_native=cli_native, env_value=env_value)
        self.forced = self.reason in ("cli", "env")
        self.supported = (platform == "win32" and self.build is not None
                          and self.build >= MIN_WINDOWS_BUILD)
        self._api_factory = api_factory
        self.api: Any = None
        self.hwnd: int = 0
        self.installed = False
        self.error = ""
        self._attached = False
        self._lost = False
        self._passthrough = False
        self._errors = 0
        self._orig = 0
        self._chained_hwnd = 0
        self._proc = None
        self._proc_address = 0
        self._invoke: Callable[[Callable[[], Any]], Any] = lambda fn: fn()
        self._hwnd_provider: Optional[Callable[[], int]] = None
        self._lock = threading.RLock()

    # ------------------------------------------------------------ attach
    def attach(self, hwnd_provider: Callable[[], int],
               invoke: Optional[Callable[[Callable[[], Any]], Any]] = None) -> bool:
        """Remember the window and apply the startup mode."""
        self._hwnd_provider = hwnd_provider
        if invoke is not None:
            self._invoke = invoke
        self._attached = True
        if self.mode != MODE_CUSTOM:
            return False
        return self._install_on_ui()

    def _install_on_ui(self) -> bool:
        provider = self._hwnd_provider
        if provider is None:
            return False
        result = {"ok": False}

        def run() -> None:
            try:
                result["ok"] = self.install(int(provider() or 0))
            except Exception as exc:  # pragma: no cover - defensive
                self.error = str(exc)
                result["ok"] = False
        try:
            self._invoke(run)
        except Exception as exc:
            self.error = f"invoke: {exc}"
            return False
        return bool(result["ok"])

    def _uninstall_on_ui(self) -> None:
        try:
            self._invoke(self.uninstall)
        except Exception as exc:
            self.error = f"invoke: {exc}"
            # Last resort: make the procedure inert so nothing custom happens.
            self._passthrough = True
            self.installed = False

    # ----------------------------------------------------------- install
    def install(self, hwnd: int) -> bool:
        """Subclass ``hwnd`` and remove its caption. Call on the UI thread."""
        with self._lock:
            if self.installed and hwnd == self.hwnd:
                return True
            if not self.supported:
                self.error = "unsupported"
                return False
            try:
                if self.api is None:
                    self.api = self._api_factory()
                api = self.api
                if not api.is_window(hwnd):
                    self.error = "no-window"
                    return False
                style = api.style(hwnd)
                if not (style & WS_CAPTION) or not (style & WS_THICKFRAME):
                    self.error = "window-style"
                    return False
                if self._proc is None:
                    self._proc = api.make_wndproc(self._wndproc)
                    _KEEPALIVE.append(self._proc)
                    self._proc_address = api.proc_address(self._proc)
                self._errors = 0
                if hwnd == self._chained_hwnd or api.get_wndproc(hwnd) == self._proc_address:
                    # Still linked into this window's chain (an earlier
                    # uninstall could not unlink): re-enable, never re-link,
                    # or the chain would loop through us twice.
                    self.hwnd = hwnd
                else:
                    previous = api.set_wndproc(hwnd, self._proc_address)
                    if not previous:
                        self.error = "SetWindowLongPtrW failed"
                        return False
                    self.hwnd = hwnd
                    self._orig = previous
                    self._chained_hwnd = 0
                self._passthrough = False
                self.installed = True
                self._lost = False
                # Keeps the DWM shadow on Windows 10 (harmless on 11).
                try:
                    api.extend_frame(hwnd, 1)
                except Exception:
                    pass
                theme = getattr(self, "native_theme", None)
                if theme is not None:
                    # Changing native window style (fullscreen/restore) can
                    # recreate the HWND; the new DWM frame has no old colours.
                    try:
                        from .win_window_theme import apply
                        apply(getattr(self, "native_surface", None), hwnd, *theme,
                              transparent=bool(getattr(self, "backdrop_active", False)))
                    except Exception:
                        pass
                api.refresh_frame(hwnd)
                maximized = api.is_zoomed(hwnd)
                if not api.is_iconic(hwnd) and not client_matches_hook(
                        api.window_rect(hwnd), api.client_origin(hwnd), maximized=maximized,
                        frame_thickness=api.frame_thickness(hwnd)):
                    self.error = "client-area-mismatch"
                    self.uninstall()
                    return False
                self.error = ""
                log.info("custom title bar installed on hwnd=%s", hwnd)
                return True
            except Exception as exc:
                self.error = str(exc) or exc.__class__.__name__
                log.warning("custom title bar failed, keeping the system one: %s", self.error)
                try:
                    self.uninstall()
                except Exception:
                    self._passthrough = True
                    self.installed = False
                return False

    def uninstall(self) -> None:
        """Give the window its system caption back. Call on the UI thread."""
        with self._lock:
            hwnd, api = self.hwnd, self.api
            was_installed = self.installed
            self.installed = False
            if not hwnd or api is None or not was_installed:
                return
            try:
                if api.is_window(hwnd):
                    if api.get_wndproc(hwnd) == self._proc_address and self._orig:
                        # ``_orig`` is kept: a stray call that still reaches
                        # our procedure must keep chaining to WinForms.
                        api.set_wndproc(hwnd, self._orig)
                        self._chained_hwnd = 0
                    else:
                        # Someone subclassed after us: we cannot unlink, so we
                        # stay in the chain as a pure pass-through.
                        self._passthrough = True
                        self._chained_hwnd = hwnd
                    try:
                        api.extend_frame(hwnd, 0)
                    except Exception:
                        pass
                    api.refresh_frame(hwnd)
            except Exception as exc:
                self._passthrough = True
                self.error = str(exc)
            log.info("custom title bar removed from hwnd=%s", hwnd)

    # --------------------------------------------------- window procedure
    def _wndproc(self, hwnd, msg, wparam, lparam):
        original = self._orig
        api = self.api
        try:
            if not self._passthrough and self.installed:
                if msg == WM_NCCALCSIZE and wparam:
                    return self._on_nccalcsize(hwnd, msg, wparam, lparam)
                if msg == WM_NCHITTEST:
                    return self._on_nchittest(hwnd, msg, wparam, lparam)
                if msg == WM_APP_TITLEBAR:
                    return self._on_app_message(hwnd, int(wparam or 0), int(lparam or 0))
                if msg == WM_DWMCOMPOSITIONCHANGED:
                    try:
                        # Round 30: a window material (win_backdrop) needs the frame over the whole client area
                        api.extend_frame(hwnd, -1 if getattr(self, "backdrop_active", False) else 1)
                    except Exception:
                        pass
            if msg == WM_NCDESTROY:
                self._on_destroy(hwnd)
        except Exception as exc:
            self._note_error(hwnd, exc)
        try:
            return api.call_proc(original, hwnd, msg, wparam, lparam)
        except Exception:  # pragma: no cover - nothing sane left to do
            return 0

    def _note_error(self, hwnd, exc: Exception) -> None:
        self._errors += 1
        if self._errors == 1 or self._errors == MAX_WNDPROC_ERRORS:
            log.warning("title bar window procedure error #%s: %s", self._errors, exc)
        if self._errors >= MAX_WNDPROC_ERRORS and not self._passthrough:
            self._passthrough = True
            self.error = f"window procedure errors: {exc}"
            try:
                self.api.post(hwnd, WM_APP_TITLEBAR, APP_UNINSTALL, 0)
            except Exception:
                pass

    def _on_nccalcsize(self, hwnd, msg, wparam, lparam):
        api = self.api
        rect = api.nccalc_rect(lparam)
        original_top = int(rect.top)
        result = api.call_proc(self._orig, hwnd, msg, wparam, lparam)
        style = api.style(hwnd)
        # pywebview's full screen drops WS_CAPTION/WS_THICKFRAME: leave it alone.
        if not (style & WS_CAPTION) or not (style & WS_THICKFRAME):
            return result
        maximized = api.is_zoomed(hwnd)
        frame = api.frame_thickness(hwnd) if maximized else 0
        edges = api.autohide_edges(hwnd) if maximized else ()
        left, top, right, bottom = nccalcsize_rect(
            (rect.left, rect.top, rect.right, rect.bottom), original_top,
            maximized=maximized, frame_thickness=frame, autohide_edges=edges)
        rect.left, rect.top, rect.right, rect.bottom = left, top, right, bottom
        return result

    def _on_nchittest(self, hwnd, msg, wparam, lparam):
        api = self.api
        result = api.call_proc(self._orig, hwnd, msg, wparam, lparam)
        x, y = unpack_point(lparam)
        maximized = api.is_zoomed(hwnd)
        dpi = api.dpi(hwnd)
        hit = top_hit_test(x, y, api.window_rect(hwnd), maximized=maximized,
                           border=scale_for_dpi(TOP_RESIZE_BORDER_96, dpi),
                           corner=scale_for_dpi(TOP_RESIZE_CORNER_96, dpi))
        if hit is not None:
            return hit
        # DefWindowProc still believes a caption exists and may answer
        # HTCAPTION/HTCLOSE for points that are now our client area.
        if result in (HTCAPTION, HTSYSMENU, HTMINBUTTON, HTMAXBUTTON, HTCLOSE) \
                and api.point_in_client(hwnd, x, y):
            return HTCLIENT
        return result

    def _on_app_message(self, hwnd, action: int, argument: int) -> int:
        api = self.api
        if action == APP_DRAG:
            hit = argument or HTCAPTION
            if hit != HTCAPTION and api.is_zoomed(hwnd):
                return 0
            if not api.primary_button_down():
                # The press already ended (a quick click): starting the
                # modal loop now would wait for a button that never comes.
                return 0
            api.release_capture()
            x, y = api.cursor_pos()
            api.send(hwnd, WM_NCLBUTTONDOWN, hit, pack_point(x, y))
            return 1
        if action == APP_SYSMENU:
            if argument == 1:
                left, top, _right, _bottom = api.window_rect(hwnd)
                inset = api.frame_thickness(hwnd) if api.is_zoomed(hwnd) else 0
                x = left + inset + scale_for_dpi(8, api.dpi(hwnd))
                y = top + inset + scale_for_dpi(CAPTION_HEIGHT_96, api.dpi(hwnd))
            else:
                x, y = api.cursor_pos()
            command = api.track_system_menu(hwnd, x, y, system_menu_states(api.is_zoomed(hwnd)))
            if command:
                api.post(hwnd, WM_SYSCOMMAND, command, 0)
            return 1
        if action == APP_UNINSTALL:
            self.uninstall()
            return 1
        return 0

    def _on_destroy(self, hwnd) -> None:
        # The HWND is going away (or WinForms recreates its handle): unlink
        # first so the original procedure finishes the teardown alone.
        api = self.api
        try:
            if api.get_wndproc(hwnd) == self._proc_address and self._orig:
                api.set_wndproc(hwnd, self._orig)
        except Exception:
            pass
        self.installed = False
        self._lost = True

    # ------------------------------------------------------- JS bridge API
    def _maybe_reattach(self) -> None:
        """Re-install after WinForms recreated the window handle."""
        if not (self._lost and self.mode == MODE_CUSTOM and self._hwnd_provider):
            return
        try:
            hwnd = int(self._hwnd_provider() or 0)
        except Exception:
            return
        if hwnd and hwnd != self.hwnd and self.api is not None and self.api.is_window(hwnd):
            self._lost = False
            self._install_on_ui()

    def state(self) -> dict:
        self._maybe_reattach()
        api, hwnd = self.api, self.hwnd
        maximized = fullscreen = False
        active = True
        double_click = 500
        if api is not None and hwnd:
            try:
                if api.is_window(hwnd):
                    maximized = api.is_zoomed(hwnd)
                    style = api.style(hwnd)
                    fullscreen = not (style & WS_CAPTION)
                    active = api.is_foreground(hwnd)
                    double_click = api.double_click_ms()
            except Exception:
                pass
        build = self.build or 0
        return {
            "ok": True,
            "supported": bool(self.supported),
            "mode": self.mode,
            "reason": self.reason,
            "forced": bool(self.forced),
            "installed": bool(self.installed and not self._passthrough),
            # Startup: the page may ask before the window was attached.
            "pending": bool(self.mode == MODE_CUSTOM and self.supported and not self._attached),
            "maximized": bool(maximized),
            "fullscreen": bool(fullscreen),
            "active": bool(active),
            "os": "win11" if build >= WINDOWS_11_BUILD else ("win10" if build else ""),
            "build": build,
            "doubleClickMs": double_click,
            "error": self.error,
        }

    def set_mode(self, mode: Any) -> dict:
        mode = normalize_mode(mode)
        if mode == MODE_CUSTOM and (self.forced or not self.supported):
            return self.state()
        self.mode = mode
        self.reason = "preference"
        if not self._attached:
            return self.state()
        if mode == MODE_CUSTOM:
            self._install_on_ui()
        else:
            self._uninstall_on_ui()
        return self.state()

    def start_drag(self, edge: Any = "caption") -> bool:
        hit = edge_to_hit(edge)
        if hit is None or not self.installed or self._passthrough or self.api is None:
            return False
        try:
            return self.api.post(self.hwnd, WM_APP_TITLEBAR, APP_DRAG, hit)
        except Exception:
            return False

    def system_menu(self, source: Any = "mouse") -> bool:
        if not self.installed or self._passthrough or self.api is None:
            return False
        try:
            return self.api.post(self.hwnd, WM_APP_TITLEBAR, APP_SYSMENU,
                                 1 if str(source) == "keyboard" else 0)
        except Exception:
            return False

    def command(self, command: Any) -> bool:
        if self.api is None or not self.hwnd:
            return False
        try:
            maximized = self.api.is_zoomed(self.hwnd)
            syscommand = command_to_syscommand(command, maximized)
            if syscommand is None:
                return False
            # WM_SYSCOMMAND goes through the same path as the native caption
            # buttons: animations, Snap state and pywebview's closing event
            # (MemuAI hides to the tray) all behave exactly as before.
            return self.api.post(self.hwnd, WM_SYSCOMMAND, syscommand, 0)
        except Exception:
            return False


class TitlebarBridge:
    """Methods exposed to the page as ``window.pywebview.api.*``.

    Kept deliberately small: pywebview exposes every public attribute of the
    ``js_api`` object, so the controller lives in a private attribute.
    """

    def __init__(self, controller: TitlebarController) -> None:
        self._controller = controller

    def titlebar_state(self) -> dict:
        return self._controller.state()

    def titlebar_set_mode(self, mode: str = MODE_CUSTOM) -> dict:
        return self._controller.set_mode(mode)

    def titlebar_drag(self, edge: str = "caption") -> bool:
        return self._controller.start_drag(edge)

    def titlebar_command(self, command: str = "") -> bool:
        return self._controller.command(command)

    def titlebar_system_menu(self, source: str = "mouse") -> bool:
        return self._controller.system_menu(source)

    # Round 30: the Windows 11 window material (app/win_backdrop.py; memuai.py sets controller.backdrop)
    def window_backdrop_state(self) -> dict:
        backdrop = getattr(self._controller, "backdrop", None)
        return backdrop.state() if backdrop is not None else {"available": False, "kinds": ["none"], "active": "none", "error": "unavailable"}

    def window_backdrop(self, kind: str = "none", dark: bool = True, background: str = "") -> dict:
        backdrop = getattr(self._controller, "backdrop", None)
        if backdrop is None:
            return self.window_backdrop_state()
        state = backdrop.apply(kind, dark, background)
        self._controller.backdrop_active = state.get("active", "none") != "none"
        self._controller.native_theme = (bool(dark), background)
        self._controller.native_surface = getattr(backdrop, "_native", None)
        return state


# ============================================================== startup
def read_preference(preferences_file: Any) -> str:
    try:
        import json
        from pathlib import Path
        data = json.loads(Path(preferences_file).read_text(encoding="utf-8"))
        return normalize_mode(data.get(PREF_KEY, MODE_CUSTOM))
    except Exception:
        return MODE_CUSTOM


def create_controller(preferences_file: Any, argv: Sequence[str] = (),
                      environ: Optional[dict] = None) -> TitlebarController:
    environ = os.environ if environ is None else environ
    return TitlebarController(
        preference=read_preference(preferences_file),
        cli_native=CLI_FLAG in list(argv or ()),
        env_value=environ.get(ENV_VAR),
    )


def winforms_ui_invoker(native) -> Callable[[Callable[[], Any]], Any]:
    """Run a callable on the WinForms UI thread of ``native`` and wait."""
    def invoke(fn: Callable[[], Any]) -> Any:
        if not getattr(native, "InvokeRequired", False):
            return fn()
        box: dict = {}

        def run() -> None:
            box["value"] = fn()
        try:
            from System import Action  # type: ignore
            native.Invoke(Action(run))
        except ImportError:
            from System import Func, Type  # type: ignore
            native.Invoke(Func[Type](lambda: run()))
        return box.get("value")
    return invoke
