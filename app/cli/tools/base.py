"""Tool definitions shared by every MemuCode tool.

A tool runs in two phases so the permission dialog can show the real change
before anything is written:

1. ``prepare(ctx, args)`` validates the arguments and computes a preview
   (a diff, the command line, the domain) — nothing changes on disk;
2. ``run(ctx, args, prepared)`` performs the action after permission.

Errors the model should see are raised as :class:`ToolError`; they become an
``ok: false`` result, never a crash of the agent loop.
"""
from __future__ import annotations

import itertools
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

if TYPE_CHECKING:  # pragma: no cover
    from ..runtime import Runtime

KINDS = ("read", "edit", "exec", "web", "meta", "agent", "mcp", "automation")
_CALL_SEQUENCE = itertools.count()


class ToolError(Exception):
    """A failure the model can act on (bad path, no match, denied…)."""


@dataclass
class ToolCall:
    name: str
    args: Dict[str, Any]
    id: str = field(default_factory=lambda: "t" + uuid.uuid4().hex[:10])
    raw_name: str = ""
    status: str = "pending"          # pending | running | done | error | denied | cancelled
    started: float = 0.0
    finished: float = 0.0
    result: Optional["ToolResult"] = None
    display: str = ""                # "Read(src/app.py)"
    live: List[str] = field(default_factory=list)   # streaming output lines for the UI
    seq: int = field(default_factory=lambda: next(_CALL_SEQUENCE))   # order in which calls were issued
    notes: List[str] = field(default_factory=list)  # what had to be repaired to read this call's JSON

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "name": self.name, "args": self.args}


@dataclass
class ToolResult:
    ok: bool
    output: Any = ""                 # what the model receives (str or JSON-able)
    summary: str = ""                # one line for the UI: "Read 120 lines"
    detail: str = ""                 # longer UI text (command output, listing)
    diff: str = ""                   # unified diff for the UI
    error: str = ""
    images: List[Dict[str, Any]] = field(default_factory=list)   # {"media_type", "data"}
    meta: Dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def fail(message: str, **meta: Any) -> "ToolResult":
        return ToolResult(ok=False, output="", summary=message.splitlines()[0][:200] if message else "",
                          error=message, meta=dict(meta))


@dataclass
class Prepared:
    key: str = ""                    # permission key: path, command, domain
    preview: str = ""                # text shown in the permission dialog
    diff: str = ""
    title: str = ""                  # dialog title ("Edit file", "Run command")
    outside: bool = False            # target outside the workspace
    readonly: bool = False           # this call only reads, although the tool can also change things
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolContext:
    runtime: "Runtime"
    call: ToolCall
    turn: str = ""
    depth: int = 0
    progress: Optional[Callable[[str], None]] = None
    agent_session: Any = None          # a sub-agent's own session (fresh read cache)
    ui: Any = None

    @property
    def root(self) -> Path:
        return self.runtime.root

    @property
    def session(self) -> Any:
        return self.agent_session if self.agent_session is not None else self.runtime.session

    @property
    def interface(self) -> Any:
        return self.ui if self.ui is not None else self.runtime.ui

    def report(self, line: str) -> None:
        if self.progress is not None:
            try:
                self.progress(line)
            except Exception:
                pass


class Tool:
    """Base class. Subclasses set the class attributes and implement ``run``."""

    name: str = ""
    aliases: Tuple[str, ...] = ()
    kind: str = "read"
    description: str = ""            # full description for native schemas
    short: str = ""                  # one line for the text protocol listing
    parameters: Dict[str, Any] = {"type": "object", "properties": {}}
    required: Tuple[str, ...] = ()
    allow_empty: Tuple[str, ...] = ()  # required arguments for which "" is a real answer (the content of an empty file)
    parallel: bool = False           # safe to run alongside other parallel tools
    plan_safe: bool = True           # allowed in plan mode

    def enabled(self, runtime: "Runtime") -> bool:
        return True

    def permission_key(self, runtime: "Runtime", args: Dict[str, Any]) -> str:
        return ""

    def display(self, args: Dict[str, Any]) -> str:
        """Short argument summary: ``Read(src/app.py)``."""
        for key in ("file_path", "path", "command", "pattern", "query", "url", "description", "task", "name"):
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                text = " ".join(value.split())
                return text if len(text) <= 90 else text[:87] + "…"
        return ""

    async def prepare(self, ctx: ToolContext, args: Dict[str, Any]) -> Prepared:
        return Prepared(key=self.permission_key(ctx.runtime, args))

    async def run(self, ctx: ToolContext, args: Dict[str, Any], prepared: Prepared) -> ToolResult:
        raise NotImplementedError

    # -------- schema
    def schema(self) -> Dict[str, Any]:
        params = dict(self.parameters)
        params.setdefault("type", "object")
        if self.required:
            params["required"] = list(self.required)
        return {"name": self.name, "description": self.description or self.short, "parameters": params}

    def listing(self) -> str:
        """``• Read {file_path, offset?, limit?} — …`` for the text protocol."""
        props = (self.parameters or {}).get("properties") or {}
        names = []
        for name, spec in props.items():
            label = name if name in self.required else name + "?"
            choices = spec.get("enum") if isinstance(spec, dict) else None
            if isinstance(choices, list) and choices and len(choices) <= 6:
                label += ": " + "|".join(str(choice) for choice in choices)
            names.append(label)
        signature = "{" + ", ".join(names) + "}" if names else "{}"
        # Many one-liners repeat the signature in braces ("… {command, timeout?}"): said once is enough — the
        # listing is in every request of a text-protocol model. A brace group inside a word (*.{ts,tsx}) stays.
        text = re.sub(r"(?:\s*:)?\s+\{[^{}]*\}", "", self.short or self.description).strip().rstrip(":")
        return f"• {self.name} {signature} — {text or self.short or self.description}"

    def check_required(self, args: Dict[str, Any]) -> None:
        """Every required argument is there. A model that wrote ``path`` for ``file_path`` (or ``cmd`` for
        ``command``) is understood: the value is moved to the name the tool reads. An empty string counts as
        missing — except where it is the answer (``allow_empty``: the text of an empty file, the replacement that
        deletes a block)."""
        missing = []
        for name in self.required:
            if self._present(args, name):
                continue
            for alias in ARG_ALIASES.get(name, ()):
                if self._present(args, alias, name):
                    args[name] = args[alias]
                    break
            else:
                missing.append(name)
        if missing:
            raise ToolError(f"{self.name}: missing required argument(s): {', '.join(missing)}")

    def _present(self, args: Dict[str, Any], key: str, canonical: str = "") -> bool:
        value = args.get(key)
        if value is None:
            return False
        return value != "" or (canonical or key) in self.allow_empty


#: Other names models use for the same argument. Only the names a tool *requires* are listed: the tools read their
#: optional arguments through :func:`arg`, which knows the aliases itself.
ARG_ALIASES: Dict[str, Tuple[str, ...]] = {
    "file_path": ("path", "filePath", "filename", "file"),
    "old_string": ("old_text", "old_str", "oldString"),
    "new_string": ("new_text", "new_str", "newString"),
    "content": ("text", "file_text", "contents"),
    "command": ("cmd",),
    "pattern": ("query", "regex"),
    "query": ("q",),
    "url": ("href", "link"),
    "task": ("goal",),
    "prompt": ("task",),
    "question": ("text",),
    "plan": ("text",),
    "name": ("skill", "id"),
    "shell_id": ("bash_id", "id"),
    "task_id": ("id",),
}


# ---------------------------------------------------------------- paths

def arg(args: Dict[str, Any], *names: str, default: Any = None) -> Any:
    """First present argument among aliases (``file_path``/``path``)."""
    for name in names:
        if name in args and args[name] not in (None, ""):
            return args[name]
    return default


def whole(value: Any, default: int, name: str = "value", *, low: Optional[int] = None, high: Optional[int] = None) -> int:
    """A whole number from what a model wrote: ``5``, ``5.0``, ``"5"`` — clamped to ``low``..``high``; the default
    when nothing was given; a sentence saying what to fix for anything else (not a ValueError for the log)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        number = default
    elif isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ToolError(f"{name} must be a whole number.")
    else:
        try:
            number = int(float(str(value).strip().replace(",", ".")))
        except (ValueError, OverflowError) as exc:
            raise ToolError(f"{name} must be a whole number, not {str(value)[:40]!r}.") from exc
    if low is not None:
        number = max(low, number)
    if high is not None:
        number = min(high, number)
    return number


def flag(value: Any, default: bool = False) -> bool:
    """A yes/no argument from what a model wrote: ``true``, ``"true"``, ``"yes"``, ``1`` — and ``"false"``, ``"0"``,
    ``"no"`` as False (``bool("false")`` is True, which once turned a careful Edit into a replace-all)."""
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in ("true", "yes", "y", "1", "on", "да"):
        return True
    if text in ("false", "no", "n", "0", "off", "none", "null", "нет"):
        return False
    return default


def structured(value: Any) -> Any:
    """The JSON a model put into a string argument (``"edits": "[{...}]"``) as a value; anything else unchanged.
    Strict JSON first, then the forgiving reader of the tool protocol."""
    if isinstance(value, str):
        text = value.strip()
        if text[:1] in "[{":
            try:
                return json.loads(text)
            except ValueError:
                pass
            try:
                from ..protocol import read_json
                read = read_json(text, 0)
                if not read.incomplete:
                    return read.value
            except Exception:
                pass
    return value


def items(value: Any) -> List[Any]:
    """A list from what a model wrote: a list stays, one string becomes a list of one, anything else is empty."""
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def resolve_path(runtime: "Runtime", value: Any, *, must_exist: bool = False) -> Path:
    raw = str(value or "").strip().strip('"').strip("'")
    if not raw:
        raise ToolError("a path is required")
    if raw.startswith("@"):
        raw = raw[1:].strip("()")
    raw = os.path.expanduser(raw)
    path = Path(raw)
    if not path.is_absolute():
        base = runtime.session.cwd if getattr(runtime.session, "cwd", None) else runtime.root
        path = Path(base) / raw
    try:
        path = path.resolve()
    except OSError as exc:
        raise ToolError(f"invalid path {raw}: {exc}") from exc
    if must_exist and not path.exists():
        raise ToolError(f"File does not exist: {display_path(runtime, path)}")
    return path


def workspace_of(runtime: "Runtime", path: Path) -> Optional[Path]:
    """The workspace folder (project root or an added directory) containing ``path``."""
    for base in [runtime.root, *runtime.add_dirs]:
        try:
            path.relative_to(base)
            return base
        except ValueError:
            continue
    return None


def display_path(runtime: "Runtime", path: Path) -> str:
    base = workspace_of(runtime, path)
    if base is None:
        return str(path)
    rel = path.relative_to(base).as_posix()
    if base == runtime.root:
        return rel or "."
    return f"{base.name}/{rel}" if rel else base.name


def relative_key(runtime: "Runtime", path: Path) -> str:
    """Permission-rule key: project-relative posix path, else absolute posix."""
    try:
        return path.relative_to(runtime.root).as_posix() or "."
    except ValueError:
        return path.as_posix()


# ---------------------------------------------------------------- registry

class ToolRegistry:
    ALIASES = {
        # MemuAI desktop MemuCode / Work names
        "work_read": "Read", "code_read_cached": "Read", "read_file": "Read", "view": "Read", "cat": "Read",
        "work_write": "Write", "write_file": "Write", "create_file": "Write",
        "work_patch": "Edit", "edit_file": "Edit", "str_replace": "Edit", "replace": "Edit",
        "str_replace_based_edit_tool": "Edit", "apply_patch": "Edit",
        "multiedit": "MultiEdit", "multi_edit": "MultiEdit",
        "work_search": "Grep", "search": "Grep", "grep_search": "Grep", "search_files": "Grep",
        "work_tree": "LS", "list_dir": "LS", "list_directory": "LS", "list_files": "Glob", "ls": "LS",
        "glob_files": "Glob", "find_files": "Glob",
        "code_run": "Bash", "run_command": "Bash", "shell": "Bash", "execute_command": "Bash",
        "terminal": "Bash", "powershell": "Bash", "code_terminal_run": "Bash",
        "code_task": "BashOutput", "bash_output": "BashOutput", "kill_shell": "KillShell",
        "web_search": "WebSearch", "websearch": "WebSearch", "web_fetch": "WebFetch", "fetch": "WebFetch",
        "fetch_url": "WebFetch", "todowrite": "TodoWrite", "todo_write": "TodoWrite", "todos": "TodoWrite",
        "code_git": "Git", "git": "Git", "code_project_info": "ProjectInfo", "project_info": "ProjectInfo",
        "code_symbols": "Symbols", "work_outline": "Outline", "code_outline_deep": "Outline",
        "code_skill": "Skill", "skill": "Skill", "agent": "Task", "subagent": "Task", "dispatch_agent": "Task",
        "ask_user": "AskUserQuestion", "askuser": "AskUserQuestion", "ask_user_question": "AskUserQuestion",
        "exit_plan_mode": "ExitPlanMode", "exitplanmode": "ExitPlanMode", "browser": "Browser",
        "computer": "Computer", "computer_use": "Computer", "decide": "Decide", "advisor": "Advisor",
        "view_image": "Read", "read_image": "Read",
    }

    def __init__(self) -> None:
        self.tools: Dict[str, Tool] = {}

    def add(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def remove_prefix(self, prefix: str) -> None:
        for name in [name for name in self.tools if name.startswith(prefix)]:
            self.tools.pop(name, None)

    def resolve(self, name: str) -> Optional[Tool]:
        raw = str(name or "").strip()
        if raw in self.tools:
            return self.tools[raw]
        low = raw.lower()
        for tool_name, tool in self.tools.items():
            if tool_name.lower() == low or low in (alias.lower() for alias in tool.aliases):
                return tool
        alias = self.ALIASES.get(low)
        if alias and alias in self.tools:
            return self.tools[alias]
        if low.startswith("mcp_") and not low.startswith("mcp__"):
            # MemuAI desktop spelling mcp_<server>_<tool>
            for tool_name, tool in self.tools.items():
                if tool_name.startswith("mcp__") and tool_name.replace("__", "_").lower() == low:
                    return tool
        return None

    def argument_names(self) -> List[str]:
        """Every argument name of every tool: the protocol parser trusts them as "the next key starts here"."""
        names: set = set()
        for tool in self.tools.values():
            properties = (tool.parameters or {}).get("properties") or {}
            if isinstance(properties, dict):
                names.update(str(key) for key in properties)
        for aliases in ARG_ALIASES.values():             # "path" for "file_path": a key too, not text
            names.update(aliases)
        return sorted(names)

    def available(self, runtime: "Runtime", *, allow: Optional[List[str]] = None,
                  deny: Optional[List[str]] = None) -> List[Tool]:
        rows = []
        for tool in self.tools.values():
            if not tool.enabled(runtime):
                continue
            if allow is not None and tool.name not in allow and not any(
                    tool.name.startswith(prefix.rstrip("*")) for prefix in allow if prefix.endswith("*")):
                continue
            if deny and tool.name in deny:
                continue
            rows.append(tool)
        return rows
