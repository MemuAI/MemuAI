"""MemuCode skills: built-in plus user/project markdown playbooks.

Project skills in ``<project>/.memuai/skills/<id>/SKILL.md`` override the
built-in catalogue; personal skills live in ``memuai/code_skills``.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

from . import code_skill_texts as _TEXTS
from .config import DATA_DIR

PERSONAL_ROOT = Path(DATA_DIR) / "memuai" / "code_skills"
PROJECT_DIR = ".memuai/skills"
MAX_SKILL_PROMPT = 20_000

#: The built-in playbooks live in ``code_skill_texts`` (Round 21: real step-by-step playbooks instead of one-liners;
#: new: write-tests, explore, pr, data, office-files, windows-shell). Ids of the older set are all kept.
BUILTIN_SKILLS: List[Dict[str, str]] = [dict(item) for item in _TEXTS.SKILLS]
_BUILTIN_PROMPTS = dict(_TEXTS.PROMPTS)


def _parse_markdown(path: Path) -> Dict[str, str] | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # Round 32: the shared Agent Skills parser (block scalars «description: >», quotes, metadata) instead of a
    # line regex that read a folded description as «>».
    from .skills_catalog import parse_front_matter
    meta, text = parse_front_matter(text)
    title = str(meta.get("title") or meta.get("name") or "").strip()
    description = str(meta.get("description") or "").strip()
    title = title or path.parent.name.replace("_", " ").title()
    prompt = text.strip()[:MAX_SKILL_PROMPT]
    if not prompt:
        return None
    return {"id": path.parent.name.lower(), "title": title[:120],
            "description": description[:240], "prompt": prompt, "source": str(path), "path": str(path)}


def _from_dir(root: Path, source: str) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    if not root.is_dir():
        return rows
    for folder in sorted([item for item in root.iterdir() if item.is_dir()]):
        skill = _parse_markdown(folder / "SKILL.md")
        if skill:
            skill["source"] = source
            rows.append(skill)
    return rows


def _cli_personal_root() -> Path:
    import os
    override = os.environ.get("MEMUCODE_CONFIG_DIR", "").strip()
    return (Path(override) if override else Path(DATA_DIR) / "memucode") / "skills"


def catalogue(root: str | Path = "", *, include_prompt: bool = False) -> List[Dict[str, Any]]:
    """Project skills win over personal skills, which win over built-ins."""
    project = _from_dir(Path(root).resolve() / PROJECT_DIR, "project") if root else []
    personal = _from_dir(PERSONAL_ROOT, "personal")
    # Round 32: skills installed from the skill catalog (and made in MemuCode CLI) live in the CLI's personal
    # folder; the MemuCode window lists them too. The window's own folder wins on an id clash.
    seen = {row["id"] for row in personal}
    personal += [row for row in _from_dir(_cli_personal_root(), "personal") if row["id"] not in seen]
    project_ids = {row["id"] for row in project}
    personal_ids = {row["id"] for row in personal}
    builtin: List[Dict[str, Any]] = []
    for item in BUILTIN_SKILLS:
        if item["id"] in project_ids or item["id"] in personal_ids:
            continue
        row: Dict[str, Any] = {**item, "source": "builtin", "enabled": True}
        if include_prompt:
            row["prompt"] = _BUILTIN_PROMPTS.get(item["id"], item["description"])
        builtin.append(row)
    personal = [row for row in personal if row["id"] not in project_ids]
    ordered = [*project, *personal, *builtin]
    if include_prompt:
        return ordered
    return [{key: value for key, value in row.items() if key != "prompt"} for row in ordered]


def prompt_for(root: str | Path, skill_id: str) -> str:
    wanted = str(skill_id or "").strip().lower()
    for row in catalogue(root, include_prompt=True):
        if row["id"] == wanted:
            return str(row.get("prompt") or row.get("description") or "")
    return ""


def match_skills(query: str, limit: int = 3) -> List[str]:
    query = str(query or "").lower()
    scored = []
    for row in BUILTIN_SKILLS:
        haystack = f"{row['id']} {row['title']} {row['description']}".lower()
        tokens = [token for token in re.findall(r"[a-zа-яё]{3,}", query) if token in haystack]
        if tokens:
            scored.append((len(set(tokens)), row["id"]))
    scored.sort(reverse=True)
    return [skill for _, skill in scored[:max(1, limit)]]
