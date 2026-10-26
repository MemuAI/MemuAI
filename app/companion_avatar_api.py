"""HTTP routes of the animated companion (Round 22): ``/api/companion/*`` and ``/companion/stage``.

Registered by ``playground_api.create_app`` before the static mount. The
logic lives in :mod:`app.companion_avatar`; the desktop window manager, when
MemuAI runs on Windows, is ``app.state.companion_window``
(:mod:`app.companion_window`).

``/companion/stage`` is the renderer page that every surface embeds in an
``<iframe>`` (Live panel, Quick Ask) or opens directly (desktop window). It
is served here rather than by the static mount because it needs its own
headers: it may be framed by MemuAI's own pages (``frame-ancestors 'self'``,
``X-Frame-Options: SAMEORIGIN``) and Live2D Cubism Core is WebAssembly, so
the page allows ``'wasm-unsafe-eval'`` — WebAssembly compilation only, never
JavaScript ``eval``. The main window keeps its stricter policy.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict

from fastapi import Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

from . import companion_avatar as core

log = logging.getLogger("memuai.companion")

STAGE_CSP = (
    "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; connect-src 'self' data: blob:; media-src 'self' blob: data:; "
    "worker-src 'self' blob:; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
)
MAX_IMPORT = max(core.MAX_VRM_BYTES, core.MAX_ZIP_BYTES)
CURSOR_HZ = 30
CURSOR_HEARTBEAT = 10.0

async def cursor_events(read, hz: float = CURSOR_HZ, is_disconnected=None, heartbeat: float = CURSOR_HEARTBEAT,
                        clock=None, sleep=None):
    """SSE lines with the global cursor (physical px) for the companion's gaze (Round 25).

    Only CHANGES are sent (an idle mouse costs nothing on the wire), plus a heartbeat comment so a closed stage
    is noticed. ``read()`` → ``(x, y)`` / ``(x, y, relative)`` or ``None`` (skipped); a point relative to the
    hosting window's client area is sent as ``x,y,c``, an absolute screen point as ``x,y``.
    Ends when ``is_disconnected()`` says so."""
    clock = clock or asyncio.get_running_loop().time
    sleep = sleep or asyncio.sleep
    period = 1.0 / max(10.0, min(60.0, float(hz or CURSOR_HZ)))
    last, last_sent = None, clock()
    yield "retry: 3000\n\n"
    while True:
        if is_disconnected is not None and await is_disconnected():
            return
        try:
            pos = read()
        except Exception:  # noqa: BLE001 - a failing read only skips a tick
            pos = None
        now = clock()
        if pos is not None and pos != last:
            last, last_sent = pos, now
            yield f"data: {int(pos[0])},{int(pos[1])}{',c' if len(pos) > 2 and pos[2] else ''}\n\n"
        elif now - last_sent >= heartbeat:
            last_sent = now
            yield ": ping\n\n"
        await sleep(period)

CURSOR_HOSTS = ("desktop", "quick", "main")

def host_hwnd(state, host: str) -> int:
    """The HWND of the window that shows a stage (a4-companion-3): ``desktop`` — the desktop companion window,
    ``quick`` — Quick Access, ``main`` — the MemuAI window (Live, settings). 0 when unknown/not running."""
    if host == "desktop":
        owner = getattr(state, "companion_window", None)
    elif host == "quick":
        owner = getattr(state, "quick_ask", None)
    elif host == "main":
        return int(getattr(state, "native_hwnd", 0) or 0)
    else:
        return 0
    return int(getattr(owner, "hwnd", 0) or 0) if owner is not None else 0

def data_root() -> Path:
    from .pgdata import MEMU_DIR
    return Path(MEMU_DIR) / "companion"

async def _read_limited(request: Request, limit: int) -> bytes:
    try:
        declared = int(request.headers.get("content-length", "") or 0)
    except (TypeError, ValueError):
        declared = 0
    if declared > limit:
        raise core.ModelError("too_large", "Файл больше 200 МБ", "The file is larger than 200 MB")
    output = bytearray()
    async for chunk in request.stream():
        output.extend(chunk)
        if len(output) > limit:
            raise core.ModelError("too_large", "Файл больше 200 МБ", "The file is larger than 200 MB")
    return bytes(output)

def _error(exc: Exception, status: int = 400) -> JSONResponse:
    code = getattr(exc, "code", "error")

    english = getattr(exc, "message_en", "") or str(exc)
    return JSONResponse({"error": str(exc), "error_en": english, "code": code}, status)

def register(app) -> None:
    library = core.Library(data_root())
    app.state.companion_library = library
    if not hasattr(app.state, "companion_window"):
        app.state.companion_window = None
    install_lock = asyncio.Lock()

    def window_manager():
        return getattr(app.state, "companion_window", None)

    @app.get("/companion/stage")
    async def companion_stage():
        try:
            html = (core.STATIC_DIR / "stage.html").read_text(encoding="utf-8")
        except OSError:
            return JSONResponse({"error": "stage.html missing"}, 500)
        return HTMLResponse(html, headers={
            "Content-Security-Policy": STAGE_CSP,
            "X-Frame-Options": "SAMEORIGIN",
            "Cache-Control": "no-cache",
        })

    @app.get("/api/companion/models")
    async def companion_models():
        rows = await asyncio.to_thread(library.list)
        return {"models": rows, "runtime": core.runtime_status(data_root())}

    @app.post("/api/companion/models/import")
    async def companion_import(request: Request, filename: str = "", name: str = ""):
        try:
            data = await _read_limited(request, MAX_IMPORT)
            if not data:
                raise core.ModelError("empty", "Пустой файл", "Empty file")
            model = await asyncio.to_thread(library.import_bytes, filename, data, name)
        except core.ModelError as exc:
            return _error(exc, 413 if exc.code == "too_large" else 400)
        except Exception as exc:
            log.warning("companion import failed: %s", exc)
            return _error(exc, 500)
        return {"ok": True, "model": model}

    @app.patch("/api/companion/models/{model_id}")
    async def companion_rename(model_id: str, request: Request):
        try:
            body = await request.json()
            model = library.rename(model_id, str((body or {}).get("name") or ""))
        except core.ModelError as exc:
            return _error(exc, 404)
        except ValueError as exc:
            return _error(exc)
        return {"ok": True, "model": model}

    @app.delete("/api/companion/models/{model_id}")
    async def companion_delete(model_id: str):
        try:
            removed = await asyncio.to_thread(library.delete, model_id)
        except core.ModelError as exc:
            return _error(exc)
        if not removed:
            return JSONResponse({"error": "Модель не найдена", "error_en": "Model not found", "code": "not_found"}, 404)
        return {"ok": True}

    @app.get("/api/companion/models/{model_id}/files/{rel:path}")
    async def companion_model_file(model_id: str, rel: str):
        path = library.file(model_id, rel)
        if path is None:
            return JSONResponse({"error": "not found"}, 404)
        media = {".json": "application/json", ".png": "image/png", ".jpg": "image/jpeg",
                 ".jpeg": "image/jpeg", ".webp": "image/webp", ".vrm": "model/gltf-binary",
                 ".moc3": "application/octet-stream"}.get(path.suffix.lower(), "application/octet-stream")
        return FileResponse(str(path), media_type=media, headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/api/companion/runtime")
    async def companion_runtime():
        return core.runtime_status(data_root())

    @app.post("/api/companion/runtime/install")
    async def companion_runtime_install(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict) or body.get("consent") is not True:
            return JSONResponse({"error": "Нужно явное согласие с условиями Live2D", "code": "consent",
                                 "error_en": "Explicit consent to the Live2D terms is required"}, 400)
        async with install_lock:
            import httpx
            folder = data_root() / "runtime"
            folder.mkdir(parents=True, exist_ok=True)
            try:
                async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                    downloaded: Dict[str, bytes] = {}
                    for name, (url, _digest, _marker) in core.RUNTIME_FILES.items():

                        async with client.stream("GET", url) as response:
                            response.raise_for_status()
                            if not str(response.url).startswith("https://"):
                                raise core.ModelError("runtime_url", f"{name}: загрузка ушла не по HTTPS",
                                                      f"{name}: the download left HTTPS")
                            chunks = bytearray()
                            async for chunk in response.aiter_bytes():
                                chunks.extend(chunk)
                                if len(chunks) > core.RUNTIME_MAX_BYTES:
                                    raise core.ModelError("runtime_size", f"{name}: неожиданный размер файла",
                                                          f"{name}: unexpected file size")
                        data = bytes(chunks)
                        core.check_runtime_file(name, data)
                        downloaded[name] = data

                    for name, data in downloaded.items():
                        tmp = folder / (name + ".part")
                        tmp.write_bytes(data)
                        tmp.replace(folder / name)
            except core.ModelError as exc:
                return _error(exc, 502)
            except Exception as exc:
                return JSONResponse({"error": f"Не удалось скачать среду Live2D: {exc}", "code": "download",
                                     "error_en": f"Could not download the Live2D runtime: {exc}"}, 502)
        from .pgdata import save_preferences
        await asyncio.to_thread(save_preferences, {"companion_live2d_consent": True})
        return {"ok": True, **core.runtime_status(data_root())}

    @app.delete("/api/companion/runtime")
    async def companion_runtime_remove():
        folder = data_root() / "runtime"
        for name in core.RUNTIME_FILES:
            try:
                (folder / name).unlink()
            except OSError:
                pass
        return {"ok": True, **core.runtime_status(data_root())}

    @app.get("/api/companion/runtime/files/{name}")
    async def companion_runtime_file(name: str):
        path = core.runtime_file(data_root(), name)
        if path is None:
            return JSONResponse({"error": "not installed"}, 404)
        return FileResponse(str(path), media_type="text/javascript", headers={"Cache-Control": "no-cache"})

    @app.get("/api/companion/state")
    async def companion_state():
        return core.load_state(data_root())

    @app.post("/api/companion/state")
    async def companion_state_save(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        return core.save_state(data_root(), body if isinstance(body, dict) else {})

    def cursor_read(host: str):
        from . import companion_window
        if host not in CURSOR_HOSTS:
            return companion_window.cursor_reader()
        return companion_window.cursor_reader(lambda: host_hwnd(app.state, host))

    @app.get("/api/companion/cursor")
    async def companion_cursor(host: str = ""):
        read = cursor_read(host)
        pos = read() if read else None
        return {"supported": bool(read), "x": pos[0] if pos else None, "y": pos[1] if pos else None,
                "relative": bool(pos and pos[2])}

    @app.get("/api/companion/cursor/stream")
    async def companion_cursor_stream(request: Request, hz: float = CURSOR_HZ, host: str = ""):
        read = cursor_read(host)
        if read is None:
            return JSONResponse({"supported": False}, 404)
        return StreamingResponse(cursor_events(read, hz, request.is_disconnected), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.get("/api/companion/window")
    async def companion_window_status():
        manager = window_manager()
        return {"supported": bool(manager and manager.supported),
                "visible": bool(manager and manager.visible)}

    @app.post("/api/companion/window")
    async def companion_window(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        body = body if isinstance(body, dict) else {}
        manager = window_manager()
        if manager is None or not manager.supported:
            return {"ok": False, "supported": False}
        action = str(body.get("action") or "")
        try:
            result: Dict[str, Any] = await asyncio.to_thread(manager.handle, action, body)
        except Exception as exc:
            log.warning("companion window %s failed: %s", action, exc)
            return {"ok": False, "supported": True, "error": str(exc)}
        return {"ok": True, "supported": True, **(result or {})}
