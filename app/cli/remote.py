"""Remote control of a running MemuCode CLI terminal (MemuAI Telegram Mini App).

A terminal cannot be "attached to" from another process, so an interactive
MemuCode session publishes itself instead, through plain files in the
MemuCode data folder (``%APPDATA%\\MemuAI\\memucode\\remote``):

* ``<rc>.json``   — the terminal's live state, rewritten when it changes and at
  least every ``HEARTBEAT`` seconds: session id, folder, model, busy flag, the
  answer being streamed right now, running tools, the open question/permission
  dialog with the *exact* tool call, and acknowledgements of remote commands;
* ``<rc>.inbox/`` — commands from the Mini App, one JSON file each (written
  atomically: ``*.tmp`` then rename). The terminal polls the folder, runs every
  command inside its own event loop exactly as if it had been typed, deletes
  the file and acknowledges it in its state.

Commands: ``prompt`` (text or a slash command, queued when the agent is busy),
``interrupt`` (Esc), ``answer`` (pick an option of the open dialog, optionally
with text). Commands older than ``MAX_COMMAND_AGE`` are dropped unexecuted.

Publishing only happens while the Mini App is enabled with MemuCode control
(``memuai/tgapp.json``: ``enabled`` and ``code_control``) and can be switched
off for one terminal with ``MEMUCODE_NO_REMOTE=1``. The folder is inside the
user's own profile; the Mini App server writes commands only for an
authenticated, paired Telegram user.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import paths

HEARTBEAT = 2.0
POLL = 0.35
STALE_AFTER = 12.0
MAX_COMMAND_AGE = 120.0
MAX_STREAM = 6000
PROTOCOL = 1

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)")
_RC_ID = re.compile(r"^[0-9]{1,10}-[0-9a-f]{8}$")


def remote_dir() -> Path:
    path = paths.home() / "remote"
    path.mkdir(parents=True, exist_ok=True)
    return path


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", str(text or ""))


def valid_rc_id(value: Any) -> bool:
    return bool(_RC_ID.match(str(value or "")))


def tgapp_allows() -> bool:
    """The Mini App is on and may drive MemuCode (read without importing the Proxy side)."""
    if os.environ.get("MEMUCODE_NO_REMOTE", "").strip() not in ("", "0"):
        return False
    try:
        data = json.loads((Path(paths.memu_dir()) / "tgapp.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("enabled") is True and data.get("code_control") is not False


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    tmp = path.with_name(path.name + f".{secrets.token_hex(3)}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------------------- server side helpers

def list_terminals() -> List[Dict[str, Any]]:
    """Live terminals (fresh heartbeat). Stale files of crashed terminals are removed."""
    rows: List[Dict[str, Any]] = []
    folder = remote_dir()
    now = time.time()
    for file in folder.glob("*.json"):
        if not valid_rc_id(file.stem):
            continue
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        if now - float(data.get("heartbeat") or 0) > STALE_AFTER:
            if now - float(data.get("heartbeat") or 0) > 3600:
                with contextlib.suppress(OSError):
                    file.unlink()
                inbox = folder / (file.stem + ".inbox")
                if inbox.is_dir():
                    for item in inbox.iterdir():
                        with contextlib.suppress(OSError):
                            item.unlink()
                    with contextlib.suppress(OSError):
                        inbox.rmdir()
            continue
        data["rc"] = file.stem
        rows.append(data)
    rows.sort(key=lambda row: -float(row.get("started") or 0))
    return rows


def terminal_state(rc_id: str) -> Optional[Dict[str, Any]]:
    if not valid_rc_id(rc_id):
        return None
    file = remote_dir() / f"{rc_id}.json"
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or time.time() - float(data.get("heartbeat") or 0) > STALE_AFTER:
        return None
    data["rc"] = rc_id
    return data


def send_command(rc_id: str, command: Dict[str, Any]) -> str:
    """Drop a command into a terminal's inbox; returns its id (acknowledged in the state)."""
    if not valid_rc_id(rc_id):
        raise ValueError("bad terminal id")
    if terminal_state(rc_id) is None:
        raise LookupError("terminal is not running")
    inbox = remote_dir() / f"{rc_id}.inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    if len(list(inbox.glob("*.json"))) > 50:
        raise RuntimeError("terminal inbox is full")
    command_id = f"{int(time.time() * 1000)}-{secrets.token_hex(4)}"
    payload = dict(command)
    payload.update({"id": command_id, "ts": time.time(), "v": PROTOCOL})
    _atomic_write(inbox / f"{command_id}.json", payload)
    return command_id


# --------------------------------------------------------------------------- terminal side

def _option_rows(dialog: Any) -> List[Dict[str, Any]]:
    rows = []
    for index, option in enumerate(getattr(dialog, "options", []) or []):
        value = getattr(option, "value", None)
        rows.append({"index": index, "label": strip_ansi(str(getattr(option, "label", "")))[:300],
                     "description": strip_ansi(str(getattr(option, "description", "") or ""))[:300],
                     "text_input": bool(getattr(option, "text_input", False)),
                     "cancel": value is None, "danger": "danger" in str(getattr(option, "style", "") or ""),
                     "value": value if isinstance(value, (str, int, float, bool)) or value is None else str(value)})
    return rows


def _tool_detail(request: Any) -> Dict[str, Any]:
    """The exact call a permission dialog is about (what the phone must show)."""
    try:
        call = getattr(request, "call", None)
        tool = getattr(request, "tool", None)
        prepared = getattr(request, "prepared", None)
        args = getattr(call, "args", None)
        try:
            args_text = json.dumps(args, ensure_ascii=False, indent=2, default=str)
        except (TypeError, ValueError):
            args_text = str(args)
        return {
            "tool": str(getattr(tool, "name", "") or getattr(call, "name", "")),
            "kind": str(getattr(tool, "kind", "") or ""),
            "args": args_text[:20000],
            "preview": strip_ansi(str(getattr(prepared, "preview", "") or ""))[:20000],
            "diff": strip_ansi(str(getattr(prepared, "diff", "") or ""))[:40000],
            "reason": strip_ansi(str(getattr(request, "reason", "") or ""))[:1000],
            "rule": str(getattr(request, "key", "") or "")[:500],
            "suggestion": strip_ansi(str(getattr(request, "suggestion_label", "") or ""))[:300],
            "danger": bool(getattr(request, "danger", False)),
        }
    except Exception:
        return {}


class RemoteControl:
    """Publishes one terminal and executes the Mini App's commands in it."""

    def __init__(self, ui: Any) -> None:
        self.ui = ui
        self.id = f"{os.getpid()}-{secrets.token_hex(4)}"
        self.started = time.time()
        self.folder = remote_dir()
        self.file = self.folder / f"{self.id}.json"
        self.inbox = self.folder / f"{self.id}.inbox"
        self.task: Optional[asyncio.Task] = None
        self.registered = False
        self.acks: List[Dict[str, Any]] = []
        self.dialog_ids: Dict[int, int] = {}
        self._dialog_seq = 0
        self._last_payload = ""
        self._last_write = 0.0
        self._last_check = 0.0
        self._allowed = False

    # lifecycle ------------------------------------------------------------
    def start(self) -> None:
        if self.task is None or self.task.done():
            self.task = asyncio.ensure_future(self._loop())

    async def stop(self) -> None:
        task, self.task = self.task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._unregister()

    def _unregister(self) -> None:
        self.registered = False
        with contextlib.suppress(OSError):
            self.file.unlink()
        with contextlib.suppress(OSError):
            (self.folder / f"{self.id}.commands.json").unlink()
        if self.inbox.is_dir():
            for item in self.inbox.iterdir():
                with contextlib.suppress(OSError):
                    item.unlink()
            with contextlib.suppress(OSError):
                self.inbox.rmdir()

    async def _loop(self) -> None:
        while True:
            try:
                now = time.monotonic()
                if now - self._last_check > 5 or not self.registered:
                    self._allowed = tgapp_allows()
                    self._last_check = now
                if not self._allowed:
                    if self.registered:
                        self._unregister()
                    await asyncio.sleep(5)
                    continue
                if not self.registered:
                    self.inbox.mkdir(parents=True, exist_ok=True)
                    self.registered = True
                    self._last_payload = ""
                await self._drain_inbox()
                self.publish()
            except asyncio.CancelledError:
                raise
            except Exception:
                with contextlib.suppress(Exception):
                    import traceback
                    self.ui.runtime.log_error(traceback.format_exc())
            await asyncio.sleep(POLL)

    # state ----------------------------------------------------------------
    def _dialog_id(self, dialog: Any) -> int:
        key = id(dialog)
        if key not in self.dialog_ids:
            self._dialog_seq += 1
            self.dialog_ids[key] = self._dialog_seq
            if len(self.dialog_ids) > 64:
                for old in list(self.dialog_ids)[:32]:
                    self.dialog_ids.pop(old, None)
        return self.dialog_ids[key]

    def snapshot(self) -> Dict[str, Any]:
        ui = self.ui
        runtime = ui.runtime
        session = runtime.session
        dialog = None
        if getattr(ui, "dialogs", None):
            top = ui.dialogs[0]
            request = getattr(top, "remote_request", None)
            dialog = {
                "id": self._dialog_id(top), "title": strip_ansi(str(top.title))[:500],
                "body": strip_ansi(str(top.body or ""))[-12000:], "options": _option_rows(top),
                "multi": bool(top.multi), "danger": bool(top.danger), "agent": bool(getattr(top, "guard", False)),
                "kind": "permission" if request is not None else "question",
                "detail": _tool_detail(request) if request is not None else None,
            }
        tools = []
        for call in list(getattr(ui, "active_tools", {}).values())[:8]:
            live = getattr(call, "live", []) or []
            tools.append({"id": str(getattr(call, "id", "")), "name": str(getattr(call, "name", "")),
                          "display": strip_ansi(str(getattr(call, "display", "") or ""))[:400],
                          "live": strip_ansi("".join(str(x) for x in live[-20:]))[-1500:],
                          "seconds": round(time.monotonic() - float(getattr(call, "started", 0) or time.monotonic()), 1)})
        stream = str(getattr(ui, "stream", "") or "")
        reasoning = str(getattr(ui, "reasoning", "") or "")
        busy = bool(getattr(ui, "busy", False))
        return {
            "v": PROTOCOL, "rc": self.id, "pid": os.getpid(), "started": self.started,
            "session_id": str(getattr(session, "id", "")), "root": str(getattr(runtime, "root", "")),
            "title": str(session.title() or "")[:200] if hasattr(session, "title") else "",
            "model": str(getattr(runtime, "model", "") or ""),
            "permission_mode": str(getattr(getattr(runtime, "permissions", None), "mode", "") or ""),
            "busy": busy, "connecting": bool(getattr(ui, "connecting", False)),
            "status": strip_ansi(str(getattr(ui, "status_text", "") or ""))[:200],
            "busy_for": round(time.monotonic() - float(getattr(ui, "busy_started", 0) or 0), 1) if busy else 0,
            "stream": stream[-MAX_STREAM:], "stream_len": len(stream),
            "reasoning": reasoning[-1500:] if busy else "",
            "tools": tools, "queue": len(getattr(ui, "queue", []) or []),
            "todos": [{"content": str(row.get("content") or row.get("activeForm") or "")[:200],
                       "status": str(row.get("status") or "")}
                      for row in (getattr(session, "todos", None) or [])[:30] if isinstance(row, dict)],
            "dialog": dialog, "acks": self.acks[-20:],
            **self._extras(),
        }

    def _extras(self) -> Dict[str, Any]:
        """Round 29: printed notices, the command catalogue and the phone-host flag (``remote_host.py``)."""
        try:
            from .remote_host import remote_extras
            return remote_extras(self)
        except Exception:
            return {}

    def publish(self, *, force: bool = False) -> None:
        payload = self.snapshot()
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        now = time.time()
        if not force and body == self._last_payload and now - self._last_write < HEARTBEAT:
            return
        payload["heartbeat"] = now
        _atomic_write(self.file, payload)
        self._last_payload = body
        self._last_write = now

    # commands -------------------------------------------------------------
    async def _drain_inbox(self) -> None:
        if not self.inbox.is_dir():
            return
        for file in sorted(self.inbox.glob("*.json")):
            try:
                command = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                command = None
            with contextlib.suppress(OSError):
                file.unlink()
            if not isinstance(command, dict):
                continue
            ok, error = False, ""
            try:
                if time.time() - float(command.get("ts") or 0) > MAX_COMMAND_AGE:
                    raise ValueError("command expired")
                self.execute(command)
                ok = True
            except Exception as exc:
                error = str(exc)[:300] or type(exc).__name__
            self.acks.append({"id": str(command.get("id") or "")[:40], "ok": ok, "error": error, "ts": time.time()})
            self.acks = self.acks[-40:]
            self.publish(force=True)

    def execute(self, command: Dict[str, Any]) -> None:
        kind = str(command.get("type") or "")
        ui = self.ui
        if kind == "prompt":
            text = str(command.get("text") or "").strip()
            if not text:
                raise ValueError("empty prompt")
            if len(text) > 100_000:
                raise ValueError("prompt too long")
            self._submit(text)
        elif kind == "interrupt":
            if getattr(ui, "dialogs", None):
                ui.dialogs[0].resolve(None)
            ui.interrupt()
        elif kind == "answer":
            self._answer(command)
        elif kind == "close":
            from .remote_host import close_host
            close_host(ui)
        else:
            raise ValueError("unknown command")
        with contextlib.suppress(Exception):
            from .remote_host import mark_activity
            mark_activity(ui)

    def _submit(self, text: str) -> None:
        """What Enter in the input box does with this text (see ``TerminalUI.submit``)."""
        ui = self.ui
        if text.lstrip().startswith("!"):
            # «!команда» в терминале выполняется сразу, без диалога разрешений и без
            # списка запрещённых команд. С телефона это обходило бы подтверждение
            # опасных действий, поэтому удалённо такая строка не выполняется:
            # попросите агента обычным сообщением — команда пройдёт через разрешения.
            with contextlib.suppress(Exception):
                from .i18n import tr
                ui.say(tr("«!команда» из Telegram не выполняется: попросите агента обычным сообщением, "
                          "команда пройдёт через диалог разрешений.",
                          "“!command” from Telegram is not run: ask the agent in a normal message so the "
                          "command goes through the permission dialog."))
            return
        with contextlib.suppress(Exception):
            ui.history.add(text)
        with contextlib.suppress(Exception):
            from .i18n import tr
            ui.say(tr("Сообщение из Telegram (MemuAI Mini App)", "Message from Telegram (MemuAI Mini App)"))
        if ui.busy:
            if ui._while_busy(text):
                return
            ui.queue.append({"content": ui._content(text, []), "display": text, "shown": text})
            ui.invalidate()
            return
        ui.dispatch(text)

    def _answer(self, command: Dict[str, Any]) -> None:
        ui = self.ui
        if not getattr(ui, "dialogs", None):
            raise LookupError("no open question")
        dialog = ui.dialogs[0]
        if int(command.get("dialog") or 0) != self._dialog_id(dialog):
            raise LookupError("the question has changed")
        options = list(dialog.options or [])
        text = str(command.get("text") or "").strip()
        if dialog.multi:
            picked = [options[int(i)].value for i in command.get("choices") or []
                      if isinstance(i, int) and 0 <= i < len(options)]
            if not picked and not getattr(dialog, "allow_empty", False):
                raise ValueError("nothing selected")
            dialog.resolve(picked)
            return
        index = command.get("choice")
        if not isinstance(index, int) or not 0 <= index < len(options):
            raise ValueError("bad option")
        option = options[index]
        if getattr(option, "text_input", False):
            if not text:
                raise ValueError("text required")
            dialog.resolve({"value": option.value, "text": text})
            return
        if getattr(dialog, "remote_request", None) is not None and getattr(dialog.remote_request, "danger", False) \
                and option.value not in (None, "no", "feedback") and command.get("confirm") is not True:
            raise PermissionError("dangerous action needs confirmation")
        dialog.resolve(option.value)


def attach(ui: Any) -> Optional[RemoteControl]:
    """Start publishing this terminal (never raises: remote control is optional)."""
    try:
        control = RemoteControl(ui)
        control.start()
        return control
    except Exception:
        return None


async def detach(control: Optional[RemoteControl]) -> None:
    if control is not None:
        with contextlib.suppress(Exception):
            await control.stop()
