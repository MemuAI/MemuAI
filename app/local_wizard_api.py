"""Panel routes of the local model wizard (Round 33): /api/admin/local/wizard/*.

Thin wrappers around ``app/local_wizard.py`` with the panel session check of /api/admin.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .admin_api import _auth, _json_object

router = APIRouter(prefix="/api/admin/local/wizard")


def _error(exc: Exception, status: int = 400) -> JSONResponse:
    text = str(getattr(exc, "message", "") or exc)
    return JSONResponse({"error": text, "error_en": str(getattr(exc, "message_en", "") or text)}, status_code=status)


@router.get("/hardware")
async def wizard_hardware(request: Request, refresh: int = 0):
    deny = _auth(request)
    if deny:
        return deny
    from . import local_hw, local_models
    profile = await asyncio.to_thread(local_hw.detect, bool(refresh), None, local_models.models_dir())
    runtime = await asyncio.to_thread(local_models.runtime_status)
    return {"hardware": profile, "runtime": {"installed": runtime["installed"], "tag": runtime["tag"],
                                             "backend": runtime["backend"], "platform": runtime["platform"],
                                             "install": runtime["install"]}}


@router.post("/recommend")
async def wizard_recommend(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    body, error = await _json_object(request, max_bytes=65_536)
    if error:
        return error
    from . import local_wizard
    goals: Dict[str, Any] = body.get("goals") if isinstance(body.get("goals"), dict) else {}
    hardware = body.get("hardware") if isinstance(body.get("hardware"), dict) else None
    try:
        return await asyncio.to_thread(local_wizard.recommend, goals, hardware)
    except (ValueError, TypeError) as exc:
        return _error(exc)


@router.post("/install")
async def wizard_install(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    body, error = await _json_object(request, max_bytes=65_536)
    if error:
        return error
    from . import local_models, local_wizard
    preset = body.get("preset") if isinstance(body.get("preset"), dict) else None
    try:
        context = int(body.get("context") or 0)
    except (TypeError, ValueError):
        context = 0
    try:
        return await asyncio.to_thread(local_wizard.install, str(body.get("id") or ""), str(body.get("quant") or ""),
                                       context, bool(body.get("vision", True)), bool(body.get("mtp")), None, preset)
    except local_models.LocalModelError as exc:
        return _error(exc)


@router.post("/catalog/refresh")
async def wizard_catalog_refresh(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    from . import local_wizard
    return local_wizard.start_refresh()


@router.get("/catalog/status")
async def wizard_catalog_status(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    from . import local_wizard
    catalog = await asyncio.to_thread(local_wizard.load_catalog)
    return {"refresh": local_wizard.refresh_status(), "version": catalog.get("version", ""),
            "origin": catalog.get("origin", ""), "count": len(catalog.get("models") or [])}


def register(app) -> None:
    app.include_router(router)
