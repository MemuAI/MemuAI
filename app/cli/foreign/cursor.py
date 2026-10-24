"""Cursor — best effort: nothing here was checked against a real Cursor profile (none is on the machine this was
written on), only against the documented shape of its stores, so a mismatch shows up as "no sessions found" and
never as damage. Two stores are read:

* **Cursor Agent** (the terminal ``cursor-agent``): ``~/.cursor/chats/<md5 of the folder>/<session-id>/store.db`` —
  SQLite with ``meta`` (a hex-encoded JSON: ``agentId``, ``name``, ``createdAt``, ``lastUsedModel``) and ``blobs``
  (``id``, ``data``): the messages are the blobs that parse as JSON with a ``role``;
* **the Cursor editor**: ``<user data>/Cursor/User/globalStorage/state.vscdb``, table ``cursorDiskKV``:
  ``composerData:<id>`` (``name``, ``createdAt``, ``lastUpdatedAt``, ``fullConversationHeadersOnly`` or
  ``conversation``) and ``bubbleId:<id>:<bubble>`` (``type`` 1 = person, 2 = agent, ``text``, ``toolFormerData``).

Both are opened read-only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List

from .model import (ForeignSession, ReadError, SessionInfo, Turn, clean_user_text, clip, iso_to_ts, short_args,
                    text_of, title_from)

KEY = "cursor"
TITLE = "Cursor"


def cli_home() -> Path:
    return Path(os.environ.get("CURSOR_HOME") or (Path.home() / ".cursor"))


def editor_database() -> Path:
    override = os.environ.get("CURSOR_USER_DATA")
    if override:
        return Path(override) / "User" / "globalStorage" / "state.vscdb"
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / "Cursor" / "User" / "globalStorage" / "state.vscdb"


def present() -> bool:
    return (cli_home() / "chats").is_dir() or editor_database().is_file()


def folder_hash(folder: str) -> str:
    """The name Cursor Agent gives the folder of a project's chats."""
    return hashlib.md5(str(folder).encode("utf-8")).hexdigest()


def _connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)


def _query(text: str) -> str:
    """The person's words from a Cursor Agent message, which wraps them as ``<user_query>…</user_query>``."""
    found = re.search(r"<user_query>\s*(.*?)\s*</user_query>", text, re.S)
    return found.group(1) if found else text


def discover(limit: int = 400) -> List[SessionInfo]:
    rows = _discover_cli() + _discover_editor()
    rows.sort(key=lambda row: -row.updated)
    return rows[:limit]


# ---------------------------------------------------------------- Cursor Agent

def _meta(con: sqlite3.Connection) -> Dict[str, Any]:
    try:
        for (value,) in con.execute("select value from meta"):
            raw = value.decode("utf-8", "ignore") if isinstance(value, bytes) else str(value)
            try:
                data = json.loads(bytes.fromhex(raw).decode("utf-8"))
            except ValueError:
                try:
                    data = json.loads(raw)
                except ValueError:
                    continue
            if isinstance(data, dict):
                return data
    except sqlite3.Error:
        pass
    return {}


def _discover_cli() -> List[SessionInfo]:
    base = cli_home() / "chats"
    rows: List[SessionInfo] = []
    if not base.is_dir():
        return rows
    for store in base.glob("*/*/store.db"):
        try:
            stat = store.stat()
            con = _connect(store)
        except (OSError, sqlite3.Error):
            continue
        try:
            meta = _meta(con)
            messages = _cli_messages(con, limit=6)
        except sqlite3.Error:
            continue
        finally:
            con.close()
        first = next((_query(clean_user_text(text_of(m.get("content")))) for m in messages if m.get("role") == "user"), "")
        if not first and not meta.get("name"):
            continue
        rows.append(SessionInfo(tool=KEY, id=str(meta.get("agentId") or store.parent.name),
                                title=title_from(str(meta.get("name") or first)), cwd="",
                                started=iso_to_ts(meta.get("createdAt")) or stat.st_ctime, updated=stat.st_mtime,
                                model=str(meta.get("lastUsedModel") or ""), path=str(store),
                                extra={"store": "cli", "folder_hash": store.parent.parent.name}))
    return rows


def _cli_messages(con: sqlite3.Connection, limit: int = 0) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for (data,) in con.execute("select data from blobs order by rowid"):
        if not isinstance(data, (bytes, str)):
            continue
        raw = data.decode("utf-8", "ignore") if isinstance(data, bytes) else data
        if not raw.lstrip().startswith("{"):
            continue
        try:
            message = json.loads(raw)
        except ValueError:
            continue
        if isinstance(message, dict) and message.get("role") in ("user", "assistant", "tool"):
            out.append(message)
            if limit and len(out) >= limit:
                break
    return out


# ---------------------------------------------------------------- the editor

def _discover_editor() -> List[SessionInfo]:
    path = editor_database()
    rows: List[SessionInfo] = []
    if not path.is_file():
        return rows
    try:
        con = _connect(path)
    except sqlite3.Error:
        return rows
    try:
        for key, value in con.execute("select key, value from cursorDiskKV where key like 'composerData:%'"):
            data = _json(value)
            headers = data.get("fullConversationHeadersOnly") or data.get("conversation") or []
            if not headers:
                continue
            composer = str(data.get("composerId") or str(key).split(":", 1)[-1])
            title = str(data.get("name") or "")
            if not title:
                title = _first_user_bubble(con, composer, headers)
            rows.append(SessionInfo(tool=KEY, id=composer, title=title_from(title or composer), cwd="",
                                    started=iso_to_ts(data.get("createdAt")), updated=iso_to_ts(data.get("lastUpdatedAt") or data.get("createdAt")),
                                    messages=len(headers), path=str(path), extra={"store": "editor"}))
    except sqlite3.Error:
        pass
    finally:
        con.close()
    return rows


def _json(value: Any) -> Dict[str, Any]:
    try:
        data = json.loads(value.decode("utf-8", "ignore") if isinstance(value, bytes) else value)
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError, AttributeError):
        return {}


def _bubble(con: sqlite3.Connection, composer: str, header: Dict[str, Any]) -> Dict[str, Any]:
    if header.get("text") is not None or header.get("type") is not None and header.get("bubbleId") is None:
        return header                                      # an older format keeps the bubbles inline
    row = con.execute("select value from cursorDiskKV where key = ?", (f"bubbleId:{composer}:{header.get('bubbleId')}",)).fetchone()
    return _json(row[0]) if row else {}


def _first_user_bubble(con: sqlite3.Connection, composer: str, headers: List[Dict[str, Any]]) -> str:
    for header in headers[:6]:
        if not isinstance(header, dict) or header.get("type") != 1:
            continue
        bubble = _bubble(con, composer, header)
        if str(bubble.get("text") or "").strip():
            return str(bubble["text"])
    return ""


# ---------------------------------------------------------------- reading

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
        if info.extra.get("store") == "editor":
            _read_editor(con, session)
        else:
            _read_cli(con, session)
    except sqlite3.Error as exc:
        raise ReadError(str(exc)) from exc
    finally:
        con.close()
    session.info.messages = len(session.turns)
    return session


def _read_cli(con: sqlite3.Connection, session: ForeignSession) -> None:
    calls: Dict[str, Dict[str, str]] = {}
    for message in _cli_messages(con):
        role = message.get("role")
        content = message.get("content")
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        for block in content or []:
            if not isinstance(block, dict):
                continue
            kind = str(block.get("type") or "")
            if kind == "text" and str(block.get("text") or "").strip():
                if role == "user":
                    text = _query(clean_user_text(str(block["text"]))).strip()
                    if text and not text.startswith("<user_info>"):
                        session.turns.append(Turn("user", text))
                elif role == "assistant":
                    session.turns.append(Turn("assistant", str(block["text"]).strip()))
            elif kind in ("tool-call", "tool_use"):
                name = str(block.get("toolName") or block.get("name") or "")
                detail = short_args(name, block.get("args") or block.get("input"))
                calls[str(block.get("toolCallId") or block.get("id") or "")] = {"tool": name, "detail": detail}
                session.turns.append(Turn("assistant", tool=name, detail=detail))
            elif kind in ("tool-result", "tool_result"):
                known = calls.get(str(block.get("toolCallId") or block.get("tool_use_id") or ""), {})
                result = block.get("result") if block.get("result") is not None else block.get("content")
                session.turns.append(Turn("tool", clip(result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)),
                                          tool=known.get("tool", str(block.get("toolName") or "")), detail=known.get("detail", "")))


def _read_editor(con: sqlite3.Connection, session: ForeignSession) -> None:
    row = con.execute("select value from cursorDiskKV where key = ?", (f"composerData:{session.info.id}",)).fetchone()
    data = _json(row[0]) if row else {}
    headers = data.get("fullConversationHeadersOnly") or data.get("conversation") or []
    for header in headers:
        if not isinstance(header, dict):
            continue
        bubble = _bubble(con, session.info.id, header)
        text = str(bubble.get("text") or "").strip()
        kind = bubble.get("type") or header.get("type")
        at = iso_to_ts(bubble.get("createdAt") or 0)
        if kind == 1 and text:
            session.turns.append(Turn("user", clean_user_text(text), at=at))
        elif kind == 2:
            if text:
                session.turns.append(Turn("assistant", text, at=at))
            tool = bubble.get("toolFormerData")
            if isinstance(tool, dict) and tool.get("name"):
                name = str(tool["name"])
                detail = short_args(name, tool.get("params") or tool.get("rawArgs"))
                session.turns.append(Turn("assistant", tool=name, detail=detail, at=at))
                if tool.get("result"):
                    session.turns.append(Turn("tool", clip(str(tool["result"])), tool=name, detail=detail,
                                              ok=str(tool.get("status") or "") != "error", at=at))
