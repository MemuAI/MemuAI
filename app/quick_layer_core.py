"""Round 28: the screen layer of Quick Ask — pure layout maths (no Win32, no I/O; unit-tested on Linux).

Round 27 put the MemuAI panels and the user's quick-action buttons INSIDE the small Quick Ask window, on a grid.
What was asked for is different: the panels and buttons live ANYWHERE on the screen. When Quick Ask opens, a
transparent, click-through layer (``app/quick_layer.py``, page ``/quick/layer.html``) covers the work area of the
monitor Quick Ask opened on, and every item appears at its own saved spot around the window.

Coordinates here are CSS px (96-DPI units) relative to the top-left corner of that monitor's WORK AREA (taskbar
excluded), so a layout is independent of DPI scaling and of where the monitor sits in the virtual desktop (negative
coordinates of monitors left of / above the primary one never reach this module — :mod:`app.quick_layer` subtracts
the work-area origin and divides by the monitor's scale).

Storage (``quick_canvas.json`` → ``layer``)::

    {"version": 1,
     "monitors": {"2560x1440@144": {"area": [w, h], "t": unix, "items": {item_id: pos}}, …},   # ≤ MAX_MONITORS
     "default": {"area": [w, h], "items": {item_id: pos}},     # the last arrangement the user made, any monitor
     "migrated": "r27-grid"}                                  # only when it came from the Round 27 window grid

A ``pos`` is resolution independent: an anchor per axis (left/centre/right, top/middle/bottom — the third of the
work area the item's centre was dropped in), the CSS offset from that anchor edge, and the normalized centre
(``fx``/``fy``) as a fallback. :func:`resolve` turns the stored positions into rectangles for one monitor:

1. the exact layout of THIS monitor (keyed by its physical size and DPI) when there is one;
2. otherwise the default arrangement adapted to this work area — the anchors keep an item glued to the edge or the
   centre it was placed at, the offsets shrink proportionally on a smaller monitor (never grow on a bigger one);
3. items without any position yet (a button just created) take the nearest free spot from the top-right corner.

Then every rectangle is clamped into the work area and never overlaps another item or the Quick Ask window
(``keepout``: the window's current rectangle is a hard limit, the room its answer can grow into a soft one that is
given up only when nothing else fits). An item that cannot fit at all is reported ``hidden`` (never off-screen).
"""
from __future__ import annotations

import math
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

LAYER_VERSION = 1
MAX_MONITORS = 12

DENSITY = {"compact": 58, "normal": 72, "large": 88}
GAP = 8
MARGIN = 12
SPACING = GAP
KEEPOUT_PAD = 14
ANCHORS_X = ("l", "c", "r")
ANCHORS_Y = ("t", "m", "b")

MIGRATION_AREA = (1920, 1032)
MIGRATION_OFFSET = 24

Rect = Tuple[float, float, float, float]

def cell(density: str) -> int:
    return DENSITY.get(density or "normal", DENSITY["normal"])

def unit_px(units: int, density: str = "normal") -> int:
    units = max(1, int(units))
    return units * cell(density) + (units - 1) * GAP

def units_fitting(px: float, density: str = "normal") -> int:
    """How many units fit into ``px`` CSS px."""
    return max(1, int((float(px) + GAP) // (cell(density) + GAP)))

def item_px(item: Dict[str, Any], density: str = "normal") -> Tuple[int, int]:
    return unit_px(int(item.get("w") or 1), density), unit_px(int(item.get("h") or 1), density)

def monitor_key(monitor: Dict[str, Any]) -> str:
    """Id of a monitor for its own layout: physical size + DPI (two identical screens share one — on purpose)."""
    left, top, right, bottom = (int(v) for v in monitor["rect"])
    dpi = int(monitor.get("dpi") or 96)
    dpi = dpi if 48 <= dpi <= 960 else 96
    return "%dx%d@%d" % (right - left, bottom - top, dpi)

def monitor_scale(monitor: Dict[str, Any]) -> float:
    dpi = int(monitor.get("dpi") or 96)
    return (dpi if 48 <= dpi <= 960 else 96) / 96.0

def work_rect(monitor: Dict[str, Any]) -> Tuple[int, int, int, int]:
    work = monitor.get("work") or monitor["rect"]
    left, top, right, bottom = (int(v) for v in work)
    if right <= left or bottom <= top:
        left, top, right, bottom = (int(v) for v in monitor["rect"])
    return left, top, right, bottom

def area_of(monitor: Dict[str, Any]) -> Dict[str, Any]:
    """The work area of ``monitor`` in CSS px + what is needed to go back to physical pixels."""
    scale = monitor_scale(monitor)
    left, top, right, bottom = work_rect(monitor)
    return {"key": monitor_key(monitor), "w": int(math.floor((right - left) / scale)),
            "h": int(math.floor((bottom - top) / scale)), "scale": scale, "origin": [left, top],
            "physical": [left, top, right, bottom]}

def to_css_rect(physical: Sequence[float], area: Dict[str, Any]) -> Rect:
    """A physical-pixel rectangle (l, t, r, b — virtual-desktop coordinates, may be negative) → CSS px in the area."""
    left, top, right, bottom = (float(v) for v in physical)
    ox, oy = area["origin"]
    scale = float(area["scale"]) or 1.0
    return ((left - ox) / scale, (top - oy) / scale, (right - left) / scale, (bottom - top) / scale)

def to_physical_rect(rect: Rect, area: Dict[str, Any]) -> Tuple[int, int, int, int]:
    """CSS px in the area → physical (l, t, r, b) in virtual-desktop coordinates."""
    x, y, w, h = rect
    ox, oy = area["origin"]
    scale = float(area["scale"]) or 1.0
    return (int(round(ox + x * scale)), int(round(oy + y * scale)), int(round(ox + (x + w) * scale)),
            int(round(oy + (y + h) * scale)))

def keepout_for(geometry: Dict[str, Any], area: Dict[str, Any]) -> Dict[str, Optional[List[float]]]:
    """The Quick Ask window as keep-out rectangles in the layer (CSS px).

    ``hard``: where the window is now. ``soft``: the room its answer grows into (down from the top edge, or up from
    the bottom edge when it is anchored at the bottom) — items avoid it while there is space elsewhere."""
    x, y, w, h = (int(geometry[k]) for k in ("x", "y", "w", "h"))
    max_h = max(h, int(geometry.get("max_h") or h))
    if geometry.get("anchor") == "bottom":
        bottom = int(geometry.get("bottom") or (y + h))
        grow = (x, bottom - max_h, x + w, bottom)
    else:
        grow = (x, y, x + w, y + max_h)
    hard = to_css_rect((x, y, x + w, y + h), area)
    soft = to_css_rect(grow, area)
    clip = lambda r: _clip(r, area["w"], area["h"])  # noqa: E731
    return {"hard": clip(hard), "soft": clip(soft)}

def _clip(rect: Rect, area_w: float, area_h: float) -> Optional[List[float]]:
    x, y, w, h = rect
    l, t, r, b = max(0.0, x), max(0.0, y), min(float(area_w), x + w), min(float(area_h), y + h)
    if r <= l or b <= t:
        return None
    return [round(l, 2), round(t, 2), round(r - l, 2), round(b - t, 2)]

def rect_to_pos(rect: Rect, area_w: float, area_h: float) -> Dict[str, Any]:
    """Where an item was dropped, independently of the monitor: anchor per axis + offset + normalized centre."""
    x, y, w, h = (float(v) for v in rect)
    area_w, area_h = max(1.0, float(area_w)), max(1.0, float(area_h))
    cx, cy = x + w / 2, y + h / 2

    def axis(start: float, size: float, centre: float, length: float, names: Tuple[str, str, str]):
        if centre < length / 3:
            return names[0], start
        if centre > 2 * length / 3:
            return names[2], length - (start + size)
        return names[1], centre - length / 2
    ax, dx = axis(x, w, cx, area_w, ANCHORS_X)
    ay, dy = axis(y, h, cy, area_h, ANCHORS_Y)
    return {"ax": ax, "ay": ay, "dx": round(dx, 2), "dy": round(dy, 2),
            "fx": round(min(max(cx / area_w, 0.0), 1.0), 5), "fy": round(min(max(cy / area_h, 0.0), 1.0), 5)}

def pos_to_xy(pos: Dict[str, Any], w: float, h: float, area_w: float, area_h: float,
              src: Optional[Sequence[float]] = None) -> Tuple[float, float]:
    """The top-left corner of a ``w``×``h`` item stored as ``pos`` on an area of ``area_w``×``area_h``.

    ``src`` is the area the position was made on: on a smaller area the offsets from the edges shrink in proportion
    (a button 300 px from the right edge of a 2560 px screen is ~160 px from it on a 1366 px one), on a bigger area
    they stay as they were — an item glued to an edge stays glued to it. Not clamped (see :func:`clamp_xy`)."""
    try:
        sw, sh = (float(v) for v in (src or (area_w, area_h)))
    except (TypeError, ValueError):
        sw, sh = float(area_w), float(area_h)
    rx = min(1.0, float(area_w) / sw) if sw > 0 else 1.0
    ry = min(1.0, float(area_h) / sh) if sh > 0 else 1.0
    dx, dy = float(pos.get("dx") or 0), float(pos.get("dy") or 0)
    ax, ay = pos.get("ax"), pos.get("ay")
    if ax == "l":
        x = dx * rx
    elif ax == "r":
        x = area_w - dx * rx - w
    elif ax == "c":
        x = area_w / 2 + dx * (float(area_w) / sw if sw > 0 else 1.0) - w / 2
    else:
        x = float(pos.get("fx", 0.5)) * area_w - w / 2
    if ay == "t":
        y = dy * ry
    elif ay == "b":
        y = area_h - dy * ry - h
    elif ay == "m":
        y = area_h / 2 + dy * (float(area_h) / sh if sh > 0 else 1.0) - h / 2
    else:
        y = float(pos.get("fy", 0.2)) * area_h - h / 2
    return x, y

def clamp_xy(x: float, y: float, w: float, h: float, area_w: float, area_h: float, margin: float = MARGIN) -> Tuple[float, float]:
    """The whole item inside the work area (with the edge margin when there is room for it)."""
    mx = margin if area_w >= w + 2 * margin else max(0.0, (area_w - w) / 2)
    my = margin if area_h >= h + 2 * margin else max(0.0, (area_h - h) / 2)
    x = min(max(float(x), mx), max(mx, area_w - mx - w))
    y = min(max(float(y), my), max(my, area_h - my - h))
    return x, y

def _inflate(rect: Sequence[float], pad: float) -> Rect:
    x, y, w, h = (float(v) for v in rect)
    return x - pad, y - pad, w + 2 * pad, h + 2 * pad

def _hits(x: float, y: float, w: float, h: float, other: Rect) -> bool:
    ox, oy, ow, oh = other
    eps = 0.01
    return x < ox + ow - eps and ox < x + w - eps and y < oy + oh - eps and oy < y + h - eps

def nearest_free(x: float, y: float, w: float, h: float, obstacles: Sequence[Rect], area_w: float, area_h: float,
                 margin: float = MARGIN) -> Optional[Tuple[float, float]]:
    """The free spot for a ``w``×``h`` item closest to (``x``, ``y``), inside the area, touching nothing in
    ``obstacles`` (already inflated by the spacing they need). ``None`` when the item fits nowhere.

    Candidates are the wanted coordinate, the area edges and the edges of every obstacle: any free position can
    slide left/up until it touches one of those, so if there is room anywhere one of the candidates is free."""
    if w > area_w or h > area_h:
        return None
    lo_x, hi_x = (margin, area_w - margin - w) if area_w >= w + 2 * margin else (0.0, area_w - w)
    lo_y, hi_y = (margin, area_h - margin - h) if area_h >= h + 2 * margin else (0.0, area_h - h)
    xs = {min(max(x, lo_x), hi_x), lo_x, hi_x}
    ys = {min(max(y, lo_y), hi_y), lo_y, hi_y}
    for ox, oy, ow, oh in obstacles:
        xs.update((ox + ow, ox - w))
        ys.update((oy + oh, oy - h))
    xs = [v for v in xs if lo_x - 1e-6 <= v <= hi_x + 1e-6]
    ys = [v for v in ys if lo_y - 1e-6 <= v <= hi_y + 1e-6]
    best: Optional[Tuple[float, float]] = None
    best_d = float("inf")
    for cy in sorted(ys, key=lambda v: abs(v - y)):
        if (cy - y) ** 2 >= best_d:
            break
        for cx in sorted(xs, key=lambda v: abs(v - x)):
            d = (cx - x) ** 2 + (cy - y) ** 2
            if d >= best_d:
                break
            if not any(_hits(cx, cy, w, h, o) for o in obstacles):
                best, best_d = (cx, cy), d
                break
    return best

def _fit_units(item: Dict[str, Any], area_w: float, area_h: float, density: str) -> Tuple[int, int]:
    """A panel too big for a small work area shrinks (whole units, at least 2×2); a button never does."""
    w_u, h_u = int(item.get("w") or 1), int(item.get("h") or 1)
    if item.get("type") != "panel":
        return w_u, h_u
    max_w = units_fitting(max(0.0, area_w - 2 * MARGIN), density)
    max_h = units_fitting(max(0.0, area_h - 2 * MARGIN), density)
    return max(2, min(w_u, max_w)), max(2, min(h_u, max_h))

def resolve(items: Sequence[Dict[str, Any]], layer: Dict[str, Any], key: str, area_w: float, area_h: float, *,
            keepout: Optional[Dict[str, Any]] = None, density: str = "normal") -> List[Dict[str, Any]]:
    """Rectangles (CSS px in the work area) of ``items`` (``layout.items`` rows) on the monitor ``key``.

    Returns ``[{id, type, ref, pinned, units: [w, h], x, y, w, h, source: exact|adapted|auto, hidden}]`` in the
    order the items were placed. Nothing overlaps, nothing leaves the area, nothing sits under the window."""
    area_w, area_h = float(area_w), float(area_h)
    keepout = keepout or {}
    hard = [_inflate(r, KEEPOUT_PAD) for r in _rects(keepout.get("hard"))]
    soft = [_inflate(r, KEEPOUT_PAD) for r in _rects(keepout.get("soft"))]
    monitors = layer.get("monitors") if isinstance(layer.get("monitors"), dict) else {}
    exact = (monitors.get(key) or {}).get("items") or {}
    exact_area = (monitors.get(key) or {}).get("area") or [area_w, area_h]
    default = layer.get("default") if isinstance(layer.get("default"), dict) else {}
    adapted = default.get("items") or {}
    default_area = default.get("area") or list(MIGRATION_AREA)

    wanted: List[Tuple[int, Dict[str, Any], float, float, int, int, str]] = []
    for index, item in enumerate(items):
        w_u, h_u = _fit_units(item, area_w, area_h, density)
        w, h = unit_px(w_u, density), unit_px(h_u, density)
        pos, src, source = exact.get(item["id"]), exact_area, "exact"
        if not isinstance(pos, dict):
            pos, src, source = adapted.get(item["id"]), default_area, "adapted"
        if isinstance(pos, dict):
            x, y = pos_to_xy(pos, w, h, area_w, area_h, src)
        else:
            source = "auto"
            x, y = area_w - MARGIN - w, MARGIN
        x, y = clamp_xy(x, y, w, h, area_w, area_h)
        rank = {"exact": 0, "adapted": 1, "auto": 2}[source]
        wanted.append((rank, item, x, y, w_u, h_u, source))
    wanted.sort(key=lambda row: (row[0], row[3], row[2]))
    placed: List[Dict[str, Any]] = []
    taken: List[Rect] = []
    for _rank, item, x, y, w_u, h_u, source in wanted:
        w, h = unit_px(w_u, density), unit_px(h_u, density)
        spot = None
        for obstacles in (taken + hard + soft, taken + hard):
            spot = nearest_free(x, y, w, h, obstacles, area_w, area_h)
            if spot is not None:
                break
        row = {"id": item["id"], "type": item["type"], "ref": item["ref"], "units": [w_u, h_u],
               "w": w, "h": h, "source": source, "hidden": spot is None}
        if item.get("pinned"):
            row["pinned"] = True
        if spot is None:
            row["x"], row["y"] = round(x, 2), round(y, 2)
        else:
            row["x"], row["y"] = round(spot[0], 2), round(spot[1], 2)
            taken.append(_inflate((spot[0], spot[1], w, h), SPACING))
        placed.append(row)
    return placed

def _rects(value: Any) -> List[Rect]:
    if not value:
        return []
    rows = value if isinstance(value, list) and value and isinstance(value[0], (list, tuple)) else [value]
    out = []
    for row in rows:
        try:
            x, y, w, h = (float(v) for v in row)
        except (TypeError, ValueError):
            continue
        if w > 0 and h > 0 and all(math.isfinite(v) for v in (x, y, w, h)):
            out.append((x, y, w, h))
    return out

def remember(layer: Dict[str, Any], key: str, area_w: float, area_h: float, rects: Iterable[Dict[str, Any]],
             now: Optional[float] = None) -> Dict[str, Any]:
    """The user arranged the items on monitor ``key``: that monitor's own layout becomes exactly this, and it is
    also the new default other monitors adapt from (the most recent arrangement wins)."""
    now = time.time() if now is None else now
    positions: Dict[str, Any] = {}
    for row in rects:
        try:
            x, y, w, h = (float(row[k]) for k in ("x", "y", "w", "h"))
        except (KeyError, TypeError, ValueError):
            continue
        if not all(math.isfinite(v) for v in (x, y, w, h)) or w <= 0 or h <= 0:
            continue
        x, y = clamp_xy(x, y, w, h, area_w, area_h)
        positions[str(row["id"])] = rect_to_pos((x, y, w, h), area_w, area_h)
    monitors = layer.setdefault("monitors", {})
    monitors[key] = {"area": [int(area_w), int(area_h)], "t": int(now), "items": positions}
    if len(monitors) > MAX_MONITORS:
        for old in sorted(monitors, key=lambda k: int((monitors[k] or {}).get("t") or 0))[:-MAX_MONITORS]:
            monitors.pop(old, None)
    default = layer.setdefault("default", {"area": [int(area_w), int(area_h)], "items": {}})
    merged = dict(default.get("items") or {})

    old_area = default.get("area") or list(MIGRATION_AREA)
    if [int(v) for v in old_area] != [int(area_w), int(area_h)]:
        for item_id, pos in list(merged.items()):
            if item_id in positions:
                continue
            fx, fy = pos_to_xy(pos, 0, 0, area_w, area_h, old_area)
            merged[item_id] = rect_to_pos((fx, fy, 0, 0), area_w, area_h)
    merged.update(positions)
    layer["default"] = {"area": [int(area_w), int(area_h)], "items": merged}
    if positions:
        layer.pop("migrated", None)
    return layer

def _clean_pos(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    try:
        dx, dy = float(raw.get("dx", 0)), float(raw.get("dy", 0))
        fx, fy = float(raw.get("fx", 0.5)), float(raw.get("fy", 0.5))
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (dx, dy, fx, fy)):
        return None
    return {"ax": raw.get("ax") if raw.get("ax") in ANCHORS_X else "c",
            "ay": raw.get("ay") if raw.get("ay") in ANCHORS_Y else "m",
            "dx": round(max(-20000.0, min(20000.0, dx)), 2), "dy": round(max(-20000.0, min(20000.0, dy)), 2),
            "fx": round(min(max(fx, 0.0), 1.0), 5), "fy": round(min(max(fy, 0.0), 1.0), 5)}

def _clean_area(raw: Any, fallback: Sequence[int]) -> List[int]:
    try:
        w, h = (int(float(v)) for v in raw)
        if 100 <= w <= 20000 and 100 <= h <= 20000:
            return [w, h]
    except (TypeError, ValueError):
        pass
    return [int(v) for v in fallback]

def _clean_items(raw: Any, ids: set) -> Dict[str, Any]:
    out = {}
    if isinstance(raw, dict):
        for item_id, pos in raw.items():
            if str(item_id) in ids:
                clean = _clean_pos(pos)
                if clean:
                    out[str(item_id)] = clean
    return out

def normalize_layer(raw: Any, items: Sequence[Dict[str, Any]], density: str = "normal") -> Dict[str, Any]:
    """Validated ``layer`` for the current ``layout.items``. No ``layer`` yet (a Round 27 file) = migration from the
    window grid: the user's arrangement keeps its shape, as a block in the top-right corner of the screen."""
    ids = {str(i["id"]) for i in items}
    if not isinstance(raw, dict):
        return migrate_grid(items, density)
    monitors = {}
    for key, row in (raw.get("monitors") or {}).items() if isinstance(raw.get("monitors"), dict) else []:
        if not isinstance(row, dict) or not isinstance(key, str) or len(key) > 40:
            continue
        monitors[key] = {"area": _clean_area(row.get("area"), MIGRATION_AREA),
                         "t": int(row.get("t") or 0) if isinstance(row.get("t"), (int, float)) else 0,
                         "items": _clean_items(row.get("items"), ids)}
    if len(monitors) > MAX_MONITORS:
        for old in sorted(monitors, key=lambda k: monitors[k]["t"])[:-MAX_MONITORS]:
            monitors.pop(old, None)
    default = raw.get("default") if isinstance(raw.get("default"), dict) else {}
    out = {"version": LAYER_VERSION, "monitors": monitors,
           "default": {"area": _clean_area(default.get("area"), MIGRATION_AREA), "items": _clean_items(default.get("items"), ids)}}
    if raw.get("migrated") == "r27-grid":
        out["migrated"] = "r27-grid"
    return out

def migrate_grid(items: Sequence[Dict[str, Any]], density: str = "normal") -> Dict[str, Any]:
    """Round 27 → 28: the window grid (8 columns of ``cell`` px) becomes screen positions.

    The whole grid block keeps its arrangement and sits in the top-right corner of a 1920×1032 work area,
    ``MIGRATION_OFFSET`` px from the edges; :func:`resolve` adapts it to the real monitor (smaller ones shrink the
    offsets, anything that collides with the window or another item moves to the nearest free spot)."""
    step = cell(density) + GAP
    block_w = unit_px(8, density)
    area_w, area_h = MIGRATION_AREA
    positions = {}
    for item in items:
        try:
            gx, gy = int(item.get("x") or 0), int(item.get("y") or 0)
        except (TypeError, ValueError):
            continue
        w, h = item_px(item, density)
        x = area_w - MIGRATION_OFFSET - block_w + gx * step
        y = MIGRATION_OFFSET + gy * step
        positions[str(item["id"])] = rect_to_pos((x, y, w, h), area_w, area_h)
    layer = {"version": LAYER_VERSION, "monitors": {}, "default": {"area": [area_w, area_h], "items": positions}}
    if positions:
        layer["migrated"] = "r27-grid"
    return layer
