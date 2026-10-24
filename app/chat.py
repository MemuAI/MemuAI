"""Центральный чат-сервис: OpenAI-запрос → провайдер → OpenAI-ответ.

Роутинг по префиксам, ротация аккаунтов провайдера, генерация OpenAI-чанков.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
import re
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

from .config import config_store
from .pool import pool
from .metrics import metrics
from .providers.base import (
    ProviderAuthError,
    ProviderContentFilterError,
    ProviderError,
    ProviderQuotaError,
    ProviderModelUnavailableError,
    ProviderNetworkError,
    ProviderRateLimitError,
    ProviderServerError,
    approx_tokens,
    provider_error_ru,
    provider_error_texts,
    readable_provider_error,
    safe_provider_error,
)
from .routing import resolve
from .state import state

log = logging.getLogger("memuai.chat")

#: Longest upstream Retry-After the Proxy waits out itself before retrying; a longer pause
#: is passed to the client in Retry-After instead of holding the request open.
_RATE_LIMIT_WAIT_CAP = 10.0


class CompletionError(Exception):
    def __init__(self, message: str, status: int = 500, err_type: str = "internal_error",
                 *, hint: str = "", hint_en: str = "", message_en: str = "",
                 provider: str = "", retry_after: Optional[float] = None) -> None:
        readable = readable_provider_error(message)
        # Провайдеры пишут ошибки по-английски: русский текст переводит их фразы,
        # английский вариант без message_en — исходный текст.
        message = provider_error_ru(readable)
        super().__init__(message)
        self.message = message
        # Английский вариант того же сообщения: панель и клиенты с en-локалью
        # больше не получают русский текст.
        self.message_en = readable_provider_error(message_en) or readable
        self.status = status
        self.err_type = err_type
        # Кто отвечал (провайдер Proxy) — клиенты (MemuCode) показывают это рядом с ошибкой.
        self.provider = provider
        # Короткая подсказка «что делать» — панель показывает её рядом с ошибкой.
        default_hint = upstream_hint(readable) or error_hint(err_type, provider)
        self.hint = hint or default_hint[0]
        self.hint_en = hint_en or default_hint[1]
        # Пауза, которую назвал апстрим при 429: клиент получает её в Retry-After.
        self.retry_after = retry_after

    @property
    def headers(self) -> Optional[Dict[str, str]]:
        """HTTP headers for the error response (``Retry-After`` for a rate limit)."""
        if self.retry_after is None or self.status not in (429, 503):
            return None
        return {"Retry-After": str(max(1, math.ceil(self.retry_after)))}


# Человеческие подсказки для каждого класса ошибок (RU, EN).
ERROR_HINTS = {
    "authentication_error": (
        "Кука или токен аккаунта больше не действуют — обновите их в разделе «Аккаунты».",
        "The account cookie or token is no longer valid — refresh it under “Accounts”."),
    "quota_exceeded": (
        "Лимит аккаунта исчерпан. Добавьте ещё один аккаунт или дождитесь сброса.",
        "The account quota is used up. Add another account or wait for the reset."),
    "rate_limit_error": (
        "Провайдер временно ограничил частоту запросов. Обычно помогает второй аккаунт.",
        "The provider is throttling requests. A second account usually helps."),
    "network_error": (
        "Провайдер недоступен по сети: проверьте интернет, прокси и SSL в настройках.",
        "The provider is unreachable: check your internet, proxy and SSL settings."),
    "content_filter": (
        "Модель отказалась отвечать по фильтрам безопасности. Переформулируйте запрос — "
        "повтор на другом аккаунте не поможет.",
        "The model refused to answer due to safety filters. Rephrase the prompt — "
        "retrying on another account will not help."),
    "model_unavailable": (
        "Ни один аккаунт провайдера не имеет доступа к этой модели (тариф или регион).",
        "No account of this provider has access to this model (plan or region)."),
    "model_not_found": (
        "Проверьте список моделей: Настройки → Каталог моделей, либо GET /v1/models.",
        "Check the catalog: Settings → Model catalog, or GET /v1/models."),
    "service_unavailable": (
        "Нет доступных аккаунтов провайдера — добавьте или включите их в разделе «Аккаунты».",
        "No usable accounts for this provider — add or enable them under “Accounts”."),
    "upstream_error": (
        "Ошибка на стороне провайдера. Подробности — в журнале запросов MemuAI Proxy.",
        "The provider returned an error. See the MemuAI Proxy request log for details."),
    "proxy_disabled": (
        "Прокси выключен: нажмите «Запустить прокси» на главной.",
        "The proxy is off: press “Start proxy” on the Home tab."),
}

# У «Собственного API» нет куки и токена аккаунта — только API-ключ подключения.
CUSTOM_ERROR_HINTS = {
    "authentication_error": (
        "API-ключ подключения больше не принимается — проверьте его в разделе «Аккаунты».",
        "The connection API key is no longer accepted — check it under “Accounts”."),
    "service_unavailable": (
        "Нет включённого подключения «Собственный API» для этой модели — добавьте или включите его "
        "в разделе «Аккаунты».",
        "No enabled “Custom API” connection serves this model — add or enable it under “Accounts”."),
}


# Round 32: подсказки по собственным словам апстрима — точнее, чем по классу ошибки.
# «No available accounts» шлют шлюзы-перепродажи (OpenAI-совместимые) при 503/429: у
# них самих нет свободного аккаунта для модели. К размеру запроса это отношения не имеет.
_UPSTREAM_HINTS = (
    (re.compile(r"no (?:available|active|healthy) (?:accounts?|channels?|keys?)|no channel available", re.I), (
        "У сервиса по этому ключу сейчас нет свободных аккаунтов для модели (это его сторона, не размер "
        "запроса). Повторите позже или выберите другую модель; если повторяется всегда — модель у него недоступна.",
        "The service behind this key has no free account for the model right now (its side, not the size of "
        "the request). Retry later or pick another model; if it always happens, the model is not available there.")),
)


def upstream_hint(message: str) -> Optional[tuple]:
    for pattern, hint in _UPSTREAM_HINTS:
        if pattern.search(str(message or "")):
            return hint
    return None


def error_hint(err_type: str, provider: str = "") -> tuple:
    """Подсказка (RU, EN) для класса ошибки с учётом провайдера."""
    if provider == "custom" and err_type in CUSTOM_ERROR_HINTS:
        return CUSTOM_ERROR_HINTS[err_type]
    return ERROR_HINTS.get(err_type, ("", ""))


# Классы исключений транспорта, которые разные HTTP-библиотеки называют
# по-своему. Общего базового класса у них нет, поэтому сверяем имя: такая
# ошибка всегда временная, её нужно повторить, а не показывать пользователю
# как «неожиданную ошибку адаптера».
_NETWORK_EXC_NAMES = frozenset({
    "ConnectionError", "ConnectError", "ConnectTimeout", "ConnectionResetError",
    "ConnectionAbortedError", "ConnectionRefusedError", "ConnectTimeoutError",
    "ReadTimeout", "ReadTimeoutError", "ReadError", "WriteError",
    "RemoteProtocolError", "RemoteDisconnected", "ProtocolError",
    "IncompleteRead", "ChunkedEncodingError", "ProxyError", "SSLError",
    "Timeout", "TimeoutError", "TimeoutException", "PoolTimeout",
    "ClientConnectorError", "ServerDisconnectedError", "ClientPayloadError",
    "CurlError", "RequestsError", "SessionClosed", "DNSError",
})


def _classify_adapter_error(provider_id: str, exc: Exception) -> ProviderError:
    """Превращает «сырое» исключение адаптера в ошибку провайдера.

    Обрыв соединения — временная сетевая ошибка: запрос обязан уйти на
    повторную попытку. Всё остальное остаётся ошибкой адаптера.
    """
    name = type(exc).__name__
    if name in _NETWORK_EXC_NAMES or isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return ProviderNetworkError(
            f"{provider_id}: соединение с провайдером прервано ({name})")
    return ProviderServerError(f"Неожиданная ошибка адаптера ({name})")


async def _reset_transport(provider, account) -> None:
    """Выбрасывает keep-alive сессию, чтобы повтор пошёл по новому соединению."""
    try:
        from .providers import http as provider_http
        await provider_http.drop_session(provider.proxy_for(account))
    except Exception:
        pass


# 4xx-ответы, которые означают временное состояние апстрима, а не ошибку в
# самом запросе: их нужно повторить на другом аккаунте, а не отдавать клиенту.
_TRANSIENT_4XX = frozenset({408, 425, 429})


class _ServiceTagScrubber:
    """Drops provider service markup such as ``<ds_safety>…</ds_safety>`` from a stream.

    Some DeepSeek deployments append a safety verdict to the visible answer. Chunks split
    tags anywhere, so a possible tag start at the end of a chunk is held back until the
    next one decides it.
    """

    TAGS = ("ds_safety",)

    def __init__(self) -> None:
        self._pending = ""
        self._inside = ""

    def feed(self, text: str) -> str:
        buf = self._pending + text
        self._pending = ""
        out: List[str] = []
        while buf:
            if self._inside:
                close = f"</{self._inside}>"
                end = buf.find(close)
                if end < 0:
                    # Keep only what could still be the start of the closing tag.
                    self._pending = _partial_tail(buf, (close,))
                    return "".join(out)
                buf = buf[end + len(close):]
                self._inside = ""
                continue
            start = buf.find("<")
            if start < 0:
                out.append(buf)
                break
            out.append(buf[:start])
            buf = buf[start:]
            for tag in self.TAGS:
                opener = "<" + tag
                if buf.startswith(opener) and len(buf) > len(opener) and buf[len(opener)] in "> /":
                    gt = buf.find(">")
                    if gt < 0:
                        self._pending = buf
                        return "".join(out)
                    if buf[gt - 1] != "/":
                        self._inside = tag
                    buf = buf[gt + 1:]
                    break
            else:
                if any(("<" + tag).startswith(buf) for tag in self.TAGS):
                    self._pending = buf
                    break
                out.append("<")
                buf = buf[1:]
        return "".join(out)

    def flush(self) -> str:
        """Text held back at the end of the stream; an unclosed service block is dropped."""
        tail = "" if self._inside else self._pending
        self._pending = ""
        self._inside = ""
        return tail


def _partial_tail(text: str, needles: tuple) -> str:
    for size in range(min(len(text), max(map(len, needles)) - 1), 0, -1):
        if any(n.startswith(text[-size:]) for n in needles):
            return text[-size:]
    return ""


class _MidStreamAbort(Exception):
    def __init__(self, upstream: Exception) -> None:
        # The wrapper may surface in framework diagnostics.  Keep the raw
        # provider message only on ``upstream``, where the account-aware
        # redactor can process it before logging or returning it.
        super().__init__("provider stream aborted")
        self.upstream = upstream


def _missing_account_message(provider: Any, resolved: Any, req_model: str) -> tuple:
    """(RU, EN): какой аккаунт добавить, чтобы модель заработала."""
    title = getattr(provider, "title", "") or resolved.provider
    title_en = getattr(provider, "title_en", "") or title
    if resolved.provider == "custom" and resolved.prefix:
        return (f"Для модели «{req_model}» нужно подключение «{title}» с префиксом «{resolved.prefix}». "
                "Добавьте его в разделе «Аккаунты».",
                f"Model “{req_model}” needs a “{title_en}” connection with the prefix "
                f"“{resolved.prefix}”. Add it on the Accounts tab.")
    return (f"Для модели «{req_model}» нужен аккаунт {title}. Добавьте его в разделе «Аккаунты».",
            f"Model “{req_model}” needs a {title_en} account. Add one on the Accounts tab.")


class ChatService:
    async def stream_completion(self, req_model: str, messages: List[Dict[str, Any]],
                                params: Optional[Dict[str, Any]] = None,
                                trace: Optional[Any] = None) -> AsyncIterator[Dict[str, Any]]:
        params = params or {}
        settings = config_store.settings

        if trace is not None:
            trace.capture_prompt(messages, tools=params.get("tools"))

        if not settings.proxy_enabled:
            raise CompletionError("Прокси выключен в панели (Главная → «Запустить прокси»).",
                                  status=503, err_type="proxy_disabled",
                                  message_en="The proxy is switched off in the panel "
                                             "(Home → “Start proxy”).")

        # Round 40: a web model («<model>:web») runs a server-side web-search tool loop over its base model
        # (app/web_models.py); every endpoint (OpenAI, Anthropic, Responses, panel) gets it through here.
        from . import web_models
        if web_models.is_web(req_model):
            async for item in web_models.run(self, req_model, messages, params, trace=trace):
                yield item
            return

        # Round 35: a composite model (memu/smart …) runs its own rule pipeline over real models (app/composite_models.py).
        from . import composite_models
        if composite_models.is_composite(req_model):
            async for item in composite_models.run(self, req_model, messages, params, trace=trace):
                yield item
            return

        if trace is not None:
            trace.add_stage("request_parsed", {
                "request_id": trace.request_id, "model": req_model,
                "stream": params.get("_stream", None), "turns": len(messages),
                "has_system_instruction": any(m.get("role") in ("system", "developer") for m in messages),
                "web_search": params.get("web_search"),
                "temperature": params.get("temperature"), "max_tokens": params.get("max_tokens"),
            })

        resolved = resolve(req_model)
        if resolved is None:
            raise CompletionError(
                f"Модель '{req_model}' не маршрутизируется: нет такого префикса и нет однозначного совпадения. "
                "Посмотрите /v1/models.", status=404, err_type="model_not_found",
                message_en=f"Model '{req_model}' cannot be routed: no such prefix and no "
                           "unambiguous match. See /v1/models.")

        if trace is not None:
            # Без этого в журнале у каждой записи пустой провайдер: фильтр
            # «Провайдер» ничего не находит, и запрос выглядит непойманным.
            trace.provider = resolved.provider
            trace.add_stage("routed", {"provider": resolved.provider, "model": resolved.model,
                                       "prefix": resolved.prefix})
        provider = self._provider(resolved.provider)
        if provider is None:
            raise CompletionError(f"Провайдер '{resolved.provider}' недоступен",
                                  status=500, err_type="internal_error",
                                  message_en=f"Provider '{resolved.provider}' is unavailable")

        if resolved.entry is not None and not resolved.entry.enabled:
            raise CompletionError(f"Модель '{req_model}' отключена в панели.",
                                  status=404, err_type="model_disabled",
                                  message_en=f"Model '{req_model}' is disabled in the panel.")
        if resolved.entry is not None and resolved.entry.availability.get("available") is False:
            raise CompletionError(
                f"Модель '{req_model}' недоступна для подключённых аккаунтов. "
                + str(resolved.entry.availability.get("reason") or "Обновите список моделей в MemuAI Proxy."),
                status=404, err_type="model_unavailable",
                message_en=f"Model '{req_model}' is unavailable for the connected accounts. "
                           "Refresh the model catalogue in MemuAI Proxy or check the upstream plan and client.")
        if resolved.entry is None and settings.strict_models:
            raise CompletionError(
                f"Модель '{req_model}' отсутствует в каталоге (включён строгий режим).",
                status=404, err_type="model_not_found",
                message_en=f"Model '{req_model}' is missing from the catalog (strict mode is on).")

        from .music_runtime import is_music_model
        if is_music_model(req_model, resolved=resolved):
            raise CompletionError("Эта модель создаёт музыку. Выберите её в Studio → Музыка или используйте /v1/audio/music.",
                                  status=400, err_type="unsupported_modality",
                                  message_en="This model generates music. Use Studio → Music or /v1/audio/music.")

        # Веб-поиск. Режим провайдера из панели важнее флага запроса: «always»
        # включает поиск и для сторонних клиентов на /v1, которые про него не
        # знают, «off» запрещает его даже по явному запросу.
        search_mode = str(config_store.provider_cfg(resolved.provider)
                          .get("web_search", "auto") or "auto").lower()
        wants_search = bool(params.get("web_search"))
        if search_mode == "always":
            wants_search = True
        elif search_mode == "off":
            wants_search = False
        params = dict(params)
        params["web_search"] = wants_search
        # Round 33 (r33-thinking): the requested reasoning level → the nearest one this model really
        # supports; providers read params["_reasoning_level"] / ["_reasoning"] (app/reasoning_levels.py).
        try:
            from . import reasoning_levels
            reasoning_levels.for_request(getattr(resolved.entry, "id", None) or req_model,
                                         resolved.provider, resolved.model, params)
        except Exception as exc:   # never let a capability table break a chat
            log.debug("reasoning levels: %s", exc)
        # Клиент с инструментами (MemuAI) ищет сам: модель решает, нужен ли
        # поиск, и формулирует собственный запрос. Подмешивать выдачу к каждому
        # сообщению нельзя — «привет» не должен уходить в поисковик.
        if params.get("tools_available"):
            wants_search = False
        if wants_search and not getattr(provider, "native_search", False):
            from . import websearch
            found: Dict[str, Any] = {}
            messages = await websearch.augment(
                messages, limit=int(settings.search_results),
                proxy=settings.default_proxy, trace=trace, sink=found)
            if found.get("sources"):
                # Отдельный чанк для клиента: чат показывает источники шагом
                # дорожки, не подмешивая их в текст ответа.
                yield {"object": "memuai.event", "event": "web_search", "web_search": found}

        candidates = pool.ordered_candidates(resolved.provider, resolved.model,
                                             prefix=resolved.prefix)
        if resolved.provider == "custom":
            candidates = [candidate for candidate in candidates
                          if str(candidate.fields.get("name", "")).strip().lower().replace(" ", "-")
                          == resolved.prefix.lower()]
        # приоритет аккаунту, к которому привязана запись каталога
        if resolved.entry and resolved.entry.account_id:
            pinned = state.get_account(resolved.entry.account_id)
            if pinned and pinned.usable():
                candidates = [pinned] + [c for c in candidates if c.id != pinned.id]
        account_allows = getattr(provider, "account_model_available", None)
        if candidates and callable(account_allows):
            candidates = [account for account in candidates if account_allows(account, resolved.model)]
            if not candidates:
                raise CompletionError(
                    f"Модель '{req_model}' отсутствует в актуальном списке доступных моделей этих аккаунтов.",
                    status=404, err_type="model_unavailable",
                    message_en=f"Model '{req_model}' is absent from these accounts' current available-model selector.")
        if not candidates:
            blocked = [a for a in state.usable_accounts(resolved.provider)
                       if pool.model_blocked(a, resolved.model)]
            if blocked:
                # Причина, которую назвал сам провайдер («проверьте аккаунт», «нет в
                # тарифе»), полезнее общей фразы; блокировка сама снимется за час.
                why = pool.block_reason(blocked, resolved.model)
                raise CompletionError(
                    f"Модель «{resolved.model}» недоступна ни одному аккаунту «{resolved.provider}»: "
                    + (why or "тариф аккаунтов её не включает."),
                    status=404, err_type="model_unavailable",
                    message_en=f"Model “{resolved.model}” is not available to any "
                               f"“{resolved.provider}” account: "
                               + (why or "their plans do not include it."))
            # Accounts exist but are switched off or were rejected (a wrong key): say which and
            # why. «Добавьте аккаунты» hid the real cause after the first failed request.
            idle = [a for a in state.accounts_of(resolved.provider)
                    if not a.usable() and (resolved.provider != "custom" or not resolved.prefix
                                           or str(a.fields.get("name", "")).strip().lower()
                                           .replace(" ", "-") == resolved.prefix.lower())]
            if idle:
                account = idle[0]
                name = account.label or account.id
                why = safe_provider_error(account.status_message or account.stats.get("last_error") or "", account)
                if not account.enabled:
                    ru, en = f"Аккаунт «{name}» выключен.", f"Account “{name}” is turned off."
                else:
                    # Round 31: say what to do next, and never leak Russian into message_en.
                    ru = (f"Аккаунт «{name}» отклонён провайдером" + (f": {why}." if why else ".")
                          + " Проверьте ключ или вход на вкладке «Аккаунты» MemuAI Proxy.")
                    why_en = why if why and not re.search(r"[А-Яа-яЁё]", why) else (
                        "the key was rejected" if re.search(r"ключ отклон", why or "") else "")
                    en = (f"Account “{name}” was rejected by the provider" + (f": {why_en}." if why_en else ".")
                          + " Check its key or sign-in on the Accounts tab of MemuAI Proxy.")
                raise CompletionError(ru, status=503, err_type="authentication_error"
                                      if account.enabled else "service_unavailable", message_en=en,
                                      provider=resolved.provider)
            ru, en = _missing_account_message(provider, resolved, req_model)
            raise CompletionError(ru, status=503, err_type="service_unavailable",
                                  message_en=en, provider=resolved.provider)

        last_error: Optional[Exception] = None
        last_error_message = ""
        last_error_message_ru = ""
        last_account = ""
        model_unavailable = 0
        passes = max(1, int(settings.max_retry_passes))
        if len(candidates) == 1:
            # С единственным аккаунтом один проход означает «упал — и всё».
            # Временная сетевая ошибка должна получить хотя бы один повтор.
            passes = max(passes, 2)
        delay = max(0.0, settings.retry_delay_ms / 1000.0)
        started_at = time.time()
        deadline = started_at + max(30, settings.request_timeout) * passes

        last_usage: Dict[str, Any] = {}

        def finish(ok: bool, err_type: str = "") -> None:
            # Токены считаются здесь, для всех путей (/v1/chat, /v1/messages,
            # /v1/responses, панель), а не только для /v1/chat с журналом.
            if settings.metrics_enabled:
                usage = last_usage if ok else {}
                cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") \
                    if isinstance(usage.get("prompt_tokens_details"), dict) else 0
                reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") \
                    if isinstance(usage.get("completion_tokens_details"), dict) else 0
                billed = usage.get("cost")
                metrics.record(provider=resolved.provider, model=req_model, ok=ok,
                               ms=int((time.time() - started_at) * 1000),
                               prompt_tokens=int(usage.get("prompt_tokens") or 0),
                               completion_tokens=int(usage.get("completion_tokens") or 0),
                               cache_read_tokens=int(cached or 0), reasoning_tokens=int(reasoning or 0),
                               cost_usd=float(billed) if isinstance(billed, (int, float)) and billed >= 0 else None,
                               estimated=bool(usage.get("estimated")) if ok else False,
                               error_code=err_type)

        # Временный таймаут или пустой ответ не должны рвать длинный диалог:
        # перебираем аккаунты и делаем несколько коротких проходов. Ошибки
        # валидации, отказ модели и «нет такой модели у аккаунта» не повторяются
        # вслепую и не портят репутацию аккаунта.
        dead: set = set()
        # Когда аккаунт снова примет запрос, если апстрим назвал паузу (Retry-After).
        ready_at: Dict[str, float] = {}
        for retry_pass in range(passes):
            for account in candidates:
                if time.time() > deadline:
                    break
                if retry_pass and (account.id in dead or not account.usable()):
                    # Аккаунт уже отказал по лимиту или авторизации в этом же
                    # запросе: повтор ничего не даст, только исказит итог.
                    continue
                if ready_at.get(account.id, 0.0) > time.time() + 0.05:
                    # Апстрим просил подождать дольше: повтор раньше срока — снова 429.
                    continue
                attempt_started = time.time()
                attempt = self._attempt(provider, account, resolved.model, req_model,
                                        messages, params, trace=trace)
                try:
                    if trace is not None:
                        trace.account = account.label or account.id
                        trace.add_stage("sending_to_provider", {
                            "provider": resolved.provider, "model": resolved.model,
                            "account": account.label or account.id,
                            "attempt": f"{retry_pass + 1}/{passes}"})
                    async for chunk in attempt:
                        if isinstance(chunk, dict) and isinstance(chunk.get("usage"), dict):
                            try:
                                from . import api_budget
                                api_budget.apply_usage_price(account, req_model, chunk["usage"], raw_model=resolved.model)
                                # _attempt emits usage exactly once, after the upstream
                                # completed. Persist before yielding: a client can close
                                # the iterator immediately after reading its final usage.
                                api_budget.record_usage(account, req_model, chunk["usage"], raw_model=resolved.model)
                            except Exception:
                                log.exception("API budget accounting failed")
                            last_usage = chunk["usage"]
                        yield chunk
                    pool.report_success(account,
                                        ms=int((time.time() - attempt_started) * 1000))
                    finish(True)
                    return
                except ProviderContentFilterError as e:
                    public_error_ru, public_error = provider_error_texts(e, account)
                    pool.report_failure(account, e, model=resolved.model)
                    if trace is not None:
                        trace.error_code = "content_filter"
                        trace.add_stage("content_filtered", {"account": account.label or account.id,
                                                             "message": public_error})
                    finish(False, "content_filter")
                    raise CompletionError(public_error_ru, status=400, err_type="content_filter",
                                          message_en=public_error) from e
                except ProviderModelUnavailableError as e:
                    model_unavailable += 1
                    # Детерминированный отказ для пары «аккаунт + модель»: на
                    # следующем проходе его не повторяем. Иначе счётчик рос на
                    # каждом проходе, и при 2 аккаунтах (один без модели, у
                    # другого сбой сети) запрос ложно завершался «модель не
                    # входит в тариф ни одного аккаунта».
                    dead.add(account.id)
                    pool.report_failure(account, e, model=resolved.model)
                    last_error = e
                    last_error_message_ru, last_error_message = provider_error_texts(e, account)
                    last_account = account.label or account.id
                    log.info("%s/%s: model unavailable - moving to the next account",
                             resolved.provider, last_account)
                    continue
                except ProviderError as e:
                    public_error_ru, public_error = provider_error_texts(e, account)
                    if (isinstance(e, ProviderServerError) and 400 <= _status_of(e) < 500
                            and _status_of(e) not in _TRANSIENT_4XX):
                        # Ошибка валидации запроса одинакова для всех аккаунтов.
                        finish(False, _err_type_of(e))
                        if _status_of(e) == 404:
                            # Almost always a model id the provider does not know (or a wrong
                            # API address): name the model instead of a bare «HTTP 404».
                            raise CompletionError(
                                f"Провайдер не нашёл модель «{resolved.model}» (или неверный адрес API): "
                                f"{public_error_ru}", status=404, err_type="model_not_found",
                                message_en=f"The provider does not know model “{resolved.model}” "
                                           f"(or the API address is wrong): {public_error}") from e
                        raise CompletionError(public_error_ru, status=_status_of(e),
                                              err_type=_err_type_of(e), message_en=public_error,
                                              provider=resolved.provider) from e
                    pool.report_failure(account, e, model=resolved.model,
                                        ms=int((time.time() - attempt_started) * 1000))
                    if isinstance(e, (ProviderAuthError, ProviderQuotaError)):
                        dead.add(account.id)
                    if isinstance(e, ProviderRateLimitError) and e.retry_after is not None:
                        ready_at[account.id] = time.time() + e.retry_after
                    if isinstance(e, ProviderNetworkError):
                        await _reset_transport(provider, account)
                    last_error, last_error_message, last_error_message_ru = e, public_error, public_error_ru
                    last_account = account.label or account.id
                    log.warning("%s/%s (pass %d/%d): %s", resolved.provider,
                                last_account, retry_pass + 1, passes, public_error)
                    if trace is not None:
                        trace.add_stage("retry", {
                            "account": last_account, "reason": public_error,
                            "attempt": f"{retry_pass + 1}/{passes}"})
                    continue
                except _MidStreamAbort as e:
                    upstream = e.upstream
                    pool.report_failure(account, upstream, model=resolved.model)
                    public_error_ru, public_error = provider_error_texts(upstream, account)
                    finish(False, _err_type_of(upstream))
                    raise CompletionError(
                        f"Поток провайдера оборвался после начала ответа: {public_error_ru}",
                        status=_status_of(upstream),
                        err_type=_err_type_of(upstream),
                        message_en="The provider stream broke off after the answer had "
                                   f"started: {public_error}",
                    ) from e
                finally:
                    from .providers import http as _http
                    await _http.close_quietly(attempt.aclose)
            if retry_pass < passes - 1:
                wait = delay * (retry_pass + 1)
                pending = [ready_at.get(account.id) for account in candidates
                           if account.id not in dead and account.usable()]
                if not pending:
                    # Every account refused for good (key, quota): another pass would only wait.
                    break
                if all(at is not None for at in pending):
                    # Every remaining account named its pause: wait for the earliest one, but
                    # never hang for long — a longer pause goes to the client as Retry-After.
                    until = min(pending) - time.time()
                    if until > _RATE_LIMIT_WAIT_CAP or time.time() + until > deadline:
                        break
                    wait = max(wait, until)
                if wait:
                    await asyncio.sleep(wait)

        if model_unavailable and model_unavailable >= len(candidates):
            finish(False, "model_unavailable")
            why = f" {last_error_message}" if last_error_message else ""
            why_ru = f" {last_error_message_ru}" if last_error_message_ru else ""
            raise CompletionError(
                f"Модель «{resolved.model}» не входит в тариф ни одного аккаунта "
                f"«{resolved.provider}».{why_ru}", status=404, err_type="model_unavailable",
                message_en=f"Model “{resolved.model}” is not part of any "
                           f"“{resolved.provider}” account plan.{why}")
        detail = f": {last_error_message}" if last_error else ""
        detail_ru = f": {last_error_message_ru}" if last_error else ""
        status = _status_of(last_error) if last_error else 502
        if status in (401, 402, 403):
            # Отказ ВЫШЕСТОЯЩЕГО аккаунта. 401 клиенту означал бы «неверный
            # ключ Proxy», и SDK OpenAI/Anthropic советовали бы сменить ключ.
            status = 503
        if last_error:
            err_type = _err_type_of(last_error)
            finish(False, err_type)
            retry_after = None
            if isinstance(last_error, ProviderRateLimitError) and ready_at:
                retry_after = max(0.0, min(ready_at.values()) - time.time())
            raise CompletionError(
                f"Все аккаунты «{resolved.provider}» недоступны ({last_account}){detail_ru}",
                status=status if status >= 400 else 502, err_type=err_type,
                message_en=f"Every “{resolved.provider}” account failed "
                           f"({last_account}){detail}", provider=resolved.provider,
                retry_after=retry_after)
        finish(False, "service_unavailable")
        raise CompletionError(f"Нет доступных аккаунтов «{resolved.provider}».",
                              status=503, err_type="service_unavailable",
                              message_en=f"No usable “{resolved.provider}” accounts.",
                              provider=resolved.provider)

    def _provider(self, provider_id: str):
        from .providers import get
        p = get(provider_id)
        return p

    async def _attempt(self, provider, account, raw_model: str, public_model: str,
                       messages: List[Dict[str, Any]], params: Dict[str, Any],
                       trace: Optional[Any] = None) -> AsyncIterator[Dict[str, Any]]:
        response_id = "chatcmpl-" + uuid.uuid4().hex[:24]
        created = int(time.time())

        def chunk(delta: Dict[str, Any]) -> Dict[str, Any]:
            return {"id": response_id, "object": "chat.completion.chunk", "created": created,
                    "model": public_model, "system_fingerprint": "memuai",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}

        text_parts: List[str] = []
        got_output = False
        got_tool_call = False
        tool_parts: List[str] = []
        finish_reason = "stop"
        real_usage: Optional[Dict[str, int]] = None
        first_ev = True
        provider_stream = None
        think_open = False
        text_scrub, reasoning_scrub = _ServiceTagScrubber(), _ServiceTagScrubber()
        first_byte_timeout = max(5, int(config_store.settings.first_byte_timeout))
        try:
            provider_stream = provider.chat_stream(account, raw_model, messages, params)
            stream_iter = provider_stream.__aiter__()
            while True:
                try:
                    if first_ev:
                        # Отдельный лимит на первый байт: зависший апстрим
                        # освобождает очередь заметно раньше общего таймаута.
                        ev = await asyncio.wait_for(stream_iter.__anext__(),
                                                    timeout=first_byte_timeout)
                    else:
                        ev = await stream_iter.__anext__()
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError as exc:
                    from .providers.base import ProviderNetworkError
                    # Round 34: the English text (API clients, error_en) was Russian too.
                    raise ProviderNetworkError(
                        f"{provider.id}: the provider did not start answering within {first_byte_timeout} s",
                        message_ru=f"{provider.id}: провайдер не начал отвечать за {first_byte_timeout} с") from exc
                if first_ev:
                    first_ev = False
                    if trace is not None:
                        trace.add_stage("provider_responded", {"provider": provider.id})
                        trace.add_stage("converting_to_openai", {})
                if trace is not None and ev.get("type") == "reasoning":
                    pass
                if ev.get("type") == "text" and ev.get("text"):
                    text = text_scrub.feed(ev["text"])
                    if not text:
                        continue
                    got_output = True
                    text_parts.append(text)
                    if think_open:
                        think_open = False
                        yield chunk({"content": "</think>\n\n" + text})
                    else:
                        yield chunk({"content": text})
                elif ev.get("type") == "reasoning" and ev.get("text"):
                    text = reasoning_scrub.feed(ev["text"])
                    if not text:
                        continue
                    mode = config_store.provider_cfg(provider.id).get("reasoning_output", "reasoning_content")
                    if mode == "reasoning_content":
                        got_output = True
                        yield chunk({"reasoning_content": text})
                    elif mode == "think_tags":
                        # Рассуждение в тегах <think>, иначе оно смешивается с ответом.
                        prefix = "" if think_open else "<think>\n"
                        think_open = True
                        yield chunk({"content": prefix + text})
                        got_output = True
                elif ev.get("type") == "finish" and ev.get("reason"):
                    finish_reason = str(ev["reason"])
                elif ev.get("type") == "usage":
                    # Счёт токенов самого провайдера: он точнее оценки по длине текста.
                    counted = {key: int(ev.get(key) or 0) for key in ("prompt_tokens", "completion_tokens")}
                    if "reasoning_tokens" in ev:
                        counted["reasoning_tokens"] = max(0, int(ev["reasoning_tokens"] or 0))
                    if int(ev.get("cached_tokens") or 0) > 0:
                        counted["cached_tokens"] = int(ev["cached_tokens"])
                    if int(ev.get("cache_write_tokens") or 0) > 0:
                        counted["cache_write_tokens"] = int(ev["cache_write_tokens"])
                    if isinstance(ev.get("cost"), (int, float)) and ev["cost"] >= 0:
                        counted["cost"] = float(ev["cost"])
                    if counted["prompt_tokens"] > 0:
                        real_usage = counted
                elif ev.get("type") == "tool_call":
                    got_output = got_tool_call = True
                    tool_delta: Dict[str, Any] = {"index": ev.get("index", 0)}
                    if ev.get("id") is not None:
                        tool_delta["id"] = ev["id"]
                        tool_delta["type"] = "function"
                    function = {}
                    if ev.get("name") is not None:
                        function["name"] = ev["name"]
                    if ev.get("arguments") is not None:
                        function["arguments"] = ev["arguments"]
                        tool_parts.append(str(ev["arguments"]))
                    if function:
                        tool_delta["function"] = function
                    yield chunk({"tool_calls": [tool_delta]})
        except ProviderError as e:
            if got_output:
                raise _MidStreamAbort(e)
            raise
        except Exception as e:
            # Provider adapters are a protocol boundary.  Normalize parser and
            # transport surprises so a failure before output can use the next
            # account, while a failure after output remains a terminal stream
            # abort.  asyncio cancellation is a BaseException and is therefore
            # deliberately not swallowed here.
            unexpected = _classify_adapter_error(provider.id, e)
            if got_output:
                raise _MidStreamAbort(unexpected) from e
            raise unexpected from e
        else:
            tail = text_scrub.flush()
            if tail:
                got_output = True
                text_parts.append(tail)
                yield chunk({"content": ("</think>\n\n" if think_open else "") + tail})
                think_open = False
            if think_open:
                yield chunk({"content": "\n</think>"})
        finally:
            close = getattr(provider_stream, "aclose", None) if provider_stream is not None else None
            if close is not None:
                from .providers import http as _http
                await _http.close_quietly(close)

        full_text = "".join(text_parts)
        if not full_text.strip() and not got_tool_call:
            empty = ProviderServerError(
                f"{provider.id}: провайдер завершил поток без текста. Чаще всего это "
                "отказ модели по фильтрам безопасности или обрыв на стороне сервиса.")
            if got_output:
                raise _MidStreamAbort(empty)
            raise empty

        if trace is not None:
            trace.add_stage("finished", {
                "finish_reason": finish_reason,
                "output_text_chars": len(full_text),
                "account": account.label or account.id,
            })

        # ``estimated`` говорит клиенту, чьи это числа: провайдера или оценка Proxy по длине текста.
        if real_usage is not None:
            pt = real_usage["prompt_tokens"]
            ct = real_usage["completion_tokens"] or _completion_tokens(full_text, tool_parts, public_model)
        else:
            pt = _prompt_tokens(messages, public_model)
            ct = _completion_tokens(full_text, tool_parts, public_model)
        if got_tool_call and finish_reason == "stop":
            finish_reason = "tool_calls"
        yield {
            "id": response_id, "object": "chat.completion.chunk", "created": created,
            "model": public_model, "system_fingerprint": "memuai",
            "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct,
                      "estimated": real_usage is None,
                      **({"completion_tokens_details": {"reasoning_tokens": real_usage["reasoning_tokens"]}}
                         if real_usage is not None and "reasoning_tokens" in real_usage else {}),
                      **({"prompt_tokens_details": {key: real_usage[key] for key in ("cached_tokens", "cache_write_tokens")
                                                    if key in real_usage}}
                         if real_usage is not None and any(key in real_usage for key in ("cached_tokens", "cache_write_tokens")) else {}),
                      **({"cost": real_usage["cost"]} if real_usage is not None and "cost" in real_usage else {})},
        }


def _completion_tokens(text: str, tool_parts: List[str], model: str = "") -> int:
    """Оценка ответа: текст и аргументы вызовов инструментов (с учётом языка, app/token_estimate.py)."""
    return (approx_tokens(text, model) if text else 0) + (approx_tokens("".join(tool_parts), model) if tool_parts else 0)


def _prompt_tokens(messages: List[Dict[str, Any]], model: str = "") -> int:
    """Оценка токенов запроса без base64: картинка в 1 МБ давала ~330k токенов."""
    from .providers.base import content_to_text
    total, images = 0, 0
    for message in messages or []:
        total += approx_tokens(content_to_text(message.get("content")), model) + 4
        content = message.get("content")
        if isinstance(content, list):
            images += sum(1 for part in content if isinstance(part, dict)
                          and part.get("type") in ("image_url", "input_image", "image"))
        for call in message.get("tool_calls") or []:
            if isinstance(call, dict):
                total += approx_tokens(json.dumps(call.get("function") or {}, ensure_ascii=False), model)
    return total + images * 1000


def _err_type_of(exc: Exception) -> str:
    from .providers.base import (
        ProviderAuthError, ProviderNetworkError, ProviderQuotaError,
        ProviderRateLimitError, ProviderServerError,
    )
    if isinstance(exc, ProviderContentFilterError):
        return "content_filter"
    if isinstance(exc, ProviderModelUnavailableError):
        return "model_unavailable"
    if isinstance(exc, ProviderAuthError):
        return "authentication_error"
    if isinstance(exc, ProviderQuotaError):
        return "quota_exceeded"
    if isinstance(exc, ProviderRateLimitError):
        return "rate_limit_error"
    if isinstance(exc, ProviderNetworkError):
        return "network_error"
    if isinstance(exc, ProviderServerError):
        return "upstream_error"
    return "internal_error"


def _status_of(exc: Exception, default: int = 502) -> int:
    try:
        status = int(getattr(exc, "status", default))
    except (TypeError, ValueError):
        return default
    return status if 400 <= status <= 599 else default


chat_service = ChatService()
