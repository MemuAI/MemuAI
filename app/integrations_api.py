"""Round 30: HTTP routes of Settings → Integrations → Google / Notion (sign-in by link, services, own clients).

* ``GET  /api/pg/integrations/google`` — account, services (chosen / granted / ready), client, sign-in flow.
* ``POST /api/pg/integrations/google`` — ``{"action": "connect", "services": [...]}`` (sign in or add services:
  opens Google's page in the default browser), ``enable`` / ``disable`` ``{"service"}``, ``cancel``,
  ``disconnect`` (revoke), ``client`` ``{"client_id", "client_secret"}`` (the user's own OAuth client),
  ``check`` (Round 33: does each ready service's Google Cloud API answer — a disabled one comes with its «Enable» link).
* ``GET  /api/pg/integrations/notion`` — connection, mode (token | oauth), OAuth availability, flow.
* ``POST /api/pg/integrations/notion`` — ``token`` ``{"token"}``, ``oauth`` (sign in with Notion), ``cancel``,
  ``client`` ``{"client_id", "client_secret"}``, ``test``, ``disconnect``.

Errors: ``400 {"error", "error_ru", "error_en", "code"}``.  Nothing secret is ever returned.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict

try:
    from fastapi import Request
except ImportError:  # pragma: no cover - not every bundle has FastAPI
    Request = None  # type: ignore[assignment,misc]

def _lang() -> str:
    try:
        from .pgdata import load_preferences
        return "en" if load_preferences().get("language") == "en" else "ru"
    except Exception:
        return "ru"

def _fail(exc: BaseException):
    from fastapi.responses import JSONResponse
    ru = getattr(exc, "ru", "") or str(exc) or type(exc).__name__
    en = getattr(exc, "en", "") or ru
    return JSONResponse({"error": en if _lang() == "en" else ru, "error_ru": ru, "error_en": en,
                         "code": getattr(exc, "code", "") or "error"}, 400)

async def _body(request) -> Dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        body = {}
    return body if isinstance(body, dict) else {}

def register(app) -> None:
    from . import google_account
    from . import integrations_notion as notion

    def google_action(body: Dict[str, Any]) -> Dict[str, Any]:
        action = str(body.get("action") or "").strip().lower()
        result: Dict[str, Any] = {}
        if action == "connect":
            services = body.get("services") if isinstance(body.get("services"), list) else []
            result = google_account.connect([str(name) for name in services])
        elif action in ("enable", "disable"):
            result = google_account.set_enabled(str(body.get("service") or ""), action == "enable")
        elif action == "cancel":
            google_account.cancel_flow()
        elif action == "disconnect":
            google_account.disconnect()
        elif action == "client":
            google_account.save_client(str(body.get("client_id") or ""), str(body.get("client_secret") or ""))
        elif action == "check":

            from . import integrations
            return {"ok": True, "auth_url": "", "check": integrations.google_check(), "status": google_account.status()}
        else:
            raise google_account.GoogleAccountError("Неизвестное действие", "Unknown action", code="bad_action")
        return {"ok": True, "auth_url": result.get("auth_url", ""), "status": google_account.status()}

    def notion_action(body: Dict[str, Any]) -> Dict[str, Any]:
        action = str(body.get("action") or "").strip().lower()
        extra: Dict[str, Any] = {}
        if action == "token":
            notion.save_token(str(body.get("token") or ""))
        elif action == "oauth":
            extra = notion.start_oauth()
        elif action == "cancel":
            notion.cancel_oauth()
        elif action == "client":
            notion.save_client(str(body.get("client_id") or ""), str(body.get("client_secret") or ""))
        elif action == "test":
            extra = notion.test()
        elif action == "disconnect":
            notion.disconnect()
        else:
            raise ValueError("Unknown action")
        return {"ok": True, "auth_url": extra.get("auth_url", ""), "account": extra.get("account", ""),
                "status": notion.status()}

    @app.get("/api/pg/integrations/google")
    async def integrations_google_status():
        try:
            return await asyncio.to_thread(google_account.status)
        except Exception as exc:
            return _fail(exc)

    @app.post("/api/pg/integrations/google")
    async def integrations_google_action(request: Request):
        body = await _body(request)
        try:
            return await asyncio.to_thread(google_action, body)
        except Exception as exc:
            return _fail(exc)

    @app.get("/api/pg/integrations/notion")
    async def integrations_notion_status():
        try:
            return await asyncio.to_thread(notion.status)
        except Exception as exc:
            return _fail(exc)

    @app.post("/api/pg/integrations/notion")
    async def integrations_notion_action(request: Request):
        body = await _body(request)
        try:
            return await asyncio.to_thread(notion_action, body)
        except Exception as exc:
            return _fail(exc)
