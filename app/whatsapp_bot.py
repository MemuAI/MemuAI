"""MemuAI WhatsApp bot: chat with MemuAI and control it from WhatsApp.

Two ways to connect, both run inside MemuAI Proxy (``app.messenger_runtime``):

* **WhatsApp Cloud API** (official, a business number from Meta). Messages arrive at the webhook
  ``/messengers/whatsapp/webhook`` of MemuAI Proxy, which must be reachable over HTTPS (a tunnel such as
  Cloudflare Tunnel). Every call is checked with the app secret (``X-Hub-Signature-256``); a request without a
  valid signature is refused. Only the person paired with a one-time code is served.
* **WAHA** (WhatsApp HTTP API, self-hosted, runs on your computer). MemuAI reads one chat you name in the
  settings — your own number or a separate bot number — and answers there. Nothing is exposed to the internet.

Commands, buttons and lists are those of ``app.messenger_core``; replies are converted to WhatsApp formatting.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional

import httpx
from fastapi import Request

from . import messenger_core as core

log = logging.getLogger("memuai.whatsapp")

GRAPH = "https://graph.facebook.com/v21.0"
POLL_SECONDS = 2.5


def redact(text: str) -> str:
    return re.sub(r"(EAA[A-Za-z0-9]{10,}|Bearer\s+\S+)", "[token]", str(text or ""))


def render_cloud(to: str, card: core.Card) -> List[Dict[str, Any]]:
    """A card as one or two Cloud API messages: a list for choices, reply buttons (max 3) otherwise."""
    text = core.to_whatsapp(card.text)
    base = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to}
    out: List[Dict[str, Any]] = []
    flat = [b for line in card.buttons for b in line]
    if card.choices:
        rows = [{"id": c.value[:200], "title": core.short(c.label.replace("● ", ""), 24),
                 **({"description": core.short(c.description, 72)} if c.description else {})} for c in card.choices[:10]]
        out.append({**base, "type": "interactive", "interactive": {
            "type": "list", "body": {"text": core.short(text, 1024)},
            "action": {"button": core.short(card.placeholder or card.title or "…", 20),
                       "sections": [{"title": core.short(card.title or "MemuAI", 24), "rows": rows}]}}})
        if flat:
            out.append({**base, "type": "interactive", "interactive": {
                "type": "button", "body": {"text": "…"},
                "action": {"buttons": [{"type": "reply", "reply": {"id": b.id[:256], "title": core.short(b.label, 20)}} for b in flat[:3]]}}})
        return out
    if flat and len(text) <= 1024:
        out.append({**base, "type": "interactive", "interactive": {
            "type": "button", "body": {"text": text},
            "action": {"buttons": [{"type": "reply", "reply": {"id": b.id[:256], "title": core.short(b.label, 20)}} for b in flat[:3]]}}})
        return out
    out.append({**base, "type": "text", "text": {"body": text[:4096], "preview_url": False}})
    if flat:
        out.append({**base, "type": "interactive", "interactive": {
            "type": "button", "body": {"text": "…"},
            "action": {"buttons": [{"type": "reply", "reply": {"id": b.id[:256], "title": core.short(b.label, 20)}} for b in flat[:3]]}}})
    return out


def render_plain(card: core.Card, lang: str) -> str:
    """For WAHA: the text, numbered choices («reply 3») and the commands of the buttons."""
    lines = [core.to_whatsapp(card.text)]
    rows = core.numbered(card) if card.choices else []
    if rows:
        lines.append("")
        lines += [f"*{number}.* {label}" + (f" — {description}" if description else "") for number, label, _, description in rows]
        lines.append(core.tr(lang, "_Ответьте номером, чтобы выбрать._", "_Reply with a number to pick._"))
    flat = [b for line in card.buttons for b in line]
    commands = [f"/{b.id}" for b in flat if ":" not in b.id and b.id in core.COMMANDS]
    if commands:
        lines.append(" · ".join(dict.fromkeys(commands)))
    return "\n".join(lines)


def _obj(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dicts(value: Any) -> List[Dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def parse_webhook(payload: Dict[str, Any]) -> List[Dict[str, str]]:
    """Incoming user messages of a Cloud API webhook: id, from, name, text or action."""
    found: List[Dict[str, str]] = []
    for entry in _dicts(payload.get("entry") if isinstance(payload, dict) else None):
        for change in _dicts(entry.get("changes")):
            value = change.get("value") if isinstance(change.get("value"), dict) else {}
            names = {str(c.get("wa_id")): str((c.get("profile") or {}).get("name") or "") if isinstance(c.get("profile"), dict) else ""
                     for c in _dicts(value.get("contacts"))}
            for message in _dicts(value.get("messages")):
                sender = str(message.get("from") or "")
                row = {"id": str(message.get("id") or ""), "from": sender, "name": names.get(sender, ""), "text": "", "action": ""}
                kind = message.get("type")
                if kind == "text":
                    row["text"] = str(_obj(message.get("text")).get("body") or "")
                elif kind == "interactive":
                    inter = _obj(message.get("interactive"))
                    reply = _obj(inter.get("button_reply")) or _obj(inter.get("list_reply"))
                    row["action"] = str(reply.get("id") or "")
                elif kind == "button":
                    row["action"] = str(_obj(message.get("button")).get("payload") or "")
                if sender and (row["text"] or row["action"]):
                    found.append(row)
    return found


class CloudAdapter(core.Adapter):
    platform = "whatsapp"
    limit = 4000
    can_edit = False

    def __init__(self, client: httpx.AsyncClient, token: str, phone_id: str) -> None:
        self.client, self.token, self.phone_id = client, token, phone_id
        self.last_incoming: Dict[str, str] = {}

    async def _post(self, body: Dict[str, Any]) -> Dict[str, Any]:
        response = await self.client.post(f"{GRAPH}/{self.phone_id}/messages", json=body,
                                          headers={"Authorization": f"Bearer {self.token}"})
        if response.status_code >= 400:
            raise RuntimeError(redact(response.text)[:300])
        return response.json() if response.content else {}

    async def send(self, peer: str, card: core.Card) -> Optional[str]:
        last = None
        for body in render_cloud(peer, card):
            try:
                data = await self._post(body)
                last = str(((data.get("messages") or [{}])[0]).get("id") or "") or last
            except (RuntimeError, httpx.HTTPError) as exc:
                log.warning("whatsapp send failed: %s", redact(str(exc)))
        return last

    async def typing(self, peer: str) -> None:
        message_id = self.last_incoming.get(peer)
        if not message_id:
            return
        try:
            await self._post({"messaging_product": "whatsapp", "status": "read", "message_id": message_id,
                              "typing_indicator": {"type": "text"}})
        except (RuntimeError, httpx.HTTPError):
            pass


class WahaAdapter(core.Adapter):
    platform = "whatsapp"
    limit = 4000
    can_edit = False

    def __init__(self, client: httpx.AsyncClient, base: str, key: str, session: str) -> None:
        self.client, self.base, self.session = client, base.rstrip("/"), session or "default"
        self.headers = {"X-Api-Key": key} if key else {}
        self.sent: Deque[str] = deque(maxlen=300)
        self.sent_texts: Deque[str] = deque(maxlen=60)

    def is_echo(self, row: Dict[str, Any], self_chat: bool) -> bool:
        """True for MemuAI's own replies. In a chat with another person only their messages count; in
        "Message yourself" your messages count too, except the ones MemuAI sent."""
        message_id = _waha_id(row)
        if message_id and _short_id(message_id) in self.sent:
            return True
        if not row.get("fromMe"):
            return False
        if not self_chat:
            return True
        return _norm_text(row.get("body")) in self.sent_texts

    async def me(self) -> str:
        try:
            response = await self.client.get(f"{self.base}/api/sessions/{self.session}/me", headers=self.headers)
            data = response.json() if response.status_code < 400 and response.content else {}
        except (httpx.HTTPError, ValueError):
            return ""
        return str((data or {}).get("id") or "") if isinstance(data, dict) else ""

    async def send(self, peer: str, card: core.Card) -> Optional[str]:
        from .pgdata import load_preferences
        text = render_plain(card, core.lang_of("whatsapp", load_preferences()))
        self.sent_texts.append(_norm_text(text))
        try:
            response = await self.client.post(f"{self.base}/api/sendText", headers=self.headers,
                                              json={"session": self.session, "chatId": peer, "text": text})
            data = response.json() if response.content else {}
            message_id = _waha_id(data)
            if message_id:
                self.sent.append(_short_id(message_id))
            return message_id or None
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("waha send failed: %s", redact(str(exc)))
            return None

    async def typing(self, peer: str) -> None:
        try:
            await self.client.post(f"{self.base}/api/startTyping", headers=self.headers, json={"session": self.session, "chatId": peer})
        except httpx.HTTPError:
            pass

    async def messages(self, chat: str) -> List[Dict[str, Any]]:
        response = await self.client.get(f"{self.base}/api/{self.session}/chats/{chat}/messages", headers=self.headers,
                                         params={"limit": 20, "downloadMedia": "false"})
        if response.status_code >= 400:
            code = {401: "waha_auth", 403: "waha_auth", 404: "waha_session", 422: "waha_session"}.get(response.status_code, "")
            raise WahaError(f"WAHA {response.status_code}: {redact(response.text)[:200]}", code)
        data = response.json()
        return data if isinstance(data, list) else []


class WahaError(RuntimeError):
    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(message)
        self.code = code


def _waha_id(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    value = data.get("id")
    if isinstance(value, dict):
        value = value.get("_serialized") or value.get("id")
    return str(value or (data.get("key") or {}).get("id") or "")


def _short_id(value: str) -> str:
    """WAHA engines report ids as ``true_<chat>_<id>`` or just ``<id>``; compare the last part."""
    return str(value or "").rsplit("_", 1)[-1]


def _norm_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:300]


def waha_chat_id(value: str) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"[\w.\-]+@(c\.us|s\.whatsapp\.net|lid)", text):
        return text
    digits = re.sub(r"\D", "", text)
    return f"{digits}@c.us" if 7 <= len(digits) <= 15 else ""


class CloudRuntime:
    """The webhook side: one engine per configuration, a short memory of handled message ids."""

    def __init__(self) -> None:
        self.key = ""
        self.engine: Optional[core.Engine] = None
        self.adapter: Optional[CloudAdapter] = None
        self.client: Optional[httpx.AsyncClient] = None
        self.seen: Deque[str] = deque(maxlen=500)
        self.tasks: set = set()

    def ensure(self, prefs: Dict[str, Any]) -> Optional[core.Engine]:
        token = str(prefs.get("whatsapp_access_token") or "").strip()
        phone = str(prefs.get("whatsapp_phone_id") or "").strip()
        if not (prefs.get("whatsapp_enabled") and prefs.get("whatsapp_mode", "cloud") == "cloud" and token and phone):
            return None
        key = f"{phone}:{hash(token)}"
        if key != self.key or self.engine is None:
            self.client = self.client or httpx.AsyncClient(timeout=httpx.Timeout(20, connect=10))
            self.adapter = CloudAdapter(self.client, token, phone)
            self.engine = core.Engine(self.adapter)
            self.key = key
        return self.engine

    def handle(self, payload: Dict[str, Any], prefs: Dict[str, Any]) -> int:
        engine = self.ensure(prefs)
        if engine is None:
            return 0
        count = 0
        for row in parse_webhook(payload):
            if row["id"] in self.seen:
                continue
            self.seen.append(row["id"])
            if self.adapter is not None:
                self.adapter.last_incoming[row["from"]] = row["id"]
            coro = engine.on_action(row["from"], row["from"], row["action"]) if row["action"] else \
                engine.on_text(row["from"], row["name"], row["from"], row["text"])
            task = asyncio.ensure_future(coro)
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
            count += 1
        if count:
            core.set_status("whatsapp", "online", "")
        return count


cloud = CloudRuntime()


def register(app: Any) -> None:
    """The Cloud API webhook on MemuAI Proxy: GET — Meta's verification, POST — signed messages."""
    from fastapi.responses import JSONResponse, PlainTextResponse

    @app.get("/messengers/whatsapp/webhook")
    async def whatsapp_verify(request: Request):
        from .pgdata import load_preferences
        prefs = load_preferences()
        params = request.query_params
        expected = str(prefs.get("whatsapp_verify_token") or "")
        if (params.get("hub.mode") == "subscribe" and expected and len(expected) >= 16
                and core.hmac.compare_digest(str(params.get("hub.verify_token") or ""), expected)):
            core.set_status("whatsapp", "online", "", webhook={"verified": time.time()})
            return PlainTextResponse(str(params.get("hub.challenge") or "")[:200])
        return JSONResponse({"error": "forbidden"}, 403)

    @app.post("/messengers/whatsapp/webhook")
    async def whatsapp_incoming(request: Request):
        from .pgdata import load_preferences
        prefs = load_preferences()
        body = await request.body()
        if len(body) > 1_000_000:
            return JSONResponse({"error": "too large"}, 413)
        secret = str(prefs.get("whatsapp_app_secret") or "")
        if not core.verify_signature(secret, body, request.headers.get("x-hub-signature-256", "")):
            return JSONResponse({"error": "bad signature"}, 403)
        try:
            payload = json.loads(body.decode("utf-8"))
        except ValueError:
            return JSONResponse({"error": "bad json"}, 400)
        cloud.handle(payload if isinstance(payload, dict) else {}, prefs)
        return {"ok": True}


async def _waha_loop(prefs: Dict[str, Any]) -> str:
    from .pgdata import load_preferences
    base = str(prefs.get("whatsapp_waha_url") or "http://127.0.0.1:3000").strip()
    chat = waha_chat_id(prefs.get("whatsapp_waha_chat"))
    if not chat:
        core.set_status("whatsapp", "error", "Enter the WhatsApp number of the chat MemuAI should answer in", code="no_chat")
        return "config"
    key = str(prefs.get("whatsapp_waha_api_key") or "")
    session = str(prefs.get("whatsapp_waha_session") or "default")
    signature = (base, chat, key, session)
    async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=8), trust_env=False) as client:
        adapter = WahaAdapter(client, base, key, session)
        engine = core.Engine(adapter)

        def owner(data: Dict[str, Any]) -> None:
            data["whatsapp"]["owner"] = {"id": chat, "name": chat.split("@")[0], "since": time.time()}
        core.update_state(owner)
        me = await adapter.me()
        self_chat = bool(me) and re.sub(r"\D", "", me.split("@")[0]) == re.sub(r"\D", "", chat.split("@")[0])
        seen: Deque[str] = deque(maxlen=400)
        started = time.time()
        first = True
        tasks: set = set()
        while True:
            fresh = load_preferences()
            now_signature = (str(fresh.get("whatsapp_waha_url") or "http://127.0.0.1:3000").strip(), waha_chat_id(fresh.get("whatsapp_waha_chat")),
                             str(fresh.get("whatsapp_waha_api_key") or ""), str(fresh.get("whatsapp_waha_session") or "default"))
            if not fresh.get("whatsapp_enabled") or fresh.get("whatsapp_mode") != "waha" or now_signature != signature:
                return "changed"
            try:
                rows = await adapter.messages(chat)
                core.set_status("whatsapp", "online", "", bot={"name": "WAHA", "chat": chat})
            except WahaError as exc:
                core.set_status("whatsapp", "reconnecting", redact(str(exc))[:200], code=exc.code)
                await asyncio.sleep(8)
                continue
            except (httpx.HTTPError, ValueError) as exc:
                core.set_status("whatsapp", "reconnecting", redact(str(exc))[:200],
                                code="waha_unreachable" if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)) else "")
                await asyncio.sleep(8)
                continue
            for row in sorted(rows, key=lambda r: float(r.get("timestamp") or 0)):
                message_id = _waha_id(row)
                if not message_id or message_id in seen:
                    continue
                seen.append(message_id)
                if first or float(row.get("timestamp") or 0) < started - 5:
                    continue
                if adapter.is_echo(row, self_chat):
                    continue
                text = str(row.get("body") or "").strip()
                if not text:
                    continue
                task = asyncio.ensure_future(engine.on_text(chat, "", chat, text))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
            first = False
            await asyncio.sleep(POLL_SECONDS)


async def supervise() -> None:
    from .pgdata import load_preferences
    while True:
        prefs = load_preferences()
        if not prefs.get("whatsapp_enabled"):
            core.set_status("whatsapp", "off", "")
            await asyncio.sleep(5)
            continue
        mode = prefs.get("whatsapp_mode") or "cloud"
        if mode == "waha":
            try:
                reason = await _waha_loop(prefs)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                core.set_status("whatsapp", "reconnecting", redact(str(exc))[:200])
                reason = "error"
            await asyncio.sleep(5 if reason in ("config", "error") else 0.5)
            continue
        ready = bool(prefs.get("whatsapp_access_token") and prefs.get("whatsapp_phone_id") and prefs.get("whatsapp_app_secret"))
        if not ready:
            core.set_status("whatsapp", "error", "Fill in the access token, phone number ID and app secret", code="cloud_config")
        else:
            part = core.load_state().get("whatsapp") or {}
            if part.get("status") not in ("online",) or time.time() - float(part.get("heartbeat") or 0) > 60:
                core.set_status("whatsapp", "waiting", "", webhook=part.get("webhook") or {})
        await asyncio.sleep(10)
