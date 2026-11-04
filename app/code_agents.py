"""MemuCode agents in the MemuAI window: the very same agents as in MemuCode CLI.

An agent is a Markdown file: front matter (``name``, ``description``, ``tools``, ``model``, ``color``) and the
agent's role prompt as the body. The built-in ones come from the CLI (``cli.project.BUILTIN_AGENTS``); personal
agents live in ``%APPDATA%\\MemuAI\\memucode\\agents`` (``cli.paths.user_agents_dir()``), project agents in
``<project>/.memucode/agents``. Agents in the Claude Code layout (``~/.claude/agents``,
``<project>/.claude/agents``) are read as well. CLI and window read the same files in the same order, so an
agent made in one of them works in the other, and a personal or project agent with the name of a built-in one
replaces it.

The CLI names tools its own way (Read, Grep, Edit, Bash…); ``window_tools`` translates a ``tools:`` line into
the tools of the window's agents (``work_agents``).
"""
from __future__ import annotations

import re
from contextlib import suppress
from pathlib import Path
from typing import Any, Dict, List, Optional

from .cli import paths
from .cli.project import BUILTIN_AGENTS, split_front_matter

MAX_PROMPT = 20_000
MAX_DESCRIPTION = 300
COLORS = ("cyan", "magenta", "yellow", "green", "blue", "red")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")

# CLI tool name -> the window's agent tools that do the same job.
TOOL_MAP: Dict[str, tuple] = {
    "read": ("work_read",),
    "grep": ("work_search",),
    "glob": ("work_tree",),
    "ls": ("work_tree",),
    "outline": ("work_outline", "code_outline_deep"),
    "symbols": ("code_symbols",),
    "projectinfo": ("code_project_info",),
    "git": ("code_git", "code_review", "work_diff"),
    "edit": ("work_patch", "work_replace_lines", "work_insert", "work_append"),
    "multiedit": ("work_patch", "work_replace_lines", "work_insert", "work_append"),
    "write": ("work_write",),
    "bash": ("code_run", "code_diagnostics"),
    "bashoutput": (),  # reads a background command's output: nothing to run in the window
    "websearch": ("web_search",),
    "webfetch": ("web_fetch",),
}

# The groups the window's editor offers, in CLI names (the file stays readable by the CLI).
GROUPS: Dict[str, List[str]] = {
    "read": ["Read", "Grep", "Glob", "LS", "Outline", "Symbols", "ProjectInfo", "Git"],
    "edit": ["Edit", "MultiEdit", "Write"],
    "run": ["Bash", "BashOutput"],
    "web": ["WebSearch", "WebFetch"],
}
_GROUPED = {name.lower() for names in GROUPS.values() for name in names}

def normalize_name(value: str) -> str:
    """The CLI's rule (``load_agents``): lower case, anything but letters, digits, ``_`` and ``-`` becomes ``-``."""
    return re.sub(r"[^\w-]+", "-", str(value or "").strip().lower()).strip("-")[:48]

def _split_tools(text: str) -> Optional[List[str]]:
    names = [item.strip() for item in re.split(r"[,\s]+", str(text or "")) if item.strip()]
    return names or None

def window_tools(names: Optional[List[str]]) -> Optional[List[str]]:
    """The window's tools for a CLI ``tools:`` list (``None`` = every tool the access mode allows).

    The window's own names (``work_read``…) are accepted as they are. An explicit
    list of unmapped tools grants no window tools."""
    if names is None:
        return None
    out: List[str] = []
    for name in names:
        raw = str(name or "").strip()
        key = raw.lower()
        mapped = TOOL_MAP.get(key) or ((key,) if re.match(r"^(work|code|web)_[a-z_]+$", key) else ())
        for item in mapped:
            if item not in out:
                out.append(item)
    return out

def groups_of(names: Optional[List[str]]) -> Dict[str, Any]:
    """Which editor groups a ``tools:`` list covers; ``other`` keeps the names the editor has no box for."""
    if not names:
        return {"all": True, "groups": sorted(GROUPS), "other": []}
    lowered = {str(name).lower() for name in names}
    # BashOutput only reads what a command printed: on its own it does not make an agent run commands.
    groups = [group for group, members in GROUPS.items()
              if lowered & ({"bash"} if group == "run" else {item.lower() for item in members})]
    return {"all": False, "groups": groups, "other": [name for name in names if str(name).lower() not in _GROUPED]}

def _dirs(root: Optional[Path]) -> List[tuple]:
    """(folder, scope, origin) in the CLI's reading order: a later file replaces an earlier one of the same name."""
    rows = [(paths.user_agents_dir(), "user", "memucode"), (Path.home() / ".claude" / "agents", "user", "claude")]
    if root is not None:
        rows += [(Path(root) / paths.PROJECT_DIR / "agents", "project", "memucode"),
                 (Path(root) / ".claude" / "agents", "project", "claude")]
    return rows

def _builtin_row(name: str) -> Dict[str, Any]:
    agent = BUILTIN_AGENTS[name]
    return {"name": agent.name, "description": agent.description, "prompt": agent.prompt,
            "tools": list(agent.tools) if agent.tools else None, "model": agent.model, "color": agent.color,
            "source": "builtin", "origin": "memucode", "path": "", "editable": False, "builtin": True}

def _read_agent_file(path: Path) -> Optional[tuple[str, Dict[str, str], str]]:
    try:
        meta, body = split_front_matter(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    name = normalize_name(meta.get("name") or path.stem)
    return (name, meta, body) if name else None

def _find_agent_file(folder: Path, name: str) -> Optional[Path]:
    # The last file with this declared name is the one the catalogue shows.
    for path in sorted(folder.glob("*.md"), reverse=True):
        parsed = _read_agent_file(path)
        if parsed is not None and parsed[0] == name:
            return path
    return None

def catalogue(root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Every agent the CLI would see in ``root``, in its order, each once (the overriding file wins)."""
    found: Dict[str, Dict[str, Any]] = {name: _builtin_row(name) for name in BUILTIN_AGENTS}
    for folder, scope, origin in _dirs(root):
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.md")):
            parsed = _read_agent_file(path)
            if parsed is None:
                continue
            name, meta, body = parsed
            found[name] = {
                "name": name, "description": meta.get("description", "")[:MAX_DESCRIPTION], "prompt": body.strip(),
                "tools": _split_tools(meta.get("tools", "")), "model": meta.get("model", "").strip(),
                "color": meta.get("color", "").strip(), "source": scope, "origin": origin, "path": str(path),
                "editable": origin == "memucode", "builtin": False, "overrides": name in BUILTIN_AGENTS,
            }
    rows = list(found.values())
    for row in rows:
        row["tool_groups"] = groups_of(row["tools"])
    return rows

def load(root: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    return {row["name"]: row for row in catalogue(root)}

def _folder(root: Optional[Path], scope: str) -> Path:
    if scope == "project":
        if root is None:
            raise ValueError("A project agent needs an open project.")
        return Path(root) / paths.PROJECT_DIR / "agents"
    return paths.user_agents_dir()

def _clean_line(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]

def render(row: Dict[str, Any]) -> str:
    tools = row.get("tools") or []
    lines = ["---", f"name: {row['name']}", f"description: {_clean_line(row.get('description'), MAX_DESCRIPTION)}",
             "tools: " + ", ".join(tools), f"model: {_clean_line(row.get('model'), 160)}"]
    if row.get("color"):
        lines.append(f"color: {row['color']}")
    lines += ["---", "", str(row.get("prompt") or "").strip(), ""]
    return "\n".join(lines)

def save(root: Optional[Path], data: Dict[str, Any]) -> Dict[str, Any]:
    """Create or change a personal (``scope: user``) or project agent; ``old_name`` renames one."""
    scope = "project" if str(data.get("scope") or "") == "project" else "user"
    name = normalize_name(data.get("name") or "")
    if not NAME_RE.match(name):
        raise ValueError("Name: Latin letters, digits and dashes, up to 48 characters.")
    prompt = str(data.get("prompt") or "").replace("\r\n", "\n").strip()
    if not prompt:
        raise ValueError("Describe what the agent does: its instructions cannot be empty.")
    if len(prompt) > MAX_PROMPT:
        raise ValueError(f"The instructions are longer than {MAX_PROMPT} characters.")
    tools = data.get("tools")
    if tools is not None:
        if not isinstance(tools, list):
            raise ValueError("Tools must be a list of names.")
        tools = [str(item).strip() for item in tools if re.fullmatch(r"[A-Za-z][\w-]{0,40}", str(item).strip())][:40]
        if not tools:
            raise ValueError("The selected tool list contains no valid names.")
    color = str(data.get("color") or "").strip().lower()
    row = {"name": name, "description": data.get("description") or "", "prompt": prompt, "tools": tools,
           "model": str(data.get("model") or "").strip(), "color": color if color in COLORS else ""}
    folder = _folder(root, scope)
    old = normalize_name(data.get("old_name") or "")
    old_scope = "project" if str(data.get("old_scope") or scope) == "project" else "user"
    source = _find_agent_file(_folder(root, old_scope), old) if old else None
    if old and source is None:
        raise ValueError("The agent being edited no longer exists. Reopen the agent list.")
    target = source if source is not None and (name, scope) == (old, old_scope) else folder / f"{name}.md"
    existing = _find_agent_file(folder, name)
    if existing is not None and existing != source:
        raise ValueError(f"An agent named {name} already exists here.")
    folder.mkdir(parents=True, exist_ok=True)
    text = render(row)
    if target == source:
        from .work_tools import _atomic_write
        _atomic_write(target, text)
    else:
        try:
            stream = target.open("x", encoding="utf-8")
        except FileExistsError as exc:
            raise ValueError(f"An agent file named {target.name} already exists here.") from exc
        try:
            with stream:
                stream.write(text)
            if source is not None:
                source.unlink()
        except OSError:
            with suppress(OSError):
                target.unlink()
            raise
    return next((item for item in catalogue(root) if item["name"] == name), {**row, "path": str(target)})

def delete(root: Optional[Path], scope: str, name: str) -> bool:
    name = normalize_name(name)
    if not NAME_RE.match(name):
        raise ValueError("Unknown agent.")
    target = _find_agent_file(_folder(root, "project" if scope == "project" else "user"), name)
    if target is None:
        return False
    target.unlink()
    return True
