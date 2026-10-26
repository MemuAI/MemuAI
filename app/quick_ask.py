"""«Быстрый доступ» — the native side of MemuAI Quick Ask (Windows).

A global hotkey (``app/win_hotkey.py``, default Alt+Space) shows a small
always-on-top popup with its own page ``/quick/``. This module owns that
second pywebview window:

* created lazily but *kept warm*: built hidden a few seconds after the main
  window appeared, so the first Alt+Space is instant; never destroyed until
  MemuAI quits (Alt+F4 / Esc only hide it);
* frameless, always on top, not in the taskbar or Alt+Tab
  (``WS_EX_TOOLWINDOW``), Windows 11 rounded corners
  (``DWMWA_WINDOW_CORNER_PREFERENCE``) and, when enabled and supported,
  the acrylic/mica system backdrop (``DWMWA_SYSTEMBACKDROP_TYPE``) behind a
  transparent WebView2; otherwise the page paints its own translucent card;
* placed on the monitor chosen by the «Monitor» setting (Round 28: under the mouse
  cursor by default, or the active window's, or the primary one — a remembered
  spot is applied on THAT monitor), using that monitor's *work
  area* (taskbar on any edge) and DPI, in physical pixels — the maths is in
  :func:`app.quick_ask_core.place` and unit-tested with negative
  coordinates and mixed DPIs. The window is positioned twice, because moving
  a per-monitor-v2 window onto a screen with another DPI makes WinForms
  rescale it once on ``WM_DPICHANGED``;
* height follows the answer: the page reports its content height and the
  window grows/shrinks with a short eased animation, clamped to the work
  area (a long answer scrolls inside);
* Round 28: the user's panels and quick-action buttons are drawn anywhere on that
  monitor by the screen layer (:mod:`app.quick_layer`), shown and hidden with the
  popup (Ctrl+B toggles it);
* ``take_screenshot`` captures the screen *without* MemuAI:
  ``SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)`` is applied to the
  popup and the main window just for the capture (Windows 10 2004+; it does
  not hide anything from the user), with DWM cloaking as the fallback on
  older builds — the windows never flicker or lose focus.

Every Win32 call is wrapped; a failure degrades a feature and never breaks
MemuAI's startup. Off Windows, :class:`QuickAskManager` is inert and the
page still works in a normal browser (tests use it that way).
"""
from __future__ import annotations

import base64
import contextlib
import io
import logging
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from . import quick_ask_core as core
from . import quick_selection
from . import quick_warm

log = logging.getLogger("memuai.quick")

TITLE = "MemuAI Quick"
WARM_DELAY = 2.5
KEEP_CONVERSATION = 300

SW_HIDE, SW_SHOWNA, SW_SHOW = 0, 8, 5
HWND_TOPMOST, HWND_NOTOPMOST = -1, -2

PAGE_FRESH = 30.0
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0004, 0x0010
SWP_SHOWWINDOW, SWP_NOOWNERZORDER, SWP_ASYNCWINDOWPOS = 0x0040, 0x0200, 0x4000
GWL_EXSTYLE = -20
WS_EX_TOPMOST, WS_EX_TOOLWINDOW, WS_EX_APPWINDOW, WS_EX_NOACTIVATE = 0x00000008, 0x00000080, 0x00040000, 0x08000000
WDA_NONE, WDA_EXCLUDEFROMCAPTURE = 0x0, 0x11
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_BORDER_COLOR = 34
DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMWA_CLOAK = 13
DWMWCP_ROUND = 2
DWMWA_COLOR_NONE = 0xFFFFFFFE
DWMSBT = {"mica": 2, "acrylic": 3, "tabbed": 4}
MONITOR_DEFAULTTONEAREST = 2
MDT_EFFECTIVE_DPI = 0
CF_UNICODETEXT = 13
TEXT_FORMATS = {1, 7, 13, 16}
VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN, VK_RWIN = 0x11, 0x12, 0x10, 0x5B, 0x5C
VK_LBUTTON = 0x01

DWM_ROUND_RADIUS = 8
REGION_RADIUS = 12
KEYEVENTF_KEYUP = 0x0002

EXCLUDE_FROM_CAPTURE_BUILD = 19041
WINDOWS_11_BUILD = 22000
SYSTEM_BACKDROP_BUILD = 22621

def _state_file():
    from pathlib import Path
    from .pgdata import MEMU_DIR
    return Path(MEMU_DIR) / "quick_state.json"

def load_state() -> Dict[str, Any]:
    """Window state that is not a setting (the last width the user dragged to)."""
    import json
    try:
        data = json.loads(_state_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}

def save_state(patch: Dict[str, Any]) -> None:
    from .storage import save_json
    data = load_state()
    data.update(patch)
    save_json(str(_state_file()), data, backup=False)

def windows_build() -> int:
    if sys.platform != "win32":
        return 0
    try:
        return int(sys.getwindowsversion().build)
    except Exception:
        return 0

def backdrop_kind(preference: str, build: int) -> str:
    """Which DWM backdrop to request: ``acrylic``/``mica``/``""`` (CSS-only)."""
    if preference == "off" or build < SYSTEM_BACKDROP_BUILD:
        return ""
    return "mica" if preference == "mica" else "acrylic"

SLOW_CLOSE = 0.5

def slow_close_report(started: float, steps: List[Any], where: str, reason: str = "") -> Optional[str]:
    """Log a slow Escape/close: ``steps`` = [(name, monotonic time)] in order; returns the logged line (tests)."""
    total = (steps[-1][1] if steps else time.monotonic()) - started
    if total < SLOW_CLOSE:
        return None
    parts, last, worst = [], started, ("", -1.0)
    for name, at in steps:
        took = at - last
        parts.append("%s=%dms" % (name, round(took * 1000)))
        if took > worst[1]:
            worst = (name, took)
        last = at
    line = "quick ask: closing took %d ms (%s; slowest: %s%s): %s" % (
        round(total * 1000), where, worst[0] or "?", (", " + reason) if reason else "", ", ".join(parts) or "-")
    log.warning(line)
    return line

class QuickBridge:
    """Round 38: ``window.pywebview.api`` of the popup. Escape hides the window through pywebview's own message
    channel (a thread of its own per call) — not through the local HTTP server, whose worker threads may all be busy.
    Only public methods are exposed; the manager stays private."""

    def __init__(self, manager: Any) -> None:
        self._manager = manager

    def hide(self, restore_focus: bool = True) -> bool:
        return bool(self._manager.hide(restore_focus is not False))

    def resize(self, height: float, animate: bool = True) -> bool:
        """Round 40 (r40-lag): the window follows its content through the same channel — a busy local server (or a
        browser whose HTTP connections were all held) left it at its old height with the new view cut off."""
        try:
            return bool(self._manager.resize(float(height or 0), None, animate=animate is not False).get("ok"))
        except (TypeError, ValueError):
            return False

class Win32:
    """ctypes layer with explicit 64-bit-safe signatures (private WinDLLs)."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self.ctypes, self.wintypes = ctypes, wintypes
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
        self.MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, ctypes.c_void_p, ctypes.c_void_p,
                                                  ctypes.POINTER(RECT), ctypes.c_ssize_t)
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        def sig(dll, name, restype, *argtypes):
            fn = getattr(dll, name, None)
            if fn is None:
                return None
            fn.restype = restype
            fn.argtypes = list(argtypes)
            return fn

        self.GetCursorPos = sig(user32, "GetCursorPos", wintypes.BOOL, ctypes.POINTER(POINT))
        self.EnumDisplayMonitors = sig(user32, "EnumDisplayMonitors", wintypes.BOOL, ctypes.c_void_p,
                                       ctypes.c_void_p, self.MONITORENUMPROC, ctypes.c_ssize_t)
        self.GetMonitorInfoW = sig(user32, "GetMonitorInfoW", wintypes.BOOL, ctypes.c_void_p,
                                   ctypes.POINTER(MONITORINFO))
        self.SetWindowPos = sig(user32, "SetWindowPos", wintypes.BOOL, HWND, HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_uint)
        self.ShowWindow = sig(user32, "ShowWindow", wintypes.BOOL, HWND, ctypes.c_int)

        self.ShowWindowAsync = sig(user32, "ShowWindowAsync", wintypes.BOOL, HWND, ctypes.c_int)
        self.IsWindow = sig(user32, "IsWindow", wintypes.BOOL, HWND)
        self.IsWindowVisible = sig(user32, "IsWindowVisible", wintypes.BOOL, HWND)
        self.IsIconic = sig(user32, "IsIconic", wintypes.BOOL, HWND)
        self.GetForegroundWindow = sig(user32, "GetForegroundWindow", HWND)
        self.GetWindowRect = sig(user32, "GetWindowRect", wintypes.BOOL, HWND, ctypes.POINTER(RECT))
        self.GetAncestor = sig(user32, "GetAncestor", HWND, HWND, ctypes.c_uint)
        self.GetSystemMetrics = sig(user32, "GetSystemMetrics", ctypes.c_int, ctypes.c_int)
        self.SetForegroundWindow = sig(user32, "SetForegroundWindow", wintypes.BOOL, HWND)
        self.BringWindowToTop = sig(user32, "BringWindowToTop", wintypes.BOOL, HWND)
        self.SetFocus = sig(user32, "SetFocus", HWND, HWND)
        self.GetWindowThreadProcessId = sig(user32, "GetWindowThreadProcessId", wintypes.DWORD, HWND,
                                            ctypes.c_void_p)
        self.AttachThreadInput = sig(user32, "AttachThreadInput", wintypes.BOOL, wintypes.DWORD,
                                     wintypes.DWORD, wintypes.BOOL)
        self.AllowSetForegroundWindow = sig(user32, "AllowSetForegroundWindow", wintypes.BOOL, wintypes.DWORD)
        self.GetCurrentThreadId = sig(kernel32, "GetCurrentThreadId", wintypes.DWORD)
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            self.GetWindowLong = sig(user32, "GetWindowLongPtrW", ctypes.c_ssize_t, HWND, ctypes.c_int)
            self.SetWindowLong = sig(user32, "SetWindowLongPtrW", ctypes.c_ssize_t, HWND, ctypes.c_int,
                                     ctypes.c_ssize_t)
        else:  # pragma: no cover - 32-bit Python
            self.GetWindowLong = sig(user32, "GetWindowLongW", ctypes.c_long, HWND, ctypes.c_int)
            self.SetWindowLong = sig(user32, "SetWindowLongW", ctypes.c_long, HWND, ctypes.c_int, ctypes.c_long)
        self.SetWindowDisplayAffinity = sig(user32, "SetWindowDisplayAffinity", wintypes.BOOL, HWND,
                                            wintypes.DWORD)
        self.GetDpiForWindow = sig(user32, "GetDpiForWindow", ctypes.c_uint, HWND)
        self.GetAsyncKeyState = sig(user32, "GetAsyncKeyState", ctypes.c_short, ctypes.c_int)
        self.keybd_event = sig(user32, "keybd_event", None, ctypes.c_ubyte, ctypes.c_ubyte, wintypes.DWORD,
                               ctypes.c_size_t)
        self.OpenClipboard = sig(user32, "OpenClipboard", wintypes.BOOL, HWND)
        self.CloseClipboard = sig(user32, "CloseClipboard", wintypes.BOOL)
        self.EmptyClipboard = sig(user32, "EmptyClipboard", wintypes.BOOL)
        self.GetClipboardData = sig(user32, "GetClipboardData", ctypes.c_void_p, ctypes.c_uint)
        self.SetClipboardData = sig(user32, "SetClipboardData", ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p)
        self.EnumClipboardFormats = sig(user32, "EnumClipboardFormats", ctypes.c_uint, ctypes.c_uint)
        self.GetClipboardSequenceNumber = sig(user32, "GetClipboardSequenceNumber", wintypes.DWORD)
        self.GlobalAlloc = sig(kernel32, "GlobalAlloc", ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t)
        self.GlobalLock = sig(kernel32, "GlobalLock", ctypes.c_void_p, ctypes.c_void_p)
        self.GlobalUnlock = sig(kernel32, "GlobalUnlock", wintypes.BOOL, ctypes.c_void_p)
        self.GlobalFree = sig(kernel32, "GlobalFree", ctypes.c_void_p, ctypes.c_void_p)
        try:
            shcore = ctypes.WinDLL("shcore")
            self.GetDpiForMonitor = sig(shcore, "GetDpiForMonitor", ctypes.c_long, ctypes.c_void_p,
                                        ctypes.c_int, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint))
        except OSError:
            self.GetDpiForMonitor = None
        try:
            dwmapi = ctypes.WinDLL("dwmapi")
            self.DwmSetWindowAttribute = sig(dwmapi, "DwmSetWindowAttribute", ctypes.c_long, HWND,
                                             wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
            self.DwmExtendFrameIntoClientArea = sig(dwmapi, "DwmExtendFrameIntoClientArea", ctypes.c_long,
                                                    HWND, ctypes.POINTER(MARGINS))
            self.DwmFlush = sig(dwmapi, "DwmFlush", ctypes.c_long)
        except OSError:
            self.DwmSetWindowAttribute = self.DwmExtendFrameIntoClientArea = self.DwmFlush = None
        try:
            gdi32 = ctypes.WinDLL("gdi32")
            self.CreateRoundRectRgn = sig(gdi32, "CreateRoundRectRgn", ctypes.c_void_p, ctypes.c_int,
                                          ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int)
        except OSError:
            self.CreateRoundRectRgn = None
        self.SetWindowRgn = sig(user32, "SetWindowRgn", ctypes.c_int, HWND, ctypes.c_void_p, wintypes.BOOL)

    def cursor(self) -> tuple:
        point = self.POINT()
        if self.GetCursorPos(self.ctypes.byref(point)):
            return int(point.x), int(point.y)
        return 0, 0

    def monitors(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        ctypes = self.ctypes

        def callback(handle, _dc, _rect, _data):
            info = self.MONITORINFO()
            info.cbSize = ctypes.sizeof(self.MONITORINFO)
            if self.GetMonitorInfoW(handle, ctypes.byref(info)):
                dpi = 96
                if self.GetDpiForMonitor is not None:
                    x, y = ctypes.c_uint(96), ctypes.c_uint(96)
                    if self.GetDpiForMonitor(handle, MDT_EFFECTIVE_DPI, ctypes.byref(x), ctypes.byref(y)) == 0:
                        dpi = int(x.value) or 96
                rect, work = info.rcMonitor, info.rcWork
                out.append({"rect": (rect.left, rect.top, rect.right, rect.bottom),
                            "work": (work.left, work.top, work.right, work.bottom),
                            "dpi": dpi, "primary": bool(info.dwFlags & 1)})
            return True

        proc = self.MONITORENUMPROC(callback)
        self.EnumDisplayMonitors(None, None, proc, 0)
        return out

    def window_rect(self, hwnd: int) -> Optional[tuple]:
        rect = self.RECT()
        if self.GetWindowRect and self.GetWindowRect(hwnd, self.ctypes.byref(rect)):
            return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
        return None

    def button_down(self) -> bool:
        """The *primary* mouse button (GetAsyncKeyState reads physical buttons: honour swapped buttons)."""
        vk = VK_LBUTTON
        try:
            if self.GetSystemMetrics and self.GetSystemMetrics(23):
                vk = 0x02
        except Exception:
            pass
        return bool(self.GetAsyncKeyState(vk) & 0x8000)

    def dwm_int(self, hwnd: int, attribute: int, value: int) -> bool:
        if not self.DwmSetWindowAttribute:
            return False
        data = self.ctypes.c_uint(value & 0xFFFFFFFF)
        return self.DwmSetWindowAttribute(hwnd, attribute, self.ctypes.byref(data),
                                          self.ctypes.sizeof(data)) == 0

    def extend_frame(self, hwnd: int, value: int) -> bool:
        if not self.DwmExtendFrameIntoClientArea:
            return False
        margins = self.MARGINS(value, value, value, value)
        return self.DwmExtendFrameIntoClientArea(hwnd, self.ctypes.byref(margins)) == 0

    def take_foreground(self, hwnd: int) -> bool:
        """SetForegroundWindow, and the documented AttachThreadInput workaround if Windows refuses."""
        try:
            self.AllowSetForegroundWindow(0xFFFFFFFF)
        except Exception:
            pass
        self.SetForegroundWindow(hwnd)
        if int(self.GetForegroundWindow() or 0) == int(hwnd):
            return True
        try:
            foreground = self.GetForegroundWindow()
            other = self.GetWindowThreadProcessId(foreground, None) if foreground else 0
            mine = self.GetCurrentThreadId()
            if other and other != mine and self.AttachThreadInput(mine, other, True):
                try:
                    self.BringWindowToTop(hwnd)
                    self.SetForegroundWindow(hwnd)
                    self.SetFocus(hwnd)
                finally:
                    self.AttachThreadInput(mine, other, False)
        except Exception:
            pass
        return int(self.GetForegroundWindow() or 0) == int(hwnd)

    def clipboard_formats(self) -> set:
        formats, fmt = set(), 0
        if not self.OpenClipboard(None):
            return {-1}
        try:
            while True:
                fmt = self.EnumClipboardFormats(fmt)
                if not fmt:
                    break
                formats.add(int(fmt))
        finally:
            self.CloseClipboard()
        return formats

    def get_text(self) -> Optional[str]:
        for _ in range(5):
            if self.OpenClipboard(None):
                break
            time.sleep(0.02)
        else:
            return None
        try:
            handle = self.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return ""
            pointer = self.GlobalLock(handle)
            if not pointer:
                return ""
            try:
                return self.ctypes.wstring_at(pointer)
            finally:
                self.GlobalUnlock(handle)
        finally:
            self.CloseClipboard()

    def set_text(self, text: Optional[str]) -> bool:
        for _ in range(5):
            if self.OpenClipboard(None):
                break
            time.sleep(0.02)
        else:
            return False
        try:
            self.EmptyClipboard()
            if text is None:
                return True
            data = (str(text) + "\0").encode("utf-16-le")
            handle = self.GlobalAlloc(0x0002, len(data))
            if not handle:
                return False
            pointer = self.GlobalLock(handle)
            if not pointer:
                self.GlobalFree(handle)
                return False
            self.ctypes.memmove(pointer, data, len(data))
            self.GlobalUnlock(handle)
            if not self.SetClipboardData(CF_UNICODETEXT, handle):
                self.GlobalFree(handle)
                return False
            return True
        finally:
            self.CloseClipboard()

    def modifiers_down(self) -> bool:
        return any(self.GetAsyncKeyState(vk) & 0x8000 for vk in (VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN, VK_RWIN))

    def chord(self, *vks: int) -> None:
        for vk in vks:
            self.keybd_event(vk, 0, 0, 0)
        for vk in reversed(vks):
            self.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)

class QuickAskManager:
    """The popup window, its hotkey and the screen capture. Thread-safe."""

    def __init__(self, base_url: str, *, load_prefs: Callable[[], Dict[str, Any]],
                 save_prefs: Optional[Callable[[Dict[str, Any]], Any]] = None,
                 main_hwnd: Callable[[], int] = lambda: 0, show_main: Callable[[], Any] = lambda: None,
                 main_window: Callable[[], Any] = lambda: None) -> None:
        self.base_url = base_url.rstrip("/")
        self.load_prefs = load_prefs
        self.save_prefs = save_prefs
        self.main_hwnd = main_hwnd
        self.show_main = show_main
        self.main_window = main_window
        self.native_ok = sys.platform == "win32"
        self.build = windows_build()
        self.api: Optional[Win32] = None
        self.window = None
        self.form = None
        self.hwnd = 0
        self.visible = False
        self.pinned = False

        self.lowered = False
        self.geometry: Dict[str, Any] = {}
        self.previous_hwnd = 0
        self.shown_at = 0.0
        self.hidden_at = 0.0
        self.backdrop = ""
        self.rounded = False
        self._lock = threading.RLock()
        self._create_lock = threading.Lock()
        self._anim_token = 0
        self._selection_token = 0
        self._quitting = False
        self._releasing = False
        self._created_at = 0.0
        self._busy_until = 0.0
        self.handoff: Optional[Dict[str, Any]] = None

        self.corner = "none"
        self.radius_css = 0
        self.css_height = float(core.COLLAPSED_HEIGHT)
        self.position_mode = "last"
        self._dragging = False
        self.page: Optional[Dict[str, Any]] = None
        self._page_token = 0
        self._capture_cancel: Optional[threading.Event] = None
        self.surface = ""
        self.layer = None
        self._layer_token = 0
        try:
            self.pinned = bool(load_state().get("pinned"))
        except Exception:
            self.pinned = False
        self.hotkey = None
        self.hotkey_status: Dict[str, Any] = {"ok": False, "hotkey": "", "code": "not_started", "error": ""}
        if self.native_ok:
            try:
                self.api = Win32()
            except Exception as exc:
                log.warning("quick ask: Win32 layer unavailable: %s", exc)
                self.native_ok = False

    def start(self) -> None:
        """Called once the main window is shown: hotkey now, warm window a bit later."""
        if not self.native_ok:
            return
        status = self.apply_preferences(self.load_prefs())
        if status.get("code") == "busy":

            threading.Thread(target=self._retry_hotkey, name="memuai-quick-retry", daemon=True).start()
        threading.Thread(target=self._warm, name="memuai-quick-warm", daemon=True).start()
        threading.Thread(target=self._release_loop, name="memuai-quick-release", daemon=True).start()

    def _release_loop(self) -> None:
        """Round 36 (app/quick_warm.py): close the long-hidden popup (and layer) so their WebView2 renderers exit."""
        while not self._quitting:
            time.sleep(quick_warm.CHECK_EVERY)
            try:
                prefs = self.load_prefs()
                since = self.hidden_at or self._created_at
                if quick_warm.should_release(prefs, has_window=bool(self.hwnd), visible=self.visible,
                                             pinned=self.pinned, hidden_for=time.monotonic() - since,
                                             busy=time.monotonic() < self._busy_until):
                    self.release_window()
            except Exception as exc:
                log.debug("quick ask: release check failed: %s", exc)

    def release_window(self) -> bool:
        """Close the hidden popup and its screen layer; the next ``show`` creates them again."""
        with self._create_lock, self._lock:
            if self.visible or self._quitting or not self.hwnd:
                return False
            layer, self.layer = getattr(self, "layer", None), None
            if layer is not None:
                try:
                    layer.destroy()
                except Exception:
                    pass
            window, self.window = self.window, None
            self._releasing = True
            self.form = None
            self.hwnd = 0
            self.hidden_at = 0.0

        try:
            if window is not None:
                window.destroy()
        except Exception as exc:
            log.debug("quick ask: release failed: %s", exc)
        finally:
            self._releasing = False
        log.info("quick ask: hidden window released (WebView2 renderer freed)")
        return True

    def _retry_hotkey(self) -> None:
        for delay in (1.0, 2.0, 4.0):
            time.sleep(delay)
            if self._quitting:
                return
            try:
                status = self.apply_preferences(self.load_prefs())
            except Exception:
                return
            if status.get("code") != "busy":
                return

    def _warm(self) -> None:
        time.sleep(WARM_DELAY)
        try:
            prefs = self.load_prefs()

            if prefs.get("quick_ask_enabled", True) and quick_warm.warm_at_start(prefs):
                self.ensure_window()
                if prefs.get("quick_ask_selection", True):
                    quick_selection.warm()

                if self.layer_settings()["on"]:
                    layer = self.ensure_layer()
                    if layer is not None and layer.available():
                        layer.ensure_window()

                if self.pinned and load_state().get("pinned_open"):
                    self.show("restore")
        except Exception as exc:
            log.warning("quick ask: warm window failed: %s", exc)

        try:
            from . import companion_window
            manager = companion_window.current()
            if manager is not None:
                manager.restore_pinned(self.load_prefs())
        except Exception as exc:
            log.debug("quick ask: pinned companion restore failed: %s", exc)

    def apply_preferences(self, prefs: Dict[str, Any]) -> Dict[str, Any]:
        """(Re)register the hotkey after Settings changed. Returns the hotkey status."""
        lang = "en" if prefs.get("language") == "en" else "ru"
        if not self.native_ok:
            self.hotkey_status = {"ok": False, "hotkey": "", "code": "unsupported",
                                  "error": "Quick Ask works only in the MemuAI Windows app" if lang == "en"
                                  else "Быстрый доступ работает только в Windows-приложении MemuAI"}
            return self.hotkey_status
        from .win_hotkey import HotkeyListener
        if self.hotkey is None:
            self.hotkey = HotkeyListener(self.toggle, lang=lang)
        self.hotkey.lang = lang
        if not prefs.get("quick_ask_enabled", True):
            self.hotkey_status = self.hotkey.clear()
            self.hotkey_status["code"] = "off"
            if self.visible:
                self.hide()
            return self.hotkey_status
        self.hotkey_status = self.hotkey.set_hotkey(str(prefs.get("quick_ask_hotkey") or core.DEFAULT_HOTKEY))
        if self.hwnd:
            self._apply_backdrop(prefs)
        return self.hotkey_status

    def status(self) -> Dict[str, Any]:
        from .win_hotkey import running_processes
        prefs = self.load_prefs()
        hotkey = str(prefs.get("quick_ask_hotkey") or core.DEFAULT_HOTKEY)
        names = running_processes() if self.native_ok else []
        current = dict(self.hotkey.status) if self.hotkey else dict(self.hotkey_status)
        if current.get("code") == "busy" or current.get("rejected"):

            copies = sum(1 for name in names if name in ("memuai.exe", "memuai"))
            if copies > 1:
                current["other_instance"] = True
        return {"native": self.native_ok, "build": self.build, "hotkey": current,
                "suspended": bool(self.hotkey and self.hotkey.suspended),
                "pinned": self.pinned, "corner": self.corner,
                "conflicts": core.conflicts_for(hotkey, names) if prefs.get("quick_ask_enabled", True) else [],
                "warnings": core.hotkey_warnings(hotkey, "en" if prefs.get("language") == "en" else "ru"),
                "window": bool(self.hwnd), "visible": self.visible, "backdrop": self.backdrop,
                "exclude_from_capture": self.build >= EXCLUDE_FROM_CAPTURE_BUILD}

    def probe(self, hotkey: str) -> Dict[str, Any]:
        if not self.native_ok:
            return {"ok": False, "code": "unsupported", "hotkey": hotkey, "error": ""}
        from .win_hotkey import HotkeyListener
        if self.hotkey is None:
            self.hotkey = HotkeyListener(self.toggle)
        return self.hotkey.probe(hotkey)

    def suspend_hotkey(self, seconds: float = 20.0) -> Dict[str, Any]:
        """Settings' capture field is active: release our hotkey so it can be pressed there."""
        if not self.native_ok or self.hotkey is None:
            return {"ok": False, "code": "unsupported"}
        return self.hotkey.suspend(seconds)

    def resume_hotkey(self) -> Dict[str, Any]:
        if not self.native_ok or self.hotkey is None:
            return {"ok": False, "code": "unsupported"}
        return self.hotkey.resume()

    def capture_combo(self, timeout: float = 15.0) -> Dict[str, Any]:
        """Poll the keyboard for the combination the user presses in Settings (see ``ComboCapture``)."""
        if not self.native_ok or not self.api:
            return {"ok": False, "code": "unsupported", "hotkey": ""}
        from .win_hotkey import ComboCapture
        if self._capture_cancel is not None:
            self._capture_cancel.set()
        cancel = self._capture_cancel = threading.Event()
        api = self.api
        main = int(self.main_hwnd() or 0)

        def ours() -> bool:
            foreground = int(api.GetForegroundWindow() or 0)
            return bool(foreground) and foreground in (main, self.hwnd)
        try:
            return ComboCapture(lambda vk: bool(api.GetAsyncKeyState(vk) & 0x8000),
                                foreground_ok=ours).run(timeout, cancel)
        finally:
            if self._capture_cancel is cancel:
                self._capture_cancel = None

    def cancel_capture(self) -> None:
        if self._capture_cancel is not None:
            self._capture_cancel.set()

    def destroy(self) -> None:
        """MemuAI is quitting: release the hotkey and really close the popup."""
        self._quitting = True
        self._disarm_escape()
        try:
            if self.hotkey:
                self.hotkey.stop()
        except Exception:
            pass
        if getattr(self, "layer", None) is not None:
            try:
                self.layer.destroy()
            except Exception:
                pass
        window, self.window = self.window, None
        if window is not None:
            try:
                window.destroy()
            except Exception:
                pass
        self.hwnd = 0

    def ensure_window(self) -> bool:
        if not self.native_ok or self._quitting:
            return False
        if self.hwnd and self.api and self.api.IsWindow(self.hwnd):
            return True
        with self._create_lock:
            if self.hwnd and self.api.IsWindow(self.hwnd):
                return True
            try:
                import webview
            except Exception:
                return False
            prefs = self.load_prefs()
            dark = prefs.get("theme") != "light"
            ready = threading.Event()
            options = dict(width=core.clamp_width(load_state().get("width", core.DEFAULT_WIDTH)),
                           height=core.COLLAPSED_HEIGHT, frameless=True, easy_drag=False, on_top=True,
                           hidden=True, focus=True, resizable=True, shadow=True, min_size=(320, 80),
                           background_color="#1b1c20" if dark else "#f7f6f3", js_api=QuickBridge(self))
            window = None
            for attempt in range(2):
                try:
                    window = webview.create_window(TITLE, self.base_url + "/quick/?native=1", **options)
                    break
                except TypeError:

                    for key in ("focus", "shadow", "easy_drag"):
                        options.pop(key, None)
                except Exception as exc:
                    log.warning("quick ask: create_window failed: %s", exc)
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
            self._created_at = time.monotonic()
            self._style_window(prefs)
            try:
                from .desktop import grant_local_permissions
                grant_local_permissions(form, self.base_url)
            except Exception:
                pass
            ready.wait(10)
            return True

    def _on_ui(self, fn: Callable[[], Any]) -> Any:
        form = self.form
        if form is None:
            return None
        try:
            from .win_titlebar import winforms_ui_invoker
            return winforms_ui_invoker(form)(fn)
        except Exception as exc:
            log.debug("quick ask: UI call failed: %s", exc)
            return None

    def _on_closing(self, *_args) -> bool:
        if self._quitting or self._releasing:
            return True

        threading.Thread(target=self.hide, daemon=True).start()
        return False

    def _style_window(self, prefs: Dict[str, Any]) -> None:
        api, hwnd = self.api, self.hwnd
        try:
            style = int(api.GetWindowLong(hwnd, GWL_EXSTYLE))
            api.SetWindowLong(hwnd, GWL_EXSTYLE, (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)
        except Exception:
            pass
        try:
            api.dwm_int(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, 0 if prefs.get("theme") == "light" else 1)
            if self.build >= WINDOWS_11_BUILD:
                self.rounded = api.dwm_int(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND)

                api.dwm_int(hwnd, DWMWA_BORDER_COLOR, DWMWA_COLOR_NONE)
        except Exception:
            pass

        if self.rounded:
            self.corner, self.radius_css = "dwm", DWM_ROUND_RADIUS
        elif api.CreateRoundRectRgn and api.SetWindowRgn:
            self.corner, self.radius_css = "region", REGION_RADIUS
        else:
            self.corner, self.radius_css = "none", 0
        self._apply_backdrop(prefs)

    def _apply_backdrop(self, prefs: Dict[str, Any]) -> None:
        kind = backdrop_kind(str(prefs.get("quick_ask_backdrop") or "auto"), self.build)
        api, hwnd, form = self.api, self.hwnd, self.form
        if not hwnd:
            return
        applied = ""
        if kind:
            def transparent() -> bool:
                try:
                    from System.Drawing import Color  # type: ignore
                    form.BackColor = Color.Black
                    browser = getattr(form, "webview", None)
                    if browser is not None:
                        browser.DefaultBackgroundColor = Color.Transparent
                    return True
                except Exception:
                    return False
            if self._on_ui(transparent) and api.extend_frame(hwnd, -1) \
                    and api.dwm_int(hwnd, DWMWA_SYSTEMBACKDROP_TYPE, DWMSBT[kind]):
                applied = kind
        if not applied:
            api.dwm_int(hwnd, DWMWA_SYSTEMBACKDROP_TYPE, 1)
        self.backdrop = applied

    def _round_region(self, w: int, h: int, scale: float) -> None:
        """No DWM rounding (Windows 10, or DWM refused): clip the popup to a rounded region.

        ``CreateRoundRectRgn`` takes the *diameter* of the corner ellipse, so
        it is twice the CSS radius the page uses (``radius_css``) at this DPI.
        The region is recreated for every size, otherwise a grown window keeps
        the old clip and shows square corners (or cuts the answer off).
        """
        if self.corner != "region" or not self.api.CreateRoundRectRgn:
            return
        diameter = core.region_diameter(self.radius_css, scale)
        region = self.api.CreateRoundRectRgn(0, 0, int(w) + 1, int(h) + 1, diameter, diameter)
        if region:
            self.api.SetWindowRgn(self.hwnd, region, True)

    def set_surface(self, color: str) -> bool:
        """The page's opaque card colour → the window's own background (Round 22).

        Painted by WinForms/WebView2 before the page and at the anti-aliased
        corner pixels: when it equals the card colour there is no light halo
        and no white flash on show. With the system backdrop (acrylic/mica)
        the background must stay black/transparent and is left alone.
        """
        rgb = core.parse_css_color(color)
        if not rgb or not self.form or self.backdrop:
            return False
        self.surface = "#%02x%02x%02x" % rgb
        form = self.form

        def paint() -> bool:
            try:
                from System.Drawing import Color  # type: ignore
                value = Color.FromArgb(255, rgb[0], rgb[1], rgb[2])
                form.BackColor = value
                browser = getattr(form, "webview", None)
                if browser is not None:
                    browser.DefaultBackgroundColor = value
                return True
            except Exception:
                return False
        return bool(self._on_ui(paint))

    def _push(self, script: str) -> Any:
        window = self.window
        if window is None:
            return None
        try:
            return window.evaluate_js(script)
        except Exception as exc:
            log.debug("quick ask: evaluate_js failed: %s", exc)
            return None

    def toggle(self) -> None:
        """The hotkey: open; close when it is in front; bring a covered popup (pinned or stepped back) to the front.

        Round 34 (r34-quick-core): one press at a time. Every WM_HOTKEY runs on its own worker thread, and ``visible``
        becomes True only at the END of ``show()`` (window creation, selection read, placement) — a quick second press
        (open → close) started a SECOND ``show()``: the window stayed open, onShow ran twice and Ctrl+C was sent to
        our own window. The second press now waits for the first and then closes the window as the user meant."""
        lock = self.__dict__.setdefault("_toggle_lock", threading.Lock())
        with lock:
            self._toggle()

    def _toggle(self) -> None:
        if not self.visible:
            self.show()
            return
        foreground = int(self.api.GetForegroundWindow() or 0) if self.api else 0
        layer_hwnd = int(getattr(getattr(self, "layer", None), "hwnd", 0) or 0) if getattr(self, "layer", None) is not None else 0
        if self.api and foreground == layer_hwnd and layer_hwnd and getattr(getattr(self, "layer", None), "visible", False):
            self.hide()
            return
        if self.api and foreground != self.hwnd:
            prefs = self.load_prefs()

            self._follow_monitor(prefs, foreground)

            self._selection_token += 1
            self._note_app(foreground)
            capture = self.grab_selection(foreground, prefs)
            pending = not capture.done
            if not pending:
                self._send_selection(capture.result)
            else:
                self._push("window.QuickAsk && QuickAsk.selectionPending && QuickAsk.selectionPending()")
            if prefs.get("quick_ask_browser", True):

                threading.Thread(target=self.current_page, name="memuai-quick-page", daemon=True).start()
            self.raise_to_top()
            capture.settle()
            self.api.take_foreground(self.hwnd)
            if pending:
                self._follow_selection(capture)
            self._on_ui(self._focus_browser)
            self._push("window.QuickAsk && QuickAsk.focusInput()")
            return
        self.hide()

    def _follow_monitor(self, prefs: Dict[str, Any], foreground: int) -> bool:
        """Round 28: the hotkey over another monitor while the window is open there (stepped back, not pinned):
        the window and its screen layer move to the monitor the «Monitor» setting picks now."""
        if self.pinned or not self.geometry or not self.api:
            return False
        monitors = self._monitors()
        target = self._target_monitor(prefs, monitors, foreground)
        if target == int(self.geometry.get("monitor", -1)):
            return False
        with self._lock:
            geometry = self._initial_geometry(prefs, foreground, monitors=monitors)
            if self.css_height:
                geometry = core.resize(geometry, self.css_height)
            self.geometry = geometry
            self._place(geometry)
            self._round_region(geometry["w"], geometry["h"], geometry["scale"])
            self._place(geometry)
        import json
        self._push("window.QuickAsk && QuickAsk.onPlaced && QuickAsk.onPlaced(%s)" % json.dumps(
            {"dock": geometry.get("dock") or "", "anchor": geometry["anchor"],
             "maxHeight": round(geometry["max_h"] / geometry["scale"]), "monitor": geometry["monitor"],
             "scale": geometry["scale"]}))
        self._layer_follow(geometry, "monitor")
        return True

    def show(self, source: str = "hotkey") -> bool:
        if not self.native_ok or self._quitting:
            return False
        prefs = self.load_prefs()
        if not prefs.get("quick_ask_enabled", True) and source in ("hotkey", "restore"):
            return False
        api = self.api

        foreground = int(api.GetForegroundWindow() or 0)
        if not self.ensure_window():
            return False
        with self._lock:
            if foreground and foreground != self.hwnd:
                self.previous_hwnd = foreground
            page = self._begin_page(foreground, prefs) if source == "hotkey" else None

            self._selection_token += 1
            capture = self.grab_selection(foreground, prefs) if source == "hotkey" else quick_selection.Capture({})
            selection = capture.result
            self._show_foreground = foreground
            if source == "hotkey":
                self._note_app(foreground)
            geometry = self._initial_geometry(prefs)

            fresh = not self.pinned and time.monotonic() - self.hidden_at > KEEP_CONVERSATION
            self.geometry = geometry
            self.lowered = False
            payload = {"source": source, "fresh": fresh, "selection": selection.get("text") or "",
                       "selection_info": quick_selection.public(selection), "selection_pending": not capture.done,
                       "backdrop": self.backdrop,
                       "rounded": self.corner != "none", "corner": self.corner, "radius": self.radius_css,
                       "monitor": geometry["monitor"], "scale": geometry["scale"],
                       "maxHeight": round(geometry["max_h"] / geometry["scale"]), "anchor": geometry["anchor"],
                       "pinned": self.pinned, "page": core.public_page(page), "build": self.build,
                       "dock": geometry.get("dock", "")}

            self._open_seq = getattr(self, "_open_seq", 0) + 1
            payload["seq"] = self._open_seq
            import json
            self._push(f"window.QuickAsk && QuickAsk.onShow({json.dumps(payload, ensure_ascii=False)})")
            if page is not None:
                page["_shown"].set()

            wanted = self._push("window.QuickAsk ? QuickAsk.contentHeight() : 0")
            try:
                if wanted and float(wanted) > 0:
                    self.css_height = float(wanted)
                    geometry = core.resize(geometry, float(wanted))
                    self.geometry = geometry
            except (TypeError, ValueError):
                pass
            self._place(geometry)
            self._round_region(geometry["w"], geometry["h"], geometry["scale"])
            capture.settle()
            self._on_ui(lambda: (self.form.Show(), None)[1])
            self._place(geometry)
            api.take_foreground(self.hwnd)
            self._on_ui(self._focus_browser)
            self.visible = True
            self.shown_at = time.monotonic()
            if not selection:
                self._follow_selection(capture)
        self._push("window.QuickAsk && QuickAsk.focusInput()")
        self._arm_escape()
        if self.pinned:
            self._remember_open(True)

        self._layer_follow(self.geometry, "open")
        return True

    def layer_settings(self) -> Dict[str, Any]:
        """What the screen layer should do now (``quick_canvas.json``): {on, show} — never raises."""
        try:
            from . import quick_canvas
            doc = quick_canvas.load()
        except Exception:
            return {"on": False, "show": False}
        st, ui = doc["settings"], doc["ui"]
        on = bool(st["enabled"] and (st["panels"] or st["actions"]) and doc["layout"]["items"])
        mode = st.get("screen", "show")
        show = on and (mode == "show" or (mode == "last" and ui.get("layer", True)))
        return {"on": on, "show": show}

    def ensure_layer(self):
        if getattr(self, "layer", None) is None and self.native_ok:
            from .quick_layer import ScreenLayer
            self.layer = ScreenLayer(self)
        return self.layer

    def _layer_follow(self, geometry: Dict[str, Any], reason: str, force: Optional[bool] = None) -> None:
        """Show (or keep hidden) the screen layer for ``geometry`` on a worker thread: the popup never waits for it."""
        if not self.native_ok or self._quitting or not geometry:
            return
        self._layer_token = getattr(self, "_layer_token", 0) + 1
        token = self._layer_token
        monitors = self._monitors()

        def run() -> None:
            try:
                layer = self.ensure_layer()
                if layer is None or not layer.available() or token != self._layer_token or not self.visible:
                    return
                if force is not None:
                    want = force
                elif reason == "monitor":
                    want = layer.visible
                else:
                    want = self.layer_settings()["show"]
                if want:
                    layer.show(geometry, monitors, reason=reason)

                    if token != self._layer_token or not self.visible:
                        layer.hide()
                        return
                elif layer.visible:
                    layer.hide()
                self._push("window.MemuQuickCanvas && MemuQuickCanvas.layerState && MemuQuickCanvas.layerState(%s)"
                           % __import__("json").dumps(layer.public()))
            except Exception as exc:
                log.debug("quick ask: screen layer failed: %s", exc)
        threading.Thread(target=run, name="memuai-quick-layer", daemon=True).start()

    def layer_command(self, action: str, on: Optional[bool] = None) -> Dict[str, Any]:
        """``/api/quick/layer``: show | hide | toggle | edit | focused | state (Ctrl+B / Ctrl+E in either window)."""
        layer = self.ensure_layer()
        if layer is None or not layer.available():
            return {"ok": False, "available": False}
        if action == "state":
            return {"ok": True, **layer.public()}
        if action == "focused":
            layer.on_focus()
            return {"ok": True}
        if not self.visible:
            return {"ok": False, **layer.public()}
        if action in ("show", "hide", "toggle"):
            want = (not layer.visible) if action == "toggle" else action == "show"
            if want:
                ok = layer.show(self.geometry, self._monitors(), reason="toggle")
            else:
                ok = layer.hide()
            self._remember_layer(layer.visible)
            if not layer.visible:
                self.api.take_foreground(self.hwnd)
                self._on_ui(self._focus_browser)
            return {"ok": bool(ok), **layer.public()}
        if action == "edit":
            if on and not layer.visible:
                layer.show(self.geometry, self._monitors(), reason="edit")
                self._remember_layer(True)
            layer.set_edit(bool(on))
            if not on:
                self.api.take_foreground(self.hwnd)
                self._on_ui(self._focus_browser)
            return {"ok": True, **layer.public()}
        return {"ok": False, "error": "unknown action"}

    @staticmethod
    def _remember_layer(visible: bool) -> None:
        try:
            from . import quick_canvas

            def apply(doc):
                if doc["ui"].get("layer") == visible:
                    return False
                doc["ui"]["layer"] = visible
                return True
            quick_canvas.mutate(apply)
        except Exception:
            pass

    def _escape_decide(self, vk: int, message: int, flags: int) -> bool:

        from .win_hotkey import escape_should_close
        foreground = int(self.api.GetForegroundWindow() or 0) if self.api else 0
        layer = int(getattr(getattr(self, "layer", None), "hwnd", 0) or 0) if getattr(self, "layer", None) is not None else 0
        return escape_should_close(vk, message, flags, visible=self.visible, foreground=foreground,
                                   own_windows=(self.hwnd, layer, *getattr(self, "extra_windows", ())), pinned=self.pinned)

    def _arm_escape(self) -> None:
        """While the popup is visible a low-level keyboard hook (never swallowing the key) closes it on Escape
        pressed in ANY program; inside the popup the page handles Escape level by level. See win_hotkey.EscapeWatcher."""
        try:
            if getattr(self, "_escape", None) is None:
                from .win_hotkey import EscapeWatcher
                self._escape = EscapeWatcher(lambda: self.hide(restore_focus=False), self._escape_decide)
            self._escape.arm()
        except Exception as exc:
            log.debug("quick ask: escape hook failed: %s", exc)

    def _disarm_escape(self) -> None:
        watcher = getattr(self, "_escape", None)
        if watcher is not None:
            try:
                watcher.disarm()
            except Exception:
                pass

    def _monitors(self) -> List[Dict[str, Any]]:
        try:
            monitors = self.api.monitors() if self.api else []
        except Exception:
            monitors = []
        return monitors or [{"rect": (0, 0, 1920, 1080), "work": (0, 0, 1920, 1040), "dpi": 96, "primary": True}]

    def _target_monitor(self, prefs: Dict[str, Any], monitors: List[Dict[str, Any]], foreground: int = 0) -> int:
        """Round 28: the monitor the «Monitor» setting picks (cursor — default, the active window's, primary)."""
        choice = str(prefs.get("quick_ask_monitor") or "cursor")
        rect = None
        if choice == "window" and foreground and foreground != self.hwnd:
            try:
                rect = self.api.window_rect(foreground)
            except Exception:
                rect = None
        try:
            return core.choose_monitor(monitors, choice, self.api.cursor(), rect)
        except Exception:
            return 0

    def _initial_geometry(self, prefs: Dict[str, Any], foreground: int = 0, *,
                          monitors: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """Where the popup opens: cursor / a docking spot / where the user left it — on the monitor the user is on.

        Round 28: the remembered spot used to carry its monitor, so after one drag Quick Ask always reopened on
        that monitor (it could not be opened on another one at all). Now the target monitor comes from the
        «Monitor» setting and the spot is applied there: the spot remembered on THAT monitor first, else the
        spot of this monitor setup re-expressed in the target monitor's work area. A pinned window is a
        workspace and still reopens exactly where it was left."""
        monitors = monitors or self._monitors()
        foreground = foreground or int(getattr(self, "_show_foreground", 0) or 0)
        cursor = self.api.cursor()
        state = load_state()
        width = core.clamp_width(state.get("width", core.DEFAULT_WIDTH))

        mode = "last" if self.pinned else str(prefs.get("quick_ask_position") or "last")
        self.position_mode = mode
        target = None if self.pinned else self._target_monitor(prefs, monitors, foreground)
        if mode != "cursor":
            record = (state.get("placements") or {}).get(core.monitor_config_key(monitors))
            if target is not None:
                from .quick_layer_core import monitor_key
                spots = state.get("monitor_spots") if isinstance(state.get("monitor_spots"), dict) else {}
                own = spots.get(monitor_key(monitors[target]))
                if isinstance(own, dict) and (mode == "last" or own.get("mode") == mode):
                    record = own

            if isinstance(record, dict) and (mode == "last" or record.get("mode") == mode):
                geometry = core.place_saved(monitors, record, cursor, width=width, height=core.COLLAPSED_HEIGHT,
                                            target=target)
                if geometry:
                    geometry["dock"] = record.get("dock") or ""
                    return geometry
        place_mode = "center" if mode == "last" else mode
        geometry = core.place(monitors, cursor, width=width, height=core.COLLAPSED_HEIGHT, mode=place_mode,
                              target=target)
        geometry["dock"] = place_mode if place_mode in core.DOCKS else ""
        return geometry

    def drag_start(self) -> Dict[str, Any]:
        """The page's drag handle was pressed: move the window with the mouse until the button is released.

        A native loop (cursor polled at ~120 Hz, ``SetWindowPos`` without
        resizing) instead of one HTTP request per mouse move: smooth, correct
        across monitors with different DPI, and independent of WebView2's
        pointer capture. On release the window snaps to a nearby edge/corner
        (:func:`core.snap`) and the spot is remembered for this monitor setup.
        """
        api = self.api
        if not self.hwnd or not self.visible or self._dragging or not api:
            return {"ok": False}
        if not api.button_down():
            return {"ok": False, "code": "released"}
        self._dragging = True
        self._anim_token += 1
        threading.Thread(target=self._drag_loop, name="memuai-quick-drag", daemon=True).start()
        return {"ok": True}

    def _drag_loop(self) -> None:
        api = self.api
        try:
            start_cursor = api.cursor()
            rect = api.window_rect(self.hwnd) or (self.geometry["x"], self.geometry["y"],
                                                  self.geometry["x"] + self.geometry["w"],
                                                  self.geometry["y"] + self.geometry["h"])
            x0, y0, w, h = rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1]
            monitors = self._monitors()
            last_dock = None
            deadline = time.monotonic() + 120
            x, y = x0, y0
            while api.button_down() and time.monotonic() < deadline and self.visible:
                cx, cy = api.cursor()
                nx, ny = x0 + cx - start_cursor[0], y0 + cy - start_cursor[1]
                if (nx, ny) != (x, y):
                    x, y = nx, ny
                    insert, flags = self._z_order(SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER | SWP_ASYNCWINDOWPOS)
                    api.SetWindowPos(self.hwnd, insert, int(x), int(y), 0, 0, flags)
                    preview = core.snap(monitors, (x, y, x + w, y + h))
                    dock = preview["dock"]
                    if dock != last_dock:
                        last_dock = dock
                        self._push("window.QuickAsk && QuickAsk.onDrag && QuickAsk.onDrag(%s)" % (
                            '{"dock": "%s"}' % dock))
                time.sleep(1 / 120)
            if (x, y) == (x0, y0):
                return
            self._settle({"record": core.snap(monitors, (x, y, x + w, y + h))["record"]}, monitors)
        except Exception as exc:
            log.debug("quick ask: drag failed: %s", exc)
        finally:
            self._dragging = False
            self._push("window.QuickAsk && QuickAsk.onDrag && QuickAsk.onDrag(null)")

    def _settle(self, snapped: Dict[str, Any], monitors: List[Dict[str, Any]]) -> None:
        """Place the window at a snapped/docked spot (DPI of the target monitor) and remember it."""
        record = dict(snapped["record"])
        record["mode"] = self.position_mode
        record["t"] = int(time.time())
        width = core.clamp_width(load_state().get("width", core.DEFAULT_WIDTH))
        geometry = core.place_saved(monitors, record, self.api.cursor(), width=width, height=self.css_height)
        if not geometry:
            return
        with self._lock:
            self.geometry = geometry
            self._place(geometry)
            self._round_region(geometry["w"], geometry["h"], geometry["scale"])
        self._layer_keepout(geometry)
        try:
            state = load_state()
            placements = state.get("placements") if isinstance(state.get("placements"), dict) else {}
            placements[core.monitor_config_key(monitors)] = record

            from .quick_layer_core import monitor_key
            spots = state.get("monitor_spots") if isinstance(state.get("monitor_spots"), dict) else {}
            index = min(max(int(geometry.get("monitor", 0)), 0), len(monitors) - 1)
            spots[monitor_key(monitors[index])] = record
            if len(spots) > 12:
                for key in sorted(spots, key=lambda k: int((spots[k] or {}).get("t") or 0))[:-12]:
                    spots.pop(key, None)

            if len(placements) > 12:
                for key in sorted(placements, key=lambda k: int((placements[k] or {}).get("t") or 0))[:-12]:
                    placements.pop(key, None)
            save_state({"placements": placements, "monitor_spots": spots})
        except Exception:
            pass
        import json
        self._push("window.QuickAsk && QuickAsk.onPlaced && QuickAsk.onPlaced(%s)" % json.dumps(
            {"dock": record.get("dock") or "", "anchor": geometry["anchor"],
             "maxHeight": round(geometry["max_h"] / geometry["scale"])}))

    def dock(self, where: str) -> Dict[str, Any]:
        """Keyboard docking (Ctrl+Alt+arrows in the window) and «reset position» (``center``)."""
        if not self.hwnd or not self.visible or where not in core.DOCKS:
            return {"ok": False}
        monitors = self._monitors()
        index = int(self.geometry.get("monitor", 0)) if self.geometry else 0
        index = min(max(index, 0), len(monitors) - 1)
        monitor = monitors[index]
        left, top, right, bottom = (int(v) for v in monitor["rect"])
        target = core.place([monitor], ((left + right) // 2, (top + bottom) // 2),
                            width=core.clamp_width(load_state().get("width", core.DEFAULT_WIDTH)),
                            height=self.css_height, mode=where)
        x, y, w, h = target["x"], target["y"], target["w"], target["h"]
        self._settle({"record": core.snap(monitors, (x, y, x + w, y + h), threshold_css=1)["record"]}, monitors)
        return {"ok": True, "dock": where}

    def _begin_page(self, foreground: int, prefs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Remember the browser tab the user came from; the URL is read asynchronously (never blocks showing)."""
        self._page_token += 1
        token = self._page_token
        self.page = None
        if not foreground or not prefs.get("quick_ask_browser", True):
            return None
        try:
            from . import quick_ask_browser as qb
            info = qb.foreground_info(foreground)
            browser = qb.browser_for_process(info.get("exe"))
        except Exception:
            return None
        if not browser:
            return None
        page = {"id": "%x-%d" % (foreground, token), "hwnd": int(foreground), "browser": browser["name"],
                "family": browser["family"], "exe": browser["exe"], "title": qb.clean_title(info.get("title")),
                "url": "", "source": "title", "pending": True, "t": time.time(), "_shown": threading.Event()}
        self.page = page

        if browser["exe"] == "chrome.exe":
            from .browser_extension import bridge as extension_bridge
            extension = extension_bridge.page_hint(page["title"])
            if extension:
                page.update(extension, pending=False)
                return page

        def resolve() -> None:
            url = ""
            try:
                url = qb.UiaReader.shared().address(foreground)
            except Exception as exc:
                log.debug("quick ask: page url failed: %s", exc)
            page.update({"url": url, "source": "uia" if url else "title", "pending": False})
            page["_shown"].wait(5)
            if token != self._page_token:
                return
            import json
            self._push("window.QuickAsk && QuickAsk.onPage && QuickAsk.onPage(%s)" % json.dumps(
                core.public_page(page), ensure_ascii=False))
        threading.Thread(target=resolve, name="memuai-quick-page", daemon=True).start()
        return page

    def current_page(self) -> Optional[Dict[str, Any]]:
        """Round 26: the tab the user is looking at NOW (topmost browser window, its address bar read again).

        The page remembered at the hotkey goes stale: a pinned window stays open while the user switches tabs,
        Alt+Space over a window that is already shown only raises it, a restored chat or a retry carries the page
        of an older question. «Summarize the page» then summarized another (old) tab. Questions about the page
        resolve it here instead; ``None`` = no browser window is open (the model says so honestly).
        """
        if not self.native_ok or not self.api:
            return self.page
        from . import quick_ask_browser as qb
        rows = qb.top_level_windows()
        hwnd = qb.pick_top_browser(rows, exclude=(self.hwnd,))
        old = self.page
        if hwnd and old and old.get("hwnd") == hwnd and old.get("url") and not old.get("pending") \
                and time.time() - float(old.get("t") or 0) < PAGE_FRESH:

            row = next((r for r in rows if r.get("hwnd") == hwnd), None)
            if row is not None and qb.clean_title(row.get("title")) == old.get("title"):
                return old
        fresh = qb.detect(hwnd) if hwnd else None
        if not fresh:
            if self.page is not None:
                self.page = None
                self._push("window.QuickAsk && QuickAsk.onPage && QuickAsk.onPage(null, true)")
            return None
        old = self.page
        if old and old.get("hwnd") == hwnd and old.get("url") == fresh.get("url") and old.get("title") == fresh.get("title"):
            old["t"] = time.time()
            if fresh.get("source") == "extension":
                old.update(source="extension", extension_tab_id=fresh["extension_tab_id"])
            return old
        self._page_token += 1
        fresh.update({"id": "%x-%d" % (hwnd, self._page_token), "pending": False, "_shown": threading.Event()})
        fresh["_shown"].set()
        self.page = fresh
        import json
        self._push("window.QuickAsk && QuickAsk.onPage && QuickAsk.onPage(%s, true)" % json.dumps(
            core.public_page(fresh), ensure_ascii=False))
        return fresh

    def page_text(self, page_id: str = "", limit: int = 60000) -> str:
        """Visible text of the remembered tab (UI Automation), for ``read_page``'s fallback."""
        page = self.page
        if not page or (page_id and page_id != page.get("id")):
            return ""
        api = self.api
        if not api or not api.IsWindow(page["hwnd"]):
            return ""
        from . import quick_ask_browser as qb
        return qb.UiaReader.shared().page_text(page["hwnd"], limit=limit)

    def _focus_browser(self) -> None:
        try:
            self.form.Activate()
            browser = getattr(self.form, "webview", None)
            if browser is not None:
                browser.Focus()
        except Exception:
            pass

    def _z_order(self, flags: int) -> tuple:
        """(hWndInsertAfter, flags) for SetWindowPos: topmost, unless the unpinned popup stepped back (keep z-order)."""
        if self.lowered and not self.pinned:
            return 0, flags | SWP_NOZORDER
        return HWND_TOPMOST, flags

    def _place(self, geometry: Dict[str, Any], *, flags: int = 0) -> None:
        insert, flags = self._z_order(SWP_NOACTIVATE | SWP_NOOWNERZORDER | flags)
        self.api.SetWindowPos(self.hwnd, insert, int(geometry["x"]), int(geometry["y"]),
                              int(geometry["w"]), int(geometry["h"]), flags)

    def hide(self, restore_focus: bool = True) -> bool:
        """Round 38 (Escape bug): the window disappears FIRST — nothing on the way waits for a page.

        Before, ``onHide`` (a synchronous ``evaluate_js``: pywebview waits up to 20 s for a page that is not ready and
        has no timeout for the answer) and the screen layer (its lock is held while the layer window is created and
        shown) ran BEFORE ``form.Hide()``. The page had already faded the card out, so the window stood as bare
        Windows 11 glass («white, shimmering») for up to ~20 s. Now: ShowWindowAsync(SW_HIDE) → form.Hide → layer →
        the page is told afterwards on a worker thread. A close slower than ``SLOW_CLOSE`` is logged with its step."""
        if not self.hwnd:
            return False
        started = time.monotonic()
        steps: List[Any] = []
        if self.visible:
            self._hide_now()
            steps.append(("async_hide", time.monotonic()))
        with self._lock:
            steps.append(("lock", time.monotonic()))
            if not self.visible:
                return True
            self._anim_token += 1
            self._selection_token += 1
            self._layer_token = getattr(self, "_layer_token", 0) + 1
            self.visible = False
            self.lowered = False
            self._on_ui(lambda: (self.form.Hide(), None)[1])
            steps.append(("form_hide", time.monotonic()))
            if getattr(self, "layer", None) is not None:
                try:
                    self.layer.hide()
                except Exception:
                    pass
                steps.append(("layer", time.monotonic()))
            self.hidden_at = time.monotonic()
            self._disarm_escape()
            if self.pinned:
                self._remember_open(False)
            previous = self.previous_hwnd
            if restore_focus and previous and self.api.IsWindow(previous) and not self.api.IsIconic(previous):
                self.api.take_foreground(previous)
                steps.append(("focus", time.monotonic()))
        threading.Thread(target=self._tell_hidden, args=(getattr(self, "_open_seq", 0),),
                         name="memuai-quick-push", daemon=True).start()
        slow_close_report(started, steps, "server")
        return True

    def _hide_now(self) -> None:
        """Take the popup off the screen without waiting for its UI thread (the state follows in ``hide``)."""
        api, hwnd = self.api, self.hwnd
        try:
            fn = getattr(api, "ShowWindowAsync", None) if api else None
            if fn and hwnd:
                fn(hwnd, SW_HIDE)
        except Exception as exc:
            log.debug("quick ask: ShowWindowAsync failed: %s", exc)

    def _tell_hidden(self, seq: int) -> None:
        """Round 38: ``onHide`` on a worker thread after the window is gone (hide never waits for ``evaluate_js``).
        Skipped when it was shown again meanwhile; the page also ignores an ``onHide`` of an older opening."""
        if not self.visible:
            self._push("window.QuickAsk && QuickAsk.onHide(%d)" % seq)

    def blur(self) -> bool:
        """Round 24: focus loss NEVER hides Quick Ask (owner's rule: only Escape and explicit ways close it —
        Escape inside the window, Escape anywhere via the hook, the hotkey again, the API/command). Kept for
        compatibility with older pages that still post ``blur``."""
        return False

    def leave_mode(self) -> str:
        """Round 32: ``quick_ask_on_leave`` — "stay" (default: on top until Escape) or "back" (Round 25 step back)."""
        try:
            mode = str((self.load_prefs() or {}).get("quick_ask_on_leave") or "stay")
        except Exception:
            mode = "stay"
        return mode if mode in core.ON_LEAVE_CHOICES else "stay"

    def _satellites(self) -> List[int]:
        """Round 32: the windows that belong to Quick Ask and must follow its z-order as ONE unit (today: the
        screen layer with the panels and buttons, when it is shown)."""
        layer = getattr(self, "layer", None)
        hwnd = int(getattr(layer, "hwnd", 0) or 0) if layer is not None else 0
        return [hwnd] if hwnd and getattr(layer, "visible", False) else []

    def step_back(self) -> bool:
        """The page reports ``inactive`` when the user goes to another program.

        Round 32 (owner's rule: «Quick Access is pinned — clicking elsewhere must not make it disappear, everything
        closes only on Escape»): by default (``quick_ask_on_leave = "stay"``) nothing happens — the window and its
        panels/buttons stay on top together. Only with ``"back"`` (the Round 25 behaviour, opt-in) the UNPINNED
        popup steps back right behind that program's window — and its screen layer steps back WITH it (Round 25–31
        lowered only the window: the topmost panels and buttons stayed on screen alone). Never hides."""
        api = self.api
        if self.pinned or not self.visible or not self.hwnd or not api or self._dragging:
            return False
        if self.leave_mode() != "back":
            if self.lowered:
                self.raise_to_top()
            return False
        foreground = int(api.GetForegroundWindow() or 0)
        if not foreground or foreground == self.hwnd:
            return False
        own = {int(h) for h in (*self._satellites(), *getattr(self, "extra_windows", ())) if h}
        layer = getattr(self, "layer", None)
        if layer is not None and int(getattr(layer, "hwnd", 0) or 0):
            own.add(int(layer.hwnd))
        if foreground in own:
            return False
        flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER
        with self._lock:
            try:
                topmost = bool(int(api.GetWindowLong(foreground, GWL_EXSTYLE)) & WS_EX_TOPMOST)
            except Exception:
                topmost = True

            for hwnd in (*self._satellites(), self.hwnd):
                api.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, flags)
                if not topmost:

                    api.SetWindowPos(hwnd, foreground, 0, 0, 0, 0, flags)
            self.lowered = True
        return True

    def raise_to_top(self) -> bool:
        """The popup is in use again (focus came back, the hotkey, pinning): topmost again — its screen layer too
        (first), so the window ends up above its panels and buttons."""
        if not self.hwnd or not self.api:
            return False
        with self._lock:
            was = self.lowered
            self.lowered = False
            flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER
            if was:
                for hwnd in self._satellites():
                    self.api.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags)
            self.api.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags)
        return was

    def _legacy_blur(self) -> bool:  # pragma: no cover - the pre-R24 behaviour, kept for reference
        if self.pinned or time.monotonic() < self._busy_until:
            return False
        if time.monotonic() - self.shown_at < 0.35:
            return False
        prefs = self.load_prefs()
        if not prefs.get("quick_ask_hide_on_blur", True):
            return False
        foreground = int(self.api.GetForegroundWindow() or 0) if self.api else 0
        if foreground == self.hwnd:
            return False
        return self.hide(restore_focus=False)

    def set_pinned(self, pinned: bool) -> bool:
        """The pin button / Ctrl+P / Ctrl+K. A pinned popup stays on top of every window, is not closed by Escape
        pressed in other programs, keeps its place and conversation, and comes back after a restart if it was
        open. Kept in ``quick_state.json`` (``pinned``, ``pinned_open``)."""
        self.pinned = bool(pinned)
        try:
            save_state({"pinned": self.pinned, "pinned_open": self.pinned and self.visible})
        except Exception:
            pass
        if self.pinned and self.visible:
            self.raise_to_top()
        return self.pinned

    def _remember_open(self, is_open: bool) -> None:
        try:
            if bool(load_state().get("pinned_open")) != is_open:
                save_state({"pinned_open": is_open})
        except Exception:
            pass

    def resize(self, height_css: float, width_css: Optional[float] = None, *, animate: bool = True,
               persist_width: bool = False) -> Dict[str, Any]:
        if not self.hwnd or not self.geometry:
            return {"ok": False}
        self.css_height = max(float(core.MIN_HEIGHT), float(height_css or 0))
        if self._dragging:
            return {"ok": True, "deferred": True}
        with self._lock:
            start = dict(self.geometry)
            target = core.resize(start, height_css, width_css=width_css)
            self.geometry = target
            self._anim_token += 1
            token = self._anim_token
        if width_css is not None and persist_width:
            try:
                save_state({"width": core.clamp_width(width_css)})
            except Exception:
                pass
        same = all(int(start[k]) == int(target[k]) for k in ("x", "y", "w", "h"))
        if same:
            return {"ok": True, "height": target["h"], "max": target["max_h"]}
        if not animate or abs(int(target["h"]) - int(start["h"])) < 6 or width_css is not None:
            self._place(target)
            self._round_region(target["w"], target["h"], target["scale"])
            self._layer_keepout(target)
        else:
            threading.Thread(target=self._animate, args=(start, target, token), daemon=True).start()
        return {"ok": True, "height": target["h"], "max": target["max_h"]}

    def _animate(self, start: Dict[str, Any], target: Dict[str, Any], token: int) -> None:
        frames = 9
        heights = core.ease_steps(int(start["h"]), int(target["h"]), frames)
        tops = core.ease_steps(int(start["y"]), int(target["y"]), frames)
        for top, height in zip(tops, heights):
            if token != self._anim_token or not self.visible:
                return
            step = dict(target, y=top, h=height)
            self._place(step)
            if self.corner == "region":
                self._round_region(step["w"], height, step["scale"])
            time.sleep(1 / 60)
        if token == self._anim_token:
            self._round_region(target["w"], target["h"], target["scale"])
            self._layer_keepout(target)

    def _layer_keepout(self, geometry: Dict[str, Any]) -> None:
        layer = getattr(self, "layer", None)
        if layer is not None and getattr(layer, "visible", False):
            try:
                layer.update_keepout(geometry)
            except Exception:
                pass

    def capture(self, monitor: Any = "cursor", region: Optional[Dict[str, float]] = None,
                max_side: int = 1920, lang: str = "ru") -> Dict[str, Any]:
        """Screenshot of the user's screen without any MemuAI window (errors in ``lang``)."""
        en = lang == "en"
        if not self.native_ok:
            return {"ok": False, "error": "Screenshots work only on Windows" if en
                    else "Снимок экрана доступен только в Windows"}
        try:
            from PIL import ImageGrab
        except Exception as exc:
            return {"ok": False, "error": f"Pillow is unavailable: {exc}" if en else f"Pillow недоступен: {exc}"}
        api = self.api
        monitors = api.monitors()
        if not monitors:
            return {"ok": False, "error": "Could not list the monitors" if en else "Не удалось получить список мониторов"}
        cursor_index = int(self.geometry.get("monitor", core.pick_monitor(monitors, *api.cursor()))) \
            if self.visible else core.pick_monitor(monitors, *api.cursor())
        cursor_index = min(max(cursor_index, 0), len(monitors) - 1)
        bbox = core.capture_bbox(monitors, monitor, cursor_index, region)
        layer = int(getattr(getattr(self, "layer", None), "hwnd", 0) or 0) if getattr(self, "layer", None) is not None else 0
        ours = [hwnd for hwnd in (self.hwnd, int(self.main_hwnd() or 0), layer)
                if hwnd and api.IsWindow(hwnd) and api.IsWindowVisible(hwnd)]
        excluded, cloaked = [], []
        self._busy_until = time.monotonic() + 2.0
        try:
            for hwnd in ours:

                if self.build >= EXCLUDE_FROM_CAPTURE_BUILD and api.SetWindowDisplayAffinity \
                        and self._affinity(hwnd, WDA_EXCLUDEFROMCAPTURE):
                    excluded.append(hwnd)
                elif api.dwm_int(hwnd, DWMWA_CLOAK, 1):
                    cloaked.append(hwnd)
            if excluded or cloaked:
                try:
                    if api.DwmFlush:
                        api.DwmFlush()
                except Exception:
                    pass
                time.sleep(0.06 if cloaked else 0.03)
            image = ImageGrab.grab(bbox=bbox, all_screens=True, include_layered_windows=True)
        except Exception as exc:
            return {"ok": False, "error": f"Screenshot failed: {exc}" if en else f"Снимок не удался: {exc}"}
        finally:
            for hwnd in excluded:
                self._affinity(hwnd, WDA_NONE)
            for hwnd in cloaked:
                api.dwm_int(hwnd, DWMWA_CLOAK, 0)
            self._busy_until = time.monotonic() + 0.3
        return encode_image(image, max_side=max_side, monitor=monitor, bbox=bbox, count=len(monitors))

    def _affinity(self, hwnd: int, value: int) -> bool:
        def call() -> bool:
            try:
                return bool(self.api.SetWindowDisplayAffinity(hwnd, value))
            except Exception:
                return False
        result = self._on_ui(call) if self.form is not None else None
        if result is None:
            result = call()
        return bool(result)

    @contextlib.contextmanager
    def hidden_from_capture(self):
        """Round 30: Computer Use screenshots without the Quick Ask window and its screen layer (the run's compact
        indicator / approval card stays visible to the user). Same mechanism as :meth:`capture`, only for the
        duration of one grab — never permanent (the window would vanish from the user's own screen sharing)."""
        api = self.api if self.native_ok else None
        excluded, cloaked = [], []
        try:
            if api is not None:
                layer = int(getattr(getattr(self, "layer", None), "hwnd", 0) or 0) if getattr(self, "layer", None) is not None else 0
                for hwnd in (self.hwnd, layer):
                    if not (hwnd and api.IsWindow(hwnd) and api.IsWindowVisible(hwnd)):
                        continue
                    if self.build >= EXCLUDE_FROM_CAPTURE_BUILD and api.SetWindowDisplayAffinity \
                            and self._affinity(hwnd, WDA_EXCLUDEFROMCAPTURE):
                        excluded.append(hwnd)
                    elif api.dwm_int(hwnd, DWMWA_CLOAK, 1):
                        cloaked.append(hwnd)
                if excluded or cloaked:
                    try:
                        if api.DwmFlush:
                            api.DwmFlush()
                    except Exception:
                        pass
                    time.sleep(0.06 if cloaked else 0.03)
        except Exception as exc:
            log.debug("quick ask: hide from capture failed: %s", exc)
        try:
            yield
        finally:
            for hwnd in excluded:
                self._affinity(hwnd, WDA_NONE)
            for hwnd in cloaked:
                try:
                    api.dwm_int(hwnd, DWMWA_CLOAK, 0)
                except Exception:
                    pass

    def monitor_count(self) -> int:
        try:
            return len(self.api.monitors()) if self.api else 0
        except Exception:
            return 0

    def _note_app(self, foreground: int) -> None:
        """Round 29: read the context of the program in front (Explorer folder, editor file…) in the background."""
        try:
            from . import quick_apps
            from .safe_mode import is_safe_mode
            if foreground and foreground != self.hwnd and not is_safe_mode():
                quick_apps.note_foreground(foreground, own_pids=(__import__("os").getpid(),))
        except Exception as exc:
            log.debug("quick ask: app context failed: %s", exc)

    def grab_selection(self, foreground: int, prefs: Dict[str, Any]) -> "quick_selection.Capture":
        """Round 27: start reading the text selected in ``foreground`` (UI Automation first, then Ctrl+C with the
        clipboard snapshotted and restored) — see app/quick_selection.py. Never raises: a failure is a finished
        capture without text. A Ctrl+C read is still running when this returns (``capture.done`` is False)."""
        try:
            return quick_selection.start(foreground, prefs)
        except Exception as exc:
            log.debug("quick ask: selection capture failed: %s", exc)
            return quick_selection.Capture({})

    def _send_selection(self, selection: Dict[str, Any], late: bool = False) -> None:
        if not selection.get("text"):
            return
        import json
        self.previous_hwnd = int(selection.get("hwnd") or 0) or self.previous_hwnd
        self._push("window.QuickAsk && QuickAsk.onSelection && QuickAsk.onSelection(%s)" % json.dumps(
            {"text": selection["text"], "info": quick_selection.public(selection), "late": late}, ensure_ascii=False))

    def _follow_selection(self, capture: "quick_selection.Capture") -> None:
        """The Ctrl+C read finishes after the window is open: hand the text to this same opening of the window —
        not after it was hidden or opened again (the page also drops it once the user sent something)."""
        if capture.done and not capture.result.get("text"):
            return
        token = self._selection_token

        def deliver(result: Dict[str, Any]) -> None:
            if result.get("text") and self.visible and token == self._selection_token:
                self._send_selection(result, late=True)
        capture.then(deliver)

    def copy_text(self, text: str) -> bool:
        return bool(self.api and self.api.set_text(str(text or "")))

    def insert_text(self, text: str) -> bool:
        """Paste the answer into the program that was active before the popup."""
        api = self.api
        if not api or not self.previous_hwnd or not api.IsWindow(self.previous_hwnd):
            return False
        if not api.set_text(str(text or "")):
            return False
        self.hide(restore_focus=True)
        deadline = time.monotonic() + 0.5
        while api.modifiers_down() and time.monotonic() < deadline:
            time.sleep(0.015)
        time.sleep(0.06)
        api.chord(VK_CONTROL, 0x56)
        return True

    def open_main(self, handoff: Optional[Dict[str, Any]] = None) -> bool:
        """«Открыть в MemuAI»: hand the conversation to the main window and bring it up."""
        if handoff:
            self.handoff = handoff
        self.hide(restore_focus=False)
        try:
            self.show_main()
        except Exception:
            pass
        window = self.main_window()
        if window is not None and handoff:
            try:
                window.evaluate_js("window.MemuQuickHandoff && MemuQuickHandoff()")
            except Exception:
                pass
        return True

def encode_image(image, *, max_side: int = 1920, **meta: Any) -> Dict[str, Any]:
    """PIL image → JPEG data URL scaled to ``max_side`` (vision models do not need 4K)."""
    from PIL import Image
    image = image.convert("RGB")
    width, height = image.size
    scale = min(1.0, float(max_side) / float(max(width, height) or 1))
    if scale < 1.0:
        image = image.resize((max(1, int(width * scale)), max(1, int(height * scale))), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=86, optimize=True)
    data = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    out = {"ok": True, "data": data, "width": image.size[0], "height": image.size[1],
           "source_width": width, "source_height": height, "bytes": len(buffer.getvalue())}
    for key, value in meta.items():
        out[key] = list(value) if isinstance(value, tuple) else value
    return out
