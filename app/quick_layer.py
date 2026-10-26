"""Round 28: the screen layer of Quick Ask (Windows side) — panels and quick-action buttons anywhere on the screen.

One frameless, always-on-top pywebview window (``/quick/layer.html``), created lazily and kept warm like the Quick Ask
window. When Quick Ask opens it covers the WORK AREA of the monitor Quick Ask opened on (taskbar excluded, physical
pixels, negative coordinates for monitors left of / above the primary one) and the page draws the user's panels and
buttons at their saved spots (:mod:`app.quick_layer_core`). It hides together with Quick Ask.

Click-through except the items: the window is clipped with ``SetWindowRgn`` to the union of the items' rounded
rectangles that the page reports (``/api/quick/layer/region``) — everywhere else clicks and the picture belong to the
desktop and the programs under it. While the user arranges things (edit mode) or a panel's options dialog is open the
region becomes the whole work area minus the Quick Ask window, so a drag can go anywhere. Behind the page the window is
DWM "glass" (the frame extended into the client area, a black form, a transparent WebView2 background — the same
technique as the acrylic Quick Ask window); where that fails the edit mode simply dims the desktop.

The layer never takes the focus by itself (``SW_SHOWNOACTIVATE``); clicking an item activates it like any window
(To-Do fields need the keyboard). Quick Ask is re-raised above it right after it is shown and whenever the layer gets
the focus, and the layout never puts an item under the Quick Ask window.

Every Win32 call is wrapped; a failure here only means the panels stay in the Quick Ask window (Round 27 fallback),
never a broken Quick Ask. Off Windows :class:`ScreenLayer` is inert (``available() is False``).
"""
from __future__ import annotations

import json
import logging
import math
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Sequence

from . import quick_layer_core as core

log = logging.getLogger("memuai.quick.layer")

TITLE = "MemuAI Quick · layer"
CLICK_GUARD = 0.3
RGN_OR, RGN_DIFF = 2, 4
SW_SHOWNOACTIVATE = 4
MAX_REGION_RECTS = 160

def effective_scale(scale: float, size: Sequence[int], view: Optional[Dict[str, Any]] = None) -> float:
    """CSS px → window pixels, as the page really renders (pure; unit-tested).

    Round 29: the region used the monitor's nominal DPI (``area["scale"]``). When WebView2 renders with another factor
    (a system-DPI-aware process, a WinForms rescale after moving between monitors, a zoomed page) the clip region and
    the cards drifted apart — the bottom / right of a card was cut off. The page now sends its real viewport
    (``view {w, h}`` CSS px); the window's physical size divided by it is the factor that is actually in effect.
    A missing or implausible view (0, NaN, a ratio far from the nominal one) keeps the nominal scale."""
    nominal = float(scale or 1.0)
    if not isinstance(view, dict):
        return nominal
    try:
        vw, vh = float(view.get("w") or 0), float(view.get("h") or 0)
        width, height = float(size[0]), float(size[1])
    except (TypeError, ValueError, IndexError):
        return nominal
    if not (math.isfinite(vw) and math.isfinite(vh)) or vw < 100 or vh < 100 or width < 100 or height < 100:
        return nominal
    sx, sy = width / vw, height / vh
    if abs(sx - sy) > 0.05 * max(sx, sy) or not 0.5 <= sx <= 5:
        return nominal
    real = round((sx + sy) / 2, 4)
    return nominal if abs(real - nominal) < 0.01 else real

def region_plan(rects: Sequence[Sequence[float]], scale: float, size: Sequence[int], *, full: bool = False,
                keepout: Optional[Sequence[float]] = None) -> Dict[str, Any]:
    """What ``SetWindowRgn`` gets, in window-relative PHYSICAL pixels (pure; unit-tested).

    ``rects``: ``[[x, y, w, h, radius]]`` CSS px from the page; ``size``: the window's physical (w, h).
    ``full``: the whole window minus the Quick Ask window (``keepout`` CSS rect) — edit mode / a modal dialog.
    Returns ``{"full": bool, "rects": [[l, t, r, b, diameter]], "minus": [l, t, r, b] | None}``."""
    width, height = int(size[0]), int(size[1])
    scale = float(scale or 1.0)

    def phys(x: float, y: float, w: float, h: float, outward: bool = True) -> List[int]:

        lo, hi = (math.floor, math.ceil) if outward else (round, round)
        l = max(0, min(width, int(lo(x * scale + 1e-6))))
        t = max(0, min(height, int(lo(y * scale + 1e-6))))
        r = max(0, min(width, int(hi((x + w) * scale - 1e-6))))
        b = max(0, min(height, int(hi((y + h) * scale - 1e-6))))
        return [l, t, r, b]
    minus = None
    if keepout:
        try:
            box = phys(*(float(v) for v in list(keepout)[:4]), outward=False)
            if box[2] > box[0] and box[3] > box[1]:
                minus = box
        except (TypeError, ValueError):
            minus = None
    if full:
        return {"full": True, "rects": [[0, 0, width, height, 0]], "minus": minus}
    out = []
    for row in list(rects)[:MAX_REGION_RECTS]:
        try:
            x, y, w, h = (float(v) for v in row[:4])
            radius = float(row[4]) if len(row) > 4 else 0.0
        except (TypeError, ValueError):
            continue
        if w <= 0 or h <= 0:
            continue
        box = phys(x, y, w, h)
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        diameter = max(0, min(int(round(2 * radius * scale)), box[2] - box[0], box[3] - box[1]))
        out.append(box + [diameter])
    return {"full": False, "rects": out, "minus": minus}

class ScreenLayer:
    """The layer window. All methods are safe to call from any worker thread (never from the WinForms UI thread)."""

    def __init__(self, manager: Any) -> None:
        self.manager = manager
        self.window = None
        self.form = None
        self.hwnd = 0
        self.visible = False
        self.edit = False
        self.glass = False
        self.shown_at = 0.0
        self.area: Optional[Dict[str, Any]] = None
        self.keepout: Dict[str, Any] = {}
        self.size = (0, 0)
        self.failed = False
        self._lock = threading.RLock()
        self._create_lock = threading.Lock()
        self._loaded = threading.Event()
        self._region_key = ""
        self._quitting = False
        self.native_ok = sys.platform == "win32" and bool(getattr(manager, "native_ok", False))
        self.gdi = None

    def available(self) -> bool:
        return self.native_ok and not self.failed and not self._quitting

    def public(self) -> Dict[str, Any]:
        return {"available": self.available(), "visible": self.visible, "edit": self.edit, "glass": self.glass,
                "monitor": (self.area or {}).get("key", "")}

    def _gdi(self):
        if self.gdi is None:
            import ctypes
            gdi32 = ctypes.WinDLL("gdi32")
            for name, args in (("CreateRectRgn", 4), ("CreateRoundRectRgn", 6)):
                fn = getattr(gdi32, name)
                fn.restype = ctypes.c_void_p
                fn.argtypes = [ctypes.c_int] * args
            gdi32.CombineRgn.restype = ctypes.c_int
            gdi32.CombineRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
            gdi32.DeleteObject.restype = ctypes.c_int
            gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
            self.gdi = gdi32
        return self.gdi

    def ensure_window(self) -> bool:
        if not self.available():
            return False
        api = getattr(self.manager, "api", None)
        if self.hwnd and api and api.IsWindow(self.hwnd):
            return True
        with self._create_lock:
            if self.hwnd and api.IsWindow(self.hwnd):
                return True
            try:
                import webview
            except Exception:
                self.failed = True
                return False
            prefs = self.manager.load_prefs() or {}
            dark = prefs.get("theme") != "light"
            options = dict(width=800, height=600, frameless=True, easy_drag=False, on_top=True, hidden=True, focus=True,
                           resizable=False, shadow=False, background_color="#000000")
            window = None
            for _attempt in range(2):
                try:
                    window = webview.create_window(TITLE, self.manager.base_url + "/quick/layer.html?native=1", **options)
                    break
                except TypeError:
                    for key in ("focus", "shadow", "easy_drag"):
                        options.pop(key, None)
                except Exception as exc:
                    log.warning("quick layer: create_window failed: %s", exc)
                    break
            if window is None:
                self.failed = True
                return False
            self.window = window
            self._loaded.clear()
            window.events.loaded += lambda *_: self._loaded.set()
            window.events.closing += self._on_closing
            deadline = time.monotonic() + 8
            form = None
            while time.monotonic() < deadline and form is None:
                form = getattr(window, "native", None)
                if form is None:
                    time.sleep(0.05)
            if form is None:
                self.failed = True
                return False
            self.form = form
            self.hwnd = int(self._ui(lambda: int(form.Handle.ToInt64())) or 0)
            if not self.hwnd:
                self.failed = True
                return False
            self._style(dark)
            try:
                from .desktop import grant_local_permissions
                grant_local_permissions(form, self.manager.base_url)
            except Exception:
                pass
            self._loaded.wait(10)
            self._empty_region()
            return True

    def _style(self, dark: bool) -> None:
        from .quick_ask import GWL_EXSTYLE, WS_EX_APPWINDOW, WS_EX_TOOLWINDOW
        api, hwnd, form = self.manager.api, self.hwnd, self.form
        try:
            style = int(api.GetWindowLong(hwnd, GWL_EXSTYLE))
            api.SetWindowLong(hwnd, GWL_EXSTYLE, (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)
        except Exception:
            pass

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
        try:
            self.glass = bool(self._ui(transparent)) and api.extend_frame(hwnd, -1)
            api.dwm_int(hwnd, 38, 1)
            api.dwm_int(hwnd, 33, 1)
            api.dwm_int(hwnd, 34, 0xFFFFFFFE)
        except Exception:
            self.glass = False

    def _on_closing(self, *_args) -> bool:
        if self._quitting:
            return True
        threading.Thread(target=self.manager.hide, daemon=True).start()
        return False

    def _ui(self, fn):
        form = self.form
        if form is None:
            return None
        try:
            from .win_titlebar import winforms_ui_invoker
            return winforms_ui_invoker(form)(fn)
        except Exception as exc:
            log.debug("quick layer: UI call failed: %s", exc)
            return None

    def _push(self, script: str) -> Any:
        window = self.window
        if window is None or not self._loaded.is_set():
            return None
        try:
            return window.evaluate_js(script)
        except Exception as exc:
            log.debug("quick layer: evaluate_js failed: %s", exc)
            return None

    def show(self, geometry: Dict[str, Any], monitors: Sequence[Dict[str, Any]], *, reason: str = "open") -> bool:
        """Cover the work area of the monitor Quick Ask is on (``geometry["monitor"]``) and draw the items."""
        if not self.ensure_window():
            return False
        api = self.manager.api
        from .quick_ask import SWP_NOACTIVATE, SWP_NOOWNERZORDER
        index = min(max(int(geometry.get("monitor", 0)), 0), len(monitors) - 1)
        monitor = monitors[index]
        area = core.area_of(monitor)
        keepout = core.keepout_for(geometry, area)
        left, top, right, bottom = area["physical"]
        with self._lock:
            moved = not self.area or self.area["physical"] != area["physical"] or self.area["scale"] != area["scale"]
            self.area, self.keepout = area, keepout
            self.size = (right - left, bottom - top)
            if not self.visible or moved:
                self._empty_region()
            flags = SWP_NOACTIVATE | SWP_NOOWNERZORDER

            insert = self.insert_after()
            api.SetWindowPos(self.hwnd, insert, left, top, right - left, bottom - top, flags)
            if not self.visible:
                self._ui(lambda: (api.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE), None)[1])

            api.SetWindowPos(self.hwnd, insert, left, top, right - left, bottom - top, flags)
            first = not self.visible
            self.visible = True
            if first:
                self.shown_at = time.monotonic()
        payload = {"reason": reason, "monitor": {"key": area["key"], "w": area["w"], "h": area["h"], "scale": area["scale"]},
                   "keepout": keepout, "glass": self.glass, "fresh": first}
        self._push("window.MemuLayer && MemuLayer.onShow(%s)" % json.dumps(payload))
        self._raise_quick()
        return True

    def insert_after(self) -> int:
        """Round 32: hWndInsertAfter for the layer — topmost, or right under a stepped-back Quick Ask window."""
        from .quick_ask import HWND_TOPMOST
        manager = self.manager
        if getattr(manager, "lowered", False) and not getattr(manager, "pinned", False) and int(getattr(manager, "hwnd", 0) or 0):
            return int(manager.hwnd)
        return HWND_TOPMOST

    def update_keepout(self, geometry: Dict[str, Any]) -> None:
        """Quick Ask moved or changed its size: the items step aside (display only, nothing is saved)."""
        if not self.visible or not self.area:
            return
        keepout = core.keepout_for(geometry, self.area)
        if keepout == self.keepout:
            return
        self.keepout = keepout
        self._region_key = ""
        self._push("window.MemuLayer && MemuLayer.setKeepout(%s)" % json.dumps(keepout))

    def hide(self) -> bool:
        if not self.hwnd or not self.visible:
            self.visible = False
            return False

        api = getattr(self.manager, "api", None)
        try:
            if api is not None and getattr(api, "ShowWindowAsync", None):
                api.ShowWindowAsync(self.hwnd, 0)
        except Exception:
            pass
        with self._lock:
            self._ui(lambda: (self.form.Hide(), None)[1])
            self.visible = False
            self.edit = False
            self._region_key = ""
        def tell() -> None:
            if not self.visible:
                self._push("window.MemuLayer && MemuLayer.onHide()")
        threading.Thread(target=tell, name="memuai-quick-layer-push", daemon=True).start()
        return True

    def set_edit(self, on: bool) -> bool:
        self.edit = bool(on) and self.visible
        if self.visible:
            self._push("window.MemuLayer && MemuLayer.setEdit(%s)" % ("true" if self.edit else "false"))
            if self.edit:
                self.focus()
        return self.edit

    def focus(self) -> bool:
        if not self.hwnd or not self.visible:
            return False
        api = self.manager.api
        ok = api.take_foreground(self.hwnd)
        self._ui(self._focus_browser)
        return ok

    def _focus_browser(self) -> None:
        try:
            self.form.Activate()
            browser = getattr(self.form, "webview", None)
            if browser is not None:
                browser.Focus()
        except Exception:
            pass

    def _raise_quick(self) -> None:
        """Quick Ask stays above the layer (the layer is topmost too and became the latest topmost window)."""
        manager = self.manager
        try:
            if getattr(manager, "visible", False) and not getattr(manager, "lowered", False):
                from .quick_ask import HWND_TOPMOST, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOOWNERZORDER, SWP_NOSIZE
                manager.api.SetWindowPos(manager.hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                                         SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER)
        except Exception:
            pass

    def on_focus(self) -> None:
        """The page got the focus (the user clicked an item): Quick Ask must not end up under the layer."""
        self._raise_quick()

    def accept_click(self) -> bool:
        """The 300 ms guard of Round 27, for the layer too: the click that finished opening runs nothing."""
        return not self.visible or time.monotonic() - self.shown_at >= CLICK_GUARD

    def _empty_region(self) -> None:
        if not self.hwnd:
            return
        try:
            gdi = self._gdi()
            region = gdi.CreateRectRgn(0, 0, 0, 0)
            if region and not self.manager.api.SetWindowRgn(self.hwnd, region, True):
                gdi.DeleteObject(region)
        except Exception as exc:
            log.debug("quick layer: empty region failed: %s", exc)

    def set_region(self, rects: Sequence[Sequence[float]], full: bool = False,
                   view: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self.hwnd or not self.visible or not self.area:
            return {"ok": False}
        hard = (self.keepout or {}).get("hard")
        scale = effective_scale(self.area["scale"], self.size, view)
        plan = region_plan(rects, scale, self.size, full=full, keepout=hard if full else None)
        key = json.dumps(plan, separators=(",", ":"))
        if key == self._region_key:
            return {"ok": True, "same": True}
        try:
            gdi = self._gdi()
            region = gdi.CreateRectRgn(0, 0, 0, 0)
            for l, t, r, b, d in plan["rects"]:
                part = gdi.CreateRoundRectRgn(l, t, r + 1, b + 1, d, d) if d else gdi.CreateRectRgn(l, t, r, b)
                if part:
                    gdi.CombineRgn(region, region, part, RGN_OR)
                    gdi.DeleteObject(part)
            if plan["minus"]:
                part = gdi.CreateRectRgn(*plan["minus"])
                if part:
                    gdi.CombineRgn(region, region, part, RGN_DIFF)
                    gdi.DeleteObject(part)
            if not self.manager.api.SetWindowRgn(self.hwnd, region, True):
                gdi.DeleteObject(region)
                return {"ok": False}
            self._region_key = key
        except Exception as exc:
            log.debug("quick layer: region failed: %s", exc)
            return {"ok": False}
        return {"ok": True, "rects": len(plan["rects"]), "full": plan["full"]}

    def relay(self, message: Dict[str, Any]) -> bool:
        if not self.window or not self.visible:
            return False
        self._push("window.MemuQuickBus && MemuQuickBus.receive(%s)" % json.dumps(message, ensure_ascii=False))
        return True

    def destroy(self) -> None:
        self._quitting = True
        window, self.window = self.window, None
        if window is not None:
            try:
                window.destroy()
            except Exception:
                pass
        self.hwnd = 0
        self.visible = False
