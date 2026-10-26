# -*- coding: utf-8 -*-
"""Round 35: ``/api/plan/flow/*`` — the step constructor of «Планирование» (MemuAI window process).

* ``catalog`` — step types, condition operators, MemuBase tables, tasks / scenarios / buttons for pickers;
* ``of`` — a task as steps (old single-action tasks → one step, lazy migration, nothing is saved);
* ``validate`` — canonical steps + numbering + problems; ``vars`` — the variable picker for a step;
* ``test`` / ``continue`` / ``cancel`` / ``run`` / ``runs`` — dry runs «Тест шаг за шагом» (executed here, side
  effects only described) and the run history with per-step outputs (real runs are executed by MemuAI Proxy);
* ``automations`` / ``as-task`` — Quick Access scenarios and buttons as flows (:mod:`app.flow_adapters`);
* ``forget-seen`` — reset «Только новые» of a task.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, Optional

from fastapi import Request
from fastapi.responses import JSONResponse

from . import flow_engine as fe

log = logging.getLogger("memuai.flows.api")

async def _json(request: Request) -> Dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}

def _lang(body: Dict[str, Any]) -> str:
    if body.get("lang") in ("ru", "en"):
        return body["lang"]
    try:
        from .pgdata import load_preferences
        return "en" if load_preferences().get("language") == "en" else "ru"
    except Exception:
        return "ru"

def _err(exc: Exception, lang: str, status: int = 400) -> JSONResponse:
    text = exc.text(lang) if isinstance(exc, fe.FlowError) else str(exc)
    return JSONResponse({"error": text[:600]}, status)

def register(app: Any, *, completion: Optional[Callable[..., Awaitable[str]]] = None) -> None:

    @app.get("/api/plan/flow/catalog")
    async def flow_catalog(lang: str = ""):
        lang = lang if lang in ("ru", "en") else _lang({})
        from .pgdata import load_schedules
        tasks = await asyncio.to_thread(load_schedules)
        out: Dict[str, Any] = {"steps": fe.step_catalog(lang), "ops": list(fe.COND_OPS),
                               "tables": await asyncio.to_thread(fe.memubase_tables),
                               "tasks": [{"id": t.get("id"), "name": t.get("name")} for t in tasks][:200],
                               "limits": {"steps": fe.MAX_STEPS, "depth": fe.MAX_DEPTH, "loop": fe.MAX_LOOP}}
        try:
            from . import flow_adapters
            autos = await asyncio.to_thread(flow_adapters.list_automations, lang)
            out["scenarios"] = [{"id": a["id"], "name": a["name"]} for a in autos if a["source"] == "scenario"]
            out["macros"] = [{"id": a["id"], "name": a["name"]} for a in autos if a["source"] == "macro"]
        except Exception:
            out["scenarios"], out["macros"] = [], []
        try:
            from . import assistant_catalog as catalog
            out["actions"] = [{"id": a["id"], "title": (a.get("title") or {}).get("en" if lang == "en" else "ru") or a["id"],
                               "tier": a.get("tier")} for a in catalog.ACTIONS if not str(a.get("id", "")).startswith("_")][:200]
        except Exception:
            out["actions"] = []
        try:
            from . import mcp_hub
            out["mcp"] = [{"name": r["name"], "tools": r["tools"]} for r in mcp_hub.scoped("flow")]
        except Exception:
            out["mcp"] = []
        try:
            from .schedule_integrations import catalog
            out["integrations"] = await asyncio.to_thread(catalog)
        except Exception:
            out["integrations"] = []
        try:
            from . import teams
            out["teams"] = [{"id": t["id"], "name": t["name"], "count": len(t.get("members") or [])}
                            for t in teams.list_teams() if isinstance(t, dict) and t.get("id")]
        except Exception:
            out["teams"] = []
        try:
            from .pgdata import load_preferences
            prefs = await asyncio.to_thread(load_preferences)
            out["decision_model"] = str(prefs.get("decision_model") or "")
            out["telegram"] = bool(prefs.get("telegram_enabled") and prefs.get("telegram_chat_id"))
        except Exception:
            out["decision_model"], out["telegram"] = "", False
        return out

    @app.post("/api/plan/flow/of")
    async def flow_of(request: Request):
        body = await _json(request)
        lang = _lang(body)
        task = body.get("task") if isinstance(body.get("task"), dict) else {}
        try:
            steps = fe.task_to_flow(task)
        except fe.FlowError as exc:
            return _err(exc, lang)
        return {"steps": steps, "numbers": fe.numbering(steps), "convertible": fe.legacy_convertible(task),
                "legacy": not fe.is_flow(task)}

    @app.post("/api/plan/flow/validate")
    async def flow_validate(request: Request):
        body = await _json(request)
        lang = _lang(body)
        try:
            steps = fe.normalize_steps(body.get("steps") or [])
        except fe.FlowError as exc:
            return _err(exc, lang, 422)
        return {"steps": steps, "numbers": fe.numbering(steps), "problems": fe.validate_flow(steps, lang)}

    @app.post("/api/plan/flow/vars")
    async def flow_vars(request: Request):
        body = await _json(request)
        lang = _lang(body)
        try:
            steps = fe.normalize_steps(body.get("steps") or [])
        except fe.FlowError as exc:
            return _err(exc, lang, 422)
        return {"vars": fe.variables_for(steps, str(body.get("before") or ""), lang)}

    @app.post("/api/plan/flow/test")
    async def flow_test(request: Request):
        body = await _json(request)
        lang = _lang(body)
        task = dict(body.get("task") if isinstance(body.get("task"), dict) else {})
        try:
            if task.get("steps"):
                task["steps"] = fe.normalize_steps(task["steps"])
            run = fe.new_run(task, trigger={"trigger": "manual", "payload": body.get("payload") or {}, "test": True},
                             dry_run=True, stepwise=bool(body.get("stepwise")), lang=lang)
        except fe.FlowError as exc:
            return _err(exc, lang)
        services = fe.Services(completion=completion, lang=lang)
        await asyncio.to_thread(fe.save_run, run)
        fe.start_background(task, run, services, record=False)
        return {"ok": True, "id": run["id"]}

    @app.post("/api/plan/flow/continue")
    async def flow_continue(request: Request):
        body = await _json(request)
        ok = await asyncio.to_thread(fe.continue_run, str(body.get("id") or ""), all_steps=bool(body.get("all")))
        return {"ok": ok}

    @app.post("/api/plan/flow/cancel")
    async def flow_cancel(request: Request):
        body = await _json(request)
        ok = await asyncio.to_thread(fe.cancel_run, str(body.get("id") or ""))
        return {"ok": ok}

    @app.get("/api/plan/flow/run")
    async def flow_run(id: str = ""):
        run = await asyncio.to_thread(fe.load_run, id)
        if not run:
            return JSONResponse({"error": "not found"}, 404)
        run = dict(run)
        run.pop("done", None)
        run.pop("seen_marks", None)
        run["numbers"] = fe.numbering(run.get("steps") or [])
        return run

    @app.get("/api/plan/flow/runs")
    async def flow_runs(task_id: str = "", limit: int = 30):
        rows = await asyncio.to_thread(fe.list_runs, task_id, max(1, min(100, limit)))
        return {"runs": rows}

    @app.get("/api/plan/flow/automations")
    async def flow_automations(lang: str = ""):
        lang = lang if lang in ("ru", "en") else _lang({})
        from . import flow_adapters
        return {"items": await asyncio.to_thread(flow_adapters.list_automations, lang)}

    @app.post("/api/plan/flow/as-task")
    async def flow_as_task(request: Request):
        body = await _json(request)
        lang = _lang(body)
        from . import flow_adapters
        items = await asyncio.to_thread(flow_adapters.list_automations, lang)
        item = next((a for a in items if a["source"] == body.get("source") and a["id"] == body.get("id")), None)
        if not item:
            return JSONResponse({"error": "Not found" if lang == "en" else "Не найдено"}, 404)
        return {"task": flow_adapters.as_task(item, lang)}

    @app.post("/api/plan/flow/forget-seen")
    async def flow_forget_seen(request: Request):
        body = await _json(request)
        await asyncio.to_thread(fe.forget_seen, str(body.get("task_id") or ""))
        return {"ok": True}
