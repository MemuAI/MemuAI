"""Everything MemuCode reads from the project and the user's folder:
memory files, custom slash commands, sub-agent definitions, skills and output
styles. Files in the Claude Code layout (``CLAUDE.md``, ``.claude/commands``,
``.claude/agents``, ``.claude/skills``) are understood too, so a repository
prepared for another agent works without changes.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import code_skills
from . import paths
from .i18n import tr

MAX_MEMORY_FILE = 40_000
MAX_MEMORY_TOTAL = 120_000


# ---------------------------------------------------------------- front matter

def split_front_matter(text: str) -> Tuple[Dict[str, str], str]:
    match = re.match(r"^﻿?---\s*\r?\n(.*?)\r?\n---\s*\r?\n?", text, re.S)
    if not match:
        return {}, text
    meta: Dict[str, str] = {}
    for line in match.group(1).splitlines():
        found = re.match(r"^([A-Za-z][\w-]*)\s*:\s*(.*)$", line.strip())
        if found:
            meta[found.group(1).lower()] = found.group(2).strip().strip('"').strip("'")
    return meta, text[match.end():]


# ---------------------------------------------------------------- memory files

@dataclass
class MemoryFile:
    path: Path
    scope: str                      # user | project | parent | local
    text: str
    imports: List[str] = field(default_factory=list)


_IMPORT = re.compile(r"(?<![\w`/])@((?:~[\\/]|\.{1,2}[\\/])?[\w.\\/-]+\.(?:md|txt|json|ya?ml|toml|rst))")


def _read_memory(path: Path, limit: int) -> str:
    with path.open(encoding="utf-8", errors="replace") as handle:
        return handle.read(max(0, limit))


def _resolve_imports(text: str, base: Path, depth: int, seen: set, *,
                     allowed_root: Optional[Path] = None,
                     budget: Optional[List[int]] = None) -> Tuple[str, List[str]]:
    if depth > 4:
        return text, []
    if budget is None:
        budget = [max(0, MAX_MEMORY_TOTAL - len(text))]
    imported: List[str] = []
    blocks: List[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
        if in_fence:
            continue
        for match in _IMPORT.finditer(line):
            target = Path(os.path.expanduser(match.group(1)))
            target = target if target.is_absolute() else (base / target)
            try:
                target = target.resolve()
                if allowed_root is not None:
                    target.relative_to(allowed_root)
            except (OSError, ValueError):
                continue
            key = os.path.normcase(str(target))
            if key in seen or budget[0] <= 0 or not target.is_file():
                continue
            seen.add(key)
            try:
                body = _read_memory(target, min(MAX_MEMORY_FILE, budget[0]))
            except OSError:
                continue
            budget[0] -= len(body)
            body, nested = _resolve_imports(body, target.parent, depth + 1, seen,
                                          allowed_root=allowed_root, budget=budget)
            imported.append(str(target))
            imported.extend(nested)
            blocks.append(f'<import path="{match.group(1)}">\n{body.strip()}\n</import>')
    if blocks:
        text = text.rstrip() + "\n\n" + "\n\n".join(blocks)
    return text, imported


def memory_files(root: Path, names: List[str], *, trusted: bool = True) -> List[MemoryFile]:
    rows: List[MemoryFile] = []
    seen_paths: set = set()
    seen_text: set = set()
    root = Path(root).resolve()
    budget = [MAX_MEMORY_TOTAL]
    # Project-configured names must not turn ancestor discovery into arbitrary
    # file reads. Only paths relative to each instruction-file directory apply.
    names = [name for name in names if isinstance(name, str) and name.strip()
             and not Path(name).is_absolute() and not Path(name).drive
             and ".." not in Path(name).parts]

    def add(path: Path, scope: str, *, boundary: Optional[Path] = None) -> None:
        try:
            resolved = path.resolve()
            if scope in ("project", "local"):
                resolved.relative_to(root)
            elif scope == "parent":
                resolved.relative_to(boundary.resolve() if boundary is not None else root)
        except (OSError, ValueError):
            return
        key = os.path.normcase(str(resolved))
        if key in seen_paths or budget[0] <= 0 or not resolved.is_file():
            return
        seen_paths.add(key)
        try:
            text = _read_memory(resolved, min(MAX_MEMORY_FILE, budget[0]))
        except OSError:
            return
        digest = text.strip()
        if not digest or digest in seen_text:
            return
        seen_text.add(digest)
        budget[0] -= len(text)
        text, imports = _resolve_imports(text, resolved.parent, 1, set(seen_paths),
                                        allowed_root=None if scope == "user" else root,
                                        budget=budget)
        rows.append(MemoryFile(resolved, scope, text, imports))

    add(paths.user_memory_file(), "user")
    if not trusted:
        return rows
    home = Path.home().resolve()
    ancestors: List[Path] = []
    current = root.parent
    for _ in range(6):
        if current == current.parent or current == home or len(current.parts) <= 1:
            break
        ancestors.append(current)
        current = current.parent
    for folder in reversed(ancestors):
        for name in names:
            if name in ("MEMUCODE.md", "AGENTS.md", "CLAUDE.md"):
                add(folder / name, "parent", boundary=folder)
    for name in names:
        add(root / name, "project")
        add(root / paths.PROJECT_DIR / name, "project")
        add(root / ".claude" / name, "project")
    for name in ("MEMUCODE.local.md", "CLAUDE.local.md"):
        add(root / name, "local")
    total = 0
    kept: List[MemoryFile] = []
    for row in rows:
        available = MAX_MEMORY_TOTAL - total
        if available <= 0:
            break
        if len(row.text) > available:
            row = MemoryFile(row.path, row.scope, row.text[:available], row.imports)
        total += len(row.text)
        kept.append(row)
    return kept


def memory_target(root: Path, scope: str) -> Path:
    if scope == "user":
        return paths.user_memory_file()
    if scope == "local":
        return Path(root) / "MEMUCODE.local.md"
    for name in ("MEMUCODE.md", "AGENTS.md", "CLAUDE.md"):
        if (Path(root) / name).is_file():
            return Path(root) / name
    return Path(root) / "MEMUCODE.md"


def add_memory(root: Path, text: str, scope: str = "project") -> Path:
    """``# always use pnpm`` → a bullet in the chosen memory file."""
    target = memory_target(root, scope)
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = target.read_text(encoding="utf-8") if target.is_file() else ""
    line = "- " + " ".join(text.strip().split())
    if not existing:
        header = "# MEMUCODE.md\n\n" + tr("Заметки для MemuCode об этом проекте.\n\n",
                                          "Notes for MemuCode about this project.\n\n") if scope != "user" else \
            "# " + tr("Мои правила для MemuCode", "My rules for MemuCode") + "\n\n"
        existing = header
    if not existing.endswith("\n"):
        existing += "\n"
    target.write_text(existing + line + "\n", encoding="utf-8")
    if scope == "local":
        from .settings import _ensure_gitignored
        _ensure_gitignored(Path(root))
    return target


# ---------------------------------------------------------------- custom commands

@dataclass
class CustomCommand:
    name: str
    description: str
    body: str
    source: str
    path: Path
    argument_hint: str = ""
    model: str = ""
    allowed_tools: List[str] = field(default_factory=list)


def _command_dirs(root: Path, trusted: bool) -> List[Tuple[Path, str]]:
    dirs = [(paths.user_commands_dir(), "user"), (Path.home() / ".claude" / "commands", "user")]
    if trusted:
        dirs += [(Path(root) / paths.PROJECT_DIR / "commands", "project"),
                 (Path(root) / ".claude" / "commands", "project")]
    return dirs


def load_commands(root: Path, trusted: bool) -> Dict[str, CustomCommand]:
    rows: Dict[str, CustomCommand] = {}
    for folder, source in _command_dirs(root, trusted):
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*.md")):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            meta, body = split_front_matter(text)
            rel = path.relative_to(folder).with_suffix("").as_posix()
            name = rel.replace("/", ":").lower()
            name = re.sub(r"[^\w:.-]+", "-", name)
            if not name:
                continue
            description = meta.get("description") or next(
                (line.strip("# ").strip() for line in body.splitlines() if line.strip()), "")[:120]
            tools = [item.strip() for item in re.split(r"[,\s]+", meta.get("allowed-tools", "")) if item.strip()]
            rows[name] = CustomCommand(name=name, description=description[:160], body=body.strip(), source=source,
                                       path=path, argument_hint=meta.get("argument-hint", ""),
                                       model=meta.get("model", ""), allowed_tools=tools)
    return rows


def expand_command(command: CustomCommand, args: str) -> str:
    text = command.body
    words = args.split()
    text = text.replace("$ARGUMENTS", args).replace("${ARGUMENTS}", args)
    for index in range(1, 10):
        value = words[index - 1] if index <= len(words) else ""
        text = text.replace(f"${index}", value)
    if "$ARGUMENTS" not in command.body and args and args not in text:
        text = text.rstrip() + "\n\n" + args
    return text


# ---------------------------------------------------------------- agents

@dataclass
class AgentDef:
    name: str
    description: str
    prompt: str
    tools: Optional[List[str]] = None       # None = every tool
    model: str = ""                         # "" = main model; "small" = fast model
    source: str = "builtin"
    color: str = ""


READ_ONLY_TOOLS = ["Read", "Glob", "Grep", "LS", "Outline", "Symbols", "ProjectInfo", "Git", "WebSearch",
                   "WebFetch", "Skill", "BashOutput"]

BUILTIN_AGENTS: Dict[str, AgentDef] = {
    "general-purpose": AgentDef(
        "general-purpose", "General agent for multi-step research and code changes",
        "You are a MemuCode sub-agent. Complete the delegated task autonomously with the tools you have. "
        "Be thorough but efficient. When done, reply with a concise report: what you found or changed, "
        "exact file paths with line numbers, and anything left unresolved. The report is read by another "
        "agent, not by the user."),
    "explore": AgentDef(
        "explore", "Fast read-only search across the codebase (where is X, how does Y work)",
        "You are a read-only code explorer. Locate the code relevant to the question quickly: search with "
        "Grep and Glob first, then read only the needed ranges. Never modify anything. Reply with the "
        "answer and precise references (path:line) — no filler.",
        tools=READ_ONLY_TOOLS, model="small", color="cyan"),
    "plan": AgentDef(
        "plan", "Architect: studies the code and proposes an implementation plan",
        "You are a software architect. Study the relevant code (read-only), then return a concrete, "
        "ordered implementation plan: files to change, the change in each, risks, and how to verify. "
        "Do not modify anything.", tools=READ_ONLY_TOOLS, color="magenta"),
    "reviewer": AgentDef(
        "reviewer", "Independent code reviewer of the current changes",
        "You are a strict code reviewer. Inspect the current diff (Git diff) and the surrounding code. "
        "Report real problems only — correctness, security, missed edge cases, broken contracts — ordered "
        "by severity with path:line and a suggested fix. Say clearly when the change looks correct.",
        tools=READ_ONLY_TOOLS + ["Bash"], color="yellow"),
    "tester": AgentDef(
        "tester", "Runs the project's tests and diagnoses failures",
        "You run and analyse tests. Find the project's test command, run it, and for each failure explain "
        "the cause with evidence (path:line, assertion, stack). Do not change code unless the delegated "
        "task explicitly says so.", tools=READ_ONLY_TOOLS + ["Bash"], color="green"),
}


def load_agents(root: Path, trusted: bool) -> Dict[str, AgentDef]:
    agents = dict(BUILTIN_AGENTS)
    dirs = [(paths.user_agents_dir(), "user"), (Path.home() / ".claude" / "agents", "user")]
    if trusted:
        dirs += [(Path(root) / paths.PROJECT_DIR / "agents", "project"), (Path(root) / ".claude" / "agents", "project")]
    for folder, source in dirs:
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.md")):
            try:
                meta, body = split_front_matter(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            name = re.sub(r"[^\w-]+", "-", (meta.get("name") or path.stem).strip().lower())
            tools_text = meta.get("tools", "")
            tools = [item.strip() for item in re.split(r"[,\s]+", tools_text) if item.strip()] or None
            agents[name] = AgentDef(name=name, description=meta.get("description", "")[:300], prompt=body.strip(),
                                    tools=tools, model=meta.get("model", ""), source=source,
                                    color=meta.get("color", ""))
    return agents


AGENT_TEMPLATE = """---
name: {name}
description: {description}
tools: Read, Glob, Grep, LS, Bash
model:
---

You are a specialised sub-agent. Describe here what this agent does, how it works
and what it must report back when it finishes.
"""


# ---------------------------------------------------------------- skills

class Skills:
    """Built-in MemuCode skills plus project and personal SKILL.md playbooks.

    Every row says where it came from (``source``: project | personal | builtin), the file it was read from
    (``path``, empty for a built-in) and whether the user switched it off (``enabled``). Disabled skills stay
    in the list for the manager but are not offered to the model, nor run by name."""

    def __init__(self, root: Path, trusted: bool, settings: Any = None) -> None:
        self.root = Path(root)
        self.trusted = trusted
        self.settings = settings
        self._cache: Optional[List[Dict[str, Any]]] = None

    def _extra(self) -> List[Dict[str, Any]]:
        dirs = [(paths.user_skills_dir(), "personal"), (Path.home() / ".claude" / "skills", "personal")]
        if self.trusted:
            dirs += [(self.root / paths.PROJECT_DIR / "skills", "project"), (self.root / ".claude" / "skills", "project")]
        rows = []
        for folder, source in dirs:
            if not folder.is_dir():
                continue
            for skill_dir in sorted(item for item in folder.iterdir() if item.is_dir()):
                parsed = code_skills._parse_markdown(skill_dir / "SKILL.md")
                if parsed:
                    parsed["source"] = source
                    rows.append(parsed)
        return rows

    def disabled(self) -> List[str]:
        raw = self.settings.get("skills.disabled") if self.settings is not None else None
        return [str(item).lower() for item in raw] if isinstance(raw, list) else []

    def catalogue(self, *, refresh: bool = False, include_disabled: bool = False) -> List[Dict[str, Any]]:
        if self._cache is None or refresh:
            rows: Dict[str, Dict[str, Any]] = {}
            base = code_skills.catalogue(self.root if self.trusted else "", include_prompt=True)
            for row in base:
                rows[row["id"]] = dict(row)
            if guide_row := self._guide_row():         # Round 33: the CLI's own built-in skill (selfdoc.py)
                rows.setdefault(guide_row["id"], guide_row)
            for row in self._extra():
                rows[row["id"]] = dict(row)             # project/personal override built-ins
            self._cache = sorted(rows.values(), key=lambda row: ({"project": 0, "personal": 1}.get(row.get("source"), 2),
                                                                 row["id"]))
        off = set(self.disabled())
        out = []
        for row in self._cache:
            enabled = row["id"] not in off
            if enabled or include_disabled:
                out.append({**row, "enabled": enabled, "builtin": row.get("source") == "builtin",
                            "path": row.get("path") or ""})
        return out

    @staticmethod
    def _guide_row() -> Optional[Dict[str, Any]]:
        try:
            from .selfdoc import skill_row
            return skill_row()
        except Exception:
            return None

    def get(self, name: str, *, include_disabled: bool = False) -> Optional[Dict[str, Any]]:
        wanted = str(name or "").strip().lower().lstrip("/#")
        for row in self.catalogue(include_disabled=include_disabled):
            if row["id"] == wanted:
                prompt = row.get("prompt") or ""
                if not prompt and row.get("source") == "builtin" and wanted == "memucode-guide":
                    try:                                # Round 33: generated from the real registry when loaded
                        from .selfdoc import build_guide
                        prompt = build_guide()
                    except Exception:
                        prompt = ""
                return {**row, "prompt": prompt or row.get("description") or ""}
        return None


SKILL_TEMPLATE = """---
name: {name}
description: {description}
---

# {title}

Write the playbook here: when to use it, the exact steps, the checks that prove
the work is done and the format of the final report.
"""


# ---------------------------------------------------------------- output styles

BUILTIN_STYLES: Dict[str, Tuple[str, str]] = {
    "default": ("Default", ""),
    "explanatory": ("Explanatory", (
        "OUTPUT STYLE — EXPLANATORY: while completing the task, add brief educational \"Insight\" notes "
        "(2-3 bullet points, marked with ★ Insight) explaining why the codebase is built this way and why "
        "you chose an approach. Keep them specific to this code, not generic.")),
    "learning": ("Learning", (
        "OUTPUT STYLE — LEARNING: the user wants to learn by doing. For meaningful design decisions or "
        "small functions (2-10 lines), stop and ask the user to write that piece themselves: add a "
        "TODO(human) marker in the code, explain what it should do and why, then wait. Explain concepts "
        "briefly as you go.")),
    "concise": ("Concise", (
        "OUTPUT STYLE — CONCISE: answer in as few words as possible. No preamble, no summaries of what you "
        "did unless asked, lists over prose, code over explanation.")),
}


def load_styles() -> Dict[str, Tuple[str, str]]:
    styles = dict(BUILTIN_STYLES)
    folder = paths.output_styles_dir()
    if folder.is_dir():
        for path in sorted(folder.glob("*.md")):
            try:
                meta, body = split_front_matter(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            key = re.sub(r"[^\w-]+", "-", (meta.get("name") or path.stem).lower())
            styles[key] = (meta.get("name") or path.stem, body.strip())
    return styles
