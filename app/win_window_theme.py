"""Keep a Windows frame and both native WebView backing surfaces in the current theme.

CSS cannot paint DWM's non-client border or the WinForms/WebView fallback exposed
while a child surface is resized at fractional DPI. No size, style or hit-test is
changed here. All native calls are best effort and run on the caller's UI thread.
"""
from __future__ import annotations

import re
import sys
from typing import Any

_HEX = re.compile(r"#[0-9a-fA-F]{6}")


def background(value: Any, dark: bool = True) -> str:
    value = str(value or "").strip()
    return value.lower() if _HEX.fullmatch(value) else ("#17171a" if dark else "#f7f6f3")


def colorref(value: str) -> int:
    """DWM uses COLORREF (00BBGGRR), not HTML's RRGGBB."""
    rgb = background(value)
    return int(rgb[1:3], 16) | (int(rgb[3:5], 16) << 8) | (int(rgb[5:7], 16) << 16)


def apply_frame(hwnd: int, dark: bool, color: str = "", *, setter=None) -> bool:
    """Dark-mode flag plus explicit border/caption colours (Win11 attributes fail harmlessly on Win10)."""
    if not hwnd or (setter is None and sys.platform != "win32"):
        return False
    if setter is None:
        try:
            import ctypes
            from ctypes import wintypes
            api = ctypes.windll.dwmapi.DwmSetWindowAttribute
            api.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
            api.restype = ctypes.c_long
        except Exception:
            return False
        def setter(attribute, value):
            data = ctypes.c_uint32(value)
            return int(api(wintypes.HWND(hwnd), attribute, ctypes.byref(data), ctypes.sizeof(data))) == 0
    ok = False
    try:
        ok = bool(setter(20, int(bool(dark))))
        if not ok:
            ok = bool(setter(19, int(bool(dark))))
        value = colorref(background(color, dark))
        # USE_IMMERSIVE_DARK_MODE alone follows the Windows theme; an app can
        # independently choose a dark page while Windows remains light.
        for attribute, data in ((34, value), (35, value), (36, 0xF4F4F4 if dark else 0x202020)):
            try:
                setter(attribute, data)
            except Exception:
                pass
    except Exception:
        pass
    return ok


def apply_surface(native: Any, dark: bool, color: str = "", *, transparent: bool = False, color_factory=None) -> bool:
    """Update the form AND WebView2 background, preserving explicit Mica transparency."""
    if native is None:
        return False
    try:
        if color_factory is None:
            from System.Drawing import Color, ColorTranslator  # type: ignore
            color_factory = ColorTranslator.FromHtml
            transparent_color, black = Color.Transparent, Color.Black
        else:
            transparent_color, black = color_factory("transparent"), color_factory("#000000")
        opaque = color_factory(background(color, dark))
        native.BackColor = black if transparent else opaque
        seen = set()
        for holder in (getattr(native, "browser", None), native):
            control = getattr(holder, "webview", None) if holder is not None else None
            if control is None or id(control) in seen:
                continue
            seen.add(id(control))
            if hasattr(control, "DefaultBackgroundColor"):
                control.DefaultBackgroundColor = transparent_color if transparent else opaque
        return True
    except Exception:
        return False


def apply(native: Any, hwnd: int, dark: bool, color: str = "", *, transparent: bool = False) -> bool:
    frame = apply_frame(hwnd, dark, color)
    surface = apply_surface(native, dark, color, transparent=transparent)
    return frame or surface
