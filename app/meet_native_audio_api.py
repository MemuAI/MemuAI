# -*- coding: utf-8 -*-
"""MemuMeet native system-audio capture: Playground API routes.

The helper (``meet_native_audio_helper.py``) runs in this process, in a
daemon thread, on a loopback port with a per-process token: no extra
executable is needed in the frozen Windows build. The host window uses
same-origin session routes; the local helper's token stays server-side
on that path.

``GET  /api/pg/meet/native-audio/status`` — ``{available, reason,
backend, devices}``; starts the helper lazily on first call.
``POST /api/pg/meet/native-audio/start``  — ``{port, token, base_url}``.
``POST /api/pg/meet/native-audio/stop``   — stop the helper (idle).

Auth is the same loopback guard as the other Playground routes: a
keyless loopback UI call passes, a cross-site website gets a 401. The
token is random per process and never persisted; the helper only binds
127.0.0.1.
"""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Any, Dict, Optional

import httpx
import anyio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .meet_native_audio import NativeAudioError
from .meet_native_audio_helper import NativeAudioServer, describe_backend

IDLE_SHUTDOWN_S = 120.0


class _Manager:
    """One helper server per process, started on demand."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._server: Optional[NativeAudioServer] = None
        self._watchdog: Optional[threading.Thread] = None

    def ensure(self, *, refresh: bool = False) -> NativeAudioServer:
        with self._lock:
            if refresh and self._server is not None:
                session = self._server._session
                if session is None or session.stopped:
                    self._server.stop()
                    self._server = None
            if self._server is None:
                backend = "sine" if _sine_forced() else "auto"
                self._server = NativeAudioServer(backend=backend).start()
                if self._watchdog is None:
                    self._watchdog = threading.Thread(target=self._watch, daemon=True,
                                                      name="meet-native-watchdog")
                    self._watchdog.start()
            return self._server

    def _watch(self) -> None:
        while True:
            time.sleep(10.0)
            with self._lock:
                server = self._server
                if server is None:
                    continue
                idle = time.monotonic() - server.last_used
                busy = server._session is not None and not server._session.stopped  # noqa: SLF001
                if idle > IDLE_SHUTDOWN_S and not busy:
                    try:
                        server.stop()
                    except Exception:
                        pass
                    self._server = None

    def stop(self) -> None:
        with self._lock:
            if self._server is not None:
                try:
                    self._server.stop()
                except Exception:
                    pass
                self._server = None


def _sine_forced() -> bool:
    import os
    return os.environ.get("MEMUAI_MEET_NATIVE_BACKEND", "").strip().lower() == "sine"


MANAGER = _Manager()


def _auth(request: Request) -> Optional[JSONResponse]:
    from .config import config_store
    auth = request.headers.get("authorization", "")
    key = auth[7:].strip() if auth.lower().startswith("bearer ") else request.headers.get("x-api-key")
    if not config_store.authorize_api_request(
        key, host=request.headers.get("host", ""),
        origin=request.headers.get("origin", ""),
        fetch_site=request.headers.get("sec-fetch-site", ""),
        peer=request.client.host if request.client else "",
    ):
        return JSONResponse(status_code=401, content={"error": "Неверный API-ключ хаба."})
    return None


def register(app: FastAPI) -> None:
    async def proxy_session(request: Request, path: str):
        denied = _auth(request)
        if denied is not None:
            return denied
        body = await request.body() if request.method == "POST" else b""
        if len(body) > 65536:
            return JSONResponse({"code": "invalid_selection"}, status_code=413)
        client = httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(8, read=None if request.method == "GET" else 8))
        try:
            server = await asyncio.to_thread(MANAGER.ensure)
            upstream = client.build_request(
                request.method, server.base_url + path, content=body,
                headers={"Authorization": f"Bearer {server.token}", "Content-Type": "application/json"},
            )
            response = await client.send(upstream, stream=True)
            content_type = response.headers.get("Content-Type", "application/json")
            if request.method == "POST" or response.status_code != 200:
                content = await response.aread()
                await client.aclose()
                return Response(content, status_code=response.status_code, media_type=content_type)
        except Exception:
            await client.aclose()
            return JSONResponse({"code": "helper_unavailable"}, status_code=503)

        async def chunks():
            try:
                async for chunk in response.aiter_raw():
                    yield chunk
            finally:
                with anyio.CancelScope(shield=True):
                    await response.aclose()
                    await client.aclose()

        return StreamingResponse(chunks(), media_type=content_type, headers={"Cache-Control": "no-store"})

    @app.post("/api/pg/meet/native-audio/sessions")
    async def create_native_session(request: Request):
        return await proxy_session(request, "/sessions")

    @app.api_route("/api/pg/meet/native-audio/sessions/{session_id}/{action}", methods=["GET", "POST"])
    async def native_session_action(request: Request, session_id: str, action: str):
        import re
        actions = ("stream", "events") if request.method == "GET" else ("gain", "stop")
        if action not in actions or not re.fullmatch(r"ses-[a-f0-9]{12}", session_id):
            return JSONResponse({"code": "not_found"}, status_code=404)
        return await proxy_session(request, f"/sessions/{session_id}/{action}")

    @app.get("/api/pg/meet/native-audio/status")
    async def native_audio_status(request: Request) -> JSONResponse:
        denied = _auth(request)
        if denied is not None:
            return denied
        return JSONResponse(await asyncio.to_thread(status_payload, refresh=request.query_params.get("refresh") == "1"))

    @app.post("/api/pg/meet/native-audio/start")
    async def native_audio_start(request: Request) -> JSONResponse:
        denied = _auth(request)
        if denied is not None:
            return denied
        try:
            server = MANAGER.ensure()
        except Exception as exc:
            return JSONResponse(status_code=503,
                                content=NativeAudioError("helper_unavailable", str(exc)[:200]).as_dict())
        return JSONResponse({"port": server.port, "token": server.token,
                             "base_url": server.base_url})

    @app.post("/api/pg/meet/native-audio/stop")
    async def native_audio_stop(request: Request) -> JSONResponse:
        denied = _auth(request)
        if denied is not None:
            return denied
        MANAGER.stop()
        return JSONResponse({"ok": True})


def status_payload(*, refresh: bool = False) -> Dict[str, Any]:
    """The same availability answer without HTTP (for tests and embeds)."""
    try:
        server = MANAGER.ensure(refresh=refresh)
        devices = server.list_devices()
    except Exception as exc:
        return {"available": False, "reason": "helper_unavailable",
                "backend": "none", "devices": [], "detail": str(exc)[:200]}
    backend = server.backend
    reason = "" if devices else getattr(backend, "reason", "no_devices")
    payload = {"available": bool(devices), "reason": reason,
               "backend": backend.name, "devices": devices,
               "sounddevice": describe_backend(backend)}
    detail = str(getattr(backend, "detail", "") or "")
    if detail:
        payload["detail"] = detail[:500]
    return payload
