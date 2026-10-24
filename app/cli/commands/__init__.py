"""Slash commands of MemuCode.

Every command is a small async function registered with :func:`command`.
Besides the built-in ones, ``/`` also lists the user's custom commands
(``.memucode/commands/*.md``, ``.claude/commands/*.md``) and every skill
(``/review``-style playbooks), so one menu finds everything.
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional, Tuple

from ..i18n import tr
from ..tui.input import CommandItem

if TYPE_CHECKING:  # pragma: no cover
    from ..tui.app import TerminalUI

Handler = Callable[["Context"], Awaitable[Any]]


@dataclass
class Command:
    name: str
    handler: Handler
    ru: str
    en: str
    aliases: Tuple[str, ...] = ()
    hint: str = ""
    category: str = "general"
    while_busy: bool = False
    immediate: bool = True             # selecting it from the menu runs it at once
    hidden: bool = False

    @property
    def description(self) -> str:
        return tr(self.ru, self.en)


REGISTRY: Dict[str, Command] = {}
CATEGORIES = {
    "general": ("Основное", "General"), "session": ("Сессия", "Session"), "model": ("Модели", "Models"),
    "context": ("Контекст и память", "Context and memory"), "code": ("Работа с кодом", "Working with code"),
    "agents": ("Агенты и инструменты", "Agents and tools"), "config": ("Настройки", "Settings"),
    "info": ("Статистика и диагностика", "Usage and diagnostics"),
}


def command(name: str, ru: str, en: str, *, aliases: Tuple[str, ...] = (), hint: str = "",
            category: str = "general", while_busy: bool = False, immediate: Optional[bool] = None,
            hidden: bool = False):
    def register(function: Handler) -> Handler:
        REGISTRY[name] = Command(name=name, handler=function, ru=ru, en=en, aliases=aliases, hint=hint,
                                 category=category, while_busy=while_busy,
                                 immediate=(not hint or hint.startswith("[")) if immediate is None else immediate,
                                 hidden=hidden)
        return function
    return register


@dataclass
class Context:
    ui: "TerminalUI"
    name: str
    args: str
    raw: str
    extras: Dict[str, Any] = field(default_factory=dict)

    @property
    def runtime(self):
        return self.ui.runtime

    @property
    def settings(self):
        return self.ui.runtime.settings

    @property
    def r(self):
        return self.ui.renderer

    def words(self) -> List[str]:
        try:
            return shlex.split(self.args, posix=True)
        except ValueError:
            return self.args.split()

    def say(self, text: str, level: str = "info") -> None:
        if "capture" in self.extras:
            from rich.text import Text
            self.print(Text(text))
            return
        self.ui.say(text, level)

    def print(self, *items: Any) -> None:
        if "capture" in self.extras:
            self.extras["capture"].extend(items)
            return
        self.ui.print(*items)

    def md(self, text: str) -> None:
        self.ui.print_markdown(text)

    async def select(self, title: str, options: List[Any], **kwargs: Any) -> Any:
        return await self.ui.select(title, options, **kwargs)

    async def ask(self, title: str, *, body: str = "", default: str = "") -> Optional[str]:
        return await self.ui.prompt_text(title, body=body, default=default)

    async def confirm(self, title: str, body: str = "", danger: bool = False) -> bool:
        return await self.ui.confirm(title, body, danger)

    async def prompt(self, text: str, display: Optional[str] = None) -> None:
        await self.ui.prompt_inline(text, display=display if display is not None else self.raw)


class CommandRegistry:
    def __init__(self, ui: "TerminalUI") -> None:
        self.ui = ui
        from . import (autocontinue_cmds, config_cmds, dashboard_cmds, decisions_cmds, foreign_cmds, guide_cmds,
                       import_cmds, info_cmds, model_cmds, routing_cmds, schedule_cmds, session_cmds, skill_cmds,
                       work_cmds)
        self.modules = (autocontinue_cmds, config_cmds, dashboard_cmds, decisions_cmds, foreign_cmds, guide_cmds,
                        import_cmds, info_cmds, model_cmds, routing_cmds, schedule_cmds, session_cmds, skill_cmds,
                        work_cmds)  # registration

    # ------------------------------------------------------------ lookup
    def find(self, name: str) -> Optional[Command]:
        name = name.lower()
        if name in REGISTRY:
            return REGISTRY[name]
        for item in REGISTRY.values():
            if name in item.aliases:
                return item
        return None

    def exists(self, name: str) -> bool:
        """A built-in command, an alias, a custom command or a skill with exactly this name."""
        name = name.strip().lstrip("/").lower()
        if not name or self.find(name) is not None:
            return bool(name)
        runtime = self.ui.runtime
        try:
            return name in runtime.commands() or runtime.skills.get(name) is not None
        except Exception:
            return False

    def items(self) -> List[CommandItem]:
        rows = [CommandItem(cmd.name, cmd.description, cmd.hint, cmd.aliases) for cmd in REGISTRY.values()
                if not cmd.hidden]
        names = {row.name for row in rows} | {alias for cmd in REGISTRY.values() for alias in cmd.aliases}
        runtime = self.ui.runtime
        try:
            for name, custom in runtime.commands().items():
                if name not in names:
                    label = tr("своя команда", "custom") + (f" · {custom.source}" if custom.source else "")
                    rows.append(CommandItem(name, f"{custom.description} ({label})", custom.argument_hint,
                                            source=custom.source))
                    names.add(name)
        except Exception:
            pass
        try:
            for skill in runtime.skills.catalogue():
                if skill["id"] not in names:
                    rows.append(CommandItem(skill["id"], f"{skill.get('description') or skill.get('title')} "
                                            f"({tr('скилл', 'skill')})", "[task]", source="skill"))
                    names.add(skill["id"])
        except Exception:
            pass
        return rows

    @staticmethod
    def split(text: str) -> Tuple[str, str]:
        body = text.strip()[1:]
        name, _, args = body.partition(" ")
        return name.strip().lower(), args.strip()

    def runs_immediately(self, text: str) -> bool:
        name, args = self.split(text)
        cmd = self.find(name)
        return bool(cmd and cmd.immediate and not args)

    def allowed_while_busy(self, text: str) -> bool:
        name, _args = self.split(text)
        cmd = self.find(name)
        return bool(cmd and cmd.while_busy)

    # ------------------------------------------------------------ run
    async def execute(self, text: str) -> None:
        name, args = self.split(text)
        cmd = self.find(name)
        ctx = Context(self.ui, name, args, text.strip())
        if cmd is not None:
            await cmd.handler(ctx)
            return
        runtime = self.ui.runtime
        custom = runtime.commands().get(name)
        if custom is not None:
            from ..project import expand_command
            prompt = expand_command(custom, args)
            previous = runtime.model
            if custom.model:
                runtime.model = custom.model
            try:
                await ctx.prompt(prompt, display=text.strip())
            finally:
                runtime.model = previous
            return
        skill = runtime.skills.get(name)
        if skill is not None:
            runtime.session.active_skills.add(skill["id"])
            prompt = (f"<skill name=\"{skill['id']}\">\n{skill['prompt']}\n</skill>\n\n"
                      f"Follow this skill for the task: {args or '(the task is described by the skill itself)'}")
            await ctx.prompt(prompt, display=text.strip())
            return
        from ..tui.input import fuzzy_score
        candidates = sorted(((fuzzy_score(name, item.name) or -1, item.name) for item in self.items()), reverse=True)
        close = [candidate for score, candidate in candidates[:3] if score > 0]
        hint = tr(f" Может быть: /{', /'.join(close)}?", f" Did you mean /{', /'.join(close)}?") if close else ""
        self.ui.say(tr(f"Нет команды /{name}.", f"Unknown command /{name}.") + hint + tr(" Список: /help", " See /help"), "warn")
