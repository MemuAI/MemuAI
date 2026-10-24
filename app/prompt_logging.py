"""Complete, detached text snapshots for the local request log.

Retention limits belong to the journal, never to individual prompt text.
Transport credentials and binary media are still excluded from snapshots.
"""
from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from typing import Any, Callable
from urllib.parse import parse_qsl, urlsplit, urlunsplit

_AUTH_TEXT = re.compile(r"(?im)(\b(?:authorization|proxy-authorization|cookie|set-cookie)\s*:\s*)[^\r\n]+")
_API_KEY_TEXT = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")
_MEDIA_KEYS = frozenset({"imageurl", "audiourl", "videourl", "imagedata", "audiodata", "videodata",
                         "filedata", "inputimage", "inputaudio", "inputvideo"})
_MEDIA_TYPES = frozenset({"image", "image_url", "audio", "video", "input_image", "input_audio",
                          "input_video", "document", "file", "base64"})


@lru_cache(maxsize=1)
def _secret_pattern(secrets):
    if not secrets:
        return None
    return re.compile("|".join(re.escape(value) if len(value) >= 8 else
                               r"(?<!\w)" + re.escape(value) + r"(?!\w)" for value in secrets))


class _Builder:
    def __init__(self, secrets, is_secret: Callable[[str], bool]):
        self.secrets = tuple(sorted({value for value in secrets if isinstance(value, str) and value},
                                    key=len, reverse=True))
        self.secret_pattern = _secret_pattern(self.secrets)
        self.is_secret = is_secret
        self.truncated = False
        self.omitted_media = False

    def text(self, value: str) -> str:
        original = value
        value = value.encode("utf-8", errors="replace").decode("utf-8")
        self.truncated |= value != original
        if self.secret_pattern is not None:
            value = self.secret_pattern.sub("[REDACTED]", value)
        value = _AUTH_TEXT.sub(r"\1[REDACTED]", value)
        value = _API_KEY_TEXT.sub("[REDACTED]", value)
        return value

    def _scalar(self, value: Any):
        if isinstance(value, str):
            prefix = value[:16].casefold()
            if prefix.startswith("data:"):
                self.omitted_media = True
                return self.text("[media omitted]")
            if prefix.startswith(("http://", "https://")):
                try:
                    parts = urlsplit(value)
                    sensitive = any(self.is_secret(key) or "signature" in key.casefold() or
                                    key.casefold() in ("key", "sig", "credential")
                                    for key, _ in parse_qsl(parts.query))
                    if parts.username is not None or sensitive:
                        self.truncated = True
                        value = urlunsplit((parts.scheme, parts.netloc.rpartition("@")[2], parts.path, "", ""))
                except ValueError:
                    self.truncated = True
                    return self.text("[invalid URL omitted]")
            return self.text(value)
        if value is None or isinstance(value, (bool, int, float)):
            if isinstance(value, float) and not math.isfinite(value):
                self.truncated = True
                return None
            return value
        self.truncated = True
        return None

    def clean(self, value: Any):
        # Iterative traversal preserves complete deep/large JSON without a
        # node/depth quota. Reject only cycles, which cannot arrive in JSON.
        result = [None]
        work = [(value, result, 0)]
        active = set()
        while work:
            current, parent, key = work.pop()
            if parent is None:
                active.remove(current)
                continue
            if not isinstance(current, (dict, list, tuple)):
                parent[key] = self._scalar(current)
                continue
            identity = id(current)
            if identity in active:
                self.truncated = True
                parent[key] = None
                continue
            if isinstance(current, dict):
                kind = current.get("type")
                if isinstance(kind, str) and kind in _MEDIA_TYPES:
                    self.omitted_media = True
                    parent[key] = self.text("[media omitted]")
                    continue
                output = {}
                children = []
                for original_key, item in current.items():
                    original_key = str(original_key)
                    clean_key = self.text(original_key)
                    normalized_key = re.sub(r"[^a-z]", "", original_key.casefold())
                    if self.is_secret(original_key):
                        output[clean_key] = self.text("[REDACTED]")
                    elif normalized_key in _MEDIA_KEYS:
                        self.omitted_media = True
                        output[clean_key] = self.text("[media omitted]")
                    else:
                        output[clean_key] = None
                        children.append((item, output, clean_key))
            else:
                output = [None] * len(current)
                children = [(item, output, index) for index, item in enumerate(current)]
            parent[key] = output
            active.add(identity)
            work.append((identity, None, None))
            work.extend(reversed(children))
        return result[0]

    def function(self, value):
        if not isinstance(value, dict):
            self.truncated = True
            return None
        self.truncated |= bool(set(value) - {"name", "arguments"})
        result = {"name": self.clean(value.get("name"))}
        arguments = value.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (ValueError, RecursionError):
                self.truncated = True
                arguments = "[unparsed tool arguments omitted]"
        result["arguments"] = self.clean(arguments)
        return result

    def tool_calls(self, value):
        if not isinstance(value, list):
            self.truncated = True
            return None
        result = []
        for call in value:
            if not isinstance(call, dict):
                self.truncated = True
                continue
            self.truncated |= bool(set(call) - {"id", "type", "function"})
            self.omitted_media |= any(re.sub(r"[^a-z]", "", str(key).casefold()) in _MEDIA_KEYS for key in call)
            result.append({"id": self.clean(call.get("id")), "type": self.clean(call.get("type")),
                           "function": self.function(call.get("function"))})
        self.truncated |= len(value) > len(result)
        return result

    def content(self, value):
        if isinstance(value, str):
            return self.text(value)
        if value is None:
            return None
        if not isinstance(value, list):
            self.truncated = True
            return self.text("[unsupported content omitted]")
        output = []
        for block in value:
            if isinstance(block, dict) and block.get("type") in ("text", "input_text", "output_text"):
                text = block.get("text")
                output.append({"type": "text", "text": self.text(text) if isinstance(text, str) else ""})
            else:
                # Do not even serialize base64, local paths or signed media URLs.
                self.omitted_media = True
                kind = str(block.get("type") or "attachment")[:40] if isinstance(block, dict) else "attachment"
                output.append({"type": "text", "text": self.text(f"[{kind} omitted]")})
        self.truncated |= len(value) > len(output)
        return output


def snapshot_prompt(messages, *, tools=None, secrets=(), is_secret=lambda key: False):
    if not isinstance(messages, list):
        return None
    builder = _Builder(secrets, is_secret)
    rows = []
    for message in messages:
        if not isinstance(message, dict):
            builder.truncated = True
            continue
        role = message.get("role")
        if not isinstance(role, str):
            builder.truncated = True
            continue
        row = {"role": builder.text(role), "content": builder.content(message.get("content"))}
        for key in ("name", "tool_call_id"):
            if key in message:
                row[key] = builder.clean(message[key])
        if "tool_calls" in message:
            row["tool_calls"] = builder.tool_calls(message["tool_calls"])
        if "function_call" in message:
            row["function_call"] = builder.function(message["function_call"])
        rows.append(row)
    builder.truncated |= len(messages) > len(rows)
    result = {"messages": rows}
    if isinstance(tools, list) and tools:
        result["tools"] = builder.clean(tools)
    result["truncated"] = builder.truncated
    result["omitted_media"] = builder.omitted_media
    return result
