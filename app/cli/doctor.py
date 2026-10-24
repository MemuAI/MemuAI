"""``memucode doctor`` / ``/doctor``: everything that can be checked locally,
with a concrete hint for each problem."""
from __future__ import annotations

import asyncio
import importlib.util
import os
import platform
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

from .. import pricing
from . import VERSION, paths
from .i18n import tr


COMPONENTS = (
    "app.cli.tui.app", "app.cli.tui.input", "app.cli.tui.transcript", "app.cli.tui.activity",
    "app.cli.lang", "app.cli.splash", "app.cli.clipboard",
    "app.cli.commands.work_cmds", "app.cli.commands.schedule_cmds", "app.cli.commands.import_cmds",
    "app.cli.commands.decisions_cmds", "app.cli.foreign", "app.cli.schedule", "app.cli.triage", "app.cli.risk",
    "app.cli.tools.automation", "app.cli.vision",
    "app.cli.tools.mcp", "app.cli.voice", "app.decisions", "app.websearch", "app.instant_answers",
    "app.code_checkpoints",
    "prompt_toolkit.output.vt100", "rich.markdown", "rich.syntax", "anthropic", "httpx",
) + (("prompt_toolkit.input.win32", "prompt_toolkit.output.win32", "prompt_toolkit.output.windows10")
     if os.name == "nt" else ("prompt_toolkit.input.vt100",))


def _row(name: str, status: str, detail: str, hint: str = "") -> Dict[str, str]:
    return {"name": name, "status": status, "detail": detail, "hint": hint}


async def run_checks(runtime: Any) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    frozen = bool(getattr(sys, "frozen", False))
    rows.append(_row("MemuCode", "ok", f"{VERSION} · {'exe' if frozen else 'Python ' + platform.python_version()} · "
                                       f"{platform.system()} {platform.release()}"))

    # terminal
    size = shutil.get_terminal_size((0, 0))
    term = os.environ.get("TERM_PROGRAM") or ("Windows Terminal" if os.environ.get("WT_SESSION") else
                                               ("conhost" if os.name == "nt" else os.environ.get("TERM", "?")))
    if not size.columns:
        # `memucode doctor > report.txt`: there is no window to measure.
        rows.append(_row(tr("Терминал", "Terminal"), "info", f"{term} · " + tr("вывод перенаправлен", "output redirected")))
    else:
        status = "ok" if size.columns >= 60 else "warn"
        rows.append(_row(tr("Терминал", "Terminal"), status, f"{term} · {size.columns}×{size.lines}",
                         "" if status == "ok" else tr("Окно узкое — расширьте его хотя бы до 80 символов",
                                                      "The window is narrow — make it at least 80 columns")))
    if os.name == "nt":
        shift = tr("Shift+Enter читается напрямую из консоли Windows", "Shift+Enter is read directly from the Windows console")
    else:
        shift = tr("Shift+Enter зависит от терминала; всегда работают Alt+Enter, Ctrl+J и \\+Enter",
                   "Shift+Enter depends on the terminal; Alt+Enter, Ctrl+J and \\+Enter always work")
    rows.append(_row(tr("Новая строка", "New line"), "info", shift, tr("Настройка: /terminal-setup", "Setup: /terminal-setup")))

    # proxy
    proxy = runtime.router.proxy
    health = await proxy.health()
    if health:
        usable = health.get("accounts_usable") or {}
        accounts = sum(int(value or 0) for value in usable.values())
        status = "ok" if accounts else "warn"
        rows.append(_row("MemuAI Proxy", status, f"{proxy.base_url()} · v{health.get('version')} · "
                                                   + tr(f"рабочих аккаунтов: {accounts}", f"{accounts} usable accounts"),
                         "" if accounts else tr("Добавьте аккаунт в окне MemuAI Proxy → Аккаунты",
                                                "Add an account in MemuAI Proxy → Accounts")))
        if not health.get("proxy_enabled", True):
            rows.append(_row(tr("Прокси", "Proxy"), "error", tr("выключен в панели", "switched off in the panel"),
                             tr("Главная → «Запустить прокси»", "Home → “Start proxy”")))
    else:
        status = "warn" if runtime.settings.connections() else "error"
        rows.append(_row("MemuAI Proxy", status, tr("не отвечает", "not responding"),
                         tr("Запустите MemuAI Proxy (меню «Пуск») или /proxy; либо подключите API: /proxy add",
                            "Start MemuAI Proxy (Start menu) or /proxy; or connect an API: /proxy add")))
    try:
        models = await asyncio.wait_for(runtime.router.models(refresh=True), 25)
    except Exception:
        models = []
    rows.append(_row(tr("Модели", "Models"), "ok" if models else "error",
                     tr(f"доступно: {len(models)}; выбрана {runtime.model or '—'}", f"{len(models)} available; using {runtime.model or '—'}"),
                     "" if models else tr("Нет ни одной модели", "No models at all")))
    if runtime.model and models and runtime.model not in {model.id for model in models} and not runtime.model.startswith("@"):
        rows.append(_row(tr("Выбранная модель", "Selected model"), "warn", runtime.model,
                         tr("Её нет в списке — выберите другую: /model", "It is not in the list — pick another: /model")))
    for name, error in runtime.router.errors.items():
        if name != "proxy":
            rows.append(_row(f"API {name}", "error", error.splitlines()[0][:160], tr("Проверьте ключ: /proxy add", "Check the key: /proxy add")))

    # tools
    from .tools.shell import shell_label
    rows.append(_row(tr("Оболочка", "Shell"), "ok", shell_label(runtime.settings),
                     tr("Git Bash не найден — команды идут через PowerShell" if os.name == "nt" and not shell_label(runtime.settings).startswith("bash") else "",
                        "Git Bash not found — commands use PowerShell" if os.name == "nt" and not shell_label(runtime.settings).startswith("bash") else "")))
    git = shutil.which("git")
    rows.append(_row("git", "ok" if git else "warn", git or tr("не найден", "not found"),
                     "" if git else tr("Установите Git: https://git-scm.com", "Install Git: https://git-scm.com")))
    rg = shutil.which("rg")
    rows.append(_row("ripgrep", "ok" if rg else "info", rg or tr("не установлен — используется встроенный поиск",
                                                                 "not installed — the built-in search is used")))
    try:
        from ..automation_runtime import _edge_executable
        edge = _edge_executable()
        rows.append(_row("Browser (Edge)", "ok", edge))
    except Exception:
        rows.append(_row("Browser (Edge)", "warn", tr("Microsoft Edge не найден", "Microsoft Edge not found"),
                         tr("Инструмент Browser недоступен", "The Browser tool is unavailable")))
    for module, label, hint in (("PIL", tr("Картинки (Pillow)", "Images (Pillow)"), "pip install pillow"),
                                ("curl_cffi", "WebFetch", "pip install curl_cffi"),
                                ("anthropic", "Anthropic SDK", "pip install anthropic"),
                                ("pypdf", "PDF", "pip install pypdf")):
        found = importlib.util.find_spec(module) is not None
        rows.append(_row(label, "ok" if found else "warn", tr("есть", "available") if found else tr("нет", "missing"),
                         "" if found else hint))

    # components: a broken install shows up here instead of as a crash later
    missing = []
    for module in COMPONENTS:
        try:
            importlib.import_module(module)
        except Exception as exc:  # ImportError, or a DLL that fails to load
            missing.append(f"{module} ({type(exc).__name__})")
    try:
        from pygments.lexers import get_lexer_by_name
        for language in ("python", "typescript", "rust", "powershell", "diff"):
            get_lexer_by_name(language)
    except Exception as exc:
        missing.append(f"pygments ({type(exc).__name__})")
    rows.append(_row(tr("Компоненты", "Components"), "error" if missing else "ok",
                     ", ".join(missing[:6]) if missing else tr(f"все {len(COMPONENTS)} на месте", f"all {len(COMPONENTS)} present"),
                     tr("Переустановите MemuAI", "Reinstall MemuAI") if missing else ""))

    # sandbox
    try:
        from .. import code_sandbox
        job = code_sandbox.SessionJob("doctor-probe", Path(runtime.root))
        job.close()
        rows.append(_row(tr("Песочница", "Sandbox"), "ok", tr("Windows Job Object создаётся" if os.name == "nt" else "реестр процессов",
                                                              "Windows Job Object works" if os.name == "nt" else "process registry")))
    except Exception as exc:
        rows.append(_row(tr("Песочница", "Sandbox"), "error", str(exc)[:160],
                         tr("Команды не смогут запускаться; можно отключить: /sandbox", "Commands cannot run; you may disable it: /sandbox")))

    # mcp
    for row in runtime.mcp.catalogue():
        if row["enabled"]:
            status = "ok" if row["running"] else ("error" if row["error"] else "warn")
            rows.append(_row(f"MCP {row['name']}", status, (f"{len(row['tools'])} " + tr("инстр.", "tools")) if row["running"]
                             else (row["error"] or tr("не запущен", "not running"))))

    # settings & storage
    for warning in runtime.settings.warnings:
        rows.append(_row(tr("Настройки", "Settings"), "warn", warning))
    try:
        probe = paths.home() / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        rows.append(_row(tr("Папка данных", "Data folder"), "ok", str(paths.home())))
    except OSError as exc:
        rows.append(_row(tr("Папка данных", "Data folder"), "error", f"{paths.home()}: {exc}"))
    memory = runtime.memory_files()
    rows.append(_row(tr("Память", "Memory"), "ok" if memory else "info",
                     ", ".join(row.path.name for row in memory) or tr("файлов нет", "no files"),
                     "" if memory else tr("/init создаст MEMUCODE.md с описанием проекта", "/init creates MEMUCODE.md describing the project")))
    from .installer import is_installed
    installed = is_installed()
    rows.append(_row(tr("Команда memucode", "memucode command"), "ok" if installed else "warn",
                     tr("в PATH" if installed else "не в PATH", "on PATH" if installed else "not on PATH"),
                     "" if installed else tr("Выполните /install", "Run /install")))
    age = pricing.cache_age()
    rows.append(_row(tr("Цены моделей", "Model prices"), "ok" if age is not None and age < 7 * 86400 else "info",
                     tr(f"каталог обновлён {int(age // 3600)} ч назад", f"catalogue updated {int(age // 3600)}h ago") if age is not None
                     else tr("каталога нет — встроенная таблица", "no catalogue — built-in table"),
                     "" if age is not None else "/cost refresh"))

    # decision model
    decisions = runtime.decisions
    if decisions.provider not in ("off", ""):
        if not decisions.enabled:
            rows.append(_row(tr("Модель решений", "Decision model"), "warn", decisions.title,
                             tr("Не хватает ключа или адреса: /decisions", "A key or address is missing: /decisions")))
        else:
            try:
                started = time.monotonic()
                result = await asyncio.wait_for(decisions.test(), 20)
                rows.append(_row(tr("Модель решений", "Decision model"), "ok",
                                 f"{decisions.title} · {result['model']} · {int((time.monotonic() - started) * 1000)} ms"))
            except Exception as exc:
                rows.append(_row(tr("Модель решений", "Decision model"), "error", str(exc)[:200], "/decisions"))
    if os.name == "nt":
        from .voice import mci_available
        rows.append(_row(tr("Микрофон (/voice)", "Microphone (/voice)"), "ok" if mci_available() else "warn",
                         tr("запись через Windows MCI", "recording via Windows MCI") if mci_available() else tr("недоступен", "unavailable")))
    return rows
