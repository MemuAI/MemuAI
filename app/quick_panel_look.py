"""Round 30: how the panels of Quick Access look — and panels built into the Alt+Space window itself.

Stored in ``quick_canvas.json`` next to the layout (so it follows the profile and Google Drive sync, category
«panels»):

* ``look`` — the global look of every panel in the window grid, on the screen layer and in the built-in section:
  ``style`` card | compact | text | icon (``text`` — no background and no frame, only lines of values; ``icon`` — a big
  icon + the value), ``opacity`` of the background 0–100 %, ``font`` 80–140 %, ``accent`` "" (the theme's) or
  ``#rrggbb``, ``theme`` app | auto (Windows light/dark) | light | dark, ``titles`` (panel names on/off),
  ``radius`` 0–24 px, ``shadow``, ``dim`` — the idle opacity of a panel on the screen layer (0 = off: always full),
  ``lines`` — how many value lines the text / compact styles show (1–5).
* ``looks`` — ``{panel_id: {…partial look…}}``: one panel's own look over the global one (only what differs).
  Round 32: a panel's own look may carry ``text`` — the «text» look as real text: a user template with
  placeholders bound to the panel's values (``{price|round:0}``, ``{#items|limit:3}{title}{/items}``), drawn by
  ``pgstatic/quick/panel-text.js``: ``{mode: flow|free, h, halo: auto|off|soft|strong, blocks: [{t, x, y, w, align,
  size, weight, color, italic, font}]}`` (see :func:`clean_text`). Only per panel (a template names one panel's values).
* ``embed`` — panels built into the Alt+Space window: ``place`` off | above | below | side (a column on the right),
  ``items`` ``[{ref, hidden}]`` in the user's order (a hidden one keeps its place), ``style`` — the section's own
  default ("" = the global look), ``max`` — how many are shown at most.

The page applies the effective look (:func:`effective`) — the server only validates.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

STYLES = ("card", "compact", "text", "icon")
THEMES = ("app", "auto", "light", "dark")
PLACES = ("off", "above", "below", "side")
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
PANEL_ID_RE = re.compile(r"^p_[A-Za-z0-9_-]{1,40}$")
MAX_EMBED = 24
MAX_LOOKS = 120

DEFAULT_LOOK: Dict[str, Any] = {
    "style": "card", "opacity": 100, "font": 100, "accent": "", "theme": "app", "titles": True,
    "radius": 14, "shadow": True, "dim": 0, "lines": 3,
}
DEFAULT_EMBED: Dict[str, Any] = {"place": "off", "items": [], "style": "", "max": 6}
_RANGES = {"opacity": (0, 100), "font": (80, 140), "radius": (0, 24), "dim": (0, 90), "lines": (1, 5)}

TEXT_MODES = ("flow", "free")
TEXT_HALOS = ("auto", "off", "soft", "strong")
TEXT_ALIGNS = ("left", "center", "right")
TEXT_FONTS = ("", "display", "mono", "serif")
TEXT_COLORS = ("", "muted", "accent", "up", "down")
MAX_TEXT_BLOCKS = 12
MAX_TEXT_LEN = 1000
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

def _num(value: Any, lo: int, hi: int) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return int(max(lo, min(hi, round(number))))

def _float(value: Any, lo: float, hi: float, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):
        return default
    return round(max(lo, min(hi, number)), 1)

def clean_text_block(raw: Any) -> Optional[Dict[str, Any]]:
    """One block of a text template: the template string and where / how it is drawn."""
    if isinstance(raw, str):
        raw = {"t": raw}
    if not isinstance(raw, dict):
        return None
    tpl = raw.get("t")
    if not isinstance(tpl, str):
        return None
    tpl = _CTRL_RE.sub("", tpl.replace("\r\n", "\n").replace("\r", "\n"))[:MAX_TEXT_LEN]
    if not tpl.strip():
        return None
    out: Dict[str, Any] = {"t": tpl,
                           "x": _float(raw.get("x"), 0, 95, 0), "y": _float(raw.get("y"), 0, 95, 0),
                           "w": _float(raw.get("w"), 5, 100, 100),
                           "align": raw.get("align") if raw.get("align") in TEXT_ALIGNS else "left",
                           "size": _num(raw.get("size"), 50, 400) or 100,
                           "weight": int(round((_num(raw.get("weight"), 300, 800) or 500) / 100.0) * 100),
                           "color": "", "italic": raw.get("italic") is True,
                           "font": raw.get("font") if raw.get("font") in TEXT_FONTS else ""}
    color = raw.get("color")
    if color in TEXT_COLORS:
        out["color"] = color
    elif isinstance(color, str) and HEX_RE.match(color):
        out["color"] = color.lower()
    if out["x"] + out["w"] > 100:
        out["w"] = max(5.0, round(100 - out["x"], 1))
    return out

def clean_text(raw: Any) -> Optional[Dict[str, Any]]:
    """A panel's text template (``None`` = none: the panel's automatic template is used)."""
    if not isinstance(raw, dict):
        return None
    blocks = [b for b in (clean_text_block(x) for x in (raw.get("blocks") if isinstance(raw.get("blocks"), list) else [])[:MAX_TEXT_BLOCKS * 2]) if b]
    if not blocks:
        return None
    return {"mode": raw.get("mode") if raw.get("mode") in TEXT_MODES else "flow",
            "h": _num(raw.get("h"), 24, 480) or 96,
            "halo": raw.get("halo") if raw.get("halo") in TEXT_HALOS else "auto",
            "blocks": blocks[:MAX_TEXT_BLOCKS]}

def clean_look(raw: Any, *, partial: bool = False) -> Dict[str, Any]:
    """A valid look; ``partial`` keeps only the keys present (a panel's own overrides; only these may carry
    a ``text`` template)."""
    src = raw if isinstance(raw, dict) else {}
    out: Dict[str, Any] = {} if partial else dict(DEFAULT_LOOK)
    if src.get("style") in STYLES:
        out["style"] = src["style"]
    if src.get("theme") in THEMES:
        out["theme"] = src["theme"]
    if "accent" in src and (src["accent"] == "" or (isinstance(src["accent"], str) and HEX_RE.match(src["accent"]))):
        out["accent"] = src["accent"].lower()
    for key in ("titles", "shadow"):
        if isinstance(src.get(key), bool):
            out[key] = src[key]
    for key, (lo, hi) in _RANGES.items():
        if key in src:
            number = _num(src[key], lo, hi)
            if number is not None:
                out[key] = number
    if partial and "text" in src:
        text = clean_text(src["text"])
        if text:
            out["text"] = text
    return out

def clean_looks(raw: Any, known: Optional[set] = None) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for key, value in (raw.items() if isinstance(raw, dict) else []):
        if not isinstance(key, str) or not PANEL_ID_RE.match(key) or (known is not None and key not in known):
            continue
        look = clean_look(value, partial=True)
        if look:
            out[key] = look
        if len(out) >= MAX_LOOKS:
            break
    return out

def clean_embed(raw: Any) -> Dict[str, Any]:
    src = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULT_EMBED)
    if src.get("place") in PLACES:
        out["place"] = src["place"]
    if src.get("style") in STYLES or src.get("style") == "":
        out["style"] = src["style"]
    number = _num(src.get("max"), 1, 12) if "max" in src else None
    if number is not None:
        out["max"] = number
    items: List[Dict[str, Any]] = []
    seen = set()
    for row in src.get("items") if isinstance(src.get("items"), list) else []:
        if isinstance(row, str):
            row = {"ref": row}
        if not isinstance(row, dict):
            continue
        ref = str(row.get("ref") or "")
        if not PANEL_ID_RE.match(ref) or ref in seen:
            continue
        seen.add(ref)
        items.append({"ref": ref, "hidden": row.get("hidden") is True})
        if len(items) >= MAX_EMBED:
            break
    out["items"] = items
    return out

def effective(look: Dict[str, Any], looks: Dict[str, Dict[str, Any]], panel_id: str, *,
              embed_style: str = "") -> Dict[str, Any]:
    """The look one panel is drawn with (global → the section's style → the panel's own)."""
    out = dict(clean_look(look))
    if embed_style in STYLES:
        out["style"] = embed_style
    out.update(looks.get(panel_id) or {})
    return out

def normalize(doc_raw: Dict[str, Any]) -> Dict[str, Any]:
    """``look`` / ``looks`` / ``embed`` of a stored document (missing → defaults)."""
    raw = doc_raw if isinstance(doc_raw, dict) else {}
    return {"look": clean_look(raw.get("look")), "looks": clean_looks(raw.get("looks")),
            "embed": clean_embed(raw.get("embed"))}

def apply_patch(doc: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    """``POST /api/quick/canvas/look``: ``{look}`` (global, merged), ``{panel, look}`` (a panel's own, merged;
    ``null`` values drop a key), ``{panel, reset: true}``, ``{reset_all: true}``, ``{embed}`` (merged)."""
    if body.get("reset_all") is True:
        doc["look"] = dict(DEFAULT_LOOK)
        doc["looks"] = {}
    panel = str(body.get("panel") or "")
    patch = body.get("look") if isinstance(body.get("look"), dict) else None
    if panel:
        if not PANEL_ID_RE.match(panel):
            raise ValueError("bad panel id")
        own = dict(doc["looks"].get(panel) or {})
        if body.get("reset") is True:
            own = {}
        if patch:
            for key, value in patch.items():
                if value is None:
                    own.pop(key, None)
            own.update(clean_look({k: v for k, v in patch.items() if v is not None}, partial=True))
        if own:
            doc["looks"][panel] = own
        else:
            doc["looks"].pop(panel, None)
    elif patch:
        doc["look"] = clean_look({**doc["look"], **patch})
    if isinstance(body.get("embed"), dict):
        doc["embed"] = clean_embed({**doc["embed"], **body["embed"]})
    return {"look": doc["look"], "looks": doc["looks"], "embed": doc["embed"]}
