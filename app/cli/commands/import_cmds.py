"""``/import`` — take things over from the other coding tools.

A short wizard: it looks at what is on this computer (Claude Code, Codex, Grok, Gemini CLI, Antigravity,
OpenCode, Cursor), shows what can be imported in groups — MCP servers, permission rules, instructions, skills,
custom commands, agents, and each tool's sessions — lets the person tick what they want, shows a summary and only
then writes. Nothing in another tool's folders is changed; nothing already in MemuCode is overwritten; secrets
(keys in an MCP server's environment) are masked on screen.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from rich.text import Text

from .. import foreign
from ..foreign import SessionInfo, configs, importer
from ..i18n import tr
from ..tui import dialogs as dlg
from . import Context, command
from .foreign_cmds import order_rows, read_session, session_option

KIND_TITLES = {
    "mcp": ("серверы MCP", "MCP servers"), "permission": ("правила разрешений", "permission rules"),
    "instructions": ("инструкции", "instructions"), "skill": ("скиллы", "skills"),
    "command": ("команды", "custom commands"), "agent": ("агенты", "agents"), "sessions": ("сессии", "sessions"),
}

OPT_IN = {"permission", "sessions"}

def kind_title(kind: str) -> str:
    ru, en = KIND_TITLES[kind]
    return tr(ru, en)

async def scan(ctx: Context, only: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """``{tool: {"items": [...], "sessions": [...]}}`` for every tool that has something."""
    ctx.ui.status_text = tr("смотрю, что есть на компьютере", "looking at what is on this computer")
    project = ctx.runtime.root
    keys = [only] if only else list(foreign.SOURCES)
    try:
        items = await asyncio.gather(*(asyncio.to_thread(configs.scan, key, project) for key in keys))
        sessions = await asyncio.gather(*(asyncio.to_thread(foreign.discover, key, 800) for key in keys))
    finally:
        ctx.ui.status_text = ""
    found: Dict[str, Dict[str, Any]] = {}
    for key, rows, sess in zip(keys, items, sessions):
        if rows or sess:
            found[key] = {"items": rows, "sessions": sess}
    return found

def groups(found: Dict[str, Dict[str, Any]]) -> List[Tuple[str, str, List[Any]]]:
    """``(tool, kind, entries)`` for every group worth a row: the sessions and the items that are not native."""
    out: List[Tuple[str, str, List[Any]]] = []
    for key, data in found.items():
        by_kind: Dict[str, List[Any]] = {}
        for item in data["items"]:
            if not item.native:
                by_kind.setdefault(item.kind, []).append(item)
        for kind in ("mcp", "instructions", "skill", "command", "agent", "permission"):
            if by_kind.get(kind):
                out.append((key, kind, by_kind[kind]))
        if data["sessions"]:
            out.append((key, "sessions", list(data["sessions"])))
    return out

def native_note(found: Dict[str, Dict[str, Any]]) -> str:
    counter: Counter = Counter()
    for data in found.values():
        for item in data["items"]:
            if item.native:
                counter[item.kind] += 1
    if not counter:
        return ""
    parts = ", ".join(f"{count} {kind_title(kind)}" for kind, count in counter.items())
    return tr(f"Уже доступно без импорта (MemuCode читает их из ~/.claude): {parts}.",
              f"Already available without importing (MemuCode reads them from ~/.claude): {parts}.")

def _row(tool: str, kind: str, entries: List[Any]) -> dlg.Option:
    source = foreign.SOURCES[tool]
    names = ", ".join((entry.name if hasattr(entry, "name") else (entry.title or entry.id))[:28] for entry in entries[:4])
    more = f" +{len(entries) - 4}" if len(entries) > 4 else ""
    label = f"{source.title} · {kind_title(kind)} ({len(entries)})"
    hint = tr("по желанию", "opt-in") if kind in OPT_IN else ""
    return dlg.Option(label, (tool, kind), names + more, hint=hint)

async def choose_sessions(ctx: Context, tool: str, rows: List[SessionInfo]) -> List[SessionInfo]:
    """Which of a tool's sessions: those of this project are ticked, the rest are one Tab away."""
    here = str(ctx.runtime.root)
    ordered = order_rows(rows, here)
    options = [session_option(row, here) for row in ordered]
    selected = [row for row in ordered if foreign.same_folder(row.cwd, here)]
    picked = await ctx.select(tr(f"Какие сессии {foreign.SOURCES[tool].title} импортировать", f"Which {foreign.SOURCES[tool].title} sessions to import"),
                              options, multi=True, filterable=True, max_visible=8, selected=selected, allow_empty=True,
                              body=ctx.r.render(Text("  " + tr("● — сессии этого проекта (отмечены). Tab — отметить, Enter — готово.",
                                                              "● — sessions of this project (ticked). Tab ticks, Enter confirms."), style="dim")))
    return list(picked or [])

def _summary(items: List[configs.Item], sessions: List[SessionInfo]) -> str:
    lines: List[str] = []
    by_kind: Dict[str, List[configs.Item]] = {}
    for item in items:
        by_kind.setdefault(item.kind, []).append(item)
    for kind, rows in by_kind.items():
        lines.append(f"• {len(rows)} {kind_title(kind)}")
        for item in rows[:8]:
            lines.append(f"    {item.source}: {item.summary or item.name}"[:160])
        if len(rows) > 8:
            lines.append(f"    … +{len(rows) - 8}")
    if sessions:
        lines.append(f"• {len(sessions)} {kind_title('sessions')}")
    return "\n".join(lines)

async def import_sessions(ctx: Context, infos: List[SessionInfo]) -> Tuple[int, int]:
    """Import these sessions as MemuCode conversations. Returns (added, skipped)."""
    added = skipped = 0
    reasons: Counter = Counter()
    for number, info in enumerate(infos, 1):
        ctx.ui.status_text = tr(f"импортирую {number}/{len(infos)}", f"importing {number}/{len(infos)}")
        session = await read_session(ctx, info)
        if session is None:
            skipped += 1
            continue
        ctx.ui.status_text = tr(f"импортирую {number}/{len(infos)}", f"importing {number}/{len(infos)}")
        try:
            result, why = await asyncio.to_thread(importer.import_session, ctx.runtime.root, session)
        except OSError as exc:
            skipped += 1
            reasons[str(exc)] += 1
            continue
        if result is None:
            skipped += 1
            reasons[why] += 1
        else:
            added += 1
    ctx.ui.status_text = ""
    if added:
        ctx.say(tr(f"Импортировано разговоров: {added}. Они в /resume и /dashboard.", f"Imported conversations: {added}. They are in /resume and /dashboard."), "success")
    for why, count in reasons.items():
        ctx.say(f"{count} × {why}", "warn")
    return added, skipped

async def apply_items(ctx: Context, items: List[configs.Item]) -> Tuple[int, Counter]:
    env = configs.default_env(ctx.runtime)
    added = 0
    reasons: Counter = Counter()
    for item in items:
        try:
            result = await asyncio.to_thread(configs.apply, item, env)
        except (OSError, ValueError) as exc:
            reasons[f"{item.kind} {item.name}: {exc}"] += 1
            continue
        if result == "added":
            added += 1
        else:
            reasons[result.replace("skipped: ", "")] += 1
    if any(item.kind in ("skill", "command", "agent", "instructions") for item in items):
        ctx.runtime.invalidate_prompt(memory=True)
        try:
            ctx.runtime.skills.catalogue(refresh=True)
        except Exception:
            pass
    return added, reasons

@command("import", "Перенести из других инструментов: сессии, MCP-серверы, правила, инструкции, скиллы, команды",
         "Import from other tools: sessions, MCP servers, rules, instructions, skills, commands",
         hint="[tool]", category="config", while_busy=False, immediate=True)
async def import_command(ctx: Context) -> None:
    only: Optional[str] = None
    wanted = ctx.args.strip()
    if wanted:
        source = foreign.source_for(wanted)
        if source is None:
            ctx.say(tr(f"Не знаю инструмент «{wanted}». Есть: ", f"Unknown tool “{wanted}”. Known: ") + ", ".join(foreign.SOURCES), "warn")
            return
        only = source.key
    found = await scan(ctx, only)
    rows = groups(found)
    if not rows:
        ctx.say(tr("Не нашёл, что импортировать: ни сессий, ни настроек других инструментов.",
                   "Nothing to import: no sessions or settings of other tools were found."), "warn")
        note = native_note(found)
        if note:
            ctx.say(note)
        return
    options = [_row(tool, kind, entries) for tool, kind, entries in rows]
    preselect = [option.value for option in options if option.value[1] not in OPT_IN]
    body = ctx.r.render(
        Text("  " + tr("Отметьте, что перенести (Tab — отметить, Enter — дальше). Источники не изменяются, уже имеющееся не перезаписывается.",
                        "Tick what to bring over (Tab ticks, Enter goes on). Sources are never changed; nothing already here is overwritten."),
             style="dim"),
        *([Text("  " + native_note(found), style="dim")] if native_note(found) else []),
        *([Text("  " + tr("Cursor читается по описанному формату и не проверялся на живых данных.", "Cursor is read from its documented format and was not checked against real data."), style="dim")]
          if any(tool == "cursor" for tool, _k, _e in rows) else []))
    picked = await ctx.select(tr("Что импортировать", "What to import"), options, multi=True, filterable=len(options) > 8, max_visible=9,
                              selected=preselect, allow_empty=True, body=body)
    if not picked:
        return
    items: List[configs.Item] = []
    sessions: List[SessionInfo] = []
    for tool, kind in picked:
        entries = next(entries for t, k, entries in rows if (t, k) == (tool, kind))
        if kind == "sessions":
            sessions += await choose_sessions(ctx, tool, entries)
        else:
            items += entries
    if not items and not sessions:
        ctx.say(tr("Ничего не выбрано.", "Nothing chosen."))
        return
    summary = ctx.r.render(Text(_summary(items, sessions), style="dim"))
    if not await ctx.confirm(tr("Импортировать это?", "Import this?"), summary):
        return
    added, reasons = await apply_items(ctx, items) if items else (0, Counter())
    if items:
        ctx.say(tr(f"Настроек добавлено: {added}", f"Settings added: {added}"), "success" if added else "info")
        for why, count in reasons.items():
            ctx.say(f"{count} × {why}", "warn")
        if any(item.kind == "mcp" for item in items) and added:
            ctx.say(tr("Серверы MCP запустятся при следующем запуске MemuCode или по /mcp.", "MCP servers start on the next MemuCode launch, or from /mcp."))
    if sessions:
        await import_sessions(ctx, sessions)
