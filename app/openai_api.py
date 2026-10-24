"""OpenAI-совместимые эндпоинты хаба: /v1/models, /v1/chat/completions."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .chat import CompletionError, chat_service
from .config import config_store
from .providers.base import safe_provider_error
from .providers.http import close_quietly
from .routing import public_id, resolve
from .state import ClosingStreamingResponse, api_error_text, state, until_client_disconnects
from .tool_protocol import (TOOL_PROVIDER_ERROR, TOOL_PROVIDER_ERROR_RU, collect_tool_delta,
                            ordered_tool_calls, tool_provider_supported)
from .version import APP_VERSION

router = APIRouter()

#: Sampling / format options an OpenAI client may send that the providers forward upstream as they are
#: (``providers/custom.py::chat_stream``); anything of another type is ignored rather than passed on.
_PASSTHROUGH_PARAMS = (
    ("stop", (str, list)), ("seed", int), ("frequency_penalty", (int, float)),
    ("presence_penalty", (int, float)), ("repetition_penalty", (int, float)), ("top_k", int),
    ("min_p", (int, float)), ("response_format", dict),
)


def _client_key(request: Request) -> Optional[str]:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key") or None


def _check_auth(request: Request) -> Optional[JSONResponse]:
    # The public endpoint (app/public_api.py) has already checked its own key and limits and
    # marks the request; only its application can set this state, never a client.
    if getattr(request.state, "public_key", ""):
        return None
    if not config_store.authorize_api_request(
        _client_key(request), host=request.headers.get("host", ""),
        origin=request.headers.get("origin", ""),
        fetch_site=request.headers.get("sec-fetch-site", ""),
    ):
        return JSONResponse(
            status_code=401,
            content={"error": {**api_error_text("Неверный API-ключ хаба. Задайте его в разделе «Настройки → API».",
                                                "Invalid hub API key. Set it in Settings → API."),
                               "type": "invalid_request_error", "code": "invalid_api_key"}})
    return None


def _openai_error(message: str, err_type: str = "internal_error", status: int = 500,
                  hint: str = "", hint_en: str = "", message_en: str = "") -> Dict[str, Any]:
    """OpenAI-совместимая ошибка + понятная подсказка «что делать» (RU/EN)."""
    payload: Dict[str, Any] = {**api_error_text(message, message_en), "type": err_type, "param": "", "code": status}
    if not hint and not hint_en:
        from .chat import ERROR_HINTS
        hint, hint_en = ERROR_HINTS.get(err_type, ("", ""))
    if hint or hint_en:
        # The hint follows the language of ``message``; both variants ride along.
        hints = api_error_text(hint or hint_en, hint_en)
        payload["hint"], payload["hint_en"] = hints["message"], hints["message_en"]
        if "message_ru" in hints:
            payload["hint_ru"] = hints["message_ru"]
    return {"error": payload}


def _completion_error_payload(exc) -> Dict[str, Any]:
    message = safe_provider_error(exc.message)
    return _openai_error(message, exc.err_type, exc.status,
                         getattr(exc, "hint", ""), getattr(exc, "hint_en", ""),
                         safe_provider_error(getattr(exc, "message_en", "") or exc.message))


class _LeadingWhitespace:
    """Holds back whitespace-only content at the start of a stream.

    Upstreams (Yandex) send ``"\n\n"`` right before a tool call; OpenAI sends no content
    there, and clients showed an empty answer bubble. The held text is dropped when a tool
    call follows and restored in front of the first real text or at the finish."""

    def __init__(self) -> None:
        self.held = ""
        self.done = False

    def feed(self, item: Dict[str, Any]) -> List[Dict[str, Any]]:
        choices = item.get("choices") or []
        if self.done or not choices or not isinstance(choices[0], dict):
            return [item]
        choice = choices[0]
        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
        if delta.get("tool_calls"):
            self.held, self.done = "", True
            if isinstance(delta.get("content"), str) and not delta["content"].strip():
                return [self._with_content(item, None)]
            return [item]
        text = delta.get("content")
        if isinstance(text, str) and text:
            if text.strip() or choice.get("finish_reason") is not None:
                self.done = True
                text, self.held = self.held + text, ""
                return [self._with_content(item, text)]
            self.held += text
            return [self._with_content(item, None)]
        if choice.get("finish_reason") is not None and self.held:
            self.done = True
            text, self.held = self.held, ""
            return [self._with_content(item, text)]
        return [item]

    def flush(self) -> List[Dict[str, Any]]:
        if self.done or not self.held:
            return []
        self.done = True
        text, self.held = self.held, ""
        return [{"object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"content": text},
                                                                 "finish_reason": None}]}]

    @staticmethod
    def _with_content(item: Dict[str, Any], text: Optional[str]) -> Dict[str, Any]:
        choice = dict(item["choices"][0])
        delta = {k: v for k, v in (choice.get("delta") or {}).items() if k != "content"}
        if text is not None:
            delta["content"] = text
        choice["delta"] = delta
        return {**item, "choices": [choice, *item["choices"][1:]]}


def _unexpected_error(exc: Exception) -> str:
    """Return diagnostic context that cannot contain exception payload secrets."""
    return f"Неожиданная внутренняя ошибка ({type(exc).__name__})"


@router.get("/health")
async def health() -> Dict[str, Any]:
    by_provider: Dict[str, int] = {}
    for pid in ("genspark", "kiro", "antigravity", "geminiweb", "grok", "custom", "local"):
        by_provider[pid] = len(state.usable_accounts(pid))
    return {
        "status": "ok", "service": "memuai-proxy", "version": APP_VERSION,
        "proxy_enabled": config_store.settings.proxy_enabled,
        "accounts_usable": by_provider, "time": int(time.time()),
    }


def listed_models() -> List[Dict[str, Any]]:
    """The ids a client sees in /v1/models (also what the public endpoint may offer)."""
    data_by_id: Dict[str, Dict[str, Any]] = {}
    groups: Dict[str, List[Any]] = {}
    default = config_store.settings.default_provider
    for m in state.enabled_models():
        groups.setdefault(public_id(m.prefix, m.model), []).append(m)
    for model_id, entries in groups.items():
        if len(entries) > 1:
            # Без префиксов одинаковые имена у разных провайдеров совпадают.
            # routing.resolve выберет только провайдера по умолчанию, иначе
            # откажет — поэтому неоднозначное имя без него не публикуем, а
            # отдаём полные id с префиксом, которые маршрутизируются.
            preferred = [m for m in entries if default and default in (m.provider, m.prefix)]
            if preferred:
                entries = preferred[:1]
            else:
                for m in entries:
                    data_by_id[m.id] = {"id": m.id, "object": "model", "created": 1735689600,
                                        "owned_by": m.provider, "prefix": m.prefix}
                continue
        m = entries[0]
        data_by_id[model_id] = {"id": model_id, "object": "model", "created": 1735689600,
                                "owned_by": m.provider, "prefix": m.prefix}
    from .music_runtime import models as music_models
    for row in music_models():
        prefix = row["provider"].split(" · ")[0]
        model_id = public_id(prefix, row["raw_model"])
        entry = data_by_id.setdefault(model_id, {"id": model_id, "object": "model", "created": 1735689600,
                                                  "owned_by": "custom", "prefix": prefix})
        entry["capabilities"] = {"music": True, "text": False}
    try:   # Round 35: composite models (app/composite_models.py) — memu/smart and the like
        from . import composite_models
        for row in composite_models.listed():
            data_by_id.setdefault(row["id"], row)
    except Exception:
        pass
    try:   # Round 40: web versions of models (app/web_models.py) — «<model>:web»
        from . import web_models
        for row in web_models.listed():
            data_by_id.setdefault(row["id"], row)
    except Exception:
        pass
    return list(data_by_id.values())


@router.post("/v1/audio/music")
async def audio_music(request: Request):
    deny = _check_auth(request)
    if deny:
        return deny
    from . import music_runtime
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ValueError("Request must be a JSON object")
        scope = "api-" + hashlib.sha256((_client_key(request) or "local").encode()).hexdigest()
        return await music_runtime.run(payload, scope=scope)
    except asyncio.CancelledError:
        return JSONResponse(status_code=409, content=_openai_error("Music generation cancelled", "cancelled", 409))
    except ValueError as exc:
        return JSONResponse(status_code=400, content=_openai_error(str(exc), "invalid_request_error", 400))
    except Exception as exc:
        return JSONResponse(status_code=502, content=_openai_error(type(exc).__name__, "upstream_error", 502))


@router.get("/v1/models")
async def list_models(request: Request):
    deny = _check_auth(request)
    if deny:
        return deny
    return {"object": "list", "data": listed_models()}


@router.get("/v1/models/{model_id:path}")
async def get_model(model_id: str, request: Request):
    deny = _check_auth(request)
    if deny:
        return deny
    music = next((row for row in listed_models() if row["id"] == model_id and row.get("capabilities", {}).get("music")), None)
    if music is not None:
        return music
    composite = next((row for row in listed_models() if row["id"] == model_id
                      and (row.get("composite") or row.get("web"))), None)
    if composite is not None:
        return composite
    resolved = resolve(model_id)
    m = resolved.entry if resolved is not None else None
    if m is None or not m.enabled:
        return JSONResponse(status_code=404, content=_openai_error(
            f"Модель '{model_id}' не найдена", "invalid_request_error", 404,
            message_en=f"Model '{model_id}' not found"))
    return {"id": public_id(m.prefix, m.model), "object": "model", "created": 1735689600,
            "owned_by": m.provider}


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    return await until_client_disconnects(request, _chat_completions(request))


async def _chat_completions(request: Request):
    started = time.time()
    deny = _check_auth(request)
    if deny:
        return deny

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content=_openai_error(
            "Тело запроса должно быть корректным JSON", "invalid_request_error", 400,
            message_en="The request body must be valid JSON"))
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content=_openai_error(
            "Тело запроса должно быть JSON-объектом", "invalid_request_error", 400,
            message_en="The request body must be a JSON object"))

    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return JSONResponse(status_code=400, content=_openai_error(
            "Поле 'messages' обязательно и непустое", "invalid_request_error", 400,
            message_en="'messages' is required and must not be empty"))
    if any(not isinstance(message, dict) for message in messages):
        return JSONResponse(status_code=400, content=_openai_error(
            "Каждый элемент 'messages' должен быть объектом", "invalid_request_error", 400,
            message_en="Every item of 'messages' must be an object"))
    model_value = body.get("model")
    if not isinstance(model_value, str) or not model_value.strip():
        return JSONResponse(status_code=400, content=_openai_error(
            "Поле 'model' обязательно", "invalid_request_error", 400,
            message_en="'model' is required"))
    req_model = model_value.strip()
    if "stream" in body and not isinstance(body["stream"], bool):
        return JSONResponse(status_code=400, content=_openai_error(
            "Поле 'stream' должно быть логическим", "invalid_request_error", 400,
            message_en="'stream' must be a boolean"))
    n_value = body.get("n")
    if n_value is not None and (isinstance(n_value, bool) or not isinstance(n_value, int)
                                or n_value < 1):
        return JSONResponse(status_code=400, content=_openai_error(
            "Поле 'n' должно быть положительным целым", "invalid_request_error", 400,
            message_en="'n' must be a positive integer"))
    if isinstance(n_value, int) and n_value > 1:
        # Proxy всегда возвращает один вариант. Молча отдать choices из одного
        # элемента на n=2 значит сломать клиента, который читает choices[1].
        return JSONResponse(status_code=400, content=_openai_error(
            "n > 1 не поддерживается: Proxy возвращает один вариант ответа",
            "invalid_request_error", 400,
            message_en="n > 1 is not supported: the Proxy returns a single choice"))
    stream_options = body.get("stream_options")
    include_usage = isinstance(stream_options, dict) and stream_options.get("include_usage") is True
    tools = body.get("tools")
    if tools is not None:
        if not isinstance(tools, list) or any(not isinstance(tool, dict) for tool in tools):
            return JSONResponse(status_code=400, content=_openai_error(
                "Поле 'tools' должно быть массивом объектов", "invalid_request_error", 400,
                message_en="'tools' must be an array of objects"))
        if body.get("tool_choice") not in (None, "none") and not tools:
            return JSONResponse(status_code=400, content=_openai_error(
                "tool_choice requires non-empty tools", "invalid_request_error", 400))
    if (tools or any(message.get("role") == "tool" or message.get("tool_calls")
                     for message in messages)) and not tool_provider_supported(req_model):
        return JSONResponse(status_code=400, content=_openai_error(
            TOOL_PROVIDER_ERROR_RU, "unsupported_tools", 400, message_en=TOOL_PROVIDER_ERROR))

    params: Dict[str, Any] = {}
    for key in ("web_search", "enable_search"):
        if isinstance(body.get(key), bool):
            params["web_search"] = body[key]
            break
    for key in ("temperature", "max_tokens", "top_p"):
        if body.get(key) is not None:
            params[key] = body[key]
    # Round 34: stop / seed / penalties / response_format were silently dropped here although the
    # Custom provider forwards them — «stop» was ignored and JSON mode never reached the upstream.
    for key, kinds in _PASSTHROUGH_PARAMS:
        if body.get(key) is not None and isinstance(body[key], kinds) and not isinstance(body[key], bool):
            params[key] = body[key]
    if body.get("max_completion_tokens") is not None:
        params["max_tokens"] = body["max_completion_tokens"]
    if tools:
        params["tools"] = tools
        params["tools_available"] = True
        if "tool_choice" in body:
            params["tool_choice"] = body["tool_choice"]
        if isinstance(body.get("parallel_tool_calls"), bool):
            params["parallel_tool_calls"] = body["parallel_tool_calls"]
    requested_effort = body.get("effort") or body.get("reasoning_effort")
    if not requested_effort and isinstance(body.get("reasoning"), dict):
        requested_effort = body["reasoning"].get("effort")
    # Round 33 (r33-thinking): none = off, xhigh too; the Proxy clamps the value to what the model supports.
    if isinstance(requested_effort, str) and requested_effort.lower() in (
            "auto", "none", "off", "minimal", "low", "medium", "high", "xhigh", "max"):
        params["effort"] = requested_effort.lower()
    if isinstance(body.get("reasoning"), dict) and body["reasoning"].get("enabled") is False:
        params["effort"] = "off"     # OpenRouter-style switch
    for key in ("enable_thinking",):
        if isinstance(body.get(key), bool):
            params["thinking"] = body[key]
            if body[key] is False:
                params["effort"] = "off"
    if isinstance(body.get("thinking"), dict) and body["thinking"].get("type") in ("enabled", "disabled"):
        params["thinking"] = body["thinking"]["type"] == "enabled"
        if body["thinking"]["type"] == "disabled":
            params["effort"] = "off"

    wants_stream = bool(body.get("stream", False))
    params["_stream"] = wants_stream
    trace = state.open_trace(model=req_model)
    trace.capture_prompt(messages, tools=params.get("tools"))
    trace.add_stage("request_parsed", {"request_id": trace.request_id, "model": req_model,
                                       "stream": wants_stream, "turns": len(messages),
                                       "has_system_instruction": any(m.get("role") in ("system", "developer") for m in messages)})

    if not wants_stream:
        content_parts: List[str] = []
        reasoning_parts: List[str] = []
        usage: Dict[str, int] = {}
        response_id = ""
        finish_reason = "stop"
        tool_calls: Dict[int, Dict[str, Any]] = {}
        source = chat_service.stream_completion(req_model, messages, params, trace=trace)
        try:
            async for chunk in source:
                if chunk.get("object") == "memuai.event":
                    continue
                response_id = chunk.get("id") or response_id
                if chunk.get("usage"):
                    usage = chunk["usage"]
                    request.state.completion_usage = usage
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    content_parts.append(delta["content"])
                if delta.get("reasoning_content"):
                    reasoning_parts.append(delta["reasoning_content"])
                collect_tool_delta(tool_calls, delta)
                if choice.get("finish_reason") is not None:
                    finish_reason = str(choice.get("finish_reason") or "stop")
        except asyncio.CancelledError:
            state.cancel_trace(trace, getattr(request.state, "completion_usage", None))
            raise
        except CompletionError as e:
            message = safe_provider_error(e.message)
            trace.status, trace.error, trace.ms = e.status, message, int((time.time() - started) * 1000)
            trace.error_code = e.err_type
            trace.error_en = safe_provider_error(getattr(e, "message_en", "") or e.message)
            trace.add_stage("finished", {"error": message, "status": e.status,
                                         "error_code": e.err_type, "hint": e.hint,
                                         "error_en": trace.error_en, "hint_en": e.hint_en})
            state.commit_trace(trace)
            return JSONResponse(status_code=e.status, content=_completion_error_payload(e), headers=e.headers)
        except Exception as e:
            trace.status, trace.error, trace.ms = 500, _unexpected_error(e), int((time.time() - started) * 1000)
            state.commit_trace(trace)
            return JSONResponse(status_code=500, content=_openai_error(
                "Внутренняя ошибка MemuAI", message_en="Internal MemuAI error"))
        finally:
            await close_quietly(source.aclose)

        message: Dict[str, Any] = {"role": "assistant", "content": "".join(content_parts)}
        if reasoning_parts:
            message["reasoning_content"] = "".join(reasoning_parts)
        if tool_calls:
            message["tool_calls"] = ordered_tool_calls(tool_calls)
            if not message["content"].strip():
                # Upstreams send "\n\n" before a tool call; OpenAI returns null content there.
                message["content"] = None
        trace.status, trace.ms = 200, int((time.time() - started) * 1000)
        trace.prompt_tokens = usage.get("prompt_tokens", 0)
        trace.completion_tokens = usage.get("completion_tokens", 0)
        if config_store.settings.log_requests:
            state.commit_trace(trace)
        else:
            state.discard_trace(trace)
        return {
            "id": response_id or "chatcmpl-memuai", "object": "chat.completion",
            "created": int(time.time()), "model": req_model,
            "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
            "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "system_fingerprint": "memuai",
        }

    # Служебные события MemuAI (веб-поиск и т. п.) не входят в схему OpenAI —
    # сторонние клиенты получают только настоящие чанки.
    async def openai_only(source):
        try:
            async for item in source:
                if item.get("object") == "memuai.event":
                    continue
                yield item
        finally:
            await close_quietly(source.aclose)

    stream = openai_only(chat_service.stream_completion(req_model, messages, params, trace=trace))
    try:
        first_chunk = await anext(stream)
        if first_chunk.get("usage"):
            request.state.completion_usage = first_chunk["usage"]
    except asyncio.CancelledError:
        await close_quietly(stream.aclose)
        state.cancel_trace(trace, getattr(request.state, "completion_usage", None))
        raise
    except CompletionError as e:
        message = safe_provider_error(e.message)
        trace.status, trace.error, trace.ms = e.status, message, int((time.time() - started) * 1000)
        trace.error_code = e.err_type
        trace.error_en = safe_provider_error(getattr(e, "message_en", "") or e.message)
        trace.add_stage("finished", {"error": message, "status": e.status,
                                     "error_code": e.err_type, "hint": e.hint,
                                     "error_en": trace.error_en, "hint_en": e.hint_en})
        state.commit_trace(trace)
        return JSONResponse(status_code=e.status, content=_completion_error_payload(e), headers=e.headers)
    except StopAsyncIteration:
        message = "Провайдер завершил поток без ответа"
        message_en = "The provider closed the stream without an answer"
        trace.status, trace.error, trace.ms = 502, message, int((time.time() - started) * 1000)
        trace.error_en = message_en
        trace.add_stage("finished", {"error": message, "status": 502, "error_en": message_en})
        state.commit_trace(trace)
        return JSONResponse(status_code=502, content=_openai_error(
            message, "upstream_error", 502, message_en=message_en))
    except Exception as e:
        trace.status, trace.error, trace.ms = 500, _unexpected_error(e), int((time.time() - started) * 1000)
        state.commit_trace(trace)
        return JSONResponse(status_code=500, content=_openai_error(
            "Внутренняя ошибка MemuAI", message_en="Internal MemuAI error"))

    async def sse() -> AsyncIterator[str]:
        err_text = ""
        err_text_en = ""
        trace_error = ""
        err_hint = ""
        err_hint_en = ""
        code = 200
        err_type = "request_error"
        usage: Dict[str, int] = {}
        def encode(item: Dict[str, Any]) -> str:
            if include_usage and item.get("usage") and item.get("choices"):
                # stream_options.include_usage по спецификации OpenAI: usage
                # приходит отдельным последним чанком с пустым choices
                # (SDK и LangChain/LiteLLM ждут именно его), а в остальных
                # чанках usage равен null.
                item = dict(item)
                trailing = {key: item[key] for key in ("id", "object", "created", "model",
                                                        "system_fingerprint") if key in item}
                trailing.update({"choices": [], "usage": item["usage"]})
                item["usage"] = None
                return (f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                        f"data: {json.dumps(trailing, ensure_ascii=False)}\n\n")
            return f"data: {json.dumps(item, ensure_ascii=False)}\n\n"

        try:
            if first_chunk.get("usage"):
                usage = first_chunk["usage"]
                request.state.completion_usage = usage
            first_choices = first_chunk.get("choices") or []
            if (first_choices and isinstance(first_choices[0], dict)
                    and isinstance(first_choices[0].get("delta"), dict)
                    and "role" not in first_choices[0]["delta"]):
                # Первый чанк OpenAI несёт delta.role = "assistant"; часть
                # клиентов (Java/.NET SDK, старые LangChain) берёт роль оттуда.
                choice0 = dict(first_choices[0])
                choice0["delta"] = {"role": "assistant", **choice0["delta"]}
                first_chunk_out = {**first_chunk, "choices": [choice0, *first_choices[1:]]}
            else:
                first_chunk_out = first_chunk
            leading = _LeadingWhitespace()
            for out in leading.feed(first_chunk_out):
                yield encode(out)
            # Клиенты (и обратные прокси) рвут «молчащее» соединение. Пока
            # провайдер думает, отправляем SSE-комментарии — они невидимы для
            # OpenAI-совместимых SDK, но держат канал живым.
            keepalive = (float(config_store.settings.sse_keepalive_interval)
                         if config_store.settings.sse_keepalive else 0.0)
            queue: asyncio.Queue = asyncio.Queue(maxsize=256)
            sentinel = object()

            async def pump() -> None:
                try:
                    async for item in stream:
                        # The provider has spent these tokens even if the
                        # client stops before this queued chunk is delivered.
                        if item.get("usage"):
                            request.state.completion_usage = item["usage"]
                        await queue.put(item)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — пробрасываем потребителю
                    await queue.put(exc)
                else:
                    await queue.put(sentinel)

            pump_task = asyncio.create_task(pump())
            try:
                while True:
                    if keepalive > 0:
                        try:
                            item = await asyncio.wait_for(queue.get(), timeout=keepalive)
                        except asyncio.TimeoutError:
                            yield ": keepalive\n\n"
                            continue
                    else:
                        item = await queue.get()
                    if item is sentinel:
                        for out in leading.flush():
                            yield encode(out)
                        break
                    if isinstance(item, BaseException):
                        raise item
                    if item.get("usage"):
                        usage = item["usage"]
                    for out in leading.feed(item):
                        yield encode(out)
            finally:
                pump_task.cancel()
                try:
                    await pump_task
                except (asyncio.CancelledError, Exception):
                    pass
        except CompletionError as e:
            code, err_text, err_type = e.status, safe_provider_error(e.message), e.err_type
            err_hint, err_hint_en = e.hint, e.hint_en
            err_text_en = safe_provider_error(getattr(e, "message_en", "") or e.message)
        except Exception as e:
            code, err_text, trace_error = 500, "Внутренняя ошибка MemuAI", _unexpected_error(e)
        finally:
            await close_quietly(stream.aclose)

        if code != 200:
            payload = _openai_error(err_text, err_type, code, err_hint, err_hint_en,
                                    err_text_en)
            trace.error_en = err_text_en
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            trace.status, trace.error, trace.ms = code, trace_error or err_text, int((time.time() - started) * 1000)
            trace.error_code = err_type
            trace.add_stage("finished", {"error": err_text, "status": code,
                                         "error_code": err_type, "hint": err_hint,
                                         "error_en": err_text_en, "hint_en": err_hint_en})
            state.commit_trace(trace)
        elif config_store.settings.log_requests:
            trace.status, trace.ms = 200, int((time.time() - started) * 1000)
            trace.prompt_tokens = usage.get("prompt_tokens", 0)
            trace.completion_tokens = usage.get("completion_tokens", 0)
            state.commit_trace(trace)
        else:
            state.discard_trace(trace)
        yield "data: [DONE]\n\n"

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return ClosingStreamingResponse(state.guard_stream(sse(), trace), closer=stream.aclose, media_type="text/event-stream", headers=headers)
