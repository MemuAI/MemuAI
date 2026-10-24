"""Sessions of the other coding tools on this computer, read-only.

``SOURCES`` lists what MemuCode can read — Claude Code, Codex, Grok, Gemini CLI, Antigravity (agy), OpenCode,
Cursor — and ``discover`` / ``read`` give one common view of them: a list of :class:`SessionInfo` and, for one,
a :class:`ForeignSession` of plain turns. ``handoff`` turns a session into the note that lets MemuCode carry on
where the other tool stopped; ``/import`` copies sessions (and settings) into MemuCode's own store.

Nothing here writes to, moves or locks another tool's files.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from . import agy, claude, codex, cursor, gemini, grok, opencode
from .model import ForeignSession, ReadError, SessionInfo, Turn


@dataclass(frozen=True)
class Source:
    key: str
    title: str
    module: Any
    aliases: tuple = ()
    verified: bool = True            # False: written from the documented format, not checked against real data

    @property
    def command(self) -> str:
        return "resume-" + self.key

    def present(self) -> bool:
        try:
            return bool(self.module.present())
        except OSError:
            return False

    def where(self) -> str:
        """A folder to tell the person we looked in."""
        module = self.module
        for name in ("home", "data_home", "cli_home"):
            if hasattr(module, name):
                return str(getattr(module, name)())
        return ""


SOURCES: Dict[str, Source] = {source.key: source for source in (
    Source("claude", claude.TITLE, claude, ("claude-code", "claudecode")),
    Source("codex", codex.TITLE, codex, ("codex-cli",)),
    Source("grok", grok.TITLE, grok, ("grok-cli",)),
    Source("gemini", gemini.TITLE, gemini, ("gemini-cli",)),
    Source("agy", agy.TITLE, agy, ("antigravity", "antigravity-cli")),
    Source("opencode", opencode.TITLE, opencode, ("open-code",)),
    Source("cursor", cursor.TITLE, cursor, ("cursor-agent", "cursor-cli"), verified=False),
)}


def source_for(name: str) -> Optional[Source]:
    name = str(name or "").strip().lower()
    if name in SOURCES:
        return SOURCES[name]
    return next((source for source in SOURCES.values() if name in source.aliases), None)


def present_sources() -> List[Source]:
    return [source for source in SOURCES.values() if source.present()]


def discover(key: str, limit: int = 400) -> List[SessionInfo]:
    source = SOURCES[key]
    if not source.present():
        return []
    try:
        return source.module.discover(limit)
    except (OSError, ReadError):
        return []


def discover_all(limit: int = 400) -> List[SessionInfo]:
    rows: List[SessionInfo] = []
    for source in present_sources():
        rows.extend(discover(source.key, limit))
    rows.sort(key=lambda row: -row.updated)
    return rows[:limit]


def read(info: SessionInfo) -> ForeignSession:
    source = SOURCES.get(info.tool)
    if source is None:
        raise ReadError(f"unknown source {info.tool}")
    return source.module.read(info)


def same_folder(first: str, second: str) -> bool:
    """Do two paths name the same folder (case and separators do not matter on Windows)?"""
    if not first or not second:
        return False
    return os.path.normcase(os.path.normpath(first)).rstrip("\\/") == os.path.normcase(os.path.normpath(second)).rstrip("\\/")


def find(rows: List[SessionInfo], query: str) -> List[SessionInfo]:
    """Sessions whose id starts with ``query`` — or, failing that, whose title or folder has all its words."""
    query = str(query or "").strip()
    if not query:
        return rows
    low = query.lower()
    by_id = [row for row in rows if row.id.lower() == low] or [row for row in rows if row.id.lower().startswith(low)]
    if by_id:
        return by_id
    words = low.split()
    return [row for row in rows if all(word in f"{row.title} {row.cwd} {row.model}".lower() for word in words)]


__all__ = ["SOURCES", "Source", "SessionInfo", "ForeignSession", "Turn", "ReadError", "source_for", "present_sources",
           "discover", "discover_all", "read", "same_folder", "find"]
