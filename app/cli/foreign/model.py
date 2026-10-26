"""What every reader of another tool's sessions produces, and the helpers they share.

The readers only ever *read*: a session of Claude Code, Codex, Grok… is never changed, moved or locked
(databases are opened read-only). What they return is plain data — a list of turns — so MemuCode can show it,
import it as a conversation of its own, or turn it into a hand-over note for the model.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

HEAD_BYTES = 96_000

RESULT_CHARS = 1_800
TEXT_CHARS = 12_000

@dataclass
class Turn:
    """One step of a session. ``role`` is ``user``, ``assistant``, ``tool`` (a result) or ``note``."""
    role: str
    text: str = ""
    tool: str = ""
    detail: str = ""
    ok: bool = True
    at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {key: value for key, value in self.__dict__.items() if value not in ("", 0.0) or key in ("role", "ok")}

@dataclass
class SessionInfo:
    """What a list of sessions shows — cheap to produce, no full read."""
    tool: str
    id: str
    title: str = ""
    cwd: str = ""
    started: float = 0.0
    updated: float = 0.0
    messages: int = 0
    model: str = ""
    path: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.tool}:{self.id}"

@dataclass
class ForeignSession:
    info: SessionInfo
    turns: List[Turn] = field(default_factory=list)
    summary: str = ""
    todos: List[str] = field(default_factory=list)
    models: List[str] = field(default_factory=list)

    def prompts(self) -> List[Turn]:
        return [turn for turn in self.turns if turn.role == "user" and turn.text.strip()]

    def files(self, limit: int = 40) -> List[str]:
        """Paths the other tool read or changed, in order of first use."""
        seen: Dict[str, None] = {}
        for turn in self.turns:
            if turn.tool and turn.detail and turn.role == "assistant" and _looks_like_path(turn.detail):
                seen.setdefault(turn.detail, None)
        return list(seen)[:limit]

class ReadError(Exception):
    """A session that cannot be read (missing, damaged, an unknown newer format)."""

def clip(text: Any, limit: int = RESULT_CHARS) -> str:
    """Text cut to ``limit`` characters, keeping the start and the end (where errors and results usually are)."""
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    keep = max(40, limit // 2)
    return text[:keep].rstrip() + f"\n… [{len(text) - 2 * keep} characters left out] …\n" + text[-keep:].lstrip()

def iso_to_ts(value: Any) -> float:
    """An ISO-8601 time (``2026-09-28T20:01:09.123Z``) or a number of seconds/milliseconds → seconds."""
    if isinstance(value, (int, float)):
        number = float(value)
        return number / 1000.0 if number > 1e11 else number
    text = str(value or "").strip()
    if not text:
        return 0.0
    if re.fullmatch(r"\d{10,13}(?:\.\d+)?", text):
        return iso_to_ts(float(text))
    try:
        fixed = text.replace("Z", "+00:00")
        fixed = re.sub(r"(\.\d{6})\d+", r"\1", fixed)
        parsed = datetime.fromisoformat(fixed)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except ValueError:
        return 0.0

def read_lines(path: Path, *, limit_bytes: int = 0) -> Iterator[Dict[str, Any]]:
    """The JSON objects of a JSONL file, skipping damaged lines. ``limit_bytes`` stops after about that much."""
    read = 0
    try:
        with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                read += len(raw)
                raw = raw.strip()
                if raw:
                    try:
                        data = json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(data, dict):
                        yield data
                if limit_bytes and read >= limit_bytes:
                    return
    except OSError as exc:
        raise ReadError(str(exc)) from exc

def tail_lines(path: Path, size: int = 48_000) -> List[Dict[str, Any]]:
    """The last JSON objects of a big JSONL file (to learn when a session was last used) without reading it all."""
    try:
        with Path(path).open("rb") as handle:
            handle.seek(0, 2)
            end = handle.tell()
            handle.seek(max(0, end - size))
            chunk = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    rows = []
    for raw in chunk.splitlines()[1:]:
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if isinstance(data, dict):
            rows.append(data)
    return rows

def text_of(content: Any) -> str:
    """The text in a message ``content``: a string, or a list of blocks with ``text`` (and nothing else of value)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                kind = str(block.get("type") or "")
                if kind in ("text", "input_text", "output_text") or ("text" in block and not kind):
                    parts.append(str(block.get("text") or ""))
                elif kind in ("image", "input_image", "image_url"):
                    parts.append("[image]")
        return "\n".join(part for part in parts if part)
    if isinstance(content, dict):
        return str(content.get("text") or "")
    return ""

_PATHY = re.compile(r"^(?:[A-Za-z]:[\\/]|/|\.{0,2}[\\/]|~)|[\\/][\w.\-]+\.\w{1,8}$|^[\w.\-]+\.\w{1,8}$")

def _looks_like_path(text: str) -> bool:
    return bool(_PATHY.search(text)) and " " not in text.strip() and len(text) < 300

def short_args(tool: str, args: Any) -> str:
    """What a tool call was about, in a few words: the command, the path or the query."""
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return clip(args, 160)
    if not isinstance(args, dict):
        return clip(args, 160) if args else ""
    for key in ("command", "cmd", "CommandLine", "script", "file_path", "filePath", "path", "AbsolutePath", "TargetFile",
                "target_file", "DirectoryPath", "directory", "pattern", "query", "Query", "url", "Url", "prompt",
                "description", "name"):
        value = args.get(key)
        if value:
            if isinstance(value, list):
                value = " ".join(str(item) for item in value)
            return clip(" ".join(str(value).split()), 200)
    for value in args.values():
        if isinstance(value, str) and value:
            return clip(" ".join(value.split()), 160)
    return ""

_WRAPPER_TAGS = ("system-reminder", "ide_opened_file", "ide_selection", "local-command-caveat", "local-command-stdout",
                 "local-command-stderr", "environment_context", "user_instructions", "recommended_plugins",
                 "permissions instructions", "user-prompt-submit-hook")
_OPENERS = re.compile(r"<(" + "|".join(re.escape(tag) for tag in _WRAPPER_TAGS) + r")[^>\n]{0,200}>", re.I)

def _strip_wrappers(text: str) -> str:
    """Remove ``<tag …>…</tag>`` for the tags a tool puts around what a person typed.

    Written as a scan with ``str.find`` and not as one regular expression: this text comes from files of other tools
    and may be anything, and ``.*?`` after thousands of opening tags with no closing one took minutes. Once a tag has
    no closing tag after some point, none of its later openers can have one either, so each tag is searched for
    once — the whole thing is linear."""
    out: list = []
    position = 0
    no_closer: set = set()
    lowered = text.lower()
    while True:
        found = _OPENERS.search(text, position)
        if found is None:
            break
        tag = found.group(1).lower()
        if tag in no_closer:
            out.append(text[position:found.end()])
            position = found.end()
            continue
        closer = "</" + tag + ">"
        end = lowered.find(closer, found.end())
        if end < 0:
            no_closer.add(tag)
            out.append(text[position:found.end()])
            position = found.end()
            continue
        out.append(text[position:found.start()])
        position = end + len(closer)
    out.append(text[position:])
    return "".join(out)

def _between(text: str, tag: str) -> Optional[str]:
    opening, closing = f"<{tag}>", f"</{tag}>"
    start = text.find(opening)
    if start < 0:
        return None
    end = text.find(closing, start + len(opening))
    return None if end < 0 else text[start + len(opening):end].strip()

def clean_user_text(text: str) -> str:
    """The words a person typed, without the wrappers a tool adds around them (reminders, IDE context…)."""
    text = _strip_wrappers(str(text or ""))
    command = _between(text, "command-name")
    if command is not None:
        args = _between(text, "command-args")
        text = (command + " " + (args or "")).strip()
    return text.strip()

def title_from(text: str, limit: int = 90) -> str:
    line = " ".join(clean_user_text(text).split())
    return line[:limit] + ("…" if len(line) > limit else "")

def now() -> float:
    return time.time()
