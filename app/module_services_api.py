"""The generic background/media bridge; no built-in module branches."""
from __future__ import annotations

import asyncio
import json
import re

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from . import module_media, module_monitor, music_runtime, pgdata

router = APIRouter()
_MUSIC_JOBS = {}


@router.post("/api/pg/modules/monitor")
async def monitor(request: Request):
    try:
        body = await request.json()
        module_id = str(body.get("module_id", ""))
        module_monitor.require(module_id, "background", "web", "notify")
        action = body.get("action", "status")
        if action == "configure":
            return await asyncio.to_thread(module_monitor.configure, module_id, body)
        if action == "scan":
            return await module_monitor.scan(module_id, force=True)
        if action == "status":
            return await asyncio.to_thread(module_monitor.status, module_id)
        if action == "events":
            return await asyncio.to_thread(module_monitor.events, max(0, int(body.get("after", 0))), module_id)
        if action == "seen":
            # The module marked its notifications as seen (persisted in the profile store).
            return await asyncio.to_thread(module_monitor.mark_seen, module_id, int(body.get("cursor", 0)))
        raise ValueError("Неизвестное действие / Unknown action")
    except PermissionError as exc:
        return JSONResponse({"error": str(exc)}, 403)
    except Exception as exc:
        return JSONResponse({"error": str(exc) if isinstance(exc, ValueError) else type(exc).__name__}, 400)


@router.get("/api/pg/modules/events")
async def events(after: int = 0, pending: int = 0):
    # pending=1: only events MemuAI has not shown yet; the acknowledgement is
    # stored server-side (the desktop WebView's localStorage does not survive a restart).
    if pending:
        return await asyncio.to_thread(module_monitor.pending_events)
    return await asyncio.to_thread(module_monitor.events, max(0, after))


@router.post("/api/pg/modules/events/ack")
async def events_ack(request: Request):
    try:
        body = await request.json()
        return await asyncio.to_thread(module_monitor.acknowledge, int(body.get("cursor", 0)))
    except Exception as exc:
        return JSONResponse({"error": str(exc) if isinstance(exc, ValueError) else type(exc).__name__}, 400)


@router.get("/api/pg/modules/ui-hints")
async def ui_hints():
    # Round 31: one-time hints of the module windows (how to close / minimize).
    from . import modules_host
    return await asyncio.to_thread(modules_host.load_ui_hints, modules_host.default_root())


@router.post("/api/pg/modules/ui-hints")
async def ui_hints_mark(request: Request):
    from . import modules_host
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("An object is expected / Ожидается объект")
        return await asyncio.to_thread(modules_host.mark_ui_hint, modules_host.default_root(),
                                       str(body.get("name", "")), body.get("seen", True) is not False)
    except Exception as exc:
        return JSONResponse({"error": str(exc) if isinstance(exc, ValueError) else type(exc).__name__}, 400)


@router.post("/api/pg/modules/music")
async def music(request: Request):
    try:
        body = await request.json()
        module_id = str(body.get("module_id", ""))
        module_monitor.require(module_id, "media")
        if body.get("action") in ("models", "config"):
            return {"models": music_runtime.models()}
        if body.get("action") == "configure":
            raise ValueError("Музыкальные подключения настраиваются в MemuAI Proxy → Аккаунты / Configure music in MemuAI Proxy → Accounts")
        request_id = str(body.get("request_id", ""))[:100]
        identity = (module_id, request_id)
        if body.get("action") == "cancel":
            task = _MUSIC_JOBS.get(identity)
            if task:
                task.cancel()
            return {"ok": True, "cancelled": bool(task)}
        if identity in _MUSIC_JOBS:
            raise ValueError("Генерация уже выполняется / Generation already running")
        module_monitor.require(module_id, "media", enabled=True)
        task = asyncio.create_task(music_runtime.generate(body, guard=lambda: module_monitor.require(module_id, "media", enabled=True)) if body.get("model")
                                   else module_media.generate_music(module_id, body))
        _MUSIC_JOBS[identity] = task
        try:
            return await task
        finally:
            _MUSIC_JOBS.pop(identity, None)
    except asyncio.CancelledError:
        return JSONResponse({"error": "Генерация отменена / Generation cancelled"}, 409)
    except PermissionError as exc:
        return JSONResponse({"error": str(exc)}, 403)
    except Exception as exc:
        message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        return JSONResponse({"error": message}, 400)


@router.post("/api/voice/clips")
async def save_voice_clip(request: Request):
    try:
        limit = module_media.MAX_AUDIO * 4 // 3 + 10000
        if int(request.headers.get("content-length", 0)) > limit:
            return JSONResponse({"error": "Recording exceeds 24 MB"}, 413)
        # A chunked upload has no Content-Length: enforce the limit while reading.
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > limit:
                return JSONResponse({"error": "Recording exceeds 24 MB"}, 413)
        body = json.loads(bytes(raw) or b"{}")
        if not isinstance(body, dict):
            raise ValueError("Invalid request")
        profile_id = str(body.get("profile_id") or pgdata.load_profiles().get("active_id", "default"))
        if profile_id != pgdata.load_profiles().get("active_id", "default"):
            return JSONResponse({"error": "Profile changed"}, 409)
        return await asyncio.to_thread(module_media.save_clip, str(body.get("audio", "")),
                profile_id=profile_id, duration=body.get("duration", 0), truncated=body.get("truncated", False))
    except Exception as exc:
        return JSONResponse({"error": str(exc) if isinstance(exc, ValueError) else type(exc).__name__}, 400)


@router.get("/api/voice/clips/{name}")
async def voice_clip(name: str, profile_id: str = ""):
    if not re.fullmatch(r"[0-9a-f]{64}\.(wav|mp3|ogg|webm|m4a|flac)", name):
        return JSONResponse({"error": "Clip not found"}, 404)
    active = pgdata.load_profiles().get("active_id", "default")
    if profile_id and profile_id != active:
        return JSONResponse({"error": "Profile changed"}, 403)
    target = module_media.clip_folder(active) / name
    if not target.is_file() or target.is_symlink():
        return JSONResponse({"error": "Clip not found"}, 404)
    return FileResponse(target, media_type=next(mime for mime, suffix in module_media.MIMES.items()
                                              if suffix == name.rsplit(".", 1)[1]),
                        headers={"Cache-Control": "private, max-age=3600"})
