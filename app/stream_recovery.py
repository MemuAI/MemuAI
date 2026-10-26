"""Round 40: Smart Stream Recovery for composite models.

Free providers (Genspark, Gemini Web, free OpenRouter models) often break a long answer in the middle: a network
error or an idle timeout after the client already got half of the text. Before round 40 a composite model could only
switch candidates before the first token, so the client saw the error.

Now :class:`Tap` watches what has already reached the client, and when the stream breaks with a passing trouble the
composite asks the next candidate to *continue* the answer: the text so far goes in as the assistant's turn plus a
short hidden instruction (works with every OpenAI-compatible API — a true assistant prefill is not portable). The first
characters of the continuation are held back by :class:`Joiner` until the overlap with the sent text is known and cut
(models like to repeat the last words or restart the sentence).

Tool calls: while recovery is on, the deltas of a tool call are held back until the call is complete (the next call
starts or ``finish_reason`` arrives), so a client never gets half of a call's JSON. A break inside a call drops that
unfinished call; if complete calls were already sent, the stream is closed with ``finish_reason=tool_calls`` (the
client runs them and the conversation goes on), otherwise the continuation re-issues the call. The internal stream
format is OpenAI chunks; the Anthropic ``/v1/messages`` and Responses endpoints convert it, so both formats recover.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

RECOVERABLE = ("timeout", "5xx", "429", "error", "unavailable", "empty")

HOLD_CHARS = 160
MIN_OVERLAP = 4

CONTINUE_RU = ("Твой предыдущий ответ оборвался из-за сбоя связи. Продолжи его РОВНО с того символа, на котором он "
               "оборвался: не повторяй уже написанное, не извиняйся и не добавляй вступлений — сразу следующий текст.")
CONTINUE_EN = ("Your previous answer was cut off by a connection failure. Continue it EXACTLY from the character "
               "where it stopped: do not repeat what is already written, do not apologise, add no preamble — just "
               "the next text.")

def _delta(item: Dict[str, Any]) -> Dict[str, Any]:
    choices = item.get("choices") or []
    if choices and isinstance(choices[0], dict):
        delta = choices[0].get("delta")
        return delta if isinstance(delta, dict) else {}
    return {}

def _finish(item: Dict[str, Any]) -> Optional[str]:
    choices = item.get("choices") or []
    if choices and isinstance(choices[0], dict):
        return choices[0].get("finish_reason")
    return None

class Tap:
    """Everything the client has received so far, across every candidate of one composite request."""

    def __init__(self, hold_tools: bool = True) -> None:
        self.hold_tools = hold_tools
        self.text = ""
        self.reasoning = ""
        self.done_calls: List[Dict[str, Any]] = []
        self.pending: Dict[int, Dict[str, Any]] = {}
        self.finished = False
        self.index_base = 0
        self.template: Optional[Dict[str, Any]] = None
        self.recoveries = 0

    def _chunk(self, delta: Dict[str, Any], finish: Optional[str] = None) -> Dict[str, Any]:
        base = copy.deepcopy(self.template) if self.template else {"object": "chat.completion.chunk"}
        base["choices"] = [{"index": 0, "delta": delta, "finish_reason": finish}]
        base.pop("usage", None)
        return base

    def _release(self) -> List[Dict[str, Any]]:
        out = []
        for index in sorted(self.pending):
            call = self.pending.pop(index)
            self.done_calls.append(call)
            out.append(self._chunk({"tool_calls": [call]}))
        return out

    def feed(self, item: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Chunks to send to the client for one chunk of the upstream (tool-call deltas are merged and held)."""
        if not isinstance(item, dict) or item.get("object") != "chat.completion.chunk":
            return [item]
        if self.template is None:
            self.template = {k: v for k, v in item.items() if k not in ("choices", "usage")}
        delta = _delta(item)
        finish = _finish(item)
        if isinstance(delta.get("content"), str):
            self.text += delta["content"]
        if isinstance(delta.get("reasoning_content"), str):
            self.reasoning += delta["reasoning_content"]
        calls = delta.get("tool_calls")
        if not calls or not self.hold_tools:
            if calls:
                for call in calls:
                    if isinstance(call, dict):
                        self.done_calls.append(call)
            out = self._release() if finish else []
            if finish:
                self.finished = True
            return out + [item]
        out: List[Dict[str, Any]] = []
        rest = {k: v for k, v in delta.items() if k != "tool_calls"}
        for call in calls:
            if not isinstance(call, dict):
                continue
            index = int(call.get("index") or 0) + self.index_base
            if index not in self.pending and (call.get("id") or (call.get("function") or {}).get("name")):

                for older in [i for i in self.pending if i < index]:
                    done = self.pending.pop(older)
                    self.done_calls.append(done)
                    out.append(self._chunk({"tool_calls": [done]}))
            slot = self.pending.setdefault(index, {"index": index, "id": None, "type": "function",
                                                   "function": {"name": "", "arguments": ""}})
            if call.get("id"):
                slot["id"] = call["id"]
            function = call.get("function") or {}
            if function.get("name"):
                slot["function"]["name"] = function["name"]
            if isinstance(function.get("arguments"), str):
                slot["function"]["arguments"] += function["arguments"]
        if rest.get("content") or rest.get("reasoning_content"):
            out.append(self._chunk(rest))
        if finish:
            out.extend(self._release())
            out.append(self._chunk({}, finish))
            self.finished = True
        return out

    def flush(self) -> List[Dict[str, Any]]:
        """End of a successful stream that never sent ``finish_reason``: release held calls."""
        return self._release()

    def broken_call(self) -> bool:
        return bool(self.pending)

    def close_with_calls(self) -> Optional[List[Dict[str, Any]]]:
        """Strategy for a break inside a tool call when complete calls were already sent: drop the unfinished one
        and end the turn with ``finish_reason=tool_calls``. ``None`` when that does not apply."""
        if not self.done_calls:
            return None
        self.pending.clear()
        self.finished = True
        return [self._chunk({}, "tool_calls")]

    def continuation(self, messages: List[Dict[str, Any]], lang: str = "ru") -> List[Dict[str, Any]]:
        """Messages that ask the next candidate to go on from the text the client already has."""
        self.pending.clear()
        self.index_base = len(self.done_calls)
        if not self.text.strip():
            return list(messages)
        return list(messages) + [{"role": "assistant", "content": self.text},
                                 {"role": "user", "content": CONTINUE_RU if lang == "ru" else CONTINUE_EN}]

def overlap(sent: str, cont: str) -> int:
    """Characters at the start of ``cont`` that repeat what was already sent (to cut off)."""
    if not sent or not cont:
        return 0
    tail = sent[-600:]

    for size in range(min(len(tail), len(cont)), 1, -1):
        if not tail.endswith(cont[:size]):
            continue
        if size >= MIN_OVERLAP:
            return size

        before = tail[-size - 1] if len(tail) > size else " "
        if not before.isalnum() and cont[:size].isalnum() and len(cont) > size and cont[size].isalnum():
            return size
        break

    stripped = cont.lstrip()
    lead = len(cont) - len(stripped)
    for size in range(min(len(stripped), 200), 11, -1):
        head = stripped[:size]
        pos = tail.rfind(head)
        if pos >= 0 and pos + size >= len(tail) - 1:
            return lead + size
    return 0

class Joiner:
    """Holds the first characters of a continuation, cuts the overlap with ``sent``, then passes everything."""

    def __init__(self, sent: str) -> None:
        self.sent = sent
        self.buffer: List[Dict[str, Any]] = []
        self.chars = ""
        self.open = not sent.strip()
        self.cut = 0

    def _strip_role(self, item: Dict[str, Any]) -> Dict[str, Any]:
        delta = _delta(item)
        if "role" in delta:
            item = copy.deepcopy(item)
            item["choices"][0]["delta"].pop("role", None)
        return item

    def feed(self, item: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not isinstance(item, dict) or item.get("object") != "chat.completion.chunk":
            return [item]
        item = self._strip_role(item)
        if self.open:
            return [item]
        delta = _delta(item)
        self.buffer.append(item)
        if isinstance(delta.get("content"), str):
            self.chars += delta["content"]
        if len(self.chars) >= HOLD_CHARS or delta.get("tool_calls") or _finish(item):
            return self.release()
        return []

    def release(self) -> List[Dict[str, Any]]:
        if self.open:
            return []
        self.open = True
        cut = overlap(self.sent, self.chars)
        self.cut = cut
        out: List[Dict[str, Any]] = []
        for item in self.buffer:
            delta = _delta(item)
            text = delta.get("content")
            if cut and isinstance(text, str) and text:
                drop = min(cut, len(text))
                cut -= drop
                item = copy.deepcopy(item)
                item["choices"][0]["delta"]["content"] = text[drop:]
                if not text[drop:] and not any(k for k in item["choices"][0]["delta"] if k != "content") \
                        and not _finish(item):
                    continue
            out.append(item)
        self.buffer = []
        return out

def event(kind: str, **data: Any) -> Dict[str, Any]:
    """A ``memuai.event`` chunk for the MemuAI window (other clients never see it)."""
    return {"object": "memuai.event", "event": kind, **data}

