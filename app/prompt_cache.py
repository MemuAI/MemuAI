"""Round 40: automatic prompt caching for OpenAI-compatible upstreams (``CustomProvider``).

MemuCode and long chats resend the same stable prefix every turn: tools + system prompt + memory + old messages.
Upstreams bill a cached prefix at a fraction of the input price, but each family wants it marked differently:

* **Claude** (Anthropic-compatible gateways, ``anthropic/claude-*`` on OpenRouter): explicit
  ``cache_control: {"type": "ephemeral"}`` breakpoints on content parts, **at most 4** per request. We put them on the
  system prompt (tools come before it in Claude's prefix order, so they are cached with it), on the last message of
  the old history and on the newest user turn (so the next turn reads everything up to it from the cache).
* **Gemini on OpenRouter**: OpenRouter accepts ``cache_control`` for Gemini too but only uses the last breakpoint —
  one marker on the end of the stable prefix. Gemini directly: implicit caching, nothing to send.
* **OpenAI** (gpt-*, o-series): automatic caching; a stable ``prompt_cache_key`` improves routing to a warm cache.
* **DeepSeek, Qwen, Kimi, GLM …**: automatic (disk) caching; nothing to send. The saving is still counted: the
  provider reports ``prompt_cache_hit_tokens`` / ``prompt_tokens_details.cached_tokens`` and the Proxy statistics
  (``metrics.py``) turn them into «Сэкономлено кэшем».

A request that already carries ``cache_control`` markers (the client manages its cache itself) is left alone.
Unsupported compatible APIs reject the optional fields with 400/415/422 and ``CustomProvider.chat_stream`` retries once
without them.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Tuple

MAX_BREAKPOINTS = 4

MIN_PREFIX_CHARS = 3500
EPHEMERAL = {"type": "ephemeral"}

def family(model: str, base: str = "") -> str:
    lowered = str(model or "").lower()
    if "claude" in lowered or lowered.startswith("anthropic/"):
        return "claude"
    if "gemini" in lowered and "openrouter" in str(base or "").lower():
        return "gemini_openrouter"
    if re.search(r"(^|[-_/])(gpt|o[1345])", lowered):
        return "openai"
    return "implicit"

def _chars(message: Dict[str, Any]) -> int:
    content = message.get("content")
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(str(p.get("text") or "")) for p in content if isinstance(p, dict))
    return 0

def _has_markers(body: Dict[str, Any]) -> bool:
    for message in body.get("messages") or []:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list) and any(isinstance(p, dict) and p.get("cache_control") for p in content):
            return True
    return False

def _markable(message: Any) -> bool:
    if not isinstance(message, dict) or message.get("role") not in ("system", "developer", "user", "assistant"):
        return False
    content = message.get("content")
    if isinstance(content, str):
        return bool(content)
    return isinstance(content, list) and any(isinstance(p, dict) and p.get("type") == "text" and p.get("text")
                                             for p in content)

def _mark(message: Dict[str, Any]) -> None:
    content = message.get("content")
    if isinstance(content, str):
        message["content"] = [{"type": "text", "text": content, "cache_control": dict(EPHEMERAL)}]
        return
    for part in reversed(content):
        if isinstance(part, dict) and part.get("type") == "text" and part.get("text"):
            part["cache_control"] = dict(EPHEMERAL)
            return

def breakpoints(messages: List[Dict[str, Any]], limit: int) -> List[int]:
    """Indexes of the messages to mark, in order of value: system, end of old history, newest user turn."""
    picked: List[int] = []
    system = [i for i, m in enumerate(messages) if isinstance(m, dict) and m.get("role") in ("system", "developer")
              and _markable(m)]
    if system:
        picked.append(system[-1])
    users = [i for i, m in enumerate(messages) if isinstance(m, dict) and m.get("role") == "user"]
    last_user = users[-1] if users else None
    prefix_chars = 0
    history_end: Optional[int] = None
    for i, message in enumerate(messages):
        if last_user is not None and i >= last_user:
            break
        prefix_chars += _chars(message) if isinstance(message, dict) else 0
        if _markable(message) and message.get("role") not in ("system", "developer"):
            history_end = i
    if history_end is not None and prefix_chars >= MIN_PREFIX_CHARS:
        picked.append(history_end)
    if last_user is not None and _markable(messages[last_user]) and \
            prefix_chars + _chars(messages[last_user]) >= MIN_PREFIX_CHARS:
        picked.append(last_user)
    out: List[int] = []
    for index in picked:
        if index not in out:
            out.append(index)
    return out[:limit]

def apply(body: Dict[str, Any], model: str, *, base: str = "", enabled: bool = True) -> Tuple[Dict[str, Any], bool]:
    """``(body, hinted)``; ``body`` is a copy when anything was added."""
    if not enabled:
        return body, False
    messages = body.get("messages") or []
    kind = family(model, base)
    if kind == "openai":
        system_text = next((m.get("content") for m in messages if isinstance(m, dict)
                            and m.get("role") in ("system", "developer") and isinstance(m.get("content"), str)
                            and m.get("content")), None)
        tools = json.dumps(body.get("tools") or [], ensure_ascii=False, sort_keys=True)[:20000]
        if not system_text and tools == "[]":
            return body, False
        cached = copy.deepcopy(body)
        cached["prompt_cache_key"] = "memuai-" + hashlib.sha256(
            f"{system_text or ''}\u0000{tools}".encode("utf-8")).hexdigest()[:32]
        return cached, True
    if kind == "implicit" or _has_markers(body):
        return body, False
    picks = breakpoints(messages, MAX_BREAKPOINTS)
    if kind == "gemini_openrouter":

        users = [i for i, m in enumerate(messages) if isinstance(m, dict) and m.get("role") == "user"]
        stable = [i for i in picks if not users or i < users[-1]]
        picks = stable[-1:]
    if not picks:
        return body, False
    cached = copy.deepcopy(body)
    for index in picks:
        _mark(cached["messages"][index])
    return cached, True

