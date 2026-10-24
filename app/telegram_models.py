"""Round 33 (r33c-models-lists): how the Telegram bot and the Mini App name a model and offer its reasoning levels.

One source for every window (user item #11):

* names — the user's own name (``model_aliases``) first, then the automatic readable name of
  :mod:`app.model_names` (``aws9-claude-opus-4-6`` → ``Claude Opus 4.6``), the same rules as the MemuAI window;
  plain-text places add the full id beside it (``Claude Opus 4.6 · aws9-claude-opus-4-6``);
* reasoning levels — only the levels THIS model accepts (:func:`app.reasoning_levels.reasoning_levels`), not a
  generic list; the chosen level is the desktop's own per-model setting ``model_efforts`` (falls back to
  ``default_effort``), so a level picked in Telegram is the one MemuAI uses for that model and vice versa.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

#: Level → (Russian, English) button text.
EFFORT_LABELS: Dict[str, tuple] = {
    "auto": ("Авто", "Auto"), "off": ("Выкл", "Off"), "on": ("Вкл", "On"), "minimal": ("Минимум", "Minimal"),
    "low": ("Низкая", "Low"), "medium": ("Средняя", "Medium"), "high": ("Высокая", "High"),
    "xhigh": ("Очень высокая", "Extra high"), "max": ("Максимум", "Max"),
}
LEVELS = tuple(EFFORT_LABELS)


def _entry(model_id: str) -> Any:
    try:
        from .state import state
        return state.find_model(model_id)
    except Exception:
        return None


def auto_name(model_id: str, entry: Any = None) -> str:
    """The automatic readable name (no alias)."""
    model_id = str(model_id or "")
    if not model_id:
        return ""
    entry = entry if entry is not None else _entry(model_id)
    try:
        from . import model_names
        return model_names.pretty(model_id, prefix=str(getattr(entry, "prefix", "") or "")) or model_id
    except Exception:
        return model_id


def display_name(model_id: str, prefs: Optional[Dict[str, Any]] = None, entry: Any = None) -> str:
    """Alias > automatic name."""
    aliases = (prefs or {}).get("model_aliases")
    alias = aliases.get(model_id) if isinstance(aliases, dict) else None
    if isinstance(alias, str) and alias.strip():
        return alias.strip()
    return auto_name(model_id, entry)


def label(model_id: str, prefs: Optional[Dict[str, Any]] = None, limit: int = 0) -> str:
    """``Name · full-id`` when it fits ``limit`` characters (0 — no limit), else the name alone."""
    name = display_name(model_id, prefs)
    same = re.sub(r"[\s._:/-]+", "", name.lower()) == re.sub(r"[\s._:/-]+", "", str(model_id or "").lower())
    full = f"{name} · {model_id}" if name and not same else (name or str(model_id or ""))
    if not limit or len(full) <= limit:
        return full
    return name if len(name) <= limit else name[:limit - 1] + "…"


def effort_options(model_id: str) -> List[str]:
    """The reasoning levels this model offers (``auto`` first; ``[]`` — no control)."""
    if not model_id:
        return []
    entry = _entry(model_id)
    try:
        from . import reasoning_levels
        return list(reasoning_levels.reasoning_levels(model_id, str(getattr(entry, "provider", "") or ""),
                                                      str(getattr(entry, "model", "") or "")))
    except Exception:
        return []


def chosen_effort(model_id: str, prefs: Optional[Dict[str, Any]] = None) -> str:
    """The level the user picked for this model (MemuAI's ``model_efforts``), else ``default_effort``; ``auto``."""
    prefs = prefs or {}
    own = prefs.get("model_efforts") if isinstance(prefs.get("model_efforts"), dict) else {}
    value = str(own.get(model_id) or prefs.get("default_effort") or "auto")
    return value if value in LEVELS else "auto"


def effort_param(model_id: str, prefs: Optional[Dict[str, Any]] = None) -> str:
    """What to send as ``effort`` for this model (``""`` — nothing, the model decides). The Proxy clamps it again."""
    value = chosen_effort(model_id, prefs)
    if value == "auto":
        return ""
    options = effort_options(model_id)
    if options and value not in options:
        try:
            from . import reasoning_levels
            entry = _entry(model_id)
            prof = reasoning_levels.profile(model_id, str(getattr(entry, "provider", "") or ""),
                                            str(getattr(entry, "model", "") or ""))
            value = reasoning_levels.clamp(value, prof)
        except Exception:
            return ""
    return "" if value == "auto" or not options else value


def efforts_patch(model_id: str, level: str, prefs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The preferences patch that stores ``level`` for ``model_id`` (``auto`` removes the model's own value)."""
    own = dict((prefs or {}).get("model_efforts") or {}) if isinstance((prefs or {}).get("model_efforts"), dict) else {}
    if level == "auto" or level not in LEVELS:
        own.pop(model_id, None)
    else:
        own[model_id] = level
    return {"model_efforts": own}


def effort_label(level: str, lang: str = "ru") -> str:
    ru, en = EFFORT_LABELS.get(level, (level, level))
    return en if lang == "en" else ru


def catalog_fields(model_id: str, entry: Any = None, prefs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """What a Mini App picker row needs besides id/provider: names, badges, vision tri-state, reasoning levels."""
    entry = entry if entry is not None else _entry(model_id)
    out: Dict[str, Any] = {"auto_name": auto_name(model_id, entry), "badges": [], "vision_state": "unknown",
                           "reasoning_levels": effort_options(model_id)}
    try:
        from . import model_names
        out["badges"] = model_names.parse(model_id, prefix=str(getattr(entry, "prefix", "") or ""))["badges"]
    except Exception:
        pass
    try:
        from . import model_catalog
        out["vision_state"] = model_catalog.vision_state(model_id, str(getattr(entry, "provider", "") or ""),
                                                         entry)["state"]
    except Exception:
        pass
    if prefs is not None:
        out["effort"] = chosen_effort(model_id, prefs)
    return out
