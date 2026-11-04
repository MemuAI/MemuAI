"""MemuAI Discord bot: chat with MemuAI and control it from Discord direct messages.

Runs inside MemuAI Proxy (``app.messenger_runtime``) over the Discord Gateway (an outgoing WebSocket):
nothing local is exposed to the internet. Only direct messages are served, and only from the person paired
with a one-time code from MemuAI → Settings → Messengers (``app.messenger_core``). Slash commands
(/menu, /new, /chats, /model, /settings, …) are registered for the bot; buttons and select menus drive
the same actions. The bot needs no privileged intents: message content of DMs is always delivered.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from contextvars import ContextVar
from typing import Any, Dict, List, Optional

import httpx

from . import messenger_core as core

log = logging.getLogger("memuai.discord")

API = "https://discord.com/api/v10"
GATEWAY = "wss://gateway.discord.gg/?v=10&encoding=json"
INTENTS = 1 << 12  # DIRECT_MESSAGES
STYLES = {"primary": 1, "secondary": 2, "success": 3, "danger": 4}
TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]{20,40}\.[A-Za-z0-9_\-]{4,10}\.[A-Za-z0-9_\-]{20,60}$")


def valid_token(token: str) -> bool:
    return bool(TOKEN_RE.match(str(token or "").strip()))


def redact(text: str) -> str:
    return re.sub(r"[A-Za-z0-9_\-]{20,40}\.[A-Za-z0-9_\-]{4,10}\.[A-Za-z0-9_\-]{20,60}", "[token]", str(text or ""))


def invite_links(app_id: str) -> Dict[str, str]:
    """Add the bot to your own account (DMs without a shared server) or to a server."""
    if not re.fullmatch(r"\d{5,25}", str(app_id or "")):
        return {}
    base = f"https://discord.com/oauth2/authorize?client_id={app_id}"
    return {"user": base + "&scope=applications.commands&integration_type=1",
            "server": base + "&scope=bot+applications.commands&permissions=0&integration_type=0"}


def slash_commands(lang: str = "ru", user_install: bool = True) -> List[Dict[str, Any]]:
    en = lang == "en"

    def cmd(name: str, ru: str, english: str, options: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        row: Dict[str, Any] = {"name": name, "type": 1, "description": english if en else ru,
                               "integration_types": [0, 1] if user_install else [0],
                               "contexts": [0, 1, 2] if user_install else [0, 1]}
        if options:
            row["options"] = options
        return row

    def text_option(name: str, ru: str, english: str, required: bool = False) -> Dict[str, Any]:
        return {"type": 3, "name": name, "description": english if en else ru, "required": required, "max_length": 100}

    return [
        cmd("menu", "Меню MemuAI", "MemuAI menu"), cmd("new", "Новый чат", "New chat"),
        cmd("chats", "Ваши чаты MemuAI", "Your MemuAI chats", [text_option("search", "Поиск по названию", "Search by title")]),
        cmd("model", "Выбрать модель", "Pick a model", [text_option("name", "Название модели", "Model name")]),
        cmd("settings", "Настройки: поиск, размышление, личность", "Settings: web search, reasoning, personality"),
        cmd("temp", "Временный чат вкл/выкл", "Temporary chat on/off"), cmd("regen", "Ответить ещё раз", "Regenerate"),
        cmd("stop", "Остановить ответ", "Stop the answer"), cmd("status", "Состояние MemuAI", "MemuAI status"),
        cmd("help", "Помощь", "Help"),
        cmd("pair", "Привязать MemuAI по коду", "Pair MemuAI with a code", [text_option("code", "Код из MemuAI", "Code from MemuAI", True)]),
    ]


def render(card: core.Card) -> Dict[str, Any]:
    """A card as a Discord message body: markdown text, a select menu and up to five rows of buttons."""
    rows: List[Dict[str, Any]] = []
    if card.choices:
        rows.append({"type": 1, "components": [{
            "type": 3, "custom_id": ("sel:" + (card.choice_id or "pick"))[:100], "placeholder": core.short(card.placeholder or "…", 140),
            "options": [{"label": core.short(c.label, 100) or "—", "value": c.value[:100],
                         **({"description": core.short(c.description, 100)} if c.description else {})} for c in card.choices[:25]]}]})
    for line in card.buttons:
        if len(rows) >= 5 or not line:
            continue
        rows.append({"type": 1, "components": [{"type": 2, "style": STYLES.get(b.style, 2), "label": core.short(b.label, 80),
                                                "custom_id": b.id[:100]} for b in line[:5]]})
    body: Dict[str, Any] = {"content": core.short(card.text, 2000) if len(card.text) > 2000 else card.text, "components": rows,
                            "allowed_mentions": {"parse": []}}
    return body


class Rest:
    def __init__(self, client: httpx.AsyncClient, token: str) -> None:
        self.client = client
        self.headers = {"Authorization": f"Bot {token}", "User-Agent": "DiscordBot (https://memuai.app, 5.0) MemuAI"}

    async def call(self, method: str, path: str, body: Any = None, auth: bool = True) -> Any:
        for attempt in range(3):
            response = await self.client.request(method, API + path, json=body, headers=self.headers if auth else {"User-Agent": self.headers["User-Agent"]})
            if response.status_code == 429:
                try:
                    wait = float(response.json().get("retry_after") or 1)
                except ValueError:
                    wait = 1.0
                await asyncio.sleep(min(10.0, wait) + 0.1)
                continue
            if response.status_code >= 400:
                raise DiscordError(response.status_code, redact(response.text)[:300])
            if response.status_code == 204 or not response.content:
                return {}
            return response.json()
        raise DiscordError(429, "rate limited")


class DiscordError(Exception):
    def __init__(self, status: int, text: str) -> None:
        super().__init__(f"{status}: {text}")
        self.status = status


class DiscordAdapter(core.Adapter):
    platform = "discord"
    limit = 1990
    can_edit = True

    def __init__(self, rest: Rest) -> None:
        self.rest = rest
        self.app_id = ""
        self._pending_reply: ContextVar[Optional[Dict[str, str]]] = ContextVar("discord_pending_reply", default=None)
        self.via_webhook: Dict[str, str] = {}  # message id → interaction token (edited through the webhook)

    async def send(self, peer: str, card: core.Card) -> Optional[str]:
        body = render(card)
        pending = self._pending_reply.get()
        token = pending.pop("token", "") if pending and pending["channel"] == peer else ""
        try:
            if token and self.app_id:
                data = await self.rest.call("PATCH", f"/webhooks/{self.app_id}/{token}/messages/@original", body)
                if data.get("id"):
                    self.via_webhook[str(data["id"])] = token
                    while len(self.via_webhook) > 200:
                        self.via_webhook.pop(next(iter(self.via_webhook)))
            else:
                data = await self.rest.call("POST", f"/channels/{peer}/messages", body)
            return str(data.get("id") or "") or None
        except (DiscordError, httpx.HTTPError) as exc:
            log.warning("discord send failed: %s", redact(str(exc)))
            return None

    async def edit(self, peer: str, message_id: str, card: core.Card) -> bool:
        token = self.via_webhook.get(str(message_id))
        path = (f"/webhooks/{self.app_id}/{token}/messages/{message_id}" if token and self.app_id
                else f"/channels/{peer}/messages/{message_id}")
        try:
            await self.rest.call("PATCH", path, render(card))
            return True
        except (DiscordError, httpx.HTTPError):
            return False

    async def typing(self, peer: str) -> None:
        try:
            await self.rest.call("POST", f"/channels/{peer}/typing")
        except (DiscordError, httpx.HTTPError):
            pass


class DiscordBot:
    def __init__(self, token: str) -> None:
        self.token = token
        self.seq: Optional[int] = None
        self.session_id = ""
        self.user: Dict[str, Any] = {}
        self.tasks: set = set()
        self.acked = True

    async def run(self, client: httpx.AsyncClient) -> str:
        """One gateway session. Returns why it ended: reconnect · unauthorized · intents."""
        import websockets
        rest = Rest(client, self.token)
        adapter = DiscordAdapter(rest)
        engine = core.Engine(adapter)
        try:
            me = await rest.call("GET", "/users/@me")
        except DiscordError as exc:
            if exc.status in (401, 403):
                core.set_status("discord", "unauthorized", "Discord rejected the bot token", code="bad_token")
                return "unauthorized"
            raise
        self.user = {"id": str(me.get("id") or ""), "name": me.get("username") or "", "app_id": str(me.get("id") or "")}
        try:
            application = await rest.call("GET", "/oauth2/applications/@me")
            self.user["app_id"] = str((application or {}).get("id") or self.user["id"])
        except (DiscordError, httpx.HTTPError):
            pass
        adapter.app_id = self.user["app_id"]
        await self._register(rest, adapter.app_id)
        core.set_status("discord", "connecting", "", bot={"name": self.user["name"], "id": self.user["id"],
                                                          "profile": f"https://discord.com/users/{self.user['id']}",
                                                          **invite_links(self.user["app_id"])})
        async with websockets.connect(GATEWAY, max_size=2 ** 22, open_timeout=20, ping_interval=None) as ws:
            hello = json.loads(await asyncio.wait_for(ws.recv(), 20))
            interval = float((hello.get("d") or {}).get("heartbeat_interval") or 41250) / 1000.0
            self.acked = True
            beat = asyncio.create_task(self._heartbeat(ws, interval))
            try:
                await ws.send(json.dumps({"op": 2, "d": {"token": self.token, "intents": INTENTS,
                                                          "properties": {"os": "windows", "browser": "MemuAI", "device": "MemuAI"}}}))
                async for raw in ws:
                    message = json.loads(raw)
                    if message.get("s") is not None:
                        self.seq = message["s"]
                    op = message.get("op")
                    if op == 0:
                        self._dispatch(engine, adapter, rest, message.get("t") or "", message.get("d") or {})
                    elif op == 1:
                        await ws.send(json.dumps({"op": 1, "d": self.seq}))
                    elif op == 11:
                        self.acked = True
                    elif op in (7, 9):
                        return "reconnect"
            except websockets.ConnectionClosed:
                pass  # the close code is read below
            finally:
                beat.cancel()
        code = getattr(ws, "close_code", None)
        if code == 4004:
            core.set_status("discord", "unauthorized", "Discord rejected the bot token", code="bad_token")
            return "unauthorized"
        if code == 4014:
            core.set_status("discord", "error", "Disallowed intents", code="intents")
            return "intents"
        return "reconnect"

    async def _heartbeat(self, ws: Any, interval: float) -> None:
        await asyncio.sleep(interval * random.random())
        while True:
            if not self.acked:  # a zombie connection: no ACK for the previous beat
                await ws.close(4000)
                return
            self.acked = False
            await ws.send(json.dumps({"op": 1, "d": self.seq}))
            core.set_status("discord", "online", "")
            await asyncio.sleep(interval)

    async def _register(self, rest: Rest, app_id: str) -> None:
        from .pgdata import load_preferences
        lang = core.lang_of("discord", load_preferences())
        for user_install in (True, False):
            try:
                await rest.call("PUT", f"/applications/{app_id}/commands", slash_commands(lang, user_install))
                return
            except DiscordError as exc:
                if exc.status == 400 and user_install:
                    continue  # the app has no "User Install" context: server-and-DM commands only
                log.info("discord: slash commands were not registered: %s", redact(str(exc)))
                return
            except httpx.HTTPError as exc:
                log.info("discord: slash commands were not registered: %s", redact(str(exc)))
                return

    def _spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        task.add_done_callback(_log_failure)

    def _dispatch(self, engine: core.Engine, adapter: DiscordAdapter, rest: Rest, kind: str, data: Dict[str, Any]) -> None:
        if kind == "READY":
            self.session_id = str(data.get("session_id") or "")
            core.set_status("discord", "online", "")
        elif kind == "MESSAGE_CREATE":
            author = data.get("author") or {}
            if author.get("bot") or data.get("guild_id") or str(author.get("id")) == self.user.get("id"):
                return
            text = str(data.get("content") or "")
            if not text and data.get("attachments"):
                text = ""
            self._spawn(engine.on_text(str(author.get("id")), str(author.get("global_name") or author.get("username") or ""),
                                       str(data.get("channel_id")), text))
        elif kind == "INTERACTION_CREATE":
            self._spawn(self._interaction(engine, adapter, rest, data))

    async def _interaction(self, engine: core.Engine, adapter: DiscordAdapter, rest: Rest, data: Dict[str, Any]) -> None:
        user = data.get("user") or (data.get("member") or {}).get("user") or {}
        user_id, channel = str(user.get("id") or ""), str(data.get("channel_id") or (data.get("channel") or {}).get("id") or "")
        kind = int(data.get("type") or 0)
        callback = f"/interactions/{data.get('id')}/{data.get('token')}/callback"
        info = data.get("data") or {}
        if data.get("guild_id"):
            await rest.call("POST", callback, {"type": 4, "data": {"content": "MemuAI works in direct messages only.", "flags": 64}})
            return
        if kind == 2:
            await rest.call("POST", callback, {"type": 5})
            name = str(info.get("name") or "menu")
            options = {str(o.get("name")): str(o.get("value") or "") for o in info.get("options") or []}
            argument = options.get("code") or options.get("name") or options.get("search") or ""
            reply = {"channel": channel, "token": str(data.get("token"))}
            pending = adapter._pending_reply.set(reply)
            try:
                await engine.on_text(user_id, str(user.get("global_name") or user.get("username") or ""), channel,
                                     f"/{name} {argument}".strip())
            finally:
                adapter._pending_reply.reset(pending)
                leftover = reply.pop("token", "")
                if leftover:
                    try:
                        await rest.call("DELETE", f"/webhooks/{adapter.app_id}/{leftover}/messages/@original")
                    except (DiscordError, httpx.HTTPError):
                        pass
        elif kind == 3:
            await rest.call("POST", callback, {"type": 6})
            custom = str(info.get("custom_id") or "")
            values = info.get("values") or []
            if custom.startswith("sel:") and values:
                await engine.on_action(user_id, channel, str(values[0]))
            elif custom:
                await engine.on_action(user_id, channel, custom)


def _log_failure(task: "asyncio.Task") -> None:
    if not task.cancelled() and task.exception() is not None:
        log.warning("discord handler failed: %s", redact(repr(task.exception())))


async def supervise() -> None:
    """Keep a gateway session running while Discord is enabled in MemuAI settings."""
    from .pgdata import load_preferences
    failures = 0
    while True:
        prefs = load_preferences()
        token = str(prefs.get("discord_bot_token") or "").strip()
        if not prefs.get("discord_enabled") or not token:
            core.set_status("discord", "off", "")
            await asyncio.sleep(5)
            continue
        if not valid_token(token):
            core.set_status("discord", "unauthorized", "The bot token does not look like a Discord token", code="bad_token")
            await asyncio.sleep(10)
            continue
        bot = DiscordBot(token)
        started = time.monotonic()
        reason = "reconnect"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=10)) as client:
                runner = asyncio.create_task(bot.run(client))
                while not runner.done():
                    await asyncio.sleep(3)
                    fresh = load_preferences()
                    if not fresh.get("discord_enabled") or str(fresh.get("discord_bot_token") or "").strip() != token:
                        runner.cancel()
                        break
                reason = await asyncio.gather(runner, return_exceptions=True)
                reason = reason[0] if reason and isinstance(reason[0], str) else "reconnect"
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - reconnect with backoff
            core.set_status("discord", "reconnecting", redact(str(exc))[:200], code="network")
            log.info("discord session ended: %s", redact(repr(exc)))
        if reason == "unauthorized" or reason == "intents":
            while str(load_preferences().get("discord_bot_token") or "").strip() == token and load_preferences().get("discord_enabled"):
                await asyncio.sleep(5)
            continue
        failures = 0 if time.monotonic() - started > 120 else failures + 1
        await asyncio.sleep(min(60.0, 1.5 * 2 ** min(failures, 5)) * (0.7 + 0.6 * random.random()))
