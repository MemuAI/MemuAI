"""OpenCode: ``~/.local/share/opencode/opencode.db`` (SQLite) — or, in older versions, the JSON files under
``storage/``.

The database has ``session`` (``id``, ``title``, ``directory``, ``time_created``/``time_updated`` in milliseconds,
``model`` JSON), ``message`` (``session_id``, ``data`` JSON with ``role``) and ``part`` (``message_id``, ``data``
JSON: ``text``, ``tool`` with a ``state`` holding the input and output, ``reasoning``, ``file``…). The database
is opened read-only.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List

from .model import (ForeignSession, ReadError, SessionInfo, Turn, clean_user_text, clip, iso_to_ts, short_args,
                    title_from)

KEY = "opencode"
TITLE = "OpenCode"

def data_home() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    return Path(base) / "opencode" if base else Path.home() / ".local" / "share" / "opencode"

def database() -> Path:
    return data_home() / "opencode.db"

def present() -> bool:
    return database().is_file() or (data_home() / "storage" / "session").is_dir()

def _connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)

def _json(value: Any) -> Dict[str, Any]:
    try:
        data = json.loads(value) if isinstance(value, (str, bytes)) else value
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}

def discover(limit: int = 400) -> List[SessionInfo]:
    rows: List[SessionInfo] = []
    path = database()
    if path.is_file():
        try:
            con = _connect(path)
            try:
                for row in con.execute("select id, title, directory, time_created, time_updated, model, parent_id from session "
                                       "order by time_updated desc limit ?", (limit,)):
                    session_id, title, directory, created, updated, model, parent = row
                    if parent:
                        continue
                    count = con.execute("select count(*) from message where session_id = ?", (session_id,)).fetchone()[0]
                    if not count:
                        continue
                    rows.append(SessionInfo(tool=KEY, id=str(session_id), title=title_from(str(title or "")),
                                            cwd=str(directory or ""), started=iso_to_ts(created), updated=iso_to_ts(updated),
                                            messages=int(count), model=str(_json(model).get("id") or ""), path=str(path)))
            finally:
                con.close()
        except sqlite3.Error:
            pass
    rows.extend(_legacy_discover(limit))
    rows.sort(key=lambda row: -row.updated)
    return rows[:limit]

def _legacy_discover(limit: int) -> List[SessionInfo]:
    base = data_home() / "storage" / "session"
    rows: List[SessionInfo] = []
    if not base.is_dir():
        return rows
    for file in base.rglob("*.json"):
        data = _json(file.read_text(encoding="utf-8", errors="replace")) if file.is_file() else {}
        if not data.get("id") or data.get("parentID"):
            continue
        times = data.get("time") if isinstance(data.get("time"), dict) else {}
        rows.append(SessionInfo(tool=KEY, id=str(data["id"]), title=title_from(str(data.get("title") or "")),
                                cwd=str(data.get("directory") or ""), started=iso_to_ts(times.get("created")),
                                updated=iso_to_ts(times.get("updated")), path=str(file), extra={"legacy": True}))
    return rows[:limit]

def read(info: SessionInfo) -> ForeignSession:
    session = ForeignSession(info=SessionInfo(**{**info.__dict__}))
    if info.extra.get("legacy"):
        return _legacy_read(session)
    path = Path(info.path)
    if not path.is_file():
        raise ReadError(f"{path} is not there any more")
    try:
        con = _connect(path)
    except sqlite3.Error as exc:
        raise ReadError(str(exc)) from exc
    models: Dict[str, None] = {}
    try:
        messages = con.execute("select id, data, time_created from message where session_id = ? order by time_created, id",
                               (info.id,)).fetchall()
        parts: Dict[str, List[Dict[str, Any]]] = {}
        for message_id, data, _created in con.execute(
                "select message_id, data, time_created from part where session_id = ? order by time_created, id", (info.id,)):
            parts.setdefault(str(message_id), []).append(_json(data))
        for message_id, data, created in messages:
            meta = _json(data)
            role = str(meta.get("role") or "")
            model = str(meta.get("modelID") or (meta.get("model") or {}).get("modelID") or "") if isinstance(meta.get("model") or {}, dict) else ""
            if model:
                models.setdefault(model, None)
            _turns(session, role, parts.get(str(message_id), []), iso_to_ts(created))
    except sqlite3.Error as exc:
        raise ReadError(str(exc)) from exc
    finally:
        con.close()
    session.models = list(models)
    session.info.messages = len(session.turns)
    return session

def _turns(session: ForeignSession, role: str, parts: List[Dict[str, Any]], at: float) -> None:
    for part in parts:
        kind = str(part.get("type") or "")
        if kind == "text" and str(part.get("text") or "").strip() and not part.get("synthetic"):
            text = str(part["text"]).strip()
            if role == "user":
                text = clean_user_text(text)
            if text:
                session.turns.append(Turn("user" if role == "user" else "assistant", text, at=at))
        elif kind == "tool":
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            name = str(part.get("tool") or "")
            detail = short_args(name, state.get("input"))
            session.turns.append(Turn("assistant", tool=name, detail=detail, at=at))
            output = state.get("output") if state.get("output") is not None else state.get("error")
            if output:
                session.turns.append(Turn("tool", clip(str(output)), tool=name, detail=detail,
                                          ok=str(state.get("status") or "") != "error", at=at))
        elif kind == "file" and part.get("filename"):
            session.turns.append(Turn("note", f"[attached: {part['filename']}]", at=at))

def _legacy_read(session: ForeignSession) -> ForeignSession:
    base = data_home() / "storage"
    folder = base / "message" / session.info.id
    if not folder.is_dir():
        raise ReadError("the messages of this session are not there any more")
    entries = []
    for file in folder.glob("*.json"):
        meta = _json(file.read_text(encoding="utf-8", errors="replace"))
        entries.append((iso_to_ts((meta.get("time") or {}).get("created")), meta))
    for at, meta in sorted(entries, key=lambda item: item[0]):
        parts = []
        part_folder = base / "part" / str(meta.get("id") or "")
        if part_folder.is_dir():
            parts = sorted((_json(file.read_text(encoding="utf-8", errors="replace")) for file in part_folder.glob("*.json")),
                           key=lambda data: iso_to_ts((data.get("time") or {}).get("start")))
        _turns(session, str(meta.get("role") or ""), parts, at)
    session.info.messages = len(session.turns)
    return session
