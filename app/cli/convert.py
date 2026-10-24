"""Session messages → the chat format a backend receives.

Two shapes exist:

* **text protocol** (web accounts behind MemuAI Proxy): tool calls are
  replayed as ``memuai-tool`` blocks inside the assistant text, and all
  results of one round travel back as a single ``<tool_results>`` user
  message;
* **native** (direct APIs): OpenAI ``tool_calls`` + ``role: tool`` messages,
  which the Anthropic backend turns into ``tool_use``/``tool_result`` blocks.

Old tool results elided by the context optimiser are replaced by a stub that
tells the model how to get the content back.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from . import vision as pictures
from .protocol import serialize_calls
from .session import load_image_data
from .tokens import split_budget

ELIDED = "[output elided to save context — call the tool again if you still need it]"
ROUND_BUDGET = 100_000


def _parts(content: Any, vision: bool = True, notes: Optional[Dict[str, Dict[str, Any]]] = None) -> Any:
    """Session parts → OpenAI content (string when there is only text).

    For a model that cannot see (``vision`` false) a picture becomes the text an image model wrote about it
    (``notes``), or a plain remark that there was a picture."""
    if isinstance(content, str):
        return content
    out: List[Dict[str, Any]] = []
    number = 0
    for part in content or []:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            out.append({"type": "text", "text": str(part.get("text") or "")})
        elif part.get("type") == "image":
            number += 1
            if not vision:
                out.append({"type": "text", "text": pictures.note_text(pictures.image_key(part), notes or {}, number)})
                continue
            loaded = load_image_data(part)
            if loaded:
                media, data = loaded
                out.append({"type": "image_url", "image_url": {"url": f"data:{media};base64,{data}"}})
    if all(part["type"] == "text" for part in out):
        return "\n\n".join(part["text"] for part in out)
    return out


def _tool_image_notes(item: Dict[str, Any], notes: Optional[Dict[str, Dict[str, Any]]]) -> str:
    """The descriptions of the pictures a tool returned, for a model that cannot see them."""
    question = str(item.get("image_question") or "")
    rows = [pictures.note_text(pictures.note_key(image, question), notes or {}, number)
            for number, image in enumerate(item.get("images") or [], 1) if isinstance(image, dict)]
    return ("\n" + "\n".join(rows)) if rows else ""


def _image_parts(images: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for image in images or []:
        loaded = load_image_data(image)
        if loaded:
            media, data = loaded
            rows.append({"type": "image_url", "image_url": {"url": f"data:{media};base64,{data}"}})
    return rows


def _tool_text(message: Dict[str, Any]) -> str:
    if message.get("elided"):
        return ELIDED
    return str(message.get("content") or "")


def to_text_protocol(messages: List[Dict[str, Any]], *, vision: bool = True,
                     notes: Optional[Dict[str, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    index = 0
    pending_calls: List[Dict[str, Any]] = []
    while index < len(messages):
        message = messages[index]
        role = message.get("role")
        if role == "assistant":
            text = str(message.get("content") or "").strip()
            calls = message.get("tool_calls") or []
            pending_calls = calls
            if calls:
                text = (text + "\n\n" if text else "") + serialize_calls(calls)
            if text:
                out.append({"role": "assistant", "content": text})
            index += 1
            if calls and (index >= len(messages) or messages[index].get("role") != "tool"):
                # The session ended between these calls and their results (a crash, a closed window): say so,
                # or the model reads the next message as if its calls had run.
                rows = [f'<result tool="{call.get("name")}" id="{call.get("id")}" ok="false">\n{MISSING_RESULT}\n'
                        "</result>" for call in calls]
                out.append({"role": "user", "content": "<tool_results>\n" + "\n".join(rows) + "\n</tool_results>"})
                pending_calls = []
            continue
        if role == "tool":
            rows: List[str] = []
            images: List[Dict[str, Any]] = []
            received = set()
            while index < len(messages) and messages[index].get("role") == "tool":
                item = messages[index]
                received.add(str(item.get("tool_call_id") or ""))
                ok = not item.get("is_error")
                head = f'<result tool="{item.get("name")}" id="{item.get("tool_call_id")}" ok="{str(ok).lower()}">'
                body_text = _tool_text(item)
                if not vision and not item.get("elided") and item.get("images"):
                    body_text += _tool_image_notes(item, notes)
                rows.append(head + "\n" + body_text + "\n</result>")
                if vision and not item.get("elided"):
                    images.extend(item.get("images") or [])
                index += 1
            for call in pending_calls:
                if str(call.get("id") or "") not in received:
                    rows.append(f'<result tool="{call.get("name")}" id="{call.get("id")}" ok="false">\n'
                                + MISSING_RESULT + "\n</result>")
            pending_calls = []
            rows = split_budget(rows, ROUND_BUDGET)
            body = "<tool_results>\n" + "\n".join(rows) + "\n</tool_results>"
            image_parts = _image_parts(images)
            if image_parts:
                out.append({"role": "user", "content": [{"type": "text", "text": body}, *image_parts]})
            else:
                out.append({"role": "user", "content": body})
            continue
        content = _parts(message.get("content"), vision, notes)
        if content:
            out.append({"role": "user" if role != "system" else "system", "content": content})
        index += 1
    return out


def to_native(messages: List[Dict[str, Any]], *, vision: bool = True,
              notes: Optional[Dict[str, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    pending_images: List[Dict[str, Any]] = []
    known_calls: set = set()
    for message in messages:
        role = message.get("role")
        if role != "tool" and pending_images:
            out.append({"role": "user", "content": [{"type": "text", "text": "Images returned by the tools above:"},
                                                    *pending_images]})
            pending_images = []
        if role == "assistant":
            calls = message.get("tool_calls") or []
            row: Dict[str, Any] = {"role": "assistant", "content": str(message.get("content") or "") or None}
            if calls:
                row["tool_calls"] = [{"id": call.get("id"), "type": "function",
                                      "function": {"name": call.get("name"),
                                                   "arguments": json.dumps(call.get("args") or {}, ensure_ascii=False)}}
                                     for call in calls]
                known_calls.update(str(call.get("id")) for call in calls)
            if message.get("_anthropic"):
                row["_anthropic"] = message["_anthropic"]
            if row["content"] is None and not calls:
                continue
            out.append(row)
        elif role == "tool":
            call_id = str(message.get("tool_call_id") or "")
            if call_id not in known_calls:
                out.append({"role": "user", "content": f"(result of {message.get('name')}) " + _tool_text(message)})
                continue
            text = _tool_text(message)
            if not vision and not message.get("elided") and message.get("images"):
                text += _tool_image_notes(message, notes)
            out.append({"role": "tool", "tool_call_id": call_id, "content": text,
                        "is_error": bool(message.get("is_error"))})
            if vision and not message.get("elided"):
                pending_images.extend(_image_parts(message.get("images") or []))
        else:
            content = _parts(message.get("content"), vision, notes)
            if content:
                out.append({"role": "user", "content": content})
    if pending_images:
        out.append({"role": "user", "content": [{"type": "text", "text": "Images returned by the tools above:"},
                                                *pending_images]})
    paired = pair_tool_results(out)
    # Parallel native calls used to bypass ROUND_BUDGET entirely: eight large
    # reads could add almost half a million characters to a single request.
    group: List[Dict[str, Any]] = []
    for row in [*paired, {"role": "boundary"}]:
        if row.get("role") == "tool":
            group.append(row)
        elif group:
            limited = split_budget([str(item.get("content") or "") for item in group], ROUND_BUDGET)
            for item, text in zip(group, limited):
                item["content"] = text
            group = []
    return paired


MISSING_RESULT = ("Error: no result was recorded for this call (the session was interrupted or closed before it "
                  "finished). Check the current state before relying on it.")


def pair_tool_results(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every native tool call answered exactly once, right after the assistant message that made it.

    OpenAI and Anthropic reject a request in which a tool call has no result, a result has no call, or one call
    has two results. A session can hold such shapes: a crash or a closed window between a call and its result, a
    summary that covers the call but not all of its results, a resumed old session. A missing result becomes an
    explicit error result, a duplicate is dropped, a result far from its call becomes plain text."""
    out: List[Dict[str, Any]] = []
    index = 0
    while index < len(rows):
        row = rows[index]
        index += 1
        if row.get("role") == "tool":
            out.append({"role": "user", "content": "(result of an earlier tool call) " + str(row.get("content") or "")})
            continue
        out.append(row)
        calls = row.get("tool_calls") if row.get("role") == "assistant" else None
        if not calls:
            continue
        wanted = [str(call.get("id")) for call in calls]
        seen: set = set()
        while index < len(rows) and rows[index].get("role") == "tool":
            result = rows[index]
            index += 1
            call_id = str(result.get("tool_call_id") or "")
            if call_id in wanted and call_id not in seen:
                seen.add(call_id)
                out.append(result)
        for call_id in wanted:
            if call_id not in seen:
                seen.add(call_id)
                out.append({"role": "tool", "tool_call_id": call_id, "content": MISSING_RESULT, "is_error": True})
    return out
