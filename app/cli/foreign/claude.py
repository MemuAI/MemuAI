"""Claude Code: ``~/.claude/projects/<folder-per-project>/<session-id>.jsonl``.

One JSON object per line. ``user`` and ``assistant`` lines carry the conversation (``message.content`` is a string
or a list of blocks: ``text``, ``tool_use``, ``tool_result``, ``thinking``); ``summary`` lines hold the titles Claude
wrote, a ``user`` line with ``isCompactSummary`` holds the summary of a compaction. Lines of a sub-agent have
``isSidechain`` set and are left out: what the sub-agent did is in what the main agent said about it.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .model import (HEAD_BYTES, ForeignSession, ReadError, SessionInfo, Turn, clean_user_text, clip, iso_to_ts,
                    read_lines, short_args, tail_lines, text_of, title_from)

KEY = "claude"
TITLE = "Claude Code"

def home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))

def present() -> bool:
    return (home() / "projects").is_dir()

def _user_text(event: Dict[str, Any]) -> str:
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    content = message.get("content")
    if isinstance(content, list) and any(isinstance(block, dict) and block.get("type") == "tool_result" for block in content):
        return ""
    return clean_user_text(text_of(content))

def discover(limit: int = 400) -> List[SessionInfo]:
    base = home() / "projects"
    rows: List[SessionInfo] = []
    if not base.is_dir():
        return rows
    for folder in base.iterdir():
        if not folder.is_dir():
            continue
        for file in folder.glob("*.jsonl"):
            if file.name.startswith("agent-"):
                continue
            info = _info(file)
            if info is not None:
                rows.append(info)
    rows.sort(key=lambda row: -row.updated)
    return rows[:limit]

def _info(file: Path) -> Optional[SessionInfo]:
    try:
        stat = file.stat()
    except OSError:
        return None
    title = cwd = model = first = ""
    started = 0.0
    events = 0
    read = 0
    for event in read_lines(file, limit_bytes=HEAD_BYTES):
        read += 1
        kind = event.get("type")
        if kind == "summary" and event.get("summary"):
            title = str(event["summary"])
        if event.get("isSidechain"):
            continue
        cwd = cwd or str(event.get("cwd") or "")
        if kind in ("user", "assistant"):
            events += 1
            started = started or iso_to_ts(event.get("timestamp"))
            if kind == "user" and not first and not event.get("isMeta") and not event.get("isCompactSummary"):
                candidate = _user_text(event)
                if candidate and not (candidate.startswith("/") and " " not in candidate):
                    first = candidate
            if kind == "assistant" and not model:
                model = str((event.get("message") or {}).get("model") or "")
    if not first and not title:
        return None
    last = 0.0
    for event in tail_lines(file):
        if event.get("type") == "summary" and event.get("summary") and not title:
            title = str(event["summary"])
        last = iso_to_ts(event.get("timestamp")) or last
    updated = last or stat.st_mtime
    estimate = events
    if stat.st_size > HEAD_BYTES and events:
        estimate = int(events * stat.st_size / HEAD_BYTES)
    return SessionInfo(tool=KEY, id=file.stem, title=title_from(title or first), cwd=cwd, started=started or stat.st_ctime,
                       updated=updated, messages=estimate, model=model, path=str(file))

def read(info: SessionInfo) -> ForeignSession:
    path = Path(info.path)
    if not path.is_file():
        raise ReadError(f"{path} is not there any more")
    session = ForeignSession(info=SessionInfo(**{**info.__dict__}))
    calls: Dict[str, Dict[str, str]] = {}
    models: Dict[str, None] = {}
    title = ""
    for event in read_lines(path):
        kind = event.get("type")
        if kind == "summary" and event.get("summary"):
            title = str(event["summary"])
            continue
        if event.get("isSidechain") or kind not in ("user", "assistant"):
            continue
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        content = message.get("content")
        at = iso_to_ts(event.get("timestamp"))
        session.info.cwd = session.info.cwd or str(event.get("cwd") or "")
        if kind == "user":
            if event.get("isCompactSummary"):
                session.summary = clip(text_of(content), 6000)
                continue
            if event.get("isMeta"):
                continue
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_result":
                        known = calls.get(str(block.get("tool_use_id") or ""), {})
                        result = block.get("content")
                        session.turns.append(Turn("tool", clip(text_of(result) if not isinstance(result, str) else result),
                                                  tool=known.get("tool", ""), detail=known.get("detail", ""),
                                                  ok=not block.get("is_error"), at=at))
                text = clean_user_text(text_of([b for b in content if not (isinstance(b, dict) and b.get("type") == "tool_result")]))
            else:
                text = clean_user_text(text_of(content))
            if text:
                session.turns.append(Turn("user", text, at=at))
            continue
        model = str(message.get("model") or "")
        if model and not model.startswith("<"):
            models.setdefault(model, None)
        blocks = content if isinstance(content, list) else [{"type": "text", "text": text_of(content)}]
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and str(block.get("text") or "").strip():
                session.turns.append(Turn("assistant", str(block["text"]).strip(), at=at))
            elif block.get("type") == "tool_use":
                name = str(block.get("name") or "")
                args = block.get("input") if isinstance(block.get("input"), dict) else {}
                detail = short_args(name, args)
                calls[str(block.get("id") or "")] = {"tool": name, "detail": detail}
                if name == "TodoWrite" and isinstance(args.get("todos"), list):
                    session.todos = [f"[{todo.get('status', '')}] {todo.get('content', '')}" for todo in args["todos"]
                                     if isinstance(todo, dict)]
                session.turns.append(Turn("assistant", tool=name, detail=detail, at=at))
    session.models = list(models)
    if title and not session.info.title:
        session.info.title = title_from(title)
    session.info.messages = len(session.turns)
    return session
