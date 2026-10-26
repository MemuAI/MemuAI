"""MemuCode CLI sessions in the Telegram Mini App.

* Sessions are the CLI's own append-only transcripts
  (``memucode/sessions/<project>/<id>.jsonl``, see ``app/cli/session.py``);
  the Mini App reads them from a byte cursor, so watching is incremental.
* A terminal that is open right now publishes itself through
  ``app/cli/remote.py``: its live state (the answer being written, running
  tools, an open permission question with the exact tool call) and an inbox
  for prompts, Esc and answers.
* A session that is not open anywhere can still be continued from the phone:
  ``memucode -r <id> -p --output-format stream-json -- "<prompt>"`` runs one
  turn headlessly in the session's folder. Without an explicit permission
  mode nothing that needs approval is executed (that is how ``-p`` works).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .tgapp_chat import Job, jobs

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
HEADLESS_MODES = ("default", "plan", "acceptEdits", "bypassPermissions")
MAX_ITEM_TEXT = 12000
MAX_READ = 2 * 1024 * 1024

def _remote():
    from .cli import remote
    return remote

def list_sessions(limit: int = 80) -> List[Dict[str, Any]]:
    from .cli.session import list_sessions as cli_list
    try:
        rows = cli_list(limit=limit)
    except Exception:
        rows = []
    live = {row.get("session_id"): row for row in _remote().list_terminals()}
    running = {job.meta.get("session_id"): job for job in jobs.jobs.values()
               if job.kind == "code" and not job.done}
    out = []
    for row in rows:
        session_id = str(row.get("id") or "")
        terminal = live.pop(session_id, None)
        out.append(_session_row(row, terminal, running.get(session_id)))

    for session_id, terminal in live.items():
        out.insert(0, _session_row({"id": session_id, "root": terminal.get("root"), "title": terminal.get("title"),
                                    "model": terminal.get("model"), "updated": terminal.get("heartbeat"),
                                    "prompts": 0, "messages": 0}, terminal, None))
    out.sort(key=lambda item: (0 if item["live"] else 1, -float(item.get("updated") or 0)))
    return out

def _session_row(row: Dict[str, Any], terminal: Optional[Dict[str, Any]], job: Optional[Job]) -> Dict[str, Any]:
    root = str(row.get("root") or "")
    return {
        "id": str(row.get("id") or ""), "title": str(row.get("title") or row.get("first") or "")[:200],
        "root": root, "project": Path(root).name if root else "", "model": str(row.get("model") or ""),
        "updated": float(row.get("updated") or 0), "prompts": int(row.get("prompts") or 0),
        "messages": int(row.get("messages") or 0), "last": str(row.get("last") or "")[:200],
        "imported": bool(row.get("imported_from")),
        "live": terminal is not None, "rc": terminal.get("rc") if terminal else "",
        "busy": bool(terminal.get("busy")) if terminal else bool(job),
        "host": bool(terminal.get("host")) if terminal else False,
        "asking": bool(terminal.get("dialog")) if terminal else False,
        "headless_job": job.id if job else "",
    }

def find_transcript(session_id: str) -> Optional[Path]:
    if not SESSION_ID_RE.match(str(session_id or "")):
        return None
    from .cli import paths
    for folder in paths.sessions_root().iterdir():
        candidate = folder / f"{session_id}.jsonl"
        if folder.is_dir() and candidate.is_file():
            return candidate
    return None

def session_root(file: Path) -> str:
    try:
        with file.open("r", encoding="utf-8", errors="replace") as handle:
            first = json.loads(handle.readline() or "{}")
        return str(first.get("root") or "") if isinstance(first, dict) else ""
    except (OSError, ValueError):
        return ""

def _text_of(message: Dict[str, Any]) -> str:
    from .cli.session import message_text
    return message_text(message)

def _clip(text: str, limit: int = MAX_ITEM_TEXT) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[:limit] + f"\n… (+{len(text) - limit})"

def transcript_items(event: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One JSONL event as zero or more display items for the phone."""
    kind = event.get("t")
    if kind == "truncate":
        return [{"kind": "rewind", "from": str(event.get("from") or "")}]
    if kind == "meta":
        values = {k: v for k, v in event.items() if k in ("title", "name", "model")}
        return [{"kind": "meta", **values}] if values else []
    if kind == "todos":
        return [{"kind": "todos", "todos": [{"content": str(row.get("content") or "")[:200],
                                              "status": str(row.get("status") or "")}
                                             for row in (event.get("todos") or [])[:30] if isinstance(row, dict)]}]
    if kind != "msg" or not isinstance(event.get("m"), dict):
        return []
    message = event["m"]
    role, sub = message.get("role"), message.get("kind")
    base = {"id": str(message.get("id") or ""), "ts": float(message.get("ts") or 0)}
    if role == "user":
        if sub in ("prompt", "command", "bash"):
            images = len(message.get("images") or [])
            if isinstance(message.get("content"), list):
                images += sum(1 for part in message["content"] if isinstance(part, dict) and part.get("type") == "image")
            return [{**base, "kind": "user", "sub": sub, "text": _clip(_text_of(message)), "images": images}]
        if sub == "summary":
            return [{**base, "kind": "summary", "text": _clip(_text_of(message), 2000)}]
        if sub == "interrupt":
            return [{**base, "kind": "interrupt"}]
        return []
    if role == "assistant":
        items = []
        text = str(message.get("content") or "") if isinstance(message.get("content"), str) else _text_of(message)
        if text.strip():
            items.append({**base, "kind": "assistant", "text": _clip(text), "model": str(message.get("model") or ""),
                          "interrupted": bool(message.get("interrupted"))})
        for call in message.get("tool_calls") or []:
            if isinstance(call, dict):
                items.append({**base, "kind": "call", "call": str(call.get("id") or ""),
                              "name": str(call.get("name") or ""),
                              "args": _clip(json.dumps(call.get("args") or {}, ensure_ascii=False, default=str), 3000)})
        return items
    if role == "tool":
        content = message.get("content")
        text = content if isinstance(content, str) else _text_of(message)
        return [{**base, "kind": "tool", "call": str(message.get("tool_call_id") or ""),
                 "name": str(message.get("name") or ""), "display": str(message.get("display") or "")[:400],
                 "summary": str(message.get("summary") or "")[:400], "ok": not message.get("is_error"),
                 "output": _clip(text, 4000), "diff": _clip(str(message.get("diff") or ""), 8000)}]
    return []

def read_transcript(session_id: str, cursor: int = 0) -> Dict[str, Any]:
    """Items appended since ``cursor`` (a byte offset); ``reset`` when the file was replaced."""
    file = find_transcript(session_id)
    if file is None:
        return {"items": [], "cursor": 0, "missing": True, "reset": False}
    size = file.stat().st_size
    reset = False
    if cursor < 0 or cursor > size:
        cursor, reset = 0, True
    start = cursor

    if cursor == 0 and size > MAX_READ:
        start = size - MAX_READ
    items: List[Dict[str, Any]] = []
    with file.open("rb") as handle:
        handle.seek(start)
        data = handle.read(min(size - start, MAX_READ * 2))
    end = start + len(data)
    if not data.endswith(b"\n"):
        cut = data.rfind(b"\n")
        if cut < 0:
            return {"items": [], "cursor": start, "reset": reset, "size": size}
        end = start + cut + 1
        data = data[:cut + 1]
    lines = data.split(b"\n")
    if start and cursor == 0 and lines:
        lines = lines[1:]
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            event = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue
        if isinstance(event, dict):
            items.extend(transcript_items(event))
    return {"items": items, "cursor": end, "reset": reset, "size": size, "partial": bool(start and cursor == 0),
            "root": session_root(file)}

def live_state(rc: str) -> Optional[Dict[str, Any]]:
    return _remote().terminal_state(rc)

def terminal_for(session_id: str) -> Optional[Dict[str, Any]]:
    return next((row for row in _remote().list_terminals() if row.get("session_id") == session_id), None)

class BlockedCommand(ValueError):
    """A prompt the phone may not send (it would skip the permission dialog)."""

BLOCKED_COMMANDS = ("/run", "/!")

def blocked_prompt(text: str) -> bool:
    head = str(text or "").lstrip().split(None, 1)[0].lower() if str(text or "").strip() else ""
    return head.startswith("!") or head in BLOCKED_COMMANDS

def send_to_terminal(rc: str, command: Dict[str, Any]) -> str:
    allowed = {"prompt": ("text",), "interrupt": (), "answer": ("dialog", "choice", "choices", "text", "confirm"),
               "close": ()}
    kind = str(command.get("type") or "")
    if kind not in allowed:
        raise ValueError("unknown command")
    clean = {"type": kind}
    for key in allowed[kind]:
        if key in command:
            clean[key] = command[key]
    if kind == "close":
        state = live_state(rc) or {}
        if not state.get("host"):
            raise ValueError("only a phone-hosted session can be closed")
    if kind == "prompt":
        text = str(clean.get("text") or "").strip()
        if not text or len(text) > 100_000:
            raise ValueError("bad prompt")
        if blocked_prompt(text):
            raise BlockedCommand("blocked")
        clean["text"] = text
    if kind == "answer":
        state = live_state(rc) or {}
        dialog = state.get("dialog") or {}
        if not dialog or int(clean.get("dialog") or 0) != int(dialog.get("id") or -1):
            raise LookupError("the question has changed")
        choice = clean.get("choice")
        options = dialog.get("options") or []
        detail = dialog.get("detail") or {}
        if isinstance(choice, int) and 0 <= choice < len(options):
            value = options[choice].get("value")
            approving = value not in (None, "no", "feedback") and not options[choice].get("cancel")
            if detail.get("danger") and approving and clean.get("confirm") is not True:
                raise PermissionError("confirm")
            if value == "always" and clean.get("confirm") is not True:
                raise PermissionError("confirm")
    return _remote().send_command(rc, clean)

def memucode_command() -> Optional[List[str]]:
    """How to start MemuCode from this process (installed exe, or the source tree)."""
    override = os.environ.get("MEMUCODE_EXE", "").strip()
    if override and Path(override).is_file():
        return [override]
    if getattr(sys, "frozen", False):
        here = Path(sys.executable).resolve().parent
        for candidate in (here.parent / "MemuCode" / "MemuCode.exe", here / "MemuCode.exe",
                          here.parent / "MemuCode.exe"):
            if candidate.is_file():
                return [str(candidate)]
        found = shutil.which("MemuCode.exe") if os.name == "nt" else None
        return [found] if found else None
    script = Path(__file__).resolve().parent.parent / "memucode.py"
    if script.is_file():
        python = Path(sys.executable)
        if os.name == "nt" and python.name.lower() == "pythonw.exe" and python.with_name("python.exe").is_file():
            python = python.with_name("python.exe")
        return [str(python), str(script)]
    return None

def start_headless(owner: str, session_id: str, text: str, mode: str, *,
                   popen: Any = subprocess.Popen) -> Job:
    file = find_transcript(session_id)
    if file is None:
        raise LookupError("session not found")
    if terminal_for(session_id) is not None:
        raise RuntimeError("live")
    if any(job.kind == "code" and not job.done and job.meta.get("session_id") == session_id
           for job in jobs.jobs.values()):
        raise RuntimeError("busy")
    if mode not in HEADLESS_MODES:
        mode = "default"
    root = session_root(file)
    if not root or not Path(root).is_dir():
        raise LookupError("project folder is missing")
    text = str(text or "").strip()
    if not text or len(text) > 20000:
        raise ValueError("bad prompt")
    command = memucode_command()
    if not command:
        raise FileNotFoundError("MemuCode CLI is not installed")
    args = [*command, "-r", session_id, "-p", "--output-format", "stream-json", "--cwd", root]
    if mode != "default":
        args += ["--permission-mode", mode]
    args += ["--", text]
    env = dict(os.environ)
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "MEMUCODE_NO_REMOTE": "1", "NO_COLOR": "1"})
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    process = popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, cwd=root,
                    env=env, creationflags=flags, text=True, encoding="utf-8", errors="replace")
    job = jobs.add(Job("code", owner))
    job.meta = {"session_id": session_id, "mode": mode, "root": root}
    job.emit("start", session_id=session_id, mode=mode, text=text)

    def cancel() -> None:
        with contextlib.suppress(Exception):
            if process.poll() is None:
                process.terminate()

    job.cancel_hook = cancel
    job.task = asyncio.ensure_future(_pump_headless(job, process))
    return job

async def _pump_headless(job: Job, process: Any) -> None:
    loop = asyncio.get_running_loop()
    queue: "asyncio.Queue[Optional[Tuple[str, str]]]" = asyncio.Queue()

    def reader(stream: Any, name: str) -> None:
        try:
            for line in iter(stream.readline, ""):
                loop.call_soon_threadsafe(queue.put_nowait, (name, line.rstrip("\r\n")))
        except Exception:
            pass
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=reader, args=(process.stdout, "out"), daemon=True).start()
    threading.Thread(target=reader, args=(process.stderr, "err"), daemon=True).start()
    finished = 0
    errors: List[str] = []
    result: Dict[str, Any] = {}
    try:
        while finished < 2:
            item = await queue.get()
            if item is None:
                finished += 1
                continue
            name, line = item
            if name == "err":
                if line.strip():
                    errors.append(line[:500])
                    errors = errors[-20:]
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get("type")
            if kind == "text":
                job.emit("delta", text=str(event.get("text") or ""))
            elif kind == "reasoning":
                job.emit("reasoning", text=str(event.get("text") or ""))
            elif kind == "tool_use":
                job.emit("tool_use", id=str(event.get("id") or ""), name=str(event.get("name") or ""),
                         input=_clip(json.dumps(event.get("input") or {}, ensure_ascii=False, default=str), 3000))
            elif kind == "tool_result":
                job.emit("tool_result", id=str(event.get("id") or ""), name=str(event.get("name") or ""),
                         ok=bool(event.get("ok")), summary=str(event.get("summary") or event.get("error") or "")[:400])
            elif kind == "todos":
                job.emit("todos", todos=event.get("todos") or [])
            elif kind == "notice":
                job.emit("notice", level=str(event.get("level") or ""), text=str(event.get("text") or "")[:500])
            elif kind == "result":
                result = {key: event.get(key) for key in ("subtype", "is_error", "result", "num_turns",
                                                          "duration_ms", "total_cost_usd")}
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            if process.poll() is None:
                process.terminate()
        job.emit("done", stopped=True, ok=False)
        job.finish()
        return
    code = None
    with contextlib.suppress(Exception):
        code = await asyncio.to_thread(process.wait, 15)
    if result:
        job.emit("result", **result)
    ok = code == 0 and not (result or {}).get("is_error")
    if not ok and not result:
        job.emit("error", message=("\n".join(errors[-5:]) or f"MemuCode exited with code {code}")[:1500])
    job.emit("done", stopped=False, ok=ok, code=code)
    job.finish()

import secrets as _secrets
import string as _string
import time as _time

HOST_WAIT = 45.0
HOST_LOG_LIMIT = 64 * 1024
MAX_TEXT_FILE = 512 * 1024
MAX_IMAGE_FILE = 4 * 1024 * 1024
MAX_DIR_ENTRIES = 600
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
               ".webp": "image/webp", ".bmp": "image/bmp", ".ico": "image/x-icon", ".svg": "image/svg+xml"}
HIDDEN_DIRS = {".git", "__pycache__", ".hg", ".svn", ".memucode-cache"}
_hosts: Dict[str, Any] = {}
_hosts_lock = threading.Lock()

def _remote_folder() -> Path:
    return _remote().remote_dir()

def host_log_tail(nonce: str) -> str:
    file = _remote_folder() / f"host-{nonce}.log"
    try:
        data = file.read_bytes()[-4000:]
    except OSError:
        return ""
    return data.decode("utf-8", "replace").strip()

def _find_host(nonce: str) -> Optional[Dict[str, Any]]:
    return next((row for row in _remote().list_terminals() if str(row.get("host_nonce") or "") == nonce), None)

def _reap_hosts() -> None:
    with _hosts_lock:
        for nonce, (process, log) in list(_hosts.items()):
            if process.poll() is not None:
                with contextlib.suppress(Exception):
                    log.close()
                _hosts.pop(nonce, None)
                with contextlib.suppress(OSError):
                    (_remote_folder() / f"host-{nonce}.log").unlink()

def start_host(owner: str, *, session_id: str = "", root: str = "", model: str = "", mode: str = "",
               lang: str = "", popen: Any = subprocess.Popen, wait: float = HOST_WAIT) -> Dict[str, Any]:
    """Open a session for the phone: the live terminal if there is one, else a new remote host.

    ``session_id`` continues a conversation (its folder comes from the transcript); without it a new
    conversation starts in ``root``. Returns ``{"rc", "session_id", "started"}``."""
    _reap_hosts()
    if session_id:
        terminal = terminal_for(session_id)
        if terminal is not None:
            return {"rc": terminal.get("rc"), "session_id": session_id, "started": False}
        file = find_transcript(session_id)
        if file is None:
            raise LookupError("session not found")
        if any(job.kind == "code" and not job.done and job.meta.get("session_id") == session_id
               for job in jobs.jobs.values()):
            raise RuntimeError("busy")
        root = session_root(file)
        if not root or not Path(root).is_dir():
            raise LookupError("project folder is missing")
    else:
        folder = Path(str(root or "")).expanduser()
        if not str(root or "").strip() or not folder.is_absolute() or not folder.is_dir():
            raise LookupError("project folder is missing")
        root = str(folder.resolve())
    if mode and mode not in ("default", "acceptEdits", "plan", "auto", "bypassPermissions"):
        mode = ""
    command = memucode_command()
    if not command:
        raise FileNotFoundError("MemuCode CLI is not installed")
    nonce = _secrets.token_hex(8)
    args = [*command, "remote-host", "--cwd", root]
    if session_id:
        args += ["-r", session_id]
    if model:
        args += ["-m", str(model)[:300]]
    if mode:
        args += ["--permission-mode", mode]
    if lang in ("ru", "en"):
        args += ["--lang", lang]
    env = dict(os.environ)
    env.pop("MEMUCODE_NO_REMOTE", None)
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "NO_COLOR": "1",
                "MEMUCODE_REMOTE_HOST_NONCE": nonce})
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    log_path = _remote_folder() / f"host-{nonce}.log"
    log = open(log_path, "wb")
    try:
        process = popen(args, stdout=subprocess.DEVNULL, stderr=log, stdin=subprocess.DEVNULL, cwd=root, env=env,
                        creationflags=flags)
    except Exception:
        log.close()
        with contextlib.suppress(OSError):
            log_path.unlink()
        raise
    with _hosts_lock:
        _hosts[nonce] = (process, log)
    deadline = _time.monotonic() + wait
    while _time.monotonic() < deadline:
        terminal = _find_host(nonce)
        if terminal is not None:
            return {"rc": terminal.get("rc"), "session_id": str(terminal.get("session_id") or session_id),
                    "started": True, "owner": owner}
        if process.poll() is not None:
            tail = host_log_tail(nonce)
            raise RuntimeError("host_failed:" + (tail.splitlines()[-1] if tail else f"exit {process.returncode}")[:300])
        _time.sleep(0.25)
    with contextlib.suppress(Exception):
        process.terminate()
    raise TimeoutError("the MemuCode host did not start in time")

def stop_hosts() -> None:
    """Proxy shutdown: hosts close themselves when idle, but never outlive the Proxy by much."""
    with _hosts_lock:
        rows = list(_hosts.items())
        _hosts.clear()
    for _nonce, (process, log) in rows:
        with contextlib.suppress(Exception):
            if process.poll() is None:
                process.terminate()
        with contextlib.suppress(Exception):
            log.close()

def recent_projects(limit: int = 40) -> List[Dict[str, Any]]:
    from .cli.session import list_sessions as cli_list
    seen: Dict[str, Dict[str, Any]] = {}
    try:
        rows = cli_list(limit=400)
    except Exception:
        rows = []
    for row in rows + [{"root": t.get("root"), "updated": t.get("heartbeat")} for t in _remote().list_terminals()]:
        root = str(row.get("root") or "")
        if not root:
            continue
        item = seen.get(root)
        if item is None:
            if not Path(root).is_dir():
                continue
            item = seen[root] = {"path": root, "name": Path(root).name or root, "sessions": 0, "updated": 0.0}
        item["sessions"] += 1
        item["updated"] = max(item["updated"], float(row.get("updated") or 0))
    out = sorted(seen.values(), key=lambda item: -item["updated"])
    return out[:limit]

def _drives() -> List[str]:
    if os.name != "nt":
        return []
    return [f"{letter}:\\" for letter in _string.ascii_uppercase if os.path.isdir(f"{letter}:\\")]

def browse_folders(path: str = "", *, hidden: bool = False) -> Dict[str, Any]:
    """Sub-folders of ``path`` for the «new session» folder picker (folders only, never file contents)."""
    home = str(Path.home())
    if not str(path or "").strip():
        return {"path": "", "parent": "", "home": home, "drives": _drives(),
                "dirs": [{"name": item["name"], "path": item["path"], "recent": True} for item in recent_projects(12)]}
    folder = Path(str(path)).expanduser()
    if not folder.is_absolute():
        raise ValueError("absolute path expected")
    folder = folder.resolve()
    if not folder.is_dir():
        raise LookupError("folder not found")
    dirs: List[Dict[str, Any]] = []
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                if len(dirs) >= MAX_DIR_ENTRIES:
                    break
                try:
                    if not entry.is_dir():
                        continue
                except OSError:
                    continue
                if not hidden and (entry.name.startswith(".") or entry.name.startswith("$")):
                    continue
                dirs.append({"name": entry.name, "path": str(folder / entry.name)})
    except PermissionError:
        raise PermissionError("no access") from None
    dirs.sort(key=lambda item: item["name"].lower())
    parent = str(folder.parent) if folder.parent != folder else ""
    return {"path": str(folder), "parent": parent, "home": home, "drives": _drives(), "dirs": dirs}

def make_folder(parent: str, name: str) -> str:
    name = str(name or "").strip()
    if not name or name in (".", "..") or re.search(r'[\\/:*?"<>|\x00-\x1f]', name) or len(name) > 120:
        raise ValueError("bad folder name")
    base = Path(str(parent or "")).expanduser()
    if not base.is_absolute() or not base.is_dir():
        raise LookupError("folder not found")
    target = base.resolve() / name
    target.mkdir(exist_ok=False)
    return str(target)

def root_of(session_id: str = "", rc: str = "") -> str:
    if rc:
        state = live_state(rc)
        if state and state.get("root"):
            return str(state["root"])
    if session_id:
        terminal = terminal_for(session_id)
        if terminal and terminal.get("root"):
            return str(terminal["root"])
        file = find_transcript(session_id)
        if file is not None:
            return session_root(file)
    return ""

def _inside(root: str, rel: str) -> Path:
    base = Path(root).resolve()
    rel = str(rel or "").replace("\\", "/").strip("/")
    if rel.startswith("/") or re.match(r"^[A-Za-z]:", rel) or "\x00" in rel:
        raise PermissionError("outside the project")
    target = (base / rel).resolve() if rel else base
    if target != base and base not in target.parents:
        raise PermissionError("outside the project")
    return target

def list_files(root: str, rel: str = "", *, hidden: bool = False) -> Dict[str, Any]:
    if not root or not Path(root).is_dir():
        raise LookupError("project folder is missing")
    folder = _inside(root, rel)
    if not folder.is_dir():
        raise LookupError("folder not found")
    entries: List[Dict[str, Any]] = []
    truncated = False
    with os.scandir(folder) as items:
        for entry in items:
            if len(entries) >= MAX_DIR_ENTRIES:
                truncated = True
                break
            if not hidden and entry.name in HIDDEN_DIRS:
                continue
            try:
                is_dir = entry.is_dir()
                stat = entry.stat()
            except OSError:
                continue
            entries.append({"name": entry.name, "dir": is_dir, "size": 0 if is_dir else stat.st_size,
                            "mtime": stat.st_mtime})
    entries.sort(key=lambda item: (not item["dir"], item["name"].lower()))
    base = Path(root).resolve()
    rel_path = "" if folder == base else folder.relative_to(base).as_posix()
    return {"root": str(base), "path": rel_path, "entries": entries, "truncated": truncated}

def read_file(root: str, rel: str) -> Dict[str, Any]:
    if not root or not Path(root).is_dir():
        raise LookupError("project folder is missing")
    target = _inside(root, rel)
    if not target.is_file():
        raise LookupError("file not found")
    size = target.stat().st_size
    suffix = target.suffix.lower()
    base = {"path": target.relative_to(Path(root).resolve()).as_posix(), "name": target.name, "size": size}
    if suffix in IMAGE_TYPES:
        if size > MAX_IMAGE_FILE:
            return {**base, "kind": "large"}
        import base64
        data = target.read_bytes()
        return {**base, "kind": "image", "data": f"data:{IMAGE_TYPES[suffix]};base64," + base64.b64encode(data).decode("ascii")}
    with target.open("rb") as handle:
        data = handle.read(MAX_TEXT_FILE + 1)
    if b"\x00" in data[:8192]:
        return {**base, "kind": "binary"}
    truncated = len(data) > MAX_TEXT_FILE
    text = data[:MAX_TEXT_FILE].decode("utf-8", "replace")
    return {**base, "kind": "text", "text": text, "truncated": truncated, "lang": suffix.lstrip(".")}

def git_changes(root: str) -> Dict[str, Any]:
    """``git status`` of the project (what the agent changed) — empty when it is not a repository."""
    if not root or not Path(root).is_dir():
        raise LookupError("project folder is missing")
    git = shutil.which("git")
    if not git:
        return {"git": False, "files": []}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        out = subprocess.run([git, "status", "--porcelain=v1", "-uall"], cwd=root, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=8, creationflags=flags)
    except (OSError, subprocess.TimeoutExpired):
        return {"git": False, "files": []}
    if out.returncode != 0:
        return {"git": False, "files": []}
    files = []
    for line in out.stdout.splitlines()[:400]:
        if len(line) > 3:
            files.append({"status": line[:2].strip() or "?", "path": line[3:].strip().strip('"')})
    return {"git": True, "files": files}

def file_diff(root: str, rel: str) -> str:
    git = shutil.which("git")
    if not git:
        return ""
    target = _inside(root, rel)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        out = subprocess.run([git, "diff", "--no-color", "HEAD", "--", str(target)], cwd=root, capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=8, creationflags=flags)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout[:200_000]

def rename_session(session_id: str, name: str) -> None:
    from .cli.session import rename_session as cli_rename
    file = find_transcript(session_id)
    if file is None:
        raise LookupError("session not found")
    name = " ".join(str(name or "").split())[:120]
    if not name:
        raise ValueError("empty name")
    root = session_root(file)
    if not root:
        raise LookupError("project folder is missing")
    cli_rename(Path(root), session_id, name)

def delete_session(session_id: str) -> None:
    from .cli.session import delete_session as cli_delete
    if terminal_for(session_id) is not None:
        raise RuntimeError("live")
    if any(job.kind == "code" and not job.done and job.meta.get("session_id") == session_id
           for job in jobs.jobs.values()):
        raise RuntimeError("busy")
    file = find_transcript(session_id)
    if file is None:
        raise LookupError("session not found")
    root = session_root(file)
    from .cli import paths
    if not root or paths.project_sessions(root).resolve() != file.parent.resolve():

        file.unlink()
        assets = file.with_suffix(".assets")
        if assets.is_dir():
            shutil.rmtree(assets, ignore_errors=True)
        return
    cli_delete(Path(root), session_id)

FALLBACK_COMMANDS = [
    ("help", "Все команды и горячие клавиши", "All commands and shortcuts", "general", True),
    ("model", "Выбрать модель", "Choose the model", "model", True),
    ("mode", "Режим разрешений", "Permission mode", "config", True),
    ("clear", "Начать новый разговор", "Start a new conversation", "session", False),
    ("compact", "Сжать историю, сохранив суть", "Compact the history", "context", False),
    ("context", "Что занимает контекст", "What fills the context", "context", True),
    ("cost", "Стоимость сессии", "Session cost", "info", True),
    ("usage", "Токены, время и стоимость", "Tokens, time and cost", "info", True),
    ("status", "Версия, модель, подключение", "Version, model, connection", "info", True),
    ("todos", "Список задач агента", "The agent's todo list", "session", True),
    ("diff", "Незакоммиченные изменения", "Uncommitted changes", "code", True),
    ("review", "Ревью текущих изменений", "Review the current changes", "code", False),
    ("init", "Изучить проект и создать MEMUCODE.md", "Study the project and write MEMUCODE.md", "code", False),
    ("test", "Запустить тесты и разобрать падения", "Run the tests and triage failures", "code", False),
    ("fix", "Найти причину ошибки и исправить", "Find the cause of a bug and fix it", "code", False),
    ("explain", "Объяснить код", "Explain code", "code", False),
    ("commit", "Сделать git-коммит", "Make a git commit", "code", False),
    ("plan", "Режим плана", "Plan mode", "code", False),
    ("goal", "Работать до достижения цели", "Work until the goal is reached", "code", False),
    ("btw", "Вопрос по ходу дела", "A side question", "agents", True),
    ("background", "Задача параллельно", "Run a task in parallel", "agents", True),
    ("search", "Поиск в интернете", "Search the web", "agents", True),
    ("fetch", "Прочитать веб-страницу", "Read a web page", "agents", True),
    ("think", "Режим размышлений вкл/выкл", "Toggle thinking", "model", True),
    ("effort", "Глубина рассуждений", "Reasoning effort", "model", True),
    ("rename", "Назвать разговор", "Name the conversation", "session", True),
    ("undo", "Отменить правки последнего ответа", "Undo the last answer's file changes", "session", False),
    ("rewind", "Вернуться к прошлому сообщению", "Rewind to an earlier message", "session", False),
    ("export", "Сохранить разговор в Markdown", "Export the conversation", "session", True),
    ("skills", "Скиллы", "Skills", "agents", True),
    ("mcp", "Серверы MCP", "MCP servers", "agents", True),
    ("tasks", "Фоновые задачи", "Background jobs", "info", True),
    ("memory", "Файлы памяти", "Memory files", "context", True),
]

def command_catalog(rc: str = "") -> Dict[str, Any]:
    folder = _remote_folder()
    candidates = []
    if rc and _remote().valid_rc_id(rc):
        candidates.append(folder / f"{rc}.commands.json")
    candidates.append(folder / "commands-cache.json")
    for file in candidates:
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("commands"), list) and data["commands"]:
            rows = [row for row in data["commands"] if isinstance(row, dict) and row.get("name")
                    and not blocked_prompt("/" + str(row["name"]))]
            return {"commands": rows[:400], "models": [str(x) for x in data.get("models") or []][:400],
                    "model": str(data.get("model") or ""), "source": "terminal" if file.name != "commands-cache.json" else "cache"}
    return {"commands": [{"name": name, "ru": ru, "en": en, "description": en, "category": category, "hint": "",
                          "aliases": [], "source": "", "while_busy": busy}
                         for name, ru, en, category, busy in FALLBACK_COMMANDS],
            "models": [], "model": "", "source": "builtin"}
