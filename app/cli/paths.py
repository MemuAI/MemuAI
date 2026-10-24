"""Where MemuCode keeps its files.

Everything personal lives next to the rest of MemuAI in ``%APPDATA%\\MemuAI``
(``MEMUAI_DATA_DIR`` in tests): settings, sessions, prompt history, the
personal MEMUCODE.md, custom commands and agents. Project files live in the
repository under ``.memucode/``.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from ..config import DATA_DIR

PROJECT_DIR = ".memucode"


def home() -> Path:
    override = os.environ.get("MEMUCODE_CONFIG_DIR", "").strip()
    path = Path(override) if override else Path(DATA_DIR) / "memucode"
    path.mkdir(parents=True, exist_ok=True)
    return path


def memu_dir() -> Path:
    return Path(DATA_DIR) / "memuai"


def user_settings_file() -> Path:
    return home() / "settings.json"


def history_file() -> Path:
    return home() / "history.jsonl"


def sessions_root() -> Path:
    path = home() / "sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def project_key(root: Path | str) -> str:
    """Folder name plus a short hash of the full path: readable and unique."""
    resolved = str(Path(root).resolve())
    name = re.sub(r"[^\w.-]+", "-", Path(resolved).name or "root").strip("-.")[:40] or "root"
    digest = hashlib.sha1(os.path.normcase(resolved).encode("utf-8")).hexdigest()[:10]
    return f"{name}-{digest}"


def project_sessions(root: Path | str) -> Path:
    path = sessions_root() / project_key(root)
    path.mkdir(parents=True, exist_ok=True)
    return path


def outputs_dir() -> Path:
    """Round 32: full outputs of commands whose result was shortened for the model ("see more" files)."""
    path = home() / "outputs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_memory_file() -> Path:
    return home() / "MEMUCODE.md"


def user_commands_dir() -> Path:
    return home() / "commands"


def user_agents_dir() -> Path:
    return home() / "agents"


def user_skills_dir() -> Path:
    return home() / "skills"


def output_styles_dir() -> Path:
    return home() / "output-styles"


def downloads_dir() -> Path:
    path = Path.home() / "Downloads" / "MemuAI"
    path.mkdir(parents=True, exist_ok=True)
    return path


def project_dir(root: Path | str) -> Path:
    return Path(root) / PROJECT_DIR
