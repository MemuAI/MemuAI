"""Command line of MemuCode.

    memucode                         interactive session in the current folder
    memucode "fix the failing test"  start with a prompt
    memucode -c / --continue         continue the latest conversation here
    memucode -r [id]                 resume a conversation
    memucode -p "task"               answer once and exit (scripts, CI, pipes)
    memucode doctor | models | sessions | mcp … | config … | install
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, List, Optional

from . import NAME, VERSION

SUBCOMMANDS = ("doctor", "mcp", "config", "sessions", "models", "schedule", "install", "uninstall", "update", "version")

class SessionNotFound(Exception):
    pass

def _prepare_console() -> None:
    if os.name == "nt":
        try:
            import ctypes
            kernel = ctypes.windll.kernel32
            kernel.SetConsoleOutputCP(65001)
            kernel.SetConsoleCP(65001)
            handle = kernel.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if kernel.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memucode", description="MemuCode — the coding agent of MemuAI in your terminal.",
                                     formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument("prompt", nargs="*", help="initial prompt")
    parser.add_argument("-p", "--print", dest="print_mode", action="store_true", help="answer once and exit (non-interactive)")
    parser.add_argument("-c", "--continue", dest="continue_", action="store_true", help="continue the latest conversation")
    parser.add_argument("-r", "--resume", nargs="?", const="__pick__", default=None, metavar="ID", help="resume a conversation")
    parser.add_argument("-m", "--model", default="", help="model id (see `memucode models`)")
    parser.add_argument("--permission-mode", choices=["default", "acceptEdits", "plan", "auto", "bypassPermissions"])
    parser.add_argument("--dangerously-skip-permissions", action="store_true", help="bypass every permission check")
    parser.add_argument("--allowedTools", "--allowed-tools", dest="allowed", nargs="*", default=[], help="rules to allow, e.g. \"Bash(npm test:*)\" Edit")
    parser.add_argument("--disallowedTools", "--disallowed-tools", dest="disallowed", nargs="*", default=[], help="rules to deny")
    parser.add_argument("--add-dir", action="append", default=[], help="extra working directory (repeatable)")
    parser.add_argument("--cwd", default="", help="project folder (default: the current folder)")
    parser.add_argument("--output-format", choices=["text", "json", "stream-json"], default="text")
    parser.add_argument("--max-turns", type=int, default=0, help="tool rounds per request")
    parser.add_argument("--system-prompt", default="", help="replace the built-in instructions")
    parser.add_argument("--append-system-prompt", default="", help="add instructions to the system prompt")
    parser.add_argument("--effort", choices=["auto", "minimal", "low", "medium", "high", "max"])
    parser.add_argument("--lang", choices=["en"], help="interface language (English)")
    parser.add_argument("--theme", default="", help="dark, light, dark-daltonized, light-daltonized, dark-ansi, light-ansi")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("-v", "--version", action="store_true", help="print the version")
    return parser

def _overrides(args: argparse.Namespace) -> dict:
    values: dict = {"language": "en"}
    if args.model:
        values["model"] = args.model
    if args.dangerously_skip_permissions:
        values["permission_mode"] = "bypassPermissions"
    elif args.permission_mode:
        values["permission_mode"] = args.permission_mode
    if args.max_turns:
        values["max_turns"] = args.max_turns
    if args.effort:
        values["effort"] = args.effort
    if args.theme:
        values["theme"] = args.theme
    if args.verbose:
        values["verbose"] = True
    return values

def _stdin_text() -> str:
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return ""
        buffer = getattr(sys.stdin, "buffer", None)
        if buffer is None:
            return sys.stdin.read()
        return decode_piped(buffer.read())
    except Exception:
        return ""

def decode_piped(raw: bytes) -> str:
    """Bytes piped into ``memucode -p`` as text. ``sys.stdin.read()`` used the ANSI code page on Windows, so the
    UTF-8 of ``git diff | memucode -p …`` arrived as mojibake. BOMs (PowerShell writes UTF-16) are honoured, UTF-8
    is tried next, then the console code pages (cp866 / cp1251) through the sandbox's output decoder."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", "replace")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", "replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        from ..code_sandbox import OutputDecoder
        return OutputDecoder().decode(raw, final=True)

def main(argv: Optional[List[str]] = None) -> int:
    _prepare_console()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "remote-host":
        from .remote_host import main as remote_host_main
        return remote_host_main(argv[1:])
    if argv and argv[0] in SUBCOMMANDS:
        return run_subcommand(argv[0], argv[1:])
    args = build_parser().parse_args(argv)
    if args.version:
        print(f"{NAME} {VERSION}")
        return 0
    root = Path(args.cwd or os.getcwd()).expanduser().resolve()
    if not root.is_dir():
        print(f"memucode: folder not found: {root}", file=sys.stderr)
        return 2
    os.chdir(root)
    from .i18n import set_language
    from .settings import Settings
    settings = Settings(root, _overrides(args))
    set_language(args.lang or str(settings.get("language") or ""))
    prompt = " ".join(args.prompt).strip()
    piped = _stdin_text() if (args.print_mode or not sys.stdin.isatty()) else ""
    if piped.strip():
        prompt = (prompt + "\n\n" if prompt else "") + "<stdin>\n" + piped[-200_000:] + "\n</stdin>"
    if args.print_mode or not sys.stdout.isatty():
        if not prompt:
            print("memucode -p: a prompt is required (argument or stdin)", file=sys.stderr)
            return 2
        try:
            return asyncio.run(_headless(root, settings, args, prompt))
        except SessionNotFound as exc:
            print(f"memucode: no conversation {exc}", file=sys.stderr)
            return 2
    if not sys.stdin.isatty():
        print("memucode: the interactive mode needs a terminal; use -p for pipes", file=sys.stderr)
        return 2
    try:
        return asyncio.run(_interactive(root, settings, args, prompt))
    except KeyboardInterrupt:
        return 130
    except SessionNotFound as exc:
        print(f"memucode: no conversation {exc}", file=sys.stderr)
        return 2

def _session_for(root: Path, args: argparse.Namespace):
    from .session import Session, latest_session
    if args.continue_:
        latest = latest_session(root)
        if latest:
            return Session.load(root, latest), True
    if args.resume and args.resume != "__pick__":
        if not re.fullmatch(r"[A-Za-z0-9-]{1,80}", args.resume):
            raise SessionNotFound(args.resume)
        try:
            return Session.load(root, args.resume), True
        except FileNotFoundError:
            from . import paths
            matches = []
            for folder in paths.sessions_root().iterdir():
                if folder.is_dir():
                    matches.extend(file for file in folder.glob("*.jsonl") if file.stem.startswith(args.resume))
            if len(matches) == 1:
                try:
                    with matches[0].open("r", encoding="utf-8") as stream:
                        metadata = json.loads(stream.readline())
                    if isinstance(metadata, dict) and metadata.get("root"):
                        return Session.load(Path(metadata["root"]), matches[0].stem), True
                except (OSError, ValueError):
                    pass
            raise SessionNotFound(args.resume)
    return Session(root), False

def _apply_rules(runtime: Any, args: argparse.Namespace) -> None:
    from .permissions import parse_rule
    for text in args.allowed or []:
        for piece in [text] if "(" in text else text.replace(",", " ").split():
            rule = parse_rule(piece, "cli")
            if rule:
                runtime.permissions.session_allow.append(rule)
    for text in args.disallowed or []:
        for piece in [text] if "(" in text else text.replace(",", " ").split():
            rule = parse_rule(piece, "cli")
            if rule:
                runtime.permissions.session_deny.append(rule)
    for folder in args.add_dir or []:
        try:
            runtime.add_dir(folder)
        except ValueError as exc:
            print(f"memucode: {exc}", file=sys.stderr)
    runtime.system_override = args.system_prompt
    runtime.system_append = args.append_system_prompt

async def _headless(root: Path, settings: Any, args: argparse.Namespace, prompt: str) -> int:
    from . import headless
    from .runtime import Runtime
    session, _resumed = _session_for(root, args)
    if session.root != root:
        from .settings import Settings
        settings = Settings(session.root, _overrides(args))
        os.chdir(session.root)
    runtime = Runtime(session.root, settings, session=session, model=args.model)
    _apply_rules(runtime, args)
    return await headless.run(runtime, prompt, fmt=args.output_format, verbose=args.verbose)

async def _interactive(root: Path, settings: Any, args: argparse.Namespace, prompt: str) -> int:
    from .i18n import tr
    from .runtime import Runtime
    from .tui.app import TerminalUI
    from .tui.banner import welcome
    session, resumed = _session_for(root, args)
    if session.root != root:
        from .settings import Settings
        settings = Settings(session.root, _overrides(args))
        os.chdir(session.root)
    runtime = Runtime(session.root, settings, session=session, model=args.model)
    _apply_rules(runtime, args)
    ui = TerminalUI(runtime)
    banner = welcome(ui.renderer, runtime)
    ui.banner = ui.add_block(lambda width, expanded: banner.render(width), kind="banner")
    if resumed:
        from .commands import Context
        from .commands.session_cmds import replay
        replay(Context(ui, "resume", "", ""), session.messages, limit=30)
        ui.say(f"Resuming “{session.title()}”", "success")

    questions = []
    if not settings.get("onboarded"):
        questions.append(_onboarding(ui))
    if not settings.is_trusted(runtime.root) and runtime.root != Path.home().resolve():
        questions.append(_trust(ui))

    async def after_start(terminal: TerminalUI) -> None:
        for answered in questions:
            await answered

        terminal.connecting = True
        terminal.start(_startup(terminal), busy=False)

        from .remote import attach
        terminal.remote_control = attach(terminal)
        if args.resume == "__pick__":
            await asyncio.sleep(0.2)
            terminal.run_command_text("/resume")

    code = await ui.run(initial=prompt or None, after_start=after_start)
    from .remote import detach
    await detach(getattr(ui, "remote_control", None))
    await runtime.shutdown()
    runtime.session.ensure_saved()
    _goodbye(ui)
    return code

async def _startup(ui: Any) -> None:
    runtime = ui.runtime
    before = runtime.model
    try:
        await runtime.startup()
    finally:
        ui.connecting = False
        if getattr(ui, "banner", None) is not None:
            ui.banner.update()
        ui.invalidate()
    if runtime.router.proxy.launched:
        ui.say("Started MemuAI Proxy in the tray", "success")
    for name, error in runtime.router.errors.items():
        ui.say(f"{name}: {error.splitlines()[0][:200]}", "warn")
    models = runtime.router.cached_models()
    if not runtime.model:
        ui.say("No models available. Add an account in MemuAI Proxy or connect an API: /proxy add", "warn")
    elif runtime.model != before:
        ui.say(f"Model: {runtime.model} (change with /model)")
    elif models and runtime.model not in {model.id for model in models} and not runtime.model.startswith("@"):
        ui.say(f"Model {runtime.model} is not available now — pick another with /model", "warn")

def _onboarding(ui: Any) -> Any:
    """Present the theme picker now; the returned coroutine waits for the choice and applies it."""
    from .i18n import tr
    from .tui import dialogs as dlg
    settings = ui.runtime.settings
    from .tui.theme import palette_for, pt_style
    original = str(settings.get("theme") or "dark")
    sample = "@@ -1,2 +1,2 @@\n def hello():\n-    print('hi')\n+    print('Hello, MemuCode!')\n"
    dialog = dlg.Dialog(title=tr("Выберите тему · ↑/↓ — предпросмотр", "Choose a theme · ↑/↓ to preview"), options=[
        dlg.Option(tr("Тёмная", "Dark"), "dark"), dlg.Option(tr("Светлая", "Light"), "light"),
        dlg.Option(tr("Тёмная · для дальтоников", "Dark · colour-blind friendly"), "dark-daltonized"),
        dlg.Option(tr("Светлая · для дальтоников", "Light · colour-blind friendly"), "light-daltonized"),
    ])

    def preview(value: str) -> None:
        ui.palette = palette_for(value, settings.get("accent"))
        ui.renderer.set_palette(ui.palette)
        dialog.body = ui.renderer.render(ui.renderer.diff(sample))
        if ui.app is not None:
            ui.app.style = pt_style(ui.palette)
            ui.invalidate()

    dialog.on_change = preview
    dialog.index = next((i for i, option in enumerate(dialog.options) if option.value == original), 0)
    preview(dialog.options[dialog.index].value)
    ui.present_dialog(dialog)

    async def answered() -> None:
        choice = await ui.open_dialog(dialog)
        if choice:
            settings.set("theme", choice)
            from .commands.config_cmds import apply_live
            from .commands import Context
            apply_live(Context(ui, "theme", "", ""), "theme")
        else:
            preview(original)
        settings.set("onboarded", True)

    return answered()

def _trust(ui: Any) -> Any:
    """Present the folder-trust question now; the returned coroutine waits for the answer and applies it."""
    from .i18n import tr
    from .tui import dialogs as dlg
    from .tui.banner import short_path
    runtime = ui.runtime

    shown = short_path(str(runtime.root), 56)
    dialog = dlg.Dialog(title=tr(f"Доверяете файлам в папке {shown}?", f"Do you trust the files in {shown}?"), options=[
        dlg.Option(tr("Да, доверяю", "Yes, I trust this folder"), True,
                   tr("проектные настройки, хуки, команды и MCP-серверы будут работать", "project settings, hooks, commands and MCP servers will work")),
        dlg.Option(tr("Нет, работать без проектных настроек", "No, work without project settings"), False),
    ], body=ui.renderer.render(ui.renderer.md(f"`{runtime.root}`\n\n" + tr(
        "MemuCode может читать, менять и запускать файлы этой папки. Проектные хуки и MCP-серверы исполняют команды "
        "из репозитория — включайте их только для своих или проверенных проектов.",
        "MemuCode may read, change and run files in this folder. Project hooks and MCP servers execute commands from "
        "the repository — enable them only for your own or reviewed projects."))))
    ui.present_dialog(dialog)

    async def answered() -> None:
        if await ui.open_dialog(dialog):
            runtime.settings.trust(runtime.root)
            runtime.invalidate_prompt(memory=True)

    return answered()

def _goodbye(ui: Any) -> None:
    from rich.text import Text
    from .. import pricing
    from .i18n import tr
    runtime = ui.runtime
    parts = []
    parts.append(f"Session UID: {runtime.session.id} · resume: memucode --resume {runtime.session.id}")
    costs = runtime.stats.costs(runtime.settings.get("pricing.overrides") or {})
    if costs["api_equivalent"] > 0:
        total = runtime.stats.total()
        parts.append(tr(f"≈ {pricing.format_usd(costs['api_equivalent'])} по ценам API · {total.input:,}↑ {total.output:,}↓ токенов",
                        f"≈ {pricing.format_usd(costs['api_equivalent'])} at API prices · {total.input:,}↑ {total.output:,}↓ tokens"))
    for line in parts:
        sys.stdout.write(ui.renderer.render(Text(line, style="dim")))
    sys.stdout.flush()

def run_subcommand(name: str, argv: List[str]) -> int:
    from .i18n import set_language
    from .settings import Settings
    root = Path(os.getcwd()).resolve()
    settings = Settings(root)
    set_language(str(settings.get("language") or ""))
    if name == "version":
        print(f"{NAME} {VERSION}")
        return 0
    if name == "install":
        from .installer import install_command
        ok, message = install_command()
        print(message)
        return 0 if ok else 1
    if name == "uninstall":
        from .installer import uninstall_command
        ok, message = uninstall_command()
        print(message)
        return 0 if ok else 1
    if name == "update":
        from .i18n import tr
        print(tr("MemuCode обновляется вместе с MemuAI: скачайте новую версию установщика MemuAI и установите поверх.",
                 "MemuCode updates together with MemuAI: install the new MemuAI setup over the old one."))
        return 0
    if name == "config":
        return _config_command(settings, argv)
    if name == "sessions":
        return _sessions_command(root, argv)
    if name == "mcp":
        return _mcp_command(root, argv)
    if name == "schedule":
        return _schedule_command(argv)
    return asyncio.run(_async_subcommand(name, root, settings, argv))

def _schedule_command(argv: List[str]) -> int:
    """``memucode schedule list`` and ``memucode schedule run <id>`` (what the Windows scheduler starts)."""
    from . import schedule
    action = argv[0] if argv else "list"
    if action == "run":
        if len(argv) < 2:
            print("usage: memucode schedule run <task id>", file=sys.stderr)
            return 2
        return asyncio.run(schedule.execute(argv[1]))
    if action in ("list", "ls"):
        tasks = schedule.load_tasks()
        for task in tasks:
            following = time.strftime("%Y-%m-%d %H:%M", time.localtime(task.next_run)) if task.next_run else "-"
            print(f"{task.id}  {'on ' if task.enabled else 'off'}  {task.runner:<7} {schedule.describe_when(task.when):<24} next {following}  {task.title}")
        if not tasks:
            print("no scheduled tasks (create one in MemuCode with /schedule)")
        return 0
    print("usage: memucode schedule [list | run <task id>]", file=sys.stderr)
    return 2

async def _async_subcommand(name: str, root: Path, settings: Any, argv: List[str]) -> int:
    from .runtime import Runtime
    from .tui.render import Renderer
    from .tui.theme import color_depth, palette_for
    runtime = Runtime(root, settings)

    depth = color_depth() if sys.stdout.isatty() else "none"
    renderer = Renderer(palette_for(settings.get("theme"), settings.get("accent")), depth)
    import shutil
    renderer.width = shutil.get_terminal_size((100, 30)).columns
    try:
        if name == "doctor":
            from rich.table import Table
            from rich.text import Text
            from .doctor import run_checks

            if sys.stderr.isatty():
                sys.stderr.write("Checking MemuCode: models, network, tools and components…\r")
                sys.stderr.flush()
            checks = await run_checks(runtime)
            if sys.stderr.isatty():
                sys.stderr.write("\x1b[2K\r")
                sys.stderr.flush()
            table = Table.grid(padding=(0, 2))
            for row in checks:
                mark = {"ok": Text("✓", style="ok"), "warn": Text("⚠", style="warn"), "error": Text("✗", style="err")}.get(
                    row["status"], Text("·", style="dim"))
                detail = Text(row["detail"])
                if row.get("hint"):
                    detail.append("\n" + row["hint"], style="dim")
                table.add_row(mark, Text(row["name"], style="bold"), detail)
            sys.stdout.write(renderer.render(table))
            return 1 if any(row["status"] == "error" for row in checks) else 0
        if name == "models":
            models = await runtime.router.models(refresh=True)
            from .. import pricing

            await asyncio.to_thread(pricing.refresh_online, timeout=8.0)
            for model in models:
                price = pricing.lookup(model.id, model.provider)
                print(f"{model.id:<48} {model.provider:<14} {model.context or '':>9} {pricing.describe(price)}")
            for source, error in runtime.router.errors.items():
                print(f"! {source}: {error}", file=sys.stderr)
            return 0 if models else 1
    finally:
        await runtime.router.close()
    return 0

def _config_command(settings: Any, argv: List[str]) -> int:
    from .settings import SettingsError, flatten, mask
    scope = "user"
    for flag, value in (("--project", "project"), ("--local", "local"), ("-g", "user"), ("--global", "user")):
        if flag in argv:
            scope = value
            argv = [item for item in argv if item != flag]
    if not argv or argv[0] in ("list", "ls"):
        for key, value in sorted(flatten(settings.data).items()):
            print(f"{key} = {json.dumps(mask(key, value), ensure_ascii=False)}  [{settings.source_of(key)}]")
        return 0
    action = argv[0]
    try:
        if action == "get" and len(argv) >= 2:
            print(json.dumps(mask(argv[1], settings.get(argv[1])), ensure_ascii=False))
            return 0
        if action == "set" and len(argv) >= 3:
            raw = " ".join(argv[2:])
            try:
                value = json.loads(raw)
            except ValueError:
                value = raw
            settings.set(argv[1], value, scope)
            print(f"{argv[1]} = {raw} ({scope})")
            return 0
        if action in ("unset", "remove") and len(argv) >= 2:
            print("removed" if settings.unset(argv[1], scope) else "not set")
            return 0
    except SettingsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("usage: memucode config [list | get <key> | set <key> <value> | unset <key>] [--project|--local]", file=sys.stderr)
    return 2

def _sessions_command(root: Path, argv: List[str]) -> int:
    from .session import list_sessions
    from .timefmt import ago
    rows = list_sessions(None if "--all" in argv else root, limit=100)
    for row in rows:
        print(f"{row['id']}  {ago(row.get('updated', 0)):<14} {int(row.get('prompts') or 0):>4}  {(row.get('title') or '')[:70]}")
    if not rows:
        print("no conversations")
    return 0

def _mcp_command(root: Path, argv: List[str]) -> int:
    from .. import mcp_client
    config = mcp_client.load_config()
    if not argv or argv[0] in ("list", "ls"):
        for name, spec in config["servers"].items():
            state = "on " if spec.get("enabled", True) else "off"
            print(f"{state} {name:<20} {spec['command']} {' '.join(spec.get('args') or [])}")
        project = root / ".mcp.json"
        if project.is_file():
            print(f"(project servers in {project})")
        return 0
    action = argv[0]
    if action == "add" and len(argv) >= 3:
        name, command = argv[1], argv[2:]
        env = {}
        while command and command[0] in ("-e", "--env") and len(command) >= 2:
            key, _, value = command[1].partition("=")
            env[key] = value
            command = command[2:]
        if command and command[0] == "--":
            command = command[1:]
        if not command:
            print("usage: memucode mcp add <name> [-e KEY=VALUE] -- <command> [args…]", file=sys.stderr)
            return 2
        config["servers"][name] = {"command": command[0], "args": command[1:], "env": env, "enabled": True}
        mcp_client.save_config(config)
        print(f"added {name}")
        return 0
    if action in ("remove", "rm") and len(argv) >= 2:
        removed = config["servers"].pop(argv[1], None)
        mcp_client.save_config(config)
        print("removed" if removed else "not found")
        return 0
    if action == "get" and len(argv) >= 2:
        print(json.dumps(config["servers"].get(argv[1]), ensure_ascii=False, indent=2))
        return 0
    print("usage: memucode mcp [list | add <name> -- <command…> | remove <name> | get <name>]", file=sys.stderr)
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
