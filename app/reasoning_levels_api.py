"""Round 33 (r33-thinking): admin routes for per-model reasoning levels (Proxy → model card).

``GET  /api/admin/reasoning/profile?model=<id>``  what the model supports: ``effective`` (what clients see),
                                                  ``auto`` (the documented / guessed profile), ``override``,
                                                  ``learned`` (values the API rejected)
``POST /api/admin/reasoning/override``            ``{"model": id, "override": {kind, levels, can_off, default,
                                                  dialect} | null}`` — set by hand or return to automatic
``POST /api/admin/reasoning/forget``              ``{"model": id}`` — forget the values the API rejected

The logic lives in :mod:`app.reasoning_levels`; these routes only check the panel session.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import reasoning_levels as rl


def _err(message: str, message_en: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message, "error_en": message_en}, status_code=status)


def _catalog_id(model_id: str) -> str:
    """The catalogue's own spelling of the id (routing is case-insensitive; stored keys are not)."""
    try:
        from .state import state
        entry = state.find_model(model_id)
        return str(getattr(entry, "id", "") or model_id)
    except Exception:
        return model_id


def _describe(model_id: str) -> Dict[str, Any]:
    from .state import state
    entry = state.find_model(model_id)
    model_id = str(getattr(entry, "id", "") or model_id)
    provider = getattr(entry, "provider", "") if entry is not None else ""
    raw = getattr(entry, "model", "") if entry is not None else model_id
    effective = rl.profile(model_id, provider, raw)
    auto = rl.profile(model_id, provider, raw, use_override=False)
    return {"model": model_id, "provider": provider, "known": entry is not None,
            "effective": rl.public(effective), "auto": rl.public(auto),
            "dialect": rl.dialect_for(effective, _base_url(entry)) if provider == "custom" else provider,
            "override": rl.get_override(model_id), "learned": rl.learned(model_id),
            "depths": list(rl.DEPTHS), "kinds": list(rl.KINDS)}


def _base_url(entry: Any) -> str:
    """base_url of the custom connection a catalogue entry belongs to (its prefix is the connection name)."""
    try:
        from .state import state
        prefix = str(getattr(entry, "prefix", "") or "").lower()
        for account in state.accounts_of("custom"):
            fields = getattr(account, "fields", None) or {}
            if str(fields.get("name", "")).strip().lower().replace(" ", "-") == prefix:
                return str(fields.get("base_url") or "")
    except Exception:
        pass
    return ""


def register(app: FastAPI) -> None:
    from .admin_api import _auth, _json_object

    @app.get("/api/admin/reasoning/profile")
    async def reasoning_profile(request: Request):
        deny = _auth(request)
        if deny:
            return deny
        model_id = str(request.query_params.get("model") or "").strip()
        if not model_id or len(model_id) > 512:
            return _err("Нужен id модели", "A model id is required")
        return JSONResponse(await asyncio.to_thread(_describe, model_id))

    @app.post("/api/admin/reasoning/override")
    async def reasoning_override(request: Request):
        deny = _auth(request)
        if deny:
            return deny
        body, error = await _json_object(request)
        if error is not None:
            return error
        model_id = str(body.get("model") or "").strip()
        if not model_id or len(model_id) > 512:
            return _err("Нужен id модели", "A model id is required")
        value = body.get("override")
        if value is not None and not isinstance(value, dict):
            return _err("override должен быть объектом или null", "override must be an object or null")
        await asyncio.to_thread(rl.set_override, _catalog_id(model_id), value)
        return JSONResponse(await asyncio.to_thread(_describe, model_id))

    @app.post("/api/admin/reasoning/forget")
    async def reasoning_forget(request: Request):
        deny = _auth(request)
        if deny:
            return deny
        body, error = await _json_object(request)
        if error is not None:
            return error
        model_id = str(body.get("model") or "").strip()
        if not model_id:
            return _err("Нужен id модели", "A model id is required")
        await asyncio.to_thread(rl.forget_learned, _catalog_id(model_id))
        return JSONResponse(await asyncio.to_thread(_describe, model_id))
