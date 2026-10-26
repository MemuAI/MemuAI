"""Session commands: /clear /resume /continue /rename /branch /rewind /undo
/export /copy /compact /context /todos /exit."""
from __future__ import annotations

import datetime as _dt
import re
import time
from pathlib import Path
from typing import Any, Dict, List

from rich.table import Table
from rich.text import Text

from ... import code_checkpoints
from .. import paths
from ..clipboard import copy_text
from ..i18n import plural, tr
from ..session import Session, list_sessions, message_text
from ..timefmt import ago
from ..tokens import compact_number, estimate_text
from ..tui import dialogs as dlg
from . import Context, command

def replay(ctx: Context, messages: List[Dict[str, Any]], *, limit: int = 40) -> None:
    """Show the end of a transcript the way it looked when it was live."""
    from ..tools.base import ToolCall, ToolResult
    from ..tui.activity import Step
    shown = [m for m in messages if m.get("kind") not in ("interrupt", "hook", "note")][-limit:]
    if len(messages) > len(shown):
        ctx.say(f"… {len(messages) - len(shown)} earlier messages")
    ui = ctx.ui
    for message in shown:
        role = message.get("role")
        if role == "user":
            if message.get("kind") == "summary":
                if message.get("source") == "handoff":
                    ctx.say(str(message.get("display") or "↪ Continued from another tool"))
                else:
                    ctx.say("✻ The history before this point was compacted")
                continue
            text = message_text(message)
            if message.get("kind") != "bash":
                text = re.sub(r"\n\n<attached_files>.*$", "", text, flags=re.S)
            images = sum(1 for part in (message.get("content") or []) if isinstance(part, dict)
                         and part.get("type") == "image") if isinstance(message.get("content"), list) else 0
            shown_text = str(message.get("display") or text)
            if message.get("kind") == "bash" and shown_text.startswith("!"):
                shown_text = shown_text[1:]
            ui.show_user(shown_text, images=images, kind=message.get("kind", "prompt"))
        elif role == "assistant":
            text = str(message.get("content") or "")
            if text.strip():
                ui.add_assistant(text, True)
        elif role == "tool":
            call = ToolCall(name=str(message.get("name") or "tool"), args=message.get("args") or {},
                            id=str(message.get("tool_call_id") or ""), display=str(message.get("display") or ""))
            ok = not message.get("is_error")
            call.status = "done" if ok else "error"
            content = str(message.get("content") or "")
            result = ToolResult(ok=ok, summary=str(message.get("summary") or ""), diff=str(message.get("diff") or ""),
                                error="" if ok else content[:300], detail=content[:3000] if ok else "")
            ui.add_step(Step(call, result))

def _switch(ctx: Context, session: Session) -> None:
    runtime = ctx.runtime
    runtime.session = session
    if session.meta.get("model") and not runtime.settings.get("model"):
        runtime.model = str(session.meta["model"])
    runtime.invalidate_prompt()
    ctx.ui.context_cache = (-1, 0.0, 0)

@command("clear", "Начать новый разговор (история сохраняется в /resume)", "Start a new conversation (the old one stays in /resume)",
         aliases=("new", "reset"), category="session", while_busy=False)
async def clear(ctx: Context) -> None:
    runtime = ctx.runtime
    old = runtime.session
    old.flush_index()
    runtime.new_session()
    ctx.ui.context_cache = (-1, 0.0, 0)
    if ctx.name in ("clear", "reset"):
        ctx.ui.transcript.clear()
        try:
            ctx.ui.app.renderer.clear()
        except Exception:
            pass
    ctx.ui.write(ctx.r.render(Text("─" * min(ctx.ui.width(), 60), style="subtle")))
    hint = tr(f" Прошлый разговор: /resume {old.id}", f" Previous conversation: /resume {old.id}") if old.messages else ""
    ctx.say(tr("Новый разговор.", "New conversation.") + hint, "success")

@command("resume", "Продолжить прошлый разговор", "Resume a previous conversation", hint="[id|search]",
         aliases=("sessions",), category="session", while_busy=False)
async def resume(ctx: Context) -> None:
    runtime = ctx.runtime
    query = ctx.args.strip()
    rows = list_sessions(runtime.root, limit=200)
    if query:
        exact = [row for row in rows if row["id"] == query or row["id"].startswith(query)]
        if not exact:
            everywhere = list_sessions(None, limit=400)
            exact = [row for row in everywhere if row["id"] == query or row["id"].startswith(query)]
        if len(exact) == 1:
            await _open(ctx, exact[0])
            return
        low = query.lower()
        rows = [row for row in rows if low in f"{row.get('title', '')} {row.get('first', '')} {row.get('last', '')}".lower()]
    if not rows:
        everywhere = [row for row in list_sessions(None, limit=100) if row.get("root") != str(runtime.root)]
        if not everywhere:
            ctx.say(tr("Сохранённых разговоров пока нет.", "No saved conversations yet."), "warn")
            return
        ctx.say(tr("В этом проекте разговоров нет — показываю другие проекты.",
                   "No conversations in this project — showing other projects."))
        rows = everywhere
    options = []
    for row in rows:
        if row["id"] == runtime.session.id:
            continue
        title = row.get("name") or row.get("title") or row.get("first") or row["id"]
        meta = f"{ago(row.get('updated', 0))} · {plural(int(row.get('prompts') or 0), 'запрос', 'запроса', 'запросов', 'prompt')}"
        if row.get("model"):
            meta += f" · {row['model']}"
        if row.get("root") and row.get("root") != str(runtime.root):
            meta += f" · {Path(row['root']).name}"
        if row.get("forked_from"):
            meta += " · " + tr("ветка", "branch")
        options.append(dlg.Option(str(title)[:90], row, meta + (f"\n{row.get('last', '')[:120]}" if row.get("last") else "")))
    if not options:
        ctx.say(tr("Других разговоров нет.", "There are no other conversations."), "warn")
        return
    choice = await ctx.select(tr("Продолжить разговор", "Resume a conversation"), options, filterable=True, max_visible=8)
    if choice:
        await _open(ctx, choice)

async def _open(ctx: Context, row: Dict[str, Any]) -> None:
    runtime = ctx.runtime
    root = Path(row.get("root") or runtime.root)
    if root.resolve() != runtime.root and root.is_dir():
        if not await ctx.confirm(tr(f"Разговор из другой папки: {root}. Перейти туда?",
                                    f"This conversation belongs to {root}. Switch there?")):
            return
        from .config_cmds import switch_folder
        if await switch_folder(ctx, root) is None:
            return
    try:
        session = Session.load(runtime.root, row["id"])
    except (FileNotFoundError, OSError) as exc:
        ctx.say(tr(f"Не удалось открыть: {exc}", f"Could not open it: {exc}"), "error")
        return
    _switch(ctx, session)
    replay(ctx, session.messages)
    ctx.say(tr(f"Продолжаем «{session.title()}» ({len(session.messages)} сообщений)",
               f"Resumed “{session.title()}” ({len(session.messages)} messages)"), "success")

@command("continue", "Продолжить последний разговор этого проекта", "Continue the most recent conversation here",
         category="session", while_busy=False)
async def continue_(ctx: Context) -> None:
    rows = [row for row in list_sessions(ctx.runtime.root, limit=5) if row["id"] != ctx.runtime.session.id]
    if not rows:
        ctx.say(tr("Нет прошлых разговоров в этом проекте.", "No previous conversations in this project."), "warn")
        return
    await _open(ctx, rows[0])

@command("rename", "Назвать текущий разговор", "Name the current conversation", hint="<name>", category="session", while_busy=True)
async def rename(ctx: Context) -> None:
    name = ctx.args.strip() or await ctx.ask(tr("Название разговора", "Conversation name"),
                                             default=ctx.runtime.session.title())
    if not name:
        return
    ctx.runtime.session.set_meta(name=name[:120])
    ctx.runtime.session.flush_index()
    ctx.say(tr(f"Разговор называется «{name}»", f"Renamed to “{name}”"), "success")

@command("branch", "Ответвить разговор: копия, в которой можно пробовать другое", "Fork the conversation into a new branch",
         hint="[name]", aliases=("fork",), category="session", while_busy=False)
async def branch(ctx: Context) -> None:
    runtime = ctx.runtime
    if not runtime.session.messages:
        ctx.say(tr("Нечего ответвлять — разговор пуст.", "Nothing to branch yet."), "warn")
        return
    parent = runtime.session
    parent.flush_index()
    child = parent.fork(ctx.args.strip() or tr(f"ветка: {parent.title()[:60]}", f"branch: {parent.title()[:60]}"))
    _switch(ctx, child)
    ctx.say(tr(f"Вы в новой ветке {child.id}. Исходный разговор: /resume {parent.id}",
               f"You are on a new branch {child.id}. The original: /resume {parent.id}"), "success")

def _turn_list(session: Session) -> List[Dict[str, Any]]:
    return [message for message in session.messages if message.get("role") == "user"
            and message.get("kind") in ("prompt", "command", "bash")]

@command("rewind", "Вернуться к прошлому сообщению: откатить код и/или разговор", "Rewind the code and/or conversation",
         aliases=("checkpoint", "checkpoints"), category="session", while_busy=False)
async def rewind(ctx: Context) -> None:
    runtime = ctx.runtime
    session = runtime.session
    prompts = _turn_list(session)
    if not prompts:
        ctx.say(tr("Откатываться некуда — сообщений ещё нет.", "Nothing to rewind to yet."), "warn")
        return
    options = []
    for message in reversed(prompts[-60:]):
        text = " ".join(message_text(message).split())
        index = session.index_of(message["id"]) or 0
        later_turns = {m.get("turn") for m in session.messages[index:]}
        files = len({entry.get("path") for entry in code_checkpoints._load_index(session.id).get("entries", [])
                     if entry.get("turn") in later_turns})
        note = ago(message.get("ts", 0))
        if files:
            note += " · " + tr(f"изменено файлов после: {files}", f"{files} files changed since")
        options.append(dlg.Option(text[:100] or "…", message, note))
    target = await ctx.select(tr("К какому сообщению вернуться?", "Rewind to which message?"), options, filterable=len(options) > 12)
    if not target:
        return
    index = session.index_of(target["id"])
    later_turns = {m.get("turn") for m in session.messages[index or 0:] if m.get("turn")}
    entries = [entry for entry in code_checkpoints._load_index(session.id).get("entries", [])
               if entry.get("turn") in later_turns]
    choices = [dlg.Option(tr("Вернуть код и разговор", "Restore code and conversation"), "both"),
               dlg.Option(tr("Только разговор", "Restore conversation only"), "chat"),
               dlg.Option(tr("Только код", "Restore code only"), "code"),
               dlg.Option(tr("Отмена", "Cancel"), None)]
    if not entries:
        choices = [dlg.Option(tr("Вернуть разговор (файлы не менялись)", "Restore the conversation (no file changes)"), "chat"),
                   dlg.Option(tr("Отмена", "Cancel"), None)]
    files = sorted({entry["path"] for entry in entries})
    body = ctx.r.render(Text("  " + tr("Файлы, которые вернутся к прежнему виду:", "Files that will be restored:"), style="dim"),
                        *[Text("    " + path, style="dim") for path in files[:12]]) if files else ""
    how = await ctx.select(tr("Что вернуть?", "What should be restored?"), choices, body=body)
    if not how:
        return
    if how in ("both", "code") and entries:
        restored, failed = [], []
        for turn in reversed([m.get("turn") for m in session.messages[index or 0:] if m.get("turn")]):
            result = code_checkpoints.restore(session.id, runtime.root, turn=turn)
            restored += result.get("restored", []) + result.get("removed", [])
            failed += result.get("failed", [])
        runtime.files.invalidate()
        session.read_state.clear()
        session.read_cache.clear()
        ctx.say(tr(f"Файлов возвращено: {len(set(restored))}", f"Restored {len(set(restored))} files"), "success")
        for row in failed[:5]:
            ctx.say(f"{row.get('path')}: {row.get('error')}", "warn")
    if how in ("both", "chat"):
        session.truncate_from(target["id"])
        text = message_text(target)
        ctx.ui.input_buffer.text = re.sub(r"\n\n<attached_files>.*$", "", text, flags=re.S)
        ctx.ui.input_buffer.cursor_position = len(ctx.ui.input_buffer.text)
        ctx.ui.context_cache = (-1, 0.0, 0)
        runtime.invalidate_prompt()
        ctx.say(tr("Разговор возвращён. Сообщение снова в поле ввода — отредактируйте и отправьте.",
                   "Conversation rewound. The message is back in the input — edit and send it."), "success")

@command("undo", "Отменить изменения файлов последнего ответа", "Undo the file changes of the last answer", category="session", while_busy=False)
async def undo(ctx: Context) -> None:
    runtime = ctx.runtime
    session = runtime.session
    prompts = _turn_list(session)
    turns = []
    for message in reversed(prompts):
        index = session.index_of(message["id"]) or 0
        turn_ids = {m.get("turn") for m in session.messages[index:] if m.get("turn")}
        entries = [e for e in code_checkpoints._load_index(session.id).get("entries", []) if e.get("turn") in turn_ids]
        if entries:
            turns = [m.get("turn") for m in session.messages[index:] if m.get("turn")]
            break
    if not turns:
        ctx.say(tr("В этой сессии агент ещё не менял файлы.", "The agent has not changed files in this session."), "warn")
        return
    restored: List[str] = []
    for turn in reversed(list(dict.fromkeys(turns))):
        result = code_checkpoints.restore(session.id, runtime.root, turn=turn)
        restored += result.get("restored", []) + result.get("removed", [])
    runtime.files.invalidate()
    session.read_state.clear()
    session.read_cache.clear()
    session.append({"role": "user", "kind": "note", "content": "[The user undid the file changes of the last answer: "
                    + ", ".join(sorted(set(restored))[:40]) + "]"})
    ctx.say(tr(f"Отменено: {', '.join(sorted(set(restored))[:8])}", f"Undone: {', '.join(sorted(set(restored))[:8])}"), "success")

def transcript_markdown(session: Session) -> str:
    rows = [f"# {session.title() or 'MemuCode session'}", "",
            f"- {tr('Сессия', 'Session')}: `{session.id}`",
            f"- {tr('Проект', 'Project')}: `{session.root}`",
            f"- {tr('Дата', 'Date')}: {_dt.datetime.fromtimestamp(session.meta.get('created') or time.time()):%Y-%m-%d %H:%M}", ""]
    for message in session.messages:
        role = message.get("role")
        if role == "user" and message.get("kind") in ("prompt", "command", "bash"):
            rows += ["## " + tr("Вы", "You"), "", message_text(message), ""]
        elif role == "user" and message.get("kind") == "summary":
            rows += ["> " + tr("(история сжата)", "(history compacted)"), ""]
        elif role == "assistant":
            text = str(message.get("content") or "").strip()
            if text:
                rows += ["## MemuCode", "", text, ""]
            for call in message.get("tool_calls") or []:
                args = call.get("args") or {}
                key = next((str(args[k]) for k in ("file_path", "path", "command", "pattern", "query", "url") if args.get(k)), "")
                rows.append(f"- `{call.get('name')}` {key[:160]}")
            if message.get("tool_calls"):
                rows.append("")
        elif role == "tool" and message.get("summary"):
            rows.append(f"  - → {message.get('summary')}")
    return "\n".join(rows).strip() + "\n"

@command("export", "Сохранить разговор в Markdown или скопировать", "Export the conversation to Markdown or the clipboard",
         hint="[file]", category="session", while_busy=True)
async def export(ctx: Context) -> None:
    session = ctx.runtime.session
    if not session.messages:
        ctx.say(tr("Разговор пуст.", "The conversation is empty."), "warn")
        return
    text = transcript_markdown(session)
    target = ctx.args.strip()
    if not target:
        choice = await ctx.select(tr("Экспорт разговора", "Export conversation"), [
            dlg.Option(tr("Сохранить в Загрузки/MemuAI", "Save to Downloads/MemuAI"), "file"),
            dlg.Option(tr("Скопировать в буфер обмена", "Copy to clipboard"), "clip")])
        if not choice:
            return
        if choice == "clip":
            ok = copy_text(text)
            ctx.say(tr("Скопировано в буфер обмена", "Copied to the clipboard") if ok else
                    tr("Не удалось скопировать", "Could not copy"), "success" if ok else "error")
            return
        safe = re.sub(r"[^\w.-]+", "-", session.title() or session.id).strip("-")[:60] or session.id
        target = str(paths.downloads_dir() / f"memucode-{safe}.md")
    path = Path(target).expanduser()
    if not path.is_absolute():
        path = ctx.runtime.root / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    ctx.say(tr(f"Сохранено: {path}", f"Saved: {path}"), "success")

@command("copy", "Скопировать последний ответ (или блок кода из него)", "Copy the last answer (or a code block of it)",
         hint="[n]", category="session", while_busy=True)
async def copy(ctx: Context) -> None:
    texts = ctx.runtime.session.assistant_texts()
    if not texts:
        ctx.say(tr("Ответов пока нет.", "There is no answer yet."), "warn")
        return
    try:
        back = max(1, int(ctx.args.strip() or 1))
    except ValueError:
        back = 1
    text = texts[-min(back, len(texts))]
    blocks = re.findall(r"```[^\n]*\n(.*?)```", text, re.S)
    if blocks:
        options = [dlg.Option(tr("Весь ответ", "Whole answer"), text, f"{len(text)} " + tr("символов", "chars"))]
        for number, block in enumerate(blocks, 1):
            first = block.strip().splitlines()[0][:70] if block.strip() else ""
            options.append(dlg.Option(tr(f"Блок кода {number}", f"Code block {number}"), block.rstrip("\n"), first))
        chosen = await ctx.select(tr("Что скопировать?", "What to copy?"), options)
        if chosen is None:
            return
        text = chosen
    ok = copy_text(text)
    if ok:
        ctx.ui._copied(len(text))
    ctx.say(tr(f"Скопировано ({len(text)} символов)", f"Copied ({len(text)} chars)") if ok else
            tr("Не удалось скопировать", "Could not copy"), "success" if ok else "error")

@command("compact", "Сжать историю, сохранив суть (можно указать, на чём сосредоточиться)",
         "Compact the history, keeping what matters (optionally say what to focus on)", hint="[instructions]",
         category="context", while_busy=False)
async def compact_cmd(ctx: Context) -> None:
    from ..compact import compact
    ctx.ui.status_text = tr("сжимаю историю", "compacting")
    try:
        result = await compact(ctx.runtime, instructions=ctx.args, reason="manual")
    except Exception as exc:
        ctx.say(tr(f"Сжать не удалось: {exc}", f"Compaction failed: {exc}"), "error")
        return
    if not result.get("ok"):
        ctx.say(result.get("error") or tr("Не удалось", "Failed"), "warn")
        return
    ctx.ui.compacted(result)
    ctx.ui.context_cache = (-1, 0.0, 0)
    if ctx.settings.get("verbose"):
        ctx.ui.print(ctx.r.md(result["summary"]))

@command("context", "Сколько занято контекста и чем", "Show what fills the context window", category="context", while_busy=True)
async def context(ctx: Context) -> None:
    runtime = ctx.runtime
    from ..compact import context_breakdown
    info = context_breakdown(runtime)
    window, used, part = info["window"], info["used"], info["parts"]
    system_tokens, memory_tokens = part["system"], part["memory"]
    message_tokens, tool_result_tokens = part["messages"], part["tool_results"]
    free, buffer_tokens = info["free"], info["buffer"]
    parts = [
        (tr("Системный промпт", "System prompt"), system_tokens, "brand2"),
        (tr("Инструменты", "Tools"), part["tools"], "info"),
        (tr("Память (MEMUCODE.md…)", "Memory files"), memory_tokens, "memory"),
        (tr("Сообщения", "Messages"), message_tokens, "brand"),
        (tr("Результаты инструментов", "Tool results"), tool_result_tokens, "warn"),
        (tr("Свободно", "Free space"), free, "subtle"),
        (tr("Запас для автосжатия", "Auto-compact buffer"), buffer_tokens, "dim"),
    ]
    cells = 100
    grid: List[str] = []
    for _label, tokens, style in parts:
        count = int(round(tokens / window * cells)) if window else 0
        if tokens > 0 and count == 0 and style not in ("subtle", "dim"):
            count = 1
        grid += [style] * count
    grid = (grid + ["subtle"] * cells)[:cells]
    lines = []
    for row in range(10):
        text = Text("  ")
        for column in range(10):
            style = grid[row * 10 + column]
            text.append("■ " if style not in ("subtle", "dim") else ("□ " if style == "subtle" else "▣ "), style=style)
        lines.append(text)
    table = Table.grid(padding=(0, 2))
    table.add_column()
    table.add_column(justify="right")
    table.add_column(justify="right")
    for label, tokens, style in parts:
        table.add_row(Text("■ " + label, style=style), compact_number(tokens), f"{tokens / window:.1%}" if window else "")
    head = Text(f"  {runtime.model} · {compact_number(used)}/{compact_number(window)} "
                + tr("токенов", "tokens") + f" ({used / window:.0%})", style="bold")
    columns = Table.grid(padding=(0, 4))
    columns.add_column()
    columns.add_column()
    from rich.console import Group
    columns.add_row(Group(*lines), table)

    origin = (tr("  Всего — по счёту провайдера за последний запрос; доли частей — оценка.",
                 "  The total is what the provider counted for the last request; the split is an estimate.")
              if info.get("source") == "provider" else
              tr("  Оценка по длине текста: провайдер не сообщает число токенов (или запросов ещё не было).",
                 "  An estimate from text length: the provider reports no token counts (or no request was made yet)."))
    ctx.print(Text(""), head, Text(origin, style="dim"), Text(""), columns)
    files = runtime.memory_files()
    if files:
        ctx.print(Text("  " + tr("Файлы памяти:", "Memory files:"), style="bold"),
                  *[Text(f"    {row.path}  ·  {compact_number(estimate_text(row.text))}", style="dim") for row in files])
    if used / window > 0.6:
        ctx.say(tr("Совет: /compact сожмёт историю, /clear начнёт заново.", "Tip: /compact shrinks the history, /clear starts over."))

@command("todos", "Показать список задач агента", "Show the agent's todo list", category="session", while_busy=True)
async def todos(ctx: Context) -> None:
    items = ctx.runtime.session.todos
    if not items:
        ctx.say(tr("Список задач пуст.", "The todo list is empty."))
        return
    done = sum(1 for todo in items if todo.get("status") == "completed")
    ctx.print(Text(f"  {tr('Задачи', 'Todos')} {done}/{len(items)}", style="bold"),
              *[ctx.r.todo_line(todo, indent=4) for todo in items])

@command("exit", "Выйти из MemuCode", "Exit MemuCode", aliases=("quit", "q"), category="general", while_busy=True)
async def exit_(ctx: Context) -> None:
    ctx.ui.exit()
