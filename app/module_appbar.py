"""Round 38 (п.6): the custom title bar («appbar») of a module installed as an app.

A module window (``module_apps.ModuleWindows``) used to be a normal Windows frame with the module's light
header strip under it — two bars on top of a dark player. Now the window is the same «caption removed,
frame kept» window as MemuAI / MemuFiles / MemuNote (``app/win_titlebar.TitlebarController``: Windows keeps
the resize borders, Aero Snap, shadow and rounded corners) and the page draws the bar itself
(``pgstatic/module-appbar.js``).

The bridge methods are named ``appbar_*`` (not ``titlebar_*``) on purpose: the module page is the MemuAI
page and also loads ``titlebar.js``, which must stay off in a module window.

Manifest (``window.appbar``)::

    {"style": "auto|transparent|none|custom", "height": 32..64, "color": "#rrggbb", "text": "#rrggbb",
     "overlay": bool}

- ``auto`` — a bar in the module's accent-tinted panel colour (default);
- ``transparent`` — the bar floats over the module's own content (``overlay`` forced on);
- ``custom`` — the module's ``color`` / ``text``;
- ``none`` — no bar, only the window buttons; the module marks its own drag areas with ``data-memu-drag``.
"""
from __future__ import annotations

import logging
import re
import sys
from typing import Any, Callable, Dict, Optional

log = logging.getLogger("memuai.module_appbar")

STYLES = ("auto", "transparent", "none", "custom")
HEIGHT_MIN, HEIGHT_MAX, HEIGHT_DEFAULT = 28, 64, 36
_COLOR = re.compile(r"#[0-9a-fA-F]{6}")

BUILTIN = {
    "music": {"style": "transparent", "text": "#f4f1f6", "overlay": True, "height": 36},
    "characters": {"style": "auto"},
    "translator": {"style": "auto"},
    "studio": {"style": "auto"},
    "design": {"style": "auto"},
    "api-models": {"style": "auto"},
    "benchmarks": {"style": "auto"},
    "catalog": {"style": "auto"},
}

def normalize(spec: Any) -> Dict[str, Any]:
    """A manifest's ``window.appbar`` — always a complete, safe dict."""
    raw = spec if isinstance(spec, dict) else {}
    style = str(raw.get("style") or "auto").strip().lower()
    if style not in STYLES:
        style = "auto"
    try:
        height = int(raw.get("height") or HEIGHT_DEFAULT)
    except (TypeError, ValueError):
        height = HEIGHT_DEFAULT
    color = str(raw.get("color") or "")
    text = str(raw.get("text") or "")
    return {
        "style": style,
        "height": max(HEIGHT_MIN, min(HEIGHT_MAX, height)),
        "color": color if _COLOR.fullmatch(color) else "",
        "text": text if _COLOR.fullmatch(text) else "",
        "overlay": style == "transparent" or bool(raw.get("overlay")),
    }

def for_module(module_id: str, window: Any) -> Dict[str, Any]:
    """The effective appbar of a module: its manifest, else the built-in default."""
    window = window if isinstance(window, dict) else {}
    if isinstance(window.get("appbar"), dict):
        return normalize(window["appbar"])
    return normalize(BUILTIN.get(module_id, {}))

def enabled(store: Dict[str, Any], module_id: str) -> bool:
    """Global switch AND the module's own switch (both on by default)."""
    if not store.get("custom_appbar", True):
        return False
    return bool((store.get("appbar") or {}).get(module_id, True))

def create_controller(*, platform: Optional[str] = None, environ: Optional[dict] = None,
                      factory: Optional[Callable[..., Any]] = None) -> Any:
    """A caption controller for one module window; ``None`` off Windows (system frame)."""
    platform = sys.platform if platform is None else platform
    if platform != "win32" and factory is None:
        return None
    try:
        from . import win_titlebar
        import os
        environ = os.environ if environ is None else environ
        make = factory or win_titlebar.TitlebarController
        return make(preference=win_titlebar.MODE_CUSTOM, env_value=environ.get(win_titlebar.ENV_VAR))
    except Exception as exc:  # noqa: BLE001 - the window still opens with the system frame
        log.warning("module appbar unavailable: %s", exc)
        return None

def attach(controller: Any, form: Any) -> bool:
    """Install the caption hook on the module window's WinForms form (UI thread)."""
    if controller is None or form is None:
        return False
    try:
        from .win_titlebar import winforms_ui_invoker

        def hwnd_provider() -> int:
            if not bool(getattr(form, "IsHandleCreated", True)):
                return 0
            return int(form.Handle.ToInt64())
        controller.native_surface = form
        return bool(controller.attach(hwnd_provider, winforms_ui_invoker(form)))
    except Exception as exc:  # noqa: BLE001
        log.warning("module appbar not attached: %s", exc)
        return False

def _press_win_z() -> bool:
    """Windows 11 Snap Layouts for the focused window (the same flyout as hovering the maximize button)."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        user32 = ctypes.windll.user32
        vk_lwin, vk_z, keyup = 0x5B, 0x5A, 0x0002
        user32.keybd_event(vk_lwin, 0, 0, 0)
        user32.keybd_event(vk_z, 0, 0, 0)
        user32.keybd_event(vk_z, 0, keyup, 0)
        user32.keybd_event(vk_lwin, 0, keyup, 0)
        return True
    except Exception:  # noqa: BLE001
        return False

class ModuleAppbarBridge:
    """``window.pywebview.api.appbar_*`` for a module window (pywebview exposes every public method)."""

    def __init__(self, controller: Any, module_id: str, spec: Dict[str, Any],
                 snap: Callable[[], bool] = _press_win_z) -> None:
        self._controller = controller
        self._module_id = module_id
        self._spec = dict(spec)
        self._snap = snap

    def appbar_state(self) -> dict:
        state = dict(self._controller.state()) if self._controller is not None else {"installed": False}
        state["module"] = self._module_id
        state["appbar"] = dict(self._spec)
        return state

    def appbar_theme(self, dark: bool = True, background: str = "") -> bool:
        """The module can have a different theme from its MemuAI host."""
        controller = self._controller
        if controller is None:
            return False
        try:
            from .win_window_theme import apply
            result = bool(controller._invoke(lambda: apply(getattr(controller, "native_surface", None),
                int(controller._hwnd_provider() or 0), bool(dark), background)))
            if result:
                controller.native_theme = (bool(dark), background)
            return result
        except Exception:
            return False

    def appbar_drag(self, edge: str = "caption") -> bool:
        return bool(self._controller is not None and self._controller.start_drag(edge))

    def appbar_command(self, command: str = "") -> bool:
        return bool(self._controller is not None and self._controller.command(command))

    def appbar_system_menu(self, source: str = "mouse") -> bool:
        return bool(self._controller is not None and self._controller.system_menu(source))

    def appbar_snap_layouts(self) -> bool:
        state = self._controller.state() if self._controller is not None else {}
        if not state.get("installed") or state.get("os") != "win11":
            return False
        return bool(self._snap())

    def appbar_set_mode(self, custom: bool = True) -> dict:
        from .win_titlebar import MODE_CUSTOM, MODE_NATIVE
        if self._controller is None:
            return {"installed": False}
        return self._controller.set_mode(MODE_CUSTOM if custom else MODE_NATIVE)
