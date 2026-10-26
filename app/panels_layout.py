"""Round 33 (r33-panels): places on the MemuAI window where a panel can live, and their per-zone settings.

Before Round 33 a panel could be pinned in three places (``home`` — the grid under the message box on the start
screen, ``sidebar`` and ``appbar``). Now there are eight *zones*; a panel can be in several at once, with its own
order in each (``panel.order[zone]``) and one size (``panel.size`` s|m|l|w — read per zone by the window).

The window-wide look of each zone lives in ``panels.json → settings.layout``::

    {"v": 1, "zones": {zone: {"view": "cards"|"chips", "chats": bool, "width": px}}}

* ``view``  — cards (full panel cards) or chips (one-line value chips, a click opens the card);
* ``chats`` — the start-screen zones (top / composer / left / right) also stay visible inside a conversation;
* ``width`` — the side columns only (``WIDTH_MIN``–``WIDTH_MAX`` px).

Everything here is pure (no I/O); ``app.panels`` calls :func:`clean_layout` from its settings normaliser and
:func:`clean_items` from the bulk layout route (``POST /api/pg/panels-layout``, undo / redo / reset of the window's
«Edit layout» mode). The same zone list is exported to the window in ``snapshot()["zones"]`` (see
:func:`public_zones`) so other parts of the UI (the theme / layout editor of Round 33) can reuse it.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

LAYOUT_VERSION = 1

ZONES: Dict[str, Dict[str, Any]] = {
    "appbar":         {"kind": "bar",    "views": ("chips",),          "chats": False, "width": False, "area": "window"},
    "top":            {"kind": "strip",  "views": ("cards", "chips"),  "chats": True,  "width": False, "area": "start"},
    "composer":       {"kind": "strip",  "views": ("chips", "cards"),  "chats": True,  "width": False, "area": "start"},
    "home":           {"kind": "grid",   "views": ("cards",),          "chats": False, "width": False, "area": "start"},
    "left":           {"kind": "column", "views": ("cards", "chips"),  "chats": True,  "width": True,  "area": "start"},
    "right":          {"kind": "column", "views": ("cards", "chips"),  "chats": True,  "width": True,  "area": "start"},
    "sidebar":        {"kind": "stack",  "views": ("cards", "chips"),  "chats": False, "width": False, "area": "window"},
    "sidebar_bottom": {"kind": "stack",  "views": ("chips", "cards"),  "chats": False, "width": False, "area": "window"},
}
ZONE_IDS = tuple(ZONES)

LEGACY_ZONES = ("home", "sidebar", "appbar")
WIDTH_MIN, WIDTH_MAX = 200, 480
DEFAULT_WIDTH = {"left": 280, "right": 300}
SIZES = ("s", "m", "l", "w")

NAMES = {
    "appbar": ("Верхняя панель", "Top bar"),
    "top": ("Над приветствием", "Above the greeting"),
    "composer": ("Над полем ввода", "Above the message box"),
    "home": ("Под полем ввода", "Below the message box"),
    "left": ("Колонка слева", "Left column"),
    "right": ("Колонка справа", "Right column"),
    "sidebar": ("Боковая панель", "Sidebar"),
    "sidebar_bottom": ("Низ боковой панели", "Sidebar bottom"),
}

def default_zone(zone: str) -> Dict[str, Any]:
    meta = ZONES[zone]
    out: Dict[str, Any] = {"view": meta["views"][0]}
    if meta["chats"]:
        out["chats"] = False
    if meta["width"]:
        out["width"] = DEFAULT_WIDTH.get(zone, 280)
    return out

def default_layout() -> Dict[str, Any]:
    return {"v": LAYOUT_VERSION, "zones": {zone: default_zone(zone) for zone in ZONE_IDS}}

def clean_zone(zone: str, raw: Any, base: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    meta = ZONES[zone]
    out = dict(base) if isinstance(base, dict) else default_zone(zone)
    out = {k: v for k, v in out.items() if k in ("view", "chats", "width")}
    if out.get("view") not in meta["views"]:
        out["view"] = meta["views"][0]
    raw = raw if isinstance(raw, dict) else {}
    view = str(raw.get("view") or "")
    if view in meta["views"]:
        out["view"] = view
    if meta["chats"]:
        if "chats" in raw:
            out["chats"] = raw.get("chats") is True
        out["chats"] = out.get("chats") is True
    else:
        out.pop("chats", None)
    if meta["width"]:
        width = raw.get("width", out.get("width"))
        try:
            width = int(round(float(width)))
        except (TypeError, ValueError):
            width = DEFAULT_WIDTH.get(zone, 280)
        out["width"] = max(WIDTH_MIN, min(WIDTH_MAX, width))
    else:
        out.pop("width", None)
    return out

def clean_layout(raw: Any, base: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Any stored / posted layout → the current form. ``base`` (the stored layout) is kept for zones the patch
    does not name, so ``{"zones": {"left": {"width": 320}}}`` changes only that one value."""
    base_zones = (base or {}).get("zones") if isinstance(base, dict) else None
    base_zones = base_zones if isinstance(base_zones, dict) else {}
    zones = raw.get("zones") if isinstance(raw, dict) and isinstance(raw.get("zones"), dict) else {}
    return {"v": LAYOUT_VERSION,
            "zones": {zone: clean_zone(zone, zones.get(zone), base_zones.get(zone)) for zone in ZONE_IDS}}

def clean_items(raw: Any, known_ids: List[str], limit: int = 200) -> List[Dict[str, Any]]:
    """The panels part of a bulk layout save: ``[{id, placements, order: {zone: n}, size}]`` for known panels only.
    Missing keys mean «leave as is» (returned without that key)."""
    out: List[Dict[str, Any]] = []
    seen = set()
    known = set(known_ids)
    for row in raw if isinstance(raw, list) else []:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("id") or "")
        if pid not in known or pid in seen:
            continue
        seen.add(pid)
        item: Dict[str, Any] = {"id": pid}
        if isinstance(row.get("placements"), list):
            item["placements"] = list(dict.fromkeys(str(p) for p in row["placements"] if str(p) in ZONES))
        if isinstance(row.get("order"), dict):
            order = {}
            for zone, value in row["order"].items():
                if zone in ZONES and isinstance(value, (int, float)) and not isinstance(value, bool):
                    order[zone] = max(0, min(10_000, int(value)))
            item["order"] = order
        if str(row.get("size") or "") in SIZES:
            item["size"] = str(row["size"])
        out.append(item)
        if len(out) >= limit:
            break
    return out

def public_zones(lang: str = "ru") -> List[Dict[str, Any]]:
    """The zone list for the window (and the Round 33 layout editor): id, kind, views, flags, names."""
    index = 1 if lang == "en" else 0
    return [{"id": zone, "kind": meta["kind"], "views": list(meta["views"]), "chats": meta["chats"],
             "width": meta["width"], "area": meta["area"], "name": NAMES[zone][index], "names": list(NAMES[zone])}
            for zone, meta in ZONES.items()]
