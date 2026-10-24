"""Round 29 — HTTP side of the stuck detector (app/stuck_detector.py).

* ``GET  /api/stuck/state``  → ``{sensitivity, pauses: [{source, card, task_id?, goal?}]}`` — every agent run that
  is paused right now (Work Browser, Computer Use, Do For You).  Quick Access and the Mini App use it for a badge.
* ``POST /api/stuck/decide`` ``{source: browser|computer|dfy, task_id?, card_id?, decision, hint?}`` — the user's
  verdict: ``continue`` | ``hint`` (``hint`` text is injected as a user note) | ``replan`` | ``stop``.

DFY pauses are ordinary DFY questions (``question.kind == "stuck"``), so ``POST /api/dfy/tasks/{id}/answer`` with
``{decision, hint}`` works as well; this route is a single entry point for every source.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# The route signatures below are strings (PEP 563, ``from __future__ import annotations``) and FastAPI resolves them in
# THIS module's globals. With ``Request`` imported only inside register(), ``request: Request`` became an unknown name,
# FastAPI read it as a required query parameter and every «Продолжить / С подсказкой / Сменить подход» answered HTTP 422.
try:
    from fastapi import Request
except Exception:  # pragma: no cover - FastAPI is a hard dependency of the server
    Request = Any  # type: ignore

from .stuck_detector import DECISIONS, PREF_KEY, normalize_sensitivity, public_pause, gate_of


def _sensitivity() -> str:
    try:
        from .pgdata import load_preferences
        return normalize_sensitivity(load_preferences().get(PREF_KEY))
    except Exception:
        return normalize_sensitivity(None)


def sensitivity() -> str:
    """The current setting (used by the run routes)."""
    return _sensitivity()


def collect_pauses(app: Any, runtimes: Dict[str, Any]) -> list:
    out = []
    for source, runtime in runtimes.items():
        card = public_pause(runtime)
        if card:
            out.append({"source": source, "card": card, "task": str(getattr(runtime, "task", "") or "")[:300]})
    manager = getattr(getattr(app, "state", None), "dfy_manager", None)
    if manager is not None:
        try:
            for task in list(getattr(manager, "tasks", {}).values()):
                question = getattr(task, "question", None) or {}
                if task.status == "waiting_user" and question.get("kind") == "stuck":
                    out.append({"source": "dfy", "task_id": task.id, "goal": str(task.goal)[:300],
                                "card": dict(question.get("card") or {}), "question_id": question.get("id")})
        except Exception:
            pass
    return out


def register(app: Any, *, runtimes: Optional[Dict[str, Any]] = None) -> None:
    from fastapi import Request
    from fastapi.responses import JSONResponse
    runtimes = dict(runtimes or {})

    @app.get("/api/stuck/state")
    async def stuck_state():
        return {"sensitivity": _sensitivity(), "pauses": collect_pauses(app, runtimes)}

    @app.post("/api/stuck/decide")
    async def stuck_decide(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            return JSONResponse({"error": "bad request"}, 400)
        source = str(body.get("source") or "").lower()
        decision = str(body.get("decision") or "").lower()
        if decision and decision not in DECISIONS:
            return JSONResponse({"error": "unknown decision"}, 400)
        payload = {"decision": decision, "hint": str(body.get("hint") or "")[:2000],
                   "answer": str(body.get("answer") or "")[:2000], "card_id": body.get("card_id")}
        if source in runtimes:
            gate = gate_of(runtimes[source])
            try:
                result = gate.decide(payload)
            except RuntimeError as exc:
                return JSONResponse({"error": str(exc)}, 409)
            return {"ok": True, **result}
        if source == "dfy":
            manager = getattr(app.state, "dfy_manager", None)
            if manager is None:
                return JSONResponse({"error": "DFY is unavailable"}, 404)
            try:
                task = manager.get(str(body.get("task_id") or ""))
                question = getattr(task, "question", None) or {}
                if task is None or question.get("kind") != "stuck":
                    return JSONResponse({"error": "this pause is already over"}, 409)
                await manager.answer(task.id, payload)
            except (KeyError, RuntimeError) as exc:
                return JSONResponse({"error": str(exc)}, 409)
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, 400)
            return {"ok": True}
        return JSONResponse({"error": "unknown source"}, 400)
