"""Antigravity CLI (``agy``): ``~/.gemini/antigravity-cli/conversations/<id>.db``.

Each conversation is a SQLite database; its ``steps`` table holds one protobuf message per step in
``step_payload`` (see ``protobuf.py`` — there is no schema, so fields were identified by reading real
conversations). What is used:

==========  ==============================  ====================================================
step_type   meaning                         fields
==========  ==============================  ====================================================
14          the person's message            19.2 text
15          the agent's turn                20.1 text · 20.7.2 tool name · 20.7.3 arguments (JSON)
8           a file was viewed               14.1 path · 14.4 content
21          a command was run               28.23 command · 28.21.1 output
17          an error                        24.3.3 message
==========  ==============================  ====================================================

Everything else (browser steps, checkpoints, bookkeeping) is skipped. ``cache/conversation_metadata.json`` gives
a summary and the time of the last change, ``cache/last_conversations.json`` / ``projects.json`` which folder a
conversation belongs to. The older ``.pb`` files are not readable and are not listed. Databases are opened
read-only, so an ``agy`` that is running at the same time is not disturbed.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List

from . import protobuf
from .model import (ForeignSession, ReadError, SessionInfo, Turn, clean_user_text, clip, iso_to_ts, short_args,
                    title_from)

KEY = "agy"
TITLE = "Antigravity (agy)"

USER, AGENT, VIEW, COMMAND, ERROR = 14, 15, 8, 21, 17

def home() -> Path:
    return Path(os.environ.get("AGY_HOME") or (Path.home() / ".gemini" / "antigravity-cli"))

def present() -> bool:
    return (home() / "conversations").is_dir()

def _cache(name: str) -> Dict[str, Any]:
    try:
        data = json.loads((home() / "cache" / name).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}

def _connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)

def _folders() -> Dict[str, str]:
    """``{conversation id: folder}`` from the two caches ``agy`` keeps (the last conversation of each folder)."""
    found: Dict[str, str] = {}
    for name in ("projects.json", "last_conversations.json"):
        for folder, conversation in _cache(name).items():
            if isinstance(conversation, str):
                found.setdefault(conversation, str(folder))
    return found

def _summary_text(row: Any) -> str:
    if not isinstance(row, dict):
        return ""
    summary = row.get("summary")
    if isinstance(summary, str):
        return summary
    if isinstance(summary, dict):
        for key in ("title", "summary", "name", "description"):
            if summary.get(key):
                return str(summary[key])
        for value in summary.values():
            if isinstance(value, str) and value:
                return value
    return ""

def discover(limit: int = 400) -> List[SessionInfo]:
    base = home() / "conversations"
    rows: List[SessionInfo] = []
    if not base.is_dir():
        return rows
    meta = _cache("conversation_metadata.json").get("conversations")
    meta = meta if isinstance(meta, dict) else {}
    folders = _folders()
    for file in base.glob("*.db"):
        try:
            stat = file.stat()
        except OSError:
            continue
        conversation = file.stem
        row = meta.get(conversation) or {}
        if row.get("is_internal"):
            continue
        title, count, first_text, cwd = _quick(file)
        if not count or not (first_text or title):
            continue
        rows.append(SessionInfo(
            tool=KEY, id=conversation, title=title_from(_summary_text(row) or first_text or title),
            cwd=folders.get(conversation) or cwd, started=stat.st_ctime,
            updated=iso_to_ts(row.get("last_modified_time")) or stat.st_mtime, messages=count, path=str(file)))
    rows.sort(key=lambda item: -item.updated)
    return rows[:limit]

def _quick(file: Path) -> "tuple[str, int, str, str]":
    """(title, number of steps, the first thing the person said, a folder seen in a tool call)."""
    try:
        con = _connect(file)
    except sqlite3.Error:
        return "", 0, "", ""
    first = cwd = ""
    try:
        count = con.execute("select count(*) from steps").fetchone()[0]
        for step_type, payload in con.execute("select step_type, step_payload from steps where step_type in (14, 15) "
                                              "order by idx limit 8"):
            tree = protobuf.decode(payload) if payload else None
            if not tree:
                continue
            if step_type == USER and not first:
                first = clean_user_text(protobuf.first(tree, "19.2", "19.3.1"))
            if step_type == AGENT and not cwd:
                args = _args(protobuf.first(tree, "20.7.3"))
                for key in ("DirectoryPath", "SearchPath", "Cwd", "cwd"):
                    if isinstance(args.get(key), str) and args[key]:
                        cwd = args[key]
                        break
    except sqlite3.Error:
        count = 0
    finally:
        con.close()
    return "", int(count or 0), first, cwd

def _args(text: str) -> Dict[str, Any]:
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}

def read(info: SessionInfo) -> ForeignSession:
    path = Path(info.path)
    if not path.is_file():
        raise ReadError(f"{path} is not there any more")
    session = ForeignSession(info=SessionInfo(**{**info.__dict__}))
    try:
        con = _connect(path)
    except sqlite3.Error as exc:
        raise ReadError(str(exc)) from exc
    try:
        rows = con.execute("select idx, step_type, step_payload from steps order by idx").fetchall()
    except sqlite3.Error as exc:
        raise ReadError(str(exc)) from exc
    finally:
        con.close()
    for _index, step_type, payload in rows:
        tree = protobuf.decode(payload) if payload else None
        if not tree:
            continue
        if step_type == USER:
            text = clean_user_text(protobuf.first(tree, "19.2", "19.3.1"))
            if text:
                session.turns.append(Turn("user", text))
        elif step_type == AGENT:
            text = protobuf.first(tree, "20.1", "20.8").strip()
            if text:
                session.turns.append(Turn("assistant", text))
            name = protobuf.first(tree, "20.7.2", "20.7.9")
            if name:
                args = protobuf.first(tree, "20.7.3")
                session.turns.append(Turn("assistant", tool=name, detail=short_args(name, _args(args) or args)))
        elif step_type == VIEW:
            where = protobuf.first(tree, "14.1")
            content = protobuf.first(tree, "14.4")
            if where:
                session.turns.append(Turn("tool", clip(content), tool="view_file", detail=where))
        elif step_type == COMMAND:
            command = protobuf.first(tree, "28.23", "28.25")
            output = protobuf.first(tree, "28.21.1") or protobuf.longest(tree, "28", skip=[command])
            if command:
                session.turns.append(Turn("tool", clip(output if output != command else ""), tool="run_command",
                                          detail=clip(command, 200)))
        elif step_type == ERROR:
            message = protobuf.first(tree, "24.3.3", "24.3.2")
            if message:
                session.turns.append(Turn("note", clip(message, 600), ok=False))
    session.info.messages = len(session.turns)
    return session
