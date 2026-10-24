"""Нормализация SSE-потока Genspark в события чата.

Genspark отдаёт строки `data: {json}`; полезный текст приходит в полях
`message_field_delta.field_name == "content"` (бэкенд agent/ai_chat) либо
`session_state.answer` / `streaming_detail_answer` (бэкенд copilot).
Мышление — в `answerthink`-полях. Ошибки (rate limit, not login, Cloudflare)
приходят сырыми строками или JSON со статусом != 0.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field as dc_field
from typing import Any, AsyncIterator, Dict, Optional, Set

from .client import (
    GensparkAuthError,
    GensparkCloudflareError,
    GensparkContentFilterError,
    GensparkFreeLimitError,
    GensparkModelError,
    GensparkRateLimitError,
    GensparkServerError,
    looks_like_cloudflare,
)

log = logging.getLogger("g2a.stream")

#: A "delta" that repeats at least this much already streamed text is treated
#: as a cumulative snapshot. Shorter repeats are legitimate Markdown ("-", "**").
_SNAPSHOT_MIN = 16

# Поля с полезным текстом зависят от бэкенда: у agent (ai_chat) это «content»,
# у copilot — session_state.*. Смешивание наборов приводит к дублированию ответа:
# в стриме встречаются и дельты, и кумулятивные снимки полного текста.
BACKEND_TEXT_FIELDS: Dict[str, Set[str]] = {
    "agent": {"content"},
    "copilot": {"session_state.answer", "session_state.streaming_detail_answer"},
}
THINK_DELTA_FIELDS: Set[str] = {
    "session_state.answerthink", "thinking", "reasoning", "reasoning_content", "session_state.thinking",
}
THINK_START_FIELDS: Set[str] = {"session_state.answerthink_is_started", "thinking_is_started"}
THINK_END_FIELDS: Set[str] = {"session_state.answerthink_is_finished", "thinking_is_finished"}
IGNORED_FIELDS_HINTS = ("markmap", "followup", "recommend", "status_bar", "search_result")

RATE_LIMIT_MARKERS = ("Rate limit exceeded cf1", "Rate limit exceeded cf2")
FREE_LIMIT_MARKERS = (
    "You've reached your free usage limit today",
    '"consume_usage_quota_exceeded": true',
    '"consume_usage_quota_exceeded":true',
)
NOT_LOGIN_MARKERS = ('"message":"not login"', '"message": "not login"', '"status":-5')

# Событийные типы, которыми Genspark сообщает о сбое генерации. Раньше они
# игнорировались, поэтому после отказа модели поток просто «замолкал» и запрос
# падал только по idle_timeout (несколько минут) с невнятной ошибкой.
ERROR_EVENT_TYPES = {
    "error", "message_error", "project_error", "task_error", "agent_error",
    "stream_error", "message_failed", "project_failed", "generate_error",
}
ERROR_FIELD_HINTS = ("error_message", "error_detail", "failed_reason",
                     "failure_reason", "error_code", "block_reason")

# Отказ модели по фильтрам безопасности. Повтор на другом аккаунте не поможет.
CONTENT_FILTER_MARKERS = (
    "safety filter", "safety filters", "safety_filter", "safety settings",
    "blocked by safety", "content policy", "content_policy", "usage policies",
    "prohibited_content", "prohibited content", "block_reason",
    "blockreason", "recitation", "response was blocked",
    "i can't help with that", "i cannot help with that",
    "нарушает политик", "фильтр безопасности", "фильтры безопасности",
    "не могу помочь с этим запросом",
)
MODEL_ERROR_MARKERS = (
    "model not found", "model_not_found", "unsupported model",
    "invalid model", "model is not available", "model unavailable",
    "feature has been retired", "model has been deprecated",
)


def _stringify(value: Any, limit: int = 2000) -> str:
    if isinstance(value, str):
        return value[:limit]
    try:
        return json.dumps(value, ensure_ascii=False)[:limit]
    except Exception:
        return str(value)[:limit]


def _error_text_of(event: Dict[str, Any]) -> str:
    """Собирает человекочитаемое описание сбоя из события апстрима."""
    parts = []
    for key in ("message", "error", "error_message", "detail", "reason",
                "failed_reason", "failure_reason", "error_code", "block_reason",
                "content", "field_value", "delta"):
        value = event.get(key)
        if value in (None, "", {}, []):
            continue
        text = _stringify(value, 600)
        if text and text not in parts:
            parts.append(text)
    data = event.get("data")
    if isinstance(data, dict):
        nested = _error_text_of(data)
        if nested and nested not in parts:
            parts.append(nested)
    return " | ".join(parts)[:1200]


def _raise_for_failure(text: str, *, status: int = 0) -> None:
    """Бросает точный тип ошибки по тексту сбоя апстрима."""
    low = (text or "").lower()
    if not low:
        raise GensparkServerError("Genspark reported a failure without details", status)
    if any(m in low for m in CONTENT_FILTER_MARKERS):
        raise GensparkContentFilterError(
            f"The upstream model refused the request (safety filter): {text}", 451)
    if any(m in low for m in MODEL_ERROR_MARKERS):
        raise GensparkModelError(f"Model unavailable on Genspark: {text}", 404)
    if any(m in low for m in ("rate limit", "too many requests", "429")):
        raise GensparkRateLimitError(f"Genspark rate limit: {text}", 429)
    if "not login" in low or "unauthor" in low or "401" in low:
        raise GensparkAuthError(f"Genspark cookie rejected: {text}", 401)
    if "quota" in low or "usage limit" in low:
        raise GensparkFreeLimitError(f"Genspark daily limit reached: {text}", 402)
    raise GensparkServerError(f"Genspark error: {text}", status)


@dataclass
class UpstreamEvent:
    kind: str                       # text | reasoning_start | reasoning_delta | reasoning_end | project | result | error
    text: str = ""
    project_id: str = ""
    data: Dict[str, Any] = dc_field(default_factory=dict)


def _is_not_login(line: str) -> bool:
    return any(m in line for m in NOT_LOGIN_MARKERS)


def _extract_project_id(event: Dict[str, Any]) -> str:
    for key in ("id", "project_id", "projectId", "message_id"):
        v = event.get(key)
        if isinstance(v, str) and v:
            return v
    return ""


def _extract_final_text(event: Dict[str, Any]) -> str:
    content = event.get("content")
    if isinstance(content, str) and content.lstrip().startswith("{"):
        try:
            inner = json.loads(content)
            if isinstance(inner, dict) and isinstance(inner.get("detailAnswer"), str):
                return inner["detailAnswer"]
        except Exception:
            pass
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return str(content.get("detailAnswer") or content.get("text") or "")
    for key in ("answer", "text", "delta"):
        v = event.get(key)
        if isinstance(v, str):
            return v
    return ""


def _parse_event(line: str, text_fields: Set[str]) -> Optional[UpstreamEvent]:
    """JSON-строка SSE → событие. None — событие игнорируем."""
    try:
        event = json.loads(line)
    except Exception:
        return None
    if not isinstance(event, dict):
        return None

    etype = event.get("type", "")

    if etype in ERROR_EVENT_TYPES:
        return UpstreamEvent("error", text=_error_text_of(event), data=event)

    if etype == "project_start":
        return UpstreamEvent("project", project_id=_extract_project_id(event))

    if etype in ("message_field", "message_field_delta"):
        field_name = event.get("field_name", "")
        delta = event.get("delta")
        if delta is None:
            delta = event.get("field_value", "")
        delta = delta if isinstance(delta, str) else ""

        if field_name in text_fields:
            return UpstreamEvent("text", text=delta, data=event)
        if field_name in THINK_DELTA_FIELDS:
            return UpstreamEvent("reasoning_delta", text=delta, data=event)
        if field_name in THINK_START_FIELDS:
            return UpstreamEvent("reasoning_start", data=event)
        if field_name in THINK_END_FIELDS:
            return UpstreamEvent("reasoning_end", data=event)
        if any(h in field_name for h in ERROR_FIELD_HINTS):
            return UpstreamEvent("error", text=delta or _error_text_of(event), data=event)
        if any(h in field_name for h in IGNORED_FIELDS_HINTS):
            return None
        if field_name:
            log.debug("unknown upstream field: %s", field_name)
        return None

    if etype in ("message_result", "message_completed", "result"):
        final = _extract_final_text(event)
        state_flag = str(event.get("status") or event.get("state") or "").lower()
        if state_flag in ("failed", "error", "cancelled", "canceled") or event.get("error"):
            return UpstreamEvent("error", text=_error_text_of(event) or final, data=event)
        return UpstreamEvent("result", text=final, data=event)

    # прочие типы (steps, search, files...) игнорируем
    log.debug("ignoring upstream event: %s", etype or "?")
    return None


async def iter_upstream_events(resp: Any, *, idle_timeout: int = 300,
                               backend: str = "agent",
                               first_event_timeout: int = 120) -> AsyncIterator[UpstreamEvent]:
    """Читает SSE-ответ Genspark, проверяет маркеры ошибок, выдаёт нормализованные события.

    При ошибке апстрима бросает соответствующее Genspark* исключение.
    Защита от дублей: апстрим может прислать и инкрементальные дельты, и
    кумулятивный снимок накопленного текста (в том же или другом поле) —
    снимок, продолжающий накопленный текст, не отправляется повторно.
    """
    status = resp.status_code
    # HTTP-статус: 401/403/429/5xx
    if status != 200:
        # при stream=True тело нужно вычитать
        head = ""
        try:
            async for chunk in resp.aiter_content():
                head += chunk.decode("utf-8", errors="replace") if isinstance(chunk, bytes) else str(chunk)
                if len(head) > 2000:
                    break
        except Exception:
            pass
        finally:
            try:
                await resp.aclose()
            except Exception:
                pass
        if looks_like_cloudflare(head):
            raise GensparkCloudflareError(f"Cloudflare is blocking the request (HTTP {status})", status)
        err = None
        if status in (401, 403):
            err = GensparkAuthError(f"The cookie was rejected (HTTP {status})", status)
        elif status == 429:
            err = GensparkRateLimitError("Rate limit (HTTP 429)", status)
        elif status >= 500:
            err = GensparkServerError(f"Ошибка Genspark (HTTP {status})", status)
        else:
            err = GensparkServerError(f"Unexpected response from Genspark (HTTP {status})", status)
        # тело может содержать JSON с message
        try:
            j = json.loads(head)
            msg = j.get("message") or j.get("detail")
            if msg and isinstance(msg, str):
                err.message = f"{err.message}: {msg}"
        except Exception:
            pass
        raise err

    lines = resp.aiter_lines()
    saw_any_event = False
    saw_text = False
    result_event: Optional[UpstreamEvent] = None
    acc_text = ""
    acc_reasoning = ""
    text_fields = BACKEND_TEXT_FIELDS.get(backend) or BACKEND_TEXT_FIELDS["agent"]

    while True:
        # До первого события ждём значительно меньше: «зависший» запрос
        # (частый случай, когда модель отказала, а Genspark не закрыл поток)
        # больше не держит клиента несколько минут.
        wait_for = idle_timeout if saw_any_event else min(idle_timeout, max(15, first_event_timeout))
        try:
            raw = await asyncio.wait_for(lines.__anext__(), timeout=wait_for)
        except asyncio.TimeoutError:
            if not saw_any_event:
                raise GensparkServerError(
                    f"Genspark sent no data within {wait_for}s "
                    f"(the account or the model is probably unavailable)", 504)
            raise GensparkServerError(
                f"Genspark stopped sending data for {idle_timeout}s", 504)
        except StopAsyncIteration:
            break
        if raw is None:
            break
        line = raw.strip() if isinstance(raw, str) else raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue

        # --- маркеры ошибок в сырых строках ---
        # Только в служебных строках: в тексте ответа модель может сама
        # написать «rate limit exceeded», и аккаунт не должен уходить в кулдаун.
        # Genspark сам присылает сообщение о лимите вместо ответа, поэтому
        # текст проверяем только до начала настоящего ответа.
        answer_text = saw_text and "message_field" in line
        if answer_text:
            pass
        elif any(m in line for m in RATE_LIMIT_MARKERS):
            raise GensparkRateLimitError("Genspark account rate limit", 429)
        elif _is_not_login(line):
            raise GensparkAuthError("Genspark cookie is not authorized (not login)", 401)
        elif any(m.lower() in line.lower() for m in FREE_LIMIT_MARKERS):
            raise GensparkFreeLimitError("Genspark daily limit for this account is used up", 402)
        if line.startswith("<!") or line.lower().startswith("<html"):
            if looks_like_cloudflare(line):
                raise GensparkCloudflareError("Cloudflare challenge inside the stream", 403)
            continue  # html-мусор внутри потока — пропускаем

        payload = line[5:].lstrip() if line.startswith("data:") else line
        if not payload:
            continue
        if payload == "[DONE]":
            break

        # --- не-JSON полезная нагрузка: ошибка или мусор ---
        if not payload.lstrip().startswith("{"):
            if saw_text or saw_any_event:
                continue
            raise GensparkServerError(f"Unexpected upstream response: {payload[:200]}", 502)

        try:
            probe = json.loads(payload)
        except Exception:
            continue
        if not isinstance(probe, dict):
            continue

        # JSON со status != 0 — ошибка апстрима (но не внутри событий чата)
        if "status" in probe and "type" not in probe:
            st = probe.get("status")
            msg = str(probe.get("message") or "")
            if st not in (0, None):
                if st == -5 or "not login" in msg.lower():
                    raise GensparkAuthError(f"Genspark cookie is not authorized: {msg or st}", 401)
                _raise_for_failure(msg or f"status={st}", status=502)

        event = _parse_event(payload, text_fields)
        if event is None:
            continue
        saw_any_event = True

        # Явная ошибка апстрима: прекращаем чтение сразу, не дожидаясь таймаута.
        if event.kind == "error":
            detail = event.text or _error_text_of(event.data)
            if saw_text:
                # Часть ответа уже отдана клиенту: это обрыв, а не успех —
                # иначе обрезанный ответ выглядит законченным.
                log.warning("Genspark failed after partial output: %s", detail[:300])
                raise GensparkServerError(
                    f"Genspark stopped mid-answer: {detail[:300] or 'upstream error'}", 502)
            _raise_for_failure(detail)

        # --- защита от дублирования/кумулятивных снимков ---
        # Сравнивать с накопленным текстом можно только кумулятивные снимки
        # (message_field / field_value). Настоящие дельты message_field_delta
        # добавляются как есть: иначе второй «-» или «**» в Markdown-ответе
        # принимался за «повтор» и пропадал.
        data = event.data if isinstance(event.data, dict) else {}
        is_snapshot = data.get("type") == "message_field" or "delta" not in data
        if event.kind == "text" and event.text:
            delta = event.text
            if is_snapshot and acc_text:
                if delta.startswith(acc_text):
                    delta = delta[len(acc_text):]  # снимок длиннее накопленного — берём хвост
                elif acc_text.startswith(delta):
                    delta = ""                      # повтор уже отправленного
            elif len(acc_text) >= _SNAPSHOT_MIN and delta.startswith(acc_text):
                # Бэкенд иногда присылает весь накопленный ответ ещё раз под видом
                # дельты. Короткие повторы («-», «**») сюда не попадают: порог
                # отсекает только копию длинного уже отданного текста.
                delta = delta[len(acc_text):]
            if not delta:
                continue
            acc_text += delta
            event.text = delta
        elif event.kind == "reasoning_delta" and event.text:
            delta = event.text
            if is_snapshot and acc_reasoning:
                if delta.startswith(acc_reasoning):
                    delta = delta[len(acc_reasoning):]
                elif acc_reasoning.startswith(delta):
                    delta = ""
            if not delta:
                continue
            acc_reasoning += delta
            event.text = delta

        if event.kind == "text" and event.text:
            saw_text = True
        if event.kind == "result":
            result_event = event
        yield event

    # стрим кончился без message_result — всё равно отдаём результат (с текстом или пустой)
    if result_event is None:
        if not saw_text and not saw_any_event:
            raise GensparkServerError(
                "Genspark closed the stream without sending any answer", 502)
        yield UpstreamEvent("result", text="")
