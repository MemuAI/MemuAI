"""The animated companion as its own transparent desktop window (Windows, experimental).

Quick Ask can show the companion outside its popup: a frameless pywebview
window with the stage page ``/companion/stage?surface=desktop``. How it is
made transparent and click-through, in order of preference:

1. **DWM glass** — ``DwmExtendFrameIntoClientArea(-1)`` on a window whose
   GDI background is black, with WebView2's ``DefaultBackgroundColor``
   transparent: the page's alpha channel is composited by DWM (soft edges).
   The same trick Quick Ask uses for its acrylic backdrop.
2. **Colour key** (fallback when DWM refuses) — WinForms ``TransparencyKey``
   on a near-black colour; keyed pixels are transparent and click-through.

Click-through except on the model: the stage reports the model's box
(CSS px, ``/api/companion/window {action:"hit"}``) and the window region
(``SetWindowRgn``) is cut to that box plus a margin, so clicks anywhere
else reach the windows below. The window never takes focus
(``WS_EX_NOACTIVATE``), has no taskbar button (``WS_EX_TOOLWINDOW``), stays
on top and keeps its place per screen in ``companion/state.json``.

Round 23 — a companion you can carry around:

* ``detach`` (from the Quick Ask window): the inline companion was dragged past the window edge; this
  window appears under the cursor at the same grab point and a native drag loop follows the mouse
  (``GetCursorPos`` + ``GetAsyncKeyState(VK_LBUTTON)``) until the button is released;
* ``drag`` (from the stage page itself): the same loop — the window no longer relies on pywebview's
  drag region, so the drop point is known;
* dropping over the visible Quick Ask window **docks** it back (``{"docked": true}``, the window hides and
  ``state.json["quick"]["place"] = "inline"``);
* the place is remembered per monitor (``desktop_by_monitor[<work area>]``), the last one is reused when that
  monitor still exists; ``pin`` keeps it visible when Quick Ask hides.

Everything Win32 is wrapped: a failure hides this feature, never MemuAI.
The geometry helpers are pure and unit-tested off Windows.
"""
from __future__ import annotations

import json
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

log = logging.getLogger("memuai.companion.window")

TITLE = "MemuAI Companion"
CSS_SIZE = (300, 440)
MARGIN = 24
HIT_PAD = 10
QUICK_TITLE = "MemuAI Quick"
VK_LBUTTON = 0x01
DRAG_TIMEOUT = 120.0
DRAG_TICK = 0.008
MIN_SIZE, MAX_SIZE = (160, 220), (1200, 1700)

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW, WS_EX_APPWINDOW, WS_EX_NOACTIVATE = 0x00000080, 0x00040000, 0x08000000
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x0001, 0x0002, 0x0010, 0x0040
DWMWA_NCRENDERING_POLICY, DWMNCRP_DISABLED = 2, 1
DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_DONOTROUND = 33, 1
DWMWA_BORDER_COLOR, DWMWA_COLOR_NONE = 34, 0xFFFFFFFE
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4

def default_rect(work: Tuple[int, int, int, int], dpi: int,
                 css: Tuple[int, int] = CSS_SIZE) -> Tuple[int, int, int, int]:
    """Bottom-right corner of a work area, in physical px: (x, y, w, h)."""
    scale = max(1.0, dpi / 96.0) if dpi else 1.0
    w, h = int(round(css[0] * scale)), int(round(css[1] * scale))
    left, top, right, bottom = work
    w, h = min(w, max(1, right - left)), min(h, max(1, bottom - top))
    margin = int(round(MARGIN * scale))
    return max(left, right - w - margin), max(top, bottom - h - margin), w, h

def clamp_rect(rect: Tuple[int, int, int, int], work: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
    """Keep at least a grabbable part of the window on the given work area."""
    x, y, w, h = rect
    w = max(MIN_SIZE[0], min(MAX_SIZE[0], int(w)))
    h = max(MIN_SIZE[1], min(MAX_SIZE[1], int(h)))
    left, top, right, bottom = work
    keep = 80
    x = max(left - w + keep, min(right - keep, int(x)))
    y = max(top, min(bottom - keep, int(y)))
    return x, y, w, h

def contain_rect(rect: Tuple[int, int, int, int], work: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
    """Round 24: the WHOLE window inside the work area (shrunk to fit when the area is smaller).

    ``clamp_rect`` lets most of the window hang off the screen (only 80 px stay grabbable) — that is how the
    companion «disappeared» after a fast drag onto a monitor edge or after a monitor was unplugged. Every place
    the window settles at (end of a drag, show, the remembered place) now goes through this.
    """
    x, y, w, h = rect
    left, top, right, bottom = work
    aw, ah = max(1, right - left), max(1, bottom - top)
    w = max(min(MIN_SIZE[0], aw), min(MAX_SIZE[0], int(w), aw))
    h = max(min(MIN_SIZE[1], ah), min(MAX_SIZE[1], int(h), ah))
    x = max(left, min(right - w, int(x)))
    y = max(top, min(bottom - h, int(y)))
    return x, y, w, h

def scale_rect(rect: Tuple[int, int, int, int], step: int,
               work: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
    """Grow (step > 0) or shrink by 10 %, anchored at the bottom-right corner."""
    x, y, w, h = rect
    factor = 1.1 if step > 0 else 1 / 1.1
    nw = max(MIN_SIZE[0], min(MAX_SIZE[0], int(round(w * factor))))
    nh = max(MIN_SIZE[1], min(MAX_SIZE[1], int(round(h * factor))))
    return clamp_rect((x + w - nw, y + h - nh, nw, nh), work)

def hit_region(hit: Dict[str, Any], dpr: float, client: Tuple[int, int],
               bubble: Optional[Dict[str, Any]] = None) -> Optional[Tuple[int, int, int, int]]:
    """The model's box from the page (CSS px) → a client-area rectangle in physical px.

    ``None`` for a missing/empty box: the caller then keeps the whole window
    clickable rather than making the companion unreachable.
    """
    try:
        x, y, w, h = (float(hit[k]) for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return None
    if w <= 2 or h <= 2:
        return None
    scale = dpr if dpr and 0.5 <= dpr <= 6 else 1.0
    pad = HIT_PAD
    cw, ch = client
    left = max(0, int((x - pad) * scale))
    top = max(0, int((y - pad) * scale))
    right = min(cw, int((x + w + pad) * scale + 0.5))
    bottom = min(ch, int((y + h + pad) * scale + 0.5))
    if right - left < 4 or bottom - top < 4:
        return None

    tools_left = max(0, cw - int(round(40 * scale)))
    left, right = min(left, tools_left), max(right, cw if tools_left < cw else right)

    try:
        if isinstance(bubble, dict):
            bx, by, bw, bh = (float(bubble[k]) for k in ("x", "y", "w", "h"))
            if bw > 2 and bh > 2:
                left = max(0, min(left, int((bx - 2) * scale)))
                right = min(cw, max(right, int((bx + bw + 2) * scale + 0.5)))
                bottom = min(ch, max(bottom, int((by + bh + 2) * scale + 0.5)))
    except (KeyError, TypeError, ValueError):
        pass
    return left, 0, right, bottom

def monitor_key(work: Tuple[int, int, int, int]) -> str:
    """The key of a monitor in ``desktop_by_monitor``: its work area."""
    return ",".join(str(int(v)) for v in work)

def detach_rect(cursor: Tuple[int, int], grab: Tuple[float, float], size: Tuple[int, int],
                work: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
    """Where the desktop window appears when the inline companion is dragged out: the point the user holds
    (``grab`` — fractions 0…1 of the companion's box) stays under the cursor."""
    gx = min(1.0, max(0.0, float(grab[0])))
    gy = min(1.0, max(0.0, float(grab[1])))
    w, h = int(size[0]), int(size[1])
    return contain_rect((int(round(cursor[0] - gx * w)), int(round(cursor[1] - gy * h)), w, h), work)

def point_in(rect: Optional[Tuple[int, int, int, int]], point: Tuple[int, int]) -> bool:
    """``rect`` is (x, y, w, h)."""
    if not rect:
        return False
    x, y, w, h = rect
    return x <= point[0] < x + w and y <= point[1] < y + h

def pick_rect(state: Dict[str, Any], work_of: Callable[[int, int], Tuple[Tuple[int, int, int, int], int]],
              cursor: Tuple[int, int]) -> Optional[Tuple[int, int, int, int]]:
    """The remembered place. The last place when its monitor still exists; otherwise the place remembered for
    the monitor under the cursor; otherwise the last place pulled onto the nearest monitor; ``None`` — default."""
    def rect_of(value: Any) -> Optional[Tuple[int, int, int, int]]:
        if isinstance(value, dict) and all(isinstance(value.get(k), int) for k in ("x", "y", "w", "h")):
            return value["x"], value["y"], value["w"], value["h"]
        return None
    places = state.get("desktop_by_monitor") if isinstance(state.get("desktop_by_monitor"), dict) else {}
    last = rect_of(state.get("desktop"))
    if last:
        center = (last[0] + last[2] // 2, last[1] + last[3] // 2)
        work, _dpi = work_of(*center)
        if work[0] <= center[0] < work[2] and work[1] <= center[1] < work[3]:
            return contain_rect(last, work)
    work, _dpi = work_of(*cursor)
    here = rect_of(places.get(monitor_key(work)))
    if here:
        return contain_rect(here, work)
    if last:
        work, _dpi = work_of(last[0] + last[2] // 2, last[1] + last[3] // 2)
        return contain_rect(last, work)
    return None

class _Win32:
    """Private ctypes layer with explicit 64-bit-safe signatures."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes
        self.ctypes = ctypes
        HWND = ctypes.c_void_p

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                        ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]

        class MARGINS(ctypes.Structure):
            _fields_ = [("cxLeftWidth", ctypes.c_int), ("cxRightWidth", ctypes.c_int),
                        ("cyTopHeight", ctypes.c_int), ("cyBottomHeight", ctypes.c_int)]

        self.RECT, self.POINT, self.MONITORINFO, self.MARGINS = RECT, POINT, MONITORINFO, MARGINS
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

        def sig(dll, name, restype, *argtypes):
            fn = getattr(dll, name, None)
            if fn is None:
                return None
            fn.restype, fn.argtypes = restype, list(argtypes)
            return fn

        self.IsWindow = sig(user32, "IsWindow", wintypes.BOOL, HWND)
        self.IsWindowVisible = sig(user32, "IsWindowVisible", wintypes.BOOL, HWND)
        self.ShowWindow = sig(user32, "ShowWindow", wintypes.BOOL, HWND, ctypes.c_int)
        self.SetWindowPos = sig(user32, "SetWindowPos", wintypes.BOOL, HWND, HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_uint)
        self.GetWindowRect = sig(user32, "GetWindowRect", wintypes.BOOL, HWND, ctypes.POINTER(RECT))
        self.GetClientRect = sig(user32, "GetClientRect", wintypes.BOOL, HWND, ctypes.POINTER(RECT))
        self.GetDpiForWindow = sig(user32, "GetDpiForWindow", ctypes.c_uint, HWND)
        self.MonitorFromWindow = sig(user32, "MonitorFromWindow", ctypes.c_void_p, HWND, wintypes.DWORD)
        self.MonitorFromPoint = sig(user32, "MonitorFromPoint", ctypes.c_void_p, POINT, wintypes.DWORD)
        self.GetMonitorInfoW = sig(user32, "GetMonitorInfoW", wintypes.BOOL, ctypes.c_void_p,
                                   ctypes.POINTER(MONITORINFO))
        self.GetCursorPos = sig(user32, "GetCursorPos", wintypes.BOOL, ctypes.POINTER(POINT))

        self.GetPhysicalCursorPos = sig(user32, "GetPhysicalCursorPos", wintypes.BOOL, ctypes.POINTER(POINT))

        self.ClientToScreen = sig(user32, "ClientToScreen", wintypes.BOOL, HWND, ctypes.POINTER(POINT))
        self.SetThreadDpiAwarenessContext = sig(user32, "SetThreadDpiAwarenessContext", ctypes.c_void_p,
                                                ctypes.c_void_p)
        self.GetAsyncKeyState = sig(user32, "GetAsyncKeyState", ctypes.c_short, ctypes.c_int)
        self.FindWindowW = sig(user32, "FindWindowW", HWND, wintypes.LPCWSTR, wintypes.LPCWSTR)
        self.SetWindowRgn = sig(user32, "SetWindowRgn", ctypes.c_int, HWND, ctypes.c_void_p, wintypes.BOOL)
        self.CreateRectRgn = sig(gdi32, "CreateRectRgn", ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int)
        self.DeleteObject = sig(gdi32, "DeleteObject", wintypes.BOOL, ctypes.c_void_p)
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            self.GetWindowLong = sig(user32, "GetWindowLongPtrW", ctypes.c_ssize_t, HWND, ctypes.c_int)
            self.SetWindowLong = sig(user32, "SetWindowLongPtrW", ctypes.c_ssize_t, HWND, ctypes.c_int,
                                     ctypes.c_ssize_t)
        else:  # pragma: no cover - 32-bit Python
            self.GetWindowLong = sig(user32, "GetWindowLongW", ctypes.c_long, HWND, ctypes.c_int)
            self.SetWindowLong = sig(user32, "SetWindowLongW", ctypes.c_long, HWND, ctypes.c_int, ctypes.c_long)
        try:
            dwmapi = ctypes.WinDLL("dwmapi")
            self.DwmSetWindowAttribute = sig(dwmapi, "DwmSetWindowAttribute", ctypes.c_long, HWND,
                                             wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
            self.DwmExtendFrameIntoClientArea = sig(dwmapi, "DwmExtendFrameIntoClientArea", ctypes.c_long,
                                                    HWND, ctypes.POINTER(MARGINS))
        except OSError:
            self.DwmSetWindowAttribute = self.DwmExtendFrameIntoClientArea = None

    def dwm_int(self, hwnd: int, attribute: int, value: int) -> bool:
        if not self.DwmSetWindowAttribute:
            return False
        data = self.ctypes.c_uint(value & 0xFFFFFFFF)
        return self.DwmSetWindowAttribute(hwnd, attribute, self.ctypes.byref(data), self.ctypes.sizeof(data)) == 0

    def extend_frame(self, hwnd: int) -> bool:
        if not self.DwmExtendFrameIntoClientArea:
            return False
        margins = self.MARGINS(-1, -1, -1, -1)
        return self.DwmExtendFrameIntoClientArea(hwnd, self.ctypes.byref(margins)) == 0

    def window_rect(self, hwnd: int) -> Optional[Tuple[int, int, int, int]]:
        rect = self.RECT()
        if self.GetWindowRect(hwnd, self.ctypes.byref(rect)):
            return int(rect.left), int(rect.top), int(rect.right - rect.left), int(rect.bottom - rect.top)
        return None

    def client_size(self, hwnd: int) -> Tuple[int, int]:
        rect = self.RECT()
        if self.GetClientRect(hwnd, self.ctypes.byref(rect)):
            return int(rect.right - rect.left), int(rect.bottom - rect.top)
        return 0, 0

    def work_area_at(self, x: int, y: int) -> Tuple[Tuple[int, int, int, int], int]:
        """Work area and effective DPI of the monitor containing (x, y)."""
        point = self.POINT(int(x), int(y))
        monitor = self.MonitorFromPoint(point, 2)
        info = self.MONITORINFO()
        info.cbSize = self.ctypes.sizeof(self.MONITORINFO)
        work = (0, 0, 1920, 1080)
        if monitor and self.GetMonitorInfoW(monitor, self.ctypes.byref(info)):
            w = info.rcWork
            work = (int(w.left), int(w.top), int(w.right), int(w.bottom))
        dpi = 96
        try:
            shcore = self.ctypes.WinDLL("shcore")
            fn = shcore.GetDpiForMonitor
            fn.restype = self.ctypes.c_long
            fn.argtypes = [self.ctypes.c_void_p, self.ctypes.c_int, self.ctypes.POINTER(self.ctypes.c_uint),
                           self.ctypes.POINTER(self.ctypes.c_uint)]
            dx, dy = self.ctypes.c_uint(96), self.ctypes.c_uint(96)
            if monitor and fn(monitor, 0, self.ctypes.byref(dx), self.ctypes.byref(dy)) == 0:
                dpi = int(dx.value) or 96
        except Exception:
            pass
        return work, dpi

    def cursor(self) -> Tuple[int, int]:
        point = self.POINT()
        if self.GetCursorPos(self.ctypes.byref(point)):
            return int(point.x), int(point.y)
        return 0, 0

    def physical_cursor(self) -> Optional[Tuple[int, int]]:
        """The cursor in physical screen pixels, ``None`` when Windows refuses (secure desktop, no session)."""
        point = self.POINT()
        fn = self.GetPhysicalCursorPos or self.GetCursorPos
        if fn and fn(self.ctypes.byref(point)):
            return int(point.x), int(point.y)
        return None

    def client_origin(self, hwnd: int) -> Optional[Tuple[int, int]]:
        """Top-left corner of a window's client area in physical screen px, ``None`` for a gone window.

        The thread is switched to per-monitor-v2 for the call (and back), so the point is physical on every
        monitor whatever DPI awareness the calling thread has — the same space as ``GetPhysicalCursorPos``."""
        if not hwnd or not self.ClientToScreen or not self.IsWindow(hwnd):
            return None
        previous = None
        if self.SetThreadDpiAwarenessContext:
            previous = self.SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
        try:
            point = self.POINT(0, 0)
            if not self.ClientToScreen(hwnd, self.ctypes.byref(point)):
                return None
            return int(point.x), int(point.y)
        finally:
            if previous:
                self.SetThreadDpiAwarenessContext(previous)

    def button_down(self) -> bool:
        if not self.GetAsyncKeyState:
            return False
        return bool(int(self.GetAsyncKeyState(VK_LBUTTON)) & 0x8000)

    def quick_rect(self) -> Optional[Tuple[int, int, int, int]]:
        """The visible Quick Ask window's rectangle, if any."""
        if not self.FindWindowW:
            return None
        hwnd = self.FindWindowW(None, QUICK_TITLE)
        if not hwnd or not self.IsWindowVisible(hwnd):
            return None
        return self.window_rect(hwnd)

_CURSOR: "Optional[_Win32]" = None
_CURSOR_FAILED = False

CursorSample = Tuple[int, int, bool]

def relative_cursor(cursor: Optional[Tuple[int, int]],
                    origin: Optional[Tuple[int, int]]) -> Optional[CursorSample]:
    """``(x, y, relative)``: the cursor relative to a client-area origin (both physical px) when the origin is
    known, else the absolute screen point with ``relative=False`` (the stage then falls back to its own math)."""
    if cursor is None:
        return None
    if origin is None:
        return int(cursor[0]), int(cursor[1]), False
    return int(cursor[0]) - int(origin[0]), int(cursor[1]) - int(origin[1]), True

def cursor_reader(hwnd_of: Optional[Callable[[], int]] = None) -> Optional[Callable[[], Optional[CursorSample]]]:
    """Round 25: the global cursor for the companion's gaze («всегда следит за курсором»).

    Returns a cheap callable → ``(x, y, relative)`` or ``None`` (see ``relative_cursor``); ``None`` off Windows /
    when user32 fails. ``hwnd_of`` names the window that shows the stage (desktop companion, Quick Access or the
    main window — looked up on every read, the window may be recreated): the point is then relative to its client
    area in physical px, so the stage only divides by its ``devicePixelRatio``. ``window.screenX`` is in screen
    DIPs, which are NOT physical px / dpr when monitors have different scales (a 150 % primary + a 100 %
    secondary put the gaze ~850 px off). Read by ``/api/companion/cursor`` (an SSE stream the stage opens only
    while it is visible), so nothing polls while every companion is hidden."""
    global _CURSOR, _CURSOR_FAILED
    if sys.platform != "win32" or _CURSOR_FAILED:
        return None
    if _CURSOR is None:
        try:
            _CURSOR = _Win32()
        except Exception as exc:  # noqa: BLE001 - never break MemuAI over the gaze
            log.info("companion cursor feed unavailable: %s", exc)
            _CURSOR_FAILED = True
            return None
    api = _CURSOR

    def read() -> Optional[CursorSample]:
        cursor = api.physical_cursor()
        if cursor is None:
            return None
        try:
            hwnd = int(hwnd_of() or 0) if hwnd_of else 0
            origin = api.client_origin(hwnd) if hwnd else None
        except Exception:  # noqa: BLE001 - a lost window only drops back to absolute coordinates
            origin = None
        return relative_cursor(cursor, origin)

    return read

_CURRENT: "Optional[CompanionWindowManager]" = None

def current() -> "Optional[CompanionWindowManager]":
    """The manager of this process (set by its constructor) — Quick Ask uses it to restore a pinned companion."""
    return _CURRENT

def should_restore_pinned(quick: Dict[str, Any], prefs: Dict[str, Any]) -> bool:
    """Round 24: a companion pinned to the desktop comes back after a MemuAI restart (without opening Quick Ask)."""
    if not isinstance(quick, dict) or not isinstance(prefs, dict):
        return False
    if not prefs.get("companion_quick_enabled") or not prefs.get("quick_ask_enabled", True):
        return False
    return bool(quick.get("pinned")) and (quick.get("place") == "desktop" or prefs.get("companion_quick_mode") == "desktop")

class CompanionWindowManager:
    """Owns the desktop companion window. Off Windows it reports ``supported=False``."""

    def __init__(self, base_url: str, *, state_root: Callable[[], Path]):
        global _CURRENT
        _CURRENT = self
        self.base_url = base_url.rstrip("/")
        self.state_root = state_root
        self.window = None
        self.form = None
        self.hwnd = 0
        self.visible = False
        self.transparency = ""
        self._region: Optional[Tuple[int, int, int, int]] = None
        self._lock = threading.Lock()
        self._quitting = False
        self.api: Optional[_Win32] = None
        if sys.platform == "win32":
            try:
                self.api = _Win32()
            except Exception as exc:  # pragma: no cover - Windows only
                log.warning("companion window: Win32 unavailable: %s", exc)

    @property
    def supported(self) -> bool:
        return self.api is not None and not self._quitting

    def _load_rect(self) -> Optional[Tuple[int, int, int, int]]:
        from .companion_avatar import load_state
        try:
            return pick_rect(load_state(self.state_root()), self.api.work_area_at, self.api.cursor())
        except Exception:
            return None

    def _save_rect(self) -> None:
        if not self.hwnd or not self.api:
            return
        rect = self.api.window_rect(self.hwnd)
        if rect:
            from .companion_avatar import save_state
            work, _dpi = self.api.work_area_at(rect[0] + rect[2] // 2, rect[1] + rect[3] // 2)
            data = dict(zip(("x", "y", "w", "h"), rect))
            try:
                save_state(self.state_root(), {"desktop": data, "desktop_by_monitor": {monitor_key(work): data}})
            except OSError:
                pass

    def _save_quick(self, **patch: Any) -> None:
        from .companion_avatar import save_state
        try:
            save_state(self.state_root(), {"quick": patch})
        except OSError:
            pass

    def _quick_state(self) -> Dict[str, Any]:
        from .companion_avatar import load_state
        try:
            value = load_state(self.state_root()).get("quick")
        except Exception:
            value = None
        return value if isinstance(value, dict) else {}

    def _on_ui(self, fn: Callable[[], Any]) -> Any:
        form = self.form
        if form is None:
            return None
        try:
            from .win_titlebar import winforms_ui_invoker
            return winforms_ui_invoker(form)(fn)
        except Exception as exc:
            log.debug("companion window: UI call failed: %s", exc)
            return None

    def _ensure(self) -> bool:
        if not self.supported:
            return False
        if self.hwnd and self.api.IsWindow(self.hwnd):
            return True
        with self._lock:
            if self.hwnd and self.api.IsWindow(self.hwnd):
                return True
            try:
                import webview
            except Exception:
                return False
            api = self.api
            cx, cy = api.cursor()
            work, dpi = api.work_area_at(cx, cy)
            rect = self._load_rect()
            if rect:
                work, dpi = api.work_area_at(rect[0] + rect[2] // 2, rect[1] + rect[3] // 2)
                rect = contain_rect(rect, work)
            else:
                rect = default_rect(work, dpi)
            scale = max(1.0, dpi / 96.0)
            ready = threading.Event()
            options = dict(width=int(rect[2] / scale), height=int(rect[3] / scale), x=rect[0], y=rect[1],
                           frameless=True, easy_drag=False, on_top=True, hidden=True, focus=False,
                           resizable=False, shadow=False, transparent=True, background_color="#000000")
            window = None
            for _ in range(3):
                try:
                    window = webview.create_window(TITLE, self.base_url + "/companion/stage?surface=desktop", **options)
                    break
                except TypeError:

                    for key in ("focus", "shadow", "transparent", "easy_drag"):
                        options.pop(key, None)
                except Exception as exc:
                    log.warning("companion window: create_window failed: %s", exc)
                    return False
            if window is None:
                return False
            self.window = window
            window.events.closing += self._on_closing
            window.events.loaded += lambda *_: ready.set()
            deadline = time.monotonic() + 8
            form = None
            while time.monotonic() < deadline and form is None:
                form = getattr(window, "native", None)
                if form is None:
                    time.sleep(0.05)
            if form is None:
                return False
            self.form = form
            self.hwnd = int(self._on_ui(lambda: int(form.Handle.ToInt64())) or 0)
            if not self.hwnd:
                return False
            self._style()
            api.SetWindowPos(self.hwnd, HWND_TOPMOST, rect[0], rect[1], rect[2], rect[3], SWP_NOACTIVATE)
            ready.wait(10)
            return True

    def _style(self) -> None:
        api, hwnd, form = self.api, self.hwnd, self.form
        try:
            style = int(api.GetWindowLong(hwnd, GWL_EXSTYLE))
            api.SetWindowLong(hwnd, GWL_EXSTYLE, (style | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE) & ~WS_EX_APPWINDOW)
        except Exception:
            pass
        api.dwm_int(hwnd, DWMWA_NCRENDERING_POLICY, DWMNCRP_DISABLED)
        api.dwm_int(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_DONOTROUND)
        api.dwm_int(hwnd, DWMWA_BORDER_COLOR, DWMWA_COLOR_NONE)

        def glass() -> bool:
            try:
                from System.Drawing import Color  # type: ignore
                form.BackColor = Color.Black
                browser = getattr(form, "webview", None)
                if browser is not None:
                    browser.DefaultBackgroundColor = Color.Transparent
                return True
            except Exception:
                return False

        def colour_key() -> bool:
            try:
                from System.Drawing import Color  # type: ignore
                key = Color.FromArgb(255, 1, 0, 1)
                form.BackColor = key
                form.TransparencyKey = key
                browser = getattr(form, "webview", None)
                if browser is not None:
                    browser.DefaultBackgroundColor = Color.Transparent
                return True
            except Exception:
                return False

        if self._on_ui(glass) and api.extend_frame(hwnd):
            self.transparency = "dwm"
        elif self._on_ui(colour_key):
            self.transparency = "colorkey"
        else:
            self.transparency = "none"
        log.info("companion window: transparency=%s", self.transparency)

    def _on_closing(self, *_args) -> bool:
        if self._quitting:
            return True
        threading.Thread(target=self.hide, daemon=True).start()
        return False

    def show(self) -> Dict[str, Any]:
        if not self._ensure():
            return {"visible": False}
        self._keep_on_screen()
        self.api.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
        self.api.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
        self.visible = True
        self._push({"type": "visible", "on": True}, own=True)
        return {"visible": True, "transparency": self.transparency}

    def restore_pinned(self, prefs: Dict[str, Any]) -> bool:
        """Show the window at startup when the user pinned it to the desktop (see ``should_restore_pinned``)."""
        if not self.supported or not should_restore_pinned(self._quick_state(), prefs):
            return False
        return bool(self.show().get("visible"))

    def _keep_on_screen(self) -> None:
        """Round 24: never lose the companion — pull the whole window back onto the monitor it is (mostly) on."""
        try:
            rect = self.api.window_rect(self.hwnd) if self.hwnd else None
            if not rect:
                return
            work, _dpi = self.api.work_area_at(rect[0] + rect[2] // 2, rect[1] + rect[3] // 2)
            fixed = contain_rect(rect, work)
            if fixed != rect:
                self._set_region(None)
                self.api.SetWindowPos(self.hwnd, HWND_TOPMOST, fixed[0], fixed[1], fixed[2], fixed[3], SWP_NOACTIVATE)
        except Exception as exc:  # pragma: no cover - Windows only
            log.debug("companion window: keep on screen failed: %s", exc)

    def hide(self) -> Dict[str, Any]:
        if self.hwnd and self.api and self.api.IsWindow(self.hwnd):
            self._save_rect()
            self._push({"type": "visible", "on": False}, own=True)
            self.api.ShowWindow(self.hwnd, SW_HIDE)
        self.visible = False
        return {"visible": False}

    def scale(self, step: int) -> Dict[str, Any]:
        if not self.hwnd:
            return {}
        rect = self.api.window_rect(self.hwnd)
        if not rect:
            return {}
        work, _dpi = self.api.work_area_at(rect[0] + rect[2] // 2, rect[1] + rect[3] // 2)
        x, y, w, h = scale_rect(rect, 1 if step > 0 else -1, work)
        self._set_region(None)
        self.api.SetWindowPos(self.hwnd, HWND_TOPMOST, x, y, w, h, SWP_NOACTIVATE)
        self._save_rect()
        return {"rect": [x, y, w, h]}

    def follow(self, offset: Tuple[int, int]) -> Dict[str, Any]:
        """Move the window with the mouse until the left button is released (runs on a worker thread).

        Round 24: every step is kept fully inside the work area of the monitor under the cursor (the window
        can no longer be thrown off-screen), ``moved`` tells a click from a drag (a click on the desktop
        companion opens Quick Ask), and only a real drag can dock it back into Quick Ask.
        """
        api = self.api
        rect = api.window_rect(self.hwnd) if self.hwnd else None
        if not rect:
            return {}
        started = time.monotonic()
        start = api.cursor()
        last = None
        moved = False
        flags = SWP_NOSIZE | SWP_NOACTIVATE | 0x0004 | 0x4000
        w, h = rect[2], rect[3]
        while time.monotonic() - started < DRAG_TIMEOUT:
            point = api.cursor()
            if point != last:
                last = point
                if abs(point[0] - start[0]) + abs(point[1] - start[1]) > 4:
                    moved = True
                if moved:
                    work, _dpi = api.work_area_at(point[0], point[1])
                    x, y, _w, _h = contain_rect((point[0] - offset[0], point[1] - offset[1], w, h), work)
                    api.SetWindowPos(self.hwnd, None, x, y, 0, 0, flags)
            if not api.button_down():
                break
            time.sleep(DRAG_TICK)
        point = api.cursor()
        if moved and point_in(api.quick_rect(), point):

            self.hide()
            self._save_quick(place="inline")
            return {"docked": True, "moved": True}
        self._keep_on_screen()
        self._save_rect()
        self._save_quick(place="desktop")
        return {"docked": False, "moved": moved, "rect": list(api.window_rect(self.hwnd) or ())}

    def drag(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """The stage page grabbed the companion: native drag with snap-back into Quick Ask."""
        if not self.hwnd or not self.visible:
            return {}
        rect = self.api.window_rect(self.hwnd)
        if not rect:
            return {}
        cx, cy = self.api.cursor()
        return self.follow((cx - rect[0], cy - rect[1]))

    def detach(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """The inline companion in Quick Ask was dragged past the window edge: continue as this window."""
        if not self._ensure():
            return {"visible": False}
        api = self.api
        cursor = api.cursor()
        work, dpi = api.work_area_at(*cursor)
        try:
            grab = (float(body.get("gx", 0.5)), float(body.get("gy", 0.35)))
        except (TypeError, ValueError):
            grab = (0.5, 0.35)
        current = api.window_rect(self.hwnd) or default_rect(work, dpi)
        screen = body.get("screen") if isinstance(body.get("screen"), dict) else None
        if screen:

            try:
                dpr = max(0.5, min(6.0, float(body.get("dpr") or 1)))
                sx, sy, sw, sh = (float(screen[k]) for k in ("x", "y", "w", "h"))
            except (KeyError, TypeError, ValueError):
                return {"error": "bad screen rect"}
            work, _dpi = api.work_area_at(int((sx + sw / 2) * dpr), int((sy + sh / 2) * dpr))
            x, y, w, h = contain_rect((int(sx * dpr), int(sy * dpr), int(max(sw, 160) * dpr), int(max(sh, 220) * dpr)), work)
            if (w, h) != (current[2], current[3]):

                self._set_region(None)
            api.SetWindowPos(self.hwnd, HWND_TOPMOST, x, y, w, h, SWP_NOACTIVATE)
            shown = self.show()
            self._save_rect()
            self._save_quick(place="desktop")
            return {"docked": False, "rect": [x, y, w, h], **shown}
        x, y, w, h = detach_rect(cursor, grab, (current[2], current[3]), work)
        api.SetWindowPos(self.hwnd, HWND_TOPMOST, x, y, w, h, SWP_NOACTIVATE)
        shown = self.show()
        if not shown.get("visible"):
            return shown
        if not api.button_down():
            self._save_rect()
            self._save_quick(place="desktop")
            return {"docked": False, "rect": [x, y, w, h]}
        return self.follow((cursor[0] - x, cursor[1] - y))

    def pin(self, body: Dict[str, Any]) -> Dict[str, Any]:
        pinned = bool(body.get("pinned"))
        self._save_quick(pinned=pinned)
        self._push({"type": "pinned", "on": pinned}, own=True)
        return {"pinned": pinned}

    def _set_region(self, region: Optional[Tuple[int, int, int, int]]) -> None:
        if region == self._region or not self.hwnd:
            return
        self._region = region
        api = self.api
        if region is None:
            api.SetWindowRgn(self.hwnd, None, True)
            return
        handle = api.CreateRectRgn(*region)
        if handle and not api.SetWindowRgn(self.hwnd, handle, True):
            api.DeleteObject(handle)

    def hit(self, body: Dict[str, Any]) -> Dict[str, Any]:
        if not self.hwnd:
            return {}
        client = self.api.client_size(self.hwnd)
        try:
            dpr = float(body.get("dpr") or 1)
        except (TypeError, ValueError):
            dpr = 1.0
        region = hit_region(body.get("rect") or {}, dpr, client,
                            body.get("bubble") if isinstance(body.get("bubble"), dict) else None)
        self._set_region(region)

        rect = self.api.window_rect(self.hwnd)
        if rect and rect != getattr(self, "_saved_rect", None):
            self._saved_rect = rect
            self._save_rect()
        return {"region": list(region) if region else None}

    def _push(self, message: Dict[str, Any], own: bool = False) -> None:
        """Hand a message to the stage page. ``relay`` drops it while BroadcastChannel is busy (it is a copy of what
        the channel already delivered); ``own=True`` — the window's own state (shown/hidden, pinned), which nothing
        else delivers: before Round 26 «visible: off» was dropped while Quick Ask talked, so the hidden window kept
        drawing 30 frames a second."""
        window = self.window
        if window is None:
            return
        method = "receive" if own else "relay"
        try:
            window.evaluate_js("window.MemuStage && MemuStage.%s(%s)" % (method, json.dumps(message, ensure_ascii=False)))
        except Exception as exc:
            log.debug("companion window: evaluate_js failed: %s", exc)

    def handle(self, action: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """Entry point of ``POST /api/companion/window`` (runs on a worker thread)."""
        if action == "show":
            return self.show()
        if action == "hide":
            return self.hide()
        if action == "toggle":
            return self.hide() if self.visible else self.show()
        if action == "scale":
            return self.scale(int(body.get("step") or 0))
        if action == "drag":
            return self.drag(body)
        if action == "detach":
            return self.detach(body)
        if action == "pin":
            return self.pin(body)
        if action == "dock":

            self.hide()
            self._save_quick(place="inline")
            return {"docked": True}
        if action == "hit":
            return self.hit(body)
        if action == "event":
            event = body.get("event")
            if isinstance(event, dict) and self.visible and str(event.get("type") or "") in (
                    "speak", "emotion", "state", "gesture", "bubble", "prefs", "visible", "pinned"):
                self._push(event)
            return {}
        if action == "status":
            quick = self._quick_state()
            return {"visible": self.visible, "transparency": self.transparency,
                    "place": quick.get("place") or "", "pinned": bool(quick.get("pinned"))}
        return {"error": "unknown action"}

    def destroy(self) -> None:
        """MemuAI is quitting: remember the place and really close the window."""
        try:
            self._save_rect()
        except Exception:
            pass
        self._quitting = True
        window, self.window = self.window, None
        if window is not None:
            try:
                window.destroy()
            except Exception:
                pass
        self.hwnd = 0
