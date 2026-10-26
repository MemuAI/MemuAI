"""Round 30 (r30-design): the Windows 11 window material behind MemuAI — Mica, Acrylic or «Tabbed».

Settings → Appearance → «Материал окна» (pref ``ui_backdrop``). The page asks through the pywebview bridge
(``TitlebarBridge.window_backdrop`` in ``app/win_titlebar.py``); this module does the native part:

* ``DwmSetWindowAttribute(DWMWA_SYSTEMBACKDROP_TYPE = 38)`` — Windows 11 22H2 (build 22621) and newer;
  build 22000–22620 only knows the undocumented ``DWMWA_MICA_EFFECT = 1029`` (Mica only);
* ``DwmExtendFrameIntoClientArea`` with negative margins — the material fills the client area;
* the WebView2 control gets a transparent ``DefaultBackgroundColor`` so the material shows through the page
  (the page then paints its chrome with see-through colours: ``html[data-backdrop]`` in appearance-plus.css).

Everything is best effort and never raises: an older Windows, a WebView2 without transparency or any error
leaves the normal opaque window, and ``state()`` says why. The decision logic is pure and unit-tested on any OS.
"""
from __future__ import annotations

import logging
import sys
from typing import Any, Callable, Dict, Optional

log = logging.getLogger("memuai.backdrop")

KINDS = ("none", "mica", "acrylic", "tabbed")

_SYSTEMBACKDROP = {"none": 1, "mica": 2, "acrylic": 3, "tabbed": 4}
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMWA_MICA_EFFECT = 1029
BUILD_SYSTEMBACKDROP = 22621
BUILD_MICA_LEGACY = 22000

def normalize_kind(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in KINDS else "none"

def supported_kinds(build: Optional[int], platform: str = sys.platform) -> tuple:
    """The materials this Windows can draw (``none`` is always there)."""
    if platform != "win32" or not build:
        return ("none",)
    if build >= BUILD_SYSTEMBACKDROP:
        return KINDS
    if build >= BUILD_MICA_LEGACY:
        return ("none", "mica")
    return ("none",)

def plan(kind: Any, build: Optional[int], platform: str = sys.platform) -> Dict[str, Any]:
    """What to call for ``kind``: {"kind", "attribute", "value", "extend", "reason"} — no side effects."""
    want = normalize_kind(kind)
    kinds = supported_kinds(build, platform)
    if want not in kinds:
        return {"kind": "none", "attribute": None, "value": 0, "extend": False,
                "reason": "windows11" if platform == "win32" else "platform"}
    if want == "none":
        attr = DWMWA_SYSTEMBACKDROP_TYPE if (build or 0) >= BUILD_SYSTEMBACKDROP else (DWMWA_MICA_EFFECT if (build or 0) >= BUILD_MICA_LEGACY else None)
        value = _SYSTEMBACKDROP["none"] if attr == DWMWA_SYSTEMBACKDROP_TYPE else 0
        return {"kind": "none", "attribute": attr, "value": value, "extend": False, "reason": ""}
    if (build or 0) >= BUILD_SYSTEMBACKDROP:
        return {"kind": want, "attribute": DWMWA_SYSTEMBACKDROP_TYPE, "value": _SYSTEMBACKDROP[want], "extend": True, "reason": ""}
    return {"kind": "mica", "attribute": DWMWA_MICA_EFFECT, "value": 1, "extend": True, "reason": ""}

class BackdropController:
    """Applies a material to one native window. ``invoke`` runs a callable on the WinForms UI thread."""

    def __init__(self, hwnd_provider: Callable[[], int], invoke: Callable[[Callable[[], Any]], Any],
                 native: Any = None, build: Optional[int] = None) -> None:
        self._hwnd = hwnd_provider
        self._invoke = invoke
        self._native = native
        if build is None:
            try:
                from .win_titlebar import windows_build
                build = windows_build()
            except Exception:
                build = None
        self.build = build
        self.active = "none"
        self.error = ""

    def state(self) -> Dict[str, Any]:
        return {"available": len(supported_kinds(self.build)) > 1, "kinds": list(supported_kinds(self.build)),
                "active": self.active, "build": self.build, "error": self.error}

    def apply(self, kind: Any, dark: bool = True, background: str = "") -> Dict[str, Any]:
        step = plan(kind, self.build)
        from .win_window_theme import background as normalize_background
        step["background"] = normalize_background(background, dark)
        try:
            ok = bool(self._invoke(lambda: self._apply_on_ui(step, bool(dark))))
            self.active = step["kind"] if ok else "none"
            self.error = "" if ok or step["reason"] else (self.error or "dwm")
        except Exception as exc:  # noqa: BLE001 — never break the window over a cosmetic feature
            log.warning("backdrop: %s", exc)
            self.active, self.error = "none", str(exc)[:200]
        if step["reason"]:
            self.error = step["reason"]
        return self.state()

    def _apply_on_ui(self, step: Dict[str, Any], dark: bool) -> bool:
        import ctypes
        from ctypes import wintypes
        hwnd = int(self._hwnd() or 0)
        if not hwnd:
            return False
        dwm = ctypes.windll.dwmapi
        on = step["kind"] != "none"
        from .win_window_theme import apply_frame, apply_surface
        color = step.get("background", "")
        apply_frame(hwnd, dark, color)

        class MARGINS(ctypes.Structure):
            _fields_ = [("left", ctypes.c_int), ("right", ctypes.c_int), ("top", ctypes.c_int), ("bottom", ctypes.c_int)]

        margins = MARGINS(-1, -1, -1, -1) if on else MARGINS(0, 0, 1, 0)
        dwm.DwmExtendFrameIntoClientArea(wintypes.HWND(hwnd), ctypes.byref(margins))
        self._webview_transparent(on)
        apply_surface(self._native, dark, color, transparent=on)
        result = 0
        if step["attribute"] is not None:
            value = ctypes.c_int(int(step["value"]))
            result = dwm.DwmSetWindowAttribute(wintypes.HWND(hwnd), int(step["attribute"]), ctypes.byref(value), ctypes.sizeof(value))
        if on and result != 0:

            dwm.DwmExtendFrameIntoClientArea(wintypes.HWND(hwnd), ctypes.byref(MARGINS(0, 0, 1, 0)))
            self._webview_transparent(False)
            apply_surface(self._native, dark, color)
            self.error = f"DwmSetWindowAttribute={result}"
            return False
        return True

    def _webview_transparent(self, on: bool) -> None:
        """WebView2 paints its own opaque background; a transparent one lets DWM show through the page."""
        native = self._native
        if native is None:
            return
        try:
            from System.Drawing import Color  # type: ignore
        except Exception:
            return
        for holder in (getattr(native, "browser", None), native):
            control = getattr(holder, "webview", None) if holder is not None else None
            if control is not None and hasattr(control, "DefaultBackgroundColor"):
                try:
                    if not hasattr(self, "_orig_webview_bg"):
                        self._orig_webview_bg = control.DefaultBackgroundColor
                    control.DefaultBackgroundColor = Color.Transparent if on else self._orig_webview_bg
                except Exception as exc:  # noqa: BLE001
                    log.info("backdrop: WebView2 transparency unavailable (%s)", exc)
                break
        try:
            if not hasattr(self, "_orig_form_bg"):
                self._orig_form_bg = native.BackColor
            native.BackColor = Color.Black if on else self._orig_form_bg
        except Exception:
            pass

def create(native: Any, hwnd_provider: Callable[[], int], invoke: Callable[[Callable[[], Any]], Any]) -> Optional[BackdropController]:
    """A controller for the main window, or ``None`` off Windows. Never raises."""
    if sys.platform != "win32":
        return None
    try:
        return BackdropController(hwnd_provider, invoke, native)
    except Exception as exc:  # noqa: BLE001
        log.info("backdrop unavailable: %s", exc)
        return None
