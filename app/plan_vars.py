# -*- coding: utf-8 -*-
"""Переменные и макросы сценариев планирования, которые живут между запусками.

* ``<данные>/plan_variables.json`` — ``{"global": {...}, "tasks": {task_id: {...}}}``:
  общие переменные (видны всем сценариям, область ``global``) и сохранённые
  переменные задачи (область ``task``: счётчики, последние значения,
  накопленные списки). Сценарий получает их при старте, а планировщик
  записывает изменения после прогона (:func:`commit`).
* ``<данные>/plan_macros.json`` — библиотека макросов: именованные группы
  шагов, которые вызывает шаг ``macro_call``. Шаги проверяет движок
  :mod:`app.plan_scenarios`; рекурсия и глубина там же ограничены.

Значения ограничены по размеру (200 000 символов на переменную, 80
переменных на задачу, 200 общих, 100 макросов) — файл не разрастается.
"""
from __future__ import annotations

import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

MAX_TASK_VARS = 80
MAX_GLOBAL_VARS = 200
MAX_TASKS = 500
MAX_MACROS = 100
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,40}$")


def _dir(root: Optional[Path] = None) -> Path:
    if root is not None:
        return Path(root)
    from .pgdata import MEMU_DIR
    return Path(MEMU_DIR)


def vars_path(root: Optional[Path] = None) -> Path:
    return _dir(root) / "plan_variables.json"


def macros_path(root: Optional[Path] = None) -> Path:
    return _dir(root) / "plan_macros.json"


def _valid_doc(value: Any) -> bool:
    return isinstance(value, dict)


def _clean_value(value: Any) -> Any:
    from .plan_scenarios import ScenarioError, _check_value
    try:
        return _check_value(value)
    except ScenarioError:
        return None


def load(root: Optional[Path] = None) -> Dict[str, Any]:
    from .storage import load_json_with_recovery
    doc = load_json_with_recovery(vars_path(root), {"global": {}, "tasks": {}}, validator=_valid_doc)
    if not isinstance(doc.get("global"), dict):
        doc["global"] = {}
    if not isinstance(doc.get("tasks"), dict):
        doc["tasks"] = {}
    return doc


def global_vars(root: Optional[Path] = None) -> Dict[str, Any]:
    return dict(load(root).get("global") or {})


def task_vars(task_id: str, root: Optional[Path] = None) -> Dict[str, Any]:
    row = (load(root).get("tasks") or {}).get(str(task_id or ""))
    return dict(row.get("vars") or {}) if isinstance(row, dict) else {}


def commit(task_id: str, persist: Dict[str, Any], root: Optional[Path] = None) -> bool:
    """Записать изменения прогона: ``{"task": {...}, "global": {...}, "deleted_task": [...], "deleted_global": [...]}``."""
    if not isinstance(persist, dict):
        return False
    task_updates = {k: v for k, v in (persist.get("task") or {}).items() if _NAME_RE.match(str(k))}
    global_updates = {k: v for k, v in (persist.get("global") or {}).items() if _NAME_RE.match(str(k))}
    task_deleted = [str(k) for k in (persist.get("deleted_task") or [])]
    global_deleted = [str(k) for k in (persist.get("deleted_global") or [])]
    if not (task_updates or global_updates or task_deleted or global_deleted):
        return False
    from .storage import mutate_json
    tid = str(task_id or "")

    def apply(doc: Dict[str, Any]) -> bool:
        doc.setdefault("global", {})
        doc.setdefault("tasks", {})
        if not isinstance(doc["global"], dict):
            doc["global"] = {}
        if not isinstance(doc["tasks"], dict):
            doc["tasks"] = {}
        for name in global_deleted:
            doc["global"].pop(name, None)
        for name, value in global_updates.items():
            clean = _clean_value(value)
            if clean is None:
                continue
            if name not in doc["global"] and len(doc["global"]) >= MAX_GLOBAL_VARS:
                continue
            doc["global"][name] = clean
        if tid and (task_updates or task_deleted):
            row = doc["tasks"].get(tid) if isinstance(doc["tasks"].get(tid), dict) else {"vars": {}}
            values = row.get("vars") if isinstance(row.get("vars"), dict) else {}
            for name in task_deleted:
                values.pop(name, None)
            for name, value in task_updates.items():
                clean = _clean_value(value)
                if clean is None:
                    continue
                if name not in values and len(values) >= MAX_TASK_VARS:
                    continue
                values[name] = clean
            row["vars"] = values
            row["updated"] = int(time.time())
            doc["tasks"][tid] = row
            if len(doc["tasks"]) > MAX_TASKS:
                oldest = sorted(doc["tasks"].items(), key=lambda kv: (kv[1] or {}).get("updated", 0))
                for key, _ in oldest[: len(doc["tasks"]) - MAX_TASKS]:
                    doc["tasks"].pop(key, None)
        doc["updated"] = int(time.time())
        return True

    mutate_json(vars_path(root), {"global": {}, "tasks": {}}, apply, backup=False, validator=_valid_doc,
                repair_invalid=True)
    return True


def set_global(name: str, value: Any, root: Optional[Path] = None) -> Dict[str, Any]:
    if not _NAME_RE.match(str(name or "")):
        raise ValueError("Имя переменной — латиница, цифры и _ / A variable name uses Latin letters, digits and _")
    commit("", {"global": {name: value}}, root)
    return global_vars(root)


def delete_global(name: str, root: Optional[Path] = None) -> Dict[str, Any]:
    commit("", {"deleted_global": [str(name or "")]}, root)
    return global_vars(root)


def set_task_var(task_id: str, name: str, value: Any, root: Optional[Path] = None) -> Dict[str, Any]:
    if not _NAME_RE.match(str(name or "")):
        raise ValueError("Имя переменной — латиница, цифры и _ / A variable name uses Latin letters, digits and _")
    commit(task_id, {"task": {name: value}}, root)
    return task_vars(task_id, root)


def reset_task(task_id: str, name: str = "", root: Optional[Path] = None) -> bool:
    """Сбросить одну сохранённую переменную задачи или все сразу."""
    from .storage import mutate_json
    tid = str(task_id or "")

    def apply(doc: Dict[str, Any]) -> bool:
        tasks = doc.get("tasks") if isinstance(doc.get("tasks"), dict) else {}
        row = tasks.get(tid)
        if not isinstance(row, dict):
            return False
        if name:
            values = row.get("vars") if isinstance(row.get("vars"), dict) else {}
            if name not in values:
                return False
            values.pop(name, None)
            row["vars"] = values
        else:
            tasks.pop(tid, None)
        doc["tasks"] = tasks
        return True

    mutate_json(vars_path(root), {"global": {}, "tasks": {}}, apply, backup=False, validator=_valid_doc,
                repair_invalid=True)
    return True


def overview(tasks: Optional[List[Dict[str, Any]]] = None, root: Optional[Path] = None) -> Dict[str, Any]:
    """Для вкладки «Переменные»: общие + сохранённые по задачам (с названием задачи)."""
    doc = load(root)
    names = {str(t.get("id")): str(t.get("name") or "") for t in (tasks or []) if isinstance(t, dict)}
    rows = []
    for tid, row in (doc.get("tasks") or {}).items():
        if not isinstance(row, dict) or not row.get("vars"):
            continue
        rows.append({"task_id": tid, "task_name": names.get(tid, ""), "exists": tid in names,
                     "vars": row.get("vars") or {}, "updated": row.get("updated") or 0})
    rows.sort(key=lambda r: (not r["exists"], -(r["updated"] or 0)))
    return {"global": doc.get("global") or {}, "tasks": rows,
            "limits": {"task": MAX_TASK_VARS, "global": MAX_GLOBAL_VARS, "value_chars": 200_000}}


# --------------------------------------------------------------- библиотека макросов
def _valid_macros(value: Any) -> bool:
    return isinstance(value, dict) and isinstance(value.get("macros", []), list)


def macros(root: Optional[Path] = None) -> List[Dict[str, Any]]:
    from .storage import load_json_with_recovery
    doc = load_json_with_recovery(macros_path(root), {"macros": []}, validator=_valid_macros)
    return [m for m in doc.get("macros") or [] if isinstance(m, dict) and m.get("id")]


def get_macro(macro_id: str, root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    key = str(macro_id or "").strip()
    return next((m for m in macros(root) if m.get("id") == key or m.get("name") == key), None)


def save_macro(raw: Dict[str, Any], root: Optional[Path] = None) -> Dict[str, Any]:
    from .plan_scenarios import ScenarioError, macro_ids, needs_confirmation, normalize_steps
    if not isinstance(raw, dict):
        raise ScenarioError("Макрос должен быть объектом", "A macro must be an object")
    name = str(raw.get("name") or "").strip()[:60]
    if not name:
        raise ScenarioError("Назовите макрос", "Name the macro")
    steps = normalize_steps(raw.get("steps") or [])
    macro_id = str(raw.get("id") or "").strip()[:40] or uuid.uuid4().hex[:10]
    if macro_id in macro_ids(steps):
        raise ScenarioError("Макрос не может вызывать сам себя", "A macro cannot call itself")
    item = {"id": macro_id, "name": name, "description": str(raw.get("description") or "").strip()[:300],
            "steps": steps, "destructive": needs_confirmation(steps), "updated": int(time.time())}
    from .storage import mutate_json

    def apply(doc: Dict[str, Any]) -> bool:
        rows = [m for m in doc.get("macros") or [] if isinstance(m, dict)]
        for index, row in enumerate(rows):
            if row.get("id") == macro_id:
                item["created"] = row.get("created") or item["updated"]
                rows[index] = item
                break
        else:
            if len(rows) >= MAX_MACROS:
                raise ScenarioError(f"В библиотеке уже {MAX_MACROS} макросов", f"The library already has {MAX_MACROS} macros")
            if any(str(r.get("name") or "").lower() == name.lower() for r in rows):
                raise ScenarioError("Макрос с таким именем уже есть", "A macro with this name already exists")
            item["created"] = item["updated"]
            rows.append(item)
        doc["macros"] = rows
        return True

    mutate_json(macros_path(root), {"macros": []}, apply, backup=False, validator=_valid_macros, repair_invalid=True)
    return item


def delete_macro(macro_id: str, root: Optional[Path] = None) -> bool:
    from .storage import mutate_json
    box = {"removed": False}

    def apply(doc: Dict[str, Any]) -> bool:
        rows = [m for m in doc.get("macros") or [] if isinstance(m, dict)]
        kept = [m for m in rows if m.get("id") != macro_id]
        box["removed"] = len(kept) != len(rows)
        doc["macros"] = kept
        return box["removed"]

    mutate_json(macros_path(root), {"macros": []}, apply, backup=False, validator=_valid_macros, repair_invalid=True)
    return box["removed"]
