"""Managing skills: create, edit, delete (into a trash), disable, restore.

A skill is a folder with a ``SKILL.md`` — a playbook the agent loads when a task matches::

    ---
    name: release-notes
    description: Writes release notes from the git log. Use when the user asks for a changelog or release notes.
    ---

    # Release notes
    1. Read the git log since the last tag …

Where skills live (the user and the agent see the same list):

* **project** — ``<project>/.memucode/skills/<id>/SKILL.md`` (shared with the repository; works in trusted folders);
* **personal** — ``%APPDATA%\\MemuAI\\memucode\\skills\\<id>\\SKILL.md`` (every project);
* **built-in** — compiled into MemuCode. They cannot be deleted, only *disabled* (``skills.disabled`` in the
  settings) or replaced by a skill with the same id.

Deleting never destroys: the folder moves to ``memucode\\trash\\skills`` and can be restored.
"""
from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .. import code_skills
from . import paths
from .i18n import tr

MAX_ID = 48
MAX_TEXT = code_skills.MAX_SKILL_PROMPT + 2000
SCOPES = ("project", "personal")

def slugify(name: str) -> str:
    """``"Release Notes!"`` → ``"release-notes"``. Letters of any alphabet are kept (a Russian name stays Russian)."""
    text = re.sub(r"[^\w-]+", "-", str(name or "").strip().lower(), flags=re.UNICODE)
    text = re.sub(r"[-_]{2,}", "-", text).strip("-_")
    return text[:MAX_ID].strip("-_")

def name_problem(slug: str) -> str:
    if not slug:
        return tr("Имя пустое — нужны буквы или цифры", "The name is empty — use letters or digits")
    if slug in ("con", "prn", "aux", "nul") or re.fullmatch(r"(com|lpt)\d", slug):
        return tr("Это имя зарезервировано Windows", "Windows reserves that name")
    return ""

def roots(project_root: Path) -> Dict[str, Path]:
    """The folder each scope keeps its skills in."""
    return {"project": Path(project_root) / paths.PROJECT_DIR / "skills", "personal": paths.user_skills_dir()}

def skill_dir(project_root: Path, scope: str, slug: str) -> Path:
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {', '.join(SCOPES)}")
    return roots(project_root)[scope] / slug

def known_roots(project_root: Path) -> List[Path]:
    """Every folder a skill may be read from — and so every place a delete is allowed to touch."""
    base = Path(project_root)
    found = [roots(project_root)["project"], roots(project_root)["personal"], base / ".claude" / "skills",
             base / ".memuai" / "skills", Path.home() / ".claude" / "skills", Path(code_skills.PERSONAL_ROOT)]
    return [path.resolve() for path in found]

def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False

def scope_label(source: str) -> str:
    return {"project": tr("проект", "project"), "personal": tr("личный", "personal"),
            "builtin": tr("встроенный", "built-in")}.get(source, source)

def parse(text: str) -> Tuple[Dict[str, str], str]:
    from .project import split_front_matter
    return split_front_matter(text)

TEMPLATE = """---
name: {id}
description: {description}
---

# {title}

Describe here when this skill applies, the exact steps to follow, the checks that prove the work is done and
the format of the final report.
"""

def template(slug: str, description: str = "", title: str = "") -> str:
    return TEMPLATE.format(id=slug, description=description or "What it does and when to use it — one sentence",
                           title=title or slug.replace("-", " ").title())

def check_text(text: str) -> List[str]:
    """What is wrong with a SKILL.md text (empty list: fine)."""
    problems: List[str] = []
    meta, body = parse(text)
    if not meta:
        problems.append(tr("Нет шапки: файл должен начинаться со строк «---», в которых есть name и description",
                           "No header: the file must start with a --- block holding name and description"))
    elif not meta.get("description", "").strip():
        problems.append(tr("В шапке нет description — одно предложение: что умеет скилл и когда его применять",
                           "The header has no description — one sentence: what the skill does and when to use it"))
    if len(body.strip()) < 20:
        problems.append(tr("Нет инструкций после шапки", "There are no instructions after the header"))
    if len(text) > MAX_TEXT:
        problems.append(tr(f"Слишком длинно (больше {MAX_TEXT} символов)", f"Too long (over {MAX_TEXT} characters)"))
    return problems

def normalize(text: str, slug: str, description: str = "") -> str:
    """The text with a header that names the skill ``slug`` (a missing header is made)."""
    text = str(text or "").replace("\r\n", "\n").lstrip("﻿")
    meta, body = parse(text)
    if not meta:
        return TEMPLATE.format(id=slug, description=description or "TODO: when to use this skill", title=slug) \
            .split("\n# ", 1)[0] + "\n" + text.strip() + "\n"
    if meta.get("name", "").strip().lower() != slug:
        header = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.S)
        if header:
            lines = header.group(1).splitlines()
            lines = [line for line in lines if not re.match(r"^\s*name\s*:", line, re.I)]
            text = "---\nname: " + slug + "\n" + "\n".join(lines) + "\n---\n" + text[header.end():]
    return text.rstrip("\n") + "\n"

def write_skill(project_root: Path, scope: str, slug: str, text: str, *, overwrite: bool = False) -> Path:
    problem = name_problem(slug)
    if problem:
        raise ValueError(problem)
    folder = skill_dir(project_root, scope, slug)
    target = folder / "SKILL.md"
    if target.exists() and not overwrite:
        raise FileExistsError(str(target))
    folder.mkdir(parents=True, exist_ok=True)
    target.write_text(normalize(text, slug), encoding="utf-8")
    return target

def trash_dir() -> Path:
    path = paths.home() / "trash" / "skills"
    path.mkdir(parents=True, exist_ok=True)
    return path

def delete_skill(project_root: Path, skill_file: Path) -> Path:
    """Move the skill's folder to the trash. Only folders under a known skills root, holding a SKILL.md."""
    folder = Path(skill_file).resolve().parent
    if not any(_inside(folder, root) and folder != root for root in known_roots(project_root)):
        raise ValueError(tr("Это не папка скилла MemuCode — удалять нельзя", "That is not a MemuCode skill folder"))
    if not (folder / "SKILL.md").is_file():
        raise ValueError(tr("В папке нет SKILL.md", "The folder has no SKILL.md"))
    target = trash_dir() / f"{folder.name}-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.move(str(folder), str(target))
    return target

def list_trash() -> List[Dict[str, Any]]:
    rows = []
    for entry in sorted(trash_dir().iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
        if entry.is_dir() and (entry / "SKILL.md").is_file():
            meta, _body = parse((entry / "SKILL.md").read_text(encoding="utf-8", errors="replace"))
            rows.append({"folder": entry, "id": re.sub(r"-\d{8}-\d{6}$", "", entry.name),
                         "description": meta.get("description", ""), "deleted": entry.stat().st_mtime})
    return rows

def restore_skill(project_root: Path, entry: Dict[str, Any], scope: str) -> Path:
    slug = str(entry["id"])
    folder = skill_dir(project_root, scope, slug)
    if folder.exists():
        raise FileExistsError(str(folder))
    folder.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(entry["folder"]), str(folder))
    return folder / "SKILL.md"

def disabled_ids(settings: Any) -> List[str]:
    raw = settings.get("skills.disabled") if settings is not None else None
    return [str(item).lower() for item in raw] if isinstance(raw, list) else []

def set_disabled(settings: Any, slug: str, disabled: bool) -> None:
    current = disabled_ids(settings)
    if disabled and slug not in current:
        current.append(slug)
    if not disabled:
        current = [item for item in current if item != slug]
    settings.set("skills.disabled", current)

def describe(row: Dict[str, Any], *, disabled: bool = False) -> str:
    """One line: ``project · Writes release notes … (off)``."""
    text = f"{scope_label(str(row.get('source') or ''))} · {row.get('description') or row.get('title') or ''}"
    return text + (tr(" (выключен)", " (off)") if disabled else "")

def location_help(project_root: Path) -> str:
    """Where skills live — for the user and for the system prompt."""
    places = roots(project_root)
    sep = os.sep
    return (f"project skills: {places['project']}{sep}<id>{sep}SKILL.md; "
            f"personal skills: {places['personal']}{sep}<id>{sep}SKILL.md")
