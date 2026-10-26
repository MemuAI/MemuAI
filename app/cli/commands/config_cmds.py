"""Settings commands: /settings /config /theme /color /vim /lang /output-style
/statusline /permissions /add-dir /cd /terminal-setup /proxy
/notifications /sandbox /hooks /install."""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.table import Table
from rich.text import Text

from .. import i18n, paths
from ..i18n import tr
from ..permissions import MODES, SYNCED, mode_explain, mode_icon, mode_title, parse_rule
from ..settings import DEFAULTS, SettingsError, flatten, mask
from ..tui import dialogs as dlg
from ..tui.theme import ACCENTS, THEMES, palette_for, pt_style
from . import Context, command

SETTING_INFO: Dict[str, tuple] = {
    "permission_mode": ("Режим разрешений при запуске", "Permission mode at start"),
    "permission_sync": ("Один режим разрешений во всех окнах MemuCode", "One permission mode in every MemuCode window"),
    "effort": ("Глубина рассуждений", "Reasoning effort"),
    "thinking": ("Размышления модели", "Extended thinking"),
    "show_thinking": ("Показывать текст размышлений", "Show the thinking text"),
    "auto_compact": ("Автоматически сжимать историю", "Auto-compact the history"),
    "auto_compact_threshold": ("Порог автосжатия, % окна", "Auto-compact threshold, % of window"),
    "context_budget": ("Экономия токенов: после стольких токенов истории старые большие выводы сворачиваются (0 — выкл.)",
                       "Token saving: past this many history tokens old large outputs are folded (0 = off)"),
    "max_turns": ("Предел раундов инструментов на запрос", "Tool rounds per request before asking"),
    "subagents": ("Суб-агенты: ask — только по вашей просьбе, auto — решает модель", "Sub-agents: ask — only when you ask, auto — the model decides"),
    "theme": ("Тема", "Theme"), "accent": ("Цвет акцента", "Accent colour"),
    "language": ("Язык интерфейса", "Interface language"), "vim_mode": ("Режим Vim в поле ввода", "Vim mode in the input"),
    "output_style": ("Стиль ответов", "Output style"), "verbose": ("Подробный вывод инструментов", "Verbose tool output"),
    "spinner_tips": ("Советы во время работы", "Tips while working"),
    "todo_panel": ("Показывать список задач", "Show the todo list"),
    "notifications": ("Уведомления о завершении", "Completion notifications"),
    "notify_after_seconds": ("Уведомлять, если задача дольше, с", "Notify when a task takes longer than, s"),
    "shell": ("Оболочка для команд", "Shell for commands"), "editor": ("Внешний редактор (Ctrl+G)", "External editor (Ctrl+G)"),
    "auto_title": ("Автоназвание разговоров", "Auto-title conversations"),
    "include_co_authored_by": ("Co-Authored-By в коммитах", "Co-Authored-By in commits"),
    "web.fetch_ask": ("Спрашивать перед открытием страниц", "Ask before fetching web pages"),
    "browser.enabled": ("Инструмент Browser", "Browser tool"), "computer.enabled": ("Инструмент Computer Use", "Computer Use tool"),
    "computer.ask_each_action": ("Подтверждать каждое действие Computer Use", "Confirm each Computer Use action"),
    "sandbox.enabled": ("Песочница для команд (Job Object)", "Command sandbox (Job Object)"),
    "sandbox.env": ("Переменные окружения команд", "Environment of commands"),
    "pricing.show_in_footer": ("Стоимость в строке состояния", "Cost in the status line"),
    "footer_metric": ("Показатель в строке состояния", "Status line metric"),
    "pricing.online_catalog": ("Обновлять цены из каталога OpenRouter", "Refresh prices from the OpenRouter catalogue"),
    "stats.record": ("Учитывать в статистике MemuCode", "Count in MemuCode statistics"),
    "tool_protocol": ("Протокол инструментов", "Tool protocol"), "backend": ("Подключение к моделям", "Model backend"),
    "proxy_autostart": ("Запускать MemuAI Proxy, если он выключен", "Start MemuAI Proxy when it is not running"),
    "cleanup_period_days": ("Хранить разговоры, дней", "Keep conversations, days"),
    "keep_recent_messages": ("Сообщений без сжатия", "Messages kept verbatim"),
    "respect_gitignore": ("Учитывать .gitignore", "Respect .gitignore"),
}
CHOICES = {
    "footer_metric": ["cost", "tokens", "duration", "tools", "off"],
    "permission_mode": list(MODES), "effort": ["auto", "off", "on", "minimal", "low", "medium", "high", "xhigh", "max"],
    "theme": list(THEMES), "language": ["en"], "output_style": ["default", "explanatory", "learning", "concise"],
    "notifications": ["auto", "bell", "toast", "off"], "subagents": ["ask", "auto", "off"], "shell": ["auto", "bash", "powershell", "pwsh", "cmd"],
    "sandbox.env": ["filtered", "strict", "full"], "tool_protocol": ["auto", "text", "native"],
    "backend": ["auto", "proxy", "direct"], "accent": [""] + list(ACCENTS),
}

def _value_text(value: Any) -> str:
    if isinstance(value, bool):
        return tr("вкл", "on") if value else tr("выкл", "off")
    if value in ("", None):
        return tr("по умолчанию", "default")
    return str(value)

def apply_live(ctx: Context, key: str) -> None:
    """Settings that change the interface right away."""
    ui = ctx.ui
    if key in ("theme", "accent"):
        ui.palette = palette_for(ctx.settings.get("theme"), ctx.settings.get("accent"))
        ui.renderer.set_palette(ui.palette)
        if ui.app is not None:
            ui.app.style = pt_style(ui.palette)
    elif key == "language":
        i18n.set_language(str(ctx.settings.get("language") or ""))
        ctx.runtime.invalidate_prompt()
    elif key == "vim_mode" and ui.app is not None:
        from prompt_toolkit.enums import EditingMode
        ui.app.editing_mode = EditingMode.VI if ctx.settings.get("vim_mode") else EditingMode.EMACS
    elif key == "verbose":
        ui.verbose = bool(ctx.settings.get("verbose"))
    elif key == "todo_panel":
        ui.show_todos = bool(ctx.settings.get("todo_panel"))
    elif key == "permission_mode":
        mode = str(ctx.settings.get("permission_mode"))
        ctx.runtime.permissions.set_mode(mode, source="command", persist=mode in SYNCED)
    elif key in ("output_style", "shell", "tool_protocol"):
        ctx.runtime.invalidate_prompt()

async def edit_setting(ctx: Context, key: str, scope: str = "user") -> bool:
    current = ctx.settings.get(key)
    label = tr(*SETTING_INFO[key]) if key in SETTING_INFO else key
    if isinstance(DEFAULTS_FLAT.get(key), bool):
        value = not bool(current)
    elif key in CHOICES:
        options = [dlg.Option(_value_text(choice) if choice else tr("по умолчанию", "default"), choice,
                              hint="✓" if choice == current else "") for choice in CHOICES[key]]
        value = await ctx.select(label, options, index=CHOICES[key].index(current) if current in CHOICES[key] else 0)
        if value is None:
            return False
    else:
        text = await ctx.ask(label, default="" if current is None else str(current))
        if text is None:
            return False
        value = text
    try:
        ctx.settings.set(key, value, scope)
    except SettingsError as exc:
        ctx.say(str(exc), "error")
        return False
    apply_live(ctx, key)
    return True

DEFAULTS_FLAT = flatten(DEFAULTS)

@command("config", "Настройки через команды", "Read or change configuration keys", hint="[get|set|list …]", category="config", while_busy=True)
async def config_cmd(ctx: Context) -> None:
    await _config_cli(ctx, ctx.words() or ["list"])

@command("settings", "Настройки MemuCode", "MemuCode settings", aliases=("preferences",),
         category="config", immediate=True, while_busy=True)
async def settings_cmd(ctx: Context) -> None:
    index = 0
    while True:
        options = [dlg.Option(tr(*info), key, hint=_value_text(ctx.settings.get(key))) for key, info in SETTING_INFO.items()]
        options.append(dlg.Option(tr("Открыть файл настроек…", "Open the settings file…"), "__file__",
                                  str(paths.user_settings_file())))
        choice = await ctx.select(tr("Настройки (Enter — изменить, Esc — закрыть)", "Settings (Enter to change, Esc to close)"),
                                  options, filterable=True, index=index, max_visible=12)
        if choice is None:
            return
        if choice == "__file__":
            _open_file(paths.user_settings_file())
            return
        index = list(SETTING_INFO).index(choice)
        await edit_setting(ctx, choice)

async def _config_cli(ctx: Context, words: List[str]) -> None:
    action = words[0].lower()
    scope = "user"
    for flag, name in (("--project", "project"), ("--local", "local"), ("--user", "user"), ("-g", "user")):
        if flag in words:
            scope = name
            words = [word for word in words if word != flag]
    if action in ("list", "ls"):
        rows = flatten(ctx.settings.data)
        table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
        table.add_column(tr("Ключ", "Key"))
        table.add_column(tr("Значение", "Value"))
        table.add_column(tr("Откуда", "Source"), style="dim")
        for key in sorted(rows):
            table.add_row(key, json.dumps(mask(key, rows[key]), ensure_ascii=False)[:80], ctx.settings.source_of(key))
        ctx.print(table)
        for warning in ctx.settings.warnings:
            ctx.say(warning, "warn")
        return
    if action == "get" and len(words) >= 2:
        key = words[1]
        ctx.print(Text(f"{key} = {json.dumps(mask(key, ctx.settings.get(key)), ensure_ascii=False)}  "
                       f"({ctx.settings.source_of(key)})"))
        return
    if action == "set" and len(words) >= 3:
        key, value = words[1], " ".join(words[2:])
        try:
            parsed = json.loads(value) if value[:1] in "[{\"" or value in ("true", "false", "null") or re.fullmatch(r"-?\d+(\.\d+)?", value) else value
            ctx.settings.set(key, parsed, scope)
        except (SettingsError, ValueError) as exc:
            ctx.say(str(exc), "error")
            return
        apply_live(ctx, key)
        ctx.say(tr(f"{key} = {value} ({scope})", f"{key} = {value} ({scope})"), "success")
        return
    if action in ("unset", "reset") and len(words) >= 2:
        removed = ctx.settings.unset(words[1], scope)
        apply_live(ctx, words[1])
        ctx.say(tr("Сброшено" if removed else "Такого ключа нет", "Reset" if removed else "No such key"),
                "success" if removed else "warn")
        return
    if action == "edit":
        target = ctx.settings.file_for(scope)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("{\n}\n", encoding="utf-8")
        _open_file(target)
        return
    ctx.say(tr("Использование: /config list | get <ключ> | set <ключ> <значение> [--project|--local] | unset <ключ> | edit",
               "Usage: /config list | get <key> | set <key> <value> [--project|--local] | unset <key> | edit"), "warn")

def _open_file(path: Path) -> None:
    try:
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", str(path)])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", str(path)])
    except OSError:
        pass

SAMPLE_DIFF = "@@ -1,3 +1,3 @@\n def greet(name):\n-    return 'Hi ' + name\n+    return f'Hello, {name}!'\n \n"

@command("theme", "Тема оформления терминала", "Terminal colour theme", hint="[name]", category="config", while_busy=True)
async def theme(ctx: Context) -> None:
    ui = ctx.ui
    wanted = ctx.args.strip()
    original = str(ctx.settings.get("theme") or "dark")
    if wanted not in THEMES:
        names = {"dark": tr("Тёмная", "Dark"), "light": tr("Светлая", "Light"),
                 "dark-daltonized": tr("Тёмная для дальтоников", "Dark (colour-blind friendly)"),
                 "light-daltonized": tr("Светлая для дальтоников", "Light (colour-blind friendly)"),
                 "dark-ansi": tr("Тёмная, 16 цветов терминала", "Dark, terminal 16 colours"),
                 "light-ansi": tr("Светлая, 16 цветов терминала", "Light, terminal 16 colours")}

        def preview(value: Any) -> None:
            if value in THEMES:
                ui.palette = palette_for(value, ctx.settings.get("accent"))
                ui.renderer.set_palette(ui.palette)
                dialog.body = ui.renderer.render(ui.renderer.diff(SAMPLE_DIFF))
                if ui.app is not None:
                    ui.app.style = pt_style(ui.palette)
                    ui.invalidate()

        dialog = dlg.Dialog(title=tr("Тема", "Theme"), options=[dlg.Option(names[key], key, hint="✓" if key == original else "")
                                                               for key in THEMES],
                            body=ui.renderer.render(ui.renderer.diff(SAMPLE_DIFF)), on_change=preview,
                            index=list(THEMES).index(original) if original in THEMES else 0)
        wanted = await ui.open_dialog(dialog)
        if not wanted:
            preview(original)
            return
    ctx.settings.set("theme", wanted)
    apply_live(ctx, "theme")
    ctx.say(tr(f"Тема: {wanted}", f"Theme: {wanted}"), "success")

@command("color", "Цвет акцента (рамки, спиннер) — удобно различать сессии", "Accent colour — handy to tell sessions apart",
         hint="[color|#hex|reset]", category="config", while_busy=True)
async def color(ctx: Context) -> None:
    value = ctx.args.strip().lower()
    if value in ("reset", "default", "сброс"):
        value = ""
    elif value not in ACCENTS and not re.fullmatch(r"#[0-9a-f]{6}", value):
        options = [dlg.Option(tr("По умолчанию (MemuAI)", "Default (MemuAI)"), "")]
        for name, hex_value in ACCENTS.items():
            options.append(dlg.Option(name, name, hex_value))
        chosen = await ctx.select(tr("Цвет акцента", "Accent colour"), options)
        if chosen is None:
            return
        value = chosen
    ctx.settings.set("accent", value, "local" if ctx.runtime.trusted() else "user")
    apply_live(ctx, "accent")
    ctx.say(tr(f"Акцент: {value or 'по умолчанию'}", f"Accent: {value or 'default'}"), "success")

@command("vim", "Режим Vim в поле ввода вкл/выкл", "Toggle Vim editing mode", category="config", while_busy=True)
async def vim(ctx: Context) -> None:
    value = not bool(ctx.settings.get("vim_mode"))
    ctx.settings.set("vim_mode", value)
    apply_live(ctx, "vim_mode")
    ctx.say(tr("Режим Vim: ", "Vim mode: ") + (tr("вкл (Esc — normal, i — insert)", "on (Esc for normal, i for insert)")
                                             if value else tr("выкл", "off")), "success")

@command("lang", "Язык интерфейса", "Interface language (English)", aliases=("language",),
         category="config", while_busy=True)
async def lang(ctx: Context) -> None:
    ctx.say("MemuCode CLI uses English for its interface.")

@command("output-style", "Стиль ответов: обычный, объясняющий, обучающий, краткий", "Answer style: default, explanatory, learning, concise",
         hint="[style]", category="config", while_busy=True)
async def output_style(ctx: Context) -> None:
    from ..project import load_styles
    styles = load_styles()
    value = ctx.args.strip().lower()
    if value not in styles:
        descriptions = {"default": tr("Как обычно", "As usual"),
                        "explanatory": tr("Объясняет решения и устройство кода по ходу работы", "Explains choices and the codebase as it works"),
                        "learning": tr("Даёт вам написать ключевые куски самим — для обучения", "Asks you to write key pieces yourself, to learn"),
                        "concise": tr("Максимально коротко", "As short as possible")}
        current = ctx.settings.get("output_style")
        options = [dlg.Option(title, key, descriptions.get(key, ""), hint="✓" if key == current else "")
                   for key, (title, _text) in styles.items()]
        value = await ctx.select(tr("Стиль ответов", "Output style"), options)
        if not value:
            return
    ctx.settings.set("output_style", value)
    apply_live(ctx, "output_style")
    ctx.say(tr(f"Стиль ответов: {value}", f"Output style: {value}"), "success")

@command("statusline", "Строка состояния: встроенная, своя команда или скрыть", "Status line: built-in, a custom command, or off",
         hint="[command|builtin|off]", category="config", while_busy=True)
async def statusline(ctx: Context) -> None:
    value = ctx.args.strip()
    if value in ("builtin", "default"):
        ctx.settings.set("statusline", {"type": "builtin", "command": ""})
    elif value == "off":
        ctx.settings.set("statusline", {"type": "command", "command": "echo"})
    elif value:
        ctx.settings.set("statusline", {"type": "command", "command": value})
    else:
        ctx.md(tr(
            "**Строка состояния** справа внизу. Своя команда получает JSON (модель, папка, стоимость) на stdin "
            "и печатает одну строку, как в Claude Code:\n\n`/statusline python ~/status.py` · `/statusline builtin`",
            "The **status line** sits bottom right. A custom command receives JSON (model, folder, cost) on stdin "
            "and prints one line, like in Claude Code:\n\n`/statusline python ~/status.py` · `/statusline builtin`"))
        return
    ctx.ui.statusline_cache = ("", 0.0)
    ctx.say(tr("Строка состояния обновлена", "Status line updated"), "success")

MODE_ORDER = ("default", "acceptEdits", "plan", "auto", "bypassPermissions")
_MODE_WORDS = {
    "default": ("default", "normal", "ask", "ask-first", "обычный", "спрашивать"),
    "acceptedits": ("acceptedits", "accept-edits", "accept", "edits", "принимать", "правки"),
    "plan": ("plan", "план"),
    "auto": ("auto", "авто"),
    "bypasspermissions": ("bypasspermissions", "bypass", "yolo", "always-approve", "без"),
}

def parse_mode(text: str) -> str:
    """A permission mode from what a person types (``auto``, ``accept edits``, ``обычный``…); "" when unknown."""
    word = re.sub(r"[\s_]+", "-", text.strip().lower())
    for mode, names in _MODE_WORDS.items():
        if word in names or word.replace("-", "") == mode:
            return next(real for real in MODE_ORDER if real.lower() == mode)
    return ""

def _judge_note(ctx: Context) -> str:
    """Who decides in auto mode, in plain words."""
    decisions = ctx.runtime.decisions
    uses = ctx.settings.get("decisions.uses") or {}
    if decisions.enabled and decisions.provider != "model" and uses.get("auto_mode", True):
        return tr(f"Сейчас решает {decisions.title} · {decisions.model_for('auto_mode') or decisions.model}.",
                  f"Right now: {decisions.title} · {decisions.model_for('auto_mode') or decisions.model}.")
    if ctx.runtime.small_model_available():
        return tr("Сейчас решает быстрая чат-модель; модель решений: /decisions.",
                  "Right now a fast chat model judges; connect a decision model: /decisions.")
    return tr("Сейчас решают встроенные правила; модель решений: /decisions.",
              "Right now built-in rules judge; connect a decision model: /decisions.")

async def set_permission_mode(ctx: Context, mode: str) -> bool:
    """Switch the mode on the user's word. A chosen mode applies to every window (plan mode: this one)."""
    perms = ctx.runtime.permissions
    if mode == perms.mode:
        ctx.say(tr(f"Режим уже такой: {mode_title(mode)}", f"Already in this mode: {mode_title(mode)}"))
        return True
    if mode == "bypassPermissions":
        shared = perms.sync_enabled()
        if not await ctx.confirm(
                tr("Отключить все подтверждения?", "Turn off every permission prompt?"),
                tr("Агент будет менять файлы и запускать команды без вопросов." + (
                    " Режим применится ко ВСЕМ окнам MemuCode." if shared else ""),
                   "The agent will edit files and run commands without asking." + (
                       " The mode applies to EVERY MemuCode window." if shared else "")), danger=True):
            return False
    perms.set_mode(mode, source="command", persist=mode in SYNCED)
    return True

async def pick_permission_mode(ctx: Context) -> None:
    perms = ctx.runtime.permissions
    options = []
    for mode in MODE_ORDER:
        text = mode_explain(mode) + (" " + _judge_note(ctx) if mode == "auto" else "")
        options.append(dlg.Option(f"{mode_icon(mode)}{mode_title(mode)}", mode, text,
                                  hint=tr("сейчас", "now") if mode == perms.mode else ""))
    where = (tr("Выбранный режим действует во всех окнах MemuCode (режим плана — только в этом).",
                "The mode you choose applies to every MemuCode window (plan mode: this one only).")
             if perms.sync_enabled() else tr("Режим действует только в этом окне (настройка permission_sync выключена).",
                                              "The mode applies to this window only (the permission_sync setting is off)."))
    if perms.pinned_by_flag:
        where += " " + tr("Это окно запущено с --permission-mode: пока вы не выберете режим здесь, оно его не меняет.",
                          "This window was started with --permission-mode: it keeps it until you choose a mode here.")
    choice = await ctx.select(tr("Режим разрешений", "Permission mode"), options,
                              body=ctx.r.render(Text("  " + where, style="dim")), index=MODE_ORDER.index(perms.mode)
                              if perms.mode in MODE_ORDER else 0)
    if choice:
        await set_permission_mode(ctx, choice)

@command("mode", "Режим разрешений: спрашивать, принимать правки, план, авто, без подтверждений (Shift+Tab)",
         "Permission mode: ask first, accept edits, plan, auto, bypass (Shift+Tab)", hint="[mode]", category="config",
         while_busy=True)
async def mode_cmd(ctx: Context) -> None:
    word = ctx.args.strip()
    if word:
        mode = parse_mode(word)
        if not mode:
            ctx.say(tr(f"Нет режима «{word}». Режимы: ", f"No mode “{word}”. Modes: ") + ", ".join(MODE_ORDER), "warn")
            return
        await set_permission_mode(ctx, mode)
        return
    await pick_permission_mode(ctx)

@command("permissions", "Режим разрешений и правила: что агент делает сам, а о чём спрашивает",
         "Permission mode and rules: what the agent does on its own and what it asks about",
         aliases=("allowed-tools", "approvals"), hint="[mode]", category="config", while_busy=True)
async def permissions(ctx: Context) -> None:
    runtime = ctx.runtime
    word = ctx.args.strip()
    if word:
        mode = parse_mode(word)
        if not mode:
            ctx.say(tr(f"Нет режима «{word}». Режимы: ", f"No mode “{word}”. Modes: ") + ", ".join(MODE_ORDER), "warn")
            return
        await set_permission_mode(ctx, mode)
        return
    while True:
        rows = []
        for kind in ("allow", "ask", "deny"):
            for rule in runtime.permissions.rules(kind):
                rows.append((kind, rule))
        table = Table(box=None, pad_edge=False, show_header=True, header_style="bold")
        table.add_column(tr("Тип", "Type"))
        table.add_column(tr("Правило", "Rule"))
        table.add_column(tr("Где", "Where"), style="dim")
        labels = {"allow": tr("разрешить", "allow"), "ask": tr("спросить", "ask"), "deny": tr("запретить", "deny")}
        for kind, rule in rows:
            table.add_row(labels[kind], str(rule), rule.source)
        current_mode = runtime.permissions.mode
        body = ctx.r.render(Text("  " + tr(f"Режим сейчас: {mode_icon(current_mode)}{mode_title(current_mode)} — {mode_explain(current_mode)}",
                                           f"Mode now: {mode_icon(current_mode)}{mode_title(current_mode)} — {mode_explain(current_mode)}"),
                                 style="bold"),
                            table if rows else Text("  " + tr("Правил пока нет.", "No rules yet."), style="dim"))
        choice = await ctx.select(tr("Разрешения", "Permissions"), [
            dlg.Option(tr("Режим разрешений…", "Permission mode…"), "pick",
                       tr("спрашивать, принимать правки, план, авто, без подтверждений — во всех окнах",
                          "ask first, accept edits, plan, auto, bypass — in every window"),
                       hint=f"{mode_icon(current_mode)}{mode_title(current_mode)}"),
            dlg.Option(tr("Добавить «разрешить»", "Add an allow rule"), "allow",
                       tr("Например Bash(npm test:*), Edit(src/**), WebFetch(domain:docs.python.org)",
                          "e.g. Bash(npm test:*), Edit(src/**), WebFetch(domain:docs.python.org)")),
            dlg.Option(tr("Добавить «спрашивать»", "Add an ask rule"), "ask"),
            dlg.Option(tr("Добавить «запретить»", "Add a deny rule"), "deny", tr("Например Read(**/.env), Bash(git push:*)",
                                                                                  "e.g. Read(**/.env), Bash(git push:*)")),
            dlg.Option(tr("Удалить правило", "Remove a rule"), "remove"),
            dlg.Option(tr("Готово", "Done"), None),
        ], body=body)
        if not choice:
            return
        if choice == "pick":
            await pick_permission_mode(ctx)
            continue
        if choice == "remove":
            if not rows:
                continue
            target = await ctx.select(tr("Какое правило удалить?", "Remove which rule?"),
                                      [dlg.Option(f"{labels[kind]}: {rule}", (kind, rule), rule.source) for kind, rule in rows])
            if target:
                kind, rule = target
                if rule.source == "session":
                    runtime.permissions.session_allow = [r for r in runtime.permissions.session_allow if r is not rule]
                else:
                    ctx.settings.remove_item(f"permissions.{kind}", rule.raw, rule.source)
            continue
        text = await ctx.ask(tr("Правило", "Rule"), body=ctx.r.render(Text(
            "  Bash(npm run test:*) · Edit(src/**) · Read(./secrets/**) · WebFetch(domain:github.com) · mcp__github", style="dim")))
        if not text or parse_rule(text) is None:
            continue
        scope = await ctx.select(tr("Где сохранить?", "Save where?"), [
            dlg.Option(tr("Только для меня в этом проекте", "Just me, this project"), "local", ".memucode/settings.local.json"),
            dlg.Option(tr("Для всех в проекте (в git)", "Everyone in this project (git)"), "project", ".memucode/settings.json"),
            dlg.Option(tr("Для всех моих проектов", "All my projects"), "user", str(paths.user_settings_file()))])
        if scope:
            ctx.settings.append(f"permissions.{choice}", text.strip(), scope)
            ctx.say(tr(f"Добавлено: {text.strip()}", f"Added: {text.strip()}"), "success")

CREATE_FLAG = re.compile(r"(?:^|\s)(?:--new|—new|–new|--create|--mkdir|--make|-n)(?=\s|$)", re.I)
_WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{n}" for n in range(1, 10)), *(f"lpt{n}" for n in range(1, 10))}

def split_create_flag(args: str) -> tuple:
    """``("path", create)``: the argument without a ``--new`` flag (a typed «—new» counts too) and whether it was there."""
    text = args.strip()
    create = bool(CREATE_FLAG.search(text))
    return CREATE_FLAG.sub(" ", text).strip().strip('"').strip("'").strip(), create

def folder_problem(path: Path) -> str:
    """Why a folder cannot be created at this path (empty when it can), without touching the disk."""
    parts = list(path.parts)
    if os.name == "nt":
        if path.drive and not Path(path.drive + "\\").exists():
            return tr(f"Диска {path.drive} нет", f"There is no drive {path.drive}")
        for part in parts[1:]:
            stem = part.split(".", 1)[0].lower()
            if re.search(r'[<>:"|?*]', part):
                return tr(f"В имени «{part}» есть символы, недопустимые в Windows: < > : \" | ? *",
                          f"“{part}” has characters Windows does not allow: < > : \" | ? *")
            if part != part.rstrip(" .") or stem in _WINDOWS_RESERVED:
                return tr(f"Имя «{part}» недопустимо в Windows", f"“{part}” is not a valid Windows name")
    if len(str(path)) > 250 and os.name == "nt":
        return tr("Путь слишком длинный для Windows", "The path is too long for Windows")
    return ""

async def offer_to_create(ctx: Context, path: Path, create: bool, *, what: str = "folder") -> bool:
    """A folder that does not exist: create it when asked to (``--new``) or when the user says yes (the
    default answer is no). True when the folder exists afterwards."""
    problem = folder_problem(path)
    if problem:
        ctx.say(problem, "error")
        return False
    if not create:
        if not getattr(ctx.ui, "interactive", False):
            ctx.say(tr(f"Папки нет: {path}. Добавьте --new, чтобы создать её.", f"No such folder: {path}. Add --new to create it."),
                    "error")
            return False
        missing = [parent for parent in (path, *path.parents) if not parent.exists()]
        extra = tr(f" Вместе с ней появятся и недостающие папки выше ({len(missing) - 1}).",
                   f" The {len(missing) - 1} missing folders above it are created too.") if len(missing) > 1 else ""
        answer = await ctx.select(tr("Папки нет. Создать?", "That folder does not exist. Create it?"), [
            dlg.Option(tr("Нет", "No"), False, tr("ничего не менять", "change nothing")),
            dlg.Option(tr("Да, создать", "Yes, create it"), True, str(path))],
            body=ctx.r.render(Text("  " + str(path) + extra, style="dim")), index=0)
        if answer is not True:
            ctx.say(tr("Папка не создана", "The folder was not created"))
            return False
    try:
        path.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        ctx.say(tr(f"Нет прав на создание: {path}", f"No permission to create {path}"), "error")
        return False
    except OSError as exc:
        ctx.say(tr(f"Не удалось создать папку: {exc}", f"Could not create the folder: {exc}"), "error")
        return False
    ctx.say(tr(f"Создана папка: {path}", f"Created folder: {path}"), "success")
    return True

def resolve_folder(root: Path, target: str) -> Path:
    path = Path(os.path.expanduser(os.path.expandvars(target)))
    if not path.is_absolute():
        path = root / path
    try:
        return path.resolve()
    except OSError:
        return path

@command("add-dir", "Дать агенту доступ к ещё одной папке", "Give the agent access to another folder",
         hint="<path> [--new]", category="config", while_busy=True)
async def add_dir(ctx: Context) -> None:
    target, create = split_create_flag(ctx.args)
    target = target or (await ctx.ask(tr("Путь к папке", "Folder path")) or "")
    if not target:
        return
    resolved = resolve_folder(ctx.runtime.root, target)
    if resolved.exists() and not resolved.is_dir():
        ctx.say(tr(f"Это файл, а не папка: {resolved}", f"That is a file, not a folder: {resolved}"), "error")
        return
    if not resolved.exists() and not await offer_to_create(ctx, resolved, create):
        return
    try:
        path = ctx.runtime.add_dir(resolved)
    except ValueError as exc:
        ctx.say(str(exc), "error")
        return
    remember = await ctx.select(tr("Запомнить папку для этого проекта?", "Remember it for this project?"),
                                [dlg.Option(tr("Только на эту сессию", "This session only"), "session"),
                                 dlg.Option(tr("Запомнить (settings.local.json)", "Remember (settings.local.json)"), "local")])
    if remember == "local":
        ctx.settings.append("permissions.additional_directories", str(path), "local")
    ctx.say(tr(f"Рабочая папка добавлена: {path}", f"Added working directory: {path}"), "success")

@command("cd", "Сменить папку проекта. Нет такой папки — предложит создать (или /cd путь --new)",
         "Change the project folder. A missing folder is offered for creation (or /cd path --new)",
         hint="<path> [--new]", category="config", while_busy=False)
async def cd(ctx: Context) -> None:
    target, create = split_create_flag(ctx.args)
    if not target:
        ctx.say(str(ctx.runtime.root))
        return
    runtime = ctx.runtime
    resolved = resolve_folder(runtime.root, target)
    if resolved.exists() and not resolved.is_dir():
        ctx.say(tr(f"Это файл, а не папка: {resolved}", f"That is a file, not a folder: {resolved}"), "error")
        return
    if not resolved.exists() and not await offer_to_create(ctx, resolved, create):
        return
    path = await switch_folder(ctx, resolved)
    if path is not None:
        ctx.say(tr(f"Проект: {path} (новый разговор)", f"Project: {path} (new conversation)"), "success")

async def switch_folder(ctx: Context, resolved: Path) -> Optional[Path]:
    """Make ``resolved`` the project: a new conversation there, its prompt history, and — for a folder not
    trusted yet — the question whether its settings, hooks and commands may run. None when it cannot be entered."""
    runtime = ctx.runtime
    try:
        path = runtime.change_root(resolved)
    except ValueError as exc:
        ctx.say(str(exc), "error")
        return None
    from ..tui.input import PromptHistory
    ctx.ui.history = PromptHistory(paths.project_key(path))
    ctx.ui.context_cache = (-1, 0.0, 0)
    if not runtime.settings.is_trusted(path):
        if await ctx.confirm(tr(f"Доверять файлам в {path}?", f"Trust the files in {path}?"),
                             tr("Проектные настройки, хуки, команды и MCP-серверы запускаются только в доверенных папках.",
                                "Project settings, hooks, commands and MCP servers run only in trusted folders.")):
            runtime.settings.trust(path)
            runtime.invalidate_prompt(memory=True)
    return path

@command("terminal-setup", "Настроить Shift+Enter для новой строки в вашем терминале", "Set up Shift+Enter for new lines in your terminal",
         category="config", while_busy=True)
async def terminal_setup(ctx: Context) -> None:
    term = os.environ.get("TERM_PROGRAM", "")
    lines = [tr("**Новая строка в поле ввода**: Shift+Enter, Alt+Enter, Ctrl+J или `\\` в конце строки + Enter.",
                "**New line in the input**: Shift+Enter, Alt+Enter, Ctrl+J, or `\\` at the end of the line + Enter.")]
    if os.name == "nt" and not term:
        lines.append(tr("В Windows Terminal и обычной консоли Windows Shift+Enter работает сразу — MemuCode читает клавиши "
                        "с модификаторами напрямую.",
                        "In Windows Terminal and the Windows console Shift+Enter works out of the box — MemuCode reads "
                        "keys with their modifiers directly."))
    ctx.md("\n\n".join(lines))
    if term == "vscode" or shutil.which("code"):
        if await ctx.confirm(tr("Добавить в VS Code сочетание Shift+Enter для встроенного терминала?",
                                "Add a Shift+Enter binding for the VS Code terminal?")):
            ok, where = _vscode_keybinding()
            ctx.say(tr(f"Готово: {where}" if ok else f"Не удалось: {where}", f"Done: {where}" if ok else f"Failed: {where}"),
                    "success" if ok else "error")

def _vscode_keybinding() -> tuple:
    base = Path(os.environ.get("APPDATA", Path.home() / ".config")) / "Code" / "User"
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Code" / "User"
    path = base / "keybindings.json"
    entry = {"key": "shift+enter", "command": "workbench.action.terminal.sendSequence",
             "args": {"text": "\u001b\r"}, "when": "terminalFocus"}
    try:
        text = path.read_text(encoding="utf-8") if path.is_file() else "[]"
        cleaned = re.sub(r"//.*", "", text)
        data = json.loads(cleaned or "[]")
        if any(isinstance(row, dict) and row.get("key") == "shift+enter" and row.get("when") == "terminalFocus"
               for row in data):
            return True, str(path) + tr(" (уже было)", " (already there)")
        data.append(entry)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return True, str(path)
    except (OSError, ValueError) as exc:
        return False, str(exc)

@command("keybindings", "Все горячие клавиши", "All keyboard shortcuts", aliases=("shortcuts", "keys"),
         category="config", while_busy=True)
async def keybindings(ctx: Context) -> None:
    ctx.ui.show_help = True
    ctx.ui.invalidate()

PRESETS = {
    "anthropic": ("Anthropic (Claude API)", "anthropic", "https://api.anthropic.com", "ANTHROPIC_API_KEY",
                  "https://console.anthropic.com/settings/keys"),
    "openai": ("OpenAI", "openai", "https://api.openai.com/v1", "OPENAI_API_KEY", "https://platform.openai.com/api-keys"),
    "openrouter": ("OpenRouter", "openai", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY",
                   "https://openrouter.ai/settings/keys"),
    "custom": (("Другой OpenAI-совместимый API", "Another OpenAI-compatible API"), "openai", "", "", ""),
}

def _preset_label(value: Any) -> str:
    return tr(*value) if isinstance(value, tuple) else str(value)

async def add_connection(ctx: Context) -> None:
    """Connect models directly with an API key (Anthropic, OpenAI, OpenRouter, any OpenAI-compatible API)."""
    runtime = ctx.runtime
    health = await runtime.router.proxy.health()
    intro = tr("MemuCode уже видит модели ваших аккаунтов через MemuAI Proxy" if health else
               "MemuAI Proxy сейчас не отвечает", "MemuCode already sees your accounts' models through MemuAI Proxy"
               if health else "MemuAI Proxy is not responding")
    kind = await ctx.select(tr("Прямое подключение к API", "Direct API connection"),
                            [dlg.Option(_preset_label(label), key, url or "") for key, (label, _f, _b, _e, url) in PRESETS.items()],
                            body=ctx.r.render(Text("  " + intro + ".", style="dim")))
    if not kind:
        return
    label, fmt, base, env_name, url = PRESETS[kind]
    label = _preset_label(label)
    name = kind if kind != "custom" else (await ctx.ask(tr("Короткое имя подключения (латиница)", "Short connection name")) or "")
    name = re.sub(r"[^\w-]+", "-", name.strip().lower()).strip("-")
    if not name:
        return
    if kind == "custom":
        base = await ctx.ask(tr("Base URL (например https://api.example.com/v1)", "Base URL (e.g. https://api.example.com/v1)")) or ""
        if not base.startswith(("http://", "https://")):
            ctx.say(tr("Нужен адрес http(s)", "An http(s) URL is required"), "error")
            return
    hint = tr(f"Ключ можно взять здесь: {url}", f"Get a key here: {url}") if url else ""
    key = await ctx.ask(tr(f"API-ключ {label}", f"{label} API key"), body=ctx.r.render(Text("  " + hint, style="dim")) if hint else "")
    if not key:
        return
    models_text = await ctx.ask(tr("Модели через запятую (пусто — взять список у сервиса)", "Models, comma separated (empty — ask the service)")) or ""
    connection = {"name": name, "format": fmt, "base_url": base.rstrip("/"), "api_key": key.strip(),
                  "models": [item.strip() for item in models_text.split(",") if item.strip()]}
    rows = [row for row in (ctx.settings.get("connections") or []) if isinstance(row, dict) and row.get("name") != name]
    rows.append(connection)
    ctx.settings.set("connections", rows)
    runtime.router.reset()
    ctx.ui.status_text = tr("проверяю подключение", "checking the connection")
    try:
        models = await runtime.router.direct(name).list_models()
    except Exception as exc:
        models = []
        ctx.say(tr(f"Проверка не удалась: {exc}", f"The check failed: {exc}"), "warn")
    finally:
        ctx.ui.status_text = ""
    if models:
        ctx.say(tr(f"Подключено «{name}»: моделей {len(models)}. Выберите модель: /model @{name}:",
                   f"Connected “{name}”: {len(models)} models. Pick one with /model @{name}:"), "success")
    else:
        ctx.say(tr(f"Подключение «{name}» сохранено. Модели будут вида @{name}:<модель>.",
                   f"Saved “{name}”. Its models are named @{name}:<model>."), "success")

async def remove_connection(ctx: Context) -> None:
    """Remove a direct API connection saved with :func:`add_connection`."""
    rows = [row for row in (ctx.settings.get("connections") or []) if isinstance(row, dict)]
    if not rows:
        ctx.say(tr("Прямых подключений нет (ключи из переменных окружения удаляются там же).",
                   "No saved connections (keys from environment variables are removed there)."), "warn")
        return
    name = await ctx.select(tr("Какое подключение удалить?", "Remove which connection?"),
                            [dlg.Option(str(row["name"]), row["name"], f"{row.get('format')} · {row.get('base_url')} · "
                                        f"{mask('api_key', row.get('api_key', ''))}") for row in rows])
    if not name:
        return
    ctx.settings.set("connections", [row for row in rows if row.get("name") != name])
    ctx.runtime.router.reset()
    ctx.say(tr(f"Удалено: {name}", f"Removed: {name}"), "success")

@command("proxy", "MemuAI Proxy: состояние и запуск; здесь же прямые API-подключения (ключи Anthropic, OpenAI, OpenRouter…)",
         "MemuAI Proxy: status and start; direct API connections (Anthropic, OpenAI, OpenRouter keys…) are managed here too",
         hint="[status|add|remove]", category="config", while_busy=True)
async def proxy(ctx: Context) -> None:
    word = ctx.args.strip().lower()
    if word in ("add", "connect", "добавить"):
        await add_connection(ctx)
        return
    if word in ("remove", "rm", "delete", "удалить"):
        await remove_connection(ctx)
        return
    backend = ctx.runtime.router.proxy
    data = await backend.health()
    if not data:
        if await ctx.confirm(tr("MemuAI Proxy не отвечает. Запустить?", "MemuAI Proxy is not responding. Start it?")):
            ctx.ui.status_text = tr("запускаю Proxy", "starting the Proxy")
            data = await backend.ensure(launch=True)
            ctx.ui.status_text = ""
        if not data:
            ctx.say(tr("Proxy не запустился. Проверьте установку MemuAI или подключите API напрямую: /proxy add",
                       "The Proxy did not start. Check the MemuAI install or connect an API directly: /proxy add"), "error")
            if word == "status":
                return
    if data:
        accounts = data.get("accounts_usable") or {}
        table = Table(box=None, pad_edge=False, show_header=False)
        table.add_row(tr("Адрес", "Address"), backend.base_url())
        table.add_row(tr("Версия", "Version"), str(data.get("version", "")))
        table.add_row(tr("Прокси включён", "Proxy enabled"), _value_text(bool(data.get("proxy_enabled"))))
        table.add_row(tr("Аккаунты", "Accounts"), ", ".join(f"{name}: {count}" for name, count in accounts.items() if count) or "—")
        models = await ctx.runtime.router.models(refresh=True)
        table.add_row(tr("Моделей", "Models"), str(len(models)))
        ctx.print(table)
    direct = [row for row in ctx.settings.connections() if isinstance(row, dict)]
    if direct:
        ctx.print(Text("  " + tr("Прямые API-подключения: ", "Direct API connections: ")
                       + ", ".join(str(row["name"]) + (" (env)" if row.get("from_env") else "") for row in direct), style="dim"))
    if word == "status" or not getattr(ctx.ui, "interactive", False):
        return
    choice = await ctx.select(tr("Подключения", "Connections"), [
        dlg.Option(tr("Добавить прямое API-подключение…", "Add a direct API connection…"), "add",
                   tr("свой ключ Anthropic, OpenAI, OpenRouter или другого сервиса", "your own Anthropic, OpenAI, OpenRouter… key")),
        dlg.Option(tr("Удалить прямое подключение…", "Remove a direct connection…"), "remove"),
        dlg.Option(tr("Готово", "Done"), None)])
    if choice == "add":
        await add_connection(ctx)
    elif choice == "remove":
        await remove_connection(ctx)

@command("notifications", "Уведомления о завершении и вопросах", "Notifications when work finishes or needs you",
         category="config", while_busy=True)
async def notifications(ctx: Context) -> None:
    if not await edit_setting(ctx, "notifications"):
        return
    ctx.say(tr(f"Уведомления: {ctx.settings.get('notifications')}", f"Notifications: {ctx.settings.get('notifications')}"), "success")

@command("sandbox", "Песочница для команд: состояние и настройки", "Command sandbox: status and settings", category="config", while_busy=True)
async def sandbox(ctx: Context) -> None:
    from ... import code_sandbox
    status = code_sandbox.manager.status(ctx.runtime.session.id)
    enabled = bool(ctx.settings.get("sandbox.enabled", True))
    body = ctx.r.render(Text("  " + tr(
        f"Команды агента работают в Windows Job Object: лимит памяти 2 ГБ, всё дерево процессов завершается по Esc. "
        f"Секреты (key/token/secret…) убраны из окружения. Активных процессов: {status.get('active_processes', 0)}.",
        f"Agent commands run in a Windows Job Object: 2 GB memory limit, the whole process tree stops on Esc. "
        f"Secrets (key/token/secret…) are removed from the environment. Active processes: {status.get('active_processes', 0)}."),
        style="dim"))
    choice = await ctx.select(tr("Песочница", "Sandbox"), [
        dlg.Option(tr("Песочница: ", "Sandbox: ") + _value_text(enabled), "toggle"),
        dlg.Option(tr("Окружение команд: ", "Command environment: ") + str(ctx.settings.get("sandbox.env")), "env",
                   tr("filtered — без секретов; strict — минимальный набор; full — всё", "filtered — no secrets; strict — minimal; full — everything")),
        dlg.Option(tr("Остановить все процессы сессии", "Stop every process of the session"), "stop"),
    ], body=body)
    if choice == "toggle":
        ctx.settings.set("sandbox.enabled", not enabled)
    elif choice == "env":
        await edit_setting(ctx, "sandbox.env")
    elif choice == "stop":
        code_sandbox.manager.close(ctx.runtime.session.id)
        ctx.runtime.shells.kill_all()
        ctx.say(tr("Процессы остановлены", "Processes stopped"), "success")

@command("hooks", "Хуки: команды на событиях агента", "Hooks: commands on agent events", category="config", while_busy=True)
async def hooks(ctx: Context) -> None:
    from ..hooks import EVENTS
    runner = ctx.runtime.hooks
    table = Table(box=None, pad_edge=False, show_header=True, header_style="bold")
    table.add_column(tr("Событие", "Event"))
    table.add_column("matcher")
    table.add_column(tr("Команда", "Command"))
    table.add_column(tr("Где", "Where"), style="dim")
    count = 0
    for event in EVENTS:
        for block in runner.entries(event):
            for hook in block.get("hooks") or []:
                table.add_row(event, str(block.get("matcher") or "*"), str(hook.get("command") or "")[:70], block.get("_source", ""))
                count += 1
    if count:
        ctx.print(table)
    ctx.md(tr(
        "Хуки задаются в `settings.json` (`/config edit`) в формате Claude Code, например:\n\n"
        "```json\n\"hooks\": {\"PostToolUse\": [{\"matcher\": \"Edit|Write\", \"hooks\": [{\"type\": \"command\", "
        "\"command\": \"npx prettier --write \\\"$MEMUCODE_FILE\\\"\"}]}]}\n```\n"
        "События: " + ", ".join(EVENTS) + ". Код выхода 2 блокирует действие.",
        "Hooks live in `settings.json` (`/config edit`) in the Claude Code format, e.g.:\n\n"
        "```json\n\"hooks\": {\"PostToolUse\": [{\"matcher\": \"Edit|Write\", \"hooks\": [{\"type\": \"command\", "
        "\"command\": \"npx prettier --write \\\"$MEMUCODE_FILE\\\"\"}]}]}\n```\n"
        "Events: " + ", ".join(EVENTS) + ". Exit code 2 blocks the action."))
    for error in runner.last_errors[-5:]:
        ctx.say(error, "warn")

@command("install", "Добавить команду memucode в PATH", "Add the memucode command to PATH", category="config", while_busy=True)
async def install(ctx: Context) -> None:
    from ..installer import install_command
    ok, message = install_command()
    ctx.say(message, "success" if ok else "error")
