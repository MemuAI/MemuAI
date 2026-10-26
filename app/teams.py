"""Profile-local team definitions; model IDs always come from the user's catalog.

Templates describe jobs, never fictitious models. A team is copied into a run so
editing its definition cannot change work that is already in progress.
"""
from __future__ import annotations

import copy
import re
import time
import uuid
from typing import Any

from . import pgdata
from .storage import load_json_with_recovery, mutate_json

MAX_TEAMS = 80
MAX_MEMBERS = 6
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")
DEFAULT_BUDGET = {"max_calls": 1200, "max_parallel": 3, "max_rounds": 300,
                  "context_chars": 48000, "output_tokens": 6000}

PRESETS = [
    {"id": "feature", "name": "Новая функция", "name_en": "New feature",
     "description": "Создание и проверка функции в готовом проекте.",
     "description_en": "Build and verify a feature in an existing project.",
     "members": [
         {"name": "Тимлид", "name_en": "Team lead", "role": "Архитектура, интеграция и итоговая проверка", "role_en": "Architecture, integration and final review", "is_lead": True},
         {"name": "Интерфейс", "name_en": "Frontend", "role": "Интерфейс, взаимодействие и доступность", "role_en": "Frontend, interaction and accessibility"},
         {"name": "Логика", "name_en": "Backend", "role": "Серверная логика, данные и надёжность", "role_en": "Backend, data and reliability"},
         {"name": "Проверка", "name_en": "QA", "role": "Воспроизведение, тесты и проверка требований", "role_en": "QA: reproduce, test and verify acceptance"}]},
    {"id": "research", "name": "Исследование", "name_en": "Research",
     "description": "Проверка источников, сравнение вариантов и ясные выводы.",
     "description_en": "Check sources, compare alternatives and draw clear conclusions.",
     "members": [
         {"name": "Аналитик", "name_en": "Analyst", "role": "План исследования, обобщение и проверка доказательств", "role_en": "Lead: research plan, synthesis and evidence review", "is_lead": True},
         {"name": "Исследователь", "name_en": "Researcher", "role": "Поиск и сравнение первоисточников", "role_en": "Find and compare primary sources"},
         {"name": "Критик", "name_en": "Reviewer", "role": "Независимая проверка фактов и предположений", "role_en": "Independently verify claims and challenge assumptions"}]},
    {"id": "creative", "name": "Творческая студия", "name_en": "Creative studio",
     "description": "От идеи и нескольких подходов до готового результата.",
     "description_en": "Explore directions and develop a finished creative result.",
     "members": [
         {"name": "Редактор", "name_en": "Editor", "role": "Творческое направление, целостность и редактура", "role_en": "Lead: creative direction, cohesion and final editing", "is_lead": True},
         {"name": "Автор", "name_en": "Writer", "role": "Разработка разных концепций и создание материала", "role_en": "Develop distinct concepts and write the deliverable"},
         {"name": "Рецензент", "name_en": "Critic", "role": "Проверка ясности, оригинальности и соответствия задаче", "role_en": "Check clarity, originality and fit to the brief"}]},
]

def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]

def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (ValueError, TypeError, OverflowError):
        return default

def normalize(raw: Any, *, strict: bool = True) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("Команда должна быть объектом / A team must be an object")
    if strict and len(str(raw.get("id") or "").strip()) > 80:
        raise ValueError("ID команды длиннее 80 символов / Team ID exceeds 80 characters")
    team_id = _text(raw.get("id"), 80) or "team_" + uuid.uuid4().hex[:16]
    if not _ID.fullmatch(team_id):
        raise ValueError("Некорректный ID команды / Invalid team ID")
    name = _text(raw.get("name"), 100)
    manager_model = _text(raw.get("manager_model"), 300)
    members = []
    used = {"manager"}
    source_members = raw.get("members")
    if strict and source_members is not None and not isinstance(source_members, list):
        raise ValueError("Участники должны быть списком / Members must be a list")
    if strict and isinstance(source_members, list) and len(source_members) > MAX_MEMBERS:
        raise ValueError(f"В команде не больше {MAX_MEMBERS} участников / At most {MAX_MEMBERS} members are allowed")
    for source in (source_members if isinstance(source_members, list) else [])[:MAX_MEMBERS]:
        if not isinstance(source, dict):
            if strict:
                raise ValueError("Участник должен быть объектом / A member must be an object")
            continue
        if strict and len(str(source.get("id") or "").strip()) > 80:
            raise ValueError("ID участника длиннее 80 символов / Member ID exceeds 80 characters")
        member_id = _text(source.get("id"), 80) or "member_" + uuid.uuid4().hex[:12]
        if not _ID.fullmatch(member_id) or member_id in used:
            raise ValueError("ID участников должны быть уникальны / Member IDs must be unique")
        used.add(member_id)
        member = {"id": member_id, "name": _text(source.get("name"), 80),
                  "model": _text(source.get("model"), 300),
                  "role": _text(source.get("role"), 1000),
                  "instructions": _text(source.get("instructions"), 8000),
                  "is_lead": source.get("is_lead") is True}
        if strict and (not member["model"] or not member["role"]):
            raise ValueError("Для каждого участника нужны модель и роль / Every member needs a model and role")
        member["name"] = member["name"] or member["role"][:60] or "Участник"
        members.append(member)
    if strict and (not name or not manager_model):
        raise ValueError("Нужны название и модель менеджера / Name and manager model are required")
    if sum(member["is_lead"] for member in members) > 1:
        raise ValueError("Назначьте одного тимлида / Select one team lead")
    budget = raw.get("budget") if isinstance(raw.get("budget"), dict) else {}
    return {"id": team_id, "name": name, "description": _text(raw.get("description"), 3000),
            "manager_approvals": raw.get("manager_approvals", True) is True,
            "manager_model": manager_model, "manager_instructions": _text(raw.get("manager_instructions"), 8000),
            "members": members, "budget": {
                "max_calls": _int(budget.get("max_calls"), 1200, 8, 10000),
                "max_parallel": _int(budget.get("max_parallel"), 3, 1, 4),
                "max_rounds": _int(budget.get("max_rounds"), 300, 3, 2000),
                "context_chars": _int(budget.get("context_chars"), 48000, 16000, 160000),
                "output_tokens": _int(budget.get("output_tokens"), 6000, 1000, 16000)},
            "created": _int(raw.get("created"), int(time.time() * 1000), 0, 10**15),
            "updated": int(time.time() * 1000) if strict else _int(raw.get("updated"), 0, 0, 10**15)}

def _path(profile_id: str = ""):
    return pgdata.MEMU_DIR / pgdata._profile_data_name("teams.json", _active_profile_id(profile_id))

def load(profile_id: str = "") -> list[dict]:
    rows = load_json_with_recovery(_path(profile_id), [], validator=lambda value: isinstance(value, list))
    result = []
    for row in rows[:MAX_TEAMS]:
        try:
            result.append(normalize(row, strict=False))
        except ValueError:
            continue
    return result

def save(raw: dict, profile_id: str) -> dict:
    team = normalize(raw)
    def update(rows):
        clean = [row for row in (rows if isinstance(rows, list) else []) if isinstance(row, dict)]
        for idx, row in enumerate(clean):
            if row.get("id") == team["id"]:
                clean[idx] = team
                return True, clean
        if len(clean) >= MAX_TEAMS:
            raise ValueError("Достигнут лимит команд / Team limit reached")
        return True, clean + [team]
    mutate_json(_path(profile_id), [], update, validator=lambda value: isinstance(value, list), repair_invalid=True)
    return copy.deepcopy(team)

def delete(team_id: str, profile_id: str) -> None:
    def update(rows):
        rows = rows if isinstance(rows, list) else []
        return True, [row for row in rows if isinstance(row, dict) and row.get("id") != team_id]
    mutate_json(_path(profile_id), [], update, validator=lambda value: isinstance(value, list), repair_invalid=True)

def presets() -> list[dict]:
    return copy.deepcopy(PRESETS)

def _active_profile_id(profile_id: str = "") -> str:
    if profile_id:
        return str(profile_id)
    try:
        return str(pgdata.load_profiles().get("active_id") or "default")
    except Exception:
        return "default"

def list_teams(profile_id: str = "") -> list[dict]:
    return load(_active_profile_id(profile_id))

def get_team(team_id: str, profile_id: str = "") -> dict | None:
    target = str(team_id or "").strip()
    if not target:
        return None
    for row in list_teams(profile_id):
        if str(row.get("id") or "") == target or str(row.get("name") or "").lower() == target.lower():
            return row
    return None

def _runs_path(profile_id: str = ""):
    return pgdata.MEMU_DIR / pgdata._profile_data_name("team-runs.json", _active_profile_id(profile_id))

def save_run(snapshot: dict, profile_id: str = "") -> None:
    if not isinstance(snapshot, dict) or not snapshot.get("id"):
        return
    path = _runs_path(profile_id)
    def apply(raw):
        rows = raw if isinstance(raw, list) else []
        rows = [row for row in rows if isinstance(row, dict) and row.get("id") != snapshot["id"]]
        return True, (rows + [snapshot])[-40:]
    mutate_json(path, [], apply, backup=False, validator=lambda value: isinstance(value, list), repair_invalid=True)
