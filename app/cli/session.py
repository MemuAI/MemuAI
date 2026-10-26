"""A MemuCode conversation and its transcript on disk.

The transcript is an append-only JSON Lines file per session
(``%APPDATA%\\MemuAI\\memucode\\sessions\\<project>\\<id>.jsonl``): every message,
metadata change, rewind and compaction is one line, so a crash never loses
more than the line being written and resuming replays the file. Images are
stored once next to it (``<id>.assets/<sha1>.jpg``) instead of inline base64.

Compaction never deletes history: a summary message marks where the model's
view starts, while ``/export`` and ``/resume`` still see everything.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from . import paths

IMPORTED_PREFIX = "imp-"

def new_id(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex[:12]

def _now() -> float:
    return time.time()

class Session:
    def __init__(self, root: Path, session_id: str = "", *, create: bool = True) -> None:
        self.root = Path(root).resolve()
        self.id = session_id or time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self.folder = paths.project_sessions(self.root)
        self.path = self.folder / f"{self.id}.jsonl"
        self.assets = self.folder / f"{self.id}.assets"
        self.messages: List[Dict[str, Any]] = []
        self.meta: Dict[str, Any] = {"id": self.id, "root": str(self.root), "created": _now(), "updated": _now(),
                                     "title": "", "name": "", "model": "", "forked_from": "", "goal": None}
        self.todos: List[Dict[str, Any]] = []
        self.plan = ""

        self.image_notes: Dict[str, Dict[str, Any]] = {}
        self.cwd: Optional[str] = None
        self.read_state: Dict[str, Tuple[int, ...]] = {}
        self.read_cache: Dict[Tuple[str, int, int], Any] = {}
        from .file_cache import TextFiles
        self.text_files = TextFiles()
        self.context_epoch = 0
        self.active_skills: Set[str] = set()
        self.lines_added = 0
        self.lines_removed = 0
        self.files_changed: Set[str] = set()
        self._lock = threading.Lock()
        self._index_dirty = False
        self.loaded = False

    def ensure_saved(self) -> None:
        """Persist an empty session so the UID printed on exit can be resumed."""
        if self.path.exists():
            return
        with self._lock:
            if self.path.exists():
                return
            self.folder.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"t": "meta", **self.meta}, ensure_ascii=False, default=str) + "\n",
                                 encoding="utf-8")
        self._index_dirty = True

    def _append(self, event: Dict[str, Any]) -> None:
        """Write one event. Normally the file appears with the first message;
        ``ensure_saved`` also persists an empty interactive session on exit."""
        if not self.path.exists():
            if event.get("t") != "msg":
                return
            header = json.dumps({"t": "meta", **self.meta}, ensure_ascii=False, default=str)
        else:
            header = ""
        line = json.dumps(event, ensure_ascii=False, default=str)
        with self._lock:
            self.folder.mkdir(parents=True, exist_ok=True)

            lead = "\n" if not header and not _ends_with_newline(self.path) else ""
            with self.path.open("a", encoding="utf-8") as handle:
                if header:
                    handle.write(header + "\n")
                handle.write(lead + line + "\n")
        self._index_dirty = True

    def _store_images(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Copy of the message for disk: inline image data moves to the assets folder."""
        def convert(parts: Any) -> Any:
            if not isinstance(parts, list):
                return parts
            out = []
            for part in parts:
                if isinstance(part, dict) and part.get("type") == "image" and part.get("data"):
                    raw = base64.b64decode(part["data"])
                    digest = hashlib.sha1(raw).hexdigest()[:20]
                    ext = {"image/png": ".png", "image/gif": ".gif", "image/webp": ".webp"}.get(
                        str(part.get("media_type")), ".jpg")
                    self.assets.mkdir(parents=True, exist_ok=True)
                    target = self.assets / (digest + ext)
                    if not target.exists():
                        target.write_bytes(raw)
                    stored = {k: v for k, v in part.items() if k != "data"}
                    stored["path"] = str(target)
                    out.append(stored)
                else:
                    out.append(part)
            return out

        stored = {key: value for key, value in message.items() if not key.startswith("_ui")}
        stored["content"] = convert(message.get("content"))
        if message.get("images"):
            stored["images"] = convert([{"type": "image", **image} for image in message["images"]])
        return stored

    @classmethod
    def load(cls, root: Path, session_id: str) -> "Session":
        session = cls(root, session_id, create=False)
        if not session.path.is_file():
            raise FileNotFoundError(session_id)

        with session.path.open("r", encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(event, dict):
                    session._replay(event)
        session.loaded = True
        return session

    def _replay(self, event: Dict[str, Any]) -> None:
        kind = event.get("t")
        if kind == "meta":
            self.meta.update({k: v for k, v in event.items() if k != "t"})
        elif kind == "msg" and isinstance(event.get("m"), dict):
            message = event["m"]
            if message.get("images"):
                message["images"] = [{k: v for k, v in image.items() if k != "type"} for image in message["images"]]
            self.messages.append(message)
        elif kind == "truncate":
            ident = event.get("from")
            index = self.index_of(ident)
            if index is not None:
                del self.messages[index:]
        elif kind == "elide":
            ids = set(event.get("ids") or [])
            for message in self.messages:
                if message.get("id") in ids:
                    message["elided"] = True
        elif kind == "todos":
            self.todos = list(event.get("todos") or [])
        elif kind == "imgnote":
            if event.get("key") and event.get("text"):
                self.image_notes[str(event["key"])] = {"text": str(event["text"]), "model": str(event.get("model") or "")}
        elif kind == "state":
            self.cwd = event.get("cwd") or self.cwd
            self.plan = event.get("plan", self.plan)

    def append(self, message: Dict[str, Any]) -> Dict[str, Any]:
        message.setdefault("id", new_id("m"))
        message.setdefault("ts", _now())
        self.messages.append(message)
        self._append({"t": "msg", "m": self._store_images(message)})
        self.meta["updated"] = message["ts"]
        return message

    def index_of(self, message_id: Any) -> Optional[int]:
        for index, message in enumerate(self.messages):
            if message.get("id") == message_id:
                return index
        return None

    def truncate_from(self, message_id: str) -> List[Dict[str, Any]]:
        """Drop the message and everything after it (rewind). Returns the dropped ones."""
        index = self.index_of(message_id)
        if index is None:
            return []
        dropped = self.messages[index:]
        del self.messages[index:]
        self._append({"t": "truncate", "from": message_id})
        self.context_epoch += 1
        self.read_cache.clear()
        self.read_state.clear()
        return dropped

    def elide(self, ids: Iterable[str]) -> int:
        ids = [ident for ident in ids if ident]
        if not ids:
            return 0
        wanted = set(ids)
        count = 0
        for message in self.messages:
            if message.get("id") in wanted and not message.get("elided"):
                message["elided"] = True
                count += 1
        if count:
            self._append({"t": "elide", "ids": list(wanted)})
            self.context_epoch += 1
            self.read_cache.clear()
        return count

    def set_meta(self, **values: Any) -> None:
        self.meta.update(values)
        self.meta["updated"] = _now()
        self._append({"t": "meta", **values})

    def save_todos(self) -> None:
        self._append({"t": "todos", "todos": self.todos})

    def add_image_note(self, key: str, text: str, model: str = "") -> None:
        """Keep a description of a picture, so it is made once and survives ``/resume``."""
        self.image_notes[str(key)] = {"text": str(text), "model": str(model)}
        self._append({"t": "imgnote", "key": str(key), "text": str(text), "model": str(model)})

    def save_state(self) -> None:
        self._append({"t": "state", "cwd": self.cwd, "plan": self.plan})

    def latest_summary(self) -> Optional[int]:
        for index in range(len(self.messages) - 1, -1, -1):
            if self.messages[index].get("kind") == "summary":
                return index
        return None

    def api_messages(self) -> List[Dict[str, Any]]:
        """What the model sees: the latest summary, then everything it does not cover.

        A summary is appended at the end of the transcript and names the last
        message it covers; the verbatim tail between that message and the
        summary, and everything after the summary, stay as they are.
        """
        position = self.latest_summary()
        if position is None:
            return [message for message in self.messages if not message.get("ui_only")]
        summary = self.messages[position]
        covered = self.index_of(summary.get("covers_until"))
        start = covered + 1 if covered is not None else position + 1
        rest = [message for index, message in enumerate(self.messages)
                if index >= start and index != position and not message.get("ui_only")
                and message.get("kind") != "summary"]
        return [summary, *rest]

    def user_prompts(self) -> List[Dict[str, Any]]:
        return [message for message in self.messages
                if message.get("role") == "user" and message.get("kind") in ("prompt", "command", "bash")]

    def last_user_text(self) -> str:
        for message in reversed(self.messages):
            if message.get("role") == "user" and message.get("kind") in ("prompt", "command"):
                return message_text(message)
        return ""

    def last_assistant_text(self) -> str:
        for message in reversed(self.messages):
            if message.get("role") == "assistant" and str(message.get("content") or "").strip():
                return message_text(message)
        return ""

    def assistant_texts(self) -> List[str]:
        return [message_text(message) for message in self.messages
                if message.get("role") == "assistant" and str(message.get("content") or "").strip()]

    def brief_context(self, chars: int = 3000) -> str:
        """Recent user requests and answers, for helpers that cannot see the conversation."""
        rows: List[str] = []
        for message in reversed(self.messages):
            if message.get("role") == "user" and message.get("kind") in ("prompt", "command", "summary"):
                rows.append("USER: " + message_text(message)[:1200])
            elif message.get("role") == "assistant" and message.get("content"):
                rows.append("ASSISTANT: " + message_text(message)[:1200])
            if sum(len(row) for row in rows) > chars:
                break
        return "\n".join(reversed(rows))[-chars:]

    def title(self) -> str:
        if self.meta.get("name"):
            return str(self.meta["name"])
        if self.meta.get("title"):
            return str(self.meta["title"])
        for message in self.messages:
            if message.get("role") == "user" and message.get("kind") in ("prompt", "command"):
                text = " ".join(message_text(message).split())
                return text[:80] or "…"
        return ""

    def note_edit(self, path: str, _text: str) -> None:
        self.files_changed.add(path)

    def index_row(self) -> Dict[str, Any]:
        prompts = [message for message in self.messages if message.get("role") == "user"
                   and message.get("kind") in ("prompt", "command")]
        return {"id": self.id, "title": self.title(), "name": self.meta.get("name", ""),
                "created": self.meta.get("created"), "updated": self.meta.get("updated"),
                "messages": len(self.messages), "prompts": len(prompts), "model": self.meta.get("model", ""),
                "first": message_text(prompts[0])[:200] if prompts else "",
                "last": message_text(prompts[-1])[:200] if prompts else "",
                "forked_from": self.meta.get("forked_from", ""), "root": str(self.root),
                "imported_from": self.meta.get("imported_from", "")}

    def flush_index(self) -> None:
        if not self._index_dirty or not self.messages:
            return
        self._index_dirty = False
        index_path = self.folder / "index.json"
        try:
            from ..storage import mutate_json

            def change(data: Any):
                data = data if isinstance(data, dict) else {}
                data[self.id] = self.index_row()
                return True, data

            mutate_json(index_path, {}, change, backup=False, validator=lambda x: isinstance(x, dict))
        except Exception:
            pass

    def fork(self, name: str = "") -> "Session":
        child = Session(self.root)
        child.meta.update({"forked_from": self.id, "model": self.meta.get("model", ""),
                           "name": name or "", "goal": self.meta.get("goal")})
        child._append({"t": "meta", **child.meta})
        for message in self.messages:
            copy = json.loads(json.dumps(self._store_images(message), ensure_ascii=False, default=str))
            child.messages.append(copy)
            child._append({"t": "msg", "m": copy})
        child.todos = list(self.todos)
        child.cwd = self.cwd
        child.flush_index()
        return child

def _ends_with_newline(path: Path) -> bool:
    """True for an empty or missing file, or one whose last byte is a newline."""
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            if handle.tell() == 0:
                return True
            handle.seek(-1, 2)
            return handle.read(1) == b"\n"
    except OSError:
        return True

def message_text(message: Dict[str, Any]) -> str:
    content = message.get("display") or message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(part.get("text") or "") for part in content
                         if isinstance(part, dict) and part.get("type") == "text")
    return ""

def load_image_data(part: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """(media_type, base64) for an image part, reading stored assets lazily."""
    if part.get("data"):
        return str(part.get("media_type") or "image/jpeg"), str(part["data"])
    path = part.get("path")
    if path and Path(path).is_file():
        try:
            return str(part.get("media_type") or "image/jpeg"), base64.b64encode(Path(path).read_bytes()).decode("ascii")
        except OSError:
            return None
    return None

def list_sessions(root: Optional[Path] = None, *, limit: int = 60) -> List[Dict[str, Any]]:
    folders = [paths.project_sessions(root)] if root is not None else \
        [item for item in paths.sessions_root().iterdir() if item.is_dir()]
    rows: List[Dict[str, Any]] = []
    for folder in folders:
        index: Dict[str, Any] = {}
        try:
            index = json.loads((folder / "index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            index = {}
        for file in folder.glob("*.jsonl"):
            session_id = file.stem
            row = index.get(session_id)
            try:
                mtime = file.stat().st_mtime
            except OSError:
                continue
            if not isinstance(row, dict) or float(row.get("updated") or 0) + 5 < mtime:
                row = _scan(file) or {}
            if not row or not row.get("root"):
                continue
            row["id"] = session_id
            row["file"] = str(file)
            row["updated"] = max(float(row.get("updated") or 0), mtime)
            rows.append(row)
    rows.sort(key=lambda row: -float(row.get("updated") or 0))
    return rows[:limit]

def _scan(file: Path) -> Optional[Dict[str, Any]]:
    """Index row from the transcript itself (index missing or stale)."""
    try:
        root = ""
        title = name = model = first = last = imported = forked = ""
        created = updated = 0.0
        prompts = messages = 0
        with file.open("r", encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("t") == "meta":
                    root = event.get("root") or root
                    title = event.get("title") or title
                    name = event.get("name") or name
                    model = event.get("model") or model
                    imported = event.get("imported_from") or imported
                    forked = event.get("forked_from") or forked
                    created = created or float(event.get("created") or 0)
                elif event.get("t") == "msg":
                    message = event.get("m") or {}
                    messages += 1
                    updated = float(message.get("ts") or updated)
                    if message.get("role") == "user" and message.get("kind") in ("prompt", "command"):
                        prompts += 1
                        text = message_text(message)[:200]
                        first = first or text
                        last = text
        return {"title": name or title or first[:80], "name": name, "root": root, "model": model,
                "created": created, "updated": updated, "prompts": prompts, "messages": messages,
                "first": first, "last": last, "imported_from": imported, "forked_from": forked}
    except OSError:
        return None

def latest_session(root: Path) -> Optional[str]:
    rows = list_sessions(root, limit=1)
    return rows[0]["id"] if rows else None

def cleanup(days: int, keep: Iterable[str] = ()) -> int:
    """Delete transcripts older than ``days`` (``cleanup_period_days``).

    ``keep`` names sessions that are open right now: ``memucode -r <old id>``
    resumed a transcript older than the period, and the housekeeping a few
    seconds later deleted it under the running session (later appends then
    started a new file without the history)."""
    cutoff = time.time() - max(1, int(days)) * 86400
    keep = {str(item) for item in keep if item}
    removed = 0
    for folder in paths.sessions_root().iterdir():
        if not folder.is_dir():
            continue
        for file in folder.glob("*.jsonl"):
            if file.stem.startswith(IMPORTED_PREFIX):
                continue
            if file.stem in keep:
                continue
            try:
                if file.stat().st_mtime < cutoff:
                    file.unlink()
                    assets = file.with_suffix(".assets")
                    if assets.is_dir():
                        import shutil
                        shutil.rmtree(assets, ignore_errors=True)
                    removed += 1
            except OSError:
                continue
    return removed

_SAFE_ID = re.compile(r"^[\w.\-]{1,120}$")

def _folder_of(root: Path) -> Path:
    return paths.project_sessions(root)

def delete_session(root: Path, session_id: str) -> None:
    """Remove one conversation for good: its transcript, its pictures and its row in the index.

    Only a plain id is accepted — an id with a path in it never reaches the file system."""
    if not _SAFE_ID.match(str(session_id or "")):
        raise ValueError("not a session id")
    folder = _folder_of(root)
    file = folder / f"{session_id}.jsonl"
    if not file.is_file():
        raise FileNotFoundError(session_id)
    file.unlink()
    assets = folder / f"{session_id}.assets"
    if assets.is_dir():
        import shutil
        shutil.rmtree(assets, ignore_errors=True)
    try:
        from ..storage import mutate_json

        def change(data: Any):
            data = data if isinstance(data, dict) else {}
            return data.pop(session_id, None) is not None, data

        mutate_json(folder / "index.json", {}, change, backup=False, validator=lambda x: isinstance(x, dict))
    except Exception:
        pass

def rename_session(root: Path, session_id: str, name: str) -> None:
    """Give a conversation a name without loading it: one ``meta`` line is appended to its transcript."""
    if not _SAFE_ID.match(str(session_id or "")):
        raise ValueError("not a session id")
    folder = _folder_of(root)
    file = folder / f"{session_id}.jsonl"
    if not file.is_file():
        raise FileNotFoundError(session_id)
    name = " ".join(str(name or "").split())[:120]
    with file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"t": "meta", "name": name, "updated": _now()}, ensure_ascii=False) + "\n")
    try:
        from ..storage import mutate_json

        def change(data: Any):
            data = data if isinstance(data, dict) else {}
            if isinstance(data.get(session_id), dict):
                data[session_id]["name"] = name
                data[session_id]["title"] = name or data[session_id].get("title", "")
                return True, data
            return False, data

        mutate_json(folder / "index.json", {}, change, backup=False, validator=lambda x: isinstance(x, dict))
    except Exception:
        pass
