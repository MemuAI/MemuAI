"""Data locations of MemuFiles (no heavy imports — read at start-up).

``%APPDATA%\\MemuAI\\memufiles`` (or ``$MEMUAI_DATA_DIR/memufiles``):
settings.json, session.json, instance.json, tags.json, window.json, thumbs/.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()


def data_root() -> Path:
    env = os.environ.get("MEMUAI_DATA_DIR") or os.environ.get("OMNIHUB_DATA_DIR")
    if env:
        return Path(env)
    return Path(os.environ.get("APPDATA") or os.path.expanduser("~")) / "MemuAI"


def files_dir() -> Path:
    path = data_root() / "memufiles"
    path.mkdir(parents=True, exist_ok=True)
    return path


def thumbs_dir() -> Path:
    path = files_dir() / "thumbs"
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
    import tempfile                          # Round 39: not on the start-up / hand-off path
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
