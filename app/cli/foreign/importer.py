"""Copy another tool's session into MemuCode's own store, as a conversation that ``/resume`` and ``/dashboard`` list.

The tool steps of the other agent are written into the text of its messages (``● shell: pytest -q`` and the start
of the result under ``⎿``) instead of being replayed as real tool calls: the other tool's calls cannot be answered
by MemuCode's tools, and a half-matching pair of call and result would break the conversation for the model.
Read back, an imported conversation is plain history — a person asked, an agent worked and said what it did.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..session import IMPORTED_PREFIX, Session, new_id
from .model import MAX_TURNS, ForeignSession, Turn, clip

RESULT_LINES = 6

def session_id(tool: str, original: str) -> str:
    """The id of the imported session. The full (sanitised) foreign id is
    kept plus a sha1 suffix: truncating to 24 characters used to collide
    two different foreign ids that share a prefix into one file."""
    safe = "".join(ch if ch.isalnum() else "-" for ch in str(original)).strip("-") or "session"
    digest = hashlib.sha1(str(original).encode("utf-8")).hexdigest()[:16]
    return f"{IMPORTED_PREFIX}{tool}-{safe[:100]}-{digest}"

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

    # F31: cap the turns an import turns into messages.
    for index, turn in enumerate(session.turns[:MAX_TURNS]):
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
    """Save ``session`` as a MemuCode conversation of the project ``root``.
    Returns ``(conversation, "added" | "skipped: …")``.

    The transcript is written to a temp file in the same folder and moved
    into place with ``os.replace``: the old behaviour deleted the existing
    file first (``unlink()``) and then appended, so a failure in between
    lost the imported session. The foreign ``cwd`` is only metadata now —
    the session always lands in MemuCode's own store for ``root``, never in
    the other tool's folder.
    """
    info = session.info
    messages = messages_of(session)
    if not messages:
        return None, "skipped: no messages"
    ident = session_id(info.tool, info.id)
    target = Session(Path(root), ident)
    if target.path.exists() and not replace:
        return None, "skipped: already imported"
    target.meta.update({"title": info.title or info.id, "created": info.started or time.time(),
                        "updated": info.updated or time.time(), "model": session.models[0] if session.models else info.model,
                        "imported_from": info.tool, "imported_id": info.id, "imported_path": info.path,
                        "imported_cwd": info.cwd})
    target.folder.mkdir(parents=True, exist_ok=True)
    tmp_path = target.path.with_name(target.path.name + ".tmp-" + secrets.token_hex(6))
    try:
        lines = [json.dumps({"t": "meta", **target.meta}, ensure_ascii=False, default=str)]
        for message in messages:
            message.setdefault("id", new_id("m"))
            message.setdefault("ts", time.time())
            stored = target._store_images(message)
            target.messages.append(message)
            lines.append(json.dumps({"t": "msg", "m": stored}, ensure_ascii=False, default=str))
        with tmp_path.open("w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target.path)
    except Exception:
        # The previous file (if any) is untouched; drop the temp file and
        # any half-written staging assets of this attempt.
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    target.meta["updated"] = info.updated or target.meta["updated"]
    target._index_dirty = True
    target.flush_index()
    if info.updated:
        try:
            os.utime(target.path, (info.updated, info.updated))
        except OSError:
            pass
    return target, "added"
