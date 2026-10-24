"""Local models: GGUF files on this computer, served by llama.cpp.

The account «Локальные модели» is created automatically with the first model.
Each model gets its own ``llama-server`` process, started on the first request
and unloaded after idle time (see app/local_models.py). Requests are forwarded
to that process's OpenAI-compatible endpoint; nothing leaves the computer.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import httpx

from .. import local_models
from .base import (Provider, ProviderError, ProviderModelUnavailableError, ProviderServerError,
                   messages_have_images)


class LocalProvider(Provider):
    id = "local"
    title = "Локальные модели"
    title_en = "Local models"
    icon = "▣"
    description = "Модели в формате GGUF на вашем компьютере: работают без интернета и без аккаунтов."
    description_en = "GGUF models on your computer: they work offline and need no account."
    #: The panel manages this provider on its own tab instead of a form.
    managed = True

    def validate_fields(self, fields: Dict[str, Any]) -> Optional[str]:
        return None

    async def dynamic_models(self, account) -> Optional[List[str]]:
        return [row["id"] for row in local_models.list_models() if row["settings"].get("enabled", True)]

    async def check(self, account) -> Tuple[Optional[bool], str]:
        runtime = local_models.runtime_status()
        models = [row for row in local_models.list_models() if row["exists"]]
        if not runtime["installed"]:
            return False, "Движок llama.cpp не установлен — вкладка «Локальные модели»"
        if not models:
            return False, "Нет ни одной модели — добавьте её на вкладке «Локальные модели»"
        return True, f"Моделей: {len(models)} · llama.cpp {runtime['tag'] or 'свой'}"

    async def usage(self, account) -> Optional[Dict[str, Any]]:
        loaded = [row for row in local_models.list_models() if row["state"] == "ready"]
        return {"loaded": [row["settings"].get("name") or row["id"] for row in loaded]}

    async def chat_stream(self, account, model: str, messages: List[Dict[str, Any]],
                          params: Dict[str, Any]) -> AsyncIterator[Dict[str, Any]]:
        info = local_models.get_model(model)
        if not info:
            raise ProviderModelUnavailableError(f"Local model “{model}” is not installed", model,
                                                message_ru=f"Локальная модель «{model}» не установлена")
        settings = info["settings"]
        if messages_have_images(messages) and not settings.get("mmproj"):
            raise ProviderServerError(
                "This local model cannot see images: give it a projector (mmproj) in its settings "
                "or remove the picture.", 400,
                message_ru="Эта локальная модель не видит изображения: добавьте ей проектор (mmproj) в настройках "
                           "или уберите картинку.")
        try:
            port = await asyncio.to_thread(local_models.pool.ensure, model)
        except local_models.LocalModelError as exc:
            # A model that cannot start fails the same way on every retry.
            # 422 ends the request at once instead of repeating the same
            # failing start three times through the shared «Локальные модели»
            # account.
            raise ProviderServerError(exc.message_en, 422, message_ru=exc.message) from exc

        body_messages = list(messages)
        system_prompt = str(settings.get("system_prompt") or "").strip()
        if system_prompt and not any(m.get("role") in ("system", "developer") for m in body_messages):
            body_messages.insert(0, {"role": "system", "content": system_prompt})
        # include_usage: llama-server then reports its real token counts in the
        # last chunk (older builds ignore the field), so the Proxy's usage is not
        # a length-based guess for a model whose tokenizer is known exactly.
        body: Dict[str, Any] = {"model": f"local-{model}", "messages": body_messages, "stream": True,
                                "stream_options": {"include_usage": True}}
        for key in ("temperature", "top_p", "max_tokens"):
            value = params.get(key) if params.get(key) is not None else settings.get(key)
            if value is not None:
                body[key] = value
        for key in ("top_k", "min_p", "repeat_penalty"):
            if settings.get(key) is not None:
                body[key] = settings[key]
        # Round 29: explicit samplers from a module request win over the local defaults.
        for key, dst in (("top_k", "top_k"), ("min_p", "min_p"), ("repetition_penalty", "repeat_penalty"),
                         ("frequency_penalty", "frequency_penalty"), ("presence_penalty", "presence_penalty"),
                         ("seed", "seed"), ("stop", "stop")):
            if params.get(key) is not None and params.get(key) != []:
                body[dst] = params[key]
        # Round 33: native function calling. llama-server runs with --jinja, so the model's own
        # chat template formats tools and its parser returns OpenAI tool_calls.
        for key in ("tools", "tool_choice", "parallel_tool_calls"):
            if params.get(key) is not None and params.get(key) != []:
                body[key] = params[key]
        # Round 33 (r33-thinking): the level already clamped to the model family (off / on / low…).
        kwargs = template_kwargs(params.get("_reasoning_level") or params.get("effort"))
        if kwargs:
            body["chat_template_kwargs"] = kwargs

        url = f"http://127.0.0.1:{port}/v1/chat/completions"
        timeout = httpx.Timeout(10.0, read=600.0)
        try:
            # The local llama-server is on 127.0.0.1: never route it through a system/VPN proxy.
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                async with client.stream("POST", url, json=body) as response:
                    if response.status_code != 200:
                        text = (await response.aread()).decode("utf-8", errors="replace")[:600]
                        raise http_error(response.status_code, text, int(settings.get("context") or 0))
                    thinking = ThinkSplitter()
                    tool_slots = {"next": 0, "last": 0}
                    async for line in response.aiter_lines():
                        local_models.pool.touch(model)
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            for event in thinking.flush():
                                yield event
                            return
                        try:
                            obj = json.loads(payload)
                        except ValueError:
                            continue
                        if not isinstance(obj, dict):
                            continue
                        counted = obj.get("usage")
                        if isinstance(counted, dict) and counted.get("prompt_tokens"):
                            yield {"type": "usage", "prompt_tokens": counted.get("prompt_tokens"),
                                   "completion_tokens": counted.get("completion_tokens")}
                        if obj.get("error"):
                            error = obj["error"]
                            text = error.get("message") if isinstance(error, dict) else str(error)
                            raise http_error(502, json.dumps({"error": error}) if isinstance(error, dict) else text,
                                             int(settings.get("context") or 0))
                        for choice in obj.get("choices") or []:
                            if not isinstance(choice, dict):
                                continue
                            delta = choice.get("delta") or {}
                            if delta.get("reasoning_content"):
                                thinking.native = True
                                yield {"type": "reasoning", "text": str(delta["reasoning_content"])}
                            if delta.get("content"):
                                # Builds or templates that do not parse reasoning leave <think>…</think>
                                # in the text: split it out so the answer stays clean.
                                for event in thinking.feed(str(delta["content"])):
                                    yield event
                            for event in tool_call_events(delta.get("tool_calls"), tool_slots):
                                yield event
                            reason = choice.get("finish_reason")
                            if reason:
                                for event in thinking.flush():
                                    yield event
                            if reason and reason != "stop":
                                yield {"type": "finish", "reason": str(reason)}
        except ProviderError:
            raise
        except httpx.HTTPError as exc:
            raise ProviderServerError(f"The connection to llama-server broke off: {type(exc).__name__}: {exc}",
                                      message_ru=f"Связь с llama-server оборвалась: {type(exc).__name__}: {exc}") from exc


# --------------------------------------------------------------------------
# Round 33 helpers (pure functions; tested in tests/round33_local_test.py)
# --------------------------------------------------------------------------

def template_kwargs(effort: Any) -> Dict[str, Any]:
    """chat_template_kwargs for a requested reasoning effort.

    Templates ignore keys they do not know: Qwen3.x reads ``enable_thinking`` and
    ``reasoning_effort``, gpt-oss reads ``reasoning_effort``, Gemma/GLM ``enable_thinking``.
    """
    effort = str(effort or "").lower()
    if effort in ("none", "minimal", "off"):
        return {"enable_thinking": False, "reasoning_effort": "low"}
    if effort in ("low", "medium", "high"):
        return {"enable_thinking": True, "reasoning_effort": effort}
    if effort == "on":
        return {"enable_thinking": True}
    if effort in ("max", "xhigh"):
        return {"enable_thinking": True, "reasoning_effort": "high"}
    return {}


def tool_call_events(parts: Any, slots: Dict[str, int]) -> List[Dict[str, Any]]:
    """Streamed tool_calls of llama-server as provider events (index kept stable)."""
    events: List[Dict[str, Any]] = []
    for part in parts or []:
        if not isinstance(part, dict):
            continue
        function = part.get("function") or {}
        if not isinstance(function, dict):
            continue
        index = part.get("index")
        if not isinstance(index, int):
            index = slots["next"] if (part.get("id") is not None or function.get("name")) else slots["last"]
        slots["last"] = index
        slots["next"] = max(slots["next"], index + 1)
        event = {"type": "tool_call", "index": index, "id": part.get("id"), "name": function.get("name"),
                 "arguments": function.get("arguments")}
        if any(event.get(key) is not None for key in ("id", "name", "arguments")):
            events.append(event)
    return events


class ThinkSplitter:
    """Moves ``<think>…</think>`` (and ``◁think▷…◁/think▷``) from streamed text into reasoning events.

    Only used when the server did not parse reasoning itself (``native`` stays False). A tag split
    across chunks is held back until it is complete; an unclosed block at the end is reasoning.
    """

    OPEN = ("<think>", "◁think▷")
    CLOSE = ("</think>", "◁/think▷")

    def __init__(self) -> None:
        self.native = False
        self.inside = False
        self.started = False       # any visible text yet (a think block counts only at the start)
        self.buffer = ""

    def _partial(self, text: str, tags) -> int:
        """Length of a suffix of ``text`` that may be the start of one of ``tags``."""
        best = 0
        for tag in tags:
            for size in range(min(len(tag) - 1, len(text)), 0, -1):
                if text.endswith(tag[:size]):
                    best = max(best, size)
                    break
        return best

    def feed(self, text: str) -> List[Dict[str, Any]]:
        if self.native:
            return [{"type": "text", "text": text}] if text else []
        self.buffer += text
        events: List[Dict[str, Any]] = []
        while self.buffer:
            if self.inside:
                ends = [(self.buffer.find(tag), tag) for tag in self.CLOSE if tag in self.buffer]
                if ends:
                    at, tag = min(ends)
                    if at:
                        events.append({"type": "reasoning", "text": self.buffer[:at]})
                    self.buffer = self.buffer[at + len(tag):].lstrip("\n")
                    self.inside = False
                    continue
                keep = self._partial(self.buffer, self.CLOSE)
                chunk, self.buffer = self.buffer[:len(self.buffer) - keep], self.buffer[len(self.buffer) - keep:]
                if chunk:
                    events.append({"type": "reasoning", "text": chunk})
                break
            if not self.started:
                stripped = self.buffer.lstrip()
                opened = next((tag for tag in self.OPEN if stripped.startswith(tag)), None)
                if opened:
                    self.buffer = stripped[len(opened):]
                    self.inside = True
                    continue
                if stripped and any(tag.startswith(stripped) for tag in self.OPEN):
                    break          # maybe "<thi" of a tag still arriving
                if not stripped:
                    break
            self.started = True
            events.append({"type": "text", "text": self.buffer})
            self.buffer = ""
        return events

    def flush(self) -> List[Dict[str, Any]]:
        if not self.buffer:
            return []
        kind = "reasoning" if self.inside else "text"
        text, self.buffer = self.buffer, ""
        return [{"type": kind, "text": text}]


def http_error(status: int, text: str, context: int = 0) -> ProviderServerError:
    """A readable error for a failed llama-server request (context overflow explained)."""
    detail = text
    n_prompt = n_ctx = 0
    try:
        data = json.loads(text)
        error = data.get("error") if isinstance(data, dict) else None
        if isinstance(error, dict):
            detail = str(error.get("message") or text)
            n_prompt = int(error.get("n_prompt_tokens") or 0)
            n_ctx = int(error.get("n_ctx") or 0)
            if error.get("type") == "exceed_context_size_error":
                detail = "exceed_context_size_error"
    except (ValueError, TypeError):
        pass
    low = detail.lower()
    if "exceed_context_size" in low or "exceeds the available context size" in low or (
            "context size" in low and ("exceed" in low or "too long" in low)):
        n_ctx = n_ctx or context
        sizes_en = f" ({n_prompt} tokens in the request, the context holds {n_ctx})" if n_prompt and n_ctx else ""
        sizes_ru = f" (в запросе {n_prompt} токенов, контекст вмещает {n_ctx})" if n_prompt and n_ctx else ""
        return ProviderServerError(
            "The conversation does not fit into the local model's context" + sizes_en + ". Raise “Context "
            "length” in the model settings (Local models → Configure), start a new chat or send less text.", 400,
            message_ru="Разговор не помещается в контекст локальной модели" + sizes_ru + ". Увеличьте «Длину "
                       "контекста» в настройках модели (Локальные модели → Настроить), начните новый чат "
                       "или отправьте меньше текста.")
    if status == 400 and ("tools" in low or "tool_choice" in low or "jinja" in low):
        return ProviderServerError(
            f"This local model's chat template cannot handle tools: {detail[:300]}", 400,
            message_ru=f"Шаблон чата этой локальной модели не поддерживает инструменты: {detail[:300]}")
    if status == 400 and ("image" in low or "mmproj" in low or "multimodal" in low):
        return ProviderServerError(
            f"llama-server rejected the image: {detail[:300]}. Check the projector (mmproj) in the model settings.",
            400, message_ru=f"llama-server отклонил изображение: {detail[:300]}. Проверьте проектор (mmproj) "
                            "в настройках модели.")
    code = status if 400 <= status < 600 else 502
    return ProviderServerError(f"llama-server answered HTTP {status}: {detail[:400]}", code,
                               message_ru=f"llama-server ответил HTTP {status}: {detail[:400]}")
