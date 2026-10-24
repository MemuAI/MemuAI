"""Small native Messages/Gemini adapters at the OpenCode provider boundary."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any
from urllib.parse import quote

from ..config import config_store
from . import http
from .base import (ProviderError, ProviderNetworkError, ProviderServerError,
                   content_to_text, retry_after_seconds, safe_provider_error)


def _parts(content: Any, dialect: str) -> list:
    if isinstance(content, str):
        return [{"text": content}] if dialect == "gemini" else [{"type": "text", "text": content}]
    if content is None:
        return []
    if not isinstance(content, list):
        raise ProviderServerError("Message content must be text or content parts", 400)
    result = []
    for part in content:
        if not isinstance(part, dict):
            raise ProviderServerError("Message content parts must be objects", 400)
        kind = part.get("type")
        if kind in ("text", "input_text", "output_text"):
            text = str(part.get("text") or "")
            result.append({"text": text} if dialect == "gemini" else {"type": "text", "text": text})
        elif kind in ("image_url", "input_image"):
            raw = part.get("image_url")
            url = raw.get("url") if isinstance(raw, dict) else raw
            url = url or part.get("image_url") or part.get("url")
            if not isinstance(url, str):
                raise ProviderServerError("Image URL is missing", 400)
            if url.startswith("data:image/") and ";base64," in url:
                header, data = url.split(";base64,", 1)
                mime = header[5:]
                if dialect == "gemini":
                    result.append({"inlineData": {"mimeType": mime, "data": data}})
                else:
                    result.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}})
            elif dialect == "messages" and url.startswith(("http://", "https://")):
                result.append({"type": "image", "source": {"type": "url", "url": url}})
            else:
                # Gemini fileData expects an uploaded provider file, not an
                # arbitrary website URL. Never download a remote URL implicitly.
                raise ProviderServerError("This OpenCode model requires inline base64 images", 400)
        else:
            raise ProviderServerError(f"Unsupported {dialect} content type: {kind}", 400)
    return result


def _arguments(value: Any) -> dict:
    try:
        parsed = json.loads(value or "{}") if isinstance(value, str) else value or {}
    except (TypeError, ValueError) as exc:
        raise ProviderServerError("Tool arguments must be JSON", 400) from exc
    if not isinstance(parsed, dict):
        raise ProviderServerError("Tool arguments must be a JSON object", 400)
    return parsed


def request_body(model: str, messages: list, params: dict, dialect: str) -> dict:
    systems = [content_to_text(m.get("content")) for m in messages if m.get("role") in ("system", "developer")]
    result = []
    calls = {str(call.get("id")): str((call.get("function") or {}).get("name") or "")
             for message in messages for call in message.get("tool_calls") or [] if isinstance(call, dict)}
    for message in messages:
        role = message.get("role")
        if role in ("system", "developer"):
            continue
        if role == "tool":
            if dialect == "gemini":
                call_id = str(message.get("tool_call_id") or "")
                block = {"functionResponse": {"name": calls.get(call_id) or "tool",
                                               "response": {"result": content_to_text(message.get("content"))}}}
                saved = (params.get("_opencode_signature_cache") or {}).get(call_id) or {}
                if saved.get("model") == model and saved.get("native_id"):
                    block["functionResponse"]["id"] = saved["native_id"]
                result.append({"role": "user", "parts": [block]})
            else:
                result.append({"role": "user", "content": [{"type": "tool_result",
                               "tool_use_id": str(message.get("tool_call_id") or ""),
                               "content": content_to_text(message.get("content"))}]})
            continue
        if role not in ("user", "assistant"):
            raise ProviderServerError("Unsupported conversation role", 400)
        parts = _parts(message.get("content"), dialect)
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            name = str(function.get("name") or "")
            arguments = _arguments(function.get("arguments"))
            if dialect == "gemini":
                block = {"functionCall": {"name": name, "args": arguments}}
                saved = (params.get("_opencode_signature_cache") or {}).get(str(call.get("id") or "")) or {}
                if saved.get("model") == model and saved.get("name") == name and saved.get("args") == arguments:
                    if saved.get("signature"):
                        block["thoughtSignature"] = saved["signature"]
                    if saved.get("native_id"):
                        block["functionCall"]["id"] = saved["native_id"]
                parts.append(block)
            else:
                parts.append({"type": "tool_use", "id": str(call.get("id") or ""), "name": name, "input": arguments})
        if not parts:
            continue
        mapped_role = "model" if dialect == "gemini" and role == "assistant" else role
        key = "parts" if dialect == "gemini" else "content"
        # Consecutive tool results belong to one user turn. This also satisfies
        # providers that require alternating user/assistant messages.
        if result and result[-1]["role"] == mapped_role:
            result[-1][key].extend(parts)
        else:
            result.append({"role": mapped_role, key: parts})
    if dialect == "gemini":
        body = {"contents": result, "generationConfig": {}}
        if systems:
            body["systemInstruction"] = {"parts": [{"text": "\n\n".join(systems)}]}
        for source, target in (("temperature", "temperature"), ("max_tokens", "maxOutputTokens"), ("top_p", "topP"), ("top_k", "topK")):
            if params.get(source) is not None:
                body["generationConfig"][target] = params[source]
        tools = [{"name": tool["function"]["name"], "description": tool["function"].get("description", ""),
                  "parameters": tool["function"].get("parameters") or {"type": "object", "properties": {}}}
                 for tool in params.get("tools") or [] if tool.get("type") == "function" and isinstance(tool.get("function"), dict)]
        if tools:
            body["tools"] = [{"functionDeclarations": tools}]
        choice = params.get("tool_choice")
        if choice is not None:
            config = {"mode": {"auto": "AUTO", "none": "NONE", "required": "ANY"}.get(choice, "AUTO")} if isinstance(choice, str) else {
                "mode": "ANY", "allowedFunctionNames": [str((choice.get("function") or {}).get("name") or "")]}
            body["toolConfig"] = {"functionCallingConfig": config}
        return body
    body = {"model": model, "messages": result, "stream": True,
            "max_tokens": params.get("max_tokens") or 4096}
    if systems:
        body["system"] = "\n\n".join(systems)
    for field in ("temperature", "top_p", "top_k"):
        if params.get(field) is not None:
            body[field] = params[field]
    tools = [{"name": tool["function"]["name"], "description": tool["function"].get("description", ""),
              "input_schema": tool["function"].get("parameters") or {"type": "object", "properties": {}}}
             for tool in params.get("tools") or [] if tool.get("type") == "function" and isinstance(tool.get("function"), dict)]
    if tools:
        body["tools"] = tools
    choice = params.get("tool_choice")
    if choice is not None:
        body["tool_choice"] = {"type": {"required": "any"}.get(choice, choice)} if isinstance(choice, str) else {
            "type": "tool", "name": str((choice.get("function") or {}).get("name") or "")}
    return body


async def _objects(response):
    """Read bounded SSE frames or a non-streaming JSON response."""
    content_type = str(getattr(response, "headers", {}).get("content-type", ""))
    lines = http.aiter_lines_str(response, idle_timeout=config_store.settings.stream_idle_timeout)
    if "application/json" in content_type and "text/event-stream" not in content_type:
        chunks = []
        size = 0
        async for line in lines:
            size += len(line)
            if size > 4_194_304:
                raise ProviderServerError("The upstream JSON response exceeds 4 MB")
            chunks.append(line)
        try:
            parsed = json.loads("\n".join(chunks))
        except ValueError as exc:
            raise ProviderServerError("OpenCode returned invalid JSON") from exc
        for value in parsed if isinstance(parsed, list) else [parsed]:
            if isinstance(value, dict):
                yield value
        return
    frame = []
    size = 0
    async for line in lines:
        if line.startswith("data:"):
            frame.append(line[5:].lstrip())
            size += len(line)
            if size > 4_194_304:
                raise ProviderServerError("OpenCode SSE frame exceeds 4 MB")
        elif not line.strip() and frame:
            raw = "\n".join(frame)
            frame, size = [], 0
            if raw.strip() == "[DONE]":
                return
            try:
                value = json.loads(raw)
            except ValueError as exc:
                raise ProviderServerError("OpenCode returned invalid SSE JSON") from exc
            if isinstance(value, dict):
                yield value
    if frame:
        raw = "\n".join(frame)
        if raw.strip() != "[DONE]":
            try:
                value = json.loads(raw)
            except ValueError as exc:
                raise ProviderServerError("OpenCode returned invalid SSE JSON") from exc
            if isinstance(value, dict):
                yield value


async def stream(provider, account, model, row, messages, params, headers):
    dialect = row["protocol"]
    body = request_body(model, messages, params, dialect)
    url = row["api"] + ("/models/" + quote(model, safe="") + ":streamGenerateContent?alt=sse" if dialect == "gemini" else "/messages")
    if dialect == "messages":
        headers = {**headers, "anthropic-version": "2023-06-01"}
    response = None
    counts = {"prompt_tokens": 0, "completion_tokens": 0}
    saw_output = completed = False
    tool_index = 0
    try:
        session = await http.get_session(provider.proxy_for(account), impersonate=False, url=url)
        response = await session.post(url, headers=headers, json=body, stream=True,
                                      timeout=config_store.settings.request_timeout, allow_redirects=False)
        if response.status_code != 200:
            head = ""
            async for part in response.aiter_content():
                head += part.decode("utf-8", "replace") if isinstance(part, bytes) else str(part)
                if len(head) > 3000:
                    break
            await provider._raise_for_status(response.status_code, safe_provider_error(head, account), retry_after=retry_after_seconds(response.headers))
        async for value in _objects(response):
            if value.get("error"):
                error = value["error"]
                status = error.get("code", 502) if isinstance(error, dict) else 502
                if isinstance(error, dict):
                    status = {"authentication_error": 401, "rate_limit_error": 429,
                              "permission_error": 403, "overloaded_error": 503}.get(error.get("type"), status)
                await provider._raise_for_status(status if isinstance(status, int) else 502, safe_provider_error(json.dumps(value), account))
            if dialect == "gemini":
                usage = value.get("usageMetadata") or {}
                if usage:
                    counts.update(prompt_tokens=int(usage.get("promptTokenCount") or 0),
                                  completion_tokens=int(usage.get("candidatesTokenCount") or 0) + int(usage.get("thoughtsTokenCount") or 0),
                                  cached_tokens=int(usage.get("cachedContentTokenCount") or 0),
                                  reasoning_tokens=int(usage.get("thoughtsTokenCount") or 0))
                for candidate in value.get("candidates") or []:
                    for part in (candidate.get("content") or {}).get("parts") or []:
                        if part.get("text"):
                            saw_output = True
                            yield {"type": "reasoning" if part.get("thought") else "text", "text": part["text"]}
                        function = part.get("functionCall")
                        if isinstance(function, dict):
                            saw_output = True
                            call_id = function.get("id") or "call_oc_" + uuid.uuid4().hex[:24]
                            signature = part.get("thoughtSignature")
                            ledger = params.get("_opencode_signature_cache")
                            if isinstance(ledger, dict):
                                # Parallel Gemini calls may sign only the first
                                # part; every call's native ID still has to make
                                # the next functionCall/functionResponse trip.
                                signature = signature if isinstance(signature, str) and len(signature) <= 262144 else ""
                                ledger[call_id] = {"model": model, "name": function.get("name"), "args": function.get("args") or {},
                                                   "signature": signature, "native_id": function.get("id"), "at": time.time()}
                                sizes = {key: len(json.dumps(item, ensure_ascii=False)) for key, item in ledger.items()}
                                total = sum(sizes.values())
                                while len(ledger) > 256 or total > 4_194_304:
                                    oldest = next(iter(ledger))
                                    ledger.pop(oldest)
                                    total -= sizes[oldest]
                            yield {"type": "tool_call", "index": tool_index, "id": call_id,
                                   "name": function.get("name"), "arguments": json.dumps(function.get("args") or {}, ensure_ascii=False)}
                            tool_index += 1
                    if candidate.get("finishReason"):
                        completed = True
                        reason = candidate["finishReason"]
                        if reason == "MAX_TOKENS":
                            yield {"type": "finish", "reason": "length"}
                        elif reason not in ("STOP", "FINISH_REASON_UNSPECIFIED"):
                            raise ProviderServerError("OpenCode Gemini stopped: " + str(reason), 400)
                continue
            kind = value.get("type")
            message = value.get("message") if kind == "message_start" else value
            usage = (message or {}).get("usage") or {}
            if usage:
                if "input_tokens" in usage:
                    read, written = int(usage.get("cache_read_input_tokens") or 0), int(usage.get("cache_creation_input_tokens") or 0)
                    counts.update(prompt_tokens=int(usage.get("input_tokens") or 0) + read + written,
                                  cached_tokens=read, cache_write_tokens=written)
                if "output_tokens" in usage:
                    counts["completion_tokens"] = int(usage["output_tokens"] or 0)
            if kind == "content_block_start":
                block = value.get("content_block") or {}
                index = int(value.get("index") or 0)
                if block.get("type") == "tool_use":
                    saw_output = True
                    yield {"type": "tool_call", "index": index, "id": block.get("id"), "name": block.get("name"),
                           "arguments": json.dumps(block["input"]) if block.get("input") else ""}
                elif block.get("type") in ("text", "thinking") and (block.get("text") or block.get("thinking")):
                    saw_output = True
                    yield {"type": "reasoning" if block["type"] == "thinking" else "text", "text": block.get("text") or block["thinking"]}
            elif kind == "content_block_delta":
                delta = value.get("delta") or {}
                if delta.get("type") == "input_json_delta":
                    saw_output = True
                    yield {"type": "tool_call", "index": int(value.get("index") or 0), "arguments": str(delta.get("partial_json") or "")}
                elif delta.get("type") in ("text_delta", "thinking_delta"):
                    saw_output = True
                    yield {"type": "reasoning" if delta["type"] == "thinking_delta" else "text", "text": str(delta.get("text") or delta.get("thinking") or "")}
            elif kind == "message_delta":
                reason = (value.get("delta") or {}).get("stop_reason")
                if reason in ("tool_use", "max_tokens"):
                    yield {"type": "finish", "reason": "tool_calls" if reason == "tool_use" else "length"}
            elif kind == "message_stop":
                completed = True
            elif kind == "message":
                for block in value.get("content") or []:
                    if block.get("type") == "tool_use":
                        saw_output = True
                        yield {"type": "tool_call", "index": tool_index, "id": block.get("id"), "name": block.get("name"),
                               "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False)}
                        tool_index += 1
                    elif block.get("type") in ("text", "thinking"):
                        saw_output = True
                        yield {"type": "reasoning" if block["type"] == "thinking" else "text", "text": str(block.get("text") or block.get("thinking") or "")}
                completed = True
                if value.get("stop_reason") in ("tool_use", "max_tokens"):
                    yield {"type": "finish", "reason": "tool_calls" if value["stop_reason"] == "tool_use" else "length"}
        if not saw_output:
            raise ProviderServerError("OpenCode closed the stream without an answer")
        if not completed:
            raise ProviderServerError("The OpenCode stream broke off before completion")
        yield {"type": "usage", **counts}
    except (ProviderError, asyncio.CancelledError, GeneratorExit):
        raise
    except Exception as exc:
        raise ProviderNetworkError("The OpenCode stream broke off: " + type(exc).__name__) from exc
    finally:
        if response is not None:
            await http.close_response(response)
