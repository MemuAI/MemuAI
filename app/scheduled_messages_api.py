# -*- coding: utf-8 -*-
"""Round 29: routes of the scheduled-message queue for the MemuAI window (``/api/schedsend/*``).

The window process owns the active profile; MemuAI Proxy reads the same files
(:mod:`app.scheduled_messages_runner`). Every write is one atomic transaction in
:mod:`app.scheduled_messages`.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import scheduled_messages as sm

def _lang(body: Dict[str, Any] | None = None) -> str:
    if body and body.get("lang") in ("ru", "en"):
        return str(body["lang"])
    try:
        from .pgdata import load_preferences
        return "en" if load_preferences().get("language") == "en" else "ru"
    except Exception:
        return "ru"

def _error(exc: Exception, lang: str, status: int = 400) -> JSONResponse:
    if isinstance(exc, sm.ScheduleError):
        return JSONResponse({"error": exc.text(lang)}, status)
    return JSONResponse({"error": str(exc)[:400] or type(exc).__name__}, status)

def _policy() -> str:
    from .pgdata import load_preferences
    return str(load_preferences().get("schedsend_missed") or "send")

def _active_profile() -> str:
    try:
        from .pgdata import load_profiles
        return str(load_profiles().get("active_id") or "default")
    except Exception:
        return "default"

async def _json(request: Request) -> Dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        body = {}
    return body if isinstance(body, dict) else {}

def register(app: FastAPI) -> None:
    @app.get("/api/schedsend/list")
    async def schedsend_list(chat_id: str = "", data: int = 0):
        try:
            rows = await asyncio.to_thread(sm.load)
            await asyncio.to_thread(sm.release_stale)
        except Exception as exc:
            return _error(exc, _lang())
        chosen = [row for row in rows if not chat_id or row["chat_id"] == chat_id]
        chosen.sort(key=lambda row: (row["status"] not in sm.ACTIVE_STATUSES,
                                     row["due_at"] if row["trigger"] == "time" else 0))
        return {"items": [sm.public_item(row, with_data=bool(data)) for row in chosen],
                "summary": sm.summary(rows), "rev": sm.revision(), "now": sm.now_ms()}

    @app.get("/api/schedsend/item/{item_id}")
    async def schedsend_item(item_id: str):
        rows = await asyncio.to_thread(sm.load)
        row = next((row for row in rows if row["id"] == item_id), None)
        if row is None:
            return JSONResponse({"error": "not found"}, 404)
        return {"item": sm.public_item(row, with_data=True)}

    @app.post("/api/schedsend/create")
    async def schedsend_create(request: Request):
        body = await _json(request)
        lang = _lang(body)
        try:
            item = await asyncio.to_thread(sm.create, body.get("item") if isinstance(body.get("item"), dict) else body)
        except Exception as exc:
            return _error(exc, lang)
        return {"item": sm.public_item(item), "rev": sm.revision()}

    @app.post("/api/schedsend/update")
    async def schedsend_update(request: Request):
        body = await _json(request)
        lang = _lang(body)
        patch = body.get("patch") if isinstance(body.get("patch"), dict) else {}
        try:
            item = await asyncio.to_thread(sm.update, str(body.get("id") or ""), patch)
        except Exception as exc:
            return _error(exc, lang)
        return {"item": sm.public_item(item), "rev": sm.revision()}

    @app.post("/api/schedsend/delete")
    async def schedsend_delete(request: Request):
        body = await _json(request)
        try:
            removed = await asyncio.to_thread(sm.delete, str(body.get("id") or ""))
        except Exception as exc:
            return _error(exc, _lang(body))
        return {"ok": removed, "rev": sm.revision()}

    @app.post("/api/schedsend/send-now")
    async def schedsend_send_now(request: Request):
        body = await _json(request)
        try:
            item = await asyncio.to_thread(sm.send_now, str(body.get("id") or ""))
        except Exception as exc:
            return _error(exc, _lang(body))
        return {"item": sm.public_item(item), "rev": sm.revision()}

    @app.post("/api/schedsend/approve")
    async def schedsend_approve(request: Request):
        body = await _json(request)
        try:
            item = await asyncio.to_thread(sm.approve_missed, str(body.get("id") or ""))
        except Exception as exc:
            return _error(exc, _lang(body))
        return {"item": sm.public_item(item), "rev": sm.revision()}

    @app.post("/api/schedsend/claim")
    async def schedsend_claim(request: Request):
        body = await _json(request)
        try:
            result = await asyncio.to_thread(sm.claim, str(body.get("id") or ""), "desktop", policy=_policy())
        except Exception as exc:
            return _error(exc, _lang(body))
        item = result.get("item")
        return {"action": result.get("action", "none"),
                "item": sm.public_item(item, with_data=True) if item else None, "rev": sm.revision()}

    @app.post("/api/schedsend/complete")
    async def schedsend_complete(request: Request):
        body = await _json(request)
        try:
            item = await asyncio.to_thread(
                sm.complete, str(body.get("id") or ""), bool(body.get("ok")),
                error=str(body.get("error") or ""), answer_error=str(body.get("answer_error") or ""),
                model_used=str(body.get("model") or ""), by="desktop", retry=bool(body.get("retry")))
        except Exception as exc:
            return _error(exc, _lang(body))
        return {"item": sm.public_item(item) if item else None, "rev": sm.revision()}

    @app.post("/api/schedsend/delegate")
    async def schedsend_delegate(request: Request):
        body = await _json(request)
        try:
            item = await asyncio.to_thread(sm.delegate, str(body.get("id") or ""))
        except Exception as exc:
            return _error(exc, _lang(body))
        return {"item": sm.public_item(item), "rev": sm.revision()}

    @app.post("/api/schedsend/ack")
    async def schedsend_ack(request: Request):
        body = await _json(request)
        ids = body.get("ids") if isinstance(body.get("ids"), list) else []
        count = await asyncio.to_thread(sm.acknowledge, [str(value) for value in ids])
        return {"ok": True, "count": count, "rev": sm.revision()}

    @app.post("/api/schedsend/drop-chat")
    async def schedsend_drop_chat(request: Request):
        body = await _json(request)
        count = await asyncio.to_thread(sm.drop_chat, str(body.get("chat_id") or ""))
        return {"ok": True, "count": count, "rev": sm.revision()}

    @app.post("/api/schedsend/heartbeat")
    async def schedsend_heartbeat(request: Request):
        body = await _json(request)
        if body.get("closing"):
            await asyncio.to_thread(sm.clear_presence)
            return {"ok": True}
        try:
            await asyncio.to_thread(sm.write_presence, profile_id=_active_profile())
        except Exception:
            pass
        return {"ok": True, "rev": sm.revision(), "now": sm.now_ms()}

    @app.get("/api/schedsend/rev")
    async def schedsend_rev():
        return {"rev": sm.revision(), "now": sm.now_ms()}
