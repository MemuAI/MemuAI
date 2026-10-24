"""Editing text from inside MemuCode without losing the screen.

The interface itself has a multi-line text field (Ctrl+S saves), so by default nothing leaves the terminal.
An editor the user *chose* (``editor`` in the settings, ``$VISUAL`` or ``$EDITOR``) is used too, but carefully:

* a **terminal editor** (vim, nano, micro…) takes over the terminal for a while, as always;
* a **window editor** (VS Code, Notepad, Sublime…) opens its own window. The interface stays on screen and says
  it is waiting — before, it handed the terminal over and showed nothing until the editor was closed.
"""
from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

from rich.text import Text

from .i18n import tr

TERMINAL_EDITORS = {"vim", "vi", "nvim", "nano", "micro", "emacs", "hx", "helix", "joe", "ne", "pico", "kak", "mcedit",
                    "view", "vimx", "gvim-nofork"}


def configured_editor(settings: Any) -> str:
    """The editor the user asked for, or "" (then the built-in field is used)."""
    return str((settings.get("editor") if settings is not None else "") or os.environ.get("VISUAL")
               or os.environ.get("EDITOR") or "").strip()


def is_terminal_editor(command: str) -> bool:
    try:
        words = shlex.split(command, posix=False)
    except ValueError:
        words = command.split()
    name = Path(words[0].strip('"')).stem.lower() if words else ""
    return name in TERMINAL_EDITORS


def _command_line(command: str, path: Path) -> str:
    # "code" is a .cmd shim on Windows and needs --wait to block until the window closes.
    low = command.lower()
    wait = "" if "--wait" in low or "-w" in low.split() or is_terminal_editor(command) else (
        " --wait" if Path(command.split()[0].strip('"')).stem.lower() in ("code", "cursor", "windsurf", "codium", "subl", "sublime_text") else "")
    return f'{command}{wait} "{path}"'


async def open_external(ui: Any, path: Path, command: str) -> bool:
    """Edit ``path`` in the user's editor and return when it is closed. False when the editor could not start."""
    first = shlex.split(command, posix=False)[0].strip('"') if command.strip() else ""
    if not first or (os.name != "nt" and not shutil.which(first)):
        return False
    line = _command_line(command, path)
    if is_terminal_editor(command):
        from prompt_toolkit.application import run_in_terminal
        await run_in_terminal(lambda: subprocess.call(line, shell=True), in_executor=True)
        return True
    from .tui import dialogs as dlg
    dialog = dlg.Dialog(title=tr("Редактор открыт в отдельном окне", "The editor is open in its own window"),
                        options=[dlg.Option(tr("Закончил — вернуться", "Done — carry on"), "done")],
                        body=ui.renderer.render(Text(
                            "  " + tr(f"Правьте файл в окне редактора и закройте его (или нажмите Enter здесь):\n  {path}",
                                      f"Edit the file in the editor window and close it (or press Enter here):\n  {path}"),
                            style="dim")))
    task = asyncio.ensure_future(asyncio.to_thread(subprocess.call, line, shell=True))
    waiting = asyncio.ensure_future(ui.open_dialog(dialog))
    try:
        await asyncio.wait({task, waiting}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        if not waiting.done():
            dialog.resolve(None)
            try:
                await waiting
            except Exception:
                pass
    return True


async def edit_file(ctx: Any, path: Path, *, title: str = "", create: str = "") -> bool:
    """Edit a text file: in the user's editor when one is configured, else in the interface's own field."""
    ui = ctx.ui
    command = configured_editor(ctx.settings)
    if command and await open_external(ui, path, command):
        return True
    try:
        text = path.read_text(encoding="utf-8") if path.exists() else create
    except OSError as exc:
        ctx.say(str(exc), "error")
        return False
    body = ui.renderer.render(Text(
        "  " + tr("Ctrl+S — сохранить, Enter — новая строка, Esc — отмена", "Ctrl+S saves, Enter starts a new line, Esc cancels")
        + f"\n  {path}", style="dim"))
    edited = await ui.prompt_text(title or path.name, body=body, default=text, tag="paste")
    if edited is None:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(edited if edited.endswith("\n") or not edited else edited + "\n", encoding="utf-8")
    return True
