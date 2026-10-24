"""MemuCode «remote host»: the full interactive MemuCode, driven only from the Telegram Mini App.

``memucode remote-host --cwd <folder> [-r <id>] [-m <model>] [--permission-mode <mode>]``

Started by the MemuAI Proxy (``app/tgapp_code.start_host``) when the phone opens a session that is not
running in any terminal, or creates a new one. It runs the real ``TerminalUI`` with a pipe as the keyboard
and a dummy screen, so *everything* behaves exactly as in a terminal — slash commands (``/help``,
``/model``, ``/compact``…), skills, permission dialogs with the exact tool call, plan approval, Esc,
the queue — and publishes itself through ``app/cli/remote.py`` like any open terminal. The phone therefore
drives it with the same protocol (prompt / interrupt / answer) plus ``close``.

What the terminal prints (the output of ``/help``, ``/cost``, notices) is not part of the session
transcript; ``remote_extras()`` publishes the latest of those lines as ``notes`` (for host *and* real
terminals) and writes the command catalogue (``<rc>.commands.json``) used for auto-complete on the phone.

The host exits by itself after ``--idle-minutes`` without remote commands while idle, when the Mini App or
its MemuCode control is switched off, or on the ``close`` command.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

NOTES_LIMIT = 40
NOTE_CHARS = 6000
NOTE_WIDTH = 96
CATALOG_EVERY = 60.0
DEFAULT_IDLE_MINUTES = 30
# Lines the remote control itself prints into the terminal: noise on the phone.
_SKIP_NOTES = ("Сообщение из Telegram", "Message from Telegram",
               # the phone marks a stopped turn itself («Прервано пользователем» / "Interrupted by the user")
               "Interrupted · ", "Прервано · ")


# --------------------------------------------------------------------------- published extras

def _block_text(block: Any) -> str:
    from .remote import strip_ansi
    source = getattr(block, "source", "")
    try:
        text = source(NOTE_WIDTH, True) if callable(source) else str(source or "")
    except Exception:
        return ""
    lines = [line.rstrip() for line in strip_ansi(text).splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    text = "\n".join(lines)
    return text if len(text) <= NOTE_CHARS else text[:NOTE_CHARS] + "\n…"


def notes_feed(ui: Any) -> List[Dict[str, Any]]:
    """The latest notices printed in the terminal (command output, warnings), oldest first.

    Every notice gets a sequence number and the time it was first published, so the phone can merge
    them with the transcript and skip what it already has. Rendered text is cached per block version."""
    transcript = getattr(ui, "transcript", None)
    blocks = list(getattr(transcript, "blocks", []) or [])
    if not blocks:
        return []
    state = getattr(ui, "_remote_notes", None)
    if state is None:
        state = {"seq": 0, "cache": {}}
        with contextlib.suppress(Exception):
            ui._remote_notes = state
    picked: List[Any] = []
    for block in reversed(blocks):
        if getattr(block, "kind", "") != "notice":
            continue
        picked.append(block)
        if len(picked) >= NOTES_LIMIT:
            break
    out: List[Dict[str, Any]] = []
    cache: Dict[int, Any] = state["cache"]
    alive = set()
    for block in reversed(picked):
        key = id(block)
        alive.add(key)
        version = getattr(block, "version", 0)
        cached = cache.get(key)
        if cached is None or cached[0] is not block or cached[1] != version:
            seq = cached[2] if cached is not None and cached[0] is block else None
            if seq is None:
                state["seq"] += 1
                seq = state["seq"]
            ts = cached[3] if cached is not None and cached[0] is block else time.time()
            cached = (block, version, seq, ts, _block_text(block))
            cache[key] = cached
        # «⎿» (the terminal's tree corner) is missing from phone fonts and showed as a box.
        text = cached[4].lstrip().removeprefix("⎿").lstrip(" ").rstrip() if cached[4].lstrip().startswith("⎿") else cached[4]
        if not text.strip() or text.lstrip("·•! ").startswith(_SKIP_NOTES):
            continue
        out.append({"seq": cached[2], "ts": round(cached[3], 3), "text": text})
    for key in [key for key in cache if key not in alive]:
        cache.pop(key, None)
    return out


def command_catalog(ui: Any) -> Dict[str, Any]:
    """Slash commands, custom commands and skills of this terminal + the models it can use."""
    rows: List[Dict[str, Any]] = []
    with contextlib.suppress(Exception):
        from .commands import REGISTRY
        categories = {}
        for cmd in REGISTRY.values():
            categories[cmd.name] = cmd.category
        for item in ui.commands.items():
            name = str(getattr(item, "name", "") or "")
            if not name:
                continue
            command = REGISTRY.get(name)
            rows.append({"name": name, "description": str(getattr(item, "description", "") or "")[:300],
                         "hint": str(getattr(item, "hint", "") or "")[:80],
                         "aliases": [str(x) for x in (getattr(item, "aliases", ()) or ())][:6],
                         "source": str(getattr(item, "source", "") or ""),
                         "category": categories.get(name, "custom" if not command else "general"),
                         "ru": command.ru[:300] if command else "", "en": command.en[:300] if command else "",
                         "while_busy": bool(command.while_busy) if command else False})
    models: List[str] = []
    with contextlib.suppress(Exception):
        models = [str(row.id) for row in ui.runtime.router.cached_models()][:400]
    return {"commands": rows, "models": models, "model": str(getattr(ui.runtime, "model", "") or ""),
            "updated": time.time()}


def remote_extras(control: Any) -> Dict[str, Any]:
    """Extra snapshot fields of a published terminal (called from ``RemoteControl.snapshot``)."""
    ui = control.ui
    out: Dict[str, Any] = {"notes": notes_feed(ui)}
    host = getattr(ui, "_remote_host", None)
    if host:
        out["host"] = True
        out["host_nonce"] = str(host.get("nonce") or "")
        out["idle_minutes"] = host.get("idle_minutes")
    now = time.monotonic()
    if now - float(getattr(control, "_catalog_at", 0) or 0) > CATALOG_EVERY:
        with contextlib.suppress(Exception):
            control._catalog_at = now
            catalog = command_catalog(ui)
            if catalog["commands"]:
                from .remote import _atomic_write
                _atomic_write(control.folder / f"{control.id}.commands.json", catalog)
                _atomic_write(control.folder / "commands-cache.json", catalog)
    return out


def drop_catalog(control: Any) -> None:
    with contextlib.suppress(OSError):
        (control.folder / f"{control.id}.commands.json").unlink()


# --------------------------------------------------------------------------- the host process

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memucode remote-host", add_help=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("-r", "--resume", default="")
    parser.add_argument("-m", "--model", default="")
    parser.add_argument("--permission-mode", default="", choices=["", "default", "acceptEdits", "plan", "auto",
                                                                    "bypassPermissions"])
    parser.add_argument("--idle-minutes", type=int, default=DEFAULT_IDLE_MINUTES)
    parser.add_argument("--lang", default="")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    root = Path(args.cwd).expanduser().resolve()
    if not root.is_dir():
        print(f"memucode remote-host: folder not found: {root}", file=sys.stderr)
        return 2
    from .remote import tgapp_allows
    if not tgapp_allows():
        print("memucode remote-host: the Mini App or its MemuCode control is off", file=sys.stderr)
        return 3
    os.chdir(root)
    try:
        return asyncio.run(_host(root, args))
    except KeyboardInterrupt:
        return 130


def _session(root: Path, resume: str):
    from .session import Session
    if resume:
        import re
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", resume):
            raise SystemExit(2)
        return Session.load(root, resume), True
    return Session(root), False


async def _host(root: Path, args: argparse.Namespace) -> int:
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    from .i18n import set_language
    from .main import _startup, _trust
    from .remote import attach, detach, tgapp_allows
    from .runtime import Runtime
    from .settings import Settings
    from .tui.app import TerminalUI

    overrides: Dict[str, Any] = {"onboarded": True}
    if args.model:
        overrides["model"] = args.model
    if args.permission_mode:
        overrides["permission_mode"] = args.permission_mode
    settings = Settings(root, overrides)
    set_language(args.lang or str(settings.get("language") or ""))
    session, _resumed = _session(root, args.resume)
    if session.root != root:
        settings = Settings(session.root, overrides)
        os.chdir(session.root)
    session.ensure_saved()                       # listed and resumable at once, even before the first message
    runtime = Runtime(session.root, settings, session=session, model=args.model)
    ui = TerminalUI(runtime)
    ui._remote_host = {"nonce": os.environ.get("MEMUCODE_REMOTE_HOST_NONCE", "")[:64],
                       "idle_minutes": max(1, int(args.idle_minutes or DEFAULT_IDLE_MINUTES)),
                       "last": time.monotonic()}
    trust_needed = not settings.is_trusted(runtime.root) and runtime.root != Path.home().resolve()

    async def watchdog(terminal: Any) -> None:
        """Leave when nobody drives this host any more."""
        off_since = 0.0
        while True:
            await asyncio.sleep(5)
            host = terminal._remote_host
            now = time.monotonic()
            if not tgapp_allows():
                off_since = off_since or now
                if now - off_since > 60:
                    terminal.exit(0)
                    return
            else:
                off_since = 0.0
            if terminal.busy or getattr(terminal, "dialogs", None) or terminal.queue:
                host["last"] = now
                continue
            with contextlib.suppress(Exception):
                if terminal.runtime.jobs.running() or terminal.runtime.shells.running():
                    host["last"] = now
                    continue
            if now - host["last"] > host["idle_minutes"] * 60:
                terminal.exit(0)
                return

    async def after_start(terminal: Any) -> None:
        terminal.connecting = True
        terminal.start(_startup(terminal), busy=False)
        terminal.remote_control = attach(terminal)
        terminal.side_tasks.append(asyncio.ensure_future(watchdog(terminal)))
        if trust_needed:
            await _trust(terminal)

    sink = open(os.devnull, "w", encoding="utf-8")
    original = sys.stdout
    sys.stdout = sink                              # nothing may draw on the (absent) console
    try:
        with create_pipe_input() as keys:
            code = await ui.run(after_start=after_start, input=keys, output=DummyOutput())
    finally:
        sys.stdout = original
        with contextlib.suppress(Exception):
            sink.close()
    control = getattr(ui, "remote_control", None)
    if control is not None:
        drop_catalog(control)
    await detach(control)
    await runtime.shutdown()
    with contextlib.suppress(Exception):
        runtime.session.ensure_saved()
    return code


def mark_activity(ui: Any) -> None:
    host = getattr(ui, "_remote_host", None)
    if host:
        host["last"] = time.monotonic()


def close_host(ui: Any) -> None:
    """The ``close`` remote command: only a host may be closed from the phone."""
    if not getattr(ui, "_remote_host", None):
        raise ValueError("only a phone-hosted session can be closed remotely")
    if getattr(ui, "busy", False):
        with contextlib.suppress(Exception):
            ui.interrupt()
    ui.exit(0)


def describe() -> str:
    return json.dumps({"protocol": "memucode-remote-host", "version": 1})
