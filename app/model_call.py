"""One call to a catalogue model through the normal Proxy pipeline.

The model detector and the built-in companion talk to models exactly the way
an API client does: routing, account pool, retries and logs all apply. This
module turns the OpenAI-style chunk stream into plain text and timing.
"""
from __future__ import annotations

import time
from typing import Any, AsyncIterator, Dict, List, Optional

async def stream(model: str, messages: List[Dict[str, Any]],
                 params: Optional[Dict[str, Any]] = None) -> AsyncIterator[Dict[str, Any]]:
    """Yield ``{"type": "text"|"reasoning", "text": ...}`` events and one final
    ``{"type": "usage", "usage": {...}}`` with the token counts of the request.

    A ``CompletionError`` from the pipeline propagates unchanged: the caller
    shows its real message instead of a fake empty answer.
    """
    from .chat import chat_service
    from .state import state

    trace = state.open_trace(model=model)
    started = time.time()
    try:
        async for chunk in chat_service.stream_completion(model, messages, dict(params or {}), trace=trace):
            if chunk.get("object") == "memuai.event":
                continue
            if isinstance(chunk.get("usage"), dict):

                yield {"type": "usage", "usage": dict(chunk["usage"])}
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                if delta.get("reasoning_content"):
                    yield {"type": "reasoning", "text": str(delta["reasoning_content"])}
                if delta.get("content"):
                    yield {"type": "text", "text": str(delta["content"])}
        trace.status = 200
    except Exception as exc:
        trace.status = int(getattr(exc, "status", 500) or 500)
        trace.error = str(getattr(exc, "message", "") or exc)
        raise
    finally:
        trace.ms = int((time.time() - started) * 1000)
        state.commit_trace(trace)

async def complete(model: str, messages: List[Dict[str, Any]],
                   params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The whole answer: text, reasoning, time to first token and total time."""
    started = time.perf_counter()
    first = 0.0
    text: List[str] = []
    reasoning: List[str] = []
    usage: Dict[str, Any] = {}
    async for event in stream(model, messages, params):
        if event["type"] == "usage":
            usage = event["usage"]
            continue
        if not first:
            first = time.perf_counter()
        (text if event["type"] == "text" else reasoning).append(event["text"])
    finished = time.perf_counter()
    body = "".join(text)
    return {
        "text": body,
        "reasoning": "".join(reasoning),
        "ttft_ms": int(((first or finished) - started) * 1000),
        "ms": int((finished - started) * 1000),
        "chars": len(body),
        "usage": usage,
    }
