"""Responses upstream dialect for the Custom API provider.

The rest of the Proxy speaks Chat Completions. Keep this conversion at the
provider boundary so chat, CLI, schedules and external API clients share the
same routing, cancellation, key rotation and usage accounting.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .base import ProviderServerError


def _content(raw: Any, role: str) -> Any:
    if isinstance(raw, str):
        return raw
    if raw is None:
        return ""
    if not isinstance(raw, list):
        raise ProviderServerError("Responses message content must be text or content parts", 400)
    parts = []
    for part in raw:
        if not isinstance(part, dict):
            raise ProviderServerError("Responses message content parts must be objects", 400)
        kind = part.get("type")
        if kind in ("text", "input_text", "output_text"):
            parts.append({"type": "output_text" if role == "assistant" else "input_text",
                          "text": str(part.get("text") or "")})
        elif kind in ("image_url", "input_image"):
            image = part.get("image_url")
            image = image if isinstance(image, dict) else {"url": image}
            converted = {"type": "input_image", "image_url": image.get("url")}
            if image.get("detail") or part.get("detail"):
                converted["detail"] = image.get("detail") or part["detail"]
            parts.append(converted)
        elif kind in ("file", "input_file"):
            file = part.get("file") if kind == "file" else part
            if not isinstance(file, dict):
                raise ProviderServerError("Responses file content must be an object", 400)
            parts.append({"type": "input_file", **{key: file[key] for key in
                          ("file_id", "file_data", "file_url", "filename") if key in file}})
        elif kind == "refusal" and role == "assistant":
            parts.append({"type": "refusal", "refusal": str(part.get("refusal") or "")})
        else:
            raise ProviderServerError(f"Unsupported Responses content type: {kind}", 400)
    return parts


def request_body(chat: Dict[str, Any]) -> Dict[str, Any]:
    """Convert an internally built Chat Completions body without mutating it.

    Stateless by design: the application's history is authoritative and no
    provider conversation is persisted on the user's behalf.
    """
    items: List[Dict[str, Any]] = []
    for message in chat.get("messages") or []:
        role = message.get("role")
        if role == "tool":
            items.append({"type": "function_call_output", "call_id": message.get("tool_call_id"),
                          "output": _content(message.get("content"), "tool")})
            continue
        if role not in ("system", "developer", "user", "assistant"):
            raise ProviderServerError(f"Unsupported Responses message role: {role}", 400)
        content = _content(message.get("content"), role)
        if content or not message.get("tool_calls"):
            items.append({"role": role, "content": content})
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            items.append({"type": "function_call", "call_id": call.get("id"),
                          "name": function.get("name"), "arguments": function.get("arguments") or "{}"})
    body: Dict[str, Any] = {"model": chat["model"], "input": items,
                            "stream": bool(chat.get("stream")), "store": False}
    for key in ("temperature", "top_p", "parallel_tool_calls", "prompt_cache_key"):
        if key in chat:
            body[key] = chat[key]
    if "max_tokens" in chat:
        body["max_output_tokens"] = chat["max_tokens"]
    tools = chat.get("tools")
    if tools:
        converted = []
        for tool in tools:
            if tool.get("type") != "function" or not isinstance(tool.get("function"), dict):
                raise ProviderServerError("Responses upstream supports function tools only", 400)
            function = tool["function"]
            converted.append({"type": "function", **{key: function[key] for key in
                              ("name", "description", "parameters", "strict") if key in function}})
        body["tools"] = converted
    choice = chat.get("tool_choice")
    if isinstance(choice, dict) and choice.get("type") == "function":
        choice = {"type": "function", "name": (choice.get("function") or {}).get("name")}
    if choice is not None:
        body["tool_choice"] = choice
    if chat.get("reasoning_effort"):
        body["reasoning"] = {"effort": chat["reasoning_effort"], "summary": "auto"}
    elif isinstance(chat.get("reasoning"), dict):
        body["reasoning"] = {key: value for key, value in chat["reasoning"].items()
                             if key in ("effort", "summary", "generate_summary")}
    fmt = chat.get("response_format")
    if isinstance(fmt, dict):
        if fmt.get("type") == "json_schema":
            body["text"] = {"format": {"type": "json_schema", **(fmt.get("json_schema") or {})}}
        else:
            body["text"] = {"format": dict(fmt)}
    return body


def _chunk(delta: Dict[str, Any] | None = None, *, finish: str | None = None,
           usage: Dict[str, Any] | None = None) -> Dict[str, Any]:
    obj: Dict[str, Any] = {"choices": [{"delta": delta or {}, "finish_reason": finish}]}
    if usage is not None:
        obj["usage"] = {"prompt_tokens": usage.get("input_tokens", 0),
                        "completion_tokens": usage.get("output_tokens", 0),
                        "prompt_tokens_details": usage.get("input_tokens_details") or {},
                        "completion_tokens_details": usage.get("output_tokens_details") or {}}
        if isinstance(usage.get("cost"), (int, float)):
            obj["usage"]["cost"] = usage["cost"]
    return obj


class StreamAdapter:
    """Convert Responses SSE/JSON into the existing provider's chunk parser.

    Done events contain the complete text/arguments again. Emit only the
    missing suffix, including on gateways which skip deltas altogether.
    """

    def __init__(self) -> None:
        self.calls: Dict[int, Dict[str, Any]] = {}
        self.texts: Dict[tuple, str] = {}
        self.terminal = False

    def _text(self, key: tuple, value: Any, *, final: bool = False,
              reasoning: bool = False) -> List[Dict[str, Any]]:
        value = str(value or "")
        old = self.texts.get(key, "")
        if final:
            # A conflicting snapshot must not duplicate already emitted data.
            delta = value[len(old):] if value.startswith(old) else (value if not old else "")
            self.texts[key] = value
        else:
            delta = value
            self.texts[key] = old + value
        return [_chunk({"reasoning_content" if reasoning else "content": delta})] if delta else []

    def _call(self, index: int, item: Dict[str, Any], *, final: bool = False) -> List[Dict[str, Any]]:
        state = self.calls.setdefault(index, {"slot": len(self.calls), "args": "", "id": "", "name": ""})
        delta: Dict[str, Any] = {"index": state["slot"], "function": {}}
        for source, target in (("call_id", "id"), ("name", "name")):
            value = str(item.get(source) or "")
            if value and not state[target]:
                state[target] = value
                if target == "id":
                    delta["id"], delta["type"] = value, "function"
                else:
                    delta["function"]["name"] = value
        if "arguments" in item:
            value = str(item.get("arguments") or "")
            old = state["args"]
            addition = value[len(old):] if final and value.startswith(old) else ("" if final and old else value)
            if addition:
                delta["function"]["arguments"] = addition
                state["args"] = old + addition
        return [_chunk({"tool_calls": [delta]})] if delta.get("id") or delta["function"] else []

    def _item(self, index: int, item: Dict[str, Any]) -> List[Dict[str, Any]]:
        out = []
        if item.get("type") == "function_call":
            return self._call(index, item, final=True)
        if item.get("type") == "message":
            for ci, part in enumerate(item.get("content") or []):
                if isinstance(part, dict) and part.get("type") in ("output_text", "refusal"):
                    kind = part["type"]
                    out.extend(self._text((index, ci, kind), part.get("text") if kind == "output_text"
                                          else part.get("refusal"), final=True))
        if item.get("type") == "reasoning":
            for si, part in enumerate(item.get("summary") or []):
                if isinstance(part, dict):
                    out.extend(self._text((index, si, "summary"), part.get("text"), final=True, reasoning=True))
        return out

    def feed(self, obj: Dict[str, Any]) -> List[Dict[str, Any]]:
        if self.terminal:
            return []
        kind = str(obj.get("type") or "")
        index = obj.get("output_index", 0)
        if not isinstance(index, int):
            index = 0
        if obj.get("error") and not kind.startswith("response."):
            self.terminal = True
            return [obj]
        if kind == "error":
            self.terminal = True
            error = {key: obj[key] for key in ("message", "code", "status") if key in obj}
            # Here `type` names the SSE event, not the error classification.
            error["type"] = obj.get("code") or "server_error"
            return [{"error": error}]
        if kind in ("response.output_text.delta", "response.refusal.delta",
                    "response.reasoning_summary_text.delta", "response.reasoning_text.delta"):
            reasoning = "reasoning" in kind
            content_kind = "summary" if reasoning else ("refusal" if "refusal" in kind else "output_text")
            ci = obj.get("summary_index", 0) if reasoning else obj.get("content_index", 0)
            return self._text((index, ci, content_kind), obj.get("delta"), reasoning=reasoning)
        if kind in ("response.output_text.done", "response.refusal.done", "response.reasoning_summary_text.done"):
            reasoning = "reasoning" in kind
            content_kind = "summary" if reasoning else ("refusal" if "refusal" in kind else "output_text")
            ci = obj.get("summary_index", 0) if reasoning else obj.get("content_index", 0)
            return self._text((index, ci, content_kind), obj.get("refusal") if "refusal" in kind
                              else obj.get("text"), final=True, reasoning=reasoning)
        if kind == "response.function_call_arguments.delta":
            return self._call(index, {"arguments": obj.get("delta") or ""})
        if kind == "response.function_call_arguments.done":
            return self._call(index, {"arguments": obj.get("arguments") or ""}, final=True)
        if kind == "response.output_item.added" and isinstance(obj.get("item"), dict):
            item = obj["item"]
            return self._call(index, item) if item.get("type") == "function_call" else []
        if kind == "response.output_item.done" and isinstance(obj.get("item"), dict):
            return self._item(index, obj["item"])
        if kind in ("response.completed", "response.incomplete", "response.failed", "response.cancelled") \
                or obj.get("object") == "response":
            response = obj.get("response") if kind else obj
            if not isinstance(response, dict):
                raise ProviderServerError("Responses terminal event has no response object")
            self.terminal = True
            status = response.get("status")
            if response.get("error") or status in ("failed", "cancelled"):
                return [{"error": response.get("error") or {"message": f"Responses upstream {status}"}}]
            if status not in ("completed", "incomplete"):
                raise ProviderServerError(f"Responses upstream returned a non-terminal response: {status}")
            out = []
            for oi, item in enumerate(response.get("output") or []):
                if isinstance(item, dict):
                    out.extend(self._item(oi, item))
            reason = "tool_calls" if self.calls else "stop"
            if status == "incomplete":
                details = response.get("incomplete_details") or {}
                reason = "length" if details.get("reason") == "max_output_tokens" else "content_filter"
            out.append(_chunk(finish=reason, usage=response.get("usage")))
            return out
        return []

    def require_terminal(self) -> None:
        if not self.terminal:
            raise ProviderServerError("The upstream Responses stream ended before a terminal response event")
