"""Round 27: panels and «Быстрые действия» in the Quick Ask window (plugin ``canvas``, ``pgstatic/quick/plugins``).

* **Panels** are the MemuAI panels themselves: the window loads ``pgstatic/panels.js`` in embed mode and reads
  ``/api/pg/panels`` — one source (``panels.json`` + ``panels-local.json``), the same refresh loop, To-Do,
  notes and timers. This module keeps only WHERE they sit in Quick Ask: a layout of its own, so arranging
  the window never moves anything in MemuAI (``placements``/``order`` of a panel are untouched).
* **Buttons** (:mod:`app.quick_actions`) — the user's round buttons and macros, executed on the server.

Storage — ``memuai/[profiles/<id>/]quick_canvas.json`` (outside ``preferences.json``: the main window saves
whole snapshots of it)::

    {version, settings{…}, ui{open}, layout{cols: 8, items[{id, type: panel|action, ref, x, y, w, h, pinned}]},
     actions[…]}

The layout grid has ``COLS`` = 8 square columns. A panel is at least 2×2 (S 2×2, M 4×2, L 4×4, W 8×2), a
button 1×1 or 2×2. :func:`resolve_layout` is the server-side guarantee: clamped sizes, no overlaps (a
colliding item moves down), unique ids — the page computes the same for narrow windows (6/4 columns).

Routes ``/api/quick/canvas/*`` (registered through :func:`app.quick_ask_api.register_router`).

Round 28 — the screen layer. The panels and buttons are no longer drawn inside the window: they sit ANYWHERE on the
screen, on a click-through layer over the monitor Quick Ask opened on (``app/quick_layer.py``, ``/quick/layer.html``).
``layout.items`` stays the list of what is placed (with its size class in grid units and the Round 27 grid cell, kept
for compatibility); WHERE each item is on the screen lives in ``layer`` (:mod:`app.quick_layer_core`: one layout per
monitor + the latest arrangement as the default other monitors adapt from). A Round 27 file is migrated on load:
its grid keeps its shape as a block in the top-right corner. Routes ``/api/quick/layer/*``. The window keeps the
in-window grid only as a fallback when the layer cannot be shown (not Windows, WebView2 failure).
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import quick_actions as qa
from . import quick_layer_core as lc
from . import quick_panel_look as look_mod

log = logging.getLogger("memuai.quick.canvas")

FILE_NAME = "quick_canvas.json"
SCHEMA_VERSION = 2
COLS = 8
MAX_ITEMS = 80
MAX_ROWS = 200
ITEM_ID_RE = re.compile(r"^i_[a-z0-9]{4,24}$")
PANEL_ID_RE = re.compile(r"^p_[A-Za-z0-9_-]{1,40}$")
PANEL_SIZES = {"s": (2, 2), "m": (4, 2), "l": (4, 4), "w": (8, 2)}
OPEN_MODES = ("input", "canvas", "last")
SCREEN_MODES = ("show", "hidden", "last")
DENSITIES = ("compact", "normal", "large")

DEFAULT_SETTINGS: Dict[str, Any] = {
    "enabled": True,
    "panels": True,
    "actions": True,
    "open_mode": "input",
    "strip": True,
    "collapse_on_answer": True,
    "density": "normal",
    "labels": True,
    "screen": "show",
}

def tr(lang: str, ru: str, en: str) -> str:
    return en if lang == "en" else ru

def _int(value: Any, lo: int, hi: int, default: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, number))

def clean_item(raw: Any, cols: int = COLS) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    kind = raw.get("type")
    ref = str(raw.get("ref") or "")
    if kind == "panel":
        if not PANEL_ID_RE.match(ref):
            return None
        w = _int(raw.get("w"), 2, cols, 2)
        h = _int(raw.get("h"), 2, 8, 2)
    elif kind == "action":
        if not qa.ID_RE.match(ref):
            return None
        w = h = 2 if _int(raw.get("w"), 1, 2, 1) == 2 else 1
    else:
        return None
    item_id = str(raw.get("id") or "")
    if not ITEM_ID_RE.match(item_id):
        item_id = "i_" + uuid.uuid4().hex[:10]
    x = _int(raw.get("x"), 0, cols - w, 0)
    y = _int(raw.get("y"), 0, MAX_ROWS, 0)
    out = {"id": item_id, "type": kind, "ref": ref, "x": x, "y": y, "w": w, "h": h}
    if raw.get("pinned") is True:
        out["pinned"] = True
    return out

def _overlaps(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    return a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"] and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"]

def resolve_layout(items: Any, cols: int = COLS) -> List[Dict[str, Any]]:
    """Clean items and remove every overlap: in reading order (y, then x) an item that collides with one
    already placed moves down to the first free row. Duplicate ids/refs keep the first one."""
    rows = items if isinstance(items, list) else []
    cleaned: List[Dict[str, Any]] = []
    seen_ids, seen_refs = set(), set()
    for raw in rows[:MAX_ITEMS * 2]:
        item = clean_item(raw, cols)
        if not item:
            continue
        key = (item["type"], item["ref"])
        if item["id"] in seen_ids or key in seen_refs:
            continue
        seen_ids.add(item["id"])
        seen_refs.add(key)
        cleaned.append(item)
        if len(cleaned) >= MAX_ITEMS:
            break
    placed: List[Dict[str, Any]] = []
    for item in sorted(cleaned, key=lambda r: (r["y"], r["x"])):
        while any(_overlaps(item, other) for other in placed):
            item["y"] += 1
        placed.append(item)
    return placed

def first_free(items: List[Dict[str, Any]], w: int, h: int, cols: int = COLS) -> Tuple[int, int]:
    """The first spot (reading order) where a w×h item fits."""
    for y in range(0, MAX_ROWS + 1):
        for x in range(0, cols - w + 1):
            probe = {"x": x, "y": y, "w": w, "h": h}
            if not any(_overlaps(probe, other) for other in items):
                return x, y
    return 0, MAX_ROWS

def add_item(items: List[Dict[str, Any]], kind: str, ref: str, size: str = "", place: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if kind == "panel":
        w, h = PANEL_SIZES.get(size or "s", PANEL_SIZES["s"])
    else:
        w = h = 2 if size == "l" else 1
    if place and isinstance(place, dict) and "x" in place and "y" in place:
        x, y = _int(place.get("x"), 0, COLS - w, 0), _int(place.get("y"), 0, MAX_ROWS, 0)
    else:
        x, y = first_free(items, w, h)
    item = clean_item({"type": kind, "ref": ref, "x": x, "y": y, "w": w, "h": h})
    items.append(item)
    items[:] = resolve_layout(items)
    return next(i for i in items if i["ref"] == ref and i["type"] == kind)

def normalize_settings(raw: Any) -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULT_SETTINGS)
    for key in ("enabled", "panels", "actions", "strip", "collapse_on_answer", "labels"):
        if isinstance(raw.get(key), bool):
            out[key] = raw[key]
    if raw.get("open_mode") in OPEN_MODES:
        out["open_mode"] = raw["open_mode"]
    if raw.get("density") in DENSITIES:
        out["density"] = raw["density"]
    if raw.get("screen") in SCREEN_MODES:
        out["screen"] = raw["screen"]
    elif raw.get("open_mode") == "last":

        out["screen"] = "last"
    return out

def normalize_document(raw: Any) -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    actions = []
    seen = set()
    for row in raw.get("actions") or []:
        try:
            action = qa.validate_action(row)
        except (qa.ActionInvalid, Exception):        # noqa: BLE001 - one broken button never hides the rest
            continue
        if action["id"] in seen:
            continue
        seen.add(action["id"])
        if isinstance(row, dict) and isinstance(row.get("updated"), (int, float)):
            action["updated"] = float(row["updated"])
        actions.append(action)
        if len(actions) >= qa.MAX_ACTIONS:
            break
    layout = raw.get("layout") if isinstance(raw.get("layout"), dict) else {}
    items = [i for i in resolve_layout(layout.get("items")) if i["type"] != "action" or i["ref"] in seen]
    ui = raw.get("ui") if isinstance(raw.get("ui"), dict) else {}
    settings = normalize_settings(raw.get("settings"))
    out_ui = {"open": ui.get("open") is True, "layer": ui.get("layer") is not False}
    layer = lc.normalize_layer(raw.get("layer"), items, settings["density"])
    return {"version": SCHEMA_VERSION, "settings": settings, "ui": out_ui,
            "layout": {"cols": COLS, "items": items}, "layer": layer, "actions": actions,
            **look_mod.normalize(raw)}

def _path() -> Path:
    from .pgdata import MEMU_DIR, _profile_data_name
    try:
        name = _profile_data_name(FILE_NAME)
    except Exception:
        name = FILE_NAME
    return Path(MEMU_DIR) / name

def load() -> Dict[str, Any]:
    from .storage import load_json_with_recovery
    try:
        raw = load_json_with_recovery(_path(), {}, validator=lambda v: isinstance(v, dict))
    except (OSError, ValueError):
        raw = {}
    return normalize_document(raw)

def mutate(fn) -> Tuple[Dict[str, Any], Any]:
    """One atomic read-modify-write of ``quick_canvas.json``: ``fn(doc)`` edits the normalized document."""
    from .storage import mutate_json
    box: Dict[str, Any] = {}

    def update(raw: Any):
        doc = normalize_document(raw)
        box["result"] = fn(doc)
        return box["result"] is not False, normalize_document(doc)

    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = mutate_json(path, {}, update, backup=True)
    return doc, box.get("result")

MAX_ACTIONS_BYTES = 2 * 1024 * 1024

def action_bytes(action: Dict[str, Any]) -> int:
    return len(json.dumps(action, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

def check_size(doc: Dict[str, Any]) -> None:
    if sum(action_bytes(a) for a in doc["actions"]) > MAX_ACTIONS_BYTES:
        raise qa.ActionInvalid("Кнопки занимают слишком много места (до 2 МБ на все) — уменьшите тексты или картинки",
                               "The buttons take too much space (2 MB for all) — shorten the texts or pictures")

EXPORT_FORMAT = "memuai-quick-actions"

def export_doc(doc: Dict[str, Any], with_layout: bool = True) -> Dict[str, Any]:
    actions = []
    for action in doc["actions"]:
        row = {k: v for k, v in action.items() if k not in ("approved", "created", "updated")}
        actions.append(row)
    out = {"format": EXPORT_FORMAT, "version": 1, "exported": int(time.time()), "actions": actions}
    if with_layout:
        out["layout"] = [dict(i) for i in doc["layout"]["items"] if i["type"] == "action"]
    return out

def import_into(doc: Dict[str, Any], data: Any, mode: str = "merge") -> Dict[str, int]:
    """Add (or replace with) buttons from an export. Imported buttons are never pre-approved: their first run
    asks again, whatever the file says. Ids that already exist get new ones."""
    if not isinstance(data, dict) or data.get("format") != EXPORT_FORMAT or not isinstance(data.get("actions"), list):
        raise qa.ActionInvalid("Это не файл быстрых действий MemuAI", "This is not a MemuAI Quick Actions file")
    rows = data["actions"][:qa.MAX_ACTIONS]
    if mode == "replace":
        doc["actions"] = []
        doc["layout"]["items"] = [i for i in doc["layout"]["items"] if i["type"] != "action"]
    existing = {a["id"] for a in doc["actions"]}
    layout = {str(i.get("ref")): i for i in (data.get("layout") or []) if isinstance(i, dict) and i.get("type") == "action"}
    added = skipped = 0
    total = sum(action_bytes(a) for a in doc["actions"])
    for row in rows:
        try:

            action = qa.validate_action({**row, "approved": "", "global": False} if isinstance(row, dict) else row, keep_meta=False)
        except qa.ActionInvalid:
            skipped += 1
            continue
        old_id = str(row.get("id") or "") if isinstance(row, dict) else ""
        if action["id"] in existing:
            action["id"] = qa.new_id()
        size = action_bytes(action)
        if len(doc["actions"]) >= qa.MAX_ACTIONS or total + size > MAX_ACTIONS_BYTES:
            skipped += 1
            continue
        total += size
        doc["actions"].append(action)
        existing.add(action["id"])
        place = layout.get(old_id)
        size = "l" if place and _int(place.get("w"), 1, 2, 1) == 2 else ""
        add_item(doc["layout"]["items"], "action", action["id"], size,
                 place if mode == "replace" and place else None)
        added += 1
    return {"added": added, "skipped": skipped}

def hotkey_conflict(doc: Dict[str, Any], action: Dict[str, Any], lang: str) -> None:
    """One combo — one button; a global one must not be taken by Quick Ask, its features or a custom command."""
    hotkey = action.get("hotkey") or ""
    if not hotkey:
        return
    other = next((a for a in doc["actions"] if a["id"] != action["id"] and a.get("hotkey") == hotkey), None)
    if other is not None:
        name = other.get("label") or tr(lang, "без подписи", "no label")
        raise qa.ActionInvalid(f"{hotkey} уже у кнопки «{name}»", f"{hotkey} is already used by the button “{name}”")
    if action.get("global"):
        try:
            from .quick_features import hotkey_owner
            owner = hotkey_owner(hotkey, lang, skip="canvas:" + action["id"])
        except Exception:                     # noqa: BLE001 - Windows reports a clash on registration anyway
            owner = ""
        if owner:
            raise qa.ActionInvalid(f"{hotkey} уже занято в MemuAI: {owner}", f"{hotkey} is already used in MemuAI: {owner}")

def global_hotkeys(doc: Dict[str, Any]) -> Dict[str, str]:
    """{action id: hotkey} of the buttons whose hotkey works in every program (feature on, buttons on)."""
    st = doc["settings"]
    if not (st["enabled"] and st["actions"]):
        return {}
    return {a["id"]: a["hotkey"] for a in doc["actions"] if a.get("global") and a.get("hotkey")}

WINDOW_STEPS = (("ask", "window"), ("text", "input"))

def needs_window(action: Dict[str, Any], settings: Optional[Dict[str, Any]] = None) -> bool:
    for step in action["steps"]:
        if (step["type"], step.get("mode")) in WINDOW_STEPS or step["type"] in ("dfy", "computer"):
            return True
    return bool(qa.risks(action, "ru", settings)) and action.get("approved") != qa.signature(action)

class GlobalHotkeys:
    """Global hotkeys of buttons: one ``win_hotkey.HotkeyListener`` per button (like the custom commands').

    A press runs the button without the window when it can (``{text}`` = the selection of the program in front,
    a paste goes back there); a button that needs the window — an answer in it, a first-run approval — opens
    Quick Ask over that program and runs from there. A run that stops for a confirmation or fails opens the window
    too (``state()`` hands such runs to the page once)."""

    MAX = 24

    def __init__(self, listener_factory=None) -> None:
        self._factory = listener_factory
        self.listeners: Dict[str, Any] = {}
        self.status: Dict[str, Dict[str, Any]] = {}
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.Lock()

    def supported(self) -> bool:
        return self._factory is not None or sys.platform == "win32"

    def sync(self, wanted: Dict[str, str], lang: str = "ru") -> Dict[str, Dict[str, Any]]:
        if not self.supported():
            self.status = {aid: {"ok": False, "code": "unsupported"} for aid in wanted}
            return dict(self.status)
        if self._factory is None:
            from .win_hotkey import HotkeyListener
            factory = lambda callback: HotkeyListener(callback, lang=lang)  # noqa: E731
        else:
            factory = self._factory
        with self._lock:
            for aid in list(self.listeners):
                listener = self.listeners[aid]
                if wanted.get(aid) != listener.active:
                    _stop(listener)
                    self.listeners.pop(aid, None)
                    self.status.pop(aid, None)
            for aid in [a for a in self.status if a not in wanted]:
                self.status.pop(aid, None)
            for aid, hotkey in list(wanted.items())[:self.MAX]:
                if aid in self.listeners:
                    continue
                listener = factory(lambda a=aid: self._fire(a))
                result = listener.set_hotkey(hotkey)
                self.status[aid] = result
                if result.get("ok"):
                    self.listeners[aid] = listener
                else:
                    _stop(listener)
        return dict(self.status)

    def _fire(self, action_id: str) -> None:
        loop = self.loop
        if loop is None or not loop.is_running():
            return
        foreground = _foreground()
        asyncio.run_coroutine_threadsafe(hotkey_run(action_id, foreground), loop)

    def suspend(self, on: bool) -> None:
        """While the editor captures a combo, the buttons' own global combos are released (Windows would swallow the
        press); they come back on resume or by themselves after 20 s."""
        with self._lock:
            listeners = list(self.listeners.values())
        for listener in listeners:
            try:
                listener.suspend(20.0) if on else listener.resume()
            except Exception:
                pass

    def stop(self) -> None:
        with self._lock:
            for listener in self.listeners.values():
                _stop(listener)
            self.listeners.clear()

def _stop(listener: Any) -> None:
    try:
        listener.clear()
        listener.stop()
    except Exception:
        pass

def _foreground() -> int:
    if sys.platform != "win32":
        return 0
    try:
        import ctypes
        return int(ctypes.windll.user32.GetForegroundWindow() or 0)
    except Exception:
        return 0

async def hotkey_run(action_id: str, foreground: int = 0) -> Optional[qa.Run]:
    """A button's global hotkey was pressed in another program."""
    doc = await asyncio.to_thread(load)
    action = next((a for a in doc["actions"] if a["id"] == action_id), None)
    if action is None or action_id not in global_hotkeys(doc):
        return None
    if any(r.action.get("id") == action_id for r in RUNNER.active()):
        return None
    lang = _ui_lang()
    manager = RUNNER._manager()

    if needs_window(action, RUNNER.engine_settings()) or (manager is not None and getattr(manager, "visible", False)):
        if manager is not None:
            await asyncio.to_thread(_show_and_run, manager, action_id)
        return None
    context: Dict[str, Any] = {}
    uses_text = any(isinstance(v, str) and re.search(r"\{(text|selection)\}", v)
                    for step in action["steps"] for v in _strings(step))
    if uses_text and foreground:
        context["selection"] = await asyncio.to_thread(_selection_of, foreground)
    if manager is not None and foreground and foreground != getattr(manager, "hwnd", 0) and not getattr(manager, "visible", False):
        manager.previous_hwnd = foreground
    run = RUNNER.start(action, lang=lang, context=context)
    asyncio.ensure_future(_watch(run, manager))
    return run

def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)

def _selection_of(foreground: int) -> str:
    try:
        from . import quick_selection
        from .pgdata import load_preferences
        return str(quick_selection.capture(foreground, load_preferences()).get("text") or "")
    except Exception:
        return ""

def _ui_lang() -> str:
    try:
        from .pgdata import load_preferences
        return "en" if load_preferences().get("language") == "en" else "ru"
    except Exception:
        return "ru"

def _show_and_run(manager: Any, action_id: str) -> None:
    """Open Quick Ask over the program in front and start the button there (approval sheet, answer in the window)."""
    if getattr(manager, "visible", False) or manager.show("hotkey"):
        manager._push("setTimeout(function () { window.MemuQuickCanvas && MemuQuickCanvas.runAction(%s, true); }, 350)"
                      % json.dumps(action_id))

async def _watch(run: qa.Run, manager: Any) -> None:
    """A run started without the window: show the window when it waits for the user or fails."""
    while True:
        waiting = run.status == "confirm" or any(not c.get("done") for c in run.client)
        finished = run.status not in ("running", "confirm")
        if waiting or (finished and run.status == "error"):
            run.surface = True
            if manager is not None and not getattr(manager, "visible", False):
                await asyncio.to_thread(manager.show, "api")
            return
        if finished:
            return
        await asyncio.sleep(0.15)

HOTKEYS = GlobalHotkeys()

async def _sync_hotkeys(doc: Optional[Dict[str, Any]] = None) -> None:
    HOTKEYS.loop = asyncio.get_running_loop()
    if doc is None:
        doc = await asyncio.to_thread(load)
    await asyncio.to_thread(HOTKEYS.sync, global_hotkeys(doc), _ui_lang())

router = APIRouter()
RUNNER = qa.Runner()

def _lang(value: Any) -> str:
    return "en" if value == "en" else "ru"

async def _body(request: Request) -> Dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}

def _bad(exc: qa.ActionInvalid, lang: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": exc.text(lang)}, status)

def _public_action(action: Dict[str, Any], settings: Optional[Dict[str, Any]], lang: str) -> Dict[str, Any]:
    out = dict(action)
    out["approved"] = action.get("approved") == qa.signature(action)
    out["risks"] = qa.risks(action, lang, settings)
    return out

def state(lang: str = "ru") -> Dict[str, Any]:
    doc = load()
    settings = RUNNER.engine_settings()
    runs = [r.public() for r in RUNNER.active()]
    for run in list(RUNNER.runs.values()):
        if getattr(run, "surface", False) and run.status not in ("running", "confirm"):
            run.surface = False
            runs.append(run.public())
    return {"settings": doc["settings"], "ui": doc["ui"], "layout": doc["layout"], "layer": layer_public(doc),
            "look": doc["look"], "looks": doc["looks"], "embed": doc["embed"],
            "actions": [_public_action(a, settings, lang) for a in doc["actions"]],
            "runs": runs, "hotkeys": {"global": HOTKEYS.supported(), "status": dict(HOTKEYS.status)},
            "limits": {"cols": COLS, "max_items": MAX_ITEMS, "max_actions": qa.MAX_ACTIONS, "max_steps": qa.MAX_STEPS,
                       "panel_sizes": PANEL_SIZES}}

@router.get("/api/quick/canvas")
async def canvas_state(lang: str = "ru"):

    await _sync_hotkeys()
    return await asyncio.to_thread(state, _lang(lang))

@router.get("/api/quick/canvas/catalog")
async def canvas_catalog(lang: str = "ru"):
    lang = _lang(lang)
    data = await asyncio.to_thread(qa.step_catalog, lang)
    data["templates"] = await asyncio.to_thread(qa.templates, lang)
    return data

@router.post("/api/quick/canvas/settings")
async def canvas_settings(request: Request):
    body = await _body(request)
    patch = body.get("settings") if isinstance(body.get("settings"), dict) else {}

    def apply(doc):
        doc["settings"] = normalize_settings({**doc["settings"], **patch})
    doc, _ = await asyncio.to_thread(mutate, apply)
    await _sync_hotkeys(doc)
    return {"ok": True, "settings": doc["settings"]}

@router.post("/api/quick/canvas/look")
async def canvas_look(request: Request):
    """Round 30: the look of the panels (global / one panel's own) and the panels built into the window."""
    body = await _body(request)
    box: Dict[str, Any] = {}

    def apply(doc):
        try:
            box["out"] = look_mod.apply_patch(doc, body)
        except ValueError as exc:
            box["error"] = str(exc)
            return False
        return True
    await asyncio.to_thread(mutate, apply)
    if box.get("error"):
        return JSONResponse({"error": box["error"]}, 400)
    return {"ok": True, **box["out"]}

@router.post("/api/quick/canvas/ui")
async def canvas_ui(request: Request):
    body = await _body(request)
    opened = body.get("open") is True

    def apply(doc):
        if doc["ui"].get("open") == opened:
            return False
        doc["ui"]["open"] = opened
        return True
    await asyncio.to_thread(mutate, apply)
    return {"ok": True}

@router.post("/api/quick/canvas/layout")
async def canvas_layout(request: Request):
    """The whole layout from the page (after a drag/resize/remove). Panels that no longer exist in MemuAI are
    dropped by the page; the server only cleans and de-overlaps."""
    body = await _body(request)

    def apply(doc):
        actions = {a["id"] for a in doc["actions"]}
        items = [i for i in resolve_layout(body.get("items")) if i["type"] != "action" or i["ref"] in actions]
        doc["layout"]["items"] = items
    doc, _ = await asyncio.to_thread(mutate, apply)
    return {"ok": True, "layout": doc["layout"]}

@router.post("/api/quick/canvas/panels")
async def canvas_add_panel(request: Request):
    body = await _body(request)
    pid = str(body.get("panel") or "")
    if not PANEL_ID_RE.match(pid):
        return JSONResponse({"error": tr(_ui_lang(), "Неизвестная панель", "Unknown panel")}, 400)
    size = body.get("size") if body.get("size") in PANEL_SIZES else "s"

    def apply(doc):
        found = next((i for i in doc["layout"]["items"] if i["type"] == "panel" and i["ref"] == pid), None)
        return found or add_item(doc["layout"]["items"], "panel", pid, size, body.get("place"))
    doc, item = await asyncio.to_thread(mutate, apply)
    return {"ok": True, "item": item, "layout": doc["layout"]}

@router.post("/api/quick/canvas/actions/validate")
async def canvas_validate(request: Request):
    body = await _body(request)
    lang = _lang(body.get("lang"))
    try:
        action = await asyncio.to_thread(qa.validate_action, body.get("action"))
    except qa.ActionInvalid as exc:
        return {"ok": False, "error": exc.text(lang)}
    return {"ok": True, "risks": qa.risks(action, lang, RUNNER.engine_settings()),
            "steps": [qa.describe_step(s, lang) for s in action["steps"]]}

@router.post("/api/quick/canvas/actions")
async def canvas_save_action(request: Request):
    """Create or update a button. A new one is placed on the canvas (``place`` {x, y} or the first free cell)."""
    body = await _body(request)
    lang = _lang(body.get("lang"))
    raw = body.get("action")
    try:
        action = await asyncio.to_thread(qa.validate_action, raw)
    except qa.ActionInvalid as exc:
        return _bad(exc, lang)

    def apply(doc):
        hotkey_conflict(doc, action, lang)
        old = next((a for a in doc["actions"] if a["id"] == action["id"]), None)
        if old:
            action["created"] = old.get("created", action["created"])

            action["approved"] = "" if body.get("forget_approval") is True else old.get("approved", "")
            doc["actions"][doc["actions"].index(old)] = action
            check_size(doc)
            return "updated"
        if len(doc["actions"]) >= qa.MAX_ACTIONS:
            raise qa.ActionInvalid(f"Не больше {qa.MAX_ACTIONS} кнопок", f"At most {qa.MAX_ACTIONS} buttons")
        action["approved"] = ""
        doc["actions"].append(action)
        check_size(doc)
        add_item(doc["layout"]["items"], "action", action["id"], "l" if body.get("size") == "l" else "", body.get("place"))
        return "created"
    try:
        doc, result = await asyncio.to_thread(mutate, apply)
    except qa.ActionInvalid as exc:
        return _bad(exc, lang)
    await _sync_hotkeys(doc)
    saved = next(a for a in doc["actions"] if a["id"] == action["id"])
    return {"ok": True, "result": result, "action": _public_action(saved, RUNNER.engine_settings(), lang), "layout": doc["layout"],
            "hotkey": HOTKEYS.status.get(saved["id"])}

@router.post("/api/quick/canvas/actions/{action_id}/duplicate")
async def canvas_duplicate(action_id: str, request: Request):
    body = await _body(request)
    lang = _lang(body.get("lang"))

    def apply(doc):
        src = next((a for a in doc["actions"] if a["id"] == action_id), None)
        if not src:
            raise KeyError(action_id)
        if len(doc["actions"]) >= qa.MAX_ACTIONS:
            raise qa.ActionInvalid(f"Не больше {qa.MAX_ACTIONS} кнопок", f"At most {qa.MAX_ACTIONS} buttons")
        copy_ = copy.deepcopy(src)
        copy_["id"] = qa.new_id()
        copy_["created"] = copy_["updated"] = time.time()
        copy_["approved"] = ""
        copy_["hotkey"] = ""
        copy_["global"] = False
        if copy_["label"]:
            copy_["label"] = (copy_["label"] + tr(lang, " (копия)", " (copy)"))[:qa.MAX_LABEL]
        doc["actions"].append(copy_)
        check_size(doc)
        src_item = next((i for i in doc["layout"]["items"] if i["type"] == "action" and i["ref"] == action_id), None)
        add_item(doc["layout"]["items"], "action", copy_["id"], "l" if src_item and src_item["w"] == 2 else "")
        return copy_["id"]
    try:
        doc, new_id = await asyncio.to_thread(mutate, apply)
    except KeyError:
        return JSONResponse({"error": tr(lang, "Кнопка не найдена", "Button not found")}, 404)
    except qa.ActionInvalid as exc:
        return _bad(exc, lang)
    saved = next(a for a in doc["actions"] if a["id"] == new_id)
    return {"ok": True, "action": _public_action(saved, RUNNER.engine_settings(), lang), "layout": doc["layout"]}

@router.delete("/api/quick/canvas/actions/{action_id}")
async def canvas_delete(action_id: str, lang: str = "ru"):
    """Delete a button; the answer carries what is needed to undo it (``/actions/restore``)."""
    def apply(doc):
        src = next((a for a in doc["actions"] if a["id"] == action_id), None)
        if not src:
            return None
        doc["actions"].remove(src)
        items = [i for i in doc["layout"]["items"] if i["type"] == "action" and i["ref"] == action_id]
        doc["layout"]["items"] = [i for i in doc["layout"]["items"] if i not in items]

        spots = (doc["layer"].get("default") or {}).get("items") or {}
        return {"action": src, "items": items, "screen": {i["id"]: spots[i["id"]] for i in items if i["id"] in spots}}
    doc, removed = await asyncio.to_thread(mutate, apply)
    await _sync_hotkeys(doc)
    if not removed:
        return JSONResponse({"error": tr(_lang(lang), "Кнопка не найдена", "Button not found")}, 404)
    return {"ok": True, "removed": removed, "layout": doc["layout"]}

@router.post("/api/quick/canvas/actions/restore")
async def canvas_restore(request: Request):
    body = await _body(request)
    lang = _lang(body.get("lang"))
    removed = body.get("removed") if isinstance(body.get("removed"), dict) else {}
    try:
        action = await asyncio.to_thread(qa.validate_action, removed.get("action"))
    except qa.ActionInvalid as exc:
        return _bad(exc, lang)
    raw = removed.get("action") if isinstance(removed.get("action"), dict) else {}

    def apply(doc):
        if any(a["id"] == action["id"] for a in doc["actions"]):
            return False

        action["approved"] = str(raw.get("approved") or "") if raw.get("approved") == qa.signature(action) else ""
        doc["actions"].append(action)
        place = next((i for i in removed.get("items") or [] if isinstance(i, dict)), None)
        item = add_item(doc["layout"]["items"], "action", action["id"], "l" if place and place.get("w") == 2 else "", place)
        screen = removed.get("screen") if isinstance(removed.get("screen"), dict) else {}
        spot = screen.get(str((place or {}).get("id") or "")) if place else None
        if isinstance(spot, dict):
            doc["layer"].setdefault("default", {"area": list(lc.MIGRATION_AREA), "items": {}}).setdefault("items", {})[item["id"]] = spot
        return True
    doc, ok = await asyncio.to_thread(mutate, apply)
    await _sync_hotkeys(doc)
    return {"ok": bool(ok), "layout": doc["layout"], "actions": [_public_action(a, RUNNER.engine_settings(), lang) for a in doc["actions"]]}

@router.post("/api/quick/canvas/app-icon")
async def canvas_app_icon(request: Request):
    """The icon of a program (a name the assistant resolves or a path to .exe/.lnk/.ico) as a PNG data URL."""
    body = await _body(request)
    from .quick_app_icon import app_icon
    return await asyncio.to_thread(app_icon, body.get("app"), _lang(body.get("lang")))

@router.post("/api/quick/canvas/hotkeys")
async def canvas_hotkeys(request: Request):
    body = await _body(request)
    await asyncio.to_thread(HOTKEYS.suspend, body.get("suspend") is True)
    return {"ok": True}

@router.get("/api/quick/canvas/export")
async def canvas_export():
    doc = await asyncio.to_thread(load)
    return export_doc(doc)

@router.post("/api/quick/canvas/import")
async def canvas_import(request: Request):
    body = await _body(request)
    lang = _lang(body.get("lang"))
    mode = "replace" if body.get("mode") == "replace" else "merge"
    data = body.get("data")

    def apply(doc):
        return import_into(doc, data, mode)
    try:
        doc, counts = await asyncio.to_thread(mutate, apply)
    except qa.ActionInvalid as exc:
        return _bad(exc, lang)
    await _sync_hotkeys(doc)
    return {"ok": True, **counts, "layout": doc["layout"],
            "actions": [_public_action(a, RUNNER.engine_settings(), lang) for a in doc["actions"]]}

@router.post("/api/quick/canvas/run")
async def canvas_run(request: Request):
    """Run a button. ``approve: true`` = the user accepted the list of its risky steps (stored as the steps'
    signature); without it a button that needs one answers ``{approval: {risks}}`` and runs nothing."""
    body = await _body(request)
    lang = _lang(body.get("lang"))
    action_id = str(body.get("id") or "")
    doc = await asyncio.to_thread(load)
    if not doc["settings"]["enabled"] or not doc["settings"]["actions"]:
        return JSONResponse({"error": tr(lang, "Быстрые действия выключены в настройках", "Quick actions are turned off in Settings")}, 403)
    action = next((a for a in doc["actions"] if a["id"] == action_id), None)
    if not action:
        return JSONResponse({"error": tr(lang, "Кнопка не найдена", "Button not found")}, 404)

    running = next((r for r in RUNNER.active() if r.action.get("id") == action_id), None)
    if running is not None:
        return {"ok": True, "run": running.public(), "already": True}
    sig = qa.signature(action)
    settings = RUNNER.engine_settings()
    risky = qa.risks(action, lang, settings)
    if risky and action.get("approved") != sig:
        if body.get("approve") is not True:
            return {"ok": False, "approval": {"risks": risky, "label": action["label"]}}

        def approve(d):
            for row in d["actions"]:
                if row["id"] == action_id and qa.signature(row) == sig:
                    row["approved"] = sig
        await asyncio.to_thread(mutate, approve)
        action = {**action, "approved": sig}
    running = next((r for r in RUNNER.active() if r.action.get("id") == action_id), None)
    if running is not None:
        return {"ok": True, "run": running.public(), "already": True}
    run = RUNNER.start(action, lang=lang, context=body.get("context"))
    return {"ok": True, "run": run.public()}

@router.get("/api/quick/canvas/runs")
async def canvas_runs():
    return {"runs": [r.public() for r in RUNNER.active()]}

def _run_gone(lang: str) -> JSONResponse:

    return JSONResponse({"error": tr(lang, "Запуск уже завершён или не найден", "The run has already finished or was not found"),
                         "code": "run_not_found"}, 404)

@router.get("/api/quick/canvas/runs/{run_id}")
async def canvas_run_state(run_id: str, lang: str = ""):
    run = RUNNER.get(run_id)
    if not run:
        return _run_gone(_lang(lang) if lang else _ui_lang())
    return {"run": run.public()}

@router.post("/api/quick/canvas/runs/{run_id}/{op}")
async def canvas_run_op(run_id: str, op: str, request: Request):
    body = await _body(request)
    lang = _lang(body.get("lang")) if body.get("lang") else _ui_lang()
    if op not in ("cancel", "confirm", "client"):
        return JSONResponse({"error": tr(lang, "Неизвестная операция", "Unknown operation")}, 400)
    if RUNNER.get(run_id) is None:
        return _run_gone(lang)
    if op == "cancel":
        ok = RUNNER.cancel(run_id)
    elif op == "confirm":
        ok = RUNNER.answer_confirm(run_id, body.get("accept") is True)
    elif op == "client":
        ok = RUNNER.client_done(run_id, _int(body.get("seq"), 0, 10 ** 6, 0), body.get("ok") is True,
                                body.get("result") or "", str(body.get("error") or ""))
    run = RUNNER.get(run_id)
    return {"ok": ok, "run": run.public() if run else None}

RELAY_EVENTS = {"run", "edit-button", "new-button", "add", "changed", "ask", "layer-state", "edit", "reload",
                "toast", "focus-quick"}
MONITOR_KEY_RE = re.compile(r"^[0-9]{2,5}x[0-9]{2,5}@[0-9]{2,3}$|^default$")

def _layer_of():
    manager = RUNNER._manager()
    if manager is None or not getattr(manager, "native_ok", False):
        return manager, None
    try:
        return manager, manager.ensure_layer()
    except Exception:                                  # noqa: BLE001 - no layer = the in-window fallback
        return manager, None

def layer_public(doc: Dict[str, Any]) -> Dict[str, Any]:
    _manager, layer = _layer_of()
    out = {"available": bool(layer is not None and layer.available()), "visible": False, "edit": False,
           "migrated": doc["layer"].get("migrated") or "", "monitors": len(doc["layer"].get("monitors") or {})}
    if layer is not None:
        out.update({"visible": bool(layer.visible), "edit": bool(layer.edit)})
    return out

def screen_items(doc: Dict[str, Any], panels: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """The items the layer shows: switched-on kinds only, buttons that exist, panels MemuAI still has (when known)."""
    st = doc["settings"]
    if not st["enabled"]:
        return []
    actions = {a["id"] for a in doc["actions"]}
    known = set(panels) if isinstance(panels, list) else None
    out = []
    for item in doc["layout"]["items"]:
        if item["type"] == "action" and st["actions"] and item["ref"] in actions:
            out.append(item)
        elif item["type"] == "panel" and st["panels"] and (known is None or item["ref"] in known):
            out.append(item)
    return out

def _monitor_arg(body: Dict[str, Any]) -> Tuple[str, int, int]:
    raw = body.get("monitor") if isinstance(body.get("monitor"), dict) else {}
    key = str(raw.get("key") or "default")
    if not MONITOR_KEY_RE.match(key):
        key = "default"
    return key, _int(raw.get("w"), 200, 20000, 1536), _int(raw.get("h"), 200, 20000, 816)

def _panels_arg(body: Dict[str, Any]) -> Optional[List[str]]:
    panels = body.get("panels")
    if not isinstance(panels, list):
        return None
    return [str(p) for p in panels[:400] if isinstance(p, str) and PANEL_ID_RE.match(p)]

def resolve_for(doc: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    key, w, h = _monitor_arg(body)
    keepout = body.get("keepout") if isinstance(body.get("keepout"), dict) else {}
    items = screen_items(doc, _panels_arg(body))
    placed = lc.resolve(items, doc["layer"], key, w, h, keepout=keepout, density=doc["settings"]["density"])
    return {"ok": True, "monitor": {"key": key, "w": w, "h": h}, "items": placed,
            "hidden": sum(1 for p in placed if p["hidden"]), "density": doc["settings"]["density"],
            "cell": lc.cell(doc["settings"]["density"]), "gap": lc.GAP, "margin": lc.MARGIN, "spacing": lc.SPACING,
            "keepout_pad": lc.KEEPOUT_PAD, "migrated": doc["layer"].get("migrated") or ""}

def move_items(doc: Dict[str, Any], body: Dict[str, Any]) -> bool:
    """The user arranged the layer: the new sizes go to ``layout.items``, the positions to ``layer`` (this monitor
    + the default). ``items``: ``[{id, x, y, units: [w, h]}]`` — every item the layer shows (CSS px)."""
    key, w, h = _monitor_arg(body)
    rows = body.get("items") if isinstance(body.get("items"), list) else []
    by_id = {i["id"]: i for i in doc["layout"]["items"]}
    density = doc["settings"]["density"]
    rects = []
    for row in rows[:MAX_ITEMS]:
        if not isinstance(row, dict) or row.get("id") not in by_id:
            continue
        item = by_id[row["id"]]
        units = row.get("units") if isinstance(row.get("units"), list) and len(row.get("units")) == 2 else [item["w"], item["h"]]
        if item["type"] == "panel":
            item["w"], item["h"] = _int(units[0], 2, COLS, item["w"]), _int(units[1], 2, 8, item["h"])
        else:
            item["w"] = item["h"] = 2 if _int(units[0], 1, 2, item["w"]) == 2 else 1
        x, y = row.get("x"), row.get("y")
        try:
            x, y = float(x), float(y)
        except (TypeError, ValueError):
            continue
        pw, ph = lc.item_px(item, density)
        rects.append({"id": item["id"], "x": x, "y": y, "w": pw, "h": ph})
    if not rects:
        return False
    lc.remember(doc["layer"], key, w, h, rects)
    return True

@router.post("/api/quick/layer/resolve")
async def layer_resolve(request: Request):
    """Where every panel/button goes on the monitor the layer covers (``monitor {key, w, h}`` CSS px of its work
    area, ``keepout {hard, soft}`` — the Quick Ask window, ``panels`` — ids MemuAI has now, when known)."""
    body = await _body(request)
    doc = await asyncio.to_thread(load)
    return resolve_for(doc, body)

@router.post("/api/quick/layer/move")
async def layer_move(request: Request):
    body = await _body(request)
    doc, changed = await asyncio.to_thread(mutate, lambda d: move_items(d, body))
    out = resolve_for(doc, body)
    out["saved"] = bool(changed)
    out["layout"] = doc["layout"]
    return out

@router.post("/api/quick/layer/region")
async def layer_region(request: Request):
    """The page's items (``rects [[x, y, w, h, radius]]`` CSS px) → the window's click-through shape."""
    body = await _body(request)
    _manager, layer = _layer_of()
    if layer is None or not layer.available():
        return {"ok": False, "native": False}
    rects = body.get("rects") if isinstance(body.get("rects"), list) else []
    view = body.get("view") if isinstance(body.get("view"), dict) else None
    if view is not None:
        return await asyncio.to_thread(layer.set_region, rects, body.get("full") is True, view=view)
    return await asyncio.to_thread(layer.set_region, rects, body.get("full") is True)

@router.post("/api/quick/layer")
async def layer_command(request: Request):
    """show | hide | toggle | edit {on} | focused | state — Ctrl+B / Ctrl+E from either page."""
    body = await _body(request)
    manager, layer = _layer_of()
    if manager is None or layer is None:
        return {"ok": False, "available": False}
    action = str(body.get("action") or "state")
    return await asyncio.to_thread(manager.layer_command, action, body.get("on") is True)

@router.post("/api/quick/layer/relay")
async def layer_relay(request: Request):
    """One page tells the other something (``{to: quick|layer, event, data, id}``). ``relayed: false`` — the page
    falls back to a BroadcastChannel (same browser profile) — off Windows, in tests."""
    body = await _body(request)
    event = str(body.get("event") or "")
    to = body.get("to")
    if event not in RELAY_EVENTS or to not in ("quick", "layer"):
        return JSONResponse({"error": "bad event"}, 400)
    message = {"id": str(body.get("id") or "")[:40], "to": to, "event": event,
               "data": body.get("data") if isinstance(body.get("data"), (dict, list, str, int, float, bool)) else None}
    if len(json.dumps(message, ensure_ascii=False)) > 20000:
        return JSONResponse({"error": "too big"}, 413)
    manager, layer = _layer_of()
    if manager is None or layer is None or not layer.available():
        return {"ok": True, "relayed": False}
    if event == "run" and to == "quick" and not layer.accept_click():
        return {"ok": False, "relayed": True, "guarded": True}
    if to == "layer":
        return {"ok": True, "relayed": await asyncio.to_thread(layer.relay, message)}
    if not getattr(manager, "visible", False):
        return {"ok": True, "relayed": False}
    script = "window.MemuQuickBus && MemuQuickBus.receive(%s)" % json.dumps(message, ensure_ascii=False)
    await asyncio.to_thread(manager._push, script)
    if event in ("run", "edit-button", "new-button", "add", "focus-quick"):

        await asyncio.to_thread(_focus_quick, manager)
    return {"ok": True, "relayed": True}

def _focus_quick(manager: Any) -> None:
    try:
        manager.raise_to_top()
        if manager.api:
            manager.api.take_foreground(manager.hwnd)
        manager._on_ui(manager._focus_browser)
    except Exception:
        pass

def register() -> None:
    from .quick_ask_api import register_router
    register_router(router)

register()
