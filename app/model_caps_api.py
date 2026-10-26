"""Admin routes for model capabilities and the full model card (Round 22).

``GET  /api/admin/models/capabilities``       compact capabilities of every catalogue model (badges, filters)
``GET  /api/admin/models/catalog/status``     state of the public-catalogue copy (OpenRouter + Models.dev)
``POST /api/admin/models/catalog/refresh``    download both catalogues now (explicit user action)
``GET  /api/admin/models/{id}/info``          every field with source + confidence, raw JSON of each source
``POST /api/admin/models/{id}/probe``         one real request with a tiny picture: ``{"confirm": true}``;
                                              ``{"action": "forget"}`` drops a stored probe result

The logic lives in :mod:`app.model_capabilities`; these routes only check the panel session.
Note: ``DELETE /api/admin/models/{id:path}`` already deletes a model, so nothing here uses DELETE.
"""
from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .admin_api import _auth, _json_object
from . import model_capabilities as caps

router = APIRouter(prefix="/api/admin")
_probe_running: set = set()

def _err(message: str, message_en: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message, "error_en": message_en}, status_code=status)

@router.get("/models/capabilities")
async def models_capabilities(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    started = time.perf_counter()
    data = await asyncio.to_thread(caps.all_capabilities)
    return JSONResponse({"capabilities": data, "catalog": caps.catalog_status(),
                         "ms": int((time.perf_counter() - started) * 1000)})

@router.get("/models/catalog/status")
async def models_catalog_status(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    return JSONResponse(caps.catalog_status())

@router.post("/models/catalog/refresh")
async def models_catalog_refresh(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    result = await asyncio.to_thread(caps.refresh_catalog)
    result["catalog"] = caps.catalog_status()
    return JSONResponse(result, status_code=200 if result.get("ok") else 502)

@router.get("/models/{model_id:path}/info")
async def model_card(model_id: str, request: Request):
    deny = _auth(request)
    if deny:
        return deny
    from .state import state
    entry = state.find_model(model_id)
    if entry is None and request.query_params.get("any") != "1":
        return _err("Модель не найдена в каталоге Proxy", "Model not found in the Proxy catalog", 404)
    card = await asyncio.to_thread(caps.describe, model_id, entry=entry)
    return JSONResponse(card)

@router.post("/models/{model_id:path}/probe")
async def model_probe(model_id: str, request: Request):
    deny = _auth(request)
    if deny:
        return deny
    body, error = await _json_object(request, max_bytes=4096)
    if error is not None:
        return error
    from .state import state
    entry = state.find_model(model_id)
    if entry is None:
        return _err("Модель не найдена в каталоге Proxy", "Model not found in the Proxy catalog", 404)
    if body.get("action") == "forget":
        return {"ok": True, "forgotten": caps.forget_probe(entry.id)}
    if body.get("confirm") is not True:

        return _err("Проверка тратит настоящий запрос — нужно подтверждение", "The probe spends a real request — confirmation required", 400)
    if entry.id in _probe_running:
        return _err("Проверка этой модели уже идёт", "This model is already being probed", 409)
    _probe_running.add(entry.id)
    try:
        result = await caps.probe_vision(entry.id)
    finally:
        _probe_running.discard(entry.id)
    result["summary"] = caps.capabilities_for(entry.id, entry=entry)
    return result

@router.post("/models/{model_id:path}/override")
async def model_override(model_id: str, request: Request):
    """Round 24: capabilities set by hand — {vision|tools|reasoning: true|false|null, context|max_output: int|null}."""
    deny = _auth(request)
    if deny:
        return deny
    body, error = await _json_object(request, max_bytes=4096)
    if error is not None:
        return error
    from .state import state
    entry = state.find_model(model_id)
    if entry is None:
        return _err("Модель не найдена в каталоге Proxy", "Model not found in the Proxy catalog", 404)
    override = await asyncio.to_thread(caps.set_override, entry.id, body)
    return {"ok": True, "override": override, "summary": caps.capabilities_for(entry.id, entry=entry)}
