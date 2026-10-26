"""HTTP routes of the wake word (Round 27) — ``/api/wake/*``; a Quick Ask backend plugin (see plugins/index.json).

Settings page (``quick/plugins/wake-settings.js``): ``GET /state``, ``POST /settings``, ``POST /phrase``
(live check), ``POST /model {action: download|cancel|delete, lang}``, ``POST /engine/install``, ``POST /test``
+ ``GET /status`` (the «Test» meter). Quick Ask window (``quick/plugins/wake.js``): ``GET /session``,
``POST /session/end``, ``POST /busy`` (mute while the window speaks or records voice input).
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import threading
from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import wake_audio, wake_word
from .wake_word import SERVICE

router = APIRouter(prefix="/api/wake")
_install: Dict[str, Any] = {"state": "idle", "error": ""}

async def _body(request: Request) -> Dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}

def _state() -> Dict[str, Any]:
    settings = SERVICE.settings
    return {"settings": settings, "status": SERVICE.status(), "models": SERVICE.models(),
            "engine": dict(wake_word.engine_info(), install=dict(_install)), "mic": wake_audio.list_devices(),
            "defaults": {lang: meta["phrase"] for lang, meta in wake_word.MODELS.items()},
            "ranges": {"sensitivity": wake_word.SENSITIVITY_RANGE, "cooldown": wake_word.COOLDOWN_RANGE}}

@router.get("/state")
async def wake_state():

    await asyncio.to_thread(SERVICE.ensure_loaded)
    return await asyncio.to_thread(_state)

@router.post("/settings")
async def wake_settings(request: Request):
    body = await _body(request)
    patch = body.get("settings") if isinstance(body.get("settings"), dict) else body
    await asyncio.to_thread(wake_word.save_settings, patch)
    await asyncio.to_thread(SERVICE.reload)
    return await asyncio.to_thread(_state)

@router.post("/phrase")
async def wake_phrase(request: Request):
    body = await _body(request)
    lang = str(body.get("lang") or SERVICE.settings["lang"])
    try:
        return await asyncio.to_thread(SERVICE.check_phrase, str(body.get("phrase") or ""), lang)
    except Exception as exc:
        return JSONResponse({"error": str(exc)[:300]}, 500)

@router.post("/model")
async def wake_model(request: Request):
    body = await _body(request)
    lang, action = str(body.get("lang") or ""), str(body.get("action") or "")
    if lang not in wake_word.MODELS:
        return JSONResponse({"error": "unknown language", "code": "lang"}, 400)
    if action == "download":
        SERVICE.start_download(lang)
    elif action == "cancel":
        SERVICE.cancel_download(lang)
    elif action == "delete":
        try:
            await asyncio.to_thread(SERVICE.delete_model, lang)
        except RuntimeError:
            return JSONResponse({"error": "Дождитесь окончания загрузки", "error_en": "Wait for the download to finish",
                                 "code": "busy"}, 409)
    else:
        return JSONResponse({"error": "unknown action", "code": "action"}, 400)
    return await asyncio.to_thread(_state)

def _pip_install() -> None:
    _install.update(state="running", error="")
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        done = subprocess.run([sys.executable, "-m", "pip", "install", "vosk>=0.3.45,<0.4"], capture_output=True,
                              text=True, timeout=600, creationflags=flags)
        if done.returncode != 0:
            raise RuntimeError((done.stderr or done.stdout or "pip").strip().splitlines()[-1][:300])
        import importlib
        importlib.invalidate_caches()
        _install.update(state="done")
        SERVICE.apply()
    except Exception as exc:
        _install.update(state="error", error=str(exc)[:300])

@router.post("/engine/install")
async def wake_engine_install():
    info = wake_word.engine_info()
    if info["available"]:
        return await asyncio.to_thread(_state)
    if not info["installable"]:
        return JSONResponse({"error": "Переустановите MemuAI: компонент распознавания входит в установщик.",
                             "error_en": "Reinstall MemuAI: the speech component ships with the installer.",
                             "code": "frozen"}, 409)
    if _install["state"] != "running":
        threading.Thread(target=_pip_install, name="memuai-wake-pip", daemon=True).start()
        _install["state"] = "running"
    return await asyncio.to_thread(_state)

@router.post("/test")
async def wake_test(request: Request):
    body = await _body(request)
    await asyncio.to_thread(SERVICE.set_test, bool(body.get("on", True)))
    return SERVICE.status()

@router.get("/status")
async def wake_status():
    await asyncio.to_thread(SERVICE.ensure_loaded)
    return SERVICE.status()

@router.get("/session")
async def wake_session(audio: int = 0):
    return {"session": SERVICE.session_public(with_audio=bool(audio))}

@router.post("/session/end")
async def wake_session_end(request: Request):
    body = await _body(request)
    action = str(body.get("action") or "done")
    if action not in ("finish", "cancel", "done"):
        return JSONResponse({"error": "unknown action", "code": "action"}, 400)
    ok = SERVICE.end_session(body.get("id"), action)
    return {"ok": ok}

@router.post("/busy")
async def wake_busy(request: Request):
    body = await _body(request)
    try:
        seconds = float(body.get("seconds", 30 if body.get("on", True) else 0))
    except (TypeError, ValueError):
        seconds = 0.0
    if seconds != seconds:
        seconds = 0.0
    SERVICE.busy(str(body.get("source") or "voice"), seconds if body.get("on", True) else 0)
    return {"ok": True}

def _register() -> None:
    from . import quick_ask_api
    quick_ask_api.register_router(router)

_register()
