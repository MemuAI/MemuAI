"""Codex CLI: ``~/.codex/sessions/YYYY/MM/DD/rollout-<time>-<id>.jsonl``.

Every line is ``{timestamp, type, payload}``. Three generations of the format exist side by side and all are read:

* newest: ``event_msg`` / ``item_completed`` with an ``item`` of type ``UserMessage``, ``AgentMessage``,
  ``CommandExecution``, ``FileChange``, ``McpToolCall``, ``WebSearch``, ``Plan``…;
* ``response_item`` lines: ``message`` (roles ``user``, ``assistant``, ``developer``), ``function_call`` /
  ``custom_tool_call`` and their ``…_output``;
* oldest: ``event_msg`` / ``user_message`` and ``agent_message``.

The first line, ``session_meta``, gives the id, the folder and the version; ``turn_context`` the model. Sessions of
sub-agents (``parent_thread_id``) are not listed on their own.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .model import (HEAD_BYTES, ForeignSession, ReadError, SessionInfo, Turn, clean_user_text, clip, iso_to_ts,
                    read_lines, short_args, tail_lines, text_of, title_from)

KEY = "codex"
TITLE = "Codex"

_NOT_THE_PERSON = ("<environment_context", "<user_instructions", "<recommended_plugins", "# AGENTS.md",
                   "<permissions", "<collaboration_mode", "<skills_instructions", "<user_shell_command")

def home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))

def present() -> bool:
    return (home() / "sessions").is_dir()

def _person(text: str) -> str:
    text = clean_user_text(text)
    if not text or text.lstrip().startswith(_NOT_THE_PERSON):
        return ""
    if text.lstrip().startswith("# Files mentioned by the user"):
        heading = re.search(r"## My request[^\n]*\n", text)
        if heading:
            text = text[heading.end():]
    return text.strip()

def discover(limit: int = 400) -> List[SessionInfo]:
    base = home() / "sessions"
    rows: List[SessionInfo] = []
    if not base.is_dir():
        return rows
    for file in base.rglob("rollout-*.jsonl"):
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
    session_id = file.stem.rsplit("-", 5)[-5:]
    session_id = "-".join(session_id) if len(session_id) == 5 else file.stem
    cwd = model = first = ""
    started = 0.0
    events = 0
    for event in read_lines(file, limit_bytes=HEAD_BYTES):
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        kind = event.get("type")
        if kind == "session_meta":
            if payload.get("parent_thread_id"):
                return None
            session_id = str(payload.get("id") or session_id)
            cwd = cwd or str(payload.get("cwd") or "")
            started = started or iso_to_ts(payload.get("timestamp") or event.get("timestamp"))
        elif kind == "turn_context":
            cwd = cwd or str(payload.get("cwd") or "")
            model = model or str(payload.get("model") or "")
        elif kind == "event_msg" and payload.get("type") == "item_completed":
            item = payload.get("item") or {}
            events += 1
            if item.get("type") == "UserMessage" and not first:
                first = _person(text_of(item.get("content")))
        elif kind == "response_item" and payload.get("type") == "message":
            events += 1
            if payload.get("role") == "user" and not first:
                first = _person(text_of(payload.get("content")))
        elif kind == "event_msg" and payload.get("type") == "user_message" and not first:
            first = _person(str(payload.get("message") or ""))
    if not first:
        return None
    last = 0.0
    for event in tail_lines(file):
        last = iso_to_ts(event.get("timestamp")) or last
    estimate = events if stat.st_size <= HEAD_BYTES else int(events * stat.st_size / HEAD_BYTES)
    return SessionInfo(tool=KEY, id=session_id, title=title_from(first), cwd=cwd, started=started or stat.st_ctime,
                       updated=last or stat.st_mtime, messages=estimate, model=model, path=str(file))

def _command(item: Dict[str, Any]) -> str:
    command = item.get("command")
    if isinstance(command, list):
        words = [str(word) for word in command]
        if len(words) >= 3 and words[-2] in ("-c", "-Command", "/c"):
            return " ".join(words[-1].split())
        return " ".join(words)
    return " ".join(str(command or "").split())

def read(info: SessionInfo) -> ForeignSession:
    path = Path(info.path)
    if not path.is_file():
        raise ReadError(f"{path} is not there any more")
    session = ForeignSession(info=SessionInfo(**{**info.__dict__}))
    items: List[Turn] = []
    responses: List[Turn] = []
    legacy: List[Turn] = []
    calls: Dict[str, Dict[str, str]] = {}
    models: Dict[str, None] = {}
    for event in read_lines(path):
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        kind = event.get("type")
        at = iso_to_ts(event.get("timestamp"))
        if kind == "turn_context" and payload.get("model"):
            models.setdefault(str(payload["model"]), None)
            session.info.cwd = session.info.cwd or str(payload.get("cwd") or "")
        elif kind == "compacted":
            text = text_of(payload.get("message"))
            if text:
                session.summary = clip(text, 6000)
        elif kind == "event_msg":
            sub = payload.get("type")
            if sub == "item_completed":
                turn = _item(payload.get("item") or {}, at)
                if turn is not None:
                    items.extend(turn)
            elif sub == "user_message":
                text = _person(str(payload.get("message") or ""))
                if text:
                    legacy.append(Turn("user", text, at=at))
            elif sub == "agent_message" and str(payload.get("message") or "").strip():
                legacy.append(Turn("assistant", str(payload["message"]).strip(), at=at))
        elif kind == "response_item":
            sub = payload.get("type")
            if sub == "message":
                role = payload.get("role")
                text = text_of(payload.get("content"))
                if role == "user":
                    text = _person(text)
                    if text:
                        responses.append(Turn("user", text, at=at))
                elif role == "assistant" and text.strip():
                    responses.append(Turn("assistant", text.strip(), at=at))
            elif sub in ("function_call", "custom_tool_call", "local_shell_call"):
                name = str(payload.get("name") or sub)
                args = payload.get("arguments") if payload.get("arguments") is not None else payload.get("input")
                detail = short_args(name, args if args is not None else payload.get("action"))
                calls[str(payload.get("call_id") or "")] = {"tool": name, "detail": detail}
                responses.append(Turn("assistant", tool=name, detail=detail, at=at))
            elif sub in ("function_call_output", "custom_tool_call_output"):
                known = calls.get(str(payload.get("call_id") or ""), {})
                output = payload.get("output")
                text = text_of(output) if not isinstance(output, str) else output
                responses.append(Turn("tool", clip(text), tool=known.get("tool", ""), detail=known.get("detail", ""), at=at))

    def weight(turns: List[Turn]) -> int:
        return sum(1 for turn in turns if turn.role != "user")

    chosen = max((items, responses, legacy), key=lambda turns: (weight(turns), len(turns)))
    session.turns = _merge(chosen)
    session.models = list(models)
    session.info.messages = len(session.turns)
    return session

def _merge(turns: List[Turn]) -> List[Turn]:
    """Drop immediate duplicates (a user message appears as an item and as an event in some versions)."""
    out: List[Turn] = []
    for turn in turns:
        if out and out[-1].role == turn.role and out[-1].text == turn.text and out[-1].tool == turn.tool \
                and out[-1].detail == turn.detail:
            continue
        out.append(turn)
    return out

def _item(item: Dict[str, Any], at: float) -> Optional[List[Turn]]:
    kind = str(item.get("type") or "")
    if kind == "UserMessage":
        text = _person(text_of(item.get("content")))
        return [Turn("user", text, at=at)] if text else None
    if kind == "AgentMessage":
        text = text_of(item.get("content")).strip()
        return [Turn("assistant", text, at=at)] if text else None
    if kind == "Plan":
        text = str(item.get("text") or "").strip()
        return [Turn("assistant", "Plan:\n" + text, at=at)] if text else None
    if kind == "CommandExecution":
        command = _command(item)
        output = str(item.get("aggregated_output") or item.get("stdout") or "")
        failed = item.get("exit_code") not in (0, None)
        return [Turn("assistant", tool="shell", detail=clip(command, 200), at=at),
                Turn("tool", clip(output), tool="shell", detail=clip(command, 200), ok=not failed, at=at)]
    if kind == "FileChange":
        changes = item.get("changes") if isinstance(item.get("changes"), dict) else {}
        return [Turn("assistant", tool="edit", detail=name, at=at) for name in list(changes)[:12]] or None
    if kind == "McpToolCall":
        name = f"{item.get('server', '')}.{item.get('tool', '')}".strip(".")
        detail = short_args(name, item.get("arguments"))
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        text = text_of(result.get("content"))
        return [Turn("assistant", tool=name, detail=detail, at=at),
                Turn("tool", clip(text), tool=name, detail=detail, ok=not result.get("isError"), at=at)]
    if kind in ("WebSearch", "Extension") and (item.get("query") or kind == "WebSearch"):
        return [Turn("assistant", tool="web_search", detail=clip(str(item.get("query") or ""), 160), at=at)]
    return None
