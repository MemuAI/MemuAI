"""What went wrong with a model request — classified precisely and told openly.

Round 32: the user saw «Слишком большой запрос» for a failure that had nothing to do with size, and the
real upstream text («No available accounts») was lost on the way. Classification here is driven by the
upstream's own words first and the HTTP status second; a status alone never makes an error a "too large"
or "context overflow" one. Every explanation keeps the original message, the status, the provider and the
model, so the user always sees what actually happened.

``classify(status, message, kind)`` → :class:`ErrorInfo` (category + short human explanation + advice);
``describe(exc, model=…)`` → the text shown in the terminal / headless output.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional, Tuple

from .i18n import tr

CATEGORIES = (
    "context_overflow", "too_many_messages", "payload_too_large", "proxy_no_accounts", "upstream_no_accounts", "rate_limit",
    "quota", "auth", "proxy_auth", "forbidden", "model_not_found", "model_unavailable", "content_filter",
    "image_rejected", "bad_request", "timeout", "network", "proxy_offline", "proxy_disabled", "server",
    "empty_answer", "unknown")

_P = lambda pattern: re.compile(pattern, re.I)  # noqa: E731

_TEXT_RULES: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("proxy_no_accounts", _P(r"no usable [“\"«]|нет доступных аккаунтов [«\"“]|no enabled [“\"«].{0,40}connection|"
                             r"нет включённого подключения")),
    ("upstream_no_accounts", _P(r"no (?:available|active|healthy) (?:accounts?|channels?|keys?|providers?|"
                                r"upstreams?|endpoints?)|all (?:accounts|channels|keys) "
                                r"(?:are )?(?:busy|exhausted|unavailable)|no channel available|"
                                r"无可用|没有可用")),
    ("too_many_messages", _P(r"too many messages|слишком много сообщений")),
    ("context_overflow", _P(
        r"context[_ ]length[_ ]exceeded|maximum context length|context window|prompt is too long|input is too long|"
        r"too many (?:input |prompt )?tokens|exceeds? the (?:model'?s? )?(?:maximum )?(?:context|token limit|"
        r"input limit)|reduce the length of the (?:messages|prompt)|token limit exceeded|"
        r"max(?:imum)?[_ ]input[_ ]tokens|input token count|prompt tokens? (?:limit|exceed)|"
        r"превыш\w* (?:длин\w* |лимит\w* |размер\w* )?контекст|слишком длинн\w* (?:запрос|контекст)")),
    ("content_filter", _P(r"content[_ ]filter|content management policy|safety (?:system|filter)|"
                          r"responsible ai|flagged|фильтр\w* безопасност")),
    ("image_rejected", _P(r"rejected the image|image (?:input )?(?:is )?not supported|does not support "
                          r"(?:image|vision)|отклонили изображение|invalid image")),
    ("quota", _P(r"insufficient[_ ](?:quota|balance|funds|credits?)|balance is (?:too low|used up|insufficient)|"
                 r"out of credits|quota (?:exceeded|is used up|exhausted)|exceeded your current quota|"
                 r"payment required|billing|баланс\w* (?:исчерпан|недостаточно)|лимит \w+ исчерпан|"
                 r"квота \w* ?исчерпана|daily (?:generation )?limit|"

                 r"закончил\w* (?:баланс|средства|кредиты|квота)|баланс\w* (?:закончил|на нуле)|недостаточно средств|"
                 r"пополните баланс|not enough (?:credits?|balance|funds)|credit balance is too low|"
                 r"insufficient_user_quota|(?:credits?|balance) (?:exhausted|depleted|ran out)|余额不足")),
    ("rate_limit", _P(r"rate[ _-]?limit|too many requests|throttl|requests per (?:minute|day)|\brpm\b|\btpm\b|"
                      r"ограничи\w* частот|слишком часто")),
    ("model_not_found", _P(r"model[^.\n]{0,80}(?:does not exist|not found|is not supported|unknown)|"
                           r"no such model|unknown model|invalid model|не нашёл модель|нет такой модели")),
    ("model_unavailable", _P(r"не входит в тариф|not part of any|no access to (?:this|the) model|"
                             r"model (?:is )?(?:not available|unavailable|overloaded)|model_not_available")),
    ("auth", _P(r"invalid (?:or missing )?api[ _-]?key|incorrect api key|api key (?:was )?(?:rejected|invalid)|"
                r"unauthori[sz]ed|authentication|ключ\w* отклон|токен\w* (?:истёк|отклон)|кука\w* отклон")),
    ("timeout", _P(r"timed? ?out|timeout|deadline exceeded|не присылал данных|не отвечает")),
    ("network", _P(r"connection (?:reset|refused|aborted|error)|connecterror|network error|сетевая ошибка|"
                   r"name or service not known|getaddrinfo|ssl|certificate|eof occurred|"
                   r"stream broke off|поток \S+ оборвался|связь с memuai proxy прервалась|remoteprotocolerror")),
    ("proxy_disabled", _P(r"proxy is switched off|прокси выключен")),
)

_PAYLOAD = _P(r"payload too large|request entity too large|request body (?:is )?too large|body too large|"
              r"тело запроса слишком|request (?:is )?too large|слишком большой запрос|запрос слишком большой")

@dataclass
class ErrorInfo:
    category: str
    title: str
    advice: str
    retryable: bool = False
    compactable: bool = False

    def to_dict(self) -> dict:
        return {"category": self.category, "title": self.title, "advice": self.advice,
                "retryable": self.retryable, "compactable": self.compactable}

def _by_status(status: int, kind: str) -> str:
    if kind in ("offline",):
        return "proxy_offline"
    if kind in ("network",):
        return "network"
    if kind in ("timeout",):
        return "timeout"
    if status == 401:
        return "auth"
    if status == 402:
        return "quota"
    if status == 403:
        return "forbidden"
    if status == 404:
        return "model_not_found"
    if status == 408 or status == 504:
        return "timeout"
    if status == 413:
        return "payload_too_large"
    if status == 429:
        return "rate_limit"
    if status in (400, 409, 415, 422):
        return "bad_request"
    if status >= 500:
        return "server"
    return "unknown"

def category_of(status: int = 0, message: str = "", kind: str = "") -> str:
    """The category of an error from its upstream text first, its status second.

    The text wins because an upstream says what it means («No available accounts» arrives as 503, 429 or
    even 400 depending on the gateway); the status only fills in when the text says nothing known."""
    text = str(message or "")
    kind = str(kind or "")
    if kind == "proxy_auth":
        return "proxy_auth"
    if kind == "empty":
        return "empty_answer"
    if kind in ("offline",):
        return "proxy_offline"
    for category, pattern in _TEXT_RULES:
        if pattern.search(text):
            if category == "context_overflow" and status not in (0, 400, 413, 422, 500):
                break
            return category
    if _PAYLOAD.search(text):

        return "payload_too_large" if status in (0, 400, 413) else _by_status(status, kind)
    by_kind = {"authentication_error": "auth", "rate_limit_error": "rate_limit", "quota_exceeded": "quota",
               "content_filter": "content_filter", "model_unavailable": "model_unavailable",
               "model_not_found": "model_not_found", "network_error": "network", "service_unavailable":
               "proxy_no_accounts", "proxy_disabled": "proxy_disabled", "auth": "auth"}.get(kind)
    if by_kind:
        return by_kind
    return _by_status(int(status or 0), kind)

def classify(status: int = 0, message: str = "", kind: str = "") -> ErrorInfo:
    category = category_of(status, message, kind)
    title, advice = _TEXTS[category]()
    return ErrorInfo(category, title, advice,
                     retryable=category in ("rate_limit", "timeout", "network", "server", "upstream_no_accounts",
                                            "empty_answer"),
                     compactable=category in ("context_overflow", "too_many_messages", "payload_too_large"))

_TEXTS = {
    "context_overflow": lambda: (
        tr("Разговор не помещается в контекст модели", "The conversation does not fit the model's context window"),
        tr("MemuCode сожмёт историю и повторит запрос; если не поможет — /compact или модель с большим контекстом.",
           "MemuCode compacts the history and retries; if that does not help — /compact or a model with a larger "
           "context.")),
    "too_many_messages": lambda: (
        tr("В запросе слишком много сообщений для Proxy", "Too many messages in one request for the Proxy"),
        tr("MemuCode сам сжимает историю один раз; если ошибка осталась — /compact или /clear, и обновите "
           "MemuAI Proxy (новые версии принимают до 5000 сообщений).",
           "MemuCode compacts the history once by itself; if the error stays — /compact or /clear, and update "
           "MemuAI Proxy (newer versions accept up to 5000 messages).")),
    "payload_too_large": lambda: (
        tr("Запрос слишком большой по объёму (байты, не токены)", "The request body is too large (bytes, not tokens)"),
        tr("Обычно виноваты большие вложения или картинки: уберите их или сожмите историю (/compact).",
           "Usually large attachments or images: remove them or compact the history (/compact).")),
    "upstream_no_accounts": lambda: (
        tr("У провайдера сейчас нет свободных аккаунтов для этой модели",
           "The provider has no free account for this model right now"),
        tr("Это сбой на стороне сервиса, а не размер запроса. Повторите через минуту или выберите другую модель "
           "(/model); если повторяется постоянно — модель у этого провайдера, вероятно, недоступна.",
           "This is the service's side, not the size of your request. Retry in a minute or pick another model "
           "(/model); if it keeps happening, the model is probably not available at this provider.")),
    "proxy_no_accounts": lambda: (
        tr("В MemuAI Proxy нет рабочего аккаунта для этой модели", "MemuAI Proxy has no working account for this model"),
        tr("Добавьте или включите аккаунт провайдера в MemuAI Proxy → «Аккаунты», либо выберите другую модель (/model).",
           "Add or enable an account of this provider in MemuAI Proxy → Accounts, or pick another model (/model).")),
    "rate_limit": lambda: (
        tr("Провайдер ограничил частоту запросов (лимит)", "The provider is rate limiting requests"),
        tr("Подождите немного и повторите; второй ключ или аккаунт в Proxy снимает ограничение.",
           "Wait a little and retry; a second key or account in the Proxy avoids it.")),
    "quota": lambda: (
        tr("Закончились средства или квота аккаунта", "The account is out of credits or quota"),
        tr("Пополните баланс, дождитесь сброса квоты или выберите другую модель/аккаунт (/model).",
           "Top up, wait for the quota to reset, or pick another model/account (/model).")),
    "auth": lambda: (
        tr("Ключ или вход аккаунта не принят провайдером", "The provider rejected the account key or sign-in"),
        tr("Проверьте ключ/куку аккаунта в MemuAI Proxy → «Аккаунты» (или подключение в /proxy).",
           "Check the account key/cookie in MemuAI Proxy → Accounts (or the connection in /proxy).")),
    "proxy_auth": lambda: (
        tr("MemuAI Proxy отклонил запрос MemuCode", "MemuAI Proxy rejected MemuCode's request"),
        tr("Proxy открыт в сеть и требует пароль: верните хост 127.0.0.1 в его настройках.",
           "The Proxy listens on the network and requires a password: set its host back to 127.0.0.1.")),
    "forbidden": lambda: (
        tr("Доступ запрещён (HTTP 403)", "Access denied (HTTP 403)"),
        tr("У ключа нет прав на эту модель или регион; проверьте аккаунт или выберите другую модель.",
           "The key has no access to this model or region; check the account or pick another model.")),
    "model_not_found": lambda: (
        tr("Провайдер не знает эту модель", "The provider does not know this model"),
        tr("Выберите модель из списка (/model) или обновите каталог моделей в Proxy.",
           "Pick a model from the list (/model) or refresh the model catalog in the Proxy.")),
    "model_unavailable": lambda: (
        tr("Модель недоступна вашим аккаунтам", "The model is not available to your accounts"),
        tr("Тариф или регион аккаунта не включает эту модель: выберите другую (/model).",
           "The account plan or region does not include it: pick another model (/model).")),
    "content_filter": lambda: (
        tr("Модель отказалась отвечать по фильтру безопасности", "The model refused because of a safety filter"),
        tr("Переформулируйте запрос; повтор того же текста не поможет.",
           "Rephrase the request; repeating the same text will not help.")),
    "image_rejected": lambda: (
        tr("Модель не приняла изображение", "The model did not accept the image"),
        tr("Выберите модель со зрением или включите описания картинок (/vision).",
           "Pick a vision model or turn on image descriptions (/vision).")),
    "bad_request": lambda: (
        tr("Провайдер отклонил запрос как некорректный", "The provider rejected the request as invalid"),
        tr("Подробности — в тексте ответа выше; часто помогает другая модель или /compact.",
           "See the provider's text above; another model or /compact often helps.")),
    "timeout": lambda: (
        tr("Провайдер не ответил вовремя", "The provider did not answer in time"),
        tr("Повторите запрос; при повторении — другая модель или аккаунт.",
           "Retry; if it repeats, use another model or account.")),
    "network": lambda: (
        tr("Сетевая ошибка по пути к провайдеру", "A network error on the way to the provider"),
        tr("Проверьте интернет и прокси/SSL в настройках MemuAI Proxy, затем повторите.",
           "Check the internet and the proxy/SSL settings of MemuAI Proxy, then retry.")),
    "proxy_offline": lambda: (
        tr("MemuAI Proxy не отвечает", "MemuAI Proxy does not respond"),
        tr("Запустите MemuAI Proxy или настройте прямой API (/proxy add).",
           "Start MemuAI Proxy or configure a direct API (/proxy add).")),
    "proxy_disabled": lambda: (
        tr("Прокси выключен в панели MemuAI Proxy", "The proxy is switched off in MemuAI Proxy"),
        tr("Нажмите «Запустить прокси» на главной вкладке Proxy.", "Press “Start proxy” on the Proxy's Home tab.")),
    "server": lambda: (
        tr("Ошибка на стороне провайдера", "An error on the provider's side"),
        tr("Обычно временно: повторите через минуту или выберите другую модель.",
           "Usually temporary: retry in a minute or pick another model.")),
    "empty_answer": lambda: (
        tr("Модель вернула пустой ответ", "The model returned an empty answer"),
        tr("Повторите; если повторяется — выберите другую модель.", "Retry; if it repeats, pick another model.")),
    "unknown": lambda: (
        tr("Запрос к модели не удался", "The model request failed"),
        tr("Текст ошибки провайдера — выше; журнал запросов MemuAI Proxy покажет подробности.",
           "The provider's text is above; the MemuAI Proxy request log has the details.")),
}

def info_of(exc: BaseException) -> ErrorInfo:
    """ErrorInfo of an exception (an LLMError carries status/kind; anything else is classified by its text)."""
    message = getattr(exc, "raw_message", None)
    if message is None:
        message = str(exc)
    return classify(int(getattr(exc, "status", 0) or 0), str(message), str(getattr(exc, "kind", "") or ""))

def describe(exc: BaseException, *, model: str = "", provider: str = "") -> str:
    """The error as the user reads it: what happened (plain words), the real upstream text with its status,
    provider and model, and what to do. Nothing is reworded away."""
    info = info_of(exc)
    raw = getattr(exc, "raw_message", None)
    raw = str(raw if raw is not None else exc).strip()
    status = int(getattr(exc, "status", 0) or 0)
    provider = provider or str(getattr(exc, "provider", "") or "")
    facts = [part for part in (f"HTTP {status}" if status else "", provider, model) if part]
    head = info.title + (f" ({' · '.join(facts)})" if facts else "")
    lines = [head]
    if raw and raw.lower() != info.title.lower():
        lines.append(tr("Ответ: ", "Upstream said: ") + raw[:1200])
    hint = str(getattr(exc, "hint", "") or "").strip()
    advice = info.advice
    if hint and hint not in advice and hint not in raw and info.category in ("unknown", "bad_request", "server"):
        advice = hint
    lines.append(tr("Что делать: ", "What to do: ") + advice)
    return "\n".join(lines)

def is_overflow(exc: BaseException) -> bool:
    """Would a smaller history cure this error? (context window, Proxy message count, body size)"""
    return info_of(exc).compactable

def provider_of(model: str, info: Optional[Any] = None) -> str:
    if info is not None and getattr(info, "provider", ""):
        return str(info.provider)
    return ""
