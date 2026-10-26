"""Copy another tool's session into MemuCode's own store, as a conversation that ``/resume`` and ``/dashboard`` list.

The tool steps of the other agent are written into the text of its messages (``● shell: pytest -q`` and the start
of the result under ``⎿``) instead of being replayed as real tool calls: the other tool's calls cannot be answered
by MemuCode's tools, and a half-matching pair of call and result would break the conversation for the model.
Read back, an imported conversation is plain history — a person asked, an agent worked and said what it did.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..session import IMPORTED_PREFIX, Session
from .model import ForeignSession, Turn, clip

RESULT_LINES = 6

def session_id(tool: str, original: str) -> str:
    safe = "".join(ch if ch.isalnum() else "-" for ch in str(original))[:24].strip("-") or "session"
    return f"{IMPORTED_PREFIX}{tool}-{safe}"

def _step(turn: Turn) -> str:
    if turn.role == "tool":
        lines = [line for line in turn.text.splitlines() if line.strip()][:RESULT_LINES]
        mark = "⎿ " if turn.ok else "⎿ (failed) "
        return mark + ("\n   ".join(clip(line, 220) for line in lines) if lines else "(no output)")
    if turn.role == "note":
        return f"[{clip(turn.text, 300)}]"
    return f"● {turn.tool}" + (f": {clip(turn.detail, 200)}" if turn.detail else "")

def messages_of(session: ForeignSession) -> List[Dict[str, Any]]:
    """The conversation as MemuCode messages: the person's requests, and one assistant message per answer in
    which the steps and the text of the agent alternate as they happened."""
    info = session.info
    out: List[Dict[str, Any]] = []
    pending: List[str] = []
    span = max(1.0, (info.updated or 0) - (info.started or 0))
    count = max(1, len(session.turns))

    def stamp(index: int, turn: Turn) -> float:
        return turn.at or ((info.started or time.time()) + span * index / count)

    last_at = info.started or time.time()

    def flush() -> None:
        if pending:
            out.append({"role": "assistant", "content": "\n".join(pending).strip(), "ts": last_at, "imported": True,
                        "model": session.models[0] if session.models else info.model})
            pending.clear()

    for index, turn in enumerate(session.turns):
        at = stamp(index, turn)
        last_at = at
        if turn.role == "user":
            flush()
            out.append({"role": "user", "kind": "prompt", "content": turn.text, "ts": at, "imported": True})
        elif turn.role == "assistant" and not turn.tool:
            pending.append(("\n" if pending else "") + turn.text)
        else:
            pending.append(_step(turn))
    flush()
    return out

def import_session(root: Path, session: ForeignSession, *, replace: bool = False) -> Tuple[Optional[Session], str]:
    """Save ``session`` as a MemuCode conversation of the project ``root`` (the folder it worked in, when that
    still exists). Returns ``(conversation, "added" | "skipped: …")``."""
    info = session.info
    folder = Path(info.cwd) if info.cwd and Path(info.cwd).is_dir() else Path(root)
    messages = messages_of(session)
    if not messages:
        return None, "skipped: no messages"
    ident = session_id(info.tool, info.id)
    target = Session(folder, ident)
    if target.path.exists():
        if not replace:
            return None, "skipped: already imported"
        target.path.unlink()
    target.meta.update({"title": info.title or info.id, "created": info.started or time.time(),
                        "updated": info.updated or time.time(), "model": session.models[0] if session.models else info.model,
                        "imported_from": info.tool, "imported_id": info.id, "imported_path": info.path})
    for message in messages:
        target.append(message)
    target.meta["updated"] = info.updated or target.meta["updated"]
    target.flush_index()
    if info.updated:
        try:
            os.utime(target.path, (info.updated, info.updated))
        except OSError:
            pass
    return target, "added"
