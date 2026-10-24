"""Общая база провайдеров MemuAI Proxy.

Провайдер = тип аккаунта (Genspark, Kiro, Antigravity, …). Ядро знает провайдера
только через этот интерфейс: схему полей аккаунта, каталог моделей по
умолчанию, поток чата (нормализованные события), проверку аккаунта и лимиты.
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field as dc_field
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from ..config import config_store


_JSON_BODY_RE = re.compile(r"\{.*\}", re.S)


def readable_provider_error(text: str) -> str:
    """Replace a raw JSON error body inside ``text`` with the message it carries.

    Providers quote upstream bodies like ``{"error":{"message":"Model … does not exist",…}}``;
    the chat showed that JSON verbatim. Text without such a body is returned unchanged.
    """
    text = str(text or "")
    found = _JSON_BODY_RE.search(text)
    if not found:
        return text
    try:
        body = json.loads(found.group(0))
    except ValueError:
        return text
    inner = body.get("error", body) if isinstance(body, dict) else None
    if isinstance(inner, dict):
        inner = inner.get("message") or inner.get("detail") or inner.get("msg")
    if not isinstance(inner, str) or not inner.strip():
        return text
    return (text[:found.start()] + inner.strip() + text[found.end():]).strip()


# Provider messages are written in English (the API clients' language). The Russian error text of the
# same failure translates their technical phrases, so the chat does not mix an English sentence with a
# Russian hint. The upstream's own quoted body stays as it was sent.
_RU_PHRASES: Tuple[Tuple["re.Pattern[str]", str], ...] = tuple((re.compile(pattern), ru) for pattern, ru in (
    (r"The API key was rejected \(HTTP (\d+)\)", r"API-ключ отклонён (HTTP \1)"),
    (r"Upstream rate limit \(HTTP 429\)", "Апстрим ограничил частоту запросов (HTTP 429)"),
    (r"The upstream balance is used up \(HTTP 402\)", "Баланс у апстрима исчерпан (HTTP 402)"),
    (r"Upstream error \(HTTP (\d+)\)", r"Ошибка апстрима (HTTP \1)"),
    (r"Upstream error:", "Ошибка апстрима:"),
    (r"Empty upstream response", "Пустой ответ апстрима"),
    (r"The upstream stream broke off:", "Поток апстрима оборвался:"),
    (r"The upstream sent no data for over (\d+) s\b", r"Апстрим не присылал данных дольше \1 с"),
    (r"(\w+) sent no data for over (\d+) s\b", r"\1 не присылал данных дольше \2 с"),
    (r"The upstream JSON response exceeds 4 MB", "JSON-ответ апстрима больше 4 МБ"),
    (r"Network error during generation:", "Сетевая ошибка при генерации:"),
    (r"Network error:", "Сетевая ошибка:"),
    (r"The account has no base_url", "У аккаунта не указан base_url"),
    (r"Could not pick an API key for the request", "Не удалось выбрать API-ключ для запроса"),
    (r"The model or API rejected the image \(HTTP (\d+)\)\. Pick a vision model or remove the attachment\.",
     r"Модель или API отклонили изображение (HTTP \1). Выберите модель со зрением или уберите вложение."),
    (r"Image generation rate limit \(HTTP 429\)", "Ограничение частоты генерации изображений (HTTP 429)"),
    (r"Image generation rate limit", "Ограничение частоты генерации изображений"),
    (r"The image generation limit is used up \(HTTP 402\)", "Лимит генерации изображений исчерпан (HTTP 402)"),
    (r"The daily generation limit is used up", "Дневной лимит генераций исчерпан"),
    (r"The (\w+) limit is used up \(HTTP 402\)", r"Лимит \1 исчерпан (HTTP 402)"),
    (r"The (\w+) limit is used up", r"Лимит \1 исчерпан"),
    (r"The (\w+) quota is used up \(HTTP 429\)", r"Квота \1 исчерпана (HTTP 429)"),
    (r"([\w.]+) is throttling requests \(HTTP 429\)", r"\1 ограничивает частоту запросов (HTTP 429)"),
    (r"([\w.]+) is rate limiting auth/session \(HTTP 429\)", r"\1 ограничивает частоту входа (HTTP 429)"),
    (r"(\w+) rate limit \(WebSocket HTTP 429\)", r"\1 ограничивает частоту запросов (WebSocket HTTP 429)"),
    (r"Rate limit (\w+)$", r"Ограничение частоты запросов \1"),
    (r"Rate limit:", "Ограничение частоты запросов:"),
    (r"The sso cookie was rejected", "Кука sso отклонена"),
    (r"The cookie was rejected during generation \(HTTP (\d+)\)", r"Кука отклонена во время генерации (HTTP \1)"),
    (r"(\w+) rejected the sso cookie", r"\1 отклонил куку sso"),
    (r"(\w+) rejected the token \(HTTP (\d+)\)", r"\1 отклонил токен (HTTP \2)"),
    (r"(\w+) rejected the refresh \(HTTP (\d+)\)", r"\1 отклонил обновление токена (HTTP \2)"),
    (r"(\w+) token refresh failed \(HTTP (\d+)\)", r"Не удалось обновить токен \1 (HTTP \2)"),
    (r"(\w+) token refresh is rate limited \(HTTP 429\)", r"Обновление токена \1 ограничено по частоте (HTTP 429)"),
    (r"(\w+) token refresh returned no access token", r"Обновление токена \1 не вернуло access token"),
    (r"The (\w+) token expired and cannot be refreshed \(HTTP (\d+)\)",
     r"Токен \1 истёк, обновить его не удалось (HTTP \2)"),
    (r"The account has no (\w+) tokens", r"У аккаунта нет токенов \1"),
    (r"No (\w+) refresh token", r"Нет refresh-токена \1"),
    (r"— sign in again", "— войдите заново"),
    (r"No (\w+) host responded", r"Ни один хост \1 не ответил"),
    (r"No (\w+) endpoint responded", r"Ни один адрес \1 не ответил"),
    (r"(\w+) closed the stream without an answer", r"\1 закрыл поток без ответа"),
    (r"The (\w+) stream broke off:", r"Поток \1 оборвался:"),
    (r"(\w+) rejected the request", r"\1 отклонил запрос"),
    (r"auth/session returned no user id — the cookie is invalid", "auth/session не вернул user id — кука недействительна"),
    (r"Generation did not reach the success state in time", "Генерация не завершилась вовремя"),
))


def provider_error_ru(text: str) -> str:
    """The Russian wording of a provider error: its known English phrases translated, the rest unchanged."""
    text = str(text or "")
    for pattern, ru in _RU_PHRASES:
        text = pattern.sub(ru, text)
    return text


# ------------------------------ исключения ------------------------------

class ProviderError(Exception):
    """Базовая ошибка провайдера. status — код для HTTP-ответа клиенту.

    message — английский текст (язык API-клиентов). message_ru — русский вариант, когда провайдер
    знает его сам (локальные модели); иначе русский текст даёт provider_error_ru.
    """

    def __init__(self, message: str, status: int = 502, *, message_ru: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.message_ru = message_ru


def provider_error_texts(exc: Exception, account: Any = None) -> Tuple[str, str]:
    """(Russian, English) public text of a provider error, credentials redacted."""
    english = safe_provider_error(exc, account)
    russian = getattr(exc, "message_ru", "")
    return (safe_provider_error(russian, account) if russian else provider_error_ru(english)), english


class ProviderAuthError(ProviderError):
    """Кука/токен не приняты — аккаунт помечается битым."""

    def __init__(self, message: str) -> None:
        super().__init__(message, 401)


class ProviderRateLimitError(ProviderError):
    """Кратковременный rate limit: запрос уходит на следующий аккаунт или повторяется.

    retry_after — пауза в секундах, которую назвал апстрим (Retry-After), если назвал.
    """

    def __init__(self, message: str, retry_after: Optional[float] = None) -> None:
        super().__init__(message, 429)
        self.retry_after = retry_after


def retry_after_seconds(headers: Any) -> Optional[float]:
    """The pause an upstream asks for: ``retry-after-ms`` or ``Retry-After`` (seconds or an HTTP date)."""
    if headers is None:
        return None
    try:
        millis = headers.get("retry-after-ms")
        raw = headers.get("retry-after")
    except Exception:
        return None
    try:
        if millis not in (None, ""):
            return min(max(float(millis) / 1000.0, 0.0), 86400.0)
    except (TypeError, ValueError):
        pass
    raw = str(raw or "").strip()
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        from email.utils import parsedate_to_datetime
        import time as _time
        try:
            seconds = parsedate_to_datetime(raw).timestamp() - _time.time()
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
    if seconds != seconds:  # NaN
        return None
    return min(max(seconds, 0.0), 86400.0)


class ProviderQuotaError(ProviderError):
    """Дневной/месячный лимит аккаунта исчерпан."""

    def __init__(self, message: str) -> None:
        super().__init__(message, 402)


class ProviderServerError(ProviderError):
    """Ошибка на стороне провайдера — пробуем другой аккаунт."""


class ProviderNetworkError(ProviderError):
    """Сеть/таймаут/прокси."""


class ProviderCaptchaError(ProviderError):
    """Провайдер требует captcha-параметр (например, ZCode/Aliyun)."""


class ProviderModelUnavailableError(ProviderError):
    """Аккаунт не имеет доступа к запрошенной модели (тариф/регион).

    Аккаунт НЕ штрафуется: ошибка детерминированная для конкретной пары
    «аккаунт + модель», поэтому пул просто пробует следующий аккаунт.
    """

    def __init__(self, message: str, model: str = "", *, message_ru: str = "") -> None:
        super().__init__(message, 404, message_ru=message_ru)
        self.model = model


class ProviderContentFilterError(ProviderError):
    """Модель отказалась отвечать (фильтры безопасности провайдера).

    Повтор на другом аккаунте бессмысленен — ответ будет тем же.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, 451)


# ------------------------------ поля аккаунта ------------------------------

@dataclass
class Field:
    key: str
    label: str
    ftype: str = "text"          # text | password | textarea | select | number | url
    placeholder: str = ""
    required: bool = True
    help: str = ""
    options: List[str] = dc_field(default_factory=list)
    default: str = ""
    # Английские варианты подписей: панель показывает их при LANG == "en".
    label_en: str = ""
    help_en: str = ""
    placeholder_en: str = ""

    def public_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key, "label": self.label, "type": self.ftype,
            "placeholder": self.placeholder, "required": self.required,
            "help": self.help, "options": list(self.options), "default": self.default,
            "label_en": self.label_en or self.label,
            "help_en": self.help_en or self.help,
            "placeholder_en": self.placeholder_en or self.placeholder,
        }


def normalize_messages(messages: List[Dict[str, Any]]) -> Tuple[str, List[Dict[str, str]], str]:
    """(system, история без последнего user, последний user) — общая утилита."""
    system_parts: List[str] = []
    parsed: List[Dict[str, str]] = []
    for m in messages or []:
        role = m.get("role", "user")
        text = content_to_text(m.get("content"))
        if role in ("system", "developer"):
            if text:
                system_parts.append(text)
            continue
        parsed.append({"role": role, "content": text})
    final_user = ""
    found = False
    for i in range(len(parsed) - 1, -1, -1):
        if parsed[i]["role"] == "user":
            final_user = parsed[i]["content"]
            parsed.pop(i)
            found = True
            break
    if not found and parsed:
        final_user = parsed.pop()["content"]
    return "\n\n".join(system_parts), parsed, (final_user or "(empty message)")


def content_to_text(content: Any) -> str:
    """OpenAI content (строка | список частей) → текст."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for p in content:
            if isinstance(p, dict) and p.get("type") in ("text", "input_text", "output_text"):
                parts.append(str(p.get("text", "")))
        return "\n".join(parts)
    return str(content)


def messages_have_images(messages: List[Dict[str, Any]]) -> bool:
    """Detect OpenAI-style attachment parts without decoding their payloads."""
    for message in messages or []:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") in (
                "image_url", "input_image", "image", "input_file", "file", "file_url",
            ):
                return True
    return False


def transcript_prompt(system_text: str, history: List[Dict[str, str]], final_user: str) -> str:
    """История транскриптом — для провайдеров без серверного контекста."""
    if not history:
        if system_text:
            return f"<system_instructions>\n{system_text}\n</system_instructions>\n\n{final_user}"
        return final_user
    lines: List[str] = []
    if system_text:
        lines.append(f"<system_instructions>\n{system_text}\n</system_instructions>")
    lines.append("<conversation_so_far>")
    for m in history:
        lines.append(f"{'user' if m['role'] == 'user' else 'assistant'}: {m['content']}")
    lines.append("</conversation_so_far>")
    lines.append("")
    lines.append("Continue the conversation. New user message:")
    lines.append(final_user)
    return "\n".join(lines)


def approx_tokens(text: str, model: str = "") -> int:
    """Tokens of a text the provider did not count: language-aware estimate (app/token_estimate.py)."""
    from ..token_estimate import estimate
    return max(1, estimate(text, model))


_BACKGROUND: set = set()


def spawn_background(coro) -> "asyncio.Task":
    """Fire-and-forget task with a strong reference: the event loop keeps only
    weak references, so an unreferenced task (an OAuth listener waiting for the
    browser callback, a cleanup request) could be garbage-collected mid-way."""
    task = asyncio.get_running_loop().create_task(coro)
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)
    return task


_ERROR_SECRET_KEY_RE = re.compile(
    r"token|cookie|password|passwd|passphrase|secret|credential|api.?key|"
    r"authorization|authentication|oauth|session|private.?key|headers?",
    re.IGNORECASE,
)
_ERROR_URL_USERINFO_RE = re.compile(
    r"([a-z][a-z0-9+.-]*://)([^/@\s]+)@", re.IGNORECASE)
_ERROR_AUTH_VALUE_RE = re.compile(r"\b(Bearer|Basic)\s+[^\s,;]+", re.IGNORECASE)
_ERROR_NAMED_SECRET_RE = re.compile(
    r"((?:api[_-]?key|(?:access[_-]?|refresh[_-]?|id[_-]?)?token|password|passwd|"
    r"passphrase|client[_-]?secret|secret|cookie)"
    r"(?:[\"']?\s*[=:]\s*[\"']?|[-_]))[^&\s,;\"'}]+",
    re.IGNORECASE,
)


def safe_provider_error(exc: Exception | str, account: Any = None) -> str:
    """Redact account credentials and common auth syntax from public errors."""
    message = str(exc)
    secrets: set[str] = set()

    def collect(value: Any, sensitive: bool = False) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                key_sensitive = sensitive or bool(_ERROR_SECRET_KEY_RE.search(str(key)))
                if key_sensitive and isinstance(child, str) and str(key).lower() in ("header", "headers"):
                    try:
                        collect(json.loads(child), True)
                    except Exception:
                        pass
                collect(child, key_sensitive)
        elif isinstance(value, (list, tuple)):
            for child in value:
                collect(child, sensitive)
        elif sensitive and value is not None:
            candidate = str(value)
            if len(candidate) >= 6:
                secrets.add(candidate)

    if account is not None:
        collect(getattr(account, "fields", {}))
        collect(getattr(account, "extra", {}))
        proxy = str(getattr(account, "proxy", "") or "")
        if proxy:
            secrets.add(proxy)

    for secret in sorted(secrets, key=len, reverse=True):
        message = message.replace(secret, "[REDACTED]")
    message = _ERROR_URL_USERINFO_RE.sub(r"\1[REDACTED]@", message)
    message = _ERROR_AUTH_VALUE_RE.sub(r"\1 [REDACTED]", message)
    message = _ERROR_NAMED_SECRET_RE.sub(r"\1[REDACTED]", message)
    # Round 34: libcurl's boilerplate tail («See https://curl.se/libcurl/c/libcurl-errors.html first for more
    # details.») doubled every network error in the chat and the log without saying anything.
    message = _CURL_BOILERPLATE_RE.sub("", message)
    return message


_CURL_BOILERPLATE_RE = re.compile(r"\s*See https://curl\.se/libcurl/c/libcurl-errors\.html first for more details\.?")


class Provider:
    """Базовый класс провайдера."""
    id: str = ""
    title: str = ""
    icon: str = "◆"
    oauth: bool = False            # есть кнопка «Войти через браузер»
    oauth_port: int = 0            # фиксированный порт колбэка (Kiro 3128, Antigravity 51121)
    description: str = ""
    title_en: str = ""             # локализованные метаданные для панели
    description_en: str = ""
    # Провайдер умеет искать в вебе сам (его API принимает флаг поиска).
    # Остальным поиск выполняет сам Proxy — см. app/websearch.py.
    native_search: bool = False

    def account_fields(self) -> List[Field]:
        return []

    def settings_fields(self) -> List[Field]:
        """Настройки провайдера для панели (сохраняются в provider_settings).

        `Field.default` задаёт значение, которое применяется, пока пользователь
        ничего не выбрал. Общие для всех провайдеров настройки (например режим
        веб-поиска) добавляются поверх этого списка автоматически.
        """
        return []

    def seed_models(self) -> List[Tuple[str, str]]:
        """[(модель, примечание)] — стартовый каталог провайдера."""
        return []

    def model_note(self, model: str) -> str:
        """Подпись найденной модели в каталоге (название у самого провайдера); пусто — по умолчанию."""
        return ""

    def validate_fields(self, fields: Dict[str, Any]) -> Optional[str]:
        for f in self.account_fields():
            v = str(fields.get(f.key, "") or "").strip()
            if f.required and not v:
                return f"Заполните поле «{f.label}»"
        return None

    def proxy_for(self, account) -> str:
        return account.proxy or config_store.settings.default_proxy

    async def chat_stream(self, account, model: str, messages: List[Dict[str, Any]],
                          params: Dict[str, Any]) -> AsyncIterator[Dict[str, Any]]:
        """Нормализованные события: {"type": "text"|"reasoning", "text": str}.
        Ошибки — исключениями Provider*Error. Реализуется в наследниках."""
        raise NotImplementedError
        yield {}

    async def check(self, account) -> Tuple[Optional[bool], str]:
        """Проверка аккаунта: True/False/None(сеть), сообщение."""
        return None, "не поддерживается"

    async def usage(self, account) -> Optional[Dict[str, Any]]:
        """Лимиты/квоты для карточки аккаунта (или None)."""
        return None

    async def dynamic_models(self, account) -> Optional[List[str]]:
        """Живой список моделей с сервера провайдера (или None)."""
        return None

    async def oauth_start(self) -> Dict[str, Any]:
        raise ProviderError("This provider does not support OAuth sign-in")

    async def oauth_status(self, flow_id: str) -> Dict[str, Any]:
        return {"status": "error", "message": "нет активного flow"}

    async def close(self) -> None:
        return
