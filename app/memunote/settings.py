"""MemuNote preferences: defaults, load normalisation and save normalisation.

Stored in ``memunote/settings.json``; every key is described in ``settings_schema.py``
(Round 38 — MemuAI's Settings → «MemuNote» reads the same schema). Every key is validated on load
*and* on save (the page sends partial updates), so a hand-edited or old file can never put the
editor into an invalid state. ``theme``/``language`` = ``"auto"``
follow MemuAI's own preferences.
"""
from __future__ import annotations
import re

from typing import Any, Dict

from .paths import memuai_preferences, note_dir, read_json, write_json
from .settings_schema import CHOICES, DEFAULTS, FONT_PRESETS, RANGES  # noqa: F401  (Round 38: one schema, also read by MemuAI)

_CSS_IMPORT_RE = re.compile(r"@import[^;]*;?", re.IGNORECASE)
_CSS_CHARSET_RE = re.compile(r"@charset[^;]*;?", re.IGNORECASE)
_CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE | re.DOTALL)


def sanitize_custom_css(value):
    """User CSS for the app window only: no outside loads, no style-tag escape, capped.

    ``@import``/``@charset`` go, ``url()`` stays only for ``data:`` (fonts, icons),
    everything else becomes an empty ``url()`` the parser drops. ``<``/``>`` go so the
    text can never break out of the ``<style>`` element it is placed in.
    """
    if not isinstance(value, str):
        return ""
    text = value[:20000]
    text = _CSS_IMPORT_RE.sub("", text)
    text = _CSS_CHARSET_RE.sub("", text)

    def _url(match):
        inner = (match.group(2) or "").strip()
        return match.group(0) if inner.lower().startswith("data:") else "url()"

    text = _CSS_URL_RE.sub(_url, text)
    text = text.replace("<", "").replace(">", "")
    text = re.sub(r"javascript\s*:", "", text, flags=re.IGNORECASE)
    text = re.sub(r"expression\s*\(", "(", text, flags=re.IGNORECASE)
    return text.strip()

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
        elif key == "accent_custom":
            from .appearance import normalize_hex
            item = normalize_hex(item)
        elif key == "proxy_url":
            from .ai import _valid_local
            item = _valid_local(str(item or "")) if item else ""
        elif key == "custom_css":
            item = sanitize_custom_css(item)
        else:
            item = str(item or "")[:120] if isinstance(default, str) else default
        out[key] = item

    if raw.get("md_live_migrated") is not True:
        if out["markdown_preview"] == "split":
            out["markdown_preview"] = "live"
        out["md_live_migrated"] = True
    return out

def path():
    return note_dir() / "settings.json"

def load() -> Dict[str, Any]:
    return normalize(read_json(path(), {}))

def language(value: str = "") -> str:
    value = value or load().get("language", "auto")
    if value in ("ru", "en"):
        return value
    return "en" if memuai_preferences().get("language") == "en" else "ru"

def save(update: Dict[str, Any]) -> Dict[str, Any]:
    current = load()
    if isinstance(update, dict):
        current.update({k: v for k, v in update.items() if k in DEFAULTS})
    value = normalize(current)
    write_json(path(), value)
    return value
