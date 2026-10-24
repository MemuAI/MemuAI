"""HTTP contract of Do For You: ``/api/dfy/*`` (see README_FOR_AI.md, «DFY»)."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from .dfy import DFYManager, BrowserChoiceRequired
from .dfy_browser import in_language
from .dfy_vault import VaultError


async def _body(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def build_router(manager: DFYManager) -> APIRouter:
    router = APIRouter()

    @router.get("/api/dfy/status")
    async def dfy_status():
        return await manager.astatus()

    @router.get("/api/dfy/tasks")
    async def dfy_list():
        return {"tasks": manager.list()}

    @router.post("/api/dfy/tasks")
    async def dfy_create(request: Request):
        body = await _body(request)
        try:
            task = await manager.create(body.get("goal"), body.get("context"),
                                        str(body.get("source") or "chat"), str(body.get("chat_id") or ""),
                                        target=str(body.get("target") or ""), browser=body.get("browser", ""))
        except BrowserChoiceRequired as exc:
            return JSONResponse({"error": str(exc), "code": "browser_choice_required", "choices": exc.choices}, 409)
        except PermissionError as exc:
            return JSONResponse({"error": str(exc), "reason": str(exc)}, 409)
        except ValueError as exc:
            return JSONResponse({"error": in_language(str(exc), manager._lang())}, 400)
        return {"id": task.id, "status": task.status}

    @router.get("/api/dfy/tasks/{task_id}")
    async def dfy_get(task_id: str):
        task = manager.get(task_id)
        if task is None:
            return JSONResponse({"error": in_language("Задача DFY не найдена", manager._lang())}, 404)
        return task.public()

    @router.get("/api/dfy/tasks/{task_id}/events")
    async def dfy_events(task_id: str, request: Request):
        task = manager.get(task_id)
        if task is None:
            return JSONResponse({"error": in_language("Задача DFY не найдена", manager._lang())}, 404)

        async def stream():
            version = -1
            while True:
                if await request.is_disconnected():
                    return
                data = await manager.wait_change(task_id, version, timeout=15.0)
                if data is None:
                    yield "event: gone\ndata: {}\n\n"
                    return
                if data["version"] != version:
                    version = data["version"]
                    yield "data: " + json.dumps(data, ensure_ascii=False) + "\n\n"
                    if data["status"] in ("done", "failed", "cancelled"):
                        return
                else:
                    yield ": keep-alive\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @router.get("/api/dfy/tasks/{task_id}/screenshots/{n}")
    async def dfy_screenshot(task_id: str, n: int):
        path = manager.screenshot_path(task_id, n)
        if path is None:
            return JSONResponse({"error": "Нет снимка"}, 404)
        return FileResponse(str(path), media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @router.post("/api/dfy/tasks/{task_id}/answer")
    async def dfy_answer(task_id: str, request: Request):
        body = await _body(request)
        try:
            task = await manager.answer(task_id, body)
        except KeyError as exc:      # Round 34: the toast in the UI language
            return JSONResponse({"error": in_language(str(exc.args[0] if exc.args else exc), manager._lang())}, 404)
        except RuntimeError as exc:
            return JSONResponse({"error": in_language(str(exc), manager._lang())}, 409)
        except (ValueError, VaultError) as exc:
            return JSONResponse({"error": in_language(str(exc), manager._lang())}, 400)
        await asyncio.sleep(0)
        return {"ok": True, "id": task.id, "status": task.status}

    @router.post("/api/dfy/tasks/{task_id}/cancel")
    async def dfy_cancel(task_id: str):
        task = await manager.cancel(task_id)
        if task is None:
            return JSONResponse({"error": in_language("Задача DFY не найдена", manager._lang())}, 404)
        return {"ok": True, "id": task.id, "status": task.status}

    # --- vault management (values are write-only: never returned) ---------
    @router.get("/api/dfy/vault")
    async def dfy_vault_list():
        return {"items": manager.vault.list(), **manager.vault.info()}

    @router.post("/api/dfy/vault")
    async def dfy_vault_add(request: Request):
        body = await _body(request)
        try:
            meta = manager.vault.add(str(body.get("kind") or ""), body.get("fields") or {},
                                     label=str(body.get("label") or ""), domain=str(body.get("domain") or ""))
        except VaultError as exc:
            return JSONResponse({"error": str(exc)}, 400)
        return {"ok": True, "item": meta}

    @router.put("/api/dfy/vault/{secret_id}")
    async def dfy_vault_update(secret_id: str, request: Request):
        body = await _body(request)
        clear = body.get("clear") if isinstance(body.get("clear"), list) else []
        try:
            meta = manager.vault.update(
                secret_id, body.get("fields") if isinstance(body.get("fields"), dict) else {},
                label=str(body["label"]) if "label" in body else None,
                domain=str(body["domain"]) if "domain" in body else None, clear=[str(x) for x in clear])
        except VaultError as exc:
            status = 404 if "не найден" in str(exc) else 400
            return JSONResponse({"error": str(exc)}, status)
        return {"ok": True, "item": meta}

    @router.delete("/api/dfy/vault/{secret_id}")
    async def dfy_vault_delete(secret_id: str):
        if not manager.vault.delete(secret_id):
            return JSONResponse({"error": "Секрет не найден"}, 404)
        return {"ok": True}

    # --- Round 30: the user takes over the browser (app/dfy_takeover.py) ---
    def _lang() -> str:
        return manager._lang()

    @router.post("/api/dfy/tasks/{task_id}/takeover")
    async def dfy_takeover_start(task_id: str):
        try:
            session = await manager.takeover_start(task_id)
        except KeyError:
            return JSONResponse({"error": in_language("Задача DFY не найдена", manager._lang())}, 404)
        except RuntimeError as exc:
            return JSONResponse({"error": in_language(str(exc), manager._lang())}, 409)
        return {"ok": True, "takeover": session.public(_lang())}

    @router.post("/api/dfy/tasks/{task_id}/takeover/end")
    async def dfy_takeover_end(task_id: str):
        try:
            task = await manager.takeover_end(task_id, reason="user")
        except KeyError:
            return JSONResponse({"error": in_language("Задача DFY не найдена", manager._lang())}, 404)
        return {"ok": True, "id": task.id, "status": task.status, "control": task.public()["control"]}

    @router.websocket("/api/dfy/tasks/{task_id}/takeover/ws")
    async def dfy_takeover_ws(websocket: WebSocket, task_id: str):
        from .dfy_takeover import local_websocket, serve_websocket
        if not local_websocket(websocket):
            await websocket.close(code=1008)
            return
        session = manager.takeover_session(task_id)
        if session is None:
            await websocket.accept()
            await websocket.send_text(json.dumps({"type": "state", "active": False}))
            await websocket.close()
            return
        await serve_websocket(websocket, session, _lang)

    # --- Round 30: the DFY proxy (app/dfy_proxy.py); login/password are write-only ---
    def _unknown_machine(machine: str) -> Optional[JSONResponse]:
        """``?machine=`` is «local» or a machine from Settings → DFY (its proxy goes away with it)."""
        if machine in ("", "local") or manager.machines.store.get(machine) is not None:
            return None
        return JSONResponse({"error": "Machine not found" if _lang() == "en" else "Машина не найдена"}, 404)

    @router.get("/api/dfy/proxy")
    async def dfy_proxy_get(machine: str = "local"):
        if (missing := _unknown_machine(machine)) is not None:
            return missing
        out = manager.proxy.get(machine)
        if machine in ("", "local"):
            out["status"] = manager.status().get("proxy")   # the local relay's state; a machine has none here
        return out

    @router.put("/api/dfy/proxy")
    async def dfy_proxy_put(request: Request, machine: str = "local"):
        from .dfy_proxy import ProxyError
        if (missing := _unknown_machine(machine)) is not None:
            return missing
        body = await _body(request)
        try:
            return {"ok": True, **manager.proxy.set(machine, body)}
        except ProxyError as exc:
            return JSONResponse({"error": exc.text(_lang()), "error_ru": exc.ru, "error_en": exc.en}, 400)

    @router.delete("/api/dfy/proxy")
    async def dfy_proxy_delete(machine: str = "local"):
        return {"ok": manager.proxy.delete(machine)}

    @router.post("/api/dfy/proxy/check")
    async def dfy_proxy_check(request: Request, machine: str = "local"):
        """External IP through the proxy: the saved one, or the form's values (``{scheme, host, port, username?, password?}``)."""
        from .dfy_proxy import ProxyError, check
        body = await _body(request)
        try:
            upstream = manager.proxy.upstream(machine, override=body if body.get("host") or body.get("address") else None)
        except ProxyError as exc:
            return JSONResponse({"ok": False, "code": exc.code, "error": exc.text(_lang())}, 400)
        if upstream is None:
            return JSONResponse({"ok": False, "code": "config", "error": "Proxy is off" if _lang() == "en" else "Прокси выключен"}, 400)
        result = await check(upstream)
        if not result.get("ok") and _lang() == "en":
            result["error"] = result.get("error_en") or result.get("error")
        return result

    @router.post("/api/dfy/browser/clear")
    async def dfy_clear_sessions():
        try:
            await manager.clear_sessions()
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, 409)
        return {"ok": True}

    return router


VISION_TTL = 30.0


def install(app: Any, completion: Any, load_preferences: Any) -> DFYManager:
    """Create the manager, mount ``/api/dfy/*`` on ``app`` and keep it in ``app.state.dfy_manager``.

    «Has Vision» comes from the same place the settings page reads it: ``caps.vision`` of the
    models in ``/api/pg/state`` (MemuAI Proxy → ``model_capabilities.adapter_vision``: probe,
    manual override, local projector, provider row, adapter). It is read in-process through
    the app itself, so the backend and the model list in Settings → Do For You always agree."""
    import time
    from pathlib import Path
    from .config import DATA_DIR

    cache: dict = {"at": 0.0, "vision": {}, "image": set()}

    async def refresh_vision() -> None:
        if time.monotonic() - cache["at"] < VISION_TTL:
            return
        cache["at"] = time.monotonic()
        import httpx
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1", timeout=8) as client:
            response = await client.get("/api/pg/state")
        data = response.json() if response.status_code == 200 else {}
        rows = data.get("models") if isinstance(data, dict) else None
        if isinstance(rows, list) and rows:
            cache["vision"] = {str(row.get("id")): (row.get("caps") or {}).get("vision") is True
                               for row in rows if isinstance(row, dict) and row.get("id")}
            # Not chat models (excluded from the DFY list by app.js too): image generators, music-only.
            cache["image"] = {str(x) for x in data.get("image_models") or []} | {
                str(row.get("id")) for row in rows if isinstance(row, dict)
                and (row.get("caps") or {}).get("music") is True and (row.get("caps") or {}).get("text") is not True}

    def lookup(model: str) -> Any:
        vision = cache["vision"]
        if model in vision:
            return vision[model]
        # The Proxy is offline or the model is not in its list: only direct evidence counts
        # (manual override, probe, local projector, the provider's own row) — unknown stays None.
        try:
            from . import model_capabilities
            return model_capabilities.has_vision(model)
        except Exception:
            return None

    def first_vision_model() -> str:
        # Same rule as defaultVisionModel() in app.js: the first model of the list that sees images.
        return next((model for model, sees in cache["vision"].items() if sees and model not in cache["image"]), "")

    manager = DFYManager(completion, load_preferences, data_root=Path(DATA_DIR) / "memuai",
                         vision_lookup=lookup, fallback_model=first_vision_model, vision_refresh=refresh_vision)
    app.state.dfy_manager = manager
    app.include_router(build_router(manager))
    from .dfy_remote_api import build_router as build_remote_router   # Round 30: remote machines
    app.include_router(build_remote_router(manager))
    return manager
