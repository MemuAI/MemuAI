"""HTTP контракт удалённых машин DFY: ``/api/dfy/remote/*`` (Раунд 30, см. README_FOR_AI.md).

Токены и пароли — только на запись: в ответах их нет никогда.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from .dfy_agent import NeedsConfirm, Refused
from .dfy_remote import AGENT_FILE, LOCAL_ID, RemoteError, agent_source, install_command, proxy_env

# curl понимает все схемы прокси (socks5h — с удалённым DNS); без curl — python (HTTP(S)-прокси)
PROXY_CHECK_COMMAND = ("curl -fsS --max-time 20 https://api.ipify.org 2>&1 || python3 -c "
                       "\"import urllib.request;print(urllib.request.urlopen('https://api.ipify.org',timeout=20).read().decode())\"")


async def _body(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def build_router(manager: Any) -> APIRouter:
    router = APIRouter()

    def lang() -> str:
        return manager._lang()

    def error(exc: Exception, status: int = 400) -> JSONResponse:
        if isinstance(exc, RemoteError):
            return JSONResponse({"error": exc.text(lang()), "error_ru": exc.ru, "error_en": exc.en}, status)
        if isinstance(exc, Refused):
            text = exc.message_en if lang() == "en" else str(exc)
            return JSONResponse({"error": text, "needs_confirm": isinstance(exc, NeedsConfirm)},
                                409 if isinstance(exc, NeedsConfirm) else 403)
        return JSONResponse({"error": str(exc)}, status)

    @router.get("/api/dfy/remote/machines")
    async def machines_list():
        return {"machines": manager.machines.store.list(), "targets": manager.machines.targets(lang()),
                "local_system": bool(manager.settings().get("dfy_local_system"))}

    @router.get("/api/dfy/remote/install")
    async def machines_install():
        """Команды установки агента (одна строка для вставки в терминал сервера, scp, systemd)."""
        return install_command()

    @router.post("/api/dfy/remote/machines")
    async def machines_add(request: Request):
        body = await _body(request)
        try:
            row = manager.machines.store.add(body)
        except (RemoteError, ValueError) as exc:
            return error(exc)
        # Сразу проверяем связь: ОС, версия агента, «онлайн». Ошибка связи не отменяет добавление.
        try:
            await manager.machines.probe(row["id"])
        except (RemoteError, Refused) as exc:
            manager.machines.store.note(row["id"], error=exc.text("ru") if isinstance(exc, RemoteError) else str(exc))
        return {"ok": True, "machine": manager.machines.store.public(manager.machines.store.get(row["id"]))}

    @router.put("/api/dfy/remote/machines/{machine_id}")
    async def machines_update(machine_id: str, request: Request):
        body = await _body(request)
        try:
            row = manager.machines.store.update(machine_id, body)
        except (RemoteError, ValueError) as exc:
            return error(exc, 404 if isinstance(exc, RemoteError) and "не найдена" in exc.ru else 400)
        manager.machines.forget_transport(machine_id)
        return {"ok": True, "machine": row}

    @router.delete("/api/dfy/remote/machines/{machine_id}")
    async def machines_delete(machine_id: str):
        if any(t.target and t.target.get("id") == machine_id and t.status in ("queued", "running", "waiting_user")
               for t in manager.tasks.values()):
            return JSONResponse({"error": "Task is running on this machine" if lang() == "en"
                                 else "На этой машине выполняется задача DFY"}, 409)
        manager.machines.forget_transport(machine_id)
        if not manager.machines.store.delete(machine_id):
            return JSONResponse({"error": "Machine not found" if lang() == "en" else "Машина не найдена"}, 404)
        proxy = getattr(manager, "proxy", None)
        if proxy is not None:
            proxy.delete(machine_id)      # прокси машины уходит вместе с ней
        return {"ok": True}

    @router.post("/api/dfy/remote/machines/{machine_id}/check")
    async def machines_check(machine_id: str):
        """Проверить связь сейчас: ``{ok, info}`` или ``{ok: false, error}``."""
        if machine_id != LOCAL_ID and manager.machines.store.get(machine_id) is None:
            return JSONResponse({"error": "Machine not found" if lang() == "en" else "Машина не найдена"}, 404)
        try:
            info = await asyncio.wait_for(manager.machines.probe(machine_id), 40)
        except asyncio.TimeoutError:
            return {"ok": False, "error": "No answer in 40 s" if lang() == "en" else "Нет ответа за 40 с"}
        except (RemoteError, Refused) as exc:
            text = exc.text(lang()) if isinstance(exc, RemoteError) else (exc.message_en if lang() == "en" else str(exc))
            return {"ok": False, "error": text}
        row = manager.machines.store.get(machine_id)
        return {"ok": True, "info": info, "machine": manager.machines.store.public(row) if row else None}

    @router.post("/api/dfy/remote/machines/{machine_id}/proxy/check")
    async def machines_proxy_check(machine_id: str, request: Request):
        """Внешний IP, который видит САМА машина через свой прокси DFY (сохранённый или из формы)."""
        import ipaddress
        import time
        from .dfy_proxy import ProxyError
        en = lang() == "en"
        if manager.machines.store.get(machine_id) is None:
            return JSONResponse({"error": "Machine not found" if en else "Машина не найдена"}, 404)
        if getattr(manager, "proxy", None) is None:
            return JSONResponse({"ok": False, "error": "The DFY proxy is unavailable" if en else "Прокси DFY недоступен"}, 404)
        body = await _body(request)
        try:
            upstream = manager.proxy.upstream(machine_id, override=body if body.get("host") or body.get("address") else None)
        except ProxyError as exc:
            return JSONResponse({"ok": False, "error": exc.text(lang())}, 400)
        if upstream is None:
            return JSONResponse({"ok": False, "error": "Proxy is off" if en else "Прокси выключен"}, 400)
        env = proxy_env(upstream)
        started = time.monotonic()
        try:
            result = await asyncio.wait_for(manager.machines.call(machine_id, "exec", {
                "command": PROXY_CHECK_COMMAND, "env": env, "timeout": 30}), 45)
        except asyncio.TimeoutError:
            return {"ok": False, "error": "No answer in 45 s" if en else "Нет ответа за 45 с"}
        except (RemoteError, Refused) as exc:
            text = exc.text(lang()) if isinstance(exc, RemoteError) else (exc.message_en if en else str(exc))
            return {"ok": False, "error": text}
        output = str(result.get("output") or "")
        for secret in (env["ALL_PROXY"], upstream.password):
            if secret:
                output = output.replace(secret, "[proxy]")
        last = output.strip().splitlines()[-1].strip() if output.strip() else ""
        try:
            ip = str(ipaddress.ip_address(last))
        except ValueError:
            ip = ""
        if result.get("exit_code") == 0 and ip:
            return {"ok": True, "ip": ip, "ms": int((time.monotonic() - started) * 1000)}
        return {"ok": False, "error": (output.strip()[-300:] or (f"exit {result.get('exit_code')}"))}

    @router.get("/api/dfy/remote/agent.py")
    async def agent_download():
        """Сам агент — один файл, ставится на сервер (``python3 memuai_remote_agent.py serve``)."""
        return PlainTextResponse(agent_source(), media_type="text/x-python; charset=utf-8", headers={
            "Content-Disposition": f'attachment; filename="{AGENT_FILE}"', "Cache-Control": "no-store"})

    return router
