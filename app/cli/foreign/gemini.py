"""Gemini CLI: ``~/.gemini/tmp/<project>/chats/session-<time>-<id>.json`` (older) or ``.jsonl`` (newer).

``<project>`` is a readable name (its ``.project_root`` file holds the folder) or, in older versions, the SHA-256 of
the folder; every session also says ``projectHash``. A ``.json`` file is one object ``{sessionId, projectHash,
startTime, lastUpdated, messages}``. A ``.jsonl`` file starts with that header and then appends records: a message
(``id``, ``timestamp``, ``type``, ``content``), a newer copy of a message with the same ``id``, or ``{"$set": {...}}``
which changes top-level fields (and may carry ``messages``). Message types: ``user``, ``gemini`` (with ``toolCalls``),
``info``, ``error``.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .model import (ForeignSession, ReadError, SessionInfo, Turn, clean_user_text, clip, iso_to_ts, short_args,
                    text_of, title_from)

KEY = "gemini"
TITLE = "Gemini CLI"


def home() -> Path:
    return Path(os.environ.get("GEMINI_HOME") or (Path.home() / ".gemini"))


def present() -> bool:
    return (home() / "tmp").is_dir()


def project_hash(folder: str) -> str:
    return hashlib.sha256(str(folder).encode("utf-8")).hexdigest()


def _load(path: Path) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """(header, messages) of a chat file in either format."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ReadError(str(exc)) from exc
    if path.suffix == ".json":
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ReadError(f"{path.name} is damaged") from exc
        messages = data.get("messages") if isinstance(data, dict) else None
        return (data if isinstance(data, dict) else {}), [m for m in (messages or []) if isinstance(m, dict)]
    header: Dict[str, Any] = {}
    order: Dict[str, Dict[str, Any]] = {}
    for number, raw in enumerate(text.splitlines()):
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        if number == 0 and "sessionId" in data and "type" not in data:
            header = data
        elif "$set" in data and isinstance(data["$set"], dict):
            for key, value in data["$set"].items():
                if key == "messages" and isinstance(value, list):
                    for message in value:
                        if isinstance(message, dict) and message.get("id"):
                            order[str(message["id"])] = message
                else:
                    header[key] = value
        elif "$rewindTo" in data:
            keep = str(data["$rewindTo"])
            ids = list(order)
            if keep in ids:
                for later in ids[ids.index(keep) + 1:]:
                    order.pop(later, None)
        elif data.get("id") and data.get("type"):
            order[str(data["id"])] = data
    return header, list(order.values())


def discover(limit: int = 400) -> List[SessionInfo]:
    base = home() / "tmp"
    rows: List[SessionInfo] = []
    if not base.is_dir():
        return rows
    for project in base.iterdir():
        chats = project / "chats"
        if not chats.is_dir():
            continue
        root = ""
        try:
            root = (project / ".project_root").read_text(encoding="utf-8").strip()
        except OSError:
            pass
        for file in list(chats.glob("session-*.json")) + list(chats.glob("session-*.jsonl")):
            info = _info(file, root)
            if info is not None:
                rows.append(info)
    rows.sort(key=lambda row: -row.updated)
    return rows[:limit]


def _info(file: Path, root: str) -> Optional[SessionInfo]:
    try:
        stat = file.stat()
        header, messages = _load(file)
    except (OSError, ReadError):
        return None
    first = ""
    model = ""
    for message in messages:
        if message.get("type") == "user" and not first:
            first = clean_user_text(text_of(message.get("content")))
        if message.get("type") == "gemini" and not model:
            model = str(message.get("model") or "")
    if not first:
        return None
    return SessionInfo(tool=KEY, id=str(header.get("sessionId") or file.stem), title=title_from(first), cwd=root,
                       started=iso_to_ts(header.get("startTime")) or stat.st_ctime,
                       updated=iso_to_ts(header.get("lastUpdated")) or stat.st_mtime, messages=len(messages),
                       model=model, path=str(file), extra={"project_hash": str(header.get("projectHash") or "")})


def read(info: SessionInfo) -> ForeignSession:
    path = Path(info.path)
    if not path.is_file():
        raise ReadError(f"{path} is not there any more")
    _header, messages = _load(path)
    session = ForeignSession(info=SessionInfo(**{**info.__dict__}))
    models: Dict[str, None] = {}
    for message in messages:
        kind = message.get("type")
        at = iso_to_ts(message.get("timestamp"))
        if kind == "user":
            text = clean_user_text(text_of(message.get("content")))
            if text:
                session.turns.append(Turn("user", text, at=at))
        elif kind == "gemini":
            if message.get("model"):
                models.setdefault(str(message["model"]), None)
            text = text_of(message.get("content")).strip()
            if text:
                session.turns.append(Turn("assistant", text, at=at))
            for call in message.get("toolCalls") or []:
                if not isinstance(call, dict):
                    continue
                name = str(call.get("name") or "")
                detail = short_args(name, call.get("args"))
                session.turns.append(Turn("assistant", tool=name, detail=detail, at=at))
                result = _call_result(call)
                if result:
                    session.turns.append(Turn("tool", clip(result), tool=name, detail=detail,
                                              ok=str(call.get("status") or "success") not in ("error", "cancelled"), at=at))
        elif kind == "error":
            text = text_of(message.get("content")).strip()
            if text:
                session.turns.append(Turn("note", text, ok=False, at=at))
    session.models = list(models)
    session.info.messages = len(session.turns)
    return session


def _call_result(call: Dict[str, Any]) -> str:
    """The text a tool call returned (Gemini keeps it as ``functionResponse`` parts, or as ``resultDisplay``)."""
    parts = []
    for item in call.get("result") or []:
        response = (item.get("functionResponse") or {}).get("response") if isinstance(item, dict) else None
        if isinstance(response, dict):
            parts.append(str(response.get("output") or response.get("error") or json.dumps(response, ensure_ascii=False)))
    if parts:
        return "\n".join(parts)
    display = call.get("resultDisplay")
    return display if isinstance(display, str) else ""
