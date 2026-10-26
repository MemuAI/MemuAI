"""Tiny OpenAI-compatible client for the prompt eval harness (stdlib only).

Keys come from the environment only (XKIRO_* / YANDEX_*, see /workspace/work/secrets/test_keys.env); they are never
printed or written anywhere. Not part of the normal test suite.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

MODELS = {

    "qwen": ("XKIRO", 6000),
    "flash": ("YANDEX", 8000),

    "oss": ("YANDEX:gpt-oss-120b", 8000),
    "qwen35": ("YANDEX:qwen3.6-35b-a3b", 8000),
}

def _endpoint(prefix: str):
    env, _, swap = prefix.partition(":")
    model = os.environ[env + "_MODEL"]
    if swap:
        model = re.sub(r"/[^/]+/latest$", "/" + swap + "/latest", model)
    return env, model

def available() -> List[str]:
    return [name for name, (prefix, _) in MODELS.items() if os.environ.get(prefix.split(":")[0] + "_KEY")]

def chat(model: str, messages: List[Dict[str, Any]], *, max_tokens: Optional[int] = None, temperature: float = 0.3,
         retries: int = 6) -> Dict[str, Any]:
    prefix, default_max = MODELS[model]
    prefix, model_id = _endpoint(prefix)
    base = os.environ[prefix + "_BASE"].rstrip("/")
    body = {"model": model_id, "messages": messages, "max_tokens": max_tokens or default_max,
            "temperature": temperature}
    data = json.dumps(body).encode()
    last = ""
    for attempt in range(retries):
        request = urllib.request.Request(base + "/chat/completions", data=data, headers={
            "Content-Type": "application/json", "User-Agent": "Mozilla/5.0 MemuAI-prompt-eval",
            "Authorization": "Bearer " + os.environ[prefix + "_KEY"]})
        started = time.time()
        try:
            with urllib.request.urlopen(request, timeout=240) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
            choice = (payload.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            content = message.get("content") or ""
            if not content and choice.get("finish_reason") == "length":
                last = "empty content (length)"
                body["max_tokens"] = int(body["max_tokens"] * 1.5)
                data = json.dumps(body).encode()
                continue
            return {"content": content, "usage": payload.get("usage") or {}, "seconds": time.time() - started,
                    "finish": choice.get("finish_reason")}
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
            if exc.code == 429 and attempt < retries - 1:
                time.sleep(15 + attempt * 15)
                continue
        except Exception as exc:
            last = type(exc).__name__
        time.sleep(3 + attempt * 5)
    return {"content": "", "error": last, "usage": {}, "seconds": 0, "finish": "error"}
