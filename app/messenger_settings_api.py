"""MemuAI window routes for Settings → Messengers → Discord / WhatsApp.

The bots run in MemuAI Proxy (``app.messenger_runtime``); the window and the Proxy share only
``messengers_state.json`` (status heartbeat, owner, pairing code) and the preferences. The connection tests
call Discord / Meta / WAHA from here, so they work even while the Proxy is stopped.
"""
from __future__ import annotations

import asyncio
import re
import secrets
from typing import Any, Callable, Dict, Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import messenger_core as core

WEBHOOK_PATH = "/messengers/whatsapp/webhook"


def _err(message: str, status: int = 400, code: str = "") -> JSONResponse:
    return JSONResponse({"error": message, "code": code}, status)


def _platform(value: Any) -> str:
    value = str(value or "")
    return value if value in core.PLATFORMS else ""


async def _body(request: Request) -> Dict[str, Any]:
    try:
        data = await request.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


async def test_discord(client: httpx.AsyncClient, token: str, en: bool) -> Dict[str, Any]:
    from .discord_bot import API, invite_links, redact, valid_token
    if not valid_token(token):
        raise ValueError("This does not look like a Discord bot token — copy it from Bot → Reset Token"
                         if en else "Это не похоже на токен бота Discord — скопируйте его в разделе Bot → Reset Token")
    try:
        response = await client.get(API + "/users/@me", headers={"Authorization": f"Bot {token}"})
    except httpx.HTTPError as exc:
        raise ConnectionError(("Discord is unreachable: " if en else "Discord недоступен: ") + redact(str(exc))[:160])
    if response.status_code in (401, 403):
        raise ValueError("Discord rejected the token — reset it and paste the new one"
                         if en else "Discord отклонил токен — сбросьте его и вставьте новый")
    if response.status_code >= 400:
        raise ConnectionError(f"Discord {response.status_code}: {redact(response.text)[:160]}")
    me = response.json()
    app_id = str(me.get("id") or "")
    try:
        application = await client.get(API + "/oauth2/applications/@me", headers={"Authorization": f"Bot {token}"})
        if application.status_code < 400:
            app_id = str(application.json().get("id") or app_id)
    except (httpx.HTTPError, ValueError):
        pass
    return {"name": str(me.get("username") or ""), "id": str(me.get("id") or ""), "app_id": app_id,
            "profile": f"https://discord.com/users/{me.get('id')}" if me.get("id") else "", **invite_links(app_id)}


async def test_whatsapp(client: httpx.AsyncClient, prefs: Dict[str, Any], body: Dict[str, Any], en: bool) -> Dict[str, Any]:
    from .whatsapp_bot import GRAPH, redact
    mode = "waha" if (body.get("mode") or prefs.get("whatsapp_mode")) == "waha" else "cloud"
    if mode == "cloud":
        token = str(body.get("token") or prefs.get("whatsapp_access_token") or "").strip()
        phone = re.sub(r"\D", "", str(body.get("phone_id") or prefs.get("whatsapp_phone_id") or ""))
        if not token or not phone:
            raise ValueError("Enter the access token and the phone number ID" if en else "Введите токен доступа и ID номера телефона")
        try:
            response = await client.get(f"{GRAPH}/{phone}", params={"fields": "display_phone_number,verified_name"},
                                        headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as exc:
            raise ConnectionError(("Meta is unreachable: " if en else "Meta недоступна: ") + redact(str(exc))[:160])
        if response.status_code in (400, 401, 403, 404):
            raise ValueError("Meta rejected the token or the phone number ID" if en else "Meta отклонила токен или ID номера")
        if response.status_code >= 400:
            raise ConnectionError(f"Meta {response.status_code}: {redact(response.text)[:160]}")
        data = response.json()
        return {"mode": mode, "name": str(data.get("verified_name") or ""), "phone": str(data.get("display_phone_number") or "")}
    from .messenger_prefs import _url
    base = _url(body.get("url") or prefs.get("whatsapp_waha_url"), "http://127.0.0.1:3000")
    session = str(body.get("session") or prefs.get("whatsapp_waha_session") or "default")
    if not re.fullmatch(r"[\w.\-]{1,64}", session):
        session = "default"
    key = str(body.get("api_key") or prefs.get("whatsapp_waha_api_key") or "")
    try:
        response = await client.get(f"{base}/api/sessions/{session}", headers={"X-Api-Key": key} if key else {})
    except httpx.HTTPError:
        raise ConnectionError(f"WAHA is not answering at {base} — start it (docker run -p 3000:3000 devlikeapro/waha)"
                              if en else f"WAHA не отвечает по адресу {base} — запустите его (docker run -p 3000:3000 devlikeapro/waha)")
    if response.status_code in (401, 403):
        raise ValueError("WAHA rejected the API key" if en else "WAHA отклонил API-ключ")
    if response.status_code == 404:
        raise ValueError(f"WAHA has no session «{session}» — create it and scan the QR code"
                         if en else f"В WAHA нет сессии «{session}» — создайте её и отсканируйте QR-код")
    if response.status_code >= 400:
        raise ConnectionError(f"WAHA {response.status_code}: {redact(response.text)[:160]}")
    data = response.json() if response.content else {}
    status = str((data or {}).get("status") or "")
    me = (data or {}).get("me") or {}
    return {"mode": mode, "session": session, "state": status, "working": status == "WORKING",
            "phone": str(me.get("id") or "").split("@")[0], "name": str(me.get("pushName") or "")}


def register_messenger_routes(app: FastAPI, client: httpx.AsyncClient,
                              proxy_url: Optional[Callable[[], str]] = None) -> None:
    @app.get("/api/pg/messengers/status")
    async def messengers_status(request: Request):
        from .pgdata import load_preferences
        from .telegram_settings_api import _proxy_online
        prefs = await asyncio.to_thread(load_preferences)
        result: Dict[str, Any] = {}
        for name in core.PLATFORMS:
            row = await asyncio.to_thread(core.public_status, name)
            row["enabled"] = bool(prefs.get(f"{name}_enabled"))
            result[name] = row
        result["whatsapp"]["mode"] = prefs.get("whatsapp_mode") or "cloud"
        result["whatsapp"]["webhook_path"] = WEBHOOK_PATH
        base = str(proxy_url() if proxy_url else "").rstrip("/")
        result["whatsapp"]["local_webhook"] = (base + WEBHOOK_PATH) if base else ""
        result["proxy_online"] = await _proxy_online(client, proxy_url)
        return result

    @app.post("/api/pg/messengers/pair")
    async def messengers_pair(request: Request):
        body = await _body(request)
        platform = _platform(body.get("platform"))
        if not platform:
            return _err("unknown messenger")
        return await asyncio.to_thread(core.new_pairing_code, platform)

    @app.post("/api/pg/messengers/unpair")
    async def messengers_unpair(request: Request):
        body = await _body(request)
        platform = _platform(body.get("platform"))
        if not platform:
            return _err("unknown messenger")
        await asyncio.to_thread(core.unpair, platform)
        return {"ok": True}

    @app.post("/api/pg/messengers/verify-token")
    async def messengers_verify_token(request: Request):
        """A fresh verify token for the Meta webhook form (saved; shown once in the settings)."""
        from .pgdata import save_preferences
        token = secrets.token_urlsafe(24)
        await asyncio.to_thread(save_preferences, {"whatsapp_verify_token": token})
        return {"token": token}

    @app.post("/api/pg/messengers/test")
    async def messengers_test(request: Request):
        from .pgdata import load_preferences
        body = await _body(request)
        en = str(body.get("lang") or "") == "en"
        platform = _platform(body.get("platform"))
        if not platform:
            return _err("unknown messenger")
        prefs = await asyncio.to_thread(load_preferences)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15, connect=8),
                                         trust_env=platform == "discord" or body.get("mode") != "waha") as http:
                if platform == "discord":
                    token = str(body.get("token") or prefs.get("discord_bot_token") or "").strip()
                    info = await test_discord(http, token, en)
                else:
                    info = await test_whatsapp(http, prefs, body, en)
        except ValueError as exc:
            return _err(str(exc), 400, "rejected")
        except ConnectionError as exc:
            return _err(str(exc), 502, "network")
        return {"ok": True, **info}
