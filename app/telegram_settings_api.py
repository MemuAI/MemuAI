"""MemuAI window routes for Settings → Remote access → Telegram.

The bot itself runs in MemuAI Proxy; these routes run in the MemuAI window
process and talk to it only through ``telegram_state.json`` (status heartbeat,
pairing code) and preferences. ``getMe`` is called here too, so the panel can
show the bot name and a deep link even while the Proxy is not running.
"""
from __future__ import annotations

import contextlib
import time
from typing import Any, Callable, Dict, Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import telegram_pairing
from .telegram_api import BotApi, TelegramError, redact, valid_token

_bot_cache: Dict[str, Any] = {"fp": "", "at": 0.0, "bot": {}, "error": ""}


def _err(message: str, status: int = 400, code: str = "") -> JSONResponse:
    return JSONResponse({"error": message, "code": code}, status)


async def _bot_info(client: httpx.AsyncClient, token: str, *, fresh: bool = False) -> Dict[str, Any]:
    """getMe with a 5-minute cache; raises TelegramError on a bad token."""
    fingerprint = telegram_pairing.token_fingerprint(token)
    if (not fresh and _bot_cache["fp"] == fingerprint and time.time() - _bot_cache["at"] < 300
            and _bot_cache["bot"]):
        return dict(_bot_cache["bot"])
    bot = await BotApi(client, token).get_me()
    _bot_cache.update(fp=fingerprint, at=time.time(), bot=bot, error="")
    return dict(bot)


def _lang(request: Request, body: Dict[str, Any] | None = None) -> str:
    value = str((body or {}).get("lang") or request.query_params.get("lang") or "")
    return "en" if value == "en" else "ru"


def _token_error(error: TelegramError, en: bool) -> JSONResponse:
    if error.unauthorized or error.error_code == 404:
        return _err("Telegram rejected the bot token — copy it again from @BotFather"
                    if en else "Telegram отклонил токен — скопируйте его заново у @BotFather", 400, "bad_token")
    return _err(("Telegram is unreachable: " if en else "Telegram недоступен: ") + redact(error.description)[:200],
                502, "network")


@contextlib.asynccontextmanager
async def _telegram_http(shared: Optional[httpx.AsyncClient]):
    """The client for api.telegram.org. The window's own client ignores proxies
    (trust_env=False, it pings 127.0.0.1), but the bot in MemuAI Proxy honours
    the system/env proxy — where Telegram is reachable only through one, «Connect»
    and «Test» said «Telegram is unreachable» while the bot itself worked."""
    if shared is not None:
        yield shared
        return
    async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=10)) as client:
        yield client


_proxy_cache: Dict[str, Any] = {"at": 0.0, "online": False}


async def _proxy_online(client: httpx.AsyncClient, proxy_url: Optional[Callable[[], str]]) -> Optional[bool]:
    """Is MemuAI Proxy (the bot's process) reachable? ``None`` when unknown."""
    if proxy_url is None:
        return None
    if time.time() - _proxy_cache["at"] < 5:
        return bool(_proxy_cache["online"])
    online = False
    try:
        base = str(proxy_url() or "").rstrip("/")
        if base:
            response = await client.get(base + "/health", timeout=2.5)
            online = response.status_code == 200
    except Exception:
        online = False
    _proxy_cache.update(at=time.time(), online=online)
    return online


def register_telegram_routes(app: FastAPI, client: httpx.AsyncClient,
                             proxy_url: Optional[Callable[[], str]] = None,
                             telegram_client: Optional[httpx.AsyncClient] = None) -> None:
    """``client`` is the window's loopback client (MemuAI Proxy health);
    Telegram calls use ``telegram_client`` or a proxy-aware client of their own."""
    @app.get("/api/pg/telegram/status")
    async def telegram_status(request: Request):
        import asyncio
        from .pgdata import load_preferences
        prefs = await asyncio.to_thread(load_preferences)
        result = await asyncio.to_thread(telegram_pairing.status, prefs)
        if result["phase"] == "offline":
            # No heartbeat. Say "start Proxy" only when Proxy really is not
            # running; otherwise the bot itself is stuck (or runs from another
            # data folder) and «Restart bot» is the fix.
            proxy = await _proxy_online(client, proxy_url)
            result["proxy_online"] = proxy
            if proxy:
                result["phase"] = "stalled"
        token = str(prefs.get("telegram_bot_token") or "").strip()
        if valid_token(token) and not result["bot"].get("username") and request.query_params.get("probe") != "0":
            try:
                async with _telegram_http(telegram_client) as tg:
                    bot = await _bot_info(tg, token)
                result["bot"] = {"username": bot.get("username", ""), "name": bot.get("first_name", ""),
                                 "id": str(bot.get("id") or "")}
            except TelegramError as error:
                result["token_error"] = "bad_token" if error.unauthorized else "network"
        username = result["bot"].get("username")
        result["bot_link"] = f"https://t.me/{username}" if username else ""
        return result

    @app.post("/api/pg/telegram/pair")
    async def telegram_pair(request: Request):
        from .pgdata import load_preferences, save_preferences
        try:
            body = await request.json()
        except Exception:
            body = {}
        body = body if isinstance(body, dict) else {}
        en = _lang(request, body) == "en"
        prefs = load_preferences()
        token = str(body.get("token") or prefs.get("telegram_bot_token") or "").strip()
        if not valid_token(token):
            return _err("Paste the bot token from @BotFather first" if en
                        else "Сначала вставьте токен бота от @BotFather", 400, "bad_token")
        try:
            async with _telegram_http(telegram_client) as tg:
                bot = await _bot_info(tg, token, fresh=True)
        except TelegramError as error:
            return _token_error(error, en)
        updates: Dict[str, Any] = {}
        if token != prefs.get("telegram_bot_token"):
            updates["telegram_bot_token"] = token
        if not prefs.get("telegram_enabled"):
            updates["telegram_enabled"] = True
        if updates:
            save_preferences(updates)
        code, expires = telegram_pairing.create_pairing_code()
        username = str(bot.get("username") or "")
        return {"ok": True, "code": telegram_pairing.format_code(code), "raw_code": code,
                "expires": expires, "ttl": int(expires - time.time()),
                "bot": {"username": username, "name": bot.get("first_name", "")},
                "link": f"https://t.me/{username}?start=pair-{code}" if username else "",
                "app_link": f"tg://resolve?domain={username}&start=pair-{code}" if username else ""}

    @app.post("/api/pg/telegram/restart")
    async def telegram_restart(request: Request):
        """«Restart bot»: the bot (in MemuAI Proxy) restarts its polling
        session with a fresh connection within ~5 s; a rejected token is
        retried at once."""
        import asyncio
        stamp = await asyncio.to_thread(telegram_pairing.request_restart)
        proxy = await _proxy_online(client, proxy_url)
        return {"ok": True, "requested": stamp, "proxy_online": proxy}

    @app.post("/api/pg/telegram/pair/cancel")
    async def telegram_pair_cancel():
        telegram_pairing.cancel_pairing_code()
        return {"ok": True}

    @app.post("/api/pg/telegram/unpair")
    async def telegram_unpair():
        telegram_pairing.unpair()
        return {"ok": True}

    @app.post("/api/pg/telegram/test")
    async def telegram_test(request: Request):
        """Validate the token (getMe) and, when a chat is paired, send a test."""
        from .pgdata import load_preferences
        try:
            body = await request.json()
        except Exception:
            body = {}
        body = body if isinstance(body, dict) else {}
        en = _lang(request, body) == "en"
        prefs = load_preferences()
        token = str(body.get("token") or prefs.get("telegram_bot_token") or "").strip()
        chat_id = str(body.get("chat_id") if "chat_id" in body else prefs.get("telegram_chat_id") or "").strip()
        if not valid_token(token):
            return _err("Invalid bot token" if en else "Некорректный Bot Token", 400, "bad_token")
        async with _telegram_http(telegram_client) as tg:
            try:
                bot = await _bot_info(tg, token, fresh=True)
            except TelegramError as error:
                return _token_error(error, en)
            sent = False
            if chat_id:
                text = ("✅ <b>MemuAI is connected</b>\nThis is a test message from Settings. Just write here — "
                        "the answer comes from your MemuAI." if prefs.get("telegram_language") == "en" else
                        "✅ <b>MemuAI подключён</b>\nЭто проверочное сообщение из настроек. Просто напишите сюда — "
                        "ответит ваш MemuAI.")
                try:
                    await BotApi(tg, token).call("sendMessage", essential=True, chat_id=chat_id, text=text,
                                                 parse_mode="HTML")
                    sent = True
                except TelegramError as error:
                    lowered = error.description.lower()
                    if "chat not found" in lowered or "bot was blocked" in lowered or error.error_code == 403:
                        return _err("The bot cannot write to this chat: open the bot in Telegram and press Start"
                                    if en else "Бот не может написать в этот чат: откройте бота в Telegram и нажмите «Start»",
                                    400, "chat_unreachable")
                    return _err(("Message not sent: " if en else "Сообщение не отправлено: ") + error.description[:200],
                                400, "send_failed")
        return {"ok": True, "sent": sent, "username": bot.get("username", ""),
                "name": bot.get("first_name", "Telegram bot")}
