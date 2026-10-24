"""Data locations of MemuNote (no heavy imports — read at start-up).

``%APPDATA%\\MemuAI\\memunote`` (or ``$MEMUAI_DATA_DIR/memunote``):
settings.json, session.json, instance.json, recovery/ (unsaved buffers).
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()


def data_root() -> Path:
    env = os.environ.get("MEMUAI_DATA_DIR") or os.environ.get("OMNIHUB_DATA_DIR")
    if env:
        return Path(env)
    return Path(os.environ.get("APPDATA") or os.path.expanduser("~")) / "MemuAI"


def note_dir() -> Path:
    path = data_root() / "memunote"
    path.mkdir(parents=True, exist_ok=True)
    return path


def recovery_dir() -> Path:
    path = note_dir() / "recovery"
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_json(path: Path, default: Any) -> Any:
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, type(default)) else default
    except (OSError, ValueError):
        return default


def write_json(path: Path, value: Any) -> None:
    """Atomic write (temp file + os.replace) so a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, ensure_ascii=False, indent=1)
    with _LOCK:
        fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(data)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def memuai_preferences() -> dict:
    """MemuAI's own preferences (theme, language) — read only, never written."""
    return read_json(data_root() / "memuai" / "preferences.json", {})
