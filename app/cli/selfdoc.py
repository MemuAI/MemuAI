"""MemuCode knows itself — the built-in ``memucode-guide`` skill (Round 33).

Asked "how do I turn on planning in MemuCode CLI?", the model used to talk about command-line flags: nothing told it
that the user types ``/plan`` (or presses Shift+Tab) in this very terminal. This module gives it an exact reference
of MemuCode itself, **generated from the real sources** so it never drifts:

* slash commands — from the command registry (``commands.REGISTRY``: name, aliases, arguments, description);
* keys — from the interface's own shortcut list (``tui.app.SHORTCUT_ROWS``);
* permission modes — from ``permissions`` (cycle order, titles, explanations);
* command-line flags and sub-commands — from the real ``argparse`` parser (``main.build_parser``) and ``SUBCOMMANDS``;
* settings — from ``settings.DEFAULTS`` (with a note for every key), hook events, built-in agents, output styles and
  the real paths of this installation.

Hand-written parts (quick answers, explanations of a few commands) may only name commands that exist: the tests
(``tests/round33_memucode_test.py``) fail when a registered command, flag, setting or sub-command is undocumented
or when the text names a ``/command`` that does not exist.

How it reaches the model:

* the skill appears in the skill list (``project.Skills``) with a line that says when to use it, and the system
  prompt has one short pointer (:data:`POINTER`);
* a cheap trigger (:func:`is_self_question`, regular expressions, no model call) recognises a question about MemuCode
  itself and attaches the whole reference to that turn (``prompts.reminders`` → :func:`turn_block`) — "auto-loaded";
* ``/help-ask <question>`` asks with the reference attached for sure (and answers offline from it without a model).
"""
from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

GUIDE_ID = "memucode-guide"
TITLE = "MemuCode guide"
DESCRIPTION = ("Exact reference of MemuCode CLI itself: every slash command, keys, permission modes (plan, auto…), "
               "flags, settings, files, sessions, agents, skills, MCP, cost, Telegram. Use when the user asks how to "
               "do something in MemuCode or what one of its commands, keys or settings does.")

POINTER = ("# About MemuCode itself\nQuestions about MemuCode (its slash commands, modes such as plan or auto, keys, "
           "settings, flags, sessions, skills, MCP, agents, cost, Telegram) are answered from the built-in skill "
           f"`{GUIDE_ID}` — load it with Skill first unless it is already attached; never guess. Features are used by "
           "typing a slash command in this terminal (planning: /plan or Shift+Tab), not by restarting with a flag.")

def pointer(runtime: Any) -> str:
    """:data:`POINTER`, unless the user switched the guide off in /skills."""
    try:
        if GUIDE_ID in set(runtime.skills.disabled()):
            return ""
    except Exception:
        pass
    return POINTER

_LOCK = threading.Lock()
_CACHE: Dict[str, str] = {}

FAQ: Tuple[Tuple[str, str], ...] = (
    ("Turn on planning / plan mode (планирование, режим плана)",
     "Type /plan (or /plan <task> to plan that task right away), or press Shift+Tab until the status line says "
     "“plan mode”. The agent only reads and researches, then shows a plan (ExitPlanMode) for you to approve; "
     "nothing is changed before that. /plan off (or Shift+Tab) leaves it. Plan mode belongs to this window only. "
     "Starting with `memucode --permission-mode plan` also works, but inside a running session /plan is the way."),
    ("Change the permission mode (ask first / accept edits / auto / bypass)",
     "Shift+Tab cycles ask first → accept edits → plan → auto; /mode <mode> or /permissions picks one. "
     "bypassPermissions only via /mode bypassPermissions or the --dangerously-skip-permissions flag. The mode is "
     "shared by every MemuCode window (setting permission_sync), except plan."),
    ("Switch the model", "/model (or Alt+P) opens a searchable picker; /model <name> picks it; /model auto routes "
     "each request by difficulty; /model notes keeps remarks about models; /fast toggles the fast model; "
     "/small-model sets the model for titles, compaction and /btw."),
    ("Think harder / reasoning", "/effort auto|minimal|low|medium|high|max; /think (Alt+T) toggles extended "
     "thinking; Alt+R shows or hides the model's thinking text."),
    ("Run things in parallel / sub-agents", "Ask for it in words (“use agents”, “in parallel”) or run /background "
     "<task> (also /bg, /subtask, or start a message with &). /tasks shows and steers them; /agents chooses the models "
     "agents may use. The setting subagents = ask|auto|off decides whether the agent may start them on its own."),
    ("Continue an old conversation", "/resume (searchable list) or /continue (the latest here); from the shell "
     "`memucode -c` or `memucode -r <id>`. /dashboard lists every session of every project and of other tools; "
     "/resume-claude, /resume-codex … hand over other tools' sessions."),
    ("Undo / go back", "/undo reverts the file edits of the last answer; /rewind (or Esc Esc) takes the code and/or "
     "the conversation back to an earlier message (checkpoints are made before every answer); /branch forks."),
    ("Context is full / save tokens", "/context shows what fills the window; /compact [focus] summarises the history "
     "(automatic at auto_compact_threshold, 85% by default); /clear starts a new conversation; /usage, /cost, /stats "
     "show tokens and cost; the status-line number is chosen in /settings (footer_metric) or by clicking it."),
    ("Limits ran out / the key stopped working for now", "/auto-continue sends “continue” by itself later: "
     "/auto-continue 18:30, +2h, in 45m, tomorrow 09:00, or auto (the reset time the provider named); status, cancel, "
     "now; it retries with backoff (--retries N) and can switch to a fallback model (--fallback MODEL). The window "
     "must stay open; the plan is saved with the conversation and re-armed by /resume."),
    ("Run a prompt on a schedule", "/schedule daily 09:00 -- <prompt> (in 20m, at 15:30, every 2h, weekdays 09:00, "
     "cron …): in this window or in the Windows Task Scheduler. /loop 10m <prompt> repeats a prompt in this window "
     "(/loop stop)."),
    ("Memory / project rules", "/init writes MEMUCODE.md for the project; a message starting with # saves a rule "
     "(project, personal project or all projects); /memory opens the memory files; /remember <fact> saves a fact to "
     "MemuAI memory shared with the MemuAI window."),
    ("Skills and custom commands", "/skills manages skills (new, edit, delete, disable, enable, view, restore); run a "
     "skill by hand as /skill-name; custom commands are .md files in .memucode/commands/ (or your personal commands "
     "folder, or .claude/commands/) and run as /name with $ARGUMENTS, $1…$9."),
    ("MCP servers", "/mcp lists, adds, toggles and restarts servers; `memucode mcp …` outside the interface. Servers "
     "come from MemuAI's MCP settings and a trusted project's .mcp.json; tools are named mcp__server__tool."),
    ("Ask without interrupting", "/btw <question> — a side question answered by the fast model, not added to the "
     "history."),
    ("Long goal", "/goal <goal> keeps the agent working until it is met; /goal status|pause|continue|clear."),
    ("Images", "Alt+V or Ctrl+V pastes an image, /image <path> or /attach <path> attaches one, @file mentions it; "
     "/vision chooses the image model for models that cannot see."),
    ("Shell", "!command runs it yourself without the model (the output joins the conversation); /run <command> is the "
     "same; Ctrl+R searches input history."),
    ("Connect a provider / API key", "/proxy shows MemuAI Proxy; /proxy add connects Anthropic, OpenAI, OpenRouter or "
     "any OpenAI-compatible API directly (models named @connection:model); /proxy remove."),
    ("Settings", "/settings opens every setting; /config get|set|list|unset|edit; /theme, /color, /vim, /lang, "
     "/output-style, /statusline, /notifications, /sandbox, /hooks, /keybindings, /terminal-setup."),
    ("Phone / Telegram", "The MemuAI Telegram Mini App shows running MemuCode terminals (streaming answer, tools, "
     "todos) and drives them: send a task or a /command, Esc, answer permission questions. It is on while the Mini "
     "App is enabled with “MemuCode control” in MemuAI's Telegram settings; MEMUCODE_NO_REMOTE=1 turns it off for "
     "one terminal."),
    ("Diagnostics", "/doctor checks install, Proxy, models, terminal, tools; /status shows version, model, modes; "
     "/bug saves a report without keys to Downloads/MemuAI; `memucode doctor` without the interface."),
    ("Exit", "/exit (or Ctrl+C twice on an empty line, or Ctrl+D); MemuCode prints `memucode --resume <id>` to come "
     "back."),
)

NOTES: Dict[str, str] = {
    "plan": "turns on plan mode for this window (same as Shift+Tab to “plan mode”); /plan off leaves it",
    "mode": "values: default, acceptEdits, plan, auto, bypassPermissions",
    "model": "/model <words> filters by provider and name",
    "goal": "the agent continues until it writes GOAL COMPLETE; up to 10 continuations by default",
    "tasks": "/tasks <n|name> <text> sends a follow-up to a running agent",
    "rewind": "Esc Esc opens it too",
    "compact": "/compact <what to keep> focuses the summary",
    "schedule": "e.g. /schedule daily 09:00 -- check the failing tests",
    "loop": "while this window is open; minimum 30 s",
    "auto-continue": "times: 18:30, 6:30pm, +2h, in 45m, 1h30m, tomorrow 09:00; also now; --retries N, --no-retry, "
                     "--fallback MODEL; countdown in the status line; keep the window open",
    "help-ask": "answers from this reference; /help <several words> does the same",
    "remember": "/remember list shows what is remembered; --code keeps it for MemuCode only",
    "import": "claude, codex, cursor, agy, grok, gemini, opencode, all",
    "proxy": "/proxy add — connect an API directly; /proxy remove",
    "decisions": "connect a decision model (OpenRouter, TypeSafe/Jev, your endpoint or a chat model)",
    "vision": "/vision test really checks who can read a picture",
    "effort": "a model without levels keeps the value but ignores it (the command says so)",
    "statusline": "a custom command's output replaces the right part of the status line",
}

SUBCOMMAND_NOTES: Dict[str, str] = {
    "doctor": "check the install, connection, terminal and tools",
    "mcp": "MCP servers: list | add <name> [-e KEY=VALUE] -- <command…> | remove <name> | get <name>",
    "config": "settings: list | get <key> | set <key> <value> | unset <key> (--project, --local, -g)",
    "sessions": "list saved conversations of this folder (--all: every folder)",
    "models": "list available models",
    "schedule": "scheduled tasks: list, run <id> (what the Windows Task Scheduler starts)",
    "install": "add the memucode command to the user PATH",
    "uninstall": "remove that command from PATH",
    "update": "MemuCode updates together with MemuAI (install the new MemuAI setup over the old one)",
    "version": "print the version",
}

FLAG_NOTES: Dict[str, str] = {
    "permission_mode": "permission mode of this window (it then ignores modes chosen in other windows)",
    "output_format": "with -p: plain text, one JSON object, or JSON events line by line",
    "effort": "how hard the model thinks (like /effort)",
    "verbose": "unfold tool output",
    "debug": "accepted for compatibility with other CLIs; changes nothing",
}

SETTING_NOTES: Dict[str, str] = {
    "model": "main model (/model)", "small_model": "fast model for titles, compaction, /btw (/small-model)",
    "worker_model": "legacy single agent model", "worker_models": "models agents may use (/agents)",
    "worker_pick": "model | auto | first — who picks an agent's model", "advisor_model": "/advisor model",
    "vision_model": "legacy (the image model is shared with MemuAI, /vision)", "vision_mode": "auto | off (/vision)",
    "browser_model": "model of the browser agent", "computer_model": "model of Computer Use",
    "model_routing": "/model auto levels fast/balanced/strong", "model_notes": "remarks per model (/model notes)",
    "effort": "auto … max (/effort)", "thinking": "extended thinking (/think, Alt+T)",
    "web_search": "the provider's built-in web search (/web)", "temperature": "sampling temperature (null = default)",
    "max_output_tokens": "answer length cap (0 = model default)",
    "backend": "auto | proxy | direct (direct: models without @connection: go to the first direct connection)", "proxy_url": "MemuAI Proxy address (empty = automatic)",
    "proxy_api_key": "Proxy password when it listens on the network (user file only)",
    "proxy_autostart": "start MemuAI Proxy when it is not running", "connections": "direct API connections (/proxy add)",
    "tool_protocol": "auto | native | text", "permission_mode": "default mode at start (/mode)",
    "permission_sync": "share the mode between windows", "permissions": "allow / deny / ask rules (/permissions)",
    "max_turns": "tool rounds per request before asking (80)", "subagents": "ask | auto | off",
    "auto_compact": "summarise the history automatically", "auto_compact_threshold": "% of the window (85)",
    "context_budget": "tokens after which old large tool results are folded (40000; 0 = by window share)",
    "context_window": "override the model's window (0 = known value)",
    "keep_recent_messages": "messages kept verbatim by compaction", "output_style": "/output-style",
    "language": "interface language (English)", "memory_files": "memory file names read from the project",
    "memuai_memory": "use facts from MemuAI memory", "respect_gitignore": "skip ignored files in @ and searches",
    "auto_title": "name conversations automatically", "include_co_authored_by": "Co-Authored-By line in commits",
    "cleanup_period_days": "keep conversations this long", "env": "extra environment variables for commands",
    "hooks": "hooks (/hooks)", "shell": "auto | bash | powershell | pwsh | cmd",
    "sandbox": "command sandbox (/sandbox)", "browser": "browser agent on/off", "computer": "Computer Use options",
    "web": "fetch_ask — ask before opening pages", "theme": "/theme", "accent": "accent colour (/color)",
    "vim_mode": "/vim", "show_thinking": "show the thinking text (Alt+R)", "verbose": "unfold tool output (Ctrl+O)",
    "spinner_tips": "tips under the spinner", "todo_panel": "todo list under the input (Ctrl+T)",
    "notifications": "auto | bell | toast | off (/notifications)", "notify_after_seconds": "notify after this long",
    "editor": "external editor for Ctrl+G", "statusline": "/statusline", "footer_metric": "cost | tokens | total | "
    "context | lines | duration | tools | requests | off", "tips": "show tips",
    "decisions": "decision model (/decisions)", "skills": "disabled skills (/skills)",
    "auto_continue": "retries, fallback_model, message of /auto-continue (e.g. /config set auto_continue.retries 6)",
    "pricing": "custom prices, online catalog, cost in the status line (/cost)", "stats": "record statistics (/stats)",
    "trusted_dirs": "trusted folders (asked on first visit)", "onboarded": "first-run questions answered",
}

INPUT_ROWS: Tuple[Tuple[str, str], ...] = (
    ("plain text", "a task for the agent"),
    ("/command", "a MemuCode command (the list opens as you type; Tab completes arguments)"),
    ("@path", "mention a project file or folder — its content is attached"),
    ("@agent-name", "hand the task to a sub-agent"),
    ("!command", "run it in the shell yourself; the output joins the conversation"),
    ("#rule", "save a rule to a memory file"),
    ("&task", "run the task in the background (/background)"),
    ("? on an empty line", "keyboard shortcuts"),
)

def _registry() -> Dict[str, Any]:
    from . import commands
    from .commands import (autocontinue_cmds, config_cmds, dashboard_cmds, decisions_cmds, foreign_cmds,  # noqa: F401
                           guide_cmds, import_cmds, info_cmds, model_cmds, routing_cmds, schedule_cmds, session_cmds, skill_cmds,
                           work_cmds)
    return commands.REGISTRY

def command_rows() -> List[Tuple[str, str, str, Tuple[str, ...], str, str]]:
    """``(category, name, hint, aliases, description, note)`` of every visible built-in command."""
    from .commands import CATEGORIES
    registry = _registry()
    order = list(CATEGORIES)
    rows = [(cmd.category, cmd.name, cmd.hint, tuple(cmd.aliases), cmd.en, NOTES.get(cmd.name, ""))
            for cmd in registry.values() if not cmd.hidden]
    rows.sort(key=lambda row: (order.index(row[0]) if row[0] in order else len(order), row[1]))
    return rows

def flag_rows() -> List[Tuple[str, str]]:
    """``("-m, --model MODEL", "help")`` for every option of the real parser."""
    from .main import build_parser
    rows = []
    for action in build_parser()._actions:
        if not action.option_strings:
            continue
        name = ", ".join(action.option_strings)
        if action.choices:
            name += " {" + ",".join(str(choice) for choice in action.choices) + "}"
        elif action.nargs not in (0,) and not isinstance(action.const, bool) and action.metavar != "==SUPPRESS==" \
                and action.__class__.__name__ not in ("_StoreTrueAction", "_HelpAction", "_StoreFalseAction"):
            name += " " + (action.metavar or action.dest.upper())
        text = (action.help or "").replace("%(default)s", str(action.default)) or FLAG_NOTES.get(action.dest, "")
        rows.append((name, text))
    return rows

def shortcut_rows() -> List[Tuple[str, str]]:
    try:
        from .tui.app import SHORTCUT_ROWS
        rows = list(SHORTCUT_ROWS)
    except Exception:
        rows = []
    extra = [("enter", "send"), ("esc", "stop the agent / close a menu"), ("pageup / pagedown", "scroll"),
             ("tab", "complete; tick a row in multi-choice lists"), ("ctrl+a / ctrl+x", "select all / cut the input")]
    known = {key for key, _ in rows}
    return rows + [row for row in extra if row[0] not in known]

def mode_rows() -> List[Tuple[str, str, str]]:
    from .permissions import CYCLE, mode_explain, mode_title
    from .settings import PERMISSION_MODES
    return [(mode, mode_title(mode), mode_explain(mode) + ("" if mode in CYCLE else
                                                           " (not in the Shift+Tab cycle)"))
            for mode in PERMISSION_MODES]

def _paths() -> List[Tuple[str, str]]:
    from . import paths
    rows: List[Tuple[str, str]] = []
    try:
        home = paths.home()
        rows += [("MemuCode folder", str(home)), ("personal settings", str(paths.user_settings_file())),
                 ("personal memory (all projects)", str(paths.user_memory_file())),
                 ("personal skills", str(paths.user_skills_dir())), ("personal commands", str(paths.user_commands_dir())),
                 ("personal agents", str(paths.user_agents_dir())),
                 ("output styles", str(paths.output_styles_dir())), ("conversations", str(paths.sessions_root()))]
    except Exception:
        pass
    rows += [("project settings (shared)", ".memucode/settings.json"),
             ("project settings (personal, git-ignored)", ".memucode/settings.local.json"),
             ("project memory", "MEMUCODE.md (also AGENTS.md, CLAUDE.md); MEMUCODE.local.md — personal"),
             ("project skills", ".memucode/skills/<id>/SKILL.md (also .claude/skills/)"),
             ("project commands", ".memucode/commands/*.md (also .claude/commands/)"),
             ("project agents", ".memucode/agents/*.md (also .claude/agents/)"),
             ("project MCP servers", ".mcp.json (trusted folders only)"),
             ("MCP servers shared with MemuAI", "<MemuAI data>/memuai/mcp.json")]
    return rows

def docs_dir() -> Optional[Path]:
    try:
        from ..docs import docs_dir as _docs
        folder = _docs() / "en"
        return folder if folder.is_dir() else None
    except Exception:
        return None

def _section(title: str, lines: Iterable[str]) -> str:
    body = "\n".join(lines)
    return f"## {title}\n{body}" if body else ""

def build_guide() -> str:
    """The whole reference (Markdown). Cached per process: its sources do not change while MemuCode runs."""
    with _LOCK:
        cached = _CACHE.get("guide")
        if cached:
            return cached
    from . import VERSION
    from .hooks import EVENTS
    from .main import SUBCOMMANDS
    from .project import BUILTIN_AGENTS, BUILTIN_STYLES
    from .settings import DEFAULTS
    from .commands import CATEGORIES
    parts: List[str] = [
        f"# MemuCode CLI {VERSION} — reference of the program itself",
        "Generated from MemuCode's own command registry, key bindings, argument parser and settings: it is exact. "
        "Answer questions about MemuCode from it, quoting commands exactly, in the user's language. A feature is used "
        "by typing a slash command in the running terminal (e.g. /plan) or a key — not by restarting with a flag, "
        "unless the question is about the `memucode` command line. Do not invent commands, flags or settings that are "
        "not listed here; if something is not here, say so.",
    ]
    parts.append(_section("Quick answers", (f"- **{question}** — {answer}" for question, answer in FAQ)))
    parts.append(_section("Input", (f"- `{key}` — {text}" for key, text in INPUT_ROWS)))
    parts.append(_section("Keys", (f"- {key} — {text}" for key, text in shortcut_rows())))
    from .permissions import CYCLE
    parts.append(_section("Permission modes (Shift+Tab cycles: " + " → ".join(CYCLE) + ")",
                          [f"- {mode} ({title}) — {text}" for mode, title, text in mode_rows()]
                          + ["- Rules (Claude Code syntax) in /permissions: Bash(npm test:*), Edit(src/**), "
                             "WebFetch(domain:docs.python.org), mcp__github; deny wins over allow; “yes, don't ask "
                             "again” in a prompt suggests a rule.",
                             "- In auto, git push, sudo, secrets, publishing, deleting whole folders and edits to .env/"
                             ".git/CI always ask; dangerous commands never run in any mode."]))
    current = ""
    lines: List[str] = []
    for category, name, hint, aliases, text, note in command_rows():
        if category != current:
            current = category
            lines.append(f"### {CATEGORIES.get(category, (category, category))[1]}")
        alias = f" (also {', '.join('/' + item for item in aliases)})" if aliases else ""
        lines.append(f"- /{name}{(' ' + hint) if hint else ''} — {text}{alias}" + (f"; {note}" if note else ""))
    lines.append("Also: every skill runs as /skill-name, custom commands as /name; /help [command] shows the list.")
    parts.append(_section("Slash commands (typed in the MemuCode prompt)", lines))
    parts.append(_section("Command line (`memucode …` in a shell)",
                          ["- `memucode` — interactive session in the current folder; `memucode \"task\"` starts with "
                           "a prompt; `memucode -p \"task\"` answers once and exits (scripts, CI, pipes: "
                           "`git diff | memucode -p \"write a commit message\"`)."]
                          + [f"- `{name}` — {text}" for name, text in flag_rows()]
                          + [f"- `memucode {name}` — {SUBCOMMAND_NOTES.get(name, '')}" for name in SUBCOMMANDS]))
    parts.append(_section("Settings (/settings, /config set <key> <value>, `memucode config set`)",
                          ["Layers, lowest first: built-in defaults → personal settings.json → .memucode/settings.json "
                           "→ .memucode/settings.local.json → command-line flags. Keys and connections only in the "
                           "personal file."]
                          + [f"- {key} = {_value(DEFAULTS[key])} — {SETTING_NOTES.get(key, '')}" for key in DEFAULTS]))
    parts.append(_section("Files and folders", (f"- {label}: {value}" for label, value in _paths())))
    parts.append(_section("Agents, skills, hooks, styles", [
        "- Built-in agent types: " + ", ".join(f"{name} ({agent.description[:70]})" for name, agent in BUILTIN_AGENTS.items()),
        "- Hook events (Claude Code format, /hooks): " + ", ".join(EVENTS),
        "- Output styles (/output-style): " + ", ".join(BUILTIN_STYLES) + " or your own .md in the output-styles folder",
        f"- Skills: built-in ones (including this {GUIDE_ID}), project and personal SKILL.md folders; the agent loads a "
        "matching one itself; built-ins can only be disabled (/skills)."]))
    parts.append(_section("How it works (good to know)", [
        "- Models come through MemuAI Proxy (every account of the app) or direct connections (/proxy add). Any model "
        "works: Proxy models call tools in a text format.",
        "- Conversations are saved and only appended to; a checkpoint of the files is made before every answer.",
        "- While the agent works you can keep typing: messages queue; commands that only show or set something run "
        "at once.",
        "- Commands run in a sandbox (Windows job object, memory limit, secrets filtered); the agent works in the "
        "project and /add-dir folders, going outside asks.",
        "- Status line: permission mode (click/Shift+Tab), goal, auto-continue countdown, agents; right: model "
        "(click = /model), ctx % (click = /context), git branch, a number from footer_metric (click to change).",
        "- Cost: tokens and an API-price estimate per model (/usage, /cost, /cost set for own prices, /stats).",
    ]))
    folder = docs_dir()
    if folder is not None:
        pages = [name for name in ("memucode-cli.md", "memucode-guide.md", "telegram-miniapp.md", "decisions.md")
                 if (folder / name).is_file()]
        if pages:
            parts.append(_section("Longer user documentation (read with Read for a detail not covered here)",
                                  [f"- {folder / name}" for name in pages]))
    text = "\n\n".join(part for part in parts if part)
    with _LOCK:
        _CACHE["guide"] = text
    return text

def _value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, str):
        return f'"{value}"'
    if isinstance(value, (dict, list)):
        import json
        text = json.dumps(value, ensure_ascii=False)
        return text if len(text) <= 70 else text[:67] + "…"
    return str(value)

def skill_row() -> Dict[str, Any]:
    """The catalogue row of the built-in skill (its prompt is built when the skill is loaded)."""
    return {"id": GUIDE_ID, "title": TITLE, "description": DESCRIPTION, "source": "builtin", "path": ""}

def documented_doc_path(name: str) -> str:
    folder = docs_dir()
    return str(folder / name) if folder is not None else ""

_NAME = re.compile(r"memu\s*-?\s*code|мему\s*-?\s*код|мемукод|memu\s*cli|memuai\s+cli|мему\s*cli", re.I)
_HOWTO = re.compile(
    r"\?|\b(?:how|what|where|which|why|can i|could i|is there|are there|do you have|does it|explain|tell me|help|"
    r"list|show me|enable|disable|turn (?:on|off)|switch|change|set up|configure|use|start|stop|cancel)\b|"
    r"\b(?:как|что|где|какой|какая|какие|каким|зачем|почему|можно|есть ли|умеешь|умеет|расскаж\w*|объясн\w*|"
    r"покаж\w*|подскаж\w*|помо\w*|справк\w*|список|включ\w*|выключ\w*|отключ\w*|переключ\w*|смен\w*|помен\w*|"
    r"настро\w*|использ\w*|запуст\w*|останов\w*|отмен\w*)", re.I)
_ADDRESS = re.compile(r"\b(?:you|your|yourself|this (?:cli|tool|terminal|program|app)|here)\b|"
                      r"(?:\bты\b|\bтебя\b|\bтебе\b|\bтвои\w*|\bтвой\b|\bтвоя\b|\bтобой\b|в этом (?:cli|терминале|"
                      r"инструменте|интерфейсе|приложении|агенте)|\bздесь\b|\bтут\b)", re.I)
_FEATURE = re.compile(
    r"slash|commands?\b|\bflags?\b|settings?|config|\bmodes?\b|plan(?:ning)? mode|planning|shortcuts?|hotkeys?|"
    r"key ?bindings?|keyboard|sessions?|resume|checkpoints?|rewind|\bundo\b|skills?|\bmcp\b|sub-?agents?|agents?|"
    r"hooks?|permissions?|\bmodels?\b|themes?|status ?line|\bcost\b|tokens?|context window|compact|telegram|"
    r"mini ?app|schedul\w*|auto-?continue|\bvim\b|memory|memucode\.md|sandbox|\bproxy\b|effort|thinking|voice|"
    r"команд\w*|флаг\w*|настрой\w*|режим\w*|план\w*|горяч\w*|клавиш\w*|сочетан\w*|сесси\w*|чекпоинт\w*|откат\w*|"
    r"скилл\w*|навык\w*|агент\w*|хук\w*|разрешен\w*|модел\w*|тем[аыу]\b|строк\w* состояния|стоимост\w*|токен\w*|"
    r"контекст\w*|сжат\w*|телеграм\w*|расписани\w*|автопродолж\w*|памят\w*|песочниц\w*|прокси|размышлен\w*|"
    r"мышлени\w*|голос\w*|лимит\w*", re.I)

_STRONG = re.compile(
    r"plan(?:ning)? mode|режим\w* план\w*|планировани\w*|slash[- ]commands?|команд\w* со слэш\w*|слэш[- ]команд\w*|"
    r"keyboard shortcuts?|hot ?keys?|key ?bindings?|горяч\w* клавиш\w*|сочетани\w* клавиш|permission mode|"
    r"режим\w* (?:разрешени\w*|подтвержд\w*)|auto-?continue|автопродолж\w*|switch (?:the )?models?|"
    r"(?:смен\w*|помен\w*|переключ\w*|выбр\w*) модел\w*|compact the (?:history|context)|сжать (?:историю|контекст)|"
    r"(?:resume|continue) (?:a |the |my )?(?:previous |old |last )?(?:session|conversation)|"
    r"(?:продолж\w*|вернуться к) (?:прошл\w*|стар\w*|предыдущ\w*) (?:сесси\w*|разговор\w*|диалог\w*)", re.I)
_CODE_WORK = re.compile(r"\b(?:fix|implement|refactor|rewrite|debug)\b|исправ\w*|почин\w*|реализу\w*|"
                        r"отрефактор\w*|перепиш\w*", re.I)
_FILE = re.compile(r"[\w/\\.-]+\.(?:py|js|ts|tsx|jsx|md|json|css|html|go|rs|java|cs|cpp|c|h)\b", re.I)
_SLASH = re.compile(r"(?<![\w/:.])/([a-z][a-z0-9-]{1,30})\b", re.I)

def _known_command(name: str) -> bool:
    try:
        registry = _registry()
    except Exception:
        return False
    name = name.lower()
    return name in registry or any(name in cmd.aliases for cmd in registry.values())

def is_self_question(text: str) -> bool:
    """Does this message ask about MemuCode itself? Cheap (regular expressions only) and generous: a false positive
    costs a few thousand tokens for one turn, a miss costs a wrong answer."""
    text = str(text or "").strip()
    if not text or len(text) > 1500:
        return False
    if text.startswith("/"):
        return False
    howto = bool(_HOWTO.search(text))
    if not howto:
        return False
    if _CODE_WORK.search(text) and _FILE.search(text):
        return False
    if _NAME.search(text):
        return True
    if any(_known_command(match.group(1)) for match in _SLASH.finditer(text)):
        return True
    if len(text) <= 400 and _STRONG.search(text):
        return True
    return bool(len(text) <= 300 and _ADDRESS.search(text) and _FEATURE.search(text))

def follow_up(text: str) -> bool:
    """A short follow-up ("а как выключить?", "and the shortcut?") to a question about MemuCode."""
    text = str(text or "").strip()
    return bool(text) and len(text) <= 160 and not text.startswith("/") and bool(_HOWTO.search(text))

def wants_guide(prompts: Sequence[str]) -> bool:
    """``prompts`` newest first: the current request asks about MemuCode, or follows such a question closely."""
    if not prompts:
        return False
    if is_self_question(prompts[0]):
        return True
    return len(prompts) > 1 and follow_up(prompts[0]) and is_self_question(prompts[1])

def turn_block(runtime: Any) -> str:
    """The reference attached to the current turn (``prompts.reminders``), or "" when it is not wanted."""
    try:
        skills = getattr(runtime, "skills", None)
        if skills is not None and GUIDE_ID in set(skills.disabled()):
            return ""
        session = runtime.session
        if GUIDE_ID in getattr(session, "active_skills", set()):
            return ""
        forced = getattr(runtime, "selfdoc_forced_for", None) == (session.id, len(session.user_prompts()))
        if not forced:
            from .session import message_text
            prompts = [message_text(message) for message in reversed(session.user_prompts()[-2:])
                       if message.get("kind") in ("prompt", "command")]
            if not wants_guide(prompts):
                return ""
        guide = build_guide()
    except Exception:
        return ""
    _announce(runtime)
    return ("The user is asking about MemuCode itself (if the question is really about their project, ignore this). "
            f"Built-in skill `{GUIDE_ID}`, attached automatically:\n<skill name=\"{GUIDE_ID}\">\n{guide}\n</skill>")

def _announce(runtime: Any) -> None:
    """One dim line per turn in the transcript, so the person sees why the answer knows the program."""
    try:
        session = runtime.session
        prompts = session.user_prompts()
        marker = (session.id, len(prompts))
        if getattr(runtime, "_selfdoc_announced", None) == marker:
            return
        runtime._selfdoc_announced = marker
        ui = getattr(runtime, "ui", None)
        if ui is not None and getattr(ui, "interactive", False):
            ui.notice("◆ Using the built-in MemuCode guide", "info")
    except Exception:
        pass

_WORD = re.compile(r"[\wа-яё-]{3,}", re.I)
_SYNONYMS = {
    "планирован": "plan", "план": "plan", "модел": "model", "сесси": "session resume", "разговор": "resume conversation",
    "клавиш": "keys shortcut", "горяч": "keys shortcut", "режим": "mode", "разрешен": "permission", "скилл": "skill",
    "агент": "agent", "контекст": "context compact", "стоимост": "cost usage", "токен": "tokens usage",
    "расписан": "schedule", "лимит": "limit auto-continue", "автопродолж": "auto-continue", "откат": "rewind undo",
    "отмен": "undo cancel", "памят": "memory", "настрой": "settings config", "тем": "theme", "картин": "image",
    "изображен": "image", "телеграм": "telegram", "телефон": "telegram phone", "ключ": "api proxy key",
    "думать": "effort thinking", "размышлен": "thinking effort", "фон": "background", "параллел": "parallel agents",
    "цель": "goal", "выход": "exit", "голос": "voice",
}

def offline_answer(question: str, limit: int = 8) -> List[str]:
    """The reference lines that best match a question — for /help-ask without a model."""
    query = str(question or "").lower()
    terms = set(_WORD.findall(query))
    for stem, extra in _SYNONYMS.items():
        if re.search(r"(?<!\w)" + stem, query):
            terms |= set(extra.split())
    terms = {term for term in terms if term not in ("how", "what", "как", "что", "the", "and", "memucode", "мемукод",
                                                    "cli", "можно", "where", "где", "это", "для")}
    if not terms:
        return []
    scored: List[Tuple[float, int, str]] = []
    for index, line in enumerate(build_guide().splitlines()):
        if not line.startswith("- "):
            continue
        low = line.lower()
        score = sum(2.0 if f"/{term}" in low else 1.0 for term in terms if term in low)
        if line.startswith("- **"):
            score *= 1.5
        if score:
            scored.append((score, -index, line[2:]))
    scored.sort(reverse=True)
    return [line for _score, _index, line in scored[:limit]]
