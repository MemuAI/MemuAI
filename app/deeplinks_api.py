"""Round 35 (protocol): HTTP routes of ``memuai://`` links in the MemuAI server.  Logic: :mod:`app.deeplinks`.

``POST /api/pg/deeplink/dispatch``          ``{url, source: os|cli|chat|app}`` → queued item (never runs it)
``GET  /api/pg/deeplink/pending?lang=&mark=1``  items waiting for the window (``mark`` = the window shows them)
``POST /api/pg/deeplink/item/{id}/preview`` details fetched for the dialog (skill text, composite checks)
``POST /api/pg/deeplink/item/{id}/run``     ``{confirm: bool, inputs: {...}}`` → ``{ok, ui?, message?}``
``POST /api/pg/deeplink/item/{id}/cancel``
``GET  /api/pg/deeplink/settings``          settings + journal + routes + Windows registration state
``POST /api/pg/deeplink/settings``          ``{enabled, always_ask, cli, chat_links, register?: bool}``
``POST /api/pg/deeplink/build``             ``{route, params}`` → ``{link}`` («Копировать ссылку memuai://»)
``POST /api/pg/deeplink/journal/clear``
The desktop boundary middleware of the playground already rejects browser pages (Origin/Host checks),
so a web page cannot call these routes; a web page can only *open* a memuai:// link, which lands here as
``source=os`` and follows the normal confirmation rules.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import deeplinks

log = logging.getLogger("memuai.deeplinks")
MAX_BODY = 64 * 1024


async def _body(request: Request) -> Dict[str, Any]:
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise ValueError("too large")
    if not raw:
        return {}
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("object expected")
    return data


def _lang(request: Request, body: Dict[str, Any] | None = None) -> str:
    value = (body or {}).get("lang") or request.query_params.get("lang")
    if value in ("en", "ru"):
        return value
    try:
        from .pgdata import load_preferences
        return "en" if load_preferences().get("language") == "en" else "ru"
    except Exception:  # noqa: BLE001
        return "ru"


def _window_nudge(app: FastAPI):
    """Bring the MemuAI window up and make the page fetch the queue right away."""
    def notify(_item) -> None:
        try:
            from .desktop import show_active_window
            show_active_window()
        except Exception:  # noqa: BLE001 - no desktop shell (browser playground)
            pass
        window = getattr(app.state, "window", None)
        if window is not None:
            try:
                window.evaluate_js("window.MemuLinks ? (MemuLinks.poll(), true) : false")   # no Promise back to pywebview
            except Exception:  # noqa: BLE001
                pass
    return notify


def register(app: FastAPI, *, proxy_json=None) -> None:
    import httpx

    async def self_request(method: str, path: str, body: Any = None):
        """A call to this same server (e.g. /api/plan/run) without a socket."""
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1", timeout=30) as client:
            response = await client.request(method, path, json=body)
            try:
                return response.status_code, response.json()
            except ValueError:
                return response.status_code, {}

    async def launch_proxy():
        try:
            from memuai import _launch_proxy  # type: ignore
        except Exception:  # noqa: BLE001
            return ""
        return await asyncio.to_thread(_launch_proxy)

    services = {"proxy_json": proxy_json, "self_request": self_request, "launch_proxy": launch_proxy}
    deeplinks.set_notifier(_window_nudge(app))
    app.state.deeplinks = deeplinks

    def ctx_for(request: Request, body: Dict[str, Any]) -> deeplinks.Ctx:
        inputs = body.get("inputs") if isinstance(body.get("inputs"), dict) else {}
        return deeplinks.Ctx(lang=_lang(request, body), inputs=dict(inputs), app=app, services=services)

    def bad(message: str = "bad request", status: int = 400) -> JSONResponse:
        return JSONResponse({"ok": False, "error": "bad_request", "message": message}, status)

    @app.post("/api/pg/deeplink/dispatch")
    async def deeplink_dispatch(request: Request):
        try:
            body = await _body(request)
        except (ValueError, UnicodeDecodeError):
            return bad()
        source = str(body.get("source") or "os")
        result = await asyncio.to_thread(deeplinks.dispatch, str(body.get("url") or ""), source, lang=_lang(request, body))
        return JSONResponse(result, 200 if result.get("ok") else (429 if result.get("error") == "rate_limited" else 400))

    @app.get("/api/pg/deeplink/pending")
    async def deeplink_pending(request: Request):
        lang = _lang(request)
        return {"ok": True, "items": deeplinks.pending(lang, mark_shown=request.query_params.get("mark") == "1")}

    @app.post("/api/pg/deeplink/item/{item_id}/preview")
    async def deeplink_preview(item_id: str, request: Request):
        try:
            body = await _body(request)
        except (ValueError, UnicodeDecodeError):
            return bad()
        return await deeplinks.preview(item_id, ctx_for(request, body))

    @app.post("/api/pg/deeplink/item/{item_id}/run")
    async def deeplink_run(item_id: str, request: Request):
        try:
            body = await _body(request)
        except (ValueError, UnicodeDecodeError):
            return bad()
        result = await deeplinks.run(item_id, ctx_for(request, body), confirmed=body.get("confirm") is True)
        return JSONResponse(result, 200 if result.get("ok") else 400)

    @app.post("/api/pg/deeplink/item/{item_id}/cancel")
    async def deeplink_cancel(item_id: str):
        return {"ok": deeplinks.reject(item_id)}

    @app.get("/api/pg/deeplink/settings")
    async def deeplink_settings(request: Request):
        lang = _lang(request)
        registration: Dict[str, Any] = {"supported": sys.platform == "win32"}
        if sys.platform == "win32":
            try:
                from . import deeplinks_win
                registration.update(await asyncio.to_thread(deeplinks_win.status))
            except Exception as exc:  # noqa: BLE001
                registration["error"] = str(exc)[:200]
        return {"ok": True, "settings": deeplinks.load_settings(), "journal": deeplinks.journal(25),
                "routes": [r.public(lang) for r in deeplinks.routes() if r.danger != "block"],
                "registration": registration}

    @app.post("/api/pg/deeplink/settings")
    async def deeplink_settings_save(request: Request):
        try:
            body = await _body(request)
        except (ValueError, UnicodeDecodeError):
            return bad()
        settings = deeplinks.save_settings({k: v for k, v in body.items() if k in deeplinks.DEFAULT_SETTINGS})
        result: Dict[str, Any] = {"ok": True, "settings": settings}
        if "register" in body and sys.platform == "win32":
            try:
                from . import deeplinks_win
                result["registration"] = await asyncio.to_thread(
                    deeplinks_win.register if body.get("register") else deeplinks_win.unregister)
            except Exception as exc:  # noqa: BLE001
                result["registration"] = {"ok": False, "error": str(exc)[:200]}
        return result

    @app.post("/api/pg/deeplink/build")
    async def deeplink_build(request: Request):
        try:
            body = await _body(request)
        except (ValueError, UnicodeDecodeError):
            return bad()
        route = str(body.get("route") or "")
        params = body.get("params") if isinstance(body.get("params"), dict) else {}
        if deeplinks.get_route(route) is None:
            return bad("unknown route")
        link = deeplinks.build(route, params)
        try:
            deeplinks.parse(link)          # a copied link must be one MemuAI accepts
        except deeplinks.LinkError as exc:
            return JSONResponse(exc.as_dict(_lang(request, body)), 400)
        return {"ok": True, "link": link}

    @app.post("/api/pg/deeplink/journal/clear")
    async def deeplink_journal_clear():
        deeplinks.clear_journal()
        return {"ok": True}
