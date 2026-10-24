"""MemuNote preferences: defaults, load normalisation and save normalisation.

Stored in ``memunote/settings.json``; every key is described in ``settings_schema.py``
(Round 38 — MemuAI's Settings → «MemuNote» reads the same schema). Every key is validated on load
*and* on save (the page sends partial updates), so a hand-edited or old file can never put the
editor into an invalid state. ``theme``/``language`` = ``"auto"``
follow MemuAI's own preferences.
"""
from __future__ import annotations

from typing import Any, Dict

from .paths import note_dir, read_json, write_json
from .settings_schema import CHOICES, DEFAULTS, FONT_PRESETS, RANGES  # noqa: F401  (Round 38: one schema, also read by MemuAI)

# The schema is strict for writes from MemuAI; loading stays forgiving: unknown encodings are normalised by
# textio (not limited to the menu), floats are handled below, out-of-range numbers are clamped.
_CHOICES = {k: v for k, v in CHOICES.items() if k != "default_encoding"}
_RANGES = {k: v for k, v in RANGES.items() if isinstance(DEFAULTS[k], int)}


def normalize(value: Any) -> Dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    out: Dict[str, Any] = {}
    for key, default in DEFAULTS.items():
        item = raw.get(key, default)
        if isinstance(default, bool):
            item = item if isinstance(item, bool) else default
        elif key in _CHOICES:
            item = item if item in _CHOICES[key] else default
        elif key in _RANGES:
            low, high = _RANGES[key]
            try:
                item = int(item)
            except (TypeError, ValueError):
                item = default
            item = max(low, min(high, item))
        elif key == "line_height":
            try:
                item = round(max(1.0, min(2.6, float(item))), 2)
            except (TypeError, ValueError):
                item = default
        elif key == "font_family":
            item = str(item or "").strip()[:64] or default
            if any(ch in item for ch in ";{}<>\"\\"):
                item = default
        elif key == "default_encoding":
            from .formats.textio import normalize_encoding
            item = normalize_encoding(str(item))
        elif key == "proxy_url":
            from .ai import _valid_local
            item = _valid_local(str(item or "")) if item else ""
        else:
            item = str(item or "")[:120] if isinstance(default, str) else default
        out[key] = item
    # Round 33: "split" was the default before the live editor existed — move it once to "live" (an explicit choice
    # made after this, saved together with md_live_migrated=True, is kept).
    if raw.get("md_live_migrated") is not True:
        if out["markdown_preview"] == "split":
            out["markdown_preview"] = "live"
        out["md_live_migrated"] = True
    return out


def path():
    return note_dir() / "settings.json"


def load() -> Dict[str, Any]:
    return normalize(read_json(path(), {}))


def save(update: Dict[str, Any]) -> Dict[str, Any]:
    current = load()
    if isinstance(update, dict):
        current.update({k: v for k, v in update.items() if k in DEFAULTS})
    value = normalize(current)
    write_json(path(), value)
    return value
