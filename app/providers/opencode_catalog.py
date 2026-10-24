"""OpenCode catalogue parsing. Model IDs, plans and wire APIs come from upstream data."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any
from urllib.parse import urlsplit

METADATA_URL = "https://models.opencode.ai/api.json"
MODELS_URLS = {
    "free": "https://opencode.ai/inference/v1/models",
    "zen": "https://opencode.ai/inference/v1/models",
    "go": "https://opencode.ai/inference/go/v1/models",
}
USAGE_URL = "https://opencode.ai/inference/go/v1/usage"


def fingerprint(fields: dict) -> str:
    data = {key: str(fields.get(key, "") or "").strip()
            for key in ("mode", "api_key", "base_url", "api_protocol")}
    if mode_of(fields) == "free":
        data["transport"] = "managed-opencode2api-v2-no-catalog-process"
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def mode_of(fields: dict) -> str:
    return str(fields.get("mode", "free") or "free").strip().lower()


def gateway_base(value: str) -> str:
    value = str(value or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/responses", "/models"):
        if value.endswith(suffix):
            value = value[:-len(suffix)]
            break
    url = urlsplit(value)
    if url.scheme in ("http", "https") and url.netloc and not url.path:
        value += "/v1"
    return value


def model_ids(body: Any) -> list[dict]:
    rows = body.get("data", body.get("models")) if isinstance(body, dict) else None
    if not isinstance(rows, list):
        raise ValueError("OpenCode returned no model list")
    found = {}
    for row in rows[:10000]:
        if isinstance(row, str):
            row = {"id": row}
        if not isinstance(row, dict):
            continue
        name = row.get("id")
        if not isinstance(name, str) or not name.strip() or len(name) > 512:
            continue
        if any(ord(char) < 33 or ord(char) == 127 for char in name):
            continue
        if row.get("available") is False or row.get("enabled") is False:
            continue
        if str(row.get("status", "")).lower() in ("disabled", "deprecated", "retired", "unavailable"):
            continue
        found[name] = dict(row)
    return list(found.values())


def protocol(npm: str) -> str:
    if "anthropic" in npm:
        return "messages"
    if "openai-compatible" in npm:
        return "chat"
    if npm == "@ai-sdk/openai":
        return "responses"
    if npm == "@ai-sdk/google":
        return "gemini"
    return ""


def safe_api(value: Any, *, go: bool) -> str:
    """A public metadata server cannot redirect a user's key to another origin."""
    if not isinstance(value, str):
        return ""
    try:
        url = urlsplit(value)
    except ValueError:
        return ""
    if url.scheme != "https" or url.netloc != "opencode.ai" or url.query or url.fragment:
        return ""
    if url.username or url.password or "\\" in value or any(ord(c) < 33 for c in value):
        return ""
    parts = url.path.split("/")
    if any(part in (".", "..") for part in parts):
        return ""
    if not url.path.startswith("/inference/"):
        return ""
    if ("/go/" in url.path) != go:
        return ""
    return value.rstrip("/")


def metadata_models(body: Any, mode: str) -> dict[str, dict]:
    if not isinstance(body, dict):
        raise ValueError("OpenCode model metadata is not an object")
    provider = body.get("opencode-go" if mode == "go" else "opencode")
    if not isinstance(provider, dict) or not isinstance(provider.get("models"), dict):
        raise ValueError("OpenCode metadata has no models for this service")
    found = {}
    for key, value in list(provider["models"].items())[:10000]:
        if not isinstance(value, dict):
            continue
        row = {**value, "id": value.get("id") or key}
        valid = model_ids({"data": [row]})
        if not valid or value.get("deprecated") is True or value.get("retirement_date"):
            continue
        cost = row.get("cost") if isinstance(row.get("cost"), dict) else {}
        if mode == "free" and not all(isinstance(cost.get(k), (int, float))
                and not isinstance(cost[k], bool) and cost[k] == 0 for k in ("input", "output")):
            continue
        own = row.get("provider") if isinstance(row.get("provider"), dict) else {}
        wire = protocol(str(own.get("npm") or provider.get("npm") or ""))
        api = safe_api(own.get("api") or provider.get("api"), go=mode == "go")
        if not wire or not api:
            continue
        found[row["id"]] = {**row, "protocol": wire, "api": api}
    return found


def quota_windows(body: Any) -> list[dict]:
    usage = body.get("usage") if isinstance(body, dict) else None
    if not isinstance(usage, dict):
        raise ValueError("OpenCode did not return quota windows")
    from datetime import datetime
    windows = []
    labels = {"rolling": "Текущее окно", "weekly": "Неделя", "monthly": "Месяц"}
    labels_en = {"rolling": "Rolling window", "weekly": "Week", "monthly": "Month"}
    for name in ("rolling", "weekly", "monthly"):
        row = usage.get(name)
        if not isinstance(row, dict):
            raise ValueError("OpenCode returned incomplete quota windows")
        percent, resets = row.get("percent"), row.get("resetsAt")
        if (not isinstance(percent, (int, float)) or isinstance(percent, bool)
                or not math.isfinite(percent) or not 0 <= percent <= 100
                or row.get("status") not in ("ok", "rate-limited") or not isinstance(resets, str)):
            raise ValueError("OpenCode returned an invalid quota window")
        try:
            parsed = datetime.fromisoformat(resets.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError()
        except (ValueError, OverflowError) as exc:
            raise ValueError("OpenCode returned an invalid reset time") from exc
        windows.append({"id": name, "label": labels[name], "label_en": labels_en[name],
                        "status": row["status"], "used_percent": float(percent),
                        "remaining_percent": 100.0 - percent, "reset_at": parsed.timestamp(),
                        "resets_at": resets})
    return windows
