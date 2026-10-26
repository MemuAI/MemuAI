"""Routes of the MCP hub (Round 40, r40-mcp): Settings → Integrations → MCP, scopes, calls, resources.

``register(app)`` from ``app/playground_api.py``. Logic: ``app/mcp_hub.py``; protocol and pool: ``app/mcp_client.py``.
The old MemuCode routes ``/api/code/mcp*`` stay as they were (the MemuCode «/mcp» panel uses them).
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict

from . import mcp_client, mcp_hub

try:
    from fastapi import Request
    from fastapi.responses import JSONResponse
except ImportError:  # pragma: no cover
    Request = Any  # type: ignore[misc,assignment]
    JSONResponse = None  # type: ignore[assignment]

def _lang(request: Any, body: Dict[str, Any] = None) -> str:
    raw = str((body or {}).get("lang") or request.query_params.get("lang") or "")
    if not raw:
        try:
            from .pgdata import load_preferences
            raw = str(load_preferences().get("language") or "")
        except Exception:
            raw = ""
    return "en" if raw == "en" else "ru"

async def _body(request: Any) -> Dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}

def _agents() -> list:
    try:
        from .pgdata import load_preferences
        memes = load_preferences().get("memes") or []
    except Exception:
        memes = []
    return [{"id": str(m.get("id")), "name": str(m.get("name") or "")[:80]} for m in memes
            if isinstance(m, dict) and m.get("id")][:100]

def _err(exc: BaseException, lang: str, status: int = 400):
    text = exc.text(lang) if hasattr(exc, "text") else str(exc)
    return JSONResponse({"error": text[:400]}, status)

def register(app: Any) -> None:
    mcp_hub.register_mentions()
    mcp_hub.register_quick()

    @app.get("/api/mcp/hub")
    async def mcp_hub_state(request: Request):
        mcp_client.registry.loop = asyncio.get_running_loop()
        lang = _lang(request)
        await asyncio.to_thread(mcp_hub.migrate_plain_secrets)
        from .safe_mode import is_safe_mode
        return {"servers": mcp_hub.public_rows(lang), "catalog": mcp_hub.catalog_public(),
                "cats": {k: list(v) for k, v in mcp_hub.CATS.items()}, "agents": _agents(),
                "safe_mode": is_safe_mode(), "secret_scheme": mcp_hub.store().scheme}

    @app.post("/api/mcp/install")
    async def mcp_hub_install(request: Request):
        body = await _body(request)
        lang = _lang(request, body)
        try:
            name = await asyncio.to_thread(mcp_hub.install, str(body.get("catalog_id") or ""),
                                           body.get("values") if isinstance(body.get("values"), dict) else {},
                                           name=str(body.get("name") or ""), read_only=body.get("read_only") is not False,
                                           chat=body.get("chat") is True)
        except mcp_hub.HubError as exc:
            return _err(exc, lang)
        return {"ok": True, "name": name}

    @app.post("/api/mcp/custom")
    async def mcp_hub_custom(request: Request):
        body = await _body(request)
        lang = _lang(request, body)
        try:
            name = await asyncio.to_thread(mcp_hub.add_custom, body)
        except mcp_hub.HubError as exc:
            return _err(exc, lang)
        return {"ok": True, "name": name}

    @app.post("/api/mcp/server/{name}")
    async def mcp_hub_update(name: str, request: Request):
        body = await _body(request)
        lang = _lang(request, body)
        try:
            await asyncio.to_thread(mcp_hub.update, name, body)
        except mcp_hub.HubError as exc:
            return _err(exc, lang, 404)
        if any(k in body for k in ("env", "enabled", "timeout")):
            await mcp_client.registry.stop(name)
        return {"ok": True}

    @app.delete("/api/mcp/server/{name}")
    async def mcp_hub_remove(name: str):
        await mcp_client.registry.stop(name)
        await asyncio.to_thread(mcp_hub.remove, name)
        return {"ok": True}

    @app.post("/api/mcp/server/{name}/check")
    async def mcp_hub_check(name: str, request: Request):
        lang = _lang(request)
        return await mcp_hub.check(name, lang)

    @app.post("/api/mcp/server/{name}/stop")
    async def mcp_hub_stop(name: str):
        await mcp_client.registry.stop(name)
        return {"ok": True}

    @app.get("/api/mcp/scope")
    async def mcp_hub_scope(request: Request):
        mcp_client.registry.loop = asyncio.get_running_loop()
        scope = str(request.query_params.get("scope") or "chat")
        return {"servers": mcp_hub.scoped(scope, str(request.query_params.get("agent") or ""))}

    @app.post("/api/mcp/call")
    async def mcp_hub_call(request: Request):
        body = await _body(request)
        lang = _lang(request, body)
        server, tool = str(body.get("server") or ""), str(body.get("tool") or "")
        if not server and tool.lower().startswith("mcp_"):
            server, tool = mcp_hub.split_tool_name(tool)
        if not server or not tool:
            return JSONResponse({"error": mcp_hub.tr(lang, "Сервер MCP не найден в настройках",
                                                     "This MCP server is not in the settings")}, 400)
        args = body.get("args") if isinstance(body.get("args"), dict) else {}
        result = await mcp_hub.call(server, tool, args, scope=str(body.get("scope") or "chat"),
                                    agent=str(body.get("agent") or ""), confirmed=body.get("confirmed") is True,
                                    full_access=str(body.get("access") or "") == "full", lang=lang)
        return {"server": server, "remote_tool": tool, **result}

    @app.post("/api/mcp/resource")
    async def mcp_hub_resource(request: Request):
        body = await _body(request)
        lang = _lang(request, body)
        try:
            return await mcp_client.registry.read_resource(str(body.get("server") or ""), str(body.get("uri") or ""))
        except Exception as exc:
            return JSONResponse({"error": mcp_client.error_text(exc, lang)[:400]}, 400)
