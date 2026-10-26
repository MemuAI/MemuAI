# -*- coding: utf-8 -*-
"""Round 29: «Планирование» in the MemuAI window process — ``/api/plan/*``.

* builder support: ``meta`` (what this computer can check, Quick Access commands,
  Work/MemuCode projects, DFY readiness), ``preview`` (summary, next runs, warnings),
  ``parse`` (a phrase → task draft through the model), ``processes`` (picker),
  ``now`` (live condition values);
* task operations that must not race the scheduler: ``save`` (one task), ``toggle``,
  ``run`` (test run now), ``duplicate``, ``delete``, ``history``;
* the executor of actions handed over by MemuAI Proxy (``schedule_handoff.json``):
  Do For You tasks and Quick Access commands run here because they need this
  process (the DFY browser, the assistant engine).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

from fastapi import Request
from fastapi.responses import JSONResponse

from . import schedule_rules as rules

log = logging.getLogger("memuai.plan")
EXECUTOR_POLL = 2.0

def _lang(body: Dict[str, Any]) -> str:
    if body.get("lang") in ("ru", "en"):
        return body["lang"]
    try:
        from .pgdata import load_preferences
        return "en" if load_preferences().get("language") == "en" else "ru"
    except Exception:
        return "ru"

def _err(exc: Exception, lang: str, status: int = 400) -> JSONResponse:
    text = exc.text(lang) if isinstance(exc, rules.RuleError) else str(exc)
    return JSONResponse({"error": text[:600]}, status)

async def _json(request: Request) -> Dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}

def _load_tasks() -> List[Dict[str, Any]]:
    from .pgdata import load_schedules
    return load_schedules()

def _public_task(task: Dict[str, Any], lang: str, by_id: Dict[str, Dict[str, Any]], now: float) -> Dict[str, Any]:
    out = dict(task)
    rule = task.get("rule") if isinstance(task.get("rule"), dict) else rules.legacy_rule(task)
    out["legacy"] = not isinstance(task.get("rule"), dict)
    out["summary"] = rules.summary(rule, lang, by_id) if rule else ""
    try:
        norm = rules.normalize_rule(rule) if rule else None
    except rules.RuleError:
        norm = None
    out["event_only"] = bool(norm) and not rules.has_time_triggers(norm)
    return out

def preview_payload(task: Dict[str, Any], tasks: List[Dict[str, Any]], lang: str, now: Optional[float] = None) -> Dict[str, Any]:
    now = time.time() if now is None else now
    rule = rules.normalize_rule(task.get("rule"))
    by_id = {str(t.get("id")): t for t in tasks}
    probe = dict(task, rule=rule)
    nexts = rules.preview(rule, now, 6)
    flow: Dict[str, Any] = {}
    if task.get("kind") == "integration":
        from .schedule_integrations import validate
        validate(task.get("tool"), task.get("args", {}))
    if isinstance(task.get("steps"), list) and task["steps"]:

        from .flow_engine import FlowError, autolink, describe, normalize_steps, validate_flow
        try:
            steps = autolink(normalize_steps(task["steps"]))
        except FlowError as exc:
            raise rules.RuleError(exc.ru, exc.en)
        errors = [p for p in validate_flow(steps, lang) if p["level"] == "error"]
        if errors:
            raise rules.RuleError(errors[0]["text"], errors[0]["text"])
        flow = {"steps": steps, "steps_text": [describe(step, lang) for step in steps]}
    return {**flow, "rule": rule, "summary": rules.summary(rule, lang, by_id), "next": nexts,
            "events": [rules.trigger_text(t, lang, by_id) for t in rule["when"] if t["type"] in rules.POLLED_TYPES],
            "warnings": rules.warnings(probe, [t for t in tasks if str(t.get("id")) != str(task.get("id"))] + [probe],
                                       lang, now=now)}

def _clean_task(raw: Dict[str, Any]) -> Dict[str, Any]:
    """A task coming from the builder: only known user fields."""
    keys = ("id", "name", "prompt", "model", "mode", "kind", "enabled", "notify_telegram", "notify_email",
            "project_id", "access_mode", "command", "command_id", "project_path", "permission_mode", "rule",
            "run_at", "interval_minutes", "daily_at", "created_by", "lang", "reminder_text", "icon", "steps", "tool", "args", "dfy_browser", "team_id")
    task = {k: raw.get(k) for k in keys if k in raw}
    if isinstance(task.get("steps"), list) and task["steps"]:

        from .flow_engine import FlowError, normalize_steps
        try:
            task["steps"] = normalize_steps(task["steps"])
        except FlowError as exc:
            raise rules.RuleError(exc.ru, exc.en)
        task["kind"] = "flow"
    elif "steps" in task:
        task["steps"] = []
        if task.get("kind") == "flow":
            task["kind"] = "chat"
    task["id"] = str(task.get("id") or uuid.uuid4().hex[:12])[:120]
    task["name"] = str(task.get("name") or "").strip()[:120]
    task["prompt"] = str(task.get("prompt") or "")[:20000]
    kind = str(task.get("kind") or "chat")
    task["kind"] = kind if kind in rules.ACTION_KINDS or kind == "reminder" else "chat"
    task["mode"] = "work" if task.get("mode") == "work" and task["kind"] == "chat" else "chat"
    task["enabled"] = task.get("enabled", True) is not False
    task["notify_telegram"] = task.get("notify_telegram") is True
    task["notify_email"] = task.get("notify_email") is True
    if task["kind"] == "dfy":
        browser = task.get("dfy_browser") or ""
        if browser not in ("", "isolated", "chrome"):
            raise rules.RuleError("Неизвестный браузер DFY", "Unknown DFY browser")
        task["dfy_browser"] = browser
    if task["kind"] == "team":
        task["team_id"] = str(task.get("team_id") or "").strip()[:120]
    if task["kind"] == "memucode":
        task["permission_mode"] = str(task.get("permission_mode") or "plan")
        if task["permission_mode"] not in ("default", "plan", "acceptEdits", "bypassPermissions"):
            task["permission_mode"] = "plan"
    return task

def _validate_action(task: Dict[str, Any], lang: str) -> None:
    en = lang == "en"
    if not task["name"]:
        raise rules.RuleError("Введите название задачи", "Enter a task name")
    kind = task["kind"]
    if kind == "flow":
        from .flow_engine import validate_flow
        if not task.get("steps"):
            raise rules.RuleError("Добавьте хотя бы один шаг", "Add at least one step")
        errors = [p for p in validate_flow(task["steps"], lang) if p["level"] == "error"]
        if errors:
            raise rules.RuleError(errors[0]["text"], errors[0]["text"])
        return
    if kind == "integration":
        from .schedule_integrations import validate
        task.update(validate(task.get("tool"), task.get("args", {})))
        return
    if kind in ("chat", "team", "notify", "dfy", "memucode", "reminder") and not task["prompt"].strip():
        raise rules.RuleError({"notify": "Введите текст уведомления", "dfy": "Опишите, что сделать в браузере", "team": "Введите задание для команды"}.get(
            kind, "Введите задание для модели"), {"notify": "Enter the notification text", "dfy": "Describe the browser task", "team": "Enter the task for the team"}.get(
            kind, "Enter the task for the model"))
    if kind == "team":
        from . import teams as _teams
        team_rows = _teams.load()
        tid = str(task.get("team_id") or "").strip()
        if not tid and team_rows:
            task["team_id"] = str(team_rows[0]["id"])
        elif not tid:
            raise rules.RuleError("Выберите команду (или создайте её в разделе «Команда»)", "Choose a team (or create one in the Team section)")
        elif team_rows and not any(r.get("id") == tid for r in team_rows):
            raise rules.RuleError("Выбранная команда не найдена", "The selected team was not found")
    if kind == "command" and not (task.get("command") or task.get("command_id")):
        raise rules.RuleError("Выберите команду быстрого доступа", "Choose a Quick Access command")
    if kind == "memucode" and not str(task.get("project_path") or "").strip():
        raise rules.RuleError("Выберите папку проекта MemuCode", "Choose the MemuCode project folder")
    if task.get("mode") == "work" and not task.get("project_id"):
        raise rules.RuleError("Выберите проект Work", "Choose a Work project")
    del en

def save_task(raw: Dict[str, Any], lang: str = "ru") -> str:
    """Validates and upserts one task of the builder (MemuAI window, Mini App, Quick Access); returns its id.

    Raises ``RuleError`` with a Russian/English text for the user."""
    task = _clean_task(raw)
    _validate_action(task, lang)
    if task.get("rule") not in (None, "", {}):
        task["rule"] = rules.normalize_rule(task["rule"])
        for key in ("run_at", "interval_minutes", "daily_at"):
            task.pop(key, None)
    tasks = _load_tasks()
    if isinstance(task.get("rule"), dict):
        others = [t for t in tasks if str(t.get("id")) != task["id"]] + [task]
        if task["id"] in rules.chain_cycles(others):
            raise rules.RuleError("Цепочка замыкается сама на себя", "The chain loops back on itself")

    from .pgdata import _mutate_schedules

    def upsert(rows: List[Dict[str, Any]]) -> bool:
        previous = next((r for r in rows if str(r.get("id")) == task["id"]), None)
        merged = dict(previous or {})
        for key in ("run_at", "interval_minutes", "daily_at", "rule"):
            merged.pop(key, None)
        merged.update(task)
        rules.sanitize_task(merged)
        rules.keep_runtime_fields(merged, previous)
        if previous is not None:
            if rules.rule_signature(merged) != rules.rule_signature(previous) or \
                    (merged.get("run_at"), merged.get("interval_minutes"), merged.get("daily_at")) != \
                    (previous.get("run_at"), previous.get("interval_minutes"), previous.get("daily_at")):
                merged.pop("next_run", None)
            rows[rows.index(previous)] = merged
        else:
            merged.setdefault("created", int(time.time()))
            rows.append(merged)
        return True

    _mutate_schedules(upsert)
    return task["id"]

class Executor:
    """Runs DFY / Quick Access command actions handed over by MemuAI Proxy."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.task: Optional[asyncio.Task] = None
        self.running: Dict[str, asyncio.Task] = {}
        self.dfy_ids: Dict[str, str] = {}
        self._mtime = None

    def start(self) -> None:
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.loop())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        jobs = list(self.running.values())
        for job in jobs:
            job.cancel()
        if jobs:
            await asyncio.gather(*jobs, return_exceptions=True)

    async def loop(self) -> None:
        from .schedule_runtime import handoff_path
        await asyncio.sleep(1.0)

        await asyncio.to_thread(self._recover)
        while True:
            try:
                try:
                    mtime = handoff_path().stat().st_mtime_ns
                except OSError:
                    mtime = None
                if mtime != self._mtime or self.running:
                    self._mtime = mtime
                    await self.pump()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("plan executor: %s", exc)
            await asyncio.sleep(EXECUTOR_POLL)

    def _recover(self) -> None:
        from .schedule_runtime import _handoff_mutate
        pid = os.getpid()

        def fix(items: List[Dict[str, Any]]) -> bool:
            changed = False
            for item in items:
                if item.get("status") == "running" and item.get("claimed_by") != pid:
                    item.update(status="done", ok=False, updated=time.time(),
                                text="MemuAI was closed while the task was running" if item.get("task", {}).get("lang") == "en"
                                else "MemuAI закрылся во время выполнения задачи")
                    changed = True
            return changed
        try:
            _handoff_mutate(fix)
        except Exception:
            pass

    async def pump(self) -> None:
        from .schedule_runtime import _handoff_mutate, handoff_items
        items = await asyncio.to_thread(handoff_items)
        for item in items:
            if item.get("status") == "running" and item.get("cancel") and item.get("id") in self.running:
                self.running[item["id"]].cancel()
        queued = [item for item in items if item.get("status") == "queued"]
        for item in queued:
            claimed: List[Dict[str, Any]] = []
            pid = os.getpid()

            def claim(rows: List[Dict[str, Any]], item_id=item["id"]) -> bool:
                for row in rows:
                    if row.get("id") == item_id and row.get("status") == "queued":
                        row.update(status="running", claimed_by=pid, updated=time.time())
                        claimed.append(dict(row))
                        return True
                return False

            await asyncio.to_thread(_handoff_mutate, claim)
            if claimed:
                job = asyncio.create_task(self.execute(claimed[0]))
                self.running[item["id"]] = job
                job.add_done_callback(lambda _j, key=item["id"]: self.running.pop(key, None))

    async def execute(self, item: Dict[str, Any]) -> None:
        from .schedule_runtime import _handoff_mutate
        ok, text, extra = False, "", {}
        try:
            if item.get("kind") == "dfy":
                ok, text, extra = await self.run_dfy(item)
            elif item.get("kind") == "command":
                ok, text, extra = await self.run_command(item)
            elif item.get("kind") == "integration":
                from .schedule_integrations import run
                ok, text, extra = await run(item.get("task") or {})
            elif item.get("kind") in ("macro", "action"):

                from .flow_adapters import run_handoff
                ok, text, extra = await run_handoff(item)
            else:
                text = "unknown action"
        except asyncio.CancelledError:
            dfy_id = self.dfy_ids.pop(item["id"], "")
            manager = getattr(self.app.state, "dfy_manager", None)
            if dfy_id and manager is not None:
                try:
                    await manager.cancel(dfy_id)
                except Exception:
                    pass
            ok, text = False, ("Stopped" if (item.get("task") or {}).get("lang") == "en" else "Остановлено")
        except Exception as exc:  # noqa: BLE001
            ok, text = False, f"{type(exc).__name__}: {exc}"[:600]
        await asyncio.to_thread(_handoff_mutate, lambda rows: _finish(rows, item["id"], ok, text, extra))

    async def run_dfy(self, item: Dict[str, Any]):
        manager = getattr(self.app.state, "dfy_manager", None)
        task = item.get("task") or {}
        lang = _lang({})
        if manager is None:
            return False, ("Do For You is unavailable" if lang == "en" else "Do For You недоступен"), {}
        goal = str(task.get("prompt") or "").strip()
        try:
            dfy = await manager.create(goal, [], "schedule", "", browser=task.get("dfy_browser") or "")
        except PermissionError as exc:
            return False, str(exc), {}
        unattended = bool(getattr(dfy, "unattended", False))
        self.dfy_ids[item["id"]] = dfy.id
        from .schedule_runtime import _handoff_mutate
        await asyncio.to_thread(_handoff_mutate, lambda rows: _set_fields(rows, item["id"], dfy_task=dfy.id))
        version, asked = -1, False
        while True:
            data = await manager.wait_change(dfy.id, version, timeout=15.0)
            if data is None:
                return False, ("The DFY task disappeared" if lang == "en" else "Задача DFY пропала"), {"dfy_task": dfy.id}
            version = data.get("version", version)
            status = data.get("status")
            if status == "waiting_user" and unattended:

                await manager.cancel(dfy.id)
                self.dfy_ids.pop(item["id"], None)
                question = data.get("question") or {}
                detail = str(question.get("question") or question.get("summary") or question.get("title") or "") \
                    if isinstance(question, dict) else str(question)
                text = ("This scheduled browser task needs user input. Update the task or sign in before retrying."
                        if lang == "en" else "Для задачи в браузере нужен ввод пользователя. Уточните задачу или войдите в аккаунт перед повтором.")
                return False, text + (" " + detail[:500] if detail else ""), {"dfy_task": dfy.id, "dfy_status": "needs_input"}
            if status == "waiting_user" and not asked:
                asked = True
                try:
                    from .desktop_notifications import show_completion
                    await asyncio.to_thread(show_completion, "MemuAI · " + str(task.get("name") or "DFY"),
                                            "Do For You is waiting for your answer — open Planning"
                                            if lang == "en" else "Do For You ждёт вашего ответа — откройте «Планирование»")
                except Exception:
                    pass
            if status in ("done", "failed", "cancelled"):
                self.dfy_ids.pop(item["id"], None)
                steps = len(data.get("steps") or [])
                text = str(data.get("result") or data.get("error") or status)
                tail = (f"\n\nSteps: {steps}" if lang == "en" else f"\n\nШагов: {steps}") if steps else ""
                return status == "done", text + tail, {"dfy_task": dfy.id, "dfy_status": status}

    async def run_command(self, item: Dict[str, Any]):
        from . import assistant_commands as ac
        task = item.get("task") or {}
        lang = _lang({})
        eng = ac.engine()
        ref = task.get("command") or task.get("command_id")
        command = eng._find_command(ref)
        if not command:
            return False, (f"Quick Access command “{ref}” not found" if lang == "en"
                           else f"Команда быстрого доступа «{ref}» не найдена"), {}
        tier = ac.command_tier(command, eng._find_command, honour_trusted=True)

        card = await eng.run("command.run", {"id": command["id"], "name": command.get("name") or ""}, source="routine-bg", lang=lang,
                             confirmed=tier != "dangerous", emit=True)
        status = card.get("status")
        lines = [str(card.get("message") or "")]
        for step in card.get("steps") or []:
            mark = {"done": "✓", "error": "✗", "skipped": "–"}.get(step.get("status"), "•")
            lines.append(f"{mark} {step.get('title') or step.get('type')}: {step.get('message') or ''}".rstrip(": "))
        if card.get("answer"):
            lines.append("\n" + str(card["answer"]))
        if status == "confirm":
            lines.append("Waiting for confirmation in Quick Access" if lang == "en"
                         else "Ждёт подтверждения в окне быстрого доступа")
        return status == "done", "\n".join(x for x in lines if x).strip(), {"command": command.get("name")}

def _finish(rows: List[Dict[str, Any]], item_id: str, ok: bool, text: str, extra: Dict[str, Any]) -> bool:
    for row in rows:
        if row.get("id") == item_id:
            row.update(status="done", ok=bool(ok), text=str(text or "")[:20000], extra=extra or {}, updated=time.time())
            return True
    return False

def _set_fields(rows: List[Dict[str, Any]], item_id: str, **fields: Any) -> bool:
    for row in rows:
        if row.get("id") == item_id:
            row.update(fields)
            return True
    return False

def register(app: Any, *, completion: Optional[Callable[..., Awaitable[str]]] = None) -> Executor:
    executor = Executor(app)
    app.state.plan_executor = executor
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(asgi_app):
        if not getattr(app.state, "plan_no_executor", False):
            executor.start()
        try:
            async with original(asgi_app) as state:
                yield state
        finally:
            await executor.stop()

    app.router.lifespan_context = lifespan

    try:
        from .schedule_triggers_ext import register_routes as _register_trigger_routes
        _register_trigger_routes(app)
    except Exception as exc:  # noqa: BLE001
        log.warning("plan: trigger routes not registered: %s", exc)

    try:
        from .schedule_conds_ext import register_routes as _register_cond_routes
        _register_cond_routes(app)
    except Exception as exc:  # noqa: BLE001
        log.warning("plan: condition routes not registered: %s", exc)

    @app.get("/api/plan/tasks")
    async def plan_tasks(lang: str = ""):
        from .pgdata import load_schedule_results
        lang = lang if lang in ("ru", "en") else _lang({})
        tasks = await asyncio.to_thread(_load_tasks)
        results = await asyncio.to_thread(load_schedule_results)
        now = time.time()
        by_id = {str(t.get("id")): t for t in tasks}
        from .schedule_runtime import handoff_items
        active = [{"task_id": it.get("task_id"), "kind": it.get("kind"), "status": it.get("status"),
                   "dfy_task": it.get("dfy_task", ""), "created": it.get("created")}
                  for it in await asyncio.to_thread(handoff_items) if it.get("status") in ("queued", "running")]
        rows = [_public_task(t, lang, by_id, now) for t in tasks]
        try:
            from .schedule_triggers_ext import health as _watch_health
            watch = await asyncio.to_thread(_watch_health, lang)
            for row in rows:
                if str(row.get("id")) in watch:
                    row["watch_health"] = watch[str(row.get("id"))]
        except Exception as exc:  # noqa: BLE001
            log.debug("plan: watch health failed: %s", exc)
        return {"tasks": rows, "results": results[:100], "active": active, "now": now}

    @app.get("/api/plan/meta")
    async def plan_meta():
        out: Dict[str, Any] = {"os": "windows" if os.name == "nt" else os.name,
                               "can": {"idle": os.name == "nt", "focus": os.name == "nt",
                                       "processes": True, "power": True, "network": True},
                               "events": list(rules.EVENTS), "conditions": list(rules.CONDITION_TYPES),
                               "kinds": list(rules.ACTION_KINDS), "protocol": rules.CHAT_PROTOCOL}
        try:
            from .schedule_integrations import catalog
            out["integrations"] = await asyncio.to_thread(catalog)
        except Exception:
            out["integrations"] = []
        try:
            import psutil  # noqa: F401
        except Exception:
            out["can"]["processes"] = False
        try:
            from . import teams as _teams
            out["teams"] = [{"id": t.get("id"), "name": t.get("name"), "manager_model": t.get("manager_model", ""),
                             "members_count": len(t.get("members") or [])} for t in await asyncio.to_thread(_teams.load)][:100]
        except Exception:
            out["teams"] = []
        try:
            from . import assistant_commands as ac
            commands = ac.engine().store.load().get("commands") or []
            out["commands"] = [{"id": c.get("id"), "name": c.get("name"), "enabled": c.get("enabled", True),
                                "steps": len(c.get("steps") or [])} for c in commands][:200]
        except Exception:
            out["commands"] = []
        try:
            from .tgapp_code import memucode_command, recent_projects
            out["memucode"] = {"installed": bool(memucode_command()), "projects": recent_projects(30)}
        except Exception:
            out["memucode"] = {"installed": False, "projects": []}
        manager = getattr(app.state, "dfy_manager", None)
        if manager is not None:
            try:
                st = manager.status()
                out["dfy"] = {"ready": st.get("ready"), "reason": st.get("reason"), "model": st.get("model")}
            except Exception:
                out["dfy"] = {"ready": False, "reason": ""}
        return out

    @app.post("/api/plan/preview")
    async def plan_preview(request: Request):
        body = await _json(request)
        lang = _lang(body)
        task = body.get("task") if isinstance(body.get("task"), dict) else {"rule": body.get("rule")}
        try:
            tasks = await asyncio.to_thread(_load_tasks)
            return preview_payload(task, tasks, lang)
        except rules.RuleError as exc:
            return _err(exc, lang)

    @app.post("/api/plan/parse")
    async def plan_parse(request: Request):
        body = await _json(request)
        lang = _lang(body)
        text = str(body.get("text") or "").strip()[:2000]
        if not text:
            return JSONResponse({"error": "Describe the schedule" if lang == "en" else "Опишите расписание"}, 400)
        if completion is None:
            return JSONResponse({"error": "no model"}, 503)
        model = str(body.get("model") or "").strip()[:300]
        if not model:
            from .pgdata import load_preferences
            prefs = await asyncio.to_thread(load_preferences)
            model = str(prefs.get("default_model") or prefs.get("model") or "")
        if not model:
            return JSONResponse({"error": "Choose a model in the chat first" if lang == "en"
                                 else "Сначала выберите модель в чате"}, 400)
        tasks = await asyncio.to_thread(_load_tasks)
        try:
            draft = await parse_text(text, model, completion, lang=lang, tasks=tasks)
            payload = preview_payload(draft, tasks, lang)
        except rules.RuleError as exc:
            return _err(exc, lang, 422)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": (("Could not parse: " if lang == "en" else "Не удалось разобрать: ")
                                           + str(exc))[:500]}, 502)
        draft["rule"] = payload["rule"]
        draft["model"] = draft.get("model") or model
        return {"task": draft, **payload}

    @app.get("/api/plan/processes")
    async def plan_processes():
        from .schedule_sampler import process_list
        return {"processes": await asyncio.to_thread(process_list)}

    @app.get("/api/plan/now")
    async def plan_now():
        from . import schedule_sampler as sampler
        power, battery = await asyncio.to_thread(sampler.power)
        return {"idle_seconds": await asyncio.to_thread(sampler.idle_seconds),
                "focus": await asyncio.to_thread(sampler.focused_window),
                "power": power, "battery": battery, "memuai_open": True, "ts": time.time()}

    @app.post("/api/plan/save")
    async def plan_save(request: Request):
        body = await _json(request)
        lang = _lang(body)
        raw = body.get("task") if isinstance(body.get("task"), dict) else {}
        try:
            task_id = await asyncio.to_thread(save_task, raw, lang)
        except rules.RuleError as exc:
            return _err(exc, lang)
        return {"ok": True, "id": task_id}

    async def _mutate_one(task_id: str, fn: Callable[[Dict[str, Any], List[Dict[str, Any]]], Any]) -> bool:
        from .pgdata import _mutate_schedules
        found: List[bool] = []

        def update(rows: List[Dict[str, Any]]) -> bool:
            for row in rows:
                if str(row.get("id") or "") == task_id:
                    fn(row, rows)
                    found.append(True)
                    return True
            return False

        await asyncio.to_thread(_mutate_schedules, update)
        return bool(found)

    def _missing(lang: str) -> JSONResponse:
        return JSONResponse({"error": "Task not found" if lang == "en" else "Задача не найдена"}, 404)

    @app.post("/api/plan/toggle")
    async def plan_toggle(request: Request):
        body = await _json(request)
        lang = _lang(body)
        enabled = body.get("enabled") is True

        def change(row: Dict[str, Any], _rows) -> None:
            was = row.get("enabled", True) not in (False, "false", 0)
            row["enabled"] = enabled
            if enabled and not was:
                if isinstance(row.get("rule"), dict):
                    state = row.get("rule_state") if isinstance(row.get("rule_state"), dict) else {}
                    row["rule_state"] = {k: v for k, v in state.items() if k in ("seen_done",)}
                    if state.get("status") == "finished":

                        row["run_count"] = 0
                        row["fail_streak"] = 0
                row.pop("next_run", None)

        if not await _mutate_one(str(body.get("id") or ""), change):
            return _missing(lang)
        return {"ok": True}

    @app.post("/api/plan/run")
    async def plan_run(request: Request):
        body = await _json(request)
        lang = _lang(body)

        def change(row: Dict[str, Any], _rows) -> None:
            if isinstance(row.get("rule"), dict):
                row["run_now"] = time.time()
            else:

                row["next_run"] = time.time()
                if row.get("enabled") is False:
                    row["enabled"] = True

        if not await _mutate_one(str(body.get("id") or ""), change):
            return _missing(lang)
        return {"ok": True}

    @app.post("/api/plan/duplicate")
    async def plan_duplicate(request: Request):
        body = await _json(request)
        lang = _lang(body)
        new_id = uuid.uuid4().hex[:12]
        suffix = " (copy)" if lang == "en" else " (копия)"

        def change(row: Dict[str, Any], rows: List[Dict[str, Any]]) -> None:
            copy = json.loads(json.dumps(row))
            copy["id"] = new_id
            copy["name"] = (str(row.get("name") or "") + suffix)[:120]
            copy["enabled"] = False
            for key in ("next_run", "last_run", "run_now", *rules.RUNTIME_FIELDS):
                copy.pop(key, None)
            rows.append(copy)

        if not await _mutate_one(str(body.get("id") or ""), change):
            return _missing(lang)
        return {"ok": True, "id": new_id}

    @app.post("/api/plan/delete")
    async def plan_delete(request: Request):
        body = await _json(request)
        lang = _lang(body)
        task_id = str(body.get("id") or "")
        from .pgdata import _mutate_schedules
        removed: List[bool] = []

        def drop(rows: List[Dict[str, Any]]) -> bool:
            keep = [r for r in rows if str(r.get("id") or "") != task_id]
            if len(keep) != len(rows):
                removed.append(True)
                rows[:] = keep
                return True
            return False

        await asyncio.to_thread(_mutate_schedules, drop)
        if not removed:
            return _missing(lang)
        return {"ok": True}

    @app.post("/api/plan/pick-folder")
    async def plan_pick_folder(request: Request):
        """A folder for «files changed» or MemuCode — the native dialog, without adding a Work project."""
        body = await _json(request)
        lang = _lang(body)
        window = getattr(app.state, "window", None)
        try:
            import webview as _wv
        except ImportError:
            _wv = None
        if not window or _wv is None:
            return JSONResponse({"error": "Folder picking works in the MemuAI window — type the path instead"
                                 if lang == "en" else "Выбор папки работает в окне MemuAI — впишите путь вручную"}, 400)
        result = await asyncio.to_thread(window.create_file_dialog, _wv.FOLDER_DIALOG)
        if not result:
            return {"path": ""}
        folder = result[0] if isinstance(result, (list, tuple)) else result
        return {"path": str(folder)}

    @app.get("/api/plan/history")
    async def plan_history(id: str = ""):
        from .pgdata import load_schedule_results
        rows = await asyncio.to_thread(load_schedule_results)
        return {"results": [r for r in rows if str(r.get("task_id")) == str(id)][:50]}

    try:
        from . import flow_api
        flow_api.register(app, completion=completion)
    except Exception:  # noqa: BLE001 - Planning keeps working without the flow routes
        log.exception("flow routes are unavailable")
    return executor

async def parse_text(text: str, model: str, completion: Callable[..., Awaitable[str]], *, lang: str = "ru",
                     tasks: Optional[List[Dict[str, Any]]] = None, commands: Optional[List[str]] = None) -> Dict[str, Any]:
    """«каждый день в 16:00 если запущена Dota 2» → a validated task draft (one retry on bad JSON)."""
    now = datetime.now()
    if commands is None:
        try:
            from . import assistant_commands as ac
            commands = [str(c.get("name")) for c in ac.engine().store.load().get("commands") or []][:60]
        except Exception:
            commands = []
    try:
        from . import teams as _teams
        team_list = [{"id": t.get("id"), "name": t.get("name")} for t in _teams.load()][:40]
    except Exception:
        team_list = []
    context = {"NOW": now.strftime("%Y-%m-%d %H:%M (%A)"), "user_language": lang,
               "existing_tasks": [{"id": t.get("id"), "name": t.get("name")} for t in (tasks or [])][:60],
               "teams": team_list,
               "quick_access_commands": commands}
    try:
        from .schedule_integrations import catalog
        context["connected_integration_tools"] = await asyncio.to_thread(catalog)
    except Exception:
        context["connected_integration_tools"] = []
    system = rules.PARSE_PROMPT
    try:
        from . import schedule_triggers_ext as trigger_ext
        system += trigger_ext.parse_prompt(lang)
        context.update(await asyncio.to_thread(trigger_ext.parse_context))
    except Exception as exc:  # noqa: BLE001
        log.warning("plan: trigger parse context failed: %s", exc)
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False) + "\n\nREQUEST: " + text}]
    last: Optional[Exception] = None
    for attempt in range(2):
        answer = await completion(model, messages, prompt_caching=False)
        try:
            data = rules.parse_model_json(answer)
            draft = rules.draft_from_model(data, lang)
            try:
                from .schedule_triggers_ext import tune_draft
                draft = tune_draft(draft)
            except Exception:  # noqa: BLE001
                pass
            return draft
        except rules.RuleError as exc:
            last = exc
            if "not a schedule" in json.dumps(answer)[:4000] and attempt == 0 and "error" in answer:
                raise
            messages = messages + [{"role": "assistant", "content": str(answer)[:4000]},
                                   {"role": "user", "content": f"That was not valid: {exc.en}. Return only the corrected JSON object."}]
    raise last or rules.RuleError("Не удалось разобрать", "Could not parse")
