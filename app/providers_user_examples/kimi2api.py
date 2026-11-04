"""Kimi2API — ЭКСПЕРИМЕНТАЛЬНЫЙ плагин-провайдер MemuAI Proxy.

Обёртка над САЙТОМ Kimi (www.kimi.com) в виде API — как Antigravity2api
и ZCode2api оборачивают веб-интерфейсы. Это НЕ официальный API Moonshot
(он OpenAI-совместимый и уже покрыт встроенным провайдером «Собственный API»).

Что проверено (по открытым источникам, 2026-07, OmniRoute PR #5858,
проверен вживую на Plus-аккаунте автором PR):
  * endpoint: POST https://www.kimi.com/apiv2/kimi.gateway.chat.v1.ChatService/Chat
  * протокол: Connect-RPC (application/connect+json), 5-байтный конверт
    (1 байт флагов + 4 байта big-endian длины) поверх JSON;
  * авторизация: JWT из куки kimi-auth — Authorization: Bearer <jwt>
    + Cookie: kimi-auth=<jwt>; заголовки x-msh-* НЕ нужны;
  * ответ: поток Connect-фреймов с событиями op/mask:
    set/block.text — первый кусок ответа, append/block.text.content — дельта,
    set/block.think и append/block.think.content — рассуждения;
    конец — сообщение assistant со статусом COMPLETED;
  * проверка сессии: GET https://www.kimi.com/api/user (объект юзера сверху);
  * старый домен kimi.moonshot.cn/api/chat УМЕР (307 на www.kimi.com).

Что НЕ проверено (без живого аккаунта Kimi это не узнать):
  * точные имена полей JSON-тела запроса ChatService/Chat.
    Функция build_chat_payload() ниже — реконструкция: сообщения, модель
    и options.thinking в одном объекте. Если шлюз отвечает invalid_argument —
    первым делом сверьте тело запроса с реальным: откройте www.kimi.com,
    DevTools → Network → запрос Chat → скопируйте тело и поправьте
    build_chat_payload() под него. Всё остальное (авторизация, фрейминг,
    разбор событий) от тела запроса не зависит.

Установка: скопируйте этот файл в папку плагинов (%APPDATA%\\MemuAI\\providers)
и нажмите «Обновить плагины» в админке Proxy (или перезапустите Proxy).
Затем добавьте аккаунт: вставьте kimi-auth — JWT из куки www.kimi.com
(DevTools → Application → Cookies → www.kimi.com → kimi-auth; принимается
голый JWT, полная строка Cookie или «Authorization: Bearer ...»).
"""
from __future__ import annotations

PLUGIN_API = 1

import base64
import json
import re
import struct
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from app.providers import http as hub_http
from app.providers.base import (
    Field,
    Provider,
    ProviderAuthError,
    ProviderNetworkError,
    ProviderServerError,
    normalize_messages,
)
from app.config import config_store

CHAT_URL = "https://www.kimi.com/apiv2/kimi.gateway.chat.v1.ChatService/Chat"
USER_URL = "https://www.kimi.com/api/user"
MAX_FRAME_LEN = 8 * 1024 * 1024  # как у OmniRoute: потолок DoS-защиты

_JWT_RE = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")


def extract_kimi_jwt(raw: Any) -> Optional[str]:
    """JWT kimi-auth из голого токена, строки Cookie или Bearer-формы.

    Принимает: голый JWT; ``kimi-auth=<jwt>`` внутри полной строки Cookie;
    ``Cookie: ...``; ``Authorization: Bearer <jwt>``. Возвращает только JWT —
    остальные куки (аналитика и т.п.) upstream не отправляются.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    match = re.search(r"kimi-auth\s*=\s*([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)", text)
    if match:
        return match.group(1)
    match = _JWT_RE.search(text)
    if match:
        return match.group(0)
    return None


def pack_connect_frame(payload: bytes) -> bytes:
    """Connect-конверт: 1 байт флагов (0x00) + 4 байта big-endian длины + JSON."""
    return b"\x00" + struct.pack(">I", len(payload)) + payload


class ConnectFrameParser:
    """Инкрементный парсер Connect-фреймов (чанки рвутся где угодно)."""

    def __init__(self, max_len: int = MAX_FRAME_LEN) -> None:
        self._buf = bytearray()
        self._max_len = max_len

    def feed(self, data: bytes) -> List[bytes]:
        """Добавить чанк, вернуть готовые JSON-пейлоады (без конверта)."""
        self._buf += data
        out: List[bytes] = []
        while True:
            if len(self._buf) < 5:
                return out
            length = struct.unpack(">I", bytes(self._buf[1:5]))[0]
            if length > self._max_len:
                raise ProviderServerError(
                    f"Kimi: Connect frame too large ({length} bytes)")
            if len(self._buf) < 5 + length:
                return out
            out.append(bytes(self._buf[5:5 + length]))
            del self._buf[:5 + length]


def translate_event(obj: Any) -> List[Tuple[str, str]]:
    """Connect-событие -> [(type, text)]: text / reasoning. Пусто — heartbeat/мусор."""
    if not isinstance(obj, dict):
        return []
    if isinstance(obj.get("error"), dict):
        message = str(obj["error"].get("message") or obj["error"].get("code") or "Kimi error")
        raise ProviderServerError(f"Kimi: {message}")
    # Некоторые события вкладывают данные в "value".
    data = obj.get("value") if isinstance(obj.get("value"), dict) else obj
    found: List[Tuple[str, str]] = []

    def _grab(container: Any, *path: Any) -> str:
        cur = container
        for key in path:
            if isinstance(cur, dict) and isinstance(key, str):
                cur = cur.get(key)
            elif isinstance(cur, list) and isinstance(key, int):
                cur = cur[key] if 0 <= key < len(cur) else None
            else:
                return ""
        return cur if isinstance(cur, str) else ""

    text = (_grab(data, "block", "text", "content")
            or _grab(data, "result", "choices", 0, "delta", "content")
            or _grab(data, "result", "message", "blocks", 0, "text", "content"))
    if text:
        found.append(("text", text))
    think = (_grab(data, "block", "think", "content")
             or _grab(data, "result", "choices", 0, "delta", "reasoning_content"))
    if think:
        found.append(("reasoning", think))
    return found


def is_end_of_stream(obj: Any) -> bool:
    """Конец генерации: сообщение assistant со статусом COMPLETED."""
    if not isinstance(obj, dict):
        return False
    message = obj.get("message")
    if isinstance(message, dict):
        return (str(message.get("role") or "") == "assistant"
                and str(message.get("status") or "").upper() == "COMPLETED")
    return (str(obj.get("role") or "") == "assistant"
            and str(obj.get("status") or "").upper() == "COMPLETED")


def build_chat_payload(model: str, messages: List[Dict[str, Any]],
                       params: Dict[str, Any]) -> Dict[str, Any]:
    """Тело запроса ChatService/Chat.

    ВНИМАНИЕ — НЕ ПОДТВЕРЖДЕНО живым аккаунтом: проверены endpoint,
    авторизация, фрейминг и события ответа, а имена полей тела — реконструкция
    по описанию OmniRoute (foldMessages: история сворачивается в запрос).
    Если шлюз вернёт invalid_argument — сверьте тело с реальным запросом
    из DevTools www.kimi.com и поправьте ТОЛЬКО эту функцию.
    """
    system, history, final_user = normalize_messages(messages)
    folded: List[Dict[str, str]] = []
    if system:
        folded.append({"role": "system", "content": system})
    folded.extend(history)
    folded.append({"role": "user", "content": final_user})
    thinking = "thinking" in (model or "").lower()
    payload: Dict[str, Any] = {
        "model": model,
        "messages": folded,
        "options": {"thinking": thinking},
        "stream": True,
    }
    for key in ("temperature", "top_p", "max_tokens"):
        if params.get(key) is not None:
            payload[key] = params[key]
    return payload


class Kimi2ApiProvider(Provider):
    id = "kimi2api"
    title = "Kimi2API (экспериментальный)"
    icon = "🌙"
    title_en = "Kimi2API (experimental)"
    description = ("Сайт Kimi (www.kimi.com) как API: нужен kimi-auth из куки "
                   "браузера. ЭКСПЕРИМЕНТАЛЬНЫЙ плагин — формат тела запроса "
                   "не подтверждён живым аккаунтом.")
    description_en = ("Kimi website (www.kimi.com) as an API: needs the kimi-auth "
                      "cookie from the browser. EXPERIMENTAL plugin — the request "
                      "body format is not confirmed against a live account.")

    def account_fields(self) -> List[Field]:
        return [Field(
            "kimi_auth", "Kimi-auth (JWT или Cookie)", "password",
            placeholder="eyJhbGciOi… или kimi-auth=eyJhbGciOi…",
            help=("JWT из куки kimi-auth на www.kimi.com: DevTools (F12) → "
                  "Application → Cookies → www.kimi.com → kimi-auth → Value. "
                  "Можно вставить голый JWT, всю строку Cookie или "
                  "«Authorization: Bearer …». Других кук не нужно."),
            label_en="Kimi-auth (JWT or Cookie)",
            help_en=("JWT from the kimi-auth cookie on www.kimi.com: DevTools (F12) → "
                     "Application → Cookies → www.kimi.com → kimi-auth → Value. "
                     "A bare JWT, a full Cookie string or “Authorization: Bearer …” "
                     "all work. No other cookies are needed."),
            placeholder_en="eyJhbGciOi… or kimi-auth=eyJhbGciOi…",
        )]

    def seed_models(self) -> List[Tuple[str, str]]:
        # kimi-k2.5 и moonshot-v1 сняты с обслуживания в августе 2026.
        return [("kimi-k2.6", "Kimi K2.6"),
                ("kimi-k2.6-thinking", "Kimi K2.6 с рассуждениями"),
                ("kimi-k3", "Kimi K3")]

    def validate_fields(self, fields: Dict[str, Any]) -> Optional[str]:
        error = super().validate_fields(fields)
        if error:
            return error
        if not extract_kimi_jwt(fields.get("kimi_auth")):
            return ("Вставьте kimi-auth: JWT из куки www.kimi.com "
                    "(голый токен, строка Cookie или «Authorization: Bearer …»)")
        return None

    def _jwt(self, account) -> str:
        token = extract_kimi_jwt((account.fields or {}).get("kimi_auth"))
        if not token:
            raise ProviderAuthError(
                "Kimi: нет kimi-auth — вставьте JWT из куки www.kimi.com")
        return token

    def _headers(self, token: str) -> Dict[str, str]:
        return {
            "Content-Type": "application/connect+json",
            "Accept": "application/connect+json",
            "connect-protocol-version": "1",
            "x-msh-platform": "web",
            "Authorization": f"Bearer {token}",
            "Cookie": f"kimi-auth={token}",
        }

    async def check(self, account) -> Tuple[Optional[bool], str]:
        token = self._jwt(account)
        try:
            session = await hub_http.get_session(
                self.proxy_for(account), impersonate=True, url=USER_URL)
            resp = await session.get(USER_URL, headers=self._headers(token),
                                     timeout=30)
        except Exception as exc:  # noqa: BLE001
            return None, f"сеть недоступна: {hub_http.network_error_text(exc)}"
        if resp.status_code in (401, 403):
            return False, "сессия недействительна — обновите kimi-auth из браузера"
        if resp.status_code != 200:
            return None, f"HTTP {resp.status_code}: /api/user недоступен"
        try:
            data = resp.json()
        except Exception:
            data = {}
        name = str(data.get("name") or data.get("email") or "").strip()
        return True, f"Kimi: {name}" if name else "Kimi: сессия активна"

    async def chat_stream(self, account, model: str, messages: List[Dict[str, Any]],
                          params: Dict[str, Any]) -> AsyncIterator[Dict[str, Any]]:
        token = self._jwt(account)
        payload = build_chat_payload(model, messages, params)
        frame = pack_connect_frame(
            json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        try:
            session = await hub_http.get_session(
                self.proxy_for(account), impersonate=True, url=CHAT_URL)
            resp = await session.post(
                CHAT_URL, headers=self._headers(token), content=frame,
                stream=True, timeout=config_store.settings.request_timeout)
        except Exception as exc:  # noqa: BLE001
            raise ProviderNetworkError(
                f"Kimi: network error: {hub_http.network_error_text(exc)}") from exc
        status = resp.status_code
        if status in (401, 403):
            raise ProviderAuthError(
                "Kimi rejected the session (HTTP %d) — refresh kimi-auth" % status)
        if status != 200:
            head = ""
            try:
                async for chunk in resp.aiter_content():
                    head += chunk.decode("utf-8", errors="replace") if isinstance(chunk, bytes) else str(chunk)
                    if len(head) > 4096:
                        break
            except Exception:
                pass
            message = head.strip()
            try:
                body = json.loads(message)
                if isinstance(body, dict):
                    inner = body.get("error") or body.get("message") or body
                    if isinstance(inner, dict):
                        message = str(inner.get("message") or inner.get("code") or message)
                    elif isinstance(inner, str):
                        message = inner
            except Exception:
                pass
            raise ProviderServerError(f"Kimi: HTTP {status}: {message[:500]}")
        parser = ConnectFrameParser()
        got_any = False
        try:
            async for chunk in resp.aiter_content():
                if not chunk:
                    continue
                try:
                    frames = parser.feed(chunk if isinstance(chunk, bytes) else bytes(chunk))
                except ProviderServerError:
                    raise
                for raw in frames:
                    try:
                        obj = json.loads(raw.decode("utf-8"))
                    except ValueError:
                        continue
                    if is_end_of_stream(obj):
                        return
                    for kind, text in translate_event(obj):
                        if text:
                            got_any = True
                            yield {"type": kind, "text": text}
        except ProviderServerError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProviderNetworkError(
                f"Kimi: stream error: {hub_http.network_error_text(exc)}") from exc
        if not got_any:
            raise ProviderServerError("Kimi closed the stream without an answer")
