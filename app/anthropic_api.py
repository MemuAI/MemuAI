"""Anthropic Messages adapter with native tool calls for Custom API models."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .chat import CompletionError, chat_service
from .config import config_store
from .providers.base import safe_provider_error
from .providers.http import close_quietly
from .state import ClosingStreamingResponse, api_error_text, state, until_client_disconnects
from .tool_protocol import (TOOL_PROVIDER_ERROR, TOOL_PROVIDER_ERROR_RU, collect_tool_delta,
                            ordered_tool_calls, tool_provider_supported)

router = APIRouter()

def _client_key(request: Request) -> Optional[str]:
    key = request.headers.get("x-api-key")
    if key:
        return key
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None

def _check_auth(request: Request) -> Optional[JSONResponse]:
    if getattr(request.state, "public_key", ""):
        return None
    if not config_store.authorize_api_request(
        _client_key(request), host=request.headers.get("host", ""),
        origin=request.headers.get("origin", ""),
        fetch_site=request.headers.get("sec-fetch-site", ""),
    ):
        return JSONResponse(status_code=401, content=_anthropic_error(
            "authentication_error",
            "Неверный API-ключ хаба. Задайте его в разделе «Настройки → API» (x-api-key или Authorization: Bearer).",
            "Invalid hub API key. Set it in Settings → API (x-api-key or Authorization: Bearer).",
        ))
    return None

def _anthropic_error(err_type: str, message: str, message_en: str = "") -> Dict[str, Any]:
    return {"type": "error", "error": {"type": err_type, **api_error_text(message, message_en)}}

def _flatten_blocks(content: Any, images: Optional[List[Dict[str, Any]]] = None) -> str:
    """Flatten text-only blocks used by system prompts and tool results.

    ``images`` собирает картинки из tool_result (Read в Claude Code на
    скриншоте): вызывающий добавит их отдельным сообщением пользователя.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: List[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            parts.append(str(block.get("text") or ""))
        elif btype == "tool_result":
            inner = block.get("content")
            if isinstance(inner, str):
                parts.append(inner)
            elif isinstance(inner, list):
                flattened = _flatten_blocks(inner, images)
                if flattened:
                    parts.append(flattened)
        elif btype == "image":
            if images is None:
                raise ValueError("Images are supported only in user messages with a base64 or URL source")
            images.append(block)
            parts.append("[image attached below]")
    return "\n".join(p for p in parts if p)

def _image_block_url(block: Dict[str, Any]) -> str:
    source = block.get("source") or {}
    if not isinstance(source, dict):
        raise ValueError("Image source must be an object")
    if source.get("type") == "base64":
        media_type, data = source.get("media_type"), source.get("data")
        if (not isinstance(media_type, str) or not media_type.startswith("image/")
                or not isinstance(data, str) or not data):
            raise ValueError("Invalid base64 image source")
        return f"data:{media_type};base64,{data}"
    if source.get("type") == "url":
        url = source.get("url")
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            raise ValueError("Invalid image URL")
        return url
    raise ValueError("Unsupported Anthropic image source")

def _to_internal_messages(body: Dict[str, Any]) -> List[Dict[str, Any]]:
    messages: List[Dict[str, Any]] = []
    system = body.get("system")
    system_text = _flatten_blocks(system) if system is not None else ""
    if system_text.strip():
        messages.append({"role": "system", "content": system_text})
    for raw in body.get("messages") or []:
        if not isinstance(raw, dict):
            continue
        role = raw.get("role")
        if role not in ("user", "assistant"):
            continue
        blocks = raw.get("content")
        if not isinstance(blocks, list):
            messages.append({"role": role, "content": _flatten_blocks(blocks)})
            continue
        if role == "assistant":
            text = _flatten_blocks([block for block in blocks if isinstance(block, dict)
                                    and block.get("type") in ("text", "image")])
            calls = []
            for block in blocks:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    calls.append({"id": str(block.get("id") or ""), "type": "function",
                                  "function": {"name": str(block.get("name") or ""),
                                               "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False)}})
            message: Dict[str, Any] = {"role": "assistant", "content": text or None}
            if calls:
                message["tool_calls"] = calls
            messages.append(message)
        else:
            content_parts: List[Dict[str, Any]] = []
            tool_images: List[Dict[str, Any]] = []

            def flush_user() -> None:
                if not content_parts:
                    return
                if all(part["type"] == "text" for part in content_parts):
                    content: Any = "\n".join(part["text"] for part in content_parts)
                else:
                    content = list(content_parts)
                messages.append({"role": "user", "content": content})
                content_parts.clear()

            for block in blocks:
                if not isinstance(block, dict):
                    raise ValueError("Each content block must be an object")
                if block.get("type") == "text":
                    content_parts.append({"type": "text", "text": str(block.get("text") or "")})
                elif block.get("type") == "image":
                    content_parts.append({"type": "image_url",
                                          "image_url": {"url": _image_block_url(block)}})
                elif block.get("type") == "tool_result":
                    flush_user()
                    messages.append({"role": "tool", "tool_call_id": str(block.get("tool_use_id") or ""),
                                     "content": _flatten_blocks(block.get("content"), tool_images)})
                else:
                    raise ValueError(f"Unsupported Anthropic content block: {block.get('type')}")
            for image in tool_images[:8]:
                content_parts.append({"type": "image_url",
                                      "image_url": {"url": _image_block_url(image)}})
            flush_user()
    return messages

def _stop_reason(openai_reason: Optional[str]) -> str:
    return {"length": "max_tokens", "tool_calls": "tool_use",
            "content_filter": "end_turn", "stop": "end_turn"}.get(
        openai_reason or "stop", "end_turn")

def _anthropic_tools(raw_tools: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_tools, list) or any(not isinstance(tool, dict) for tool in raw_tools):
        raise ValueError("'tools' must be an array of objects")
    result = []
    for tool in raw_tools:
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("Each tool needs a name")
        result.append({"type": "function", "function": {"name": name,
                       "description": str(tool.get("description") or ""),
                       "parameters": tool.get("input_schema") or {"type": "object", "properties": {}}}})
    return result

def _tool_choice(raw: Any) -> Any:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("'tool_choice' must be an object")
    kind = raw.get("type", "auto")
    if kind == "auto":
        return "auto"
    if kind == "none":
        return "none"
    if kind == "any":
        return "required"
    if kind == "tool" and isinstance(raw.get("name"), str):
        return {"type": "function", "function": {"name": raw["name"]}}
    raise ValueError("Unsupported tool_choice")

def _tool_blocks(calls: Dict[int, Dict[str, Any]]) -> List[Dict[str, Any]]:
    blocks = []
    for call in ordered_tool_calls(calls):
        try:
            args = json.loads(call["function"]["arguments"] or "{}")
        except (TypeError, ValueError) as exc:
            raise ValueError("Upstream returned invalid tool arguments") from exc
        if not isinstance(args, dict):
            raise ValueError("Upstream tool arguments must be an object")
        blocks.append({"type": "tool_use", "id": call["id"],
                       "name": call["function"]["name"], "input": args})
    return blocks

def _msg_id() -> str:
    return "msg_" + uuid.uuid4().hex[:24]

@router.post("/v1/messages/count_tokens")
async def count_tokens(request: Request):
    """Round 27: Claude Code (ANTHROPIC_BASE_URL → Proxy) asks this before long turns and for /context;
    the route was missing and the panel's catch-all answered 405. Upstreams have no such endpoint, so this
    is the same estimate the Proxy records for usage when an upstream reports none."""
    deny = _check_auth(request)
    if deny:
        return deny
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict) or not isinstance(body.get("messages"), list):
        return JSONResponse(status_code=400, content=_anthropic_error(
            "invalid_request_error", "Нужен JSON-объект с полем 'messages'",
            "A JSON object with a 'messages' field is required"))
    try:
        tools = _anthropic_tools(body.get("tools") or [])
        messages_in = _to_internal_messages(body)
    except ValueError as exc:
        return JSONResponse(status_code=400, content=_anthropic_error("invalid_request_error", str(exc)))
    from .chat import _prompt_tokens
    from .providers.base import approx_tokens
    model = str(body.get("model") or "")
    total = _prompt_tokens(messages_in, model)
    if tools:
        total += approx_tokens(json.dumps(tools, ensure_ascii=False), model)
    return {"input_tokens": total}

@router.post("/v1/messages")
async def messages(request: Request):
    return await until_client_disconnects(request, _messages(request))

async def _messages(request: Request):
    started = time.time()
    deny = _check_auth(request)
    if deny:
        return deny

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content=_anthropic_error(
            "invalid_request_error", "Тело запроса должно быть корректным JSON",
            "The request body must be valid JSON"))
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content=_anthropic_error(
            "invalid_request_error", "Тело запроса должно быть JSON-объектом",
            "The request body must be a JSON object"))

    model_value = body.get("model")
    if not isinstance(model_value, str) or not model_value.strip():
        return JSONResponse(status_code=400, content=_anthropic_error(
            "invalid_request_error", "Поле 'model' обязательно", "The 'model' field is required"))
    req_model = model_value.strip()

    raw_messages = body.get("messages")
    if not isinstance(raw_messages, list) or not raw_messages:
        return JSONResponse(status_code=400, content=_anthropic_error(
            "invalid_request_error", "Поле 'messages' обязательно и непустое",
            "The 'messages' field is required and must not be empty"))

    try:
        tools = _anthropic_tools(body.get("tools") or [])
        tool_choice = _tool_choice(body.get("tool_choice"))
    except ValueError as exc:
        return JSONResponse(status_code=400, content=_anthropic_error(
            "invalid_request_error", str(exc)))
    if tool_choice not in (None, "none") and not tools:
        return JSONResponse(status_code=400, content=_anthropic_error(
            "invalid_request_error", "tool_choice requires non-empty tools"))
    try:
        messages_in = _to_internal_messages(body)
    except ValueError as exc:
        return JSONResponse(status_code=400, content=_anthropic_error(
            "invalid_request_error", str(exc)))
    if (tools or any(message.get("role") == "tool" or message.get("tool_calls")
                     for message in messages_in)) and not tool_provider_supported(req_model):
        return JSONResponse(status_code=400, content=_anthropic_error(
            "invalid_request_error", TOOL_PROVIDER_ERROR_RU, TOOL_PROVIDER_ERROR))
    if not messages_in or not any(m["role"] in ("user", "tool") for m in messages_in):
        return JSONResponse(status_code=400, content=_anthropic_error(
            "invalid_request_error", "Нужно хотя бы одно сообщение с ролью 'user'",
            "At least one message with role 'user' is required"))

    params: Dict[str, Any] = {}
    if isinstance(body.get("temperature"), (int, float)):
        params["temperature"] = body["temperature"]
    if isinstance(body.get("top_p"), (int, float)):
        params["top_p"] = body["top_p"]
    max_tokens = body.get("max_tokens")
    if isinstance(max_tokens, int) and max_tokens > 0:
        params["max_tokens"] = max_tokens

    stops = body.get("stop_sequences")
    if isinstance(stops, list) and stops and all(isinstance(item, str) and item for item in stops):
        params["stop"] = stops[:4]
    if isinstance(body.get("top_k"), int) and not isinstance(body.get("top_k"), bool) and body["top_k"] > 0:
        params["top_k"] = body["top_k"]
    thinking = body.get("thinking") if isinstance(body.get("thinking"), dict) else {}
    wants_thinking = thinking.get("type") in ("enabled", "adaptive")
    if thinking.get("type") in ("enabled", "disabled"):
        params["thinking"] = thinking["type"] == "enabled"
        if thinking["type"] == "disabled":
            params["effort"] = "off"
    effort = (body.get("output_config") or {}).get("effort") if isinstance(body.get("output_config"), dict) else None
    if isinstance(effort, str) and effort.lower() in ("low", "medium", "high", "xhigh", "max"):
        params["effort"] = effort.lower()
    budget = thinking.get("budget_tokens")
    if thinking.get("type") == "enabled" and "effort" not in params and isinstance(budget, int) \
            and not isinstance(budget, bool) and budget > 0:

        params["effort"] = "low" if budget <= 4096 else ("medium" if budget <= 12000 else "high")
    if tools:
        params["tools"] = tools
        params["tools_available"] = True
        if tool_choice is not None:
            params["tool_choice"] = tool_choice
        if isinstance(body.get("disable_parallel_tool_use"), bool):
            params["parallel_tool_calls"] = not body["disable_parallel_tool_use"]

    wants_stream = bool(body.get("stream", False))
    params["_stream"] = wants_stream
    trace = state.open_trace(model=req_model)
    trace.capture_prompt(messages_in, tools=params.get("tools"))
    trace.add_stage("request_parsed", {"request_id": trace.request_id, "model": req_model,
                                       "stream": wants_stream, "turns": len(messages_in),
                                       "api": "anthropic.messages"})

    if not wants_stream:
        content_parts: List[str] = []
        reasoning_parts: List[str] = []
        usage: Dict[str, int] = {}
        finish_reason = "stop"
        tool_calls: Dict[int, Dict[str, Any]] = {}
        source = chat_service.stream_completion(req_model, messages_in, params, trace=trace)
        try:
            async for chunk in source:
                if chunk.get("object") == "memuai.event":
                    continue
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
                if wants_thinking and delta.get("reasoning_content"):
                    reasoning_parts.append(str(delta["reasoning_content"]))
                collect_tool_delta(tool_calls, delta)
                if choice.get("finish_reason") is not None:
                    finish_reason = str(choice.get("finish_reason") or "stop")
        except asyncio.CancelledError:
            state.cancel_trace(trace, getattr(request.state, "completion_usage", None))
            raise
        except CompletionError as e:
            return _completion_error_response(e, trace, started)
        except Exception as e:
            return _unexpected_error_response(e, trace, started)
        finally:
            await close_quietly(source.aclose)

        try:
            text = "".join(content_parts)

            blocks = ([{"type": "text", "text": text}]
                      if text and (text.strip() or not tool_calls) else []) + _tool_blocks(tool_calls)
            thought = "".join(reasoning_parts)
            if thought.strip():
                blocks.insert(0, {"type": "thinking", "thinking": thought, "signature": ""})
        except ValueError:
            trace.status, trace.error = 502, "Upstream returned invalid tool arguments"
            state.commit_trace(trace)
            return JSONResponse(status_code=502, content=_anthropic_error(
                "api_error", "Upstream returned invalid tool arguments"))
        trace.status, trace.ms = 200, int((time.time() - started) * 1000)
        trace.prompt_tokens = usage.get("prompt_tokens", 0)
        trace.completion_tokens = usage.get("completion_tokens", 0)
        if config_store.settings.log_requests:
            state.commit_trace(trace)
        else:
            state.discard_trace(trace)
        if tool_calls:
            finish_reason = "tool_calls"
        return {
            "id": _msg_id(), "type": "message", "role": "assistant", "model": req_model,
            "content": blocks or [{"type": "text", "text": ""}],
            "stop_reason": _stop_reason(finish_reason), "stop_sequence": None,
            "usage": {"input_tokens": usage.get("prompt_tokens", 0),
                      "output_tokens": usage.get("completion_tokens", 0)},
        }

    async def openai_only(source):
        try:
            async for item in source:
                if item.get("object") == "memuai.event":
                    continue
                yield item
        finally:
            await close_quietly(source.aclose)

    stream = openai_only(chat_service.stream_completion(req_model, messages_in, params, trace=trace))
    try:
        first_chunk = await anext(stream)
        if first_chunk.get("usage"):
            request.state.completion_usage = first_chunk["usage"]
    except asyncio.CancelledError:
        await close_quietly(stream.aclose)
        state.cancel_trace(trace, getattr(request.state, "completion_usage", None))
        raise
    except CompletionError as e:
        return _completion_error_response(e, trace, started)
    except StopAsyncIteration:
        trace.status, trace.error, trace.ms = 502, "Провайдер завершил поток без ответа", int((time.time() - started) * 1000)
        state.commit_trace(trace)
        return JSONResponse(status_code=502, content=_anthropic_error(
            "api_error", "Провайдер завершил поток без ответа",
            "The provider ended the stream without a response"))
    except Exception as e:
        return _unexpected_error_response(e, trace, started)

    async def sse() -> AsyncIterator[str]:
        def ev(event_type: str, data: Dict[str, Any]) -> str:
            return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        msg_id = _msg_id()
        usage: Dict[str, int] = {}
        finish_reason = "stop"
        err: Optional[CompletionError] = None
        unexpected: Optional[Exception] = None
        block_open = False
        text_index = 0
        thinking_open = False
        next_index = 0
        pending_text = ""
        tool_calls: Dict[int, Dict[str, Any]] = {}

        def close_thinking() -> List[str]:
            nonlocal thinking_open, next_index
            if not thinking_open:
                return []
            thinking_open = False
            next_index += 1
            return [ev("content_block_delta", {"type": "content_block_delta", "index": next_index - 1,
                                               "delta": {"type": "signature_delta", "signature": ""}}),
                    ev("content_block_stop", {"type": "content_block_stop", "index": next_index - 1})]

        try:
            yield ev("message_start", {"type": "message_start", "message": {
                "id": msg_id, "type": "message", "role": "assistant", "model": req_model,
                "content": [], "stop_reason": None, "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0}}})

            keepalive = (float(config_store.settings.sse_keepalive_interval)
                        if config_store.settings.sse_keepalive else 0.0)
            queue: asyncio.Queue = asyncio.Queue(maxsize=256)
            sentinel = object()

            async def pump(first) -> None:
                try:
                    await queue.put(first)
                    async for item in stream:
                        if item.get("usage"):
                            request.state.completion_usage = item["usage"]
                        await queue.put(item)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    await queue.put(exc)
                else:
                    await queue.put(sentinel)

            pump_task = asyncio.create_task(pump(first_chunk))
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
                        break
                    if isinstance(item, BaseException):
                        raise item
                    if item.get("usage"):
                        usage = item["usage"]
                    choices = item.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") or {}
                    thought = delta.get("reasoning_content") if wants_thinking and not block_open else None
                    if thought:
                        if not thinking_open:
                            thinking_open = True
                            yield ev("content_block_start", {"type": "content_block_start", "index": next_index,
                                                              "content_block": {"type": "thinking", "thinking": ""}})
                        yield ev("content_block_delta", {"type": "content_block_delta", "index": next_index,
                                                         "delta": {"type": "thinking_delta", "thinking": str(thought)}})
                    text = delta.get("content")
                    if text and not block_open and not text.strip():

                        pending_text += text
                    elif text:
                        if not block_open:
                            for event in close_thinking():
                                yield event
                            block_open = True
                            text_index = next_index
                            text, pending_text = pending_text + text, ""
                            yield ev("content_block_start", {"type": "content_block_start", "index": text_index,
                                                              "content_block": {"type": "text", "text": ""}})
                        yield ev("content_block_delta", {"type": "content_block_delta", "index": text_index,
                                                         "delta": {"type": "text_delta", "text": text}})
                    collect_tool_delta(tool_calls, delta)
                    if choice.get("finish_reason") is not None:
                        finish_reason = str(choice.get("finish_reason") or "stop")
            finally:
                pump_task.cancel()
                try:
                    await pump_task
                except (asyncio.CancelledError, Exception):
                    pass
        except CompletionError as e:
            err = e
        except Exception as e:
            unexpected = e
        finally:
            await close_quietly(stream.aclose)

        if err is not None:
            message = safe_provider_error(err.message)
            trace.status, trace.error, trace.ms = err.status, message, int((time.time() - started) * 1000)
            trace.error_code = err.err_type
            trace.add_stage("finished", {"error": message, "status": err.status, "error_code": err.err_type})
            state.commit_trace(trace)
            yield ev("error", _anthropic_error(_anthropic_type(err.status), message,
                                              safe_provider_error(err.message_en)))
            return
        if unexpected is not None:
            trace.status, trace.error, trace.ms = 500, f"Неожиданная внутренняя ошибка ({type(unexpected).__name__})", int((time.time() - started) * 1000)
            state.commit_trace(trace)
            yield ev("error", _anthropic_error("api_error", "Внутренняя ошибка MemuAI", "Internal MemuAI error"))
            return

        for event in close_thinking():
            yield event
        if pending_text and not block_open and not tool_calls:
            block_open = True
            text_index = next_index
            yield ev("content_block_start", {"type": "content_block_start", "index": text_index,
                                              "content_block": {"type": "text", "text": ""}})
            yield ev("content_block_delta", {"type": "content_block_delta", "index": text_index,
                                             "delta": {"type": "text_delta", "text": pending_text}})
        if block_open:
            yield ev("content_block_stop", {"type": "content_block_stop", "index": text_index})
            next_index = text_index + 1
        if tool_calls:
            try:
                blocks = _tool_blocks(tool_calls)
            except ValueError:
                trace.status, trace.error = 502, "Upstream returned invalid tool arguments"
                state.commit_trace(trace)
                yield ev("error", _anthropic_error("api_error", "Upstream returned invalid tool arguments"))
                return
            for index, block in enumerate(blocks, start=next_index):
                yield ev("content_block_start", {"type": "content_block_start", "index": index,
                                                "content_block": {"type": "tool_use", "id": block["id"],
                                                                  "name": block["name"], "input": {}}})
                yield ev("content_block_delta", {"type": "content_block_delta", "index": index,
                                                "delta": {"type": "input_json_delta",
                                                          "partial_json": json.dumps(block["input"], ensure_ascii=False)}})
                yield ev("content_block_stop", {"type": "content_block_stop", "index": index})
            finish_reason = "tool_calls"
        yield ev("message_delta", {"type": "message_delta",
                                   "delta": {"stop_reason": _stop_reason(finish_reason), "stop_sequence": None},
                                   "usage": {"input_tokens": usage.get("prompt_tokens", 0),
                                             "output_tokens": usage.get("completion_tokens", 0)}})
        yield ev("message_stop", {"type": "message_stop"})

        trace.status, trace.ms = 200, int((time.time() - started) * 1000)
        trace.prompt_tokens = usage.get("prompt_tokens", 0)
        trace.completion_tokens = usage.get("completion_tokens", 0)
        if config_store.settings.log_requests:
            state.commit_trace(trace)
        else:
            state.discard_trace(trace)

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return ClosingStreamingResponse(state.guard_stream(sse(), trace), closer=stream.aclose, media_type="text/event-stream", headers=headers)

def _anthropic_type(status: int) -> str:
    """Тип ошибки по статусу, как у Anthropic: SDK и Claude Code повторяют
    запрос при overloaded_error/rate_limit_error, а не при api_error."""
    return {400: "invalid_request_error", 401: "authentication_error",
            403: "permission_error", 404: "not_found_error", 413: "request_too_large",
            429: "rate_limit_error", 503: "overloaded_error",
            529: "overloaded_error"}.get(int(status or 500), "api_error")

def _completion_error_response(e: CompletionError, trace, started: float) -> JSONResponse:
    message = safe_provider_error(e.message)
    trace.status, trace.error, trace.ms = e.status, message, int((time.time() - started) * 1000)
    trace.error_code = e.err_type
    trace.add_stage("finished", {"error": message, "status": e.status, "error_code": e.err_type})
    state.commit_trace(trace)
    return JSONResponse(status_code=e.status, content=_anthropic_error(
        _anthropic_type(e.status), message, safe_provider_error(e.message_en)), headers=e.headers)

def _unexpected_error_response(e: Exception, trace, started: float) -> JSONResponse:
    trace.status, trace.error, trace.ms = 500, f"Неожиданная внутренняя ошибка ({type(e).__name__})", int((time.time() - started) * 1000)
    state.commit_trace(trace)
    return JSONResponse(status_code=500, content=_anthropic_error("api_error", "Внутренняя ошибка MemuAI", "Internal MemuAI error"))
