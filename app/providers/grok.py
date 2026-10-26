"""Провайдер Grok (grok.com): WebSocket-гатвей mgw + кука sso.

Референс — grok-to-openai: сессия создаётся по wss://grok.com/ws/mgw/?uid=<user_id>,
uid берётся из /api/auth/session по кукам. Дельты приходят событиями
response.chunk / response.output_text.delta; reasoning — NOTETAKER-канал.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from .base import (
    Field,
    Provider,
    ProviderAuthError,
    ProviderError,
    ProviderNetworkError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderServerError,
    normalize_messages,
    transcript_prompt,
)
from ..config import config_store
from . import http

BASE = "https://grok.com"
WS_URL = "wss://grok.com/ws/mgw/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36")

SEED = [
    ("grok-fast", "быстрый режим"),
    ("grok-auto", "авто-выбор"),
    ("grok-expert", "экспертный режим"),
    ("grok-heavy", "Heavy"),
]

_RENDER_RE = re.compile(r"<grok:render>.*?</grok:render>", re.S)

def _unseen(shape_text: str, emitted: str, delta: str) -> str:
    """Часть ``shape_text``, которой ещё нет в уже отданном тексте."""
    if shape_text.startswith(emitted):
        return shape_text[len(emitted):]
    if emitted.startswith(shape_text):
        return ""
    return delta

class GrokProvider(Provider):
    id = "grok"
    title = "Grok"
    icon = "𝕏"
    title_en = "Grok"
    description = "grok.com: кука sso из браузера, чат через WebSocket-шлюз."
    description_en = "grok.com: the sso cookie from your browser, chat over the WebSocket gateway."

    def account_fields(self) -> List[Field]:
        return [
            Field("cookies", "Кука sso (или вся строка Cookie)", "textarea",
                  placeholder="sso=eyJhbGciOi…  или вся строка cookie из DevTools",
                  help="ШАГИ: 1) откройте grok.com и войдите; 2) F12 → вкладка Network → "
                       "обновите страницу → кликните первый запрос к grok.com → Headers → "
                       "Request Headers → найдите строку cookie: и скопируйте кусок от «sso=» "
                       "до первого «;». Можно вставить и всю строку целиком — лишнее отфильтруется.",
                  label_en="sso cookie (or the whole Cookie header)",
                  placeholder_en="sso=eyJhbGciOi…  or the full cookie string from DevTools",
                  help_en="STEPS: 1) open grok.com and sign in; 2) F12 → Network tab → reload → "
                          "click the first grok.com request → Headers → Request Headers → find the "
                          "cookie: line and copy the part from “sso=” up to the first “;”. Pasting "
                          "the whole string also works — the rest is filtered out."),
            Field("model_mode", "Режим по умолчанию", "select", required=False, default="fast",
                  options=["fast", "auto", "expert", "heavy"], label_en="Default mode"),
        ]

    def seed_models(self) -> List[Tuple[str, str]]:
        return SEED

    @staticmethod
    def _cookie(account) -> str:
        raw = str(account.fields.get("cookies", "")).strip()
        if not raw:
            return ""
        if "sso=" in raw:

            parts = [p.strip() for p in raw.replace("\n", "; ").split(";") if "=" in p]
            return "; ".join(parts)
        return f"sso={raw}"

    async def _user_id(self, account) -> str:
        uid = str(account.extra.get("user_id", ""))
        if uid:
            return uid
        try:
            session = await http.get_session(self.proxy_for(account))
            resp = await session.get(f"{BASE}/api/auth/session",
                                     headers={"Cookie": self._cookie(account), "User-Agent": UA,
                                              "Accept": "application/json"}, timeout=30)
        except Exception as e:
            raise ProviderNetworkError(f"auth/session: {e}")
        if resp.status_code in (401, 403):
            raise ProviderAuthError("The sso cookie was rejected")

        if resp.status_code == 429:
            raise ProviderRateLimitError("grok.com is rate limiting auth/session (HTTP 429)")
        if resp.status_code >= 500:
            raise ProviderServerError(f"grok.com auth/session failed (HTTP {resp.status_code})", 502)
        if resp.status_code != 200:
            raise ProviderAuthError(f"Could not fetch user_id (HTTP {resp.status_code})")
        try:
            data = resp.json()
            uid = str((data.get("user") or {}).get("id") or data.get("userId") or data.get("session", {}).get("userId") or "")
        except Exception:
            uid = ""
        if not uid:
            raise ProviderAuthError("auth/session returned no user id — the cookie is invalid")
        account.extra["user_id"] = uid
        from ..state import state as app_state
        app_state.save_accounts()
        return uid

    @staticmethod
    def _find_session_id(obj: Any, depth: int = 0) -> str:
        """Ищет непустой session_id на первых уровнях структуры события."""
        if depth > 3 or not isinstance(obj, dict):
            return ""
        v = obj.get("session_id") or obj.get("sessionId") or obj.get("sid")
        if isinstance(v, str) and v:
            return v
        for child in obj.values():
            if isinstance(child, dict):
                found = GrokProvider._find_session_id(child, depth + 1)
                if found:
                    return found
        return ""

    async def chat_stream(self, account, model: str, messages: List[Dict[str, Any]],
                          params: Dict[str, Any]) -> AsyncIterator[Dict[str, Any]]:
        from .base import messages_have_images
        if messages_have_images(messages):
            raise ProviderServerError(
                "Текущий адаптер Grok не поддерживает изображения. "
                "Удалите вложение или выберите мультимодальную модель другого провайдера.", 400)
        try:
            import websockets
        except ImportError:
            raise ProviderServerError("The websockets library is missing (pip install websockets)")

        uid = await self._user_id(account)
        mode = model.replace("grok-", "", 1) if model.startswith("grok-") else str(
            account.fields.get("model_mode", "fast") or "fast")
        if mode not in ("fast", "auto", "expert", "heavy"):
            mode = "fast"

        system_text, history, final_user = normalize_messages(messages)

        prompt = transcript_prompt(system_text, history, final_user)
        input_chunks: List[Dict[str, Any]] = [{"text": {"text": prompt}}]

        import ssl
        ws_headers = {
            "Origin": BASE, "User-Agent": UA, "Cookie": self._cookie(account),
            "Accept-Language": "en-US,en;q=0.9",
        }
        proxy = self.proxy_for(account) or None
        connect_kwargs: Dict[str, Any] = {
            "additional_headers": ws_headers,
            "ssl": ssl.create_default_context(),
        }
        if proxy and proxy.startswith(("socks", "http")):

            try:
                connect_kwargs["proxy"] = proxy
            except TypeError:
                pass

        session_id = ""
        acc_text = ""

        shape_acc = {"chunk": "", "delta": ""}
        got_result = False

        try:
            async with websockets.connect(f"{WS_URL}?uid={uid}", max_size=20 * 1024 * 1024,
                                          **connect_kwargs) as ws:
                await ws.send(json.dumps({
                    "event": {"type": "session.create", "event_id": f"evt_init_{uuid.uuid4().hex[:8]}",
                              "session": {"model": mode, "x_grok": {
                                  "use_chunk": True, "enable_image_generation": False,
                                  "image_generation_count": 2, "disable_artifact": True,
                                  "is_temporary": True, "disable_memory": True,
                                  "keep_context": False,
                                  "protocol_capabilities": ["conversation_attached", "custom_methods_v1"],
                              }}}}))
                item_event = {
                    "session_id": "",
                    "event": {"type": "conversation.item.create",
                              "event_id": f"evt_msg_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}",
                              "item": {"type": "message", "role": "user",
                                       "x_grok": {"client_message_id": str(uuid.uuid4()),
                                                  "input_chunks": input_chunks}}},
                }
                resp_event = {"session_id": "", "event": {"type": "response.create",
                                                          "event_id": f"evt_resp_{uuid.uuid4().hex[:8]}"}}
                sent_item = False

                frames = ws.__aiter__()
                while True:
                    try:
                        raw = await asyncio.wait_for(
                            anext(frames), timeout=config_store.settings.stream_idle_timeout)
                    except StopAsyncIteration:
                        break
                    except asyncio.TimeoutError:
                        raise ProviderNetworkError(
                            f"Grok sent no data for over "
                            f"{config_store.settings.stream_idle_timeout} с")
                    try:
                        frame = json.loads(raw)
                    except Exception:
                        continue
                    ev = frame.get("event") or {}
                    etype = ev.get("type", "")

                    if etype == "session.created":
                        session_id = self._find_session_id(frame)
                        item_event["session_id"] = session_id
                        resp_event["session_id"] = session_id
                        await ws.send(json.dumps(item_event))
                        await ws.send(json.dumps(resp_event))
                        sent_item = True
                        continue
                    if not sent_item:

                        session_id = self._find_session_id(frame)
                        if session_id:
                            item_event["session_id"] = session_id
                            resp_event["session_id"] = session_id
                            await ws.send(json.dumps(item_event))
                            await ws.send(json.dumps(resp_event))
                            sent_item = True
                        continue

                    if etype in ("response.chunk",):
                        chunk = ev.get("chunk") or {}
                        text_obj = chunk.get("text") or {}
                        text = str(text_obj.get("text") or "")
                        if text and str(text_obj.get("channel", "")) == "NOTETAKER":
                            yield {"type": "reasoning", "text": text}
                        elif text:
                            clean = _RENDER_RE.sub("", text)
                            if clean:
                                shape_acc["chunk"] += clean
                                fresh = _unseen(shape_acc["chunk"], acc_text, clean)
                                if fresh:
                                    acc_text += fresh
                                    yield {"type": "text", "text": fresh}
                    elif etype in ("response.output_text.delta",):
                        text = str((ev.get("delta") or ""))
                        if text:
                            shape_acc["delta"] += text
                            fresh = _unseen(shape_acc["delta"], acc_text, text)
                            if fresh:
                                acc_text += fresh
                                yield {"type": "text", "text": fresh}
                    elif etype in ("response.reasoning_text.delta",):
                        text = str(ev.get("delta") or "")
                        if text:
                            yield {"type": "reasoning", "text": text}
                    elif etype == "response.grok.output":
                        out = ev.get("output") or {}
                        if isinstance(out, dict) and out.get("stream_error"):
                            stream_error = out["stream_error"]
                            kind = str(stream_error.get("kind", "") if isinstance(stream_error, dict)
                                       else stream_error)
                            lowered_kind = kind.lower()
                            if "rate" in lowered_kind:
                                raise ProviderRateLimitError("Rate limit Grok")
                            if any(marker in lowered_kind for marker in ("usage", "quota", "free", "limit")):
                                raise ProviderQuotaError("The Grok limit is used up")
                            raise ProviderServerError(f"Grok error: {out['stream_error']}")
                    elif etype == "error":
                        error = ev.get("error")
                        msg = str(error.get("message", ev) if isinstance(error, dict) else (error or ev))
                        low = msg.lower()
                        if "auth" in low or "cookie" in low or "unauthorized" in low:
                            raise ProviderAuthError(f"Grok: {msg}")
                        if "quota" in low or "usage limit" in low or "free limit" in low:
                            raise ProviderQuotaError(f"Grok: {msg}")
                        if "rate limit" in low or "429" in low:
                            raise ProviderRateLimitError(f"Grok: {msg}")
                        raise ProviderServerError(f"Grok: {msg}")
                    elif etype == "response.done":
                        got_result = True
                        break
                if not got_result and not acc_text:
                    raise ProviderServerError("Grok closed the stream without an answer")
        except ProviderError:
            raise
        except Exception as e:

            response = getattr(e, "response", None)
            status = getattr(response, "status_code", None) or getattr(e, "status_code", None)
            if status == 401:
                raise ProviderAuthError(f"Grok rejected the sso cookie (WebSocket HTTP {status})")
            if status == 429:
                raise ProviderRateLimitError("Grok rate limit (WebSocket HTTP 429)")
            raise ProviderNetworkError(f"WebSocket: {type(e).__name__}: {e}")

    async def check(self, account) -> Tuple[Optional[bool], str]:
        try:
            uid = await self._user_id(account)
        except ProviderAuthError as e:
            return False, str(e)
        except ProviderError as e:
            return None, str(e)
        return True, f"подключено · uid {uid[:12]}…"

    async def usage(self, account) -> Optional[Dict[str, Any]]:
        return None
