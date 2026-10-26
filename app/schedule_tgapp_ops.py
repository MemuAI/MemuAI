# -*- coding: utf-8 -*-
"""Round 37 (plan-2): the conditions of a Planning task in the Telegram Mini App.

``schedules.conds {id}`` — the task's «Только если» as flat rows (groups nested by ``depth``) with their text;
``schedules.cond_set {id, path, off?, not?, match?}`` — switch a condition off / on, invert it («НЕ»), or flip the
task's (``path`` = "") / a group's all ↔ any; ``schedules.cond_check {id}`` — «Проверить сейчас» (the same
explanation the desktop builder shows). ``path`` is "2" for ``only_if[2]`` and "2.1" for an item of that group.
Editing the conditions themselves stays in MemuAI on the computer.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Dict, List, Optional

def _lang(ctx: Dict[str, Any]) -> str:
    prefs = ctx.get("prefs") or {}
    return "en" if str(prefs.get("lang") or prefs.get("language") or "").startswith("en") else "ru"

def _find(task_id: str) -> Optional[Dict[str, Any]]:
    from .pgdata import load_schedules
    return next((t for t in load_schedules() if str(t.get("id") or "") == task_id), None)

def condition_rows(task: Dict[str, Any], lang: str) -> Dict[str, Any]:
    from . import schedule_rules as rules
    rule = rules.normalize_rule(task.get("rule"))
    rows: List[Dict[str, Any]] = []

    def visit(cond: Dict[str, Any], path: str, depth: int) -> None:
        rows.append({"path": path, "depth": depth, "type": cond["type"], "text": rules.condition_text(cond, lang),
                     "not": bool(cond.get("not")), "off": bool(cond.get("off")),
                     "match": cond.get("match") if cond["type"] == "group" else None})
        if cond["type"] == "group":
            for j, item in enumerate(cond.get("items") or []):
                visit(item, f"{path}.{j}", depth + 1)

    for i, cond in enumerate(rule.get("only_if") or []):
        visit(cond, str(i), 0)
    return {"rows": rows, "match": rule.get("match", "all"), "summary": rules.summary(rule, lang)}

def _locate(rule: Dict[str, Any], path: str) -> Optional[Dict[str, Any]]:
    node: Any = {"items": rule.get("only_if") or []}
    for part in path.split("."):
        items = node.get("items") if isinstance(node, dict) else None
        if not isinstance(items, list) or not part.isdigit() or int(part) >= len(items):
            return None
        node = items[int(part)]
    return node if isinstance(node, dict) else None

def apply_change(task: Dict[str, Any], body: Dict[str, Any]) -> bool:
    """Mutates ``task["rule"]`` in place; False when the path is unknown."""
    from . import schedule_rules as rules
    rule = task.get("rule")
    if not isinstance(rule, dict):
        return False
    path = str(body.get("path") or "").strip()
    if not path:
        if body.get("match") in ("all", "any"):
            rule["match"] = body["match"]
            return True
        return False
    cond = _locate(rule, path)
    if cond is None:
        return False
    for key in ("off", "not"):
        if key in body:
            if body[key]:
                cond[key] = True
            else:
                cond.pop(key, None)
    if body.get("match") in ("all", "any") and cond.get("type") == "group":
        cond["match"] = body["match"]
    rules.normalize_rule(rule)
    state = task.get("rule_state") if isinstance(task.get("rule_state"), dict) else {}
    if state.get("status") == "blocked":
        state.pop("status", None)
        state.pop("reason", None)
    task.pop("next_run", None)
    return True

def check_task(task: Dict[str, Any], lang: str) -> Dict[str, Any]:
    from . import schedule_rules as rules
    from .pgdata import load_schedules
    from .schedule_conds_ext import explain, prefetch
    from .schedule_sampler import Sampler
    rule = rules.normalize_rule(task.get("rule"))
    needs = rules.needs(rule, str(task.get("kind") or "chat"))
    now = time.time()
    if needs.get("ext"):
        prefetch(needs["ext"], now)
    _prev, sample = Sampler().get(needs, now=now, fresh=True)
    sample = dict(sample or {})
    by_id = {str(t.get("id")): t for t in load_schedules()}
    return explain(rule, sample, now, lang, {"task": task, "tasks_by_id": by_id, "state": task.get("rule_state") or {}})

def register_tgapp_ops(op: Callable[[str], Callable], ApiError: Any) -> None:  # noqa: N803
    from . import schedule_rules as rules

    async def load(ctx: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
        task = await asyncio.to_thread(_find, str(body.get("id") or ""))
        if not task:
            raise ApiError(404, "not_found", "Задача не найдена", "Task not found")
        if not isinstance(task.get("rule"), dict):
            raise ApiError(400, "no_rule", "У этой задачи нет условий", "This task has no conditions")
        return task

    @op("schedules.conds")
    async def schedules_conds(ctx, body):
        task = await load(ctx, body)
        try:
            return condition_rows(task, _lang(ctx))
        except rules.RuleError as exc:
            raise ApiError(400, "invalid", exc.text("ru"), exc.text("en")) from None

    @op("schedules.cond_set")
    async def schedules_cond_set(ctx, body):
        task_id = str(body.get("id") or "")
        await load(ctx, body)
        from .tgapp_extras import _set_schedule
        errors: List[Any] = []

        def change(task: Dict[str, Any]) -> None:
            try:
                if not apply_change(task, body):
                    errors.append(None)
            except rules.RuleError as exc:
                errors.append(exc)
                raise

        try:
            found = await asyncio.to_thread(_set_schedule, task_id, change)
        except rules.RuleError as exc:
            raise ApiError(400, "invalid", exc.text("ru"), exc.text("en")) from None
        if not found or errors:
            raise ApiError(404, "not_found", "Условие не найдено — обновите список", "Condition not found — refresh the list")
        task = await asyncio.to_thread(_find, task_id)
        return condition_rows(task or {}, _lang(ctx))

    @op("schedules.cond_check")
    async def schedules_cond_check(ctx, body):
        task = await load(ctx, body)
        try:
            return await asyncio.to_thread(check_task, task, _lang(ctx))
        except rules.RuleError as exc:
            raise ApiError(400, "invalid", exc.text("ru"), exc.text("en")) from None
