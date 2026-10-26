"""Провайдер Kiro (AWS CodeWhisperer): OAuth через браузер + чат.

Два входа: PKCE через браузер (колбэк на localhost:3128, как в Kiro IDE)
и Builder ID через device code (без локального порта). Работает и вставка
готового kiro-auth-token.json.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import secrets
import time
import uuid
import webbrowser
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from .base import (
    Field,
    Provider,
    ProviderAuthError,
    ProviderModelUnavailableError,
    ProviderError,
    ProviderNetworkError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderServerError,
    normalize_messages,
    transcript_prompt,
    spawn_background,
)
from ..config import config_store
from . import http

log = logging.getLogger("memuai.kiro")

KIRO_REGION = "us-east-1"
TOKEN_URL = f"https://prod.{KIRO_REGION}.auth.desktop.kiro.dev/oauth/token"
REFRESH_URL = f"https://prod.{KIRO_REGION}.auth.desktop.kiro.dev/refreshToken"
Q_BASE = f"https://q.{KIRO_REGION}.amazonaws.com"

DATA_PLANE_HOSTS = (
    "https://codewhisperer.{region}.amazonaws.com",
    "https://q.{region}.amazonaws.com",
    "https://prod.{region}.codewhisperer.amazonaws.com",
)

CONTROL_HOSTS = (
    "https://codewhisperer.{region}.amazonaws.com",
    "https://q.{region}.amazonaws.com",
)
DEFAULT_PROFILE_ARN = "arn:aws:codewhisperer:us-east-1:699475941385:profile/EHGA3GRVQMUK"
KIRO_VERSION = "0.12.333"
REDIRECT_URI = "http://localhost:3128"
CALLBACK_PATH = "/oauth/callback"

SEED = [
    ("auto", "automatic model choice по задаче"),
    ("claude-sonnet-4-5", "Claude Sonnet 4.5 · available on the free plan"),
    ("claude-haiku-4.5", "Claude Haiku 4.5"),
    ("claude-sonnet-4", "Claude Sonnet 4"),
    ("claude-opus-4-5", "Claude Opus 4.5 · requires a paid plan"),
]

_MODEL_ERROR_MARKERS = ("INVALID_MODEL_ID", "invalid model", "model is not supported",
                        "not authorized to use model", "modelid")
_THROTTLE_MARKERS = ("throttl", "too many requests", "rate exceeded", "slow down")

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")

async def _read_head(resp, limit: int = 1500) -> str:
    """Безопасно читает начало тела stream-ответа для диагностики."""
    try:
        head = ""
        async for chunk in resp.aiter_content():
            head += chunk.decode("utf-8", errors="replace") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
            if len(head) >= limit:
                break
        return head[:limit]
    except Exception:
        try:
            return str(resp.text)[:limit]
        except Exception:
            return ""

async def _safe_close(resp) -> None:

    await http.close_response(resp)

def _ua() -> str:
    fp = hashlib.sha256(f"memuai-{os.getuid() if hasattr(os, 'getuid') else 'win'}".encode()).hexdigest()[:16]
    return (f"aws-sdk-js/1.0.34 ua/2.1 os/win32#10.0.29580 lang/js md/nodejs#22.22.0 "
            f"api/codewhispererstreaming#1.0.34 m/E KiroIDE-{KIRO_VERSION}-{fp}")

_FRAME_PRELUDE = 12
_FRAME_SUFFIX = 4
_FRAME_MAX = 32 * 1024 * 1024

def _parse_event_headers(raw: bytes) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    index = 0
    size = len(raw)
    while index < size:
        name_len = raw[index]
        index += 1
        name = raw[index:index + name_len].decode("utf-8", "replace")
        index += name_len
        if index >= size:
            break
        value_type = raw[index]
        index += 1
        if value_type in (0, 1):
            value = "true" if value_type == 0 else "false"
        elif value_type == 2:
            value, index = str(raw[index]), index + 1
        elif value_type in (3, 4, 5, 8):
            width = {3: 2, 4: 4, 5: 8, 8: 8}[value_type]
            value = str(int.from_bytes(raw[index:index + width], "big"))
            index += width
        elif value_type in (6, 7):
            width = int.from_bytes(raw[index:index + 2], "big")
            index += 2
            chunk = raw[index:index + width]
            index += width
            value = chunk.decode("utf-8", "replace") if value_type == 7 else chunk.hex()
        elif value_type == 9:
            value, index = raw[index:index + 16].hex(), index + 16
        else:
            break
        headers[name] = value
    return headers

def _take_event_frames(buffer: bytearray) -> List[Tuple[Dict[str, str], bytes]]:
    """Забирает из буфера все целые кадры. Неполный хвост остаётся в буфере."""
    frames: List[Tuple[Dict[str, str], bytes]] = []
    while len(buffer) >= _FRAME_PRELUDE:
        total = int.from_bytes(buffer[0:4], "big")
        headers_len = int.from_bytes(buffer[4:8], "big")
        if total < _FRAME_PRELUDE + _FRAME_SUFFIX or total > _FRAME_MAX:
            break
        if headers_len > total - _FRAME_PRELUDE - _FRAME_SUFFIX:
            break
        if len(buffer) < total:
            break
        frame = bytes(buffer[:total])
        del buffer[:total]
        headers = _parse_event_headers(frame[_FRAME_PRELUDE:_FRAME_PRELUDE + headers_len])
        payload = frame[_FRAME_PRELUDE + headers_len:total - _FRAME_SUFFIX]
        frames.append((headers, payload))
    return frames

def _payload_json(payload: bytes) -> Dict[str, Any]:
    try:
        obj = json.loads(payload.decode("utf-8", "replace") or "{}")
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}

class KiroProvider(Provider):
    id = "kiro"
    title = "Kiro"
    icon = "⌘"
    oauth = True
    oauth_port = 3128
    title_en = "Kiro"
    description = "AWS Kiro IDE: Claude и другие модели. Вход через браузер или файл токена."
    description_en = "AWS Kiro IDE: Claude and other models. Sign in with a browser or paste a token file."

    def __init__(self) -> None:
        self._flows: Dict[str, Dict[str, Any]] = {}
        self._servers: Dict[str, asyncio.AbstractServer] = {}

    def account_fields(self) -> List[Field]:
        return [
            Field("token_json", "Файл токена kiro-auth-token.json (JSON)", "textarea", required=False,
                  placeholder='{"accessToken": "…", "refreshToken": "…", "expiresAt": "…"}',
                  help="Либо вставьте JSON из ~/.aws/sso/cache/kiro-auth-token.json, либо войдите кнопкой",
                  label_en="Token file kiro-auth-token.json (JSON)",
                  help_en="Paste the JSON from ~/.aws/sso/cache/kiro-auth-token.json, or use the sign-in button"),
            Field("region", "Регион", "select", required=False, default="us-east-1",
                  options=["us-east-1", "eu-central-1", "us-west-2"],
                  label_en="Region"),
            Field("endpoint", "Свой эндпоинт (необязательно)", "url", required=False,
                  placeholder="https://codewhisperer.us-east-1.amazonaws.com",
                  help="Обычно не нужен: MemuAI сам перебираетхосты Kiro/CodeWhisperer/Amazon Q. "
                       "Заполните, если вы точно знаете рабочий адрес.",
                  label_en="Custom endpoint (optional)",
                  help_en="Rarely needed: MemuAI already rotates the Kiro/CodeWhisperer/Amazon Q hosts. "
                          "Fill it in only if you know a working address."),
        ]

    def seed_models(self) -> List[Tuple[str, str]]:
        return SEED

    def settings_fields(self) -> List[Field]:
        return [
            Field("agent_mode", "Режим агента", "select", required=False, default="vibe",
                  options=["vibe", "spec"],
                  help="Заголовок x-amzn-kiro-agent-mode. vibe — обычный чат, "
                       "spec — режим спецификаций Kiro IDE.",
                  label_en="Agent mode",
                  help_en="The x-amzn-kiro-agent-mode header. vibe is the normal chat, "
                          "spec is the Kiro IDE specification mode."),
            Field("send_history", "Передавать историю диалога", "select", required=False,
                  default="true", options=["true", "false"],
                  help="Выключите, если Kiro отвечает ошибкой на длинных диалогах.",
                  label_en="Send conversation history",
                  help_en="Turn off if Kiro fails on long conversations."),
            Field("max_output_chars", "Ограничение ответа, символов", "number",
                  required=False, default="0",
                  help="0 — без ограничения.",
                  label_en="Answer limit, characters", help_en="0 means no limit."),
        ]

    @staticmethod
    def _tokens(account) -> Dict[str, Any]:
        t = dict(account.extra.get("tokens") or {})
        raw = str(account.fields.get("token_json", "") or "").strip()
        if raw and not t:
            try:
                t = json.loads(raw)
            except Exception:
                pass
        return t

    @staticmethod
    def _expires_at(t: Dict[str, Any]) -> float:
        v = t.get("expiresAt")
        if isinstance(v, (int, float)):

            return float(v) / 1000.0 if v > 1e11 else float(v)
        try:
            from datetime import datetime
            return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0.0

    async def _ensure_token(self, account) -> str:
        t = self._tokens(account)
        if not t.get("accessToken") and not t.get("refreshToken"):
            raise ProviderAuthError("The account has no Kiro tokens — sign in again")
        expires = self._expires_at(t)
        if t.get("accessToken") and (not expires or expires > time.time() + 120):
            return str(t["accessToken"])

        locks = self.__dict__.setdefault("_refresh_locks", {})
        lock = locks.setdefault(account.id, asyncio.Lock())
        async with lock:
            fresh = self._tokens(account)
            fresh_expires = self._expires_at(fresh)
            if fresh.get("accessToken") and fresh_expires and fresh_expires > time.time() + 120:
                return str(fresh["accessToken"])
            return await self._refresh_token(account, fresh)

    async def _refresh_token(self, account, t: Dict[str, Any]) -> str:
        if t.get("authMethod") == "builder" or (t.get("clientId") and t.get("clientSecret")):
            body = {"grantType": "refresh_token", "clientId": t.get("clientId"),
                    "clientSecret": t.get("clientSecret"), "refreshToken": t.get("refreshToken")}

            region = t.get("oidcRegion") or account.fields.get("region", KIRO_REGION) or KIRO_REGION
            url = f"https://oidc.{region}.amazonaws.com/token"
        else:
            body = {"refreshToken": t.get("refreshToken")}
            url = REFRESH_URL
        try:
            session = await http.get_session(self.proxy_for(account))
            resp = await session.post(url, json=body,
                                      headers={"User-Agent": _ua(), "Content-Type": "application/json"},
                                      timeout=30)
        except Exception as e:
            raise ProviderNetworkError(f"Token refresh failed: {e}")
        if resp.status_code in (400, 401, 403):
            raise ProviderAuthError(f"The Kiro token expired and cannot be refreshed (HTTP {resp.status_code})")
        if resp.status_code == 429:
            raise ProviderRateLimitError("Kiro token refresh is rate limited (HTTP 429)")
        if resp.status_code != 200:

            raise ProviderServerError(f"Kiro token refresh failed (HTTP {resp.status_code})",
                                      502)
        try:
            data = resp.json()
        except Exception:
            data = None
        if not isinstance(data, dict) or not data.get("accessToken"):

            raise ProviderServerError("Kiro token refresh returned no access token", 502)
        account.extra["tokens"] = {
            "accessToken": data.get("accessToken"),
            "refreshToken": data.get("refreshToken") or t.get("refreshToken"),
            "expiresAt": time.time() + int(data.get("expiresIn", 3600)) - 60,

            "profileArn": data.get("profileArn") or t.get("profileArn") or "",
            "authMethod": t.get("authMethod", "social"),
            "clientId": t.get("clientId"), "clientSecret": t.get("clientSecret"),
            **({"oidcRegion": t["oidcRegion"]} if t.get("oidcRegion") else {}),
        }
        from ..state import state as app_state
        app_state.save_accounts()
        return str(account.extra["tokens"]["accessToken"])

    def _profile_arn(self, account) -> Optional[str]:
        """profileArn берём только из токена самого аккаунта.

        Зашитый DEFAULT_PROFILE_ARN принадлежит чужому аккаунту: Amazon
        отвечает на него «Invalid profileArn» (HTTP 400) в getUsageLimits и
        403 в чате. Его оставляем только как последнюю попытку в чате, где
        перебор запасных вариантов всё равно есть.
        """
        tokens = self._tokens(account)
        stored = str(tokens.get("profileArn") or "").strip()
        if stored and stored != DEFAULT_PROFILE_ARN:
            return stored
        method = str(tokens.get("authMethod") or "social").lower()
        if method in ("builder", "idc", "sso"):
            return None
        return stored or None

    def _hosts(self, account) -> List[str]:
        region = str(account.fields.get("region", KIRO_REGION) or KIRO_REGION)
        custom = str(account.fields.get("endpoint", "") or "").strip().rstrip("/")
        hosts = [host.format(region=region) for host in DATA_PLANE_HOSTS]
        if custom:
            hosts.insert(0, custom)
        return list(dict.fromkeys(hosts))

    def _control_hosts(self, account) -> List[str]:
        region = str(account.fields.get("region", KIRO_REGION) or KIRO_REGION)
        custom = str(account.fields.get("endpoint", "") or "").strip().rstrip("/")
        hosts = [host.format(region=region) for host in CONTROL_HOSTS]
        if custom:
            hosts.insert(0, custom)
        return list(dict.fromkeys(hosts))

    def _resolve_model_id(self, account, model: str) -> str:
        """Сопоставляет запрошенное имя с реальным id из каталога аккаунта.

        Только точное совпадение с точностью до «.», «_» и «-»: подстрочный
        поиск подменял claude-sonnet-4-5 на более короткий claude-sonnet-4.
        """
        requested = str(model or "").strip()
        known = [str(item) for item in (account.extra.get("available_models") or []) if item]

        def norm(value: str) -> str:
            return re.sub(r"[._]", "-", value.lower())

        wanted = norm(requested)
        for item in known:
            if norm(item) == wanted:
                return item
        return requested

    @staticmethod
    def _well_formed_history(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """History in the shape CodeWhisperer accepts: user/assistant pairs.

        Amazon answers «Improperly formed request» (HTTP 400) when two turns of
        the same role follow each other, when the history starts with the
        assistant or ends with the user (the current message is a user turn too),
        or when a turn has empty content. MemuAI legitimately produces such
        sequences: attached files go as a separate user message right before the
        question, an assistant greeting can open a chat, an empty assistant turn
        is left by a failed reply. Neighbouring turns of one role are merged
        (images kept), blanks get a placeholder and a leading assistant turn
        gets a minimal user stub. A trailing user turn is left for the caller to
        fold into the current message.
        """
        merged: List[Dict[str, Any]] = []
        for item in history:
            kind = "userInputMessage" if "userInputMessage" in item else "assistantResponseMessage"
            body = dict(item[kind])
            if merged and kind in merged[-1]:
                previous = merged[-1][kind]
                text = "\n\n".join(part for part in (previous.get("content") or "",
                                                     body.get("content") or "") if part)
                previous["content"] = text
                images = list(previous.get("images") or []) + list(body.get("images") or [])
                if images:
                    previous["images"] = images
                continue
            merged.append({kind: body})
        for item in merged:
            for body in item.values():
                if not str(body.get("content") or "").strip():
                    body["content"] = "(empty message)"
        if merged and "assistantResponseMessage" in merged[0]:
            merged.insert(0, {"userInputMessage": {"content": "(empty message)", "origin": "AI_EDITOR"}})
        return merged

    @staticmethod
    def _classify_body(status: int, body: str, model: str) -> ProviderError:
        """Превращает сырой ответ Amazon в понятную пользователю ошибку."""
        head = (body or "")[:600]
        low = head.lower()
        if any(marker in low for marker in _MODEL_ERROR_MARKERS):
            return ProviderModelUnavailableError(
                f"Model “{model}” is not available to this Kiro account "
                f"(the plan does not include it). Amazon replied: {head[:200]}", model)
        if status == 429:
            if any(marker in low for marker in _THROTTLE_MARKERS):
                return ProviderRateLimitError(
                    f"Kiro is throttling requests (HTTP 429). Amazon replied: {head[:200]}")
            if "quota" in low or "limit" in low:
                return ProviderQuotaError(
                    f"The Kiro account limit is used up (HTTP 429). Amazon replied: {head[:200]}")
            return ProviderRateLimitError(
                "Kiro returned HTTP 429 without an explanation. Usually the request went to "
                "перегруженный хост Amazon или исчерпан лимит аккаунта. "
                + (f"Ответ Amazon: {head[:200]}" if head else ""))
        return ProviderServerError(f"Kiro: HTTP {status}: {head[:300]}", status)

    def _q_headers(self, token: str) -> Dict[str, str]:
        mode = str(config_store.provider_cfg(self.id).get("agent_mode", "vibe") or "vibe")
        return {
            "Authorization": f"Bearer {token}",
            "User-Agent": _ua(),
            "x-amzn-kiro-agent-mode": mode if mode in ("vibe", "spec") else "vibe",
            "amz-sdk-invocation-id": str(uuid.uuid4()),
            "amz-sdk-request": "attempt=1; max=3",
            "Content-Type": "application/json",
            "Accept": "*/*",
        }

    async def chat_stream(self, account, model: str, messages: List[Dict[str, Any]],
                          params: Dict[str, Any]) -> AsyncIterator[Dict[str, Any]]:
        token = await self._ensure_token(account)
        region = str(account.fields.get("region", KIRO_REGION) or KIRO_REGION)
        system_text, history, final_user = normalize_messages(messages)
        first_user = transcript_prompt(system_text, [], final_user) if system_text else final_user

        def image_blocks(content: Any) -> List[Dict[str, Any]]:
            blocks: List[Dict[str, Any]] = []
            for part in content if isinstance(content, list) else []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") in ("input_file", "file", "file_url"):
                    raise ProviderServerError(
                        "Kiro accepts PNG, JPEG, GIF or WebP images only, not arbitrary files.", 400)
                if part.get("type") not in ("image_url", "input_image", "image"):
                    continue
                image = part.get("image_url") or part.get("image") or part.get("url") or ""
                url = image.get("url", "") if isinstance(image, dict) else str(image)
                match = re.match(r"^data:image/(png|jpe?g|gif|webp);base64,(.+)$", url, re.I | re.S)
                if not match:
                    raise ProviderServerError(
                        "Kiro accepts attachments only as PNG, JPEG, GIF or WebP data URLs. "
                        "Удалите удалённую ссылку или передайте изображение в base64.", 400)
                fmt = "jpeg" if match.group(1).lower() in ("jpg", "jpeg") else match.group(1).lower()

                if len(match.group(2)) > 5_242_880:
                    raise ProviderServerError("The image exceeds the Kiro limit of 3.75 MB.", 400)
                try:
                    base64.b64decode(match.group(2), validate=True)
                except Exception:
                    raise ProviderServerError("The Kiro attachment contains malformed base64 data.", 400)
                blocks.append({"format": fmt, "source": {"bytes": match.group(2)}})
            if len(blocks) > 20:
                raise ProviderServerError("Kiro accepts at most 20 images per message.", 400)
            return blocks

        user_contents = [m.get("content") for m in messages
                         if m.get("role") == "user"]
        user_image_sets = [image_blocks(content) for content in user_contents]
        final_images = user_image_sets[-1] if user_image_sets else []
        historical_images = iter(user_image_sets[:-1])

        conv_history: List[Dict[str, Any]] = []
        if str(config_store.provider_cfg(self.id).get("send_history", "true")).lower() == "false":
            history = []
        for m in history:
            if m["role"] == "user":
                images = next(historical_images, [])
                conv_history.append({"userInputMessage": {
                    "content": m["content"], "origin": "AI_EDITOR",
                    **({"images": images} if images else {}),
                }})
            else:
                conv_history.append({"assistantResponseMessage": {"content": m["content"]}})
        conv_history = self._well_formed_history(conv_history)
        if conv_history and "userInputMessage" in conv_history[-1]:

            carried = conv_history.pop()["userInputMessage"]
            joined = "\n\n".join(part for part in (str(carried.get("content") or ""), final_user)
                                  if part and part != "(empty message)") or final_user
            first_user = transcript_prompt(system_text, [], joined) if system_text else joined
            final_images = list(carried.get("images") or []) + list(final_images)

        model_id = self._resolve_model_id(account, model)
        body: Dict[str, Any] = {
            "conversationState": {
                "chatTriggerType": "MANUAL",
                "conversationId": str(uuid.uuid4()),
                "currentMessage": {
                    "userInputMessage": {
                        "content": first_user,
                        "modelId": model_id,
                        "origin": "AI_EDITOR",
                        "userInputMessageContext": {},
                        **({"images": final_images} if final_images else {}),
                    }
                },
                "history": conv_history,
            },
        }
        profile_arn = self._profile_arn(account)
        if profile_arn:
            body["profileArn"] = profile_arn

        resp = None
        last_error: Optional[ProviderError] = None
        tried_default_arn = False
        dropped_arn = False
        hosts = self._hosts(account)

        queue = list(hosts)
        while queue:
            host = queue.pop(0)
            index = len(hosts) - len(queue) - 1
            try:
                session = await http.get_session(self.proxy_for(account))
                resp = await session.post(f"{host}/generateAssistantResponse",
                                          headers=self._q_headers(token), json=body, stream=True,
                                          timeout=config_store.settings.request_timeout)
            except Exception as e:
                last_error = ProviderNetworkError(
                    f"Network error {host.split('//')[-1]}: {type(e).__name__}: {e}")
                resp = None
                continue

            status = resp.status_code
            if status == 200:
                break

            head = await _read_head(resp)
            await _safe_close(resp)
            resp = None
            if status in (401, 403):
                if any(marker in head.lower() for marker in _MODEL_ERROR_MARKERS):

                    raise self._classify_body(status, head, model_id)
                if "not authorized" in head.lower() and profile_arn:

                    dropped_arn = True
                    body.pop("profileArn", None)
                    profile_arn = None
                    last_error = ProviderServerError(
                        "Kiro rejected profileArn — retrying without it", status)
                    queue.insert(0, host)
                    continue
                if "profilearn" in head.lower() and not profile_arn and not tried_default_arn:

                    tried_default_arn = True
                    profile_arn = DEFAULT_PROFILE_ARN
                    body["profileArn"] = profile_arn
                    last_error = ProviderServerError(
                        "Kiro requires a profileArn — retrying with the IDE default", status)
                    queue.insert(0, host)
                    continue
                raise ProviderAuthError(
                    f"Kiro rejected the token (HTTP {status}). {head[:200]}".strip())
            if status == 400 and "profilearn" in head.lower() and profile_arn:

                profile_arn = None
                body.pop("profileArn", None)
                last_error = ProviderServerError(
                    "Kiro rejected profileArn — retrying without it", status)
                queue.insert(0, host)
                continue
            if status == 402:
                raise ProviderQuotaError(f"The Kiro limit is used up (HTTP 402). {head[:200]}".strip())
            error = self._classify_body(status, head, model_id)
            if isinstance(error, ProviderModelUnavailableError):
                raise error
            last_error = error
            if index < len(hosts) - 1:

                log.info("Kiro %s -> HTTP %s, trying the next host", host, status)
                continue

        if resp is None:
            raise last_error or ProviderServerError("No Kiro host responded")
        if dropped_arn:

            tokens = dict(account.extra.get("tokens") or {})
            if tokens.pop("profileArn", None) is not None:
                account.extra["tokens"] = tokens
                from ..state import state as app_state
                app_state.save_accounts()

        buffer = bytearray()
        produced = False
        saw_frame = False
        tail = ""
        try:
            char_limit = max(0, int(float(config_store.provider_cfg(self.id)
                                          .get("max_output_chars") or 0)))
        except (TypeError, ValueError):
            char_limit = 0
        sent_chars = 0
        try:
            async for chunk in self._aiter_bytes(resp):
                buffer += chunk
                for headers, payload in _take_event_frames(buffer):
                    saw_frame = True
                    for event in self._frame_events(headers, payload, model_id):
                        if event["type"] in ("text", "reasoning") and event.get("text"):
                            produced = True
                            if char_limit and event["type"] == "text":
                                room = char_limit - sent_chars
                                if len(event["text"]) >= room:

                                    if room > 0:
                                        yield {"type": "text", "text": event["text"][:room]}
                                    yield {"type": "finish", "reason": "length"}
                                    return
                                sent_chars += len(event["text"])
                            yield event
                        elif event["type"] == "stop":
                            return
                if saw_frame and len(buffer) > _FRAME_MAX:
                    raise ProviderServerError("Kiro sent a malformed eventstream frame")
            if saw_frame and len(buffer) >= _FRAME_PRELUDE:

                raise ProviderServerError("Kiro stream ended with an incomplete or malformed frame")
            if not saw_frame:

                text = buffer.decode("utf-8", "replace")
                tail = text[-600:]
                acc = ""
                for obj in self._iter_json(text):
                    kind, piece = self._extract(obj, acc)
                    if kind == "text" and piece:
                        acc += piece
                        produced = True
                        yield {"type": "text", "text": piece}
                    elif kind == "reasoning" and piece:
                        produced = True
                        yield {"type": "reasoning", "text": piece}
                error = self._stream_error(text)
                if error is not None and not produced:
                    raise error
            if not produced:

                raise ProviderServerError(
                    "Kiro finished the stream without any text. "
                    f"Amazon replied: {tail or '(empty body)'}")
        except ProviderError:
            raise
        except asyncio.TimeoutError:
            raise ProviderNetworkError(
                f"Kiro sent no data for over {config_store.settings.stream_idle_timeout} s")
        except Exception as e:
            raise ProviderServerError(f"The Kiro stream broke off: {type(e).__name__}: {e}")
        finally:
            await http.close_response(resp)

    @staticmethod
    async def _aiter_bytes(resp) -> AsyncIterator[bytes]:
        """Сырые байты ответа с тем же лимитом тишины, что и у остальных."""
        idle = float(config_store.settings.stream_idle_timeout or 0)
        chunks = resp.aiter_content()
        try:
            while True:
                try:
                    if idle > 0:
                        chunk = await asyncio.wait_for(chunks.__anext__(), timeout=idle)
                    else:
                        chunk = await chunks.__anext__()
                except StopAsyncIteration:
                    return
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8", "replace")
                if chunk:
                    yield bytes(chunk)
        finally:
            close = getattr(chunks, "aclose", None)
            if close is not None:
                try:
                    await close()
                except Exception:
                    pass

    def _frame_events(self, headers: Dict[str, str], payload: bytes,
                      model: str) -> List[Dict[str, Any]]:
        """Кадр event-stream → нормализованные события MemuAI."""
        message_type = headers.get(":message-type", "event")
        event_type = headers.get(":event-type", "")
        obj = _payload_json(payload)
        if message_type in ("exception", "error") or headers.get(":exception-type"):
            name = headers.get(":exception-type") or headers.get(":error-code") or event_type
            message = str(obj.get("message") or obj.get("Message") or payload[:300])
            raise self._classify_body(429 if "throttl" in name.lower() else 502,
                                      f"{name}: {message}", model)
        if event_type in ("messageMetadataEvent", "followupPromptEvent", "citationEvent",
                          "supplementaryWebLinksEvent", "traceEvent"):
            return []
        if event_type == "invalidStateEvent":
            raise ProviderServerError(
                f"Kiro reported an invalid state: {obj.get('message') or obj.get('reason') or ''}")
        events: List[Dict[str, Any]] = []
        for key in ("thinking", "reasoningContent", "reasoning"):
            value = obj.get(key)
            if isinstance(value, str) and value:
                events.append({"type": "reasoning", "text": value})
        content = obj.get("content")
        if isinstance(content, str) and content:
            events.append({"type": "text", "text": content})
        elif isinstance(obj.get("text"), str) and obj["text"]:
            events.append({"type": "text", "text": obj["text"]})
        if obj.get("stop") is True or event_type == "messageStopEvent":
            events.append({"type": "stop", "text": ""})
        return events

    def _stream_error(self, text: str) -> Optional[ProviderError]:
        """Ошибка Amazon, пришедшая телом ответа с кодом 200."""
        low = (text or "").lower()
        if not low.strip():
            return None
        for marker in ("throttlingexception", "serviceunavailable", "internalserver",
                       "validationexception", "accessdenied", "quotaexceeded",
                       "improperly formed request"):
            if marker in low:
                return self._classify_body(429 if "throttl" in marker else 502, text, "")
        return None

    @staticmethod
    def _iter_json(line: str):
        decoder = json.JSONDecoder()
        idx = 0
        n = len(line)
        while idx < n:
            ch = line.find("{", idx)
            if ch < 0:
                return
            try:
                obj, end = decoder.raw_decode(line, ch)
            except Exception:
                return
            if isinstance(obj, dict):
                yield obj
            idx = max(end, ch + 1)

    @staticmethod
    def _extract(obj: Dict[str, Any], acc: str) -> Tuple[str, str]:
        if obj.get("stop") is True:
            return "stop", ""
        content = obj.get("content")
        if isinstance(content, str) and content:
            if acc and content.startswith(acc):
                return "text", content[len(acc):]
            if acc and acc.endswith(content):
                return "skip", ""
            return "text", content
        for key in ("thinking", "reasoningContent", "reasoning"):
            v = obj.get(key)
            if isinstance(v, str) and v:
                return "reasoning", v
        if isinstance(obj.get("text"), str) and obj["text"] and not acc:
            return "text", obj["text"]
        return "skip", ""

    def _search_hosts(self, account) -> List[str]:
        region = str(account.fields.get("region", KIRO_REGION) or KIRO_REGION)
        custom = str(account.fields.get("endpoint", "") or "").strip().rstrip("/")
        hosts = [f"https://q.{region}.amazonaws.com", f"https://runtime.{region}.kiro.dev"]
        if custom:
            hosts.insert(0, custom)
        return list(dict.fromkeys(hosts))

    async def web_search(self, account, query: str, limit: int = 5) -> List[Dict[str, str]]:
        """Встроенный поиск Kiro: тот же, что у Kiro IDE и kiro-cli.

        Это удалённый MCP-инструмент ``web_search`` на data-plane хосте
        (POST /mcp, JSON-RPC ``tools/call``). Поле ``result.content[0].text``
        — JSON-строка ``{"results": [{title, url, snippet, publishedDate}]}``.
        Новым аккаунтам Amazon требует profileArn, старые принимают без него.
        """
        token = await self._ensure_token(account)
        profile_arn = self._profile_arn(account)
        last = "нет ответа"
        for host in self._search_hosts(account):
            for with_arn in ((True, False) if profile_arn else (False,)):
                request_id = (f"web_search_tooluse_{secrets.token_hex(11)}_"
                              f"{int(time.time() * 1000)}_{secrets.token_hex(4)}")
                body: Dict[str, Any] = {
                    "id": request_id, "jsonrpc": "2.0", "method": "tools/call",
                    "params": {"name": "web_search", "arguments": {"query": query}},
                }
                if with_arn:
                    body["profileArn"] = profile_arn
                headers = self._q_headers(token)
                headers["x-amzn-codewhisperer-optout"] = "false"
                try:
                    session = await http.get_session(self.proxy_for(account))
                    resp = await session.post(f"{host}/mcp", json=body, headers=headers, timeout=40)
                except Exception as exc:
                    last = f"{type(exc).__name__}: {exc}"
                    break
                if resp.status_code in (401, 403):
                    raise ProviderAuthError(f"Kiro web search: HTTP {resp.status_code}")
                if resp.status_code != 200:
                    last = f"HTTP {resp.status_code}"
                    continue
                try:
                    data = resp.json() or {}
                    if data.get("error"):
                        last = str(data.get("error"))[:200]
                        continue
                    result = data.get("result") or {}
                    if result.get("isError"):
                        last = "isError"
                        continue
                    text = ((result.get("content") or [{}])[0] or {}).get("text") or "{}"
                    payload = json.loads(text) if isinstance(text, str) else text
                except Exception as exc:
                    last = f"bad response: {exc}"
                    continue
                rows = payload.get("results") if isinstance(payload, dict) else None
                results: List[Dict[str, str]] = []
                for item in rows or []:
                    if not isinstance(item, dict) or not item.get("url"):
                        continue
                    row = {"title": str(item.get("title") or "")[:200],
                           "url": str(item.get("url"))[:500],
                           "snippet": str(item.get("snippet") or "")[:500]}
                    published = item.get("publishedDate")
                    if isinstance(published, (int, float)) and published > 0:
                        row["published"] = time.strftime("%Y-%m-%d", time.gmtime(published / 1000))
                    results.append(row)
                    if len(results) >= limit:
                        break
                return results
        raise ProviderNetworkError(f"Kiro web search failed: {last}")

    async def _list_models(self, account, token: str) -> Tuple[Optional[List[str]], str]:
        """ListAvailableModels по всем управляющим хостам. → (модели, сообщение)."""
        params: Dict[str, Any] = {"origin": "AI_EDITOR"}
        profile_arn = self._profile_arn(account)
        if profile_arn:
            params["profileArn"] = profile_arn
        last = ""
        for host in self._control_hosts(account):
            try:
                session = await http.get_session(self.proxy_for(account))
                resp = await session.get(f"{host}/ListAvailableModels", params=params,
                                         headers=self._q_headers(token), timeout=30)
            except Exception as e:
                last = f"сеть недоступна: {http.network_error_text(e)}"
                continue
            if resp.status_code in (401, 403):
                return None, "токен отклонён"
            if resp.status_code != 200:
                last = f"HTTP {resp.status_code}"
                continue
            try:
                rows = resp.json().get("models") or []
                models = [str(m.get("modelId")) for m in rows if m.get("modelId")]
            except Exception:
                rows, models = [], []
            try:

                from .. import model_capabilities
                model_capabilities.record_raw(self.id, rows, id_field="modelId", source="api:kiro")
            except Exception:
                pass
            if models:
                account.extra["available_models"] = models
                account.extra["models_synced"] = time.time()
                from ..state import state as app_state
                app_state.save_accounts()
            return models, ""
        return None, last or "нет ответа"

    async def check(self, account) -> Tuple[Optional[bool], str]:
        try:
            token = await self._ensure_token(account)
        except ProviderAuthError as e:
            return False, str(e)
        except ProviderError as e:
            return None, str(e)
        models, error = await self._list_models(account, token)
        if models is None:
            if error == "токен отклонён":
                return False, error
            return None, error
        tier = str(account.extra.get("subscription") or "")
        suffix = f" · plan: {tier}" if tier else ""
        return True, f"токен работает · моделей: {len(models)}{suffix}"

    async def dynamic_models(self, account) -> Optional[List[str]]:
        try:
            token = await self._ensure_token(account)
        except Exception:
            return None
        models, _ = await self._list_models(account, token)
        return models

    async def usage(self, account) -> Optional[Dict[str, Any]]:
        try:
            token = await self._ensure_token(account)
        except Exception:
            return None

        variants = [
            {"isEmailRequired": "true", "origin": "AI_EDITOR",
             "resourceType": "AGENTIC_REQUEST"},
            {"isEmailRequired": "true", "origin": "AI_EDITOR"},
            {"origin": "AI_EDITOR"},
            {},
        ]

        own_arn = self._profile_arn(account)
        if own_arn:
            variants = variants + [dict(item, profileArn=own_arn) for item in variants]
        last = ""
        for host in self._control_hosts(account):
            for params in variants:
                try:
                    session = await http.get_session(self.proxy_for(account))
                    resp = await session.get(f"{host}/getUsageLimits", params=params,
                                             headers=self._q_headers(token), timeout=30)
                except Exception as exc:
                    last = f"{type(exc).__name__}: {exc}"
                    break
                if resp.status_code != 200:
                    try:
                        last = f"HTTP {resp.status_code}: {str(resp.text)[:160]}"
                    except Exception:
                        last = f"HTTP {resp.status_code}"
                    continue
                try:
                    data = resp.json() or {}
                except Exception as exc:
                    last = f"malformed JSON: {exc}"
                    continue
                parsed = self._parse_usage(account, data)
                if parsed is not None:
                    return parsed
                last = "the response carries no usage breakdown"

        raise ProviderServerError(f"Kiro getUsageLimits: {last or 'no response'}")

    @staticmethod
    def _parse_usage(account, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        subscription = (data.get("subscriptionInfo") or {}).get("subscriptionTitle", "")
        if subscription:
            account.extra["subscription"] = subscription
        rows = []
        breakdown = (data.get("usageBreakdownList") or data.get("usageBreakdowns")
                     or data.get("usageLimitList") or [])
        for item in breakdown:
            if not isinstance(item, dict):
                continue
            used = item.get("currentUsageWithPrecision", item.get("currentUsage", 0))
            limit = item.get("usageLimitWithPrecision", item.get("usageLimit", 0))
            rows.append({"display": item.get("displayName") or item.get("resourceType") or "—",
                         "used": used, "limit": limit, "unit": item.get("unit", "")})
        if not rows and (data.get("usageLimit") or data.get("currentUsage")):
            rows.append({"display": str(data.get("resourceType") or "requests"),
                         "used": data.get("currentUsage", 0),
                         "limit": data.get("usageLimit", 0), "unit": ""})
        if not rows and not subscription:
            return None
        return {"subscription": subscription, "rows": rows,
                "next_reset": data.get("nextDateReset"),
                "email": (data.get("userInfo") or {}).get("email", "")}

    async def oauth_start(self) -> Dict[str, Any]:

        for fid in list(self._flows.keys()):
            self._flows[fid]["status"] = "cancelled"
        for srv in list(self._servers.values()):
            try:
                srv.close()
            except Exception:
                pass
        self._servers.clear()

        flow_id = uuid.uuid4().hex[:12]
        state = _b64url(secrets.token_bytes(16))
        verifier = _b64url(secrets.token_bytes(32))
        challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
        flow = {"status": "waiting", "state": state, "verifier": verifier,
                "challenge": challenge, "account": None, "error": ""}
        self._flows[flow_id] = flow
        url = ("https://app.kiro.dev/signin?state=" + state +
               "&code_challenge=" + challenge +
               "&code_challenge_method=S256&redirect_uri=" + REDIRECT_URI +
               "&redirect_from=KiroIDE")
        spawn_background(self._run_listener(flow_id, flow))
        await asyncio.sleep(0.4)
        if flow["status"] == "error":
            return {"ok": False, "message": flow["error"]}
        webbrowser.open(url)
        return {"ok": True, "flow_id": flow_id, "url": url, "port": 3128}

    async def oauth_status(self, flow_id: str) -> Dict[str, Any]:
        flow = self._flows.get(flow_id)
        if not flow:
            return {"status": "error", "message": "flow not found"}
        if flow["status"] == "done" and flow.get("account"):
            acc = flow.pop("account")
            self._flows.pop(flow_id, None)
            return {"status": "success", "account": acc}
        return {"status": flow["status"], "message": flow.get("error", "")}

    async def _exchange_code(self, flow: Dict[str, Any]) -> None:
        """Обмен кода: redirect_uri пробуем в вариантах — Cognito привередлив."""
        session = await http.get_session("")
        variants = []
        opt = flow.get("login_option", "")
        if opt:
            variants.append("http://localhost:3128" + CALLBACK_PATH + f"?login_option={opt}")
        variants += ["http://localhost:3128" + CALLBACK_PATH, REDIRECT_URI]
        last = ""
        for redirect in variants:
            resp = await session.post(TOKEN_URL, json={
                "code": flow["code"], "code_verifier": flow["verifier"],
                "redirect_uri": redirect,
            }, headers={"User-Agent": _ua(), "Content-Type": "application/json"}, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                tokens = {"accessToken": data.get("accessToken"),
                          "refreshToken": data.get("refreshToken"),
                          "expiresAt": time.time() + int(data.get("expiresIn", 3600)) - 60,
                          "profileArn": data.get("profileArn") or DEFAULT_PROFILE_ARN,
                          "authMethod": "social"}
                from ..state import state as app_state
                acc = app_state.add_account("kiro", fields={"region": KIRO_REGION},
                                            label="Kiro (браузер)", extra={"tokens": tokens})
                flow["status"] = "done"
                flow["account"] = acc.public_dict()
                return
            last = f"HTTP {resp.status_code}: {resp.text[:120]}"
        flow["status"], flow["error"] = "error", f"Обмен кода не удался ({last})"

    async def _run_listener(self, flow_id: str, flow: Dict[str, Any]) -> None:
        done = asyncio.Event()

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                raw = await asyncio.wait_for(reader.read(8192), timeout=120)
                line = raw.decode("utf-8", errors="replace").split("\r\n")[0]
                target = line.split(" ")[1] if " " in line else ""
                from urllib.parse import urlparse, parse_qs
                q = parse_qs(urlparse(target).query)
                code = (q.get("code") or [""])[0]
                if code:
                    qstate = (q.get("state") or [""])[0]
                    if qstate and qstate != flow["state"]:
                        flow["status"], flow["error"] = "error", "state mismatch"
                    else:
                        flow["code"] = code
                        flow["login_option"] = (q.get("login_option") or [""])[0]
                        done.set()
                ok_page = b"success" if flow.get("code") else b"error"
                writer.write(b"HTTP/1.1 302 Found\r\nLocation: https://app.kiro.dev/signin?auth_status="
                             + ok_page + b"&redirect_from=KiroIDE\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                await writer.drain()
            except Exception:
                pass
            finally:
                try:
                    writer.close()
                except Exception:
                    pass

        server = None
        try:
            server = await asyncio.start_server(handle, "127.0.0.1", 3128)
            self._servers[flow_id] = server
            try:
                await asyncio.wait_for(done.wait(), timeout=600)
            except asyncio.TimeoutError:
                flow["status"], flow["error"] = "error", "Ожидание входа истекло (10 минут)"
                return
            if flow["status"] != "error" and flow.get("code"):
                await self._exchange_code(flow)
        except OSError as e:
            flow["status"] = "error"
            flow["error"] = f"Порт 3128 занят: {e}"
        except Exception as e:
            flow["status"], flow["error"] = "error", f"Ошибка входа: {type(e).__name__}: {e}"
        finally:
            if server:
                try:
                    server.close()
                    await server.wait_closed()
                except Exception:
                    pass

    async def oauth_device_start(self) -> Dict[str, Any]:
        flow_id = uuid.uuid4().hex[:12]
        flow: Dict[str, Any] = {"status": "waiting", "account": None, "error": ""}
        self._flows[flow_id] = flow
        spawn_background(self._run_device_flow(flow_id, flow))

        for _ in range(100):
            if flow["status"] == "error" or flow.get("user_code"):
                break
            await asyncio.sleep(0.2)
        if flow["status"] == "error":
            return {"ok": False, "message": flow["error"]}
        if not flow.get("user_code"):
            return {"ok": False, "message": "AWS не выдал код входа вовремя — повторите через минуту"}
        return {"ok": True, "flow_id": flow_id, "user_code": flow.get("user_code", ""),
                "verification_uri": flow.get("verification_uri", "")}

    async def _run_device_flow(self, flow_id: str, flow: Dict[str, Any]) -> None:
        try:
            session = await http.get_session("")
            reg = await session.post("https://oidc.us-east-1.amazonaws.com/client/register",
                                     json={"clientName": "Kiro IDE", "clientType": "public",
                                           "scopes": ["codewhisperer:completions",
                                                      "codewhisperer:analysis",
                                                      "codewhisperer:conversations"]},
                                     headers={"Content-Type": "application/json"}, timeout=30)
            regj = reg.json() if reg.status_code == 200 else {}
            client_id, client_secret = regj.get("clientId"), regj.get("clientSecret")
            if not client_id:
                flow["status"], flow["error"] = "error", f"Регистрация клиента: HTTP {reg.status_code}"
                return
            da = await session.post("https://oidc.us-east-1.amazonaws.com/device_authorization",
                                    json={"clientId": client_id, "clientSecret": client_secret,
                                          "startUrl": "https://view.awsapps.com/start"},
                                    headers={"Content-Type": "application/json"}, timeout=30)
            daj = da.json() if da.status_code == 200 else {}
            if not daj.get("deviceCode"):
                flow["status"], flow["error"] = "error", f"device_authorization: HTTP {da.status_code}"
                return
            flow["user_code"] = daj.get("userCode", "")
            flow["verification_uri"] = daj.get("verificationUri", "")

            webbrowser.open(daj.get("verificationUriComplete") or flow["verification_uri"])
            interval = int(daj.get("interval", 5))
            deadline = time.time() + int(daj.get("expiresIn", 600))
            while time.time() < deadline and flow["status"] == "waiting":
                await asyncio.sleep(interval)
                tok = await session.post("https://oidc.us-east-1.amazonaws.com/token",
                                         json={"clientId": client_id, "clientSecret": client_secret,
                                               "deviceCode": daj.get("deviceCode"),
                                               "grantType": "urn:ietf:params:oauth:grant-type:device_code"},
                                         headers={"Content-Type": "application/json"}, timeout=30)

                try:
                    data = tok.json() if tok.status_code in (200, 400) else {}
                except Exception:
                    data = {}
                if not isinstance(data, dict):
                    data = {}
                if data.get("accessToken"):
                    tokens = {"accessToken": data["accessToken"],
                              "refreshToken": data.get("refreshToken", ""),
                              "expiresAt": time.time() + int(data.get("expiresIn", 3600)) - 60,
                              "profileArn": DEFAULT_PROFILE_ARN, "authMethod": "builder",
                              "clientId": client_id, "clientSecret": client_secret,
                              "oidcRegion": "us-east-1"}
                    from ..state import state as app_state
                    acc = app_state.add_account("kiro", fields={"region": KIRO_REGION},
                                                label="Kiro (Builder ID)", extra={"tokens": tokens})
                    flow["status"] = "done"
                    flow["account"] = acc.public_dict()
                    return
                if data.get("error") == "authorization_pending":
                    continue
                if data.get("error") == "slow_down":
                    interval += 5
                    continue
                if data.get("error"):
                    flow["status"], flow["error"] = "error", str(data.get("error"))
                    return
            if flow["status"] == "waiting":
                flow["status"], flow["error"] = "error", "The sign-in timed out"
        except Exception as e:
            flow["status"], flow["error"] = "error", f"{type(e).__name__}: {e}"
