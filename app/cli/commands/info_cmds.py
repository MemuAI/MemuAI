"""Information commands: /help /status /doctor /usage /cost /stats /tasks /mcp
/memory /agents /skills /tools /bug /release-notes /version."""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import os
import platform
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List

from rich.table import Table
from rich.text import Text
from rich.console import Group
from rich.panel import Panel

from ... import pricing
from .. import VERSION, paths
from ..i18n import language, tr
from ..permissions import mode_label
from ..timefmt import duration as _duration
from ..tokens import compact_number
from ..tui import dialogs as dlg
from . import CATEGORIES, REGISTRY, Context, command

@command("help", "Все команды и горячие клавиши", "All commands and shortcuts", hint="[command]", aliases=("?", "h"),
         category="general", while_busy=True)
async def help_cmd(ctx: Context) -> None:
    name = ctx.args.strip().lstrip("/")
    if name:
        cmd = ctx.ui.commands.find(name)
        if cmd is None and len(name.split()) > 1:

            ctx.ui.run_command_text("/help-ask " + ctx.args.strip())
            return
        if cmd is None:
            ctx.say(tr(f"Нет команды /{name}", f"No command /{name}"), "warn")
            return
        aliases = (tr(" · также: ", " · also: ") + ", ".join("/" + alias for alias in cmd.aliases)) if cmd.aliases else ""
        ctx.md(f"**/{cmd.name}** {cmd.hint}\n\n{cmd.description}{aliases}")
        return
    items = ctx.ui.commands.items()
    order = list(CATEGORIES)

    def rank(item) -> tuple:
        cmd = REGISTRY.get(item.name)
        category = cmd.category if cmd else "zz"
        return (order.index(category) if category in order else len(order), item.name)

    dialog = dlg.Dialog("MemuCode help", [], tabs=["General", "Commands", "Skills and custom commands"], inline=True,
                        footer="Tab / ← → switch tabs · type to filter · Enter to use a command · Esc to close")

    def tab(index):
        dialog.filterable = index != 0
        dialog.index = 0
        if index == 0:
            dialog.body = ctx.r.render(help_overview(ctx.ui.width()), width=ctx.ui.width())
            dialog.options = []
        else:
            dialog.body = "" if index == 1 else "Your skills and project commands — run one as /name <task>"
            rows = sorted((item for item in items if bool(item.source) == (index == 2)), key=rank)
            dialog.options = [dlg.Option("/" + item.name + (" " + item.hint if item.hint else ""),
                                         "/" + item.name, item.description) for item in rows]
        dialog.view = list(range(len(dialog.options)))
    dialog.on_tab = tab
    tab(0)
    choice = await ctx.ui.open_dialog(dialog)
    if choice:
        ctx.ui.input_buffer.text = choice + " "
        ctx.ui.input_buffer.cursor_position = len(ctx.ui.input_buffer.text)

HELP_KEYS = [("/", "browse commands"), ("@", "mention a file"), ("!", "run a shell command"),
             ("#", "save a rule to memory"), ("Shift+Tab", "cycle permission modes"),
             ("Esc Esc", "rewind to an earlier message"), ("Shift+Enter", "new line"),
             ("Ctrl+V / Alt+V", "paste text or an image"), ("Ctrl+R", "search input history"),
             ("Ctrl+G", "edit in your editor"), ("Ctrl+O", "unfold every gray line"),
             ("Ctrl+C twice", "exit (prints the resume id)")]
HELP_NOTES = [
    "Click a gray summary line to see exactly what was done. Click the model, the context or the number in the "
    "status line to change them.",
    "/agents picks the model used by parallel agents · /subtask <task> starts one · /tasks shows what runs in the "
    "background.",
    "/help-ask <question> answers anything about MemuCode itself · /auto-continue sends “continue” by itself later "
    "when the limits ran out.",
    "Guide: MemuAI → Settings → Documentation → MemuCode CLI",
]

def help_overview(width: int) -> Any:
    """The «General» tab of /help, laid out for the window it is shown in.

    Round 34: the text had hand-made line breaks for ~95 columns — in an 80- or 70-column terminal every row of
    the two-column key list broke in the middle («mention a / file»). Keys are a grid now: two pairs per row in a
    wide window, one per row in a narrow one; notes are paragraphs with a hanging indent."""
    from rich.padding import Padding
    pairs = 2 if width >= 92 else 1
    keys = Table.grid(padding=(0, 2))
    for _ in range(pairs):
        keys.add_column(style="brand", no_wrap=True)
        keys.add_column(style="text")
    for start in range(0, len(HELP_KEYS), pairs):
        row: List[str] = []
        for key, what in HELP_KEYS[start:start + pairs]:
            row += [key, what]
        keys.add_row(*row)
    parts: List[Any] = [Text("Describe your task in plain language. MemuCode reads, edits and tests your project.",
                             style="text"), Text(""), Padding(keys, (0, 0, 0, 2))]
    for note in HELP_NOTES:
        parts += [Text(""), Padding(Text(note, style="text"), (0, 0, 0, 2))]
    return Group(*parts)

@command("status", "Версия, модель, подключение, режимы, память, MCP", "Version, model, connection, modes, memory, MCP",
         category="info", while_busy=True)
async def status(ctx: Context) -> None:
    runtime = ctx.runtime

    health = None
    if runtime.settings.get("backend") != "direct":
        try:
            health = await asyncio.wait_for(runtime.router.proxy.health(), 0.6)
        except asyncio.TimeoutError:
            health = None
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim", no_wrap=True)
    table.add_column()
    table.add_row("MemuCode", f"{VERSION} · Python {platform.python_version()} · {platform.system()} {platform.release()}")
    table.add_row(tr("Сессия", "Session"), f"{runtime.session.id} · {runtime.session.title() or '—'}")
    table.add_row(tr("Папка", "Folder"), str(runtime.root) + (tr(" (доверенная)", " (trusted)") if runtime.trusted() else ""))
    if runtime.add_dirs:
        table.add_row(tr("Доп. папки", "Extra folders"), ", ".join(str(path) for path in runtime.add_dirs))
    table.add_row(tr("Модель", "Model"), f"{runtime.model} · {tr('окно', 'window')} {compact_number(runtime.effective_window())}")
    table.add_row("MemuAI Proxy", (f"{runtime.router.proxy.base_url()} · {tr('работает', 'online')} · v{health.get('version')}"
                                   if health else tr("не отвечает", "offline")))
    connections = runtime.settings.connections()
    if connections:
        table.add_row(tr("Прямые API", "Direct APIs"), ", ".join(f"{row['name']} ({row.get('format')})" for row in connections))
    table.add_row(tr("Протокол инструментов", "Tool protocol"),
                  tr("нативный", "native") if runtime.router.uses_native_tools(runtime.model) else tr("текстовый memuai-tool", "text memuai-tool"))
    table.add_row(tr("Режим", "Mode"), mode_label(runtime.permissions.mode))
    table.add_row(tr("Усилие / размышления", "Effort / thinking"),
                  f"{runtime.settings.get('effort')} / {tr('вкл', 'on') if runtime.settings.get('thinking') else tr('выкл', 'off')}")
    memory = runtime.memory_files()
    table.add_row(tr("Память", "Memory"), ", ".join(f"{row.path.name} ({row.scope})" for row in memory) or "—")
    servers = runtime.mcp.catalogue()
    if servers:
        table.add_row("MCP", ", ".join(f"{row['name']}{' ✓' if row['running'] else ' ✗'}" for row in servers))
    table.add_row(tr("Хуки", "Hooks"), str(runtime.hooks.count()))
    table.add_row(tr("Модель решений", "Decision model"),
                  f"{runtime.decisions.title} · {runtime.decisions.model}" if runtime.decisions.enabled else tr("выкл (/decisions)", "off (/decisions)"))
    advisor = runtime.settings.get("advisor_model")
    if advisor:
        table.add_row(tr("Советник", "Advisor"), str(advisor))
    sources = [name for name, path, _data in runtime.settings.layers[1:4] if path and Path(path).is_file()]
    table.add_row(tr("Настройки", "Settings"), ", ".join(sources) or tr("по умолчанию", "defaults"))
    ctx.print(Text(""), table)
    for warning in runtime.settings.warnings:
        ctx.say(warning, "warn")

@command("version", "Версия MemuCode", "MemuCode version", category="info", while_busy=True, hidden=True)
async def version(ctx: Context) -> None:
    ctx.say(f"MemuCode {VERSION}")

@command("doctor", "Проверить установку, подключение, терминал и инструменты", "Check the install, connection, terminal and tools",
         category="info", while_busy=True)
async def doctor(ctx: Context) -> None:
    from ..doctor import run_checks
    ctx.ui.status_text = tr("проверяю", "checking")
    try:
        checks = await run_checks(ctx.runtime)
    finally:
        ctx.ui.status_text = ""
    table = Table.grid(padding=(0, 2))
    table.add_column(no_wrap=True)
    table.add_column(style="bold", no_wrap=True)
    table.add_column()
    marks = {"ok": Text("✓", style="ok"), "warn": Text("⚠", style="warn"), "error": Text("✗", style="err"),
             "info": Text("·", style="dim")}
    for row in checks:
        detail = Text(row["detail"])
        if row.get("hint"):
            detail.append("\n" + row["hint"], style="dim")
        table.add_row(marks.get(row["status"], marks["info"]), row["name"], detail)
    ctx.print(Text(""), Text(tr("Диагностика MemuCode", "MemuCode diagnostics"), style="bold"), table)
    problems = sum(1 for row in checks if row["status"] == "error")
    warnings = sum(1 for row in checks if row["status"] == "warn")
    if problems:
        ctx.say(tr(f"Проблем: {problems}, предупреждений: {warnings}", f"{problems} problems, {warnings} warnings"), "error")
    elif warnings:
        ctx.say(tr(f"Всё работает; предупреждений: {warnings}", f"Everything works; {warnings} warnings"), "warn")
    else:
        ctx.say(tr("Всё в порядке", "All good"), "success")

def _usage_table(ctx: Context, costs: Dict[str, Any]) -> Table:
    table = Table(box=None, pad_edge=False, show_header=True, header_style="bold")
    for column, justify in ((tr("Модель", "Model"), "left"), (tr("Запросы", "Requests"), "right"),
                            (tr("Вход", "Input"), "right"), (tr("Кэш", "Cached"), "right"),
                            (tr("Выход", "Output"), "right"), (tr("Стоимость", "Cost"), "right"), ("", "left")):
        table.add_column(column, justify=justify)
    for row in costs["rows"]:
        usage = row["usage"]
        estimate = row["estimate"]
        billing = {"subscription": tr("≈ по ценам API", "≈ at API prices"), "api": tr("счёт API", "API bill"),
                   "local": tr("локально", "local"), "unknown": tr("цена неизвестна", "unknown price")}.get(row["billing"], "")
        source = ""
        if estimate.price is not None:
            source = f" · {estimate.price.source}" + ("~" if estimate.price.approximate else "")
        if row["reported"] is not None:
            source = tr(" · от провайдера", " · reported")
        table.add_row(row["model"], str(usage.requests), compact_number(usage.input), compact_number(usage.cache_read),
                      compact_number(usage.output), pricing.format_usd(row["cost"], precise=True),
                      Text(billing + source + (" *" if usage.estimated else ""), style="dim"))
    return table

@command("usage", "Токены, время и стоимость сессии, лимиты аккаунтов", "Tokens, time and cost of the session, account limits",
         category="info", while_busy=True)
async def usage(ctx: Context) -> None:
    from dataclasses import replace
    captured: List[Any] = []
    capture = replace(ctx, extras={**ctx.extras, "capture": captured})
    _session_usage(capture)
    if ctx.ui.app is None or not ctx.ui.app.is_running:
        await _ledger(capture)
        await _limits(capture)
        ctx.print(*captured)
        return

    waiting = Text("\n  " + tr("Загружаю активность и лимиты аккаунтов…", "Loading activity and account limits…"), style="dim")

    def body(done: bool = False) -> str:
        return ctx.r.render(Group(*captured, *([] if done else [waiting])))

    async def load(set_body) -> None:
        await _ledger(capture)
        set_body(body())
        await _limits(capture)
        set_body(body(done=True))

    await ctx.ui.open_document("Usage", body(), loader=load)

def _session_usage(ctx: Context) -> None:
    runtime = ctx.runtime
    stats = runtime.stats
    costs = stats.costs(ctx.settings.get("pricing.overrides") or {})
    total = stats.total()
    wall = time.time() - stats.started
    summary = Table.grid(padding=(0, 2))
    summary.add_column(style="dim")
    summary.add_column()
    summary.add_row(tr("Всего токенов", "Total tokens"), f"{compact_number(total.input)} ↑  {compact_number(total.output)} ↓"
                    + (f"  ({compact_number(total.cache_read)} {tr('из кэша', 'cached')})" if total.cache_read else ""))
    summary.add_row(tr("Стоимость по ценам API", "At API prices"), pricing.format_usd(costs["api_equivalent"], precise=True))
    if costs["actual"]:
        summary.add_row(tr("Фактически по счёту API", "Actually billed by APIs"), pricing.format_usd(costs["actual"], precise=True))
    summary.add_row(tr("Время ответа моделей", "Model time"), _duration(stats.api_seconds))
    summary.add_row(tr("Длительность сессии", "Session duration"), _duration(wall))
    summary.add_row(tr("Инструменты", "Tool calls"), f"{stats.tool_calls}" + (f" ({stats.tool_errors} {tr('с ошибкой', 'failed')})" if stats.tool_errors else ""))
    summary.add_row(tr("Изменения кода", "Code changes"), f"+{stats.lines_added} −{stats.lines_removed}")
    if stats.decision_calls:
        summary.add_row(tr("Модель решений", "Decision model"), f"{stats.decision_calls} · {pricing.format_usd(stats.decision_cost, precise=True)}")
    if stats.compactions:
        summary.add_row(tr("Сжатий истории", "Compactions"), str(stats.compactions))
    details = _usage_table(ctx, costs) if costs["rows"] else Text("No model requests yet.", style="dim")
    ctx.print(Text(""), Panel(Group(details, Text(""), summary),
                              title=f"Usage · {runtime.session.id}", border_style="brand"))
    if any(row["usage"].estimated for row in costs["rows"]):
        ctx.say(tr("* провайдер не сообщил токены — они оценены по длине текста",
                   "* the provider reported no token counts — they are estimated from text length"))
    if costs["unknown"]:
        ctx.say(tr(f"Нет цены для: {', '.join(costs['unknown'])}. Задайте: /cost set <модель> <вход> <выход>",
                   f"No price for: {', '.join(costs['unknown'])}. Set one: /cost set <model> <in> <out>"), "warn")

async def _ledger(ctx: Context) -> None:
    try:
        from ... import code_stats
        today = await asyncio.to_thread(code_stats.summary, 1)
        month = await asyncio.to_thread(code_stats.summary, 30)
    except Exception:
        return
    table = Table.grid(padding=(0, 3))
    table.add_column(style="dim")
    table.add_column(justify="right")
    table.add_column(justify="right")
    table.add_row("", tr("сегодня", "today"), tr("30 дней", "30 days"))
    table.add_row(tr("Токены", "Tokens"), compact_number(today["tokens"]["total"]), compact_number(month["tokens"]["total"]))
    table.add_row(tr("Сообщения", "Messages"), str(today["messages"]), str(month["messages"]))
    for label, data in (("today", today), ("month", month)):
        data["_cost"] = sum(float((row.get("cost") or 0)) for row in data.get("models") or [])
    if month["_cost"]:
        table.add_row(tr("≈ по ценам API", "≈ at API prices"), pricing.format_usd(today["_cost"]), pricing.format_usd(month["_cost"]))
    ctx.print(Text(""), Panel(table, title="Activity · this computer", border_style="brand2"))

async def _limits(ctx: Context) -> None:
    runtime = ctx.runtime
    info = runtime.router.info(runtime.model)
    if info is None or info.backend != "proxy" or not await runtime.router.proxy.health():
        return
    provider = info.provider
    try:
        accounts = [row for row in await runtime.router.proxy.accounts() if row.get("provider") == provider and row.get("enabled")]
    except Exception:
        return
    if not accounts:
        return
    async def fetch(account: Dict[str, Any]) -> Any:
        try:
            response = await runtime.router.proxy.client().get(
                runtime.router.proxy.base_url() + f"/api/admin/accounts/{account['id']}/usage",
                headers=runtime.router.proxy.headers(), timeout=5)
            return response.json().get("usage") if response.status_code == 200 else None
        except Exception:
            return None

    shown_accounts = accounts[:6]
    usage_rows = await asyncio.gather(*(fetch(account) for account in shown_accounts))
    rows: List[Any] = []
    for account, data in zip(shown_accounts, usage_rows):
        label = str(account.get("label") or account.get("id"))[:40]
        stats = account.get("stats") or {}
        head = Text(f"  {label}", style="bold")
        head.append(f"  · {stats.get('requests', 0)} " + tr("запросов", "requests"), style="dim")
        rows.append(head)
        if isinstance(data, dict) and isinstance(data.get("rows"), list):
            for item in data["rows"][:6]:
                try:
                    share = float(item.get("used") or 0) / float(item.get("limit") or 100)
                except (TypeError, ValueError, ZeroDivisionError):
                    share = 0.0
                line = Text(f"    {str(item.get('display', ''))[:22]:<22} ")
                line.append_text(ctx.r.meter(share, 18))
                line.append(f" {item.get('used')}{item.get('unit', '')}", style="dim")
                if item.get("reset"):
                    line.append(tr("  сброс ", "  resets ") + str(item["reset"])[:16], style="subtle")
                rows.append(line)
        elif data:
            rows.append(Text("    " + json.dumps(data, ensure_ascii=False)[:160], style="dim"))
    if rows:
        ctx.print(Text(""), Panel(Group(*rows), title=f"{provider} account limits", border_style="info"))

@command("cost", "Стоимость сессии по моделям; свои цены: /cost set", "Session cost per model; custom prices: /cost set",
         hint="[set|refresh|prices]", category="info", while_busy=True)
async def cost(ctx: Context) -> None:
    words = ctx.words()
    if words and words[0] == "set":
        if len(words) < 4:
            ctx.say(tr("Использование: /cost set <модель или шаблон*> <вход $/1M> <выход $/1M> [кэш $/1M]",
                       "Usage: /cost set <model or pattern*> <input $/1M> <output $/1M> [cache $/1M]"), "warn")
            return
        try:
            row = {"input": float(words[2]), "output": float(words[3])}
            if len(words) > 4:
                row["cache_read"] = float(words[4])
        except ValueError:
            ctx.say(tr("Цены — числа в долларах за миллион токенов", "Prices are numbers in $ per million tokens"), "error")
            return
        overrides = dict(ctx.settings.get("pricing.overrides") or {})
        overrides[words[1]] = row
        ctx.settings.set("pricing.overrides", overrides)
        ctx.say(tr(f"Цена для {words[1]}: ${row['input']:g} / ${row['output']:g} за 1M", f"Price for {words[1]}: ${row['input']:g} / ${row['output']:g} per 1M"), "success")
        return
    if words and words[0] == "refresh":
        ctx.ui.status_text = tr("обновляю цены", "refreshing prices")
        result = await asyncio.to_thread(pricing.refresh_online, force=True)
        ctx.ui.status_text = ""
        ctx.say(tr(f"Каталог цен: {result.get('rows', 0)} моделей", f"Price catalogue: {result.get('rows', 0)} models")
                if result.get("ok") else tr(f"Не удалось: {result.get('error')}", f"Failed: {result.get('error')}"),
                "success" if result.get("ok") else "error")
        return
    if words and words[0] == "prices":
        models = await ctx.runtime.router.models()
        table = Table(box=None, pad_edge=False, show_header=True, header_style="bold")
        table.add_column(tr("Модель", "Model"))
        table.add_column(tr("Вход / выход за 1M", "In / out per 1M"))
        table.add_column(tr("Источник", "Source"), style="dim")
        for model in models[:80]:
            price = pricing.lookup(model.id, model.provider)
            table.add_row(model.id, pricing.describe(price), (price.source + (" ~" if price.approximate else "")) if price else "—")
        ctx.print(table)
        return
    costs = ctx.runtime.stats.costs(ctx.settings.get("pricing.overrides") or {})
    if not costs["rows"]:
        ctx.say(tr("Запросов ещё не было.", "No requests yet."))
        return
    ctx.print(Text(""), _usage_table(ctx, costs))
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim")
    table.add_column(style="bold")
    table.add_row(tr("Итого по ценам API", "Total at API prices"), pricing.format_usd(costs["api_equivalent"], precise=True))
    table.add_row(tr("Реальные расходы API", "Real API spend"), pricing.format_usd(costs["actual"], precise=True))
    ctx.print(table)
    age = pricing.cache_age()
    ctx.say(tr("Цены: каталог OpenRouter", "Prices: OpenRouter catalogue")
            + (tr(f" (обновлён {int(age // 3600)} ч назад)", f" (updated {int(age // 3600)}h ago)") if age is not None else
               tr(" недоступен — встроенная таблица", " unavailable — built-in table"))
            + tr(". Аккаунты веб-провайдеров (Genspark, Kiro, Antigravity…) не списывают деньги за запрос — это оценка по ценам API.",
                 ". Web-provider accounts (Genspark, Kiro, Antigravity…) are not billed per request — this is the API-price equivalent."))

@command("stats", "Статистика MemuCode: активность, модели, токены", "MemuCode statistics: activity, models, tokens",
         hint="[7|30|all]", category="info", while_busy=True)
async def stats_cmd(ctx: Context) -> None:
    from ... import code_stats
    arg = ctx.args.strip().lower()
    days = None if arg in ("all", "всё", "все") else (int(arg) if arg.isdigit() else 90)
    data = await asyncio.to_thread(code_stats.summary, days)
    if not data["messages"]:
        ctx.say(tr("Статистики пока нет.", "No statistics yet."))
        return
    by_day = {row["day"]: row for row in data["days"]}
    end = _dt.date.fromisoformat(data["today"])
    weeks = 18
    start = end - _dt.timedelta(days=end.weekday() + 7 * (weeks - 1))
    peak = max((row["tokens"] for row in data["days"]), default=1) or 1
    levels = ["subtle", "brand2", "brand", "bold brand"]
    grid = []
    for weekday in range(7):
        line = Text("  ")
        for week in range(weeks):
            day = start + _dt.timedelta(days=week * 7 + weekday)
            row = by_day.get(day.isoformat())
            if day > end:
                line.append("  ")
                continue
            if not row or not row["tokens"]:
                line.append("· ", style="subtle")
            else:
                level = min(3, int(row["tokens"] / peak * 3.999))
                line.append("■ ", style=levels[level])
        grid.append(line)
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    table.add_row(tr("Сессии", "Sessions"), str(data["sessions"]))
    table.add_row(tr("Сообщения", "Messages"), str(data["messages"]))
    table.add_row(tr("Токены", "Tokens"), f"{compact_number(data['tokens']['input'])} ↑ {compact_number(data['tokens']['output'])} ↓")
    table.add_row(tr("Активных дней", "Active days"), str(data["active_days"]))
    if data.get("peak_hour") is not None:
        table.add_row(tr("Самый активный час", "Busiest hour"), f"{data['peak_hour']:02d}:00")
    if data.get("favorite_model"):
        table.add_row(tr("Любимая модель", "Favourite model"), data["favorite_model"])
    total_cost = sum(float(row.get("cost") or 0) for row in data.get("models") or [])
    if total_cost:
        table.add_row(tr("≈ по ценам API", "≈ at API prices"), pricing.format_usd(total_cost))
    models = Table(box=None, pad_edge=False, show_header=False)
    for row in (data.get("models") or [])[:6]:
        models.add_row(row["model"], Text(f"{row['share']:.0f}%", style="dim"), compact_number(row["tokens"]))
    ctx.print(Text(""), Text(tr("Активность", "Activity") + f" · {data['range']}", style="bold"), *grid, Text(""),
              table, Text(""), models)
    if data.get("estimated"):
        ctx.say(tr("Часть токенов оценена по длине текста.", "Some token counts are estimates."))

@command("tasks", "Фоновые агенты и команды: вывод, указание, остановка", "Background agents and shells: output, follow-up, stop",
         hint="[number|name] [follow-up]", aliases=("bashes", "jobs"), category="agents", while_busy=True)
async def tasks(ctx: Context) -> None:
    runtime = ctx.runtime
    jobs = runtime.jobs.jobs
    shells = runtime.shells.list()
    head, _sep, rest = ctx.args.strip().partition(" ")
    if head:
        job = runtime.jobs.find(head)
        if job is None:
            ctx.say(tr("Нет такой задачи", "No such job"), "warn")
            return
        if rest.strip():

            try:
                outcome = runtime.jobs.send(job["id"], rest.strip())
            except ValueError as exc:
                ctx.say(str(exc), "warn")
                return
            ctx.say(tr(f"Агенту #{job['id']} {job.get('name', '')}: " + ("получит после текущего шага" if outcome["queued"]
                                                                         else "продолжает работу с вашим указанием"),
                       f"To agent #{job['id']} {job.get('name', '')}: " + ("delivered after its current step" if outcome["queued"]
                                                                          else "resumed with your instructions")))
            return
        _show_job(ctx, job)
        return
    if not jobs and not shells:
        ctx.say(tr("Фоновых задач нет. Запустить: /background <задача> или &задача", "No background work. Start one: /background <task> or &task"))
        return
    options = []
    for job in jobs:
        state = {"running": tr("работает", "running"), "done": tr("готово", "done")}.get(job["status"], job["status"])
        label = f"#{job['id']} {job['name']}: " if job.get("name") else f"#{job['id']} "
        options.append(dlg.Option(label + (job.get("description") or job["prompt"])[:70], ("job", job),
                                  f"{state} · {_duration((job.get('finished') or time.time()) - job['started'])}"
                                  + (f" · {job['role'][:30]}" if job.get("role") else "")))
    for shell in shells:
        state = tr("работает", "running") if shell["running"] else f"exit {shell['exit_code']}"
        options.append(dlg.Option(f"$ {shell['command'][:70]}", ("shell", shell), f"{shell['id']} · {state}"))
    choice = await ctx.select(tr("Фоновые задачи", "Background work"), options)
    if not choice:
        return
    kind, item = choice
    if kind == "job":
        _show_job(ctx, item)
        if item["status"] == "running" and await ctx.confirm(tr("Остановить задачу?", "Stop this job?")):
            runtime.jobs.cancel(item["id"])
    else:
        data = runtime.shells.read(item["id"])
        ctx.print(Text(f"$ {data['command']}", style="bold"), Text(data["new_output"][-4000:] or item.get("tail") or "", style="dim"))
        if data["running"] and await ctx.confirm(tr("Остановить команду?", "Stop this command?")):
            runtime.shells.kill(item["id"])

def _show_job(ctx: Context, job: Dict[str, Any]) -> None:
    who = " · ".join(part for part in (job.get("name"), job.get("role"), job.get("model")) if part)
    ctx.print(Text(f"#{job['id']} · {job['status']}" + (f" · {who}" if who else "") + f" · {job['prompt'][:200]}",
                   style="bold"),
              *[Text("  " + line.rstrip(), style="dim") for line in job["log"][-15:]])
    if job.get("result"):
        ctx.ui.write(ctx.r.assistant(job["result"]))

@command("mcp", "Серверы MCP: список, добавить, включить, перезапустить", "MCP servers: list, add, toggle, restart",
         category="agents", while_busy=False)
async def mcp(ctx: Context) -> None:
    from ... import mcp_client
    runtime = ctx.runtime
    while True:
        rows = runtime.mcp.catalogue()
        options = []
        for row in rows:
            state = (tr("работает", "running") + f" · {len(row['tools'])} " + tr("инстр.", "tools")) if row["running"] else (
                tr("выключен", "disabled") if not row["enabled"] else (tr("ошибка: ", "error: ") + row["error"][:60] if row["error"] else tr("не запущен", "stopped")))
            options.append(dlg.Option(row["name"], ("server", row), f"{row['source']} · {row['type']} · {state}"))
        options.append(dlg.Option(tr("＋ Добавить сервер", "＋ Add a server"), ("add", None),
                                  tr("команда (stdio) или адрес (HTTP)", "a command (stdio) or a URL (HTTP)")))
        choice = await ctx.select(tr("Серверы MCP", "MCP servers"), options)
        if not choice:
            return
        kind, row = choice
        if kind == "add":
            await _mcp_add(ctx)
            continue
        action = await ctx.select(row["name"], [
            dlg.Option(tr("Инструменты", "Tools"), "tools", ", ".join(row["tools"][:12])),
            dlg.Option(tr("Перезапустить", "Restart"), "restart"),
            dlg.Option(tr("Выключить" if row["enabled"] else "Включить", "Disable" if row["enabled"] else "Enable"), "toggle"),
            dlg.Option(tr("Удалить", "Remove"), "remove", style="dialog.danger"),
        ])
        if action == "tools":
            server = runtime.mcp.servers.get(row["name"])
            tools = server.tools if server is not None else []
            ctx.print(*[Text(f"  mcp__{row['name']}__{tool['name']}  ", style="brand") + Text(tool.get("description", "")[:100], style="dim")
                        for tool in tools] or [Text(tr("  нет инструментов (сервер не запущен?)", "  no tools (not running?)"), style="dim")])
        elif action == "restart":
            try:
                await runtime.mcp.restart(row["name"])
                runtime.invalidate_prompt()
                ctx.say(tr("Перезапущен", "Restarted"), "success")
            except Exception as exc:
                ctx.say(str(exc), "error")
        elif action in ("toggle", "remove"):
            if row["source"] == "project":
                ctx.say(tr("Серверы проекта меняются в .mcp.json", "Project servers are edited in .mcp.json"), "warn")
                continue
            config = mcp_client.load_config()
            name = row["name"]
            original = next((key for key in config["servers"] if key == name or key.replace(" ", "_") == name), name)
            if action == "remove":
                config["servers"].pop(original, None)
            elif original in config["servers"]:
                config["servers"][original]["enabled"] = not config["servers"][original].get("enabled", True)
            mcp_client.save_config(config)
            live = runtime.mcp.servers.pop(name, None)
            if live is not None:
                await live.stop()
            runtime.mcp.register_tools()
            runtime.invalidate_prompt()

async def _mcp_add(ctx: Context) -> None:
    from ... import mcp_client
    name = await ctx.ask(tr("Имя сервера (латиница)", "Server name"))
    if not name:
        return
    name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name.strip())[:40]
    target = await ctx.ask(tr("Команда запуска (например: npx -y @modelcontextprotocol/server-github) или адрес https://…",
                              "Launch command (e.g. npx -y @modelcontextprotocol/server-github) or an https:// URL"))
    if not target:
        return
    where = await ctx.select(tr("Где сохранить?", "Save where?"), [
        dlg.Option(tr("Для всех проектов (общий с приложением MemuAI)", "All projects (shared with the MemuAI app)"), "user"),
        dlg.Option(tr("Только этот проект (.mcp.json)", "This project only (.mcp.json)"), "project")])
    if not where:
        return
    env_text = await ctx.ask(tr("Переменные окружения KEY=VALUE через пробел (необязательно)", "Environment KEY=VALUE pairs (optional)")) or ""
    env = dict(item.split("=", 1) for item in env_text.split() if "=" in item)
    if target.startswith(("http://", "https://")):
        spec = {"type": "http", "url": target}
    else:
        import shlex
        parts = shlex.split(target, posix=os.name != "nt")
        if os.name == "nt":

            parts = [part[1:-1] if len(part) >= 2 and part[0] == part[-1] and part[0] in "\"'" else part
                     for part in parts]
        if not parts:
            return
        spec = {"command": parts[0], "args": parts[1:]}
    if env:
        spec["env"] = env
    if where == "user":
        if spec.get("type") == "http":
            ctx.say(tr("HTTP-серверы сохраняются в .mcp.json проекта", "HTTP servers are saved in the project's .mcp.json"), "warn")
            where = "project"
        else:
            config = mcp_client.load_config()
            config["servers"][name] = {**spec, "enabled": True}
            mcp_client.save_config(config)
    if where == "project":
        path = ctx.runtime.root / ".mcp.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            data = exc
        if not isinstance(data, dict) or not isinstance(data.get("mcpServers", {}), dict):

            ctx.say(tr(f"{path} не читается как JSON-объект — файл не изменён. Исправьте его и повторите.",
                       f"{path} is not a readable JSON object — the file was left untouched. Fix it and try again."),
                    "error")
            return
        data.setdefault("mcpServers", {})[name] = spec
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if not ctx.runtime.trusted():
            ctx.runtime.settings.trust()
    ctx.ui.status_text = tr("запускаю сервер", "starting the server")
    try:
        await ctx.runtime.mcp.restart(name)
        server = ctx.runtime.mcp.servers.get(name)
        ctx.say(tr(f"Сервер {name} работает: инструментов {len(server.tools) if server else 0}",
                   f"Server {name} is running: {len(server.tools) if server else 0} tools"), "success")
    except Exception as exc:
        ctx.say(tr(f"Сохранено, но запуск не удался: {exc}", f"Saved, but it failed to start: {exc}"), "warn")
    finally:
        ctx.ui.status_text = ""
    ctx.runtime.invalidate_prompt()

@command("memory", "Файлы памяти: правила для агента (MEMUCODE.md и др.)", "Memory files: rules for the agent (MEMUCODE.md etc.)",
         category="context", while_busy=True)
async def memory(ctx: Context) -> None:
    from ..project import memory_target
    runtime = ctx.runtime
    loaded = runtime.memory_files()
    options = [dlg.Option(f"{row.path.name}", str(row.path), f"{row.scope} · {row.path}") for row in loaded]
    for scope, label in (("project", tr("Проект: MEMUCODE.md", "Project: MEMUCODE.md")),
                         ("local", tr("Лично в проекте: MEMUCODE.local.md", "Personal in project: MEMUCODE.local.md")),
                         ("user", tr("Мои правила для всех проектов", "My rules for all projects"))):
        target = memory_target(runtime.root, scope)
        if not any(Path(option.value) == target for option in options):
            options.append(dlg.Option(label + tr(" (создать)", " (create)"), str(target), str(target)))
    choice = await ctx.select(tr("Какой файл открыть в редакторе?", "Open which file in the editor?"), options,
                              body=ctx.r.render(Text("  " + tr("Быстро добавить правило: начните сообщение с #",
                                                                "Quick add: start a message with #"), style="dim")))
    if not choice:
        return
    path = Path(choice)
    from ..editor import edit_file
    if await edit_file(ctx, path, title=path.name, create="# MEMUCODE.md\n\n"):
        runtime.invalidate_prompt(memory=True)
        ctx.say(tr(f"Память обновлена: {path}", f"Memory updated: {path}"), "success")

@command("remember", "Запомнить факт о себе в памяти MemuAI (общей с окном MemuAI)",
         "Remember a fact about you in MemuAI memory (shared with the MemuAI window)",
         hint="[--code] <fact> | list", category="context", while_busy=True)
async def remember(ctx: Context) -> None:
    """Round 29: MemuAI memory v2 from the console. ``--code`` limits the fact to MemuCode."""
    from ... import memory_v2
    text = ctx.args.strip()
    runtime = ctx.runtime
    if not text or text == "list":
        facts = memory_v2.context_facts("code", "", budget_tokens=100000, note_use=False)
        if not facts:
            ctx.say(tr("В памяти MemuAI пока ничего нет. /remember <факт> — добавить.",
                       "MemuAI memory is empty. /remember <fact> adds one."))
            return
        table = Table(show_header=False, box=None, padding=(0, 1))
        for row in facts[:60]:
            scope = tr(" · только MemuCode", " · MemuCode only") if row.get("scope") == ["code"] else ""
            table.add_row(Text("*" if row.get("pinned") else "-", style="dim"),
                          Text(str(row["text"])[:300]), Text(f"{int(row['confidence'] * 100)}%{scope}", style="dim"))
        ctx.print(Text(""), table)
        return
    scope = None
    if text.startswith("--code "):
        scope, text = ["code"], text[len("--code "):].strip()
    if memory_v2.sensitivity(text)[0] == "hard":
        ctx.say(tr("Похоже на пароль или ключ — такое в память не сохраняется.",
                   "This looks like a password or a key — it is never saved to memory."), "warn")
        return
    out = memory_v2.add_manual(text, scope=scope, service="code",
                               source={"service": "code", "detail": "cli", "by": "user",
                                       "excerpt": str(runtime.root)[:200]})
    result = out["result"]
    if result["added"] or result["updated"]:
        runtime.invalidate_prompt(memory=True)
        ctx.say(tr("Запомнил: ", "Remembered: ") + text[:120], "success")
    elif result["duplicates"]:
        ctx.say(tr("Это уже есть в памяти.", "That is already in memory."))
    else:
        ctx.say(tr("Не сохранено (память выключена или правило запрещает).",
                   "Not saved (memory is off or a rule forbids it)."), "warn")

@command("tools", "Инструменты агента и их состояние", "The agent's tools and their state", category="agents", while_busy=True)
async def tools(ctx: Context) -> None:
    runtime = ctx.runtime
    table = Table(box=None, pad_edge=False, show_header=True, header_style="bold")
    table.add_column(tr("Инструмент", "Tool"), style="brand")
    table.add_column(tr("Тип", "Kind"), style="dim")
    table.add_column(tr("Описание", "Description"))
    for tool in runtime.tools.tools.values():
        enabled = tool.enabled(runtime)
        table.add_row(tool.name + ("" if enabled else " ✗"), tool.kind, (tool.short or tool.description)[:90])
    ctx.print(table)

@command("bug", "Собрать отчёт о проблеме (без ключей) в Загрузки/MemuAI", "Collect a problem report (no keys) in Downloads/MemuAI",
         aliases=("feedback",), category="info", while_busy=True)
async def bug(ctx: Context) -> None:
    from ..doctor import run_checks
    from ..settings import flatten, mask
    note = await ctx.ask(tr("Что случилось? (необязательно)", "What happened? (optional)"))
    if note is None:
        return
    checks = await run_checks(ctx.runtime)
    runtime = ctx.runtime
    settings = {key: mask(key, value) for key, value in flatten(runtime.settings.data).items()}
    errors = ""
    try:
        errors = (paths.home() / "errors.log").read_text(encoding="utf-8")[-12000:]
    except OSError:
        pass
    lines = [f"# MemuCode report {_dt.datetime.now():%Y-%m-%d %H:%M}", "", note, "",
             f"- version: {VERSION}", f"- python: {platform.python_version()}", f"- os: {platform.platform()}",
             f"- model: {runtime.model}", f"- session: {runtime.session.id}", "", "## Checks", ""]
    lines += [f"- [{row['status']}] {row['name']}: {row['detail']}" for row in checks]
    lines += ["", "## Settings (secrets masked)", "", "```json", json.dumps(settings, ensure_ascii=False, indent=1, default=str), "```",
              "", "## Recent errors", "", "```", errors, "```"]
    path = paths.downloads_dir() / f"memucode-report-{int(time.time())}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    ctx.say(tr(f"Отчёт: {path}", f"Report: {path}"), "success")

RELEASE_NOTES = {
    "ru": """**MemuCode CLI {version}** — агент для кода из комплекта MemuAI.

- Агент для кода в терминале: чтение, поиск, правки с диффом, команды в песочнице, git, веб, браузер, Computer Use, MCP, суб-агенты.
- Все модели ваших аккаунтов через MemuAI Proxy и прямые API (Anthropic, OpenAI, OpenRouter) с нативными инструментами.
- Режимы разрешений (Shift+Tab): обычный, принимать правки, план, авто (с моделью решений), без подтверждений.
- Больше 60 команд: /model, /compact, /context, /usage с оценкой стоимости, /rewind, /branch, /btw, /goal, /advisor, /background и другие.
- Сессии сохраняются и продолжаются: `memucode -c`, `/resume`. Откат правок без git.
- Хуки, свои команды, агенты и скиллы в формате Claude Code.

**Новое:** `/schedule` (задачи по расписанию, в том числе через планировщик Windows), `/dashboard` и `/resume-claude`, `/resume-codex`, `/resume-grok`, `/resume-gemini`, `/resume-agy`, `/resume-opencode`, `/resume-cursor` (сессии других инструментов), `/import`, `/decisions` с живым каталогом моделей решений, `/model auto` и `/agents` с несколькими моделями и вашими заметками, менеджер `/skills`, один режим разрешений во всех окнах, поиск моделей по провайдеру и имени, мягкое чтение вызовов инструментов. `/login` и `/logout` заменены на `/proxy add` и `/proxy remove`.

**Раунд 33:** `/auto-continue` — кончились лимиты? MemuCode сам отправит «continue» в 18:30, через 2 часа или когда провайдер снимет лимит (`auto`), с повторами и запасной моделью; `/help-ask` и встроенный справочник — MemuCode точно знает свои команды, клавиши и настройки.""",
    "en": """**MemuCode CLI {version}** — the coding agent of the MemuAI suite.

- A coding agent in your terminal: read, search, diff-reviewed edits, sandboxed commands, git, web, browser, Computer Use, MCP, sub-agents.
- Every model of your accounts through MemuAI Proxy, plus direct APIs (Anthropic, OpenAI, OpenRouter) with native tools.
- Permission modes (Shift+Tab): default, accept edits, plan, auto (with a decision model), bypass.
- 60+ commands: /model, /compact, /context, /usage with cost estimates, /rewind, /branch, /btw, /goal, /advisor, /background and more.
- Sessions persist and resume: `memucode -c`, `/resume`. Undo edits without git.
- Hooks, custom commands, agents and skills in the Claude Code format.

**New:** `/schedule` (tasks on a schedule, also through the Windows Task Scheduler), `/dashboard` and `/resume-claude`, `/resume-codex`, `/resume-grok`, `/resume-gemini`, `/resume-agy`, `/resume-opencode`, `/resume-cursor` (sessions of other tools), `/import`, `/decisions` with a live catalogue of decision models, `/model auto` and `/agents` with several models and your remarks, the `/skills` manager, one permission mode in every window, model search by provider and name, forgiving reading of tool calls. `/login` and `/logout` are replaced by `/proxy add` and `/proxy remove`.

**Round 33:** `/auto-continue` — out of limits? MemuCode sends “continue” by itself at 18:30, in 2 hours or when the provider lifts the limit (`auto`), with retries and a fallback model; `/help-ask` and a built-in guide — MemuCode knows its own commands, keys and settings exactly.""",
}

@command("release-notes", "Что нового в MemuCode", "What's new in MemuCode", category="info", while_busy=True)
async def release_notes(ctx: Context) -> None:

    ctx.md(RELEASE_NOTES["en" if language() == "en" else "ru"].replace("{version}", VERSION))
