"""Grok CLI: ``~/.grok/sessions/<url-encoded folder>/<session-id>/``.

* ``summary.json`` — ``info.id``, ``info.cwd``, ``created_at``, ``updated_at``, ``num_messages``, the model and
  (sometimes) a title in ``session_summary``;
* ``chat_history.jsonl`` — one object per line: ``system``, ``user`` (``content`` is a list of ``text`` parts),
  ``assistant`` (``content`` text and ``tool_calls``), ``tool_result`` (``tool_call_id``, ``content``), ``reasoning``
  (left out), ``backend_tool_call`` (a search the service ran itself).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

from .model import (HEAD_BYTES, ForeignSession, ReadError, SessionInfo, Turn, clean_user_text, clip, iso_to_ts,
                    read_lines, short_args, text_of, title_from)

KEY = "grok"
TITLE = "Grok"


def home() -> Path:
    return Path(os.environ.get("GROK_HOME") or (Path.home() / ".grok"))


def present() -> bool:
    return (home() / "sessions").is_dir()


def _summary(folder: Path) -> Dict[str, Any]:
    try:
        data = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def discover(limit: int = 400) -> List[SessionInfo]:
    base = home() / "sessions"
    rows: List[SessionInfo] = []
    if not base.is_dir():
        return rows
    for project in base.iterdir():
        if not project.is_dir():
            continue
        for folder in project.iterdir():
            chat = folder / "chat_history.jsonl"
            if not chat.is_file():
                continue
            info = _info(folder, chat, unquote(project.name))
            if info is not None:
                rows.append(info)
    rows.sort(key=lambda row: -row.updated)
    return rows[:limit]


def _info(folder: Path, chat: Path, cwd_from_folder: str) -> Optional[SessionInfo]:
    summary = _summary(folder)
    meta = summary.get("info") if isinstance(summary.get("info"), dict) else {}
    first = model = ""
    for event in read_lines(chat, limit_bytes=HEAD_BYTES):
        if event.get("type") == "user" and not first:
            first = _user_text(event)
        elif event.get("type") == "assistant" and not model:
            model = str(event.get("model_id") or "")
    if not first:
        return None
    try:
        mtime = chat.stat().st_mtime
    except OSError:
        mtime = 0.0
    title = str(summary.get("session_summary") or "").strip() or first
    return SessionInfo(tool=KEY, id=str(meta.get("id") or folder.name), title=title_from(title),
                       cwd=str(meta.get("cwd") or cwd_from_folder), started=iso_to_ts(summary.get("created_at")) or mtime,
                       updated=iso_to_ts(summary.get("last_active_at") or summary.get("updated_at")) or mtime,
                       messages=int(summary.get("num_chat_messages") or summary.get("num_messages") or 0),
                       model=str(summary.get("current_model_id") or model), path=str(folder))


def _user_text(event: Dict[str, Any]) -> str:
    return clean_user_text(text_of(event.get("content")))


def read(info: SessionInfo) -> ForeignSession:
    folder = Path(info.path)
    chat = folder / "chat_history.jsonl"
    if not chat.is_file():
        raise ReadError(f"{chat} is not there any more")
    session = ForeignSession(info=SessionInfo(**{**info.__dict__}))
    calls: Dict[str, Dict[str, str]] = {}
    models: Dict[str, None] = {}
    for event in read_lines(chat):
        kind = event.get("type")
        if kind == "user":
            text = _user_text(event)
            if text:
                session.turns.append(Turn("user", text))
        elif kind == "assistant":
            if event.get("model_id"):
                models.setdefault(str(event["model_id"]), None)
            text = text_of(event.get("content")).strip()
            if text:
                session.turns.append(Turn("assistant", text))
            for call in event.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                name = str(call.get("name") or "")
                detail = short_args(name, call.get("arguments"))
                calls[str(call.get("id") or "")] = {"tool": name, "detail": detail}
                session.turns.append(Turn("assistant", tool=name, detail=detail))
        elif kind == "tool_result":
            known = calls.get(str(event.get("tool_call_id") or ""), {})
            session.turns.append(Turn("tool", clip(text_of(event.get("content")) or str(event.get("content") or "")),
                                      tool=known.get("tool", ""), detail=known.get("detail", "")))
        elif kind == "backend_tool_call":
            action = (event.get("kind") or {}).get("action") if isinstance(event.get("kind"), dict) else None
            query = str((action or {}).get("query") or "") if isinstance(action, dict) else ""
            if query:
                session.turns.append(Turn("assistant", tool="web_search", detail=clip(query, 160)))
    session.models = list(models)
    session.info.messages = len(session.turns)
    return session
