"""Settings of the other tools that MemuCode can take over: MCP servers, permission rules, instructions, skills,
custom commands and agents.

``scan`` only reads (nothing in another tool's folder is changed) and returns :class:`Item` rows; ``apply`` writes
*MemuCode's* side of it, and refuses to overwrite anything that is already there. Some things need no copying at
all — MemuCode reads Claude Code's ``~/.claude/skills``, ``commands`` and ``agents`` on its own — and are listed
as ``native`` so the person sees they are already available.

Secrets (an API key in an MCP server's environment, a bearer token in its headers) are copied, because the server
cannot run without them, but are always masked in what is shown (``summary``).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

SECRETY = re.compile(r"(key|token|secret|password|passwd|auth|bearer|credential)", re.I)
MAX_FILE = 1_000_000
MAX_FILES = 200


@dataclass
class Item:
    kind: str                       # mcp | permission | instructions | skill | command | agent
    source: str                     # claude, codex, gemini, cursor, opencode, grok
    name: str
    summary: str = ""
    data: Any = None
    native: bool = False            # MemuCode already reads it where it is
    origin: str = ""

    @property
    def key(self) -> str:
        return f"{self.source}:{self.kind}:{self.name}"


@dataclass
class ApplyEnv:
    """Where MemuCode's side lives. Production values come from :func:`default_env`; tests pass temporary folders."""
    settings: Any
    project: Path
    skills_dir: Path
    commands_dir: Path
    agents_dir: Path
    memory_file: Path
    load_mcp: Callable[[], Dict[str, Any]]
    save_mcp: Callable[[Dict[str, Any]], None]


def default_env(runtime: Any) -> ApplyEnv:
    from .. import paths
    from ... import mcp_client
    return ApplyEnv(settings=runtime.settings, project=runtime.root, skills_dir=paths.user_skills_dir(),
                    commands_dir=paths.user_commands_dir(), agents_dir=paths.user_agents_dir(),
                    memory_file=paths.user_memory_file(), load_mcp=mcp_client.load_config, save_mcp=mcp_client.save_config)


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", str(name or "").lower()).strip("-.")[:60]


def mask(value: Any) -> str:
    text = str(value)
    return text[:3] + "…" + "*" * 4 if len(text) > 8 else "****"


# ---------------------------------------------------------------- MCP

def mcp_spec(raw: Any) -> Optional[Dict[str, Any]]:
    """The common shape of one MCP server: ``{command, args, env}`` or ``{type: "http", url, headers}``. None when
    the entry is not something MemuCode can run."""
    if not isinstance(raw, dict):
        return None
    if raw.get("disabled") is True or raw.get("enabled") is False:
        return None
    url = str(raw.get("url") or raw.get("httpUrl") or "").strip()
    command = raw.get("command")
    args = raw.get("args")
    if isinstance(command, list) and command:                       # OpenCode: ["npx", "-y", "server"]
        args = [str(item) for item in command[1:]] + [str(item) for item in (args or [])]
        command = command[0]
    env = raw.get("env") if isinstance(raw.get("env"), dict) else raw.get("environment")
    env = {str(key): str(value) for key, value in (env or {}).items()} if isinstance(env, dict) else {}
    if command:
        spec: Dict[str, Any] = {"command": str(command), "args": [str(item) for item in (args or [])]}
        if env:
            spec["env"] = env
        if raw.get("cwd"):
            spec["cwd"] = str(raw["cwd"])
        return spec
    if url.startswith(("http://", "https://")):
        spec = {"type": "http", "url": url}
        headers = raw.get("headers") if isinstance(raw.get("headers"), dict) else {}
        if headers:
            spec["headers"] = {str(key): str(value) for key, value in headers.items()}
        return spec
    return None


def describe_mcp(spec: Dict[str, Any]) -> str:
    if spec.get("type") == "http":
        return "http " + str(spec["url"])
    shown = " ".join([spec["command"], *spec.get("args", [])])
    env = spec.get("env") or {}
    if env:
        shown += "  env: " + ", ".join(f"{key}={mask(value) if SECRETY.search(key) else value}" for key, value in list(env.items())[:4])
    return shown[:200]


def _mcp_items(source: str, servers: Any, origin: str) -> List[Item]:
    items: List[Item] = []
    if isinstance(servers, dict):
        for name, raw in servers.items():
            spec = mcp_spec(raw)
            if spec is not None:
                items.append(Item("mcp", source, str(name), describe_mcp(spec), spec, origin=origin))
    return items


# ---------------------------------------------------------------- readers

def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _jsonc(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    text = re.sub(r"(?m)^\s*//.*$", "", text)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    try:
        return json.loads(text)
    except ValueError:
        return None


def _toml(path: Path) -> Any:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig") if path.stat().st_size <= MAX_FILE else ""
    except OSError:
        return ""


def _skills(source: str, base: Path, native: bool = False) -> List[Item]:
    items: List[Item] = []
    if not base.is_dir():
        return items
    for folder in sorted(base.iterdir()):
        if folder.name.startswith(".") or not (folder / "SKILL.md").is_file():
            continue
        head = _text(folder / "SKILL.md")
        description = re.search(r"(?m)^description:\s*(.+)$", head)
        items.append(Item("skill", source, folder.name, (description.group(1).strip() if description else "")[:140], folder,
                          native=native, origin=str(folder)))
    return items


def _markdown_dir(kind: str, source: str, base: Path, native: bool = False) -> List[Item]:
    items: List[Item] = []
    if not base.is_dir():
        return items
    for file in sorted(base.rglob("*.md")):
        text = _text(file)
        if not text.strip():
            continue
        description = re.search(r"(?m)^description:\s*(.+)$", text)
        name = slug(file.relative_to(base).with_suffix("").as_posix().replace("/", "-"))
        items.append(Item(kind, source, name, (description.group(1).strip() if description else text.strip().splitlines()[0])[:140],
                          text, native=native, origin=str(file)))
    return items


def gemini_command(path: Path, base: Path) -> Optional[Item]:
    """A Gemini CLI custom command (``commands/<group>/<name>.toml``: ``prompt``, ``description``) as a MemuCode one."""
    data = _toml(path)
    if not isinstance(data, dict) or not str(data.get("prompt") or "").strip():
        return None
    prompt = str(data["prompt"]).replace("{{args}}", "$ARGUMENTS")
    body = "---\n" + (f"description: {' '.join(str(data.get('description') or '').split())}\n" if data.get("description") else "") + "---\n" + prompt
    if "!{" in prompt or "@{" in prompt:
        body += "\n\n(Imported from Gemini CLI: `!{…}` shell injections and `@{…}` file injections are not carried over.)\n"
    name = slug(path.relative_to(base).with_suffix("").as_posix().replace("/", "-"))
    return Item("command", "gemini", name, str(data.get("description") or prompt)[:140], body, origin=str(path))


def _instructions(source: str, path: Path) -> List[Item]:
    text = _text(path)
    if not text.strip():
        return []
    return [Item("instructions", source, path.name, f"{len(text.splitlines())} lines from {path}", text, origin=str(path))]


def _claude(project: Path) -> List[Item]:
    home = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
    items: List[Item] = []
    state = _json(Path.home() / ".claude.json") if not os.environ.get("CLAUDE_CONFIG_DIR") else _json(home / ".claude.json")
    if isinstance(state, dict):
        items += _mcp_items("claude", state.get("mcpServers"), "~/.claude.json")
        projects = state.get("projects") if isinstance(state.get("projects"), dict) else {}
        for folder, row in projects.items():
            if isinstance(row, dict) and _same(folder, project):
                items += _mcp_items("claude", row.get("mcpServers"), f"~/.claude.json · {folder}")
    rules: Dict[str, List[str]] = {"allow": [], "deny": [], "ask": []}
    for name in ("settings.json", "settings.local.json"):
        data = _json(home / name)
        permissions = data.get("permissions") if isinstance(data, dict) and isinstance(data.get("permissions"), dict) else {}
        for kind in rules:
            for rule in permissions.get(kind) or []:
                if isinstance(rule, str) and rule.strip() and rule not in rules[kind]:
                    rules[kind].append(rule.strip())
    for kind, values in rules.items():
        for rule in values:
            items.append(Item("permission", "claude", rule, f"{kind}: {rule}", {"kind": kind, "rule": rule}, origin="settings.json"))
    items += _instructions("claude", home / "CLAUDE.md")
    items += _skills("claude", home / "skills", native=True)
    items += _markdown_dir("command", "claude", home / "commands", native=True)
    items += _markdown_dir("agent", "claude", home / "agents", native=True)
    return items


def _same(first: str, second: Any) -> bool:
    return os.path.normcase(os.path.normpath(str(first))) == os.path.normcase(os.path.normpath(str(second)))


def _codex() -> List[Item]:
    home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    items: List[Item] = []
    config = _toml(home / "config.toml")
    if isinstance(config, dict):
        items += _mcp_items("codex", config.get("mcp_servers"), "config.toml")
    items += _instructions("codex", home / "AGENTS.md")
    items += _skills("codex", home / "skills")
    items += _markdown_dir("command", "codex", home / "prompts")
    return items


def _gemini() -> List[Item]:
    home = Path(os.environ.get("GEMINI_HOME") or (Path.home() / ".gemini"))
    items: List[Item] = []
    settings = _json(home / "settings.json")
    if isinstance(settings, dict):
        items += _mcp_items("gemini", settings.get("mcpServers"), "settings.json")
    items += _instructions("gemini", home / "GEMINI.md")
    items += _skills("gemini", home / "skills")
    base = home / "commands"
    if base.is_dir():
        for file in sorted(base.rglob("*.toml")):
            item = gemini_command(file, base)
            if item is not None:
                items.append(item)
    return items


def _cursor() -> List[Item]:
    home = Path(os.environ.get("CURSOR_HOME") or (Path.home() / ".cursor"))
    items: List[Item] = []
    data = _json(home / "mcp.json")
    if isinstance(data, dict):
        items += _mcp_items("cursor", data.get("mcpServers"), "mcp.json")
    items += _markdown_dir("command", "cursor", home / "commands")
    items += _skills("cursor", home / "skills")
    return items


def _opencode() -> List[Item]:
    base = os.environ.get("XDG_CONFIG_HOME")
    home = Path(base) / "opencode" if base else Path.home() / ".config" / "opencode"
    items: List[Item] = []
    for name in ("opencode.json", "opencode.jsonc"):
        data = _jsonc(home / name)
        if isinstance(data, dict):
            items += _mcp_items("opencode", data.get("mcp"), name)
            break
    items += _instructions("opencode", home / "AGENTS.md")
    items += _markdown_dir("command", "opencode", home / "command")
    items += _markdown_dir("agent", "opencode", home / "agent")
    return items


def _grok() -> List[Item]:
    home = Path(os.environ.get("GROK_HOME") or (Path.home() / ".grok"))
    return _skills("grok", home / "skills") + _markdown_dir("command", "grok", home / "commands")


def scan(source: str, project: Path) -> List[Item]:
    """What can be taken from one tool. Never raises: a damaged file simply contributes nothing."""
    readers: Dict[str, Callable[[], List[Item]]] = {
        "claude": lambda: _claude(project), "codex": _codex, "gemini": _gemini, "cursor": _cursor,
        "opencode": _opencode, "grok": _grok}
    try:
        return readers.get(source, lambda: [])()
    except Exception:
        return []


# ---------------------------------------------------------------- writing MemuCode's side

def _copy_tree(source: Path, target: Path) -> int:
    count = 0
    for file in source.rglob("*"):
        if count >= MAX_FILES:
            break
        if not file.is_file() or file.is_symlink() or any(part.startswith(".") for part in file.relative_to(source).parts):
            continue
        try:
            if file.stat().st_size > MAX_FILE:
                continue
        except OSError:
            continue
        destination = target / file.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, destination)
        count += 1
    return count


def apply(item: Item, env: ApplyEnv) -> str:
    """Take one item over. Returns ``"added"`` or ``"skipped: <why>"``; never overwrites and never raises for a
    conflict (an I/O error does raise)."""
    if item.native:
        return "skipped: MemuCode already reads it where it is"
    name = slug(item.name) or "item"
    if item.kind == "mcp":
        spec = item.data
        if spec.get("type") == "http":
            path = env.project / ".mcp.json"
            data = _json(path) if path.is_file() else {}
            data = data if isinstance(data, dict) else {}
            servers = data.setdefault("mcpServers", {})
            if name in servers:
                return "skipped: a server of this name is already in .mcp.json"
            servers[name] = spec
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            return "added"
        config = env.load_mcp()
        servers = config.setdefault("servers", {})
        if name in servers:
            return "skipped: a server of this name is already set up"
        servers[name] = {**spec, "enabled": True, "description": f"Imported from {item.source}"}
        env.save_mcp(config)
        return "added"
    if item.kind == "permission":
        key = f"permissions.{item.data['kind']}"
        current = list(env.settings.get(key) or [])
        if item.data["rule"] in current:
            return "skipped: the rule is already there"
        env.settings.append(key, item.data["rule"], scope="user")
        return "added"
    if item.kind == "instructions":
        marker = f"<!-- imported from {item.source}: {hashlib.sha1(item.data.encode('utf-8')).hexdigest()[:12]} -->"
        existing = _text(env.memory_file) if env.memory_file.is_file() else ""
        if marker in existing:
            return "skipped: already imported"
        env.memory_file.parent.mkdir(parents=True, exist_ok=True)
        block = f"\n\n{marker}\n# Imported from {item.source} ({item.name})\n\n{item.data.strip()}\n"
        with env.memory_file.open("a", encoding="utf-8") as handle:
            handle.write(block if existing else block.lstrip("\n"))
        return "added"
    if item.kind == "skill":
        target = env.skills_dir / name
        if target.exists():
            return "skipped: a skill of this name exists"
        _copy_tree(Path(item.data), target)
        return "added"
    if item.kind in ("command", "agent"):
        folder = env.commands_dir if item.kind == "command" else env.agents_dir
        target = folder / f"{name}.md"
        if target.exists():
            return f"skipped: a {item.kind} of this name exists"
        folder.mkdir(parents=True, exist_ok=True)
        target.write_text(str(item.data), encoding="utf-8")
        return "added"
    return "skipped: unknown kind"
