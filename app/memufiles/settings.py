"""MemuFiles settings (``settings.json``) and the user's tags / colours / favourites (``tags.json``).

Every settings key is described in ``settings_schema.py`` (Round 38: MemuAI's Settings → «MemuFiles»
reads and writes the same file through that schema)."""
from __future__ import annotations

import os
import threading
from typing import Any, Dict, List

from .paths import files_dir, read_json, write_json
from .settings_schema import CHOICES, DEFAULTS, RANGES  # Round 38: one schema, also read by MemuAI


_LOCK = threading.Lock()
TAG_COLORS = ("red", "orange", "yellow", "green", "blue", "purple", "gray")


def _path():
    return files_dir() / "settings.json"


def load() -> Dict[str, Any]:
    data = read_json(_path(), {})
    value = dict(DEFAULTS)
    for key, default in DEFAULTS.items():
        if key not in data:
            continue
        got = data[key]
        if isinstance(default, bool):
            ok = isinstance(got, bool)
        elif isinstance(default, int):
            ok = isinstance(got, (int, float)) and not isinstance(got, bool)
        else:
            ok = isinstance(got, type(default))
        if ok and key in CHOICES:
            ok = got in CHOICES[key]
        if ok and key in RANGES:
            got = max(RANGES[key][0], min(RANGES[key][1], int(got)))
        if ok:
            value[key] = got
    return value


def save(changes: Dict[str, Any]) -> Dict[str, Any]:
    with _LOCK:
        current = load()
        for key, val in (changes or {}).items():
            if key not in DEFAULTS:
                continue
            default = DEFAULTS[key]
            if isinstance(default, bool):
                current[key] = bool(val)
            elif isinstance(default, int):
                try:
                    low, high = RANGES.get(key, (int(val), int(val)))
                    current[key] = max(low, min(high, int(val)))
                except (TypeError, ValueError):
                    continue
            elif key in CHOICES:
                if val in CHOICES[key]:
                    current[key] = val
            elif isinstance(default, str):
                current[key] = str(val)[:400]
            elif isinstance(default, list) and isinstance(val, list):
                current[key] = [str(v) for v in val if isinstance(v, str)][:200]
            elif isinstance(default, dict) and isinstance(val, dict):
                items = list(val.items())[-500:]
                current[key] = {str(k)[:2048]: v for k, v in items if isinstance(v, dict)}
        write_json(_path(), current)
        return current


# ------------------------------------------------------------------ tags

def _tags_path():
    return files_dir() / "tags.json"


def _key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def tags_all() -> Dict[str, Dict[str, Any]]:
    """``{normcase path: {"path", "colors": [..], "favorite": bool, "note": str}}``."""
    data = read_json(_tags_path(), {})
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def tags_set(path: str, colors: List[str] = None, favorite: Any = None, note: Any = None) -> Dict[str, Any]:
    with _LOCK:
        data = tags_all()
        key = _key(path)
        row = dict(data.get(key) or {"path": path, "colors": [], "favorite": False, "note": ""})
        row["path"] = path
        if colors is not None:
            row["colors"] = [c for c in TAG_COLORS if c in set(colors)]
        if favorite is not None:
            row["favorite"] = bool(favorite)
        if note is not None:
            row["note"] = str(note)[:500]
        if not row["colors"] and not row["favorite"] and not row.get("note"):
            data.pop(key, None)
        else:
            data[key] = row
        write_json(_tags_path(), data)
        return row


def tags_moved(old: str, new: str) -> None:
    """Keep tags when a file/folder is renamed or moved inside MemuFiles."""
    with _LOCK:
        data = tags_all()
        okey = _key(old)
        changed = False
        for key in list(data):
            if key == okey or key.startswith(okey + os.sep.lower() if os.name == "nt" else okey + os.sep):
                row = data.pop(key)
                suffix = row["path"][len(old):]
                row["path"] = new + suffix
                data[_key(row["path"])] = row
                changed = True
        if changed:
            write_json(_tags_path(), data)
