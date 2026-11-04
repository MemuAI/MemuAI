"""Шаблон веб-обёртки (2api-стиль) — образец для своего провайдера.

Скопируйте файл в папку плагинов под новым именем и поправьте места,
помеченные ``TODO``:

1. ``ENDPOINT`` — URL чата сайта, ``SITE`` — его главная (для check()).
2. ``AUTH_COOKIE`` — имя куки сессии (или токена).
3. ``build_request()`` — тело запроса: как сайт ждёт историю и модель.
4. ``extract_text()`` — где в ответе текст/рассуждения (SSE, JSON, свой формат).

Паттерн тот же, что у встроенных веб-провайдеров MemuAI (Genspark, Kiro,
Antigravity): сессия из браузера, особые заголовки, нестандартный ответ.
Всё, что не относится к сайту, уже готово: регистрация в Proxy, поля
аккаунта, модели, проверка, ошибки.

Пока ENDPOINT не заменён, плагин загружается, но честно отказывается
работать — чтобы шаблон в списке провайдеров не выглядел рабочим.
"""
from __future__ import annotations

PLUGIN_API = 1

import json
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

# TODO(1): адрес чата сайта и его главная страница.
ENDPOINT = "https://example.com/api/chat"
SITE = "https://example.com/"
# TODO(2): имя куки сессии (например, "session_id", "sso", "access_token").
AUTH_COOKIE = "session_id"


def parse_cookie_value(raw: Any, name: str) -> Optional[str]:
    """Значение куки ``name`` из голого значения или строки Cookie."""
    text = str(raw or "").strip()
    if not text:
        return None
    for part in text.replace(";", "\n").splitlines():
        part = part.strip()
        if part.startswith(name + "="):
            value = part[len(name) + 1:].strip().strip('"')
            if value:
                return value
    if "=" not in text and "\n" not in text and " " not in text:
        return text  # вставлено голое значение куки
    return None


def build_request(model: str, messages: List[Dict[str, Any]],
                  params: Dict[str, Any]) -> Dict[str, Any]:
    """TODO(3): тело запроса так, как его ждёт сайт."""
    system, history, final_user = normalize_messages(messages)
    folded: List[Dict[str, str]] = []
    if system:
        folded.append({"role": "system", "content": system})
    folded.extend(history)
    folded.append({"role": "user", "content": final_user})
    body: Dict[str, Any] = {"model": model, "messages": folded, "stream": True}
    if params.get("temperature") is not None:
        body["temperature"] = params["temperature"]
    return body


def extract_text(obj: Any) -> List[Tuple[str, str]]:
    """TODO(4): текст/рассуждения из одного события ответа сайта.

    Пример ниже понимает OpenAI-style SSE (choices[0].delta.content /
    reasoning_content). Для своего формата поправьте пути.
    """
    if not isinstance(obj, dict):
        return []
    found: List[Tuple[str, str]] = []
    choices = obj.get("choices")
    if isinstance(choices, list) and choices:
        delta = choices[0].get("delta") if isinstance(choices[0], dict) else None
        if isinstance(delta, dict):
            if isinstance(delta.get("content"), str) and delta["content"]:
                found.append(("text", delta["content"]))
            if isinstance(delta.get("reasoning_content"), str) and delta["reasoning_content"]:
                found.append(("reasoning", delta["reasoning_content"]))
    if not found and isinstance(obj.get("content"), str) and obj["content"]:
        found.append(("text", obj["content"]))
    return found


class TemplateWebProvider(Provider):
    id = "web-template"
    title = "Веб-обёртка (шаблон)"
    icon = "🧩"
    title_en = "Web wrapper (template)"
    description = ("Шаблон провайдера-обёртки над сайтом: поправьте ENDPOINT, "
                   "куку и формат ответа. Пока не настроен — не работает.")
    description_en = ("Template for a website-wrapper provider: set ENDPOINT, "
                      "the cookie and the response format. Not configured yet.")

    def account_fields(self) -> List[Field]:
        return [Field(
            AUTH_COOKIE, f"Кука {AUTH_COOKIE}", "password",
            placeholder=f"{AUTH_COOKIE}=…",
            help=(f"Значение куки {AUTH_COOKIE} с сайта {SITE}: браузер → F12 → "
                  "Application → Cookies. Можно вставить голое значение или "
                  "всю строку Cookie."),
            label_en=f"{AUTH_COOKIE} cookie",
            help_en=(f"The {AUTH_COOKIE} cookie value from {SITE}: browser → F12 → "
                     "Application → Cookies. A bare value or a full Cookie string."),
        )]

    def seed_models(self) -> List[Tuple[str, str]]:
        return [("template-model", "Модель из шаблона")]

    def _cookie(self, account) -> str:
        value = parse_cookie_value((account.fields or {}).get(AUTH_COOKIE), AUTH_COOKIE)
        if not value:
            raise ProviderAuthError(
                f"Нет куки {AUTH_COOKIE} — вставьте её из браузера ({SITE})")
        return value

    async def check(self, account) -> Tuple[Optional[bool], str]:
        if ENDPOINT.startswith("https://example.com"):
            return None, "шаблон не настроен: поправьте ENDPOINT"
        cookie = self._cookie(account)
        try:
            session = await hub_http.get_session(
                self.proxy_for(account), impersonate=True, url=SITE)
            resp = await session.get(
                SITE, headers={"Cookie": f"{AUTH_COOKIE}={cookie}"}, timeout=30)
        except Exception as exc:  # noqa: BLE001
            return None, f"сеть недоступна: {hub_http.network_error_text(exc)}"
        if resp.status_code in (401, 403):
            return False, "кука отклонена — войдите на сайте заново"
        if resp.status_code != 200:
            return None, f"HTTP {resp.status_code}"
        return True, "сессия активна"

    async def chat_stream(self, account, model: str, messages: List[Dict[str, Any]],
                          params: Dict[str, Any]) -> AsyncIterator[Dict[str, Any]]:
        if ENDPOINT.startswith("https://example.com"):
            raise ProviderServerError(
                "Шаблон не настроен: поправьте ENDPOINT, AUTH_COOKIE, "
                "build_request() и extract_text() в файле плагина")
        cookie = self._cookie(account)
        body = build_request(model, messages, params)
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Cookie": f"{AUTH_COOKIE}={cookie}",
            "Referer": SITE,
        }
        try:
            session = await hub_http.get_session(
                self.proxy_for(account), impersonate=True, url=ENDPOINT)
            resp = await session.post(
                ENDPOINT, headers=headers, json=body, stream=True,
                timeout=config_store.settings.request_timeout)
        except Exception as exc:  # noqa: BLE001
            raise ProviderNetworkError(
                f"network error: {hub_http.network_error_text(exc)}") from exc
        if resp.status_code in (401, 403):
            raise ProviderAuthError("Сайт отклонил куку — войдите заново")
        if resp.status_code != 200:
            raise ProviderServerError(f"HTTP {resp.status_code}")
        got_any = False
        try:
            async for line in hub_http.aiter_lines_str(
                    resp, idle_timeout=config_store.settings.stream_idle_timeout):
                text = (line or "").strip()
                if not text.startswith("data:"):
                    continue
                payload = text[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    obj = json.loads(payload)
                except ValueError:
                    continue
                for kind, piece in extract_text(obj):
                    if piece:
                        got_any = True
                        yield {"type": kind, "text": piece}
        except ProviderServerError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProviderNetworkError(
                f"stream error: {hub_http.network_error_text(exc)}") from exc
        if not got_any:
            raise ProviderServerError("Сайт закрыл поток без ответа")
