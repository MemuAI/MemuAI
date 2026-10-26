"""Region screenshots for MemuAI: select any part of any monitor.

The old tool opened Windows Snipping Tool (``ms-screenclip:``) and watched
the clipboard for a minute. Cancelling left that watcher running, the
Snipping Tool interface could come back, and the clipboard got overwritten.
This module replaces it with MemuAI's own selector:

* a separate process (``MemuAI.exe --memuai-screen-capture``) so a native
  window and its message loop never share the WebView process;
* the whole virtual desktop is captured once, before anything is drawn, and
  shown frozen and dimmed under a borderless top-most window;
* drag to select an area; hover highlights the window under the cursor and a
  click takes that window; Enter takes the monitor under the cursor;
  Esc or a right click cancels; losing focus or three minutes of inactivity
  also cancels, so the overlay can never stay stuck on screen;
* the process is per-monitor DPI aware and works in physical pixels, so the
  crop matches the screen on mixed-DPI multi-monitor setups;
* the result is a PNG file written only after a selection; the clipboard is
  never touched.

Only standard Win32 (user32, gdi32, dwmapi, msimg32) through ``ctypes`` and
Pillow for PNG encoding — no new dependencies.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

TIMEOUT_SECONDS = 180

_capture_lock = threading.Lock()

_last_cancel_at = 0.0
REPLAY_WINDOW = 2.0

def _worker_command(out_path: str, language: str) -> List[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--memuai-screen-capture", out_path, language]
    return [sys.executable, "-m", "app.screen_capture", out_path, language]

def _set_cloak(hwnd: int, hidden: bool) -> bool:
    """Hide or show the MemuAI window instantly without changing its state."""
    if not hwnd or os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes
    value = ctypes.c_int(1 if hidden else 0)
    try:

        result = ctypes.windll.dwmapi.DwmSetWindowAttribute(wintypes.HWND(hwnd), 13, ctypes.byref(value), ctypes.sizeof(value))
        return result == 0
    except Exception:
        return False

def capture_region(hwnd: int = 0, language: str = "ru", timeout: float = TIMEOUT_SECONDS) -> Dict[str, Any]:
    """Run the selector and return ``{"ok": True, "png": bytes, ...}`` or a reason."""
    global _last_cancel_at
    if os.name != "nt":
        return {"ok": False, "code": "unsupported", "error": "Region capture is available only on Windows"}
    if time.monotonic() - _last_cancel_at < REPLAY_WINDOW:
        return {"ok": False, "code": "cancelled", "error": "Область не выбрана"}
    if not _capture_lock.acquire(blocking=False):
        return {"ok": False, "code": "busy", "error": "Снимок экрана уже выполняется"}
    out = Path(tempfile.gettempdir()) / "MemuAI" / f"shot-{os.getpid()}-{int(time.time() * 1000)}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    result: Dict[str, Any] = {}
    try:
        result = _capture(hwnd, language, timeout, out)
        return result
    finally:
        if result.get("code") == "cancelled":
            _last_cancel_at = time.monotonic()
        _capture_lock.release()

def _capture(hwnd: int, language: str, timeout: float, out: Path) -> Dict[str, Any]:
    import ctypes
    cloaked = False
    minimized = False
    try:
        if hwnd:
            cloaked = _set_cloak(hwnd, True)
            if not cloaked:
                minimized = bool(ctypes.windll.user32.ShowWindow(hwnd, 6))
                time.sleep(0.3)
            else:
                time.sleep(0.12)
        try:
            ctypes.windll.user32.AllowSetForegroundWindow(-1)
        except Exception:
            pass
        root = Path(__file__).resolve().parent.parent
        try:
            process = subprocess.run(
                _worker_command(str(out), "en" if language == "en" else "ru"),
                cwd=None if getattr(sys, "frozen", False) else str(root),
                capture_output=True, timeout=timeout + 10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            return {"ok": False, "code": "cancelled", "error": "Время выбора области истекло"}
        except OSError as exc:
            return {"ok": False, "code": "unsupported", "error": f"Не удалось запустить выбор области: {exc}"}
        status: Dict[str, Any] = {}
        try:
            status = json.loads(Path(str(out) + ".json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            for line in reversed((process.stdout or b"").decode("utf-8", "replace").splitlines()):
                try:
                    status = json.loads(line)
                    break
                except ValueError:
                    continue
        if status.get("ok") and out.is_file():
            data = out.read_bytes()
            return {"ok": True, "png": data, "width": status.get("width"), "height": status.get("height")}
        if status.get("code") == "cancelled" or process.returncode == 2:
            return {"ok": False, "code": "cancelled", "error": "Область не выбрана"}
        detail = status.get("error") or (process.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
        return {"ok": False, "code": "failed", "error": f"Снимок не удался: {detail if isinstance(detail, str) else detail[0]}"}
    finally:
        if cloaked:
            _set_cloak(hwnd, False)
        elif minimized:
            ctypes.windll.user32.ShowWindow(hwnd, 9)
        if hwnd:
            try:
                ctypes.windll.user32.SetForegroundWindow(hwnd)
            except Exception:
                pass
        for leftover in (out, Path(str(out) + ".json")):
            try:
                leftover.unlink()
            except OSError:
                pass

HINTS = {
    "ru": "Выделите область мышью  ·  клик — окно  ·  Enter — весь экран  ·  Esc — отмена",
    "en": "Drag to select an area  ·  click — a window  ·  Enter — whole screen  ·  Esc — cancel",
}

def _take_foreground(user32, kernel32, hwnd) -> None:
    """SetForegroundWindow, and if Windows refuses it, borrow the input queue
    of the current foreground window for a moment (the documented workaround)."""
    user32.SetForegroundWindow(hwnd)
    if user32.GetForegroundWindow() != hwnd:
        try:
            foreground = user32.GetForegroundWindow()
            other = user32.GetWindowThreadProcessId(foreground, None)
            mine = kernel32.GetCurrentThreadId()
            if other and other != mine and user32.AttachThreadInput(mine, other, True):
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
                user32.AttachThreadInput(mine, other, False)
        except Exception:
            pass
    user32.SetFocus(hwnd)

def _run_overlay(out_path: str, language: str) -> Dict[str, Any]:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    msimg32 = ctypes.WinDLL("msimg32")
    try:
        dwmapi = ctypes.WinDLL("dwmapi")
    except OSError:
        dwmapi = None
    try:
        shcore = ctypes.WinDLL("shcore")
    except OSError:
        shcore = None

    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            shcore and shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass

    LRESULT = ctypes.c_ssize_t
    HANDLE = wintypes.HANDLE
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    class WNDCLASSEXW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", HANDLE),
                    ("hIcon", HANDLE), ("hCursor", HANDLE), ("hbrBackground", HANDLE),
                    ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", HANDLE)]

    class PAINTSTRUCT(ctypes.Structure):
        _fields_ = [("hdc", HANDLE), ("fErase", wintypes.BOOL), ("rcPaint", wintypes.RECT),
                    ("fRestore", wintypes.BOOL), ("fIncUpdate", wintypes.BOOL), ("rgbReserved", ctypes.c_byte * 32)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                    ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

    class BLENDFUNCTION(ctypes.Structure):
        _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte),
                    ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_byte)]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, wintypes.HWND, HANDLE, HANDLE, ctypes.c_void_p]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = HANDLE
    user32.ReleaseDC.argtypes = [wintypes.HWND, HANDLE]
    user32.BeginPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
    user32.BeginPaint.restype = HANDLE
    user32.EndPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
    user32.InvalidateRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT), wintypes.BOOL]
    user32.LoadCursorW.argtypes = [HANDLE, ctypes.c_void_p]
    user32.LoadCursorW.restype = HANDLE
    user32.SetCursor.argtypes = [HANDLE]
    user32.FillRect.argtypes = [HANDLE, ctypes.POINTER(wintypes.RECT), HANDLE]
    user32.DrawTextW.argtypes = [HANDLE, wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(wintypes.RECT), wintypes.UINT]
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    user32.MonitorFromPoint.restype = HANDLE
    user32.GetMonitorInfoW.argtypes = [HANDLE, ctypes.POINTER(MONITORINFO)]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetFocus.argtypes = [wintypes.HWND]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.SetCapture.argtypes = [wintypes.HWND]
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    gdi32.CreateCompatibleDC.argtypes = [HANDLE]
    gdi32.CreateCompatibleDC.restype = HANDLE
    gdi32.CreateCompatibleBitmap.argtypes = [HANDLE, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = HANDLE
    gdi32.SelectObject.argtypes = [HANDLE, HANDLE]
    gdi32.SelectObject.restype = HANDLE
    gdi32.DeleteObject.argtypes = [HANDLE]
    gdi32.DeleteDC.argtypes = [HANDLE]
    gdi32.BitBlt.argtypes = [HANDLE, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, HANDLE, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
    gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
    gdi32.CreateSolidBrush.restype = HANDLE
    gdi32.CreatePen.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.COLORREF]
    gdi32.CreatePen.restype = HANDLE
    gdi32.GetStockObject.argtypes = [ctypes.c_int]
    gdi32.GetStockObject.restype = HANDLE
    gdi32.Rectangle.argtypes = [HANDLE, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
    gdi32.SetBkMode.argtypes = [HANDLE, ctypes.c_int]
    gdi32.SetTextColor.argtypes = [HANDLE, wintypes.COLORREF]
    gdi32.CreateFontW.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.LPCWSTR]
    gdi32.CreateFontW.restype = HANDLE
    gdi32.GetDIBits.argtypes = [HANDLE, HANDLE, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), wintypes.UINT]
    gdi32.RoundRect.argtypes = [HANDLE, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
    msimg32.AlphaBlend.argtypes = [HANDLE, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, HANDLE, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, BLENDFUNCTION]
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = HANDLE
    if shcore is not None:
        shcore.GetDpiForMonitor.argtypes = [HANDLE, ctypes.c_int, ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT)]
    if dwmapi is not None:
        dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]

    SRCCOPY, CAPTUREBLT = 0x00CC0020, 0x40000000
    vx, vy = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
    vw, vh = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
    if vw <= 0 or vh <= 0:
        return {"ok": False, "error": "screen size unavailable"}

    screen = user32.GetDC(None)
    orig_dc = gdi32.CreateCompatibleDC(screen)
    orig_bmp = gdi32.CreateCompatibleBitmap(screen, vw, vh)
    gdi32.SelectObject(orig_dc, orig_bmp)
    gdi32.BitBlt(orig_dc, 0, 0, vw, vh, screen, vx, vy, SRCCOPY | CAPTUREBLT)
    dim_dc = gdi32.CreateCompatibleDC(screen)
    dim_bmp = gdi32.CreateCompatibleBitmap(screen, vw, vh)
    gdi32.SelectObject(dim_dc, dim_bmp)
    gdi32.BitBlt(dim_dc, 0, 0, vw, vh, orig_dc, 0, 0, SRCCOPY)
    black_dc = gdi32.CreateCompatibleDC(screen)
    black_bmp = gdi32.CreateCompatibleBitmap(screen, 1, 1)
    gdi32.SelectObject(black_dc, black_bmp)
    one = wintypes.RECT(0, 0, 1, 1)
    black_brush = gdi32.CreateSolidBrush(0x000000)
    user32.FillRect(black_dc, ctypes.byref(one), black_brush)
    msimg32.AlphaBlend(dim_dc, 0, 0, vw, vh, black_dc, 0, 0, 1, 1, BLENDFUNCTION(0, 0, 118, 0))
    back_dc = gdi32.CreateCompatibleDC(screen)
    back_bmp = gdi32.CreateCompatibleBitmap(screen, vw, vh)
    gdi32.SelectObject(back_dc, back_bmp)
    user32.ReleaseDC(None, screen)

    monitors: List[Tuple[int, int, int, int]] = []
    MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, HANDLE, HANDLE, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def on_monitor(_h, _dc, rect_ptr, _data):
        rect = rect_ptr.contents
        monitors.append((rect.left - vx, rect.top - vy, rect.right - vx, rect.bottom - vy))
        return True
    user32.EnumDisplayMonitors.argtypes = [HANDLE, ctypes.c_void_p, MONITORENUMPROC, wintypes.LPARAM]
    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(on_monitor), 0)
    if not monitors:
        monitors.append((0, 0, vw, vh))

    windows: List[Tuple[int, int, int, int]] = []
    own_pid = os.getpid()
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def on_window(hwnd, _data):
        try:
            if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == own_pid:
                return True
            if dwmapi is not None:
                cloaked = ctypes.c_int(0)
                dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
                if cloaked.value:
                    return True
            name = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, name, 64)
            if name.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
                return True
            rect = wintypes.RECT()
            got = False
            if dwmapi is not None:

                got = dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)) == 0
            if not got:
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
            left, top = max(rect.left - vx, 0), max(rect.top - vy, 0)
            right, bottom = min(rect.right - vx, vw), min(rect.bottom - vy, vh)
            if right - left >= 60 and bottom - top >= 40 and user32.GetWindowTextLengthW(hwnd) > 0:
                windows.append((left, top, right, bottom))
        except Exception:
            pass
        return True
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    user32.EnumWindows(WNDENUMPROC(on_window), 0)

    state: Dict[str, Any] = {"start": None, "end": None, "hover": None, "result": None, "was_active": False,
                             "cursor": (0, 0), "last_input": time.time(), "drag": False, "decor": None}
    accent = 0x63 | (0x66 << 8) | (0xF1 << 16)
    pen = gdi32.CreatePen(0, 2, accent)
    label_brush = gdi32.CreateSolidBrush(0x201A16)
    null_brush = gdi32.GetStockObject(5)

    def dpi_scale(x: int, y: int) -> float:
        if shcore is None:
            return 1.0
        try:
            monitor = user32.MonitorFromPoint(wintypes.POINT(x + vx, y + vy), 2)
            dx, dy = wintypes.UINT(), wintypes.UINT()
            if shcore.GetDpiForMonitor(monitor, 0, ctypes.byref(dx), ctypes.byref(dy)) == 0:
                return max(1.0, dx.value / 96.0)
        except Exception:
            pass
        return 1.0

    def monitor_at(x: int, y: int) -> Tuple[int, int, int, int]:
        for rect in monitors:
            if rect[0] <= x < rect[2] and rect[1] <= y < rect[3]:
                return rect
        return monitors[0]

    def window_at(x: int, y: int) -> Optional[Tuple[int, int, int, int]]:
        for rect in windows:
            if rect[0] <= x < rect[2] and rect[1] <= y < rect[3]:
                return rect
        return None

    def selection() -> Optional[Tuple[int, int, int, int]]:
        if state["start"] and state["end"]:
            (x1, y1), (x2, y2) = state["start"], state["end"]
            left, top, right, bottom = min(x1, x2), min(y1, y2), max(x1, x2) + 1, max(y1, y2) + 1
            return max(0, left), max(0, top), min(vw, right), min(vh, bottom)
        return state["hover"]

    fonts: Dict[int, Any] = {}

    def font(size: int):
        if size not in fonts:
            fonts[size] = gdi32.CreateFontW(-size, 0, 0, 0, 600, 0, 0, 0, 1, 0, 0, 5, 0, "Segoe UI")
        return fonts[size]

    def label_rect(sel, scale) -> Tuple[int, int, int, int]:
        width, height = int(118 * scale), int(26 * scale)
        left = sel[0]
        top = sel[1] - height - int(6 * scale)
        if top < 0:
            top = sel[1] + int(6 * scale)
        return left, top, left + width, top + height

    def hint_rect() -> Tuple[int, int, int, int, float]:
        mx, my = state["cursor"]
        mon = monitor_at(mx, my)
        scale = dpi_scale(mon[0] + 5, mon[1] + 5)
        width, height = int(640 * scale), int(40 * scale)
        left = mon[0] + (mon[2] - mon[0] - width) // 2
        top = mon[1] + int(22 * scale)
        return left, top, left + width, top + height, scale

    def decor_bounds() -> Tuple[int, int, int, int]:
        sel = selection()
        if not sel:
            return 0, 0, 0, 0
        scale = dpi_scale(sel[0], sel[1])
        lab = label_rect(sel, scale)
        pad = int(6 * scale)
        return (min(sel[0], lab[0]) - pad, min(sel[1], lab[1]) - pad,
                max(sel[2], lab[2]) + pad, max(sel[3], lab[3]) + pad)

    def invalidate(hwnd, bounds) -> None:
        if not bounds or bounds[2] <= bounds[0]:
            return
        rect = wintypes.RECT(*bounds)
        user32.InvalidateRect(hwnd, ctypes.byref(rect), False)

    def refresh(hwnd) -> None:
        old = state["decor"]
        new = decor_bounds()
        state["decor"] = new
        invalidate(hwnd, old)
        invalidate(hwnd, new)
        hint = hint_rect()
        invalidate(hwnd, hint[:4])

    def paint(hwnd) -> None:
        ps = PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
        r = ps.rcPaint
        w, h = r.right - r.left, r.bottom - r.top
        if w > 0 and h > 0:
            gdi32.BitBlt(back_dc, r.left, r.top, w, h, dim_dc, r.left, r.top, SRCCOPY)
            sel = selection()
            if sel:
                left, top = max(sel[0], r.left), max(sel[1], r.top)
                right, bottom = min(sel[2], r.right), min(sel[3], r.bottom)
                if right > left and bottom > top:
                    gdi32.BitBlt(back_dc, left, top, right - left, bottom - top, orig_dc, left, top, SRCCOPY)
                old_pen = gdi32.SelectObject(back_dc, pen)
                old_brush = gdi32.SelectObject(back_dc, null_brush)
                gdi32.Rectangle(back_dc, sel[0], sel[1], sel[2], sel[3])
                scale = dpi_scale(sel[0], sel[1])
                lab = wintypes.RECT(*label_rect(sel, scale))
                gdi32.SelectObject(back_dc, label_brush)
                gdi32.RoundRect(back_dc, lab.left, lab.top, lab.right, lab.bottom, int(10 * scale), int(10 * scale))
                gdi32.SelectObject(back_dc, old_pen)
                gdi32.SelectObject(back_dc, old_brush)
                gdi32.SetBkMode(back_dc, 1)
                gdi32.SetTextColor(back_dc, 0xFFFFFF)
                old_font = gdi32.SelectObject(back_dc, font(int(13 * scale)))
                user32.DrawTextW(back_dc, f"{sel[2] - sel[0]} × {sel[3] - sel[1]}", -1, ctypes.byref(lab), 0x25)
                gdi32.SelectObject(back_dc, old_font)
            left, top, right, bottom, scale = hint_rect()
            box = wintypes.RECT(left, top, right, bottom)
            old_brush = gdi32.SelectObject(back_dc, label_brush)
            old_pen = gdi32.SelectObject(back_dc, gdi32.GetStockObject(8))
            gdi32.RoundRect(back_dc, left, top, right, bottom, int(16 * scale), int(16 * scale))
            gdi32.SelectObject(back_dc, old_brush)
            gdi32.SelectObject(back_dc, old_pen)
            gdi32.SetBkMode(back_dc, 1)
            gdi32.SetTextColor(back_dc, 0xF2F2F2)
            old_font = gdi32.SelectObject(back_dc, font(int(14 * scale)))
            user32.DrawTextW(back_dc, HINTS.get(language, HINTS["ru"]), -1, ctypes.byref(box), 0x25)
            gdi32.SelectObject(back_dc, old_font)
            gdi32.BitBlt(hdc, r.left, r.top, w, h, back_dc, r.left, r.top, SRCCOPY)
        user32.EndPaint(hwnd, ctypes.byref(ps))

    def finish(hwnd, rect) -> None:
        state["result"] = rect
        user32.DestroyWindow(hwnd)

    def cancel(hwnd) -> None:
        state["result"] = None
        user32.DestroyWindow(hwnd)

    def point(lparam) -> Tuple[int, int]:
        x = ctypes.c_short(lparam & 0xFFFF).value
        y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
        return x, y

    cross = user32.LoadCursorW(None, ctypes.c_void_p(32515))

    WM_DESTROY, WM_PAINT, WM_ERASEBKGND, WM_ACTIVATE = 0x0002, 0x000F, 0x0014, 0x0006
    WM_SETCURSOR, WM_KEYDOWN, WM_MOUSEMOVE = 0x0020, 0x0100, 0x0200
    WM_LBUTTONDOWN, WM_LBUTTONUP, WM_RBUTTONDOWN, WM_TIMER = 0x0201, 0x0202, 0x0204, 0x0113

    def wndproc(hwnd, msg, wparam, lparam):
        try:
            if msg == WM_PAINT:
                paint(hwnd)
                return 0
            if msg == WM_ERASEBKGND:
                return 1
            if msg == WM_SETCURSOR:
                user32.SetCursor(cross)
                return 1
            if msg == WM_MOUSEMOVE:
                state["cursor"] = point(lparam)
                state["last_input"] = time.time()
                if state["drag"]:
                    state["end"] = state["cursor"]
                else:
                    state["hover"] = window_at(*state["cursor"])
                refresh(hwnd)
                return 0
            if msg == WM_LBUTTONDOWN:
                state["start"] = state["end"] = point(lparam)
                state["drag"] = True
                state["last_input"] = time.time()
                user32.SetCapture(hwnd)
                refresh(hwnd)
                return 0
            if msg == WM_LBUTTONUP and state["drag"]:
                user32.ReleaseCapture()
                state["drag"] = False
                state["end"] = point(lparam)
                (x1, y1), (x2, y2) = state["start"], state["end"]
                if abs(x2 - x1) >= 4 and abs(y2 - y1) >= 4:
                    finish(hwnd, selection())
                else:

                    state["start"] = state["end"] = None
                    finish(hwnd, window_at(x2, y2) or monitor_at(x2, y2))
                return 0
            if msg == WM_RBUTTONDOWN:
                if state["drag"] or state["start"]:
                    user32.ReleaseCapture()
                    state.update({"drag": False, "start": None, "end": None})
                    refresh(hwnd)
                else:
                    cancel(hwnd)
                return 0
            if msg == WM_KEYDOWN:
                state["last_input"] = time.time()
                if wparam == 0x1B:
                    cancel(hwnd)
                elif wparam == 0x0D:
                    sel = selection() if state["start"] else None
                    finish(hwnd, sel or monitor_at(*state["cursor"]))
                return 0
            if msg == WM_ACTIVATE:
                if wparam & 0xFFFF:
                    state["was_active"] = True
                elif state["was_active"] and state["result"] is None:
                    cancel(hwnd)
                return 0
            if msg == WM_TIMER:
                if wparam == 2:

                    if user32.GetForegroundWindow() != hwnd and user32.GetAsyncKeyState(0x1B) & 0x8000:
                        cancel(hwnd)
                    return 0
                if time.time() - state["last_input"] > TIMEOUT_SECONDS:
                    cancel(hwnd)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
        except Exception:
            user32.DestroyWindow(hwnd)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    proc = WNDPROC(wndproc)
    instance = kernel32.GetModuleHandleW(None)
    klass = WNDCLASSEXW()
    klass.cbSize = ctypes.sizeof(WNDCLASSEXW)
    klass.lpfnWndProc = proc
    klass.hInstance = instance
    klass.hCursor = cross
    klass.lpszClassName = "MemuAIRegionCapture"
    user32.RegisterClassExW(ctypes.byref(klass))
    cursor = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(cursor))
    state["cursor"] = (cursor.x - vx, cursor.y - vy)
    state["hover"] = window_at(*state["cursor"])
    state["decor"] = decor_bounds()
    WS_POPUP, WS_VISIBLE = 0x80000000, 0x10000000
    WS_EX_TOPMOST, WS_EX_TOOLWINDOW = 0x00000008, 0x00000080
    hwnd = user32.CreateWindowExW(WS_EX_TOPMOST | WS_EX_TOOLWINDOW, "MemuAIRegionCapture", "MemuAI",
                                  WS_POPUP | WS_VISIBLE, vx, vy, vw, vh, None, None, instance, None)
    if not hwnd:
        return {"ok": False, "error": f"overlay window failed ({ctypes.get_last_error()})"}
    _take_foreground(user32, kernel32, hwnd)
    user32.SetTimer(hwnd, 1, 1000, None)
    user32.SetTimer(hwnd, 2, 60, None)
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))

    rect = state["result"]
    result: Dict[str, Any] = {"ok": False, "code": "cancelled"}
    if rect and rect[2] - rect[0] >= 2 and rect[3] - rect[1] >= 2:
        left, top, right, bottom = rect
        width, height = right - left, bottom - top
        crop_dc = gdi32.CreateCompatibleDC(orig_dc)
        crop_bmp = gdi32.CreateCompatibleBitmap(orig_dc, width, height)
        default_bmp = gdi32.SelectObject(crop_dc, crop_bmp)
        gdi32.BitBlt(crop_dc, 0, 0, width, height, orig_dc, left, top, SRCCOPY)
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        buffer = ctypes.create_string_buffer(width * height * 4)
        gdi32.SelectObject(crop_dc, default_bmp)
        lines = gdi32.GetDIBits(crop_dc, crop_bmp, 0, height, buffer, ctypes.byref(info), 0)
        gdi32.DeleteDC(crop_dc)
        gdi32.DeleteObject(crop_bmp)
        if lines == height:
            from PIL import Image
            image = Image.frombuffer("RGBA", (width, height), buffer.raw, "raw", "BGRA", 0, 1).convert("RGB")
            image.save(out_path, "PNG", optimize=False)
            result = {"ok": True, "width": width, "height": height}
        else:
            result = {"ok": False, "error": "could not read the captured pixels"}
    for dc in (orig_dc, dim_dc, black_dc, back_dc):
        gdi32.DeleteDC(dc)
    for handle in (orig_bmp, dim_bmp, black_bmp, back_bmp, pen, label_brush, black_brush, *fonts.values()):
        gdi32.DeleteObject(handle)
    return result

def main(argv: List[str]) -> int:
    if len(argv) < 1:
        print(json.dumps({"ok": False, "error": "usage: screen_capture <out.png> [ru|en]"}))
        return 1
    out_path = argv[0]
    language = argv[1] if len(argv) > 1 else "ru"
    try:
        result = _run_overlay(out_path, language)
    except Exception as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    try:
        Path(out_path + ".json").write_text(json.dumps(result), encoding="utf-8")
    except OSError:
        pass
    try:
        if sys.stdout:
            sys.stdout.write(json.dumps(result) + "\n")
            sys.stdout.flush()
    except Exception:
        pass
    if result.get("ok"):
        return 0
    return 2 if result.get("code") == "cancelled" else 1

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
