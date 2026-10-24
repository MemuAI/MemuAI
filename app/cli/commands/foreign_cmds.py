"""Sessions of other tools: ``/resume-claude``, ``/resume-codex``, ``/resume-grok``, ``/resume-gemini``,
``/resume-agy``, ``/resume-opencode``, ``/resume-cursor`` — and the shared steps ``/dashboard`` and ``/import`` use.

Continuing another tool's session means what a colleague would do: read the record, write down what was asked and
how it ended, check the real state, go on (see ``foreign/handoff.py``). The record is given to the model as data
that it must verify, never as instructions.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import List, Optional

from rich.text import Text

from .. import foreign
from ..foreign import ForeignSession, ReadError, SessionInfo, Source, handoff
from ..i18n import tr
from ..timefmt import ago
from ..tui import dialogs as dlg
from . import Context, command


def _article(title: str) -> str:
    """English article for a tool name: "an OpenCode session", "a Codex session"."""
    return "an" if title[:1].lower() in "aeiou" else "a"


def where_text(info: SessionInfo) -> str:
    return Path(info.cwd).name or info.cwd if info.cwd else tr("папка неизвестна", "folder unknown")


def session_option(info: SessionInfo, here: str = "") -> dlg.Option:
    """One row of a list of foreign sessions."""
    bits = [ago(info.updated), tr(f"{info.messages} шагов", f"{info.messages} steps") if info.messages else "", where_text(info),
            info.model, info.id[:8]]
    mark = "●" if here and foreign.same_folder(info.cwd, here) else ""
    return dlg.Option((info.title or info.id)[:90], info, " · ".join(bit for bit in bits if bit), hint=mark)


def order_rows(rows: List[SessionInfo], here: str) -> List[SessionInfo]:
    """Sessions of this project first, then by time."""
    return sorted(rows, key=lambda row: (not foreign.same_folder(row.cwd, here), -row.updated))


async def read_session(ctx: Context, info: SessionInfo) -> Optional[ForeignSession]:
    ctx.ui.status_text = tr("читаю сессию", "reading the session")
    try:
        return await asyncio.to_thread(foreign.read, info)
    except (ReadError, OSError) as exc:
        ctx.say(tr(f"Не удалось прочитать сессию: {exc}", f"Could not read the session: {exc}"), "error")
        return None
    finally:
        ctx.ui.status_text = ""


async def continue_from(ctx: Context, info: SessionInfo, *, request: str = "") -> bool:
    """Carry on a foreign session: read it, move to its folder when the person agrees, put the hand-over note at the
    head of a fresh conversation and let the model take it from there. False when nothing was started."""
    from .config_cmds import switch_folder
    runtime = ctx.runtime
    source = foreign.SOURCES[info.tool]
    session = await read_session(ctx, info)
    if session is None:
        return False
    if not session.turns:
        ctx.say(tr("В этой сессии нет сообщений.", "That session has no messages."), "warn")
        return False
    folder = Path(info.cwd) if info.cwd else None
    if folder and folder.is_dir() and not foreign.same_folder(str(folder), str(runtime.root)):
        if await ctx.confirm(tr(f"Сессия работала в {folder}. Перейти туда?", f"The session worked in {folder}. Switch there?"),
                             tr("Иначе MemuCode продолжит в текущей папке, но файлы из записи искать будет не там.",
                                "Otherwise MemuCode carries on in the current folder, where the files of the record are not.")):
            if await switch_folder(ctx, folder) is None:
                return False
    elif folder and not folder.is_dir():
        ctx.say(tr(f"Папки {folder} больше нет — продолжаю в {runtime.root}.", f"{folder} is gone — carrying on in {runtime.root}."), "warn")
    if runtime.session.messages:
        runtime.new_session()
    note = handoff.build(session, source_title=source.title)
    shown = handoff.display(session, source.title)
    runtime.session.set_meta(title="↪ " + (info.title or info.id)[:70], handoff={"tool": info.tool, "id": info.id})
    runtime.session.append({"role": "user", "kind": "summary", "content": note, "display": shown, "source": "handoff"})
    ctx.ui.context_cache = (-1, 0.0, 0)
    runtime.invalidate_prompt()
    ctx.say(shown, "success")
    if not source.verified:
        ctx.say(tr("Cursor читается по описанному формату и не проверялся на живых данных — если записка пустая или странная, "
                   "скажите: поправлю.", "Cursor is read from its documented format and was not checked against real data — if the "
                   "note looks empty or odd, say so."), "warn")
    wanted = request.strip()
    prompt = (f"The person's request now: {wanted}\n\nUse the handoff above as background; verify before you rely on it."
              if wanted else handoff.instruction(session))
    await ctx.prompt(prompt, display=wanted or tr("продолжай", "continue"))
    return True


async def pick_session(ctx: Context, source: Source, rows: List[SessionInfo], query: str) -> Optional[SessionInfo]:
    here = str(ctx.runtime.root)
    rows = order_rows(rows, here)
    if query:
        found = foreign.find(rows, query)
        if len(found) == 1:
            return found[0]
        if not found:
            ctx.say(tr(f"Нет сессии «{query}» — вот весь список.", f"No session matches “{query}” — here is the whole list."), "warn")
        else:
            rows = found
    options = [session_option(row, here) for row in rows]
    body = ctx.r.render(Text("  " + tr("● — сессии этого проекта. Enter — продолжить в MemuCode: записка о сессии станет началом нового "
                                      "разговора, а модель сверит записанное с тем, что есть на диске.",
                                      "● — sessions of this project. Enter continues it in MemuCode: a note about the session starts a new "
                                      "conversation and the model checks what it says against what is on disk."), style="dim"))
    return await ctx.select(tr(f"Продолжить сессию {source.title}", f"Continue {_article(source.title)} {source.title} session"), options, filterable=True,
                            max_visible=8, body=body)


async def resume_foreign(ctx: Context, source: Source) -> None:
    if not source.present():
        ctx.say(tr(f"Сессий {source.title} на этом компьютере не нашёл (искал в {source.where()}).",
                   f"No {source.title} sessions found on this computer (looked in {source.where()})."), "warn")
        return
    ctx.ui.status_text = tr(f"читаю список сессий {source.title}", f"reading the list of {source.title} sessions")
    try:
        rows = await asyncio.to_thread(foreign.discover, source.key, 600)
    finally:
        ctx.ui.status_text = ""
    if not rows:
        ctx.say(tr(f"У {source.title} нет сохранённых сессий.", f"{source.title} has no saved sessions."), "warn")
        return
    query = ctx.args.strip()
    request = ""
    first, _, rest = query.partition(" ")
    if rest.strip() and re.fullmatch(r"[\w-]{6,}", first) and foreign.find(rows, first) and \
            any(row.id.lower().startswith(first.lower()) for row in rows):
        query, request = first, rest.strip()                  # "<id> <what to do next>"
    info = await pick_session(ctx, source, rows, query)
    if info is not None:
        await continue_from(ctx, info, request=request)


def _register(source: Source) -> None:
    async def handler(ctx: Context) -> None:
        await resume_foreign(ctx, source)

    handler.__name__ = "resume_" + source.key
    command(source.command,
            f"Продолжить сессию {source.title} по её ID (или выбрать из списка)",
            f"Continue {_article(source.title)} {source.title} session by its ID (or pick it from a list)",
            hint="[id|words] [next request]", category="session", while_busy=False, immediate=True)(handler)


for _source in foreign.SOURCES.values():
    _register(_source)
