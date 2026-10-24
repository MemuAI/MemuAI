"""Token estimates without a tokenizer.

Providers behind MemuAI Proxy rarely report exact usage, and tokenizers
differ per model family anyway. The count itself lives in
``app/token_estimate.py`` (one estimate for all of MemuAI, calibrated on real
tokenizers per script: Latin, Cyrillic, code, CJK); this module adds what the
agent loop needs on top of it — messages, images, streaming speed.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..token_estimate import _MEMO, _MEMO_LIMIT, estimate, estimate_float  # noqa: F401  (the memo is shared)


def estimate_text(text: str) -> int:
    return estimate(text) if text else 0


def chunk_tokens(text: str) -> float:
    """Tokens of a piece of streamed text: like :func:`estimate_text`, but fractional (a three-character piece is
    not "one token") and without the memo — pieces are short and never repeat."""
    return estimate_float(text) if text else 0.0


class RateMeter:
    """How fast a model writes, in tokens per second — and silence when that cannot be known.

    A speed is only real when text arrives *piece by piece over time*. Many providers behind the Proxy hold the
    whole answer back and release it in one or two big pieces: dividing that by the (short) time since the first
    piece gives thousands of tokens per second that no model produces. So:

    * a piece larger than ``BURST`` tokens is a dump, not a stream: it is counted in the totals elsewhere, but
      it restarts the measurement instead of adding to it;
    * the speed is the tokens that arrived *after* the first piece of the window, divided by the time between the
      first and the last piece of the window (``WINDOW`` seconds, so it follows the model, not the whole turn);
    * nothing is shown before ``MIN_PIECES`` pieces over ``MIN_SPAN`` seconds, nor when the stream stalled
      (``STALE`` seconds without a piece), nor above ``CEILING``;
    * the number is smoothed, so it does not jitter from frame to frame.

    One meter measures one request: call :meth:`reset` when a new request starts, or the tokens of the earlier
    rounds of a turn would be divided by the time of the last one."""

    WINDOW = 6.0
    MIN_SPAN = 1.5
    MIN_PIECES = 4
    BURST = 140.0
    STALE = 3.0
    CEILING = 1500.0

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._events: List[Tuple[float, float]] = []     # (time, tokens of that piece); the first one is the anchor
        self._shown = 0.0
        self.bursts = 0

    def add(self, tokens: float, now: float) -> None:
        if tokens <= 0:
            return
        if tokens > self.BURST:
            self.bursts += 1
            self._events = [(now, 0.0)]                    # start measuring again after the dump
            self._shown = 0.0
            return
        if not self._events:
            self._events.append((now, 0.0))                # the first piece only says when the stream began
            return
        self._events.append((now, tokens))
        edge = now - self.WINDOW
        while len(self._events) > 2 and self._events[1][0] < edge:
            self._events.pop(0)                            # the next piece becomes the anchor: its tokens are not counted

    def rate(self, now: float) -> Optional[float]:
        events = self._events
        if len(events) < self.MIN_PIECES + 1:
            return None
        if now - events[-1][0] > self.STALE:
            return None
        span = events[-1][0] - events[0][0]
        if span < self.MIN_SPAN:
            return None
        tokens = sum(tokens for _time, tokens in events[1:])
        raw = tokens / span
        if raw <= 0 or raw > self.CEILING:
            return None
        self._shown = raw if not self._shown else self._shown * 0.7 + raw * 0.3
        return self._shown


def estimate_image(width: int = 0, height: int = 0) -> int:
    """Vision cost of one image: tokens ≈ pixels / 750, capped like the APIs do."""
    if width <= 0 or height <= 0:
        return 1100
    scale = min(1.0, 1568 / max(width, height))
    w, h = width * scale, height * scale
    return int(min(1600, max(85, (w * h) / 750)))


def estimate_content(content: Any) -> int:
    if content is None:
        return 0
    if isinstance(content, str):
        return estimate_text(content)
    if isinstance(content, list):
        total = 0
        for part in content:
            if not isinstance(part, dict):
                total += estimate_text(str(part))
                continue
            kind = part.get("type")
            if kind in ("image", "image_url", "input_image"):
                total += estimate_image(int(part.get("width") or 0), int(part.get("height") or 0))
            else:
                total += estimate_text(str(part.get("text") or part.get("content") or ""))
        return total
    if isinstance(content, dict):
        return estimate_text(json.dumps(content, ensure_ascii=False))
    return estimate_text(str(content))


#: What an elided tool result costs: convert.py sends a one-line stub instead of its body.
_ELIDED_TOKENS = 24


def estimate_message(message: Dict[str, Any]) -> int:
    if message.get("elided") and message.get("role") == "tool":
        # The body is not sent any more. Counting it in full kept the usage at
        # the same level after elision, so compaction fired anyway and /context
        # showed tokens the request did not carry.
        return 4 + _ELIDED_TOKENS
    total = 4 + estimate_content(message.get("content"))
    for image in message.get("images") or []:
        if isinstance(image, dict):
            total += estimate_image(int(image.get("width") or 0), int(image.get("height") or 0))
    for call in message.get("tool_calls") or []:
        if isinstance(call, dict):
            function = call.get("function") if isinstance(call.get("function"), dict) else call
            arguments = function.get("arguments", call.get("args") or {})
            total += 8 + estimate_text(str(function.get("name") or "")) + estimate_text(
                arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False))
    return total


def estimate_messages(messages: Iterable[Dict[str, Any]]) -> int:
    return sum(estimate_message(message) for message in messages)


def compact_number(value: float) -> str:
    """1234 → 1.2k, 1_500_000 → 1.5M."""
    value = float(value or 0)
    if abs(value) >= 1_000_000:
        text = f"{value / 1_000_000:.1f}".rstrip("0").rstrip(".")
        return text + "M"
    if abs(value) >= 1000:
        text = f"{value / 1000:.1f}".rstrip("0").rstrip(".")
        return text + "k"
    return str(int(value))


def split_budget(texts: List[str], budget: int) -> List[str]:
    """Share ``budget`` characters between texts so one huge result cannot
    push out all the others; small ones keep everything."""
    budget = max(0, int(budget))
    if sum(len(text) for text in texts) <= budget:
        return texts
    remaining, left = budget, len(texts)
    out: List[str] = [""] * len(texts)
    for index in sorted(range(len(texts)), key=lambda i: len(texts[i])):
        share = remaining // max(1, left)
        text = texts[index]
        if len(text) <= share:
            out[index] = text
        else:
            marker = f"\n…[{len(text)} characters omitted]…\n"
            if share <= len(marker):
                out[index] = text[:share]
            else:
                available = share - len(marker)
                head = int(available * 0.7)
                tail = available - head
                omitted = len(text) - head - tail
                marker = f"\n…[{omitted} characters omitted]…\n"
                out[index] = text[:head] + marker + (text[-tail:] if tail else "")
        remaining -= len(out[index])
        left -= 1
    return out
