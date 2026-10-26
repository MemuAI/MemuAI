"""Stateless OpenAI Responses API bridge used by current Codex clients.

The model still runs through MemuAI routing. Function calls are sent to the
selected OpenAI-compatible Custom API; execution remains in the Codex client.
"""
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
from .openai_api import _check_auth, _completion_error_payload, _openai_error, _unexpected_error
from .providers.base import safe_provider_error
from .state import ClosingStreamingResponse, state, until_client_disconnects
from .providers.http import close_quietly
from .tool_protocol import (TOOL_PROVIDER_ERROR, TOOL_PROVIDER_ERROR_RU, collect_tool_delta,
                            ordered_tool_calls, tool_provider_supported)

router = APIRouter()

def _finish_trace(trace: Any, status: int, error: str = "", code: str = "",
                  usage: Optional[Dict[str, int]] = None, *, commit: bool = True) -> None:
    """Итог запроса в журнале: статус, ошибка, время и токены (как в /v1/chat)."""
    trace.status = status
    if error:
        trace.error = error
    if code:
        trace.error_code = code
    trace.ms = int((time.time() - trace.ts) * 1000)
    if usage:
        trace.prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        trace.completion_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    if commit:
        state.commit_trace(trace)

def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") in ("input_text", "output_text", "text"):
                    parts.append(str(item.get("text") or ""))
                elif item.get("type") == "refusal":
                    parts.append(str(item.get("refusal") or ""))
                else:
                    raise ValueError(f"Unsupported Responses content type: {item.get('type')}")
        return "\n".join(parts)
    raise ValueError("Message content must be text or text blocks")

def _message_content(content: Any, role: str) -> Any:
    if not isinstance(content, list):
        return _text(content)
    if not any(isinstance(part, dict) and part.get("type") == "input_image"
               for part in content):
        return _text(content)
    if role != "user":
        raise ValueError("Images are supported only in user messages")
    parts = []
    for part in content:
        if not isinstance(part, dict):
            raise ValueError("Each content part must be an object")
        kind = part.get("type")
        if kind in ("input_text", "text"):
            parts.append({"type": "text", "text": str(part.get("text") or "")})
        elif kind == "input_image":
            url = part.get("image_url")
            if not isinstance(url, str) or not url.startswith(("data:image/", "https://", "http://")):
                raise ValueError("input_image requires an image_url or data URL; file_id is unsupported")
            image: Dict[str, Any] = {"url": url}
            if part.get("detail") in ("auto", "low", "high", "original"):
                image["detail"] = part["detail"]
            parts.append({"type": "image_url", "image_url": image})
        else:
            raise ValueError(f"Unsupported Responses content type: {kind}")
    return parts

def _messages(body: Dict[str, Any]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    instructions = body.get("instructions")
    if instructions:
        result.append({"role": "system", "content": str(instructions)})
    raw = body.get("input")
    if isinstance(raw, str):
        result.append({"role": "user", "content": raw})
    elif isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                raise ValueError("Each input item must be an object")
            kind = item.get("type")
            if kind in ("function_call", "custom_tool_call"):
                arguments = (str(item.get("arguments") or "{}") if kind == "function_call"
                             else json.dumps({"input": str(item.get("input") or "")}, ensure_ascii=False))
                call = {
                    "id": str(item.get("call_id") or ""), "type": "function",
                    "function": {"name": str(item.get("name") or ""),
                                 "arguments": arguments}}
                if result and result[-1].get("role") == "assistant" and "tool_calls" in result[-1]:
                    result[-1]["tool_calls"].append(call)
                else:
                    result.append({"role": "assistant", "content": None, "tool_calls": [call]})
            elif kind in ("function_call_output", "custom_tool_call_output"):
                result.append({"role": "tool", "tool_call_id": str(item.get("call_id") or ""),
                               "content": _text(item.get("output") or "")})
            elif kind in (None, "message"):
                role = item.get("role")
                if role not in ("system", "developer", "user", "assistant"):
                    raise ValueError("Unsupported input message role")
                result.append({"role": role,
                               "content": _message_content(item.get("content") or "", role)})
            elif kind == "reasoning":
                continue
            else:
                raise ValueError(f"Unsupported Responses input item: {kind}")
    else:
        raise ValueError("'input' must be text or an array of input items")
    return result

def _tools(body: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = body.get("tools") or []
    if not isinstance(raw, list):
        raise ValueError("'tools' must be an array")
    tools = []
    for tool in raw:
        if not isinstance(tool, dict) or tool.get("type") not in ("function", "custom"):
            raise ValueError("Only client-owned function and custom tools are supported")
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("Function tools need a name")
        if tool["type"] == "custom":
            fmt = tool.get("format") or {}
            description = str(tool.get("description") or "")
            if isinstance(fmt, dict) and fmt.get("syntax") and fmt.get("definition"):
                description += ("\nInput grammar (" + str(fmt["syntax"]) + "): \n"
                                + str(fmt["definition"]))
            function = {"name": name, "description": description,
                        "parameters": {"type": "object", "properties": {"input": {"type": "string"}},
                                       "required": ["input"], "additionalProperties": False}}
        else:
            function = {"name": name, "description": str(tool.get("description") or ""),
                        "parameters": tool.get("parameters") or {"type": "object", "properties": {}}}
        if "strict" in tool:
            function["strict"] = tool["strict"]
        tools.append({"type": "function", "function": function})
    return tools

def _choice(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, str) and raw in ("auto", "none", "required"):
        return raw
    if isinstance(raw, dict) and raw.get("type") in ("function", "custom") and isinstance(raw.get("name"), str):
        return {"type": "function", "function": {"name": raw["name"]}}
    raise ValueError("Unsupported tool_choice")

def _message_added(event, message_id: str) -> List[str]:
    return [event("response.output_item.added", output_index=0,
                  item={"id": message_id, "type": "message",
                        "status": "in_progress", "role": "assistant", "content": []}),
            event("response.content_part.added", item_id=message_id,
                  output_index=0, content_index=0,
                  part={"type": "output_text", "text": "", "annotations": []})]

def _output(text: str, calls: Dict[int, Dict[str, Any]], response_id: str,
            custom_names: set[str]) -> List[Dict[str, Any]]:
    output = []
    if text.strip() or (text and not calls):

        output.append({"id": "msg_" + response_id[5:], "type": "message", "status": "completed",
                       "role": "assistant", "content": [{"type": "output_text", "text": text,
                                                          "annotations": []}]})
    for call in ordered_tool_calls(calls):
        name = call["function"]["name"]
        base = {"id": "fc_" + uuid.uuid4().hex[:24], "status": "completed",
                "call_id": call["id"], "name": name}
        if name in custom_names:
            try:
                parsed = json.loads(call["function"]["arguments"] or "{}")
            except (TypeError, ValueError) as exc:
                raise ValueError("Upstream returned invalid custom tool arguments") from exc
            if not isinstance(parsed, dict) or not isinstance(parsed.get("input"), str):
                raise ValueError("Upstream custom tool input must be a string")
            output.append({**base, "type": "custom_tool_call", "input": parsed["input"]})
        else:
            output.append({**base, "type": "function_call",
                           "arguments": call["function"]["arguments"]})
    return output

def _detail(usage: Dict[str, Any], group: str, key: str) -> int:
    details = usage.get(group)
    try:
        return max(0, int((details or {}).get(key) or 0)) if isinstance(details, dict) else 0
    except (TypeError, ValueError):
        return 0

def _response(response_id: str, model: str, output: List[Dict[str, Any]],
              usage: Dict[str, int], status: str = "completed",
              finish_reason: str = "") -> Dict[str, Any]:
    incomplete = None
    if status == "completed" and finish_reason == "length":

        status, incomplete = "incomplete", {"reason": "max_output_tokens"}
    return {"id": response_id, "object": "response", "created_at": int(time.time()),
            "status": status, "model": model, "output": output,
            "usage": {"input_tokens": usage.get("prompt_tokens", 0),

                      "input_tokens_details": {"cached_tokens": _detail(usage, "prompt_tokens_details",
                                                                        "cached_tokens")},
                      "output_tokens": usage.get("completion_tokens", 0),
                      "output_tokens_details": {"reasoning_tokens": _detail(usage, "completion_tokens_details",
                                                                            "reasoning_tokens")},
                      "total_tokens": usage.get("total_tokens", 0)},
            "error": None, "incomplete_details": incomplete, "parallel_tool_calls": True}

@router.post("/v1/responses")
async def responses(request: Request):
    return await until_client_disconnects(request, _responses(request))

async def _responses(request: Request):
    denied = _check_auth(request)
    if denied:
        return denied
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("Body must be a JSON object")
        model = body.get("model")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("'model' is required")
        if "stream" in body and not isinstance(body["stream"], bool):
            raise ValueError("'stream' must be boolean")
        messages = _messages(body)
        tools = _tools(body)
        custom_names = {str(tool["name"]) for tool in body.get("tools") or []
                        if isinstance(tool, dict) and tool.get("type") == "custom"}
        choice = _choice(body.get("tool_choice"))
        if choice not in (None, "none") and not tools:
            raise ValueError("tool_choice requires non-empty tools")
        if body.get("previous_response_id"):
            raise ValueError("previous_response_id is unsupported; send the full input history")
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return JSONResponse(status_code=400, content=_openai_error(
            str(exc), "invalid_request_error", 400))
    if (tools or any(message.get("role") == "tool" or message.get("tool_calls")
                     for message in messages)) and not tool_provider_supported(model):
        return JSONResponse(status_code=400, content=_openai_error(
            TOOL_PROVIDER_ERROR_RU, "unsupported_tools", 400, message_en=TOOL_PROVIDER_ERROR))

    params: Dict[str, Any] = {"_stream": bool(body.get("stream"))}
    if tools:
        params["tools"] = tools
        params["tools_available"] = True
        if choice is not None:
            params["tool_choice"] = choice
        if isinstance(body.get("parallel_tool_calls"), bool):
            params["parallel_tool_calls"] = body["parallel_tool_calls"]
    if isinstance(body.get("max_output_tokens"), int):
        params["max_tokens"] = body["max_output_tokens"]
    if isinstance(body.get("temperature"), (int, float)):
        params["temperature"] = body["temperature"]
    response_id = "resp_" + uuid.uuid4().hex[:24]
    trace = state.open_trace(model=model)
    trace.capture_prompt(messages, tools=params.get("tools"))
    source = chat_service.stream_completion(model, messages, params, trace=trace)

    if not params["_stream"]:
        text_parts: List[str] = []
        calls: Dict[int, Dict[str, Any]] = {}
        usage: Dict[str, int] = {}
        finish_reason = ""
        try:
            async for chunk in source:
                if chunk.get("object") == "memuai.event":
                    continue
                if chunk.get("usage"):
                    usage = chunk["usage"]
                    request.state.completion_usage = usage
                for choice_chunk in chunk.get("choices") or []:
                    delta = choice_chunk.get("delta") or {}
                    if delta.get("content"):
                        text_parts.append(delta["content"])
                    collect_tool_delta(calls, delta)
                    if choice_chunk.get("finish_reason"):
                        finish_reason = str(choice_chunk["finish_reason"])
        except asyncio.CancelledError:
            state.cancel_trace(trace, getattr(request.state, "completion_usage", None))
            raise
        except CompletionError as exc:
            _finish_trace(trace, exc.status, safe_provider_error(exc.message), exc.err_type)
            return JSONResponse(status_code=exc.status, content=_completion_error_payload(exc),
                            headers=exc.headers)
        except Exception as exc:
            _finish_trace(trace, 500, _unexpected_error(exc), "internal_error")
            return JSONResponse(status_code=500, content=_openai_error("Internal MemuAI error"))
        finally:
            await close_quietly(source.aclose)
        try:
            output = _output("".join(text_parts), calls, response_id, custom_names)
        except ValueError as exc:
            _finish_trace(trace, 502, str(exc), "upstream_error")
            return JSONResponse(status_code=502, content=_openai_error(
                str(exc), "upstream_error", 502))
        _finish_trace(trace, 200, usage=usage, commit=bool(config_store.settings.log_requests))
        if not config_store.settings.log_requests:
            state.discard_trace(trace)
        return _response(response_id, model, output, usage, finish_reason=finish_reason)

    async def filtered(source_stream):
        try:
            async for chunk in source_stream:
                if chunk.get("object") != "memuai.event":
                    yield chunk
        finally:
            await close_quietly(source_stream.aclose)

    stream = filtered(source)
    try:
        first = await anext(stream)
        if first.get("usage"):
            request.state.completion_usage = first["usage"]
    except asyncio.CancelledError:
        await close_quietly(stream.aclose)
        state.cancel_trace(trace, getattr(request.state, "completion_usage", None))
        raise
    except CompletionError as exc:
        _finish_trace(trace, exc.status, safe_provider_error(exc.message), exc.err_type)
        return JSONResponse(status_code=exc.status, content=_completion_error_payload(exc),
                            headers=exc.headers)
    except StopAsyncIteration:
        _finish_trace(trace, 502, "Provider closed without a response", "upstream_error")
        return JSONResponse(status_code=502, content=_openai_error(
            "Provider closed without a response", "upstream_error", 502))
    except Exception as exc:
        _finish_trace(trace, 500, _unexpected_error(exc), "internal_error")
        return JSONResponse(status_code=500, content=_openai_error(
            "Internal MemuAI error", "internal_error", 500))

    async def sse() -> AsyncIterator[str]:
        sequence = 0

        def event(kind: str, **fields: Any) -> str:
            nonlocal sequence
            sequence += 1
            obj = {"type": kind, "sequence_number": sequence, **fields}
            return f"event: {kind}\ndata: {json.dumps(obj, ensure_ascii=False)}\n\n"

        text_parts: List[str] = []
        calls: Dict[int, Dict[str, Any]] = {}
        usage: Dict[str, int] = {}
        finish_reason = ""
        message_started = False
        message_id = "msg_" + response_id[5:]
        try:
            yield event("response.created", response=_response(response_id, model, [], {}, "in_progress"))
            async def all_chunks():
                yield first
                async for chunk in stream:
                    yield chunk

            async for chunk in all_chunks():
                if chunk.get("usage"):
                    usage = chunk["usage"]
                    request.state.completion_usage = usage
                for choice_chunk in chunk.get("choices") or []:
                    delta = choice_chunk.get("delta") or {}
                    text_delta = delta.get("content")
                    if text_delta:
                        text_parts.append(text_delta)

                    if text_delta and (message_started or "".join(text_parts).strip()):
                        if not message_started:
                            message_started = True
                            text_delta = "".join(text_parts)
                            for line in _message_added(event, message_id):
                                yield line
                        yield event("response.output_text.delta", item_id=message_id,
                                    output_index=0, content_index=0, delta=text_delta)
                    collect_tool_delta(calls, delta)
                    if choice_chunk.get("finish_reason"):
                        finish_reason = str(choice_chunk["finish_reason"])
        except CompletionError as exc:
            _finish_trace(trace, exc.status, safe_provider_error(exc.message), exc.err_type, usage)
            yield event("response.failed", response={**_response(response_id, model, [], usage, "failed"),
                        "error": _completion_error_payload(exc)["error"]})
            return
        except Exception as exc:
            _finish_trace(trace, 500, _unexpected_error(exc), "internal_error", usage)
            yield event("response.failed", response={**_response(response_id, model, [], usage, "failed"),
                        "error": {"message": "Internal MemuAI error", "type": "internal_error"}})
            return
        finally:
            await close_quietly(stream.aclose)

        text = "".join(text_parts)
        try:
            output = _output(text, calls, response_id, custom_names)
        except ValueError:
            _finish_trace(trace, 502, "Upstream returned invalid tool arguments", "upstream_error", usage)
            yield event("response.failed", response={**_response(response_id, model, [], usage, "failed"),
                        "error": {"message": "Upstream returned invalid tool arguments",
                                  "type": "upstream_error"}})
            return
        if not message_started and output and output[0]["type"] == "message":
            message_started = True
            for line in _message_added(event, message_id):
                yield line
            yield event("response.output_text.delta", item_id=message_id,
                        output_index=0, content_index=0, delta=text)
        if message_started:
            item = output[0]
            yield event("response.output_text.done", item_id=message_id, output_index=0,
                        content_index=0, text=text)
            yield event("response.content_part.done", item_id=message_id, output_index=0,
                        content_index=0, part=item["content"][0])
            yield event("response.output_item.done", output_index=0, item=item)
        for index, item in enumerate(output[int(message_started):], start=int(message_started)):
            field = "input" if item["type"] == "custom_tool_call" else "arguments"
            pending = dict(item, status="in_progress", **{field: ""})
            yield event("response.output_item.added", output_index=index, item=pending)
            if item[field]:
                yield event("response.custom_tool_call_input.delta" if field == "input"
                            else "response.function_call_arguments.delta", item_id=item["id"],
                            output_index=index, delta=item[field])
            yield event("response.custom_tool_call_input.done" if field == "input"
                        else "response.function_call_arguments.done", item_id=item["id"],
                        output_index=index, **{field: item[field]})
            yield event("response.output_item.done", output_index=index, item=item)
        _finish_trace(trace, 200, usage=usage, commit=bool(config_store.settings.log_requests))
        if not config_store.settings.log_requests:
            state.discard_trace(trace)
        final = _response(response_id, model, output, usage, finish_reason=finish_reason)
        yield event("response.incomplete" if final["status"] == "incomplete" else "response.completed",
                    response=final)

    return ClosingStreamingResponse(state.guard_stream(sse(), trace), closer=stream.aclose, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
