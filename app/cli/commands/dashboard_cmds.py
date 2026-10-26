"""``/dashboard`` — every earlier session in one place.

``/resume`` lists the conversations of this project. The dashboard lists *all* of them: MemuCode's own, in every
project, and — one tab each — the sessions of the other tools found on this computer (Claude Code, Codex, Grok,
Gemini CLI, Antigravity, OpenCode, Cursor). Enter on a row opens a short menu: open or continue it, rename,
export, delete (MemuCode's own), or continue / import (another tool's).
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Dict, List

from rich.text import Text

from .. import foreign, paths
from ..foreign import SessionInfo
from ..i18n import plural, tr
from ..session import Session, delete_session, list_sessions, rename_session
from ..timefmt import ago
from ..tui import dialogs as dlg
from . import Context, command
from .foreign_cmds import continue_from, order_rows, session_option

OWN = "memucode"

def _own_option(row: Dict[str, Any], here: str) -> dlg.Option:
    title = row.get("name") or row.get("title") or row.get("first") or row["id"]
    bits = [ago(row.get("updated", 0)), plural(int(row.get("prompts") or 0), "запрос", "запроса", "запросов", "prompt"),
            Path(row["root"]).name if row.get("root") else "", row.get("model") or ""]
    if row.get("imported_from"):
        bits.append(tr("импорт: ", "imported: ") + str(row["imported_from"]))
    if row.get("forked_from"):
        bits.append(tr("ветка", "branch"))
    mark = "●" if row.get("root") and foreign.same_folder(row["root"], here) else ""
    last = f"\n{row['last'][:120]}" if row.get("last") else ""
    return dlg.Option(str(title)[:90], (OWN, row), " · ".join(bit for bit in bits if bit) + last, hint=mark)

def _summary_line(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return tr("Сохранённых разговоров пока нет.", "No saved conversations yet.")
    projects = {row.get("root") for row in rows if row.get("root")}
    prompts = sum(int(row.get("prompts") or 0) for row in rows)
    week = sum(1 for row in rows if time.time() - float(row.get("updated") or 0) < 7 * 86400)
    folders = len(projects)
    return tr(f"{plural(len(rows), 'разговор', 'разговора', 'разговоров')} в {folders} "
              f"{'проекте' if folders % 10 == 1 and folders % 100 != 11 else 'проектах'} · "
              f"{plural(prompts, 'запрос', 'запроса', 'запросов')} · за неделю: {week}",
              f"{plural(len(rows), '', '', '', 'conversation')} in {plural(folders, '', '', '', 'project')} · "
              f"{plural(prompts, '', '', '', 'prompt')} · this week: {week}")

async def load(ctx: Context) -> Dict[str, Any]:
    """``{"own": [...], "<tool>": [SessionInfo…]}`` — every list, read in parallel."""
    ctx.ui.status_text = tr("читаю сессии", "reading the sessions")
    try:
        sources = foreign.present_sources()
        results = await asyncio.gather(asyncio.to_thread(list_sessions, None, limit=800),
                                       *(asyncio.to_thread(foreign.discover, source.key, 600) for source in sources))
    finally:
        ctx.ui.status_text = ""
    data: Dict[str, Any] = {"own": results[0]}
    for source, rows in zip(sources, results[1:]):
        data[source.key] = rows
    return data

async def dashboard(ctx: Context, *, tab: str = OWN) -> None:
    here = str(ctx.runtime.root)
    while True:
        data = await load(ctx)
        sources = [source for source in foreign.present_sources() if data.get(source.key)]
        keys = [OWN] + [source.key for source in sources]
        titles = {OWN: "MemuCode", **{source.key: source.title for source in sources}}
        counts = {OWN: len(data["own"]), **{source.key: len(data[source.key]) for source in sources}}
        if tab not in keys:
            tab = OWN
        dialog = dlg.Dialog(title=tr("Все сессии", "All sessions"), options=[],
                            tabs=[f"{titles[key]} ({counts[key]})" for key in keys], filterable=True, max_visible=8,
                            footer=tr("Tab / ← → — вкладки · печатайте для поиска · Enter — действия · Esc — закрыть",
                                      "Tab / ← → switch tabs · type to filter · Enter for actions · Esc to close"))
        dialog.tab_index = keys.index(tab)

        def fill(index: int, dialog: dlg.Dialog = dialog) -> None:
            key = keys[index]
            if key == OWN:
                rows = sorted(data["own"], key=lambda row: (not (row.get("root") and foreign.same_folder(row["root"], here)),
                                                            -float(row.get("updated") or 0)))
                dialog.options = [_own_option(row, here) for row in rows]
                line = _summary_line(data["own"])
            else:
                rows = order_rows(data[key], here)
                dialog.options = [dlg.Option(opt.label, (key, opt.value), opt.description, hint=opt.hint)
                                  for opt in (session_option(row, here) for row in rows)]
                line = tr(f"{len(rows)} сессий {titles[key]} на этом компьютере. Enter — продолжить в MemuCode или импортировать.",
                          f"{len(rows)} {titles[key]} sessions on this computer. Enter to continue in MemuCode or import.")
                if not foreign.SOURCES[key].verified:
                    line += tr(" (читается по описанию формата, не проверено на живых данных)",
                               " (read from the documented format, not checked against real data)")
            dialog.body = ctx.r.render(Text("  " + line + "   ● = " + tr("этот проект", "this project"), style="dim"))
            dialog.view = list(range(len(dialog.options)))
            dialog.index = 0
            dialog.tab_index = index

        dialog.on_tab = fill
        fill(dialog.tab_index)
        picked = await ctx.ui.open_dialog(dialog)
        if not picked:
            return
        tab_key, payload = picked
        if tab_key == OWN:
            if await own_actions(ctx, payload):
                return
        elif await foreign_actions(ctx, payload):
            return
        tab = tab_key

async def own_actions(ctx: Context, row: Dict[str, Any]) -> bool:
    """The menu of one of MemuCode's own conversations. True when the dashboard should close."""
    from .session_cmds import _open, transcript_markdown
    runtime = ctx.runtime
    title = row.get("name") or row.get("title") or row["id"]
    current = row["id"] == runtime.session.id
    options = [dlg.Option(tr("Открыть и продолжить", "Open and continue"), "open") if not current else
               dlg.Option(tr("Это текущий разговор", "This is the current conversation"), "open")]
    options += [dlg.Option(tr("Переименовать…", "Rename…"), "rename"),
                dlg.Option(tr("Сохранить в Markdown", "Export to Markdown"), "export"),
                dlg.Option(tr("Удалить…", "Delete…"), "delete", tr("безвозвратно: файл разговора и его картинки", "for good: the conversation file and its pictures"),
                           style="dialog.danger"),
                dlg.Option(tr("Назад", "Back"), None)]
    choice = await ctx.select(str(title)[:100], options, body=ctx.r.render(Text(
        "  " + tr(f"Сессия {row['id']} · {row.get('root', '')}", f"Session {row['id']} · {row.get('root', '')}"), style="dim")))
    if choice == "open":
        if not current:
            await _open(ctx, row)
        return True
    if choice == "rename":
        name = await ctx.ask(tr("Название разговора", "Conversation name"), default=str(title))
        if name:
            try:
                rename_session(Path(row["root"]), row["id"], name.strip()[:120])
                ctx.say(tr(f"Переименовано: «{name.strip()}»", f"Renamed to “{name.strip()}”"), "success")
            except OSError as exc:
                ctx.say(str(exc), "error")
    elif choice == "export":
        try:
            session = Session.load(Path(row["root"]), row["id"])
        except (OSError, FileNotFoundError) as exc:
            ctx.say(str(exc), "error")
            return False
        import re
        safe = re.sub(r"[^\w.-]+", "-", session.title() or session.id).strip("-")[:60] or session.id
        target = paths.downloads_dir() / f"memucode-{safe}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(transcript_markdown(session), encoding="utf-8")
        ctx.say(tr(f"Сохранено: {target}", f"Saved: {target}"), "success")
    elif choice == "delete":
        if current:
            ctx.say(tr("Это текущий разговор — сначала начните другой (/clear).", "That is the current conversation — start another first (/clear)."), "warn")
        elif await ctx.confirm(tr(f"Удалить «{str(title)[:60]}»?", f"Delete “{str(title)[:60]}”?"),
                               tr("Файл разговора и его картинки будут удалены без возможности вернуть.",
                                  "The conversation file and its pictures are removed for good."), danger=True):
            try:
                delete_session(Path(row["root"]), row["id"])
                ctx.say(tr("Удалено", "Deleted"), "success")
            except OSError as exc:
                ctx.say(str(exc), "error")
    return False

async def foreign_actions(ctx: Context, info: SessionInfo) -> bool:
    """The menu of another tool's session. True when the dashboard should close."""
    source = foreign.SOURCES[info.tool]
    options = [dlg.Option(tr("Продолжить в MemuCode", "Continue in MemuCode"), "continue",
                          tr("записка о сессии станет началом нового разговора; модель проверит состояние и продолжит",
                             "a note about the session starts a new conversation; the model checks the state and carries on")),
               dlg.Option(tr("Импортировать как разговор MemuCode", "Import as a MemuCode conversation"), "import",
                          tr("сохранится в /resume и здесь; источник не меняется", "kept in /resume and here; the source is not changed")),
               dlg.Option(tr("Посмотреть запросы", "Look at the requests"), "look"),
               dlg.Option(tr("Назад", "Back"), None)]
    choice = await ctx.select(f"{source.title}: {(info.title or info.id)[:80]}", options, body=ctx.r.render(Text(
        "  " + f"{info.id} · {info.cwd or tr('папка неизвестна', 'folder unknown')}", style="dim")))
    if choice == "continue":
        return await continue_from(ctx, info)
    if choice == "import":
        from .import_cmds import import_sessions
        await import_sessions(ctx, [info])
    elif choice == "look":
        from .foreign_cmds import read_session
        session = await read_session(ctx, info)
        if session is not None:
            lines = [f"{index}. {' '.join(turn.text.split())[:240]}" for index, turn in enumerate(session.prompts()[:30], 1)]
            ctx.md("\n".join([f"**{info.title or info.id}** — {source.title}", ""] + (lines or [tr("(запросов нет)", "(no requests)")])))
    return False

@command("dashboard", "Все прошлые сессии: MemuCode по всем проектам и других инструментов (Claude, Codex, Grok…)",
         "Every earlier session: MemuCode's in all projects, and other tools' (Claude, Codex, Grok…)",
         aliases=("all-sessions",), hint="[tool]", category="session", while_busy=False, immediate=True)
async def dashboard_command(ctx: Context) -> None:
    wanted = ctx.args.strip().lower()
    tab = OWN
    if wanted:
        source = foreign.source_for(wanted)
        if source is None and wanted not in ("memucode", "own", "all"):
            ctx.say(tr(f"Не знаю источник «{wanted}». Есть: memucode, " + ", ".join(foreign.SOURCES),
                       f"Unknown source “{wanted}”. Known: memucode, " + ", ".join(foreign.SOURCES)), "warn")
            return
        tab = source.key if source else OWN
    await dashboard(ctx, tab=tab)
