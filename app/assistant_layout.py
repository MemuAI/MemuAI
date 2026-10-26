"""Window layout capture/restore — the pure geometry (Round 22).

A *layout* is a JSON snapshot of visible top-level windows: which app (exe
path, display name, AppUserModelID when known), on which monitor, at which
rectangle and in which state. Rectangles are in **physical pixels of the
virtual screen** (MemuAI is per-monitor DPI aware), may be negative (monitors
left of / above the primary one) and are the *visible* frame (DWM extended
frame bounds, without the invisible resize borders of Windows 10/11).

Each window also stores its rectangle relative to its monitor's work area
(fractions 0..1), so a layout saved on a 2560×1440 monitor is restored
proportionally on a 1920×1080 one, and a layout from a missing monitor lands
on the closest remaining monitor instead of off-screen.

Nothing here calls Windows: ``assistant_actions_win`` provides the window and
monitor lists and applies the plan; tests feed synthetic ones.
"""
from __future__ import annotations

import difflib
import re
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

Rect = Tuple[int, int, int, int]
MIN_VISIBLE = 64
LAYOUT_VERSION = 1
MAX_WINDOWS = 24

def _rect(value: Any) -> Optional[Rect]:
    try:
        left, top, right, bottom = (int(round(float(v))) for v in list(value)[:4])
    except (TypeError, ValueError, OverflowError):
        return None
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom

def _int(value: Any, default: int = 0) -> int:
    """``int()`` that never raises: imported JSON may carry text, NaN or 1e999 (Round 23 — an
    ``OverflowError`` from ``int(float('inf'))`` escaped as a 500 from the commands import)."""
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default

def width(rect: Rect) -> int:
    return rect[2] - rect[0]

def height(rect: Rect) -> int:
    return rect[3] - rect[1]

def intersection(a: Rect, b: Rect) -> int:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return max(0, w) * max(0, h)

def monitor_for_rect(rect: Rect, monitors: Sequence[Dict[str, Any]]) -> int:
    """Index of the monitor holding most of ``rect`` (closest centre when none overlaps)."""
    best, best_area = 0, -1
    for index, mon in enumerate(monitors):
        area = intersection(rect, tuple(mon["rect"]))
        if area > best_area:
            best, best_area = index, area
    if best_area > 0:
        return best
    cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2

    def dist(mon):
        r = mon["rect"]
        dx = max(r[0] - cx, 0, cx - r[2])
        dy = max(r[1] - cy, 0, cy - r[3])
        return dx * dx + dy * dy
    return min(range(len(monitors)), key=lambda i: dist(monitors[i])) if monitors else 0

def relative(rect: Rect, work: Rect) -> Dict[str, float]:
    ww, wh = max(1, width(work)), max(1, height(work))
    return {"x": round((rect[0] - work[0]) / ww, 5), "y": round((rect[1] - work[1]) / wh, 5),
            "w": round(width(rect) / ww, 5), "h": round(height(rect) / wh, 5)}

def from_relative(rel: Dict[str, float], work: Rect) -> Rect:
    ww, wh = width(work), height(work)
    left = work[0] + int(round(float(rel.get("x", 0)) * ww))
    top = work[1] + int(round(float(rel.get("y", 0)) * wh))
    w = max(120, int(round(float(rel.get("w", 0.5)) * ww)))
    h = max(80, int(round(float(rel.get("h", 0.5)) * wh)))
    return left, top, left + w, top + h

def clamp_to(rect: Rect, work: Rect, min_visible: int = MIN_VISIBLE) -> Rect:
    """Shrink a rectangle larger than the work area and pull it back so it is reachable."""
    w = min(width(rect), width(work))
    h = min(height(rect), height(work))
    left = min(max(rect[0], work[0] - w + min_visible), work[2] - min_visible)
    top = min(max(rect[1], work[1]), work[3] - min_visible)
    return left, top, left + w, top + h

def normalize_monitor(mon: Dict[str, Any], index: int) -> Dict[str, Any]:
    rect = _rect(mon.get("rect")) or (0, 0, 1920, 1080)
    work = _rect(mon.get("work")) or rect
    return {"index": index, "device": str(mon.get("device") or ""), "rect": list(rect), "work": list(work),
            "dpi": max(48, min(960, _int(mon.get("dpi") or 96, 96))), "primary": bool(mon.get("primary"))}

def capture(windows: Sequence[Dict[str, Any]], monitors: Sequence[Dict[str, Any]], *,
            name: str = "", now: Optional[float] = None) -> Dict[str, Any]:
    """Snapshot of ``windows`` (top-most first) as a JSON-safe layout."""
    mons = [normalize_monitor(m, i) for i, m in enumerate(monitors)] or [normalize_monitor({}, 0)]
    rows = []
    for z, win in enumerate(windows):
        rect = _rect(win.get("rect"))
        if not rect:
            continue
        state = str(win.get("state") or "normal")
        if state not in ("normal", "maximized", "minimized"):
            state = "normal"

        normal = _rect(win.get("normal_rect")) or rect
        geo = normal if state != "normal" else rect
        index = monitor_for_rect(geo, mons)
        mon = mons[index]
        rows.append({
            "app": {"name": str(win.get("app") or win.get("name") or "")[:120],
                    "exe": str(win.get("exe") or "").lower()[:120],
                    "path": str(win.get("path") or "")[:400],
                    "aumid": str(win.get("aumid") or "")[:300]},
            "title": str(win.get("title") or "")[:200],
            "monitor": index, "device": mon["device"],
            "rect": list(geo), "rel": relative(geo, tuple(mon["work"])), "state": state, "z": z,
        })
        if len(rows) >= MAX_WINDOWS:
            break
    return {"version": LAYOUT_VERSION, "name": str(name or "")[:60], "captured_at": int(now or time.time()),
            "monitors": mons, "windows": rows}

def validate(layout: Any) -> Dict[str, Any]:
    """Clean an imported/stored layout; raises ValueError when unusable."""
    if not isinstance(layout, dict):
        raise ValueError("layout must be an object")
    mons = [normalize_monitor(m, i) for i, m in enumerate(layout.get("monitors") or []) if isinstance(m, dict)][:8]
    if not mons:
        raise ValueError("layout has no monitors")
    rows = []
    for win in (layout.get("windows") or [])[:MAX_WINDOWS]:
        if not isinstance(win, dict):
            continue
        rect = _rect(win.get("rect"))
        app = win.get("app") if isinstance(win.get("app"), dict) else {}
        if not rect or not (app.get("exe") or app.get("path") or app.get("name") or app.get("aumid")):
            continue
        index = _int(win.get("monitor") or 0)
        index = index if 0 <= index < len(mons) else monitor_for_rect(rect, mons)
        rel = win.get("rel") if isinstance(win.get("rel"), dict) else relative(rect, tuple(mons[index]["work"]))
        try:
            rel = {k: max(-4.0, min(4.0, float(rel.get(k, 0)))) for k in ("x", "y", "w", "h")}
            if any(v != v for v in rel.values()):
                raise ValueError("NaN")
        except (TypeError, ValueError):
            rel = relative(rect, tuple(mons[index]["work"]))
        state = win.get("state") if win.get("state") in ("normal", "maximized", "minimized") else "normal"
        rows.append({"app": {k: str(app.get(k) or "")[:400] for k in ("name", "exe", "path", "aumid")},
                     "title": str(win.get("title") or "")[:200], "monitor": index,
                     "device": str(win.get("device") or mons[index]["device"]), "rect": list(rect), "rel": rel,
                     "state": state, "z": _int(win.get("z") or len(rows), len(rows))})
    if not rows:
        raise ValueError("layout has no windows")
    return {"version": LAYOUT_VERSION, "name": str(layout.get("name") or "")[:60],
            "captured_at": _int(layout.get("captured_at") or 0), "monitors": mons, "windows": rows}

def map_monitors(saved: Sequence[Dict[str, Any]], current: Sequence[Dict[str, Any]]) -> Dict[int, Tuple[int, bool]]:
    """saved index → (current index, same geometry). Device name + size first, then position, then order."""
    out: Dict[int, Tuple[int, bool]] = {}
    if not current:
        return out
    used: set = set()
    cur = list(current)

    def same(a, b):
        return list(a["rect"]) == list(b["rect"]) and list(a["work"]) == list(b["work"])

    for s in saved:
        for i, c in enumerate(cur):
            if i not in used and s["device"] and s["device"] == c.get("device") and same(s, c):
                out[s["index"]] = (i, True)
                used.add(i)
                break
    for s in saved:
        if s["index"] in out:
            continue
        for i, c in enumerate(cur):
            if i not in used and same(s, c):
                out[s["index"]] = (i, True)
                used.add(i)
                break
    for s in saved:
        if s["index"] in out:
            continue
        for i, c in enumerate(cur):
            if i not in used and s["device"] and s["device"] == c.get("device"):
                out[s["index"]] = (i, False)
                used.add(i)
                break
    order = sorted(range(len(cur)), key=lambda i: (cur[i]["rect"][0], cur[i]["rect"][1]))
    saved_order = sorted(saved, key=lambda m: (m["rect"][0], m["rect"][1]))
    for rank, s in enumerate(saved_order):
        if s["index"] in out:
            continue
        free = [i for i in order if i not in used]
        if free:
            pick = free[min(rank, len(free) - 1)] if len(saved) == len(cur) else free[0]
            out[s["index"]] = (pick, False)
            used.add(pick)
        else:

            centre = ((s["rect"][0] + s["rect"][2]) / 2, (s["rect"][1] + s["rect"][3]) / 2)
            box = (int(centre[0]), int(centre[1]), int(centre[0]) + 1, int(centre[1]) + 1)
            out[s["index"]] = (monitor_for_rect(box, cur), False)
    return out

def _title_score(a: str, b: str) -> float:
    a, b = re.sub(r"\s+", " ", a or "").lower(), re.sub(r"\s+", " ", b or "").lower()
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()

def match_windows(saved: Sequence[Dict[str, Any]], running: Sequence[Dict[str, Any]]) -> List[Optional[int]]:
    """For each saved window the index of a running window of the same app (each used once)."""
    used: set = set()
    result: List[Optional[int]] = []
    for row in saved:
        app = row.get("app") or {}
        exe = (app.get("exe") or "").lower()
        path = (app.get("path") or "").lower()
        aumid = app.get("aumid") or ""
        best, best_score = None, 0.0
        for i, win in enumerate(running):
            if i in used:
                continue
            same_app = (aumid and win.get("aumid") == aumid) or (path and str(win.get("path") or "").lower() == path) \
                or (exe and str(win.get("exe") or "").lower() == exe)
            if not same_app:
                continue
            score = 1.0 + _title_score(row.get("title", ""), str(win.get("title") or ""))
            if score > best_score:
                best, best_score = i, score
        if best is not None:
            used.add(best)
        result.append(best)
    return result

def target_rect(row: Dict[str, Any], saved_monitors: Sequence[Dict[str, Any]],
                current: Sequence[Dict[str, Any]], mapping: Dict[int, Tuple[int, bool]]) -> Tuple[int, Rect]:
    """(current monitor index, visible rectangle) where a saved window goes."""
    cur_index, same = mapping.get(int(row.get("monitor") or 0), (0, False))
    mon = current[cur_index]
    work = tuple(mon["work"])
    if same:
        rect = _rect(row["rect"]) or from_relative(row["rel"], work)
    else:
        rect = from_relative(row["rel"], work)
    return cur_index, clamp_to(rect, work)

def plan_restore(layout: Dict[str, Any], current_monitors: Sequence[Dict[str, Any]],
                 running: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Steps to restore ``layout``: ``{row, monitor, rect, state, window (index in running) | None, launch}``.

    Steps are ordered bottom-most first so placing them in order rebuilds the z-order."""
    clean = validate(layout)
    current = [normalize_monitor(m, i) for i, m in enumerate(current_monitors)] or [normalize_monitor({}, 0)]
    mapping = map_monitors(clean["monitors"], current)
    matches = match_windows(clean["windows"], running)
    steps = []
    for row, match in zip(clean["windows"], matches):
        mon_index, rect = target_rect(row, clean["monitors"], current, mapping)
        steps.append({"row": row, "monitor": mon_index, "rect": list(rect), "state": row["state"],
                      "window": match, "launch": match is None})
    steps.sort(key=lambda s: -int(s["row"].get("z") or 0))
    return steps

def frame_offsets(window_rect: Rect, visible_rect: Rect) -> Tuple[int, int, int, int]:
    """Invisible resize borders: GetWindowRect minus DWM extended frame bounds (each ≥ 0)."""
    return (max(0, visible_rect[0] - window_rect[0]), max(0, visible_rect[1] - window_rect[1]),
            max(0, window_rect[2] - visible_rect[2]), max(0, window_rect[3] - visible_rect[3]))

def expand(rect: Rect, offsets: Tuple[int, int, int, int]) -> Rect:
    """The SetWindowPos rectangle that makes the *visible* frame equal ``rect``."""
    return rect[0] - offsets[0], rect[1] - offsets[1], rect[2] + offsets[2], rect[3] + offsets[3]

def snap_rect(work: Rect, where: str) -> Rect:
    l, t, r, b = work
    mx, my = (l + r) // 2, (t + b) // 2
    return {"snap_left": (l, t, mx, b), "snap_right": (mx, t, r, b), "snap_top": (l, t, r, my),
            "snap_bottom": (l, my, r, b)}.get(where, work)

def center_rect(rect: Rect, work: Rect) -> Rect:
    w, h = min(width(rect), width(work)), min(height(rect), height(work))
    left = work[0] + (width(work) - w) // 2
    top = work[1] + (height(work) - h) // 2
    return left, top, left + w, top + h

def tile(count: int, work: Rect, layout: str = "side_by_side") -> List[Rect]:
    """Rectangles for ``count`` windows on one monitor."""
    if count <= 0:
        return []
    l, t, r, b = work
    W, H = r - l, b - t
    if layout == "cascade":
        step = max(24, min(48, H // 20))
        w, h = int(W * 0.66), int(H * 0.7)

        fit = max(1, min((W - w) // step, (H - h) // step) + 1)
        return [(l + (i % fit) * step, t + (i % fit) * step, l + (i % fit) * step + w, t + (i % fit) * step + h)
                for i in range(count)]
    if layout == "stack":
        rows = count
        cols = 1
    elif layout == "grid" or count > 3:
        cols = 1
        while cols * cols < count:
            cols += 1
        rows = (count + cols - 1) // cols
    else:
        cols, rows = count, 1
    out = []
    for i in range(count):
        row, col = divmod(i, cols)

        in_row = cols if row < rows - 1 else count - cols * (rows - 1)
        cw = W // in_row
        x0 = l + col * cw
        x1 = r if col == in_row - 1 else x0 + cw
        rh = H // rows
        y0 = t + row * rh
        y1 = b if row == rows - 1 else y0 + rh
        out.append((x0, y0, x1, y1))
    return out

def move_to_monitor(rect: Rect, src_work: Rect, dst_work: Rect) -> Rect:
    """Same relative position and size on another monitor."""
    return clamp_to(from_relative(relative(rect, src_work), dst_work), dst_work)

def pick_monitor(spec: Any, current: int, count: int) -> int:
    text = str(spec or "next").strip().lower()
    if count <= 0:
        return 0
    if text in ("next", "other", "следующий", "другой"):
        return (current + 1) % count
    if text in ("prev", "previous", "предыдущий"):
        return (current - 1) % count
    try:
        number = int(float(text))
    except ValueError:
        return (current + 1) % count
    return max(0, min(count - 1, number - 1))
