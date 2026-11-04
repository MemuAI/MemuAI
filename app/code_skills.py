"""MemuCode skills: built-in plus user/project markdown playbooks plus the shared MemuAI skill library.

Project skills in ``<project>/.memuai/skills/<id>/SKILL.md`` override the
built-in catalogue; personal skills live in ``memuai/code_skills``. The skills of the
MemuAI window (Skills) that work in MemuCode come last with ``source: "library"``
(app/skills_scope.py; the place is ``code`` for the window, ``cli`` for MemuCode CLI).
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

BUILTIN_SKILLS: List[Dict[str, str]] = [dict(item) for item in _TEXTS.SKILLS]
_BUILTIN_PROMPTS = dict(_TEXTS.PROMPTS)

def _parse_markdown(path: Path) -> Dict[str, str] | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

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

def _library_rows(place: str, include_prompt: bool, taken: set) -> List[Dict[str, Any]]:
    """Skills of the shared MemuAI library switched on for ``place`` (a broken library = none)."""
    try:
        from . import skills_scope
        rows = skills_scope.for_place(place) if place else []
    except Exception:  # noqa: BLE001
        return []
    out: List[Dict[str, Any]] = []
    for row in rows:
        skill_id = str(row.get("id") or "").strip().lower()
        if not skill_id or skill_id in taken:
            continue
        own = row.get("custom") or row.get("customized")
        about = " ".join(str((not own and row.get("cue")) or row.get("desc") or row.get("when") or "").split())
        item: Dict[str, Any] = {"id": skill_id, "title": str(row.get("name") or skill_id)[:120],
                                "title_ru": str((not own and row.get("name_ru")) or row.get("name") or skill_id)[:120],
                                "description": about[:240], "source": "library", "enabled": True}
        about_ru = " ".join(str((not own and (row.get("when_ru") or row.get("desc_ru"))) or "").split())
        if about_ru:
            item["description_ru"] = about_ru[:240]
        if include_prompt:
            item["prompt"] = str(row.get("prompt") or "").strip()[:MAX_SKILL_PROMPT]
        out.append(item)
    return out


def catalogue(root: str | Path = "", *, include_prompt: bool = False, place: str = "code") -> List[Dict[str, Any]]:
    """Project skills win over personal skills, which win over built-ins, which win over the shared library.

    ``place`` — where the library rows must work: ``code`` (MemuCode window) or ``cli``; "" leaves them out."""
    project = _from_dir(Path(root).resolve() / PROJECT_DIR, "project") if root else []
    personal = _from_dir(PERSONAL_ROOT, "personal")

    seen = {row["id"] for row in personal}
    personal += [row for row in _from_dir(_cli_personal_root(), "personal") if row["id"] not in seen]
    project_ids = {row["id"] for row in project}
    personal_ids = {row["id"] for row in personal}
    builtin: List[Dict[str, Any]] = []
    for item in BUILTIN_SKILLS:
        if item["id"] in project_ids or item["id"] in personal_ids:
            continue
        row: Dict[str, Any] = {**item, "source": "builtin", "enabled": True}
        if item["id"] in _TEXTS.RU:
            row["title_ru"], row["description_ru"] = _TEXTS.RU[item["id"]]
        if include_prompt:
            row["prompt"] = _BUILTIN_PROMPTS.get(item["id"], item["description"])
        builtin.append(row)
    personal = [row for row in personal if row["id"] not in project_ids]
    taken = project_ids | personal_ids | {row["id"] for row in builtin}
    ordered = [*project, *personal, *builtin, *_library_rows(place, include_prompt, taken)]
    if include_prompt:
        return ordered
    return [{key: value for key, value in row.items() if key != "prompt"} for row in ordered]

def window_catalogue(root: str | Path = "", *, include_prompt: bool = False) -> List[Dict[str, Any]]:
    """The skills of the MemuCode window: the very list MemuCode CLI builds in this project (``cli.project.Skills``).

    The catalogue above, then the folders the CLI reads on top of it — its personal folder, Claude Code's
    ``~/.claude/skills``, the project's ``.memucode/skills`` and ``.claude/skills`` — a later folder replacing a
    skill of the same id. Project skills come first, then personal ones, built-ins, the library."""
    rows = {row["id"]: row for row in catalogue(root, include_prompt=True, place="code")}
    folders = [(_cli_personal_root(), "personal"), (Path.home() / ".claude" / "skills", "personal")]
    if root:
        base = Path(root).resolve()
        folders += [(base / ".memucode" / "skills", "project"), (base / ".claude" / "skills", "project")]
    for folder, source in folders:
        for row in _from_dir(folder, source):
            rows[row["id"]] = row
    rank = {"project": 0, "personal": 1, "builtin": 2}
    ordered = sorted(rows.values(), key=lambda row: rank.get(str(row.get("source")), 3))
    if include_prompt:
        return ordered
    return [{key: value for key, value in row.items() if key != "prompt"} for row in ordered]

def prompt_for(root: str | Path, skill_id: str, place: str = "code") -> str:
    wanted = str(skill_id or "").strip().lower()
    rows = window_catalogue(root, include_prompt=True) if place == "code" else catalogue(root, include_prompt=True, place=place)
    for row in rows:
        if row["id"] == wanted:
            return str(row.get("prompt") or row.get("description") or "")
    return ""

def match_skills(query: str, limit: int = 3) -> List[str]:
    query = str(query or "").lower()
    scored = []
    for row in BUILTIN_SKILLS:
        haystack = f"{row['id']} {row['title']} {row['description']} {' '.join(_TEXTS.RU.get(row['id'], ()))}".lower()
        tokens = [token for token in re.findall(r"[a-zа-яё]{3,}", query) if token in haystack]
        if tokens:
            scored.append((len(set(tokens)), row["id"]))
    scored.sort(reverse=True)
    return [skill for _, skill in scored[:max(1, limit)]]
