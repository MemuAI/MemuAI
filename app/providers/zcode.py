"""Провайдер ZCode (план Z.AI для программистов): модели GLM по протоколу настольного клиента ZCode.

* Вход — OAuth-поток Z.AI «для CLI» в браузере (тот же, что у официального клиента) или вставка JWT ZCode.
* Чат — Anthropic Messages API (SSE) на zcode.z.ai с идентичностью официального клиента: набор заголовков
  ZCode 3.16.3 и три системных блока из ``zcode_identity`` (без них ZCode отвечает кодом 3012). Инструкции
  клиента вписываются в последний блок, как это делает сам клиент.
* Нативные вызовы инструментов (function calling) переводятся в обе стороны: OpenAI ``tools`` ↔
  Anthropic ``tool_use``/``tool_result``.
* Лимиты — из биллинга плана (``billing/balance``): остаток по каждой модели и срок сброса.
* Ошибки ZCode разобраны по кодам: перегрузка и 5xx повторяются на месте, исчерпанный лимит закрывает модель
  на аккаунте до сброса, 3012 дважды подряд — пауза аккаунта, 401 — «войдите заново».

Логи — только ASCII и без токенов.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import secrets
import time
import uuid
import webbrowser
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple
from urllib.parse import quote

from . import http
from .base import (
    Field,
    Provider,
    ProviderAuthError,
    ProviderCaptchaError,
    ProviderContentFilterError,
    ProviderError,
    ProviderModelUnavailableError,
    ProviderNetworkError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderServerError,
    content_to_text,
    retry_after_seconds,
    spawn_background,
)
from .zcode_identity import system_blocks
from ..config import config_store

log = logging.getLogger("memuai.zcode")

DEFAULT_ORIGIN = "https://zcode.z.ai"
APP_URL = "https://zcode.z.ai"
CLIENT_VERSION = "3.16.3"
PLATFORM = "darwin-x64"
OS_CATEGORY = "macos"
TIMEZONE = "Europe/Bucharest"
BILLING_OS_VERSION = ("Darwin Kernel Version 21.6.0: Mon Jun 24 00:56:10 PDT 2024; "
                      "root:xnu-8020.240.18.709.2~1/RELEASE_X86_64")
CHAT_OS_VERSION = "21.6.0"
CHAT_UA = f"ZCode/{CLIENT_VERSION} ai-sdk/provider-utils/4.0.27 runtime/node.js/24"
BILLING_UA = f"ZCode/{CLIENT_VERSION}"
ANTHROPIC_BETA = "mid-conversation-system-2026-04-07"

MESSAGES_PATH = "/api/v1/zcode-plan/anthropic/v1/messages"
BALANCE_PATH = "/api/v1/zcode-plan/billing/balance"
PREVIEW_PATH = "/api/v1/zcode-plan/billing/preview"
CONFIGS_PATH = "/api/v1/client/configs"
OAUTH_INIT_PATH = "/api/v1/oauth/cli/init"
OAUTH_POLL_PATH = "/api/v1/oauth/cli/poll/"

MAX_TOKENS_CAP = 131072
DEFAULT_MAX_TOKENS = 32000
LOGIN_TTL = 300
WAF_PAUSE = 15 * 60
QUOTA_FALLBACK_PAUSE = 3600
CONFIGS_TTL = 6 * 3600
PREVIEW_TTL = 3600

SEED = [
    ("glm-5.3", "GLM-5.3 · the main ZCode model"),
    ("glm-5.3-flash", "GLM-5.3 Flash · faster, a separate quota"),
]

AUTH_CODES = frozenset({1000, 1001, 1002, 1003, 1004, 1110, 1111, 1112})
QUOTA_CODES = frozenset({1005, 1113, 1304, 1308, 1309, 1310, 1313,
                         1316, 1317, 1318, 1319, 1320, 1321})
PLAN_LACKS_MODEL = 1311
MODEL_CODES = frozenset({1211, 1220, 1221, 1222})
THROTTLE_CODES = frozenset({1302, 1303, 1305})
LOAD_CODES = frozenset({3007, 3009, 3010, 3012})
WAF_CODE = 3012
CAPTCHA_CODE = 3007
ADMISSION_CODE = 3010
CONTENT_FILTER_CODE = 1301
TOO_LONG_CODE = 1261

CLIENT_NOTE = (
    "This request comes from MemuAI, a general-purpose assistant app (chat, notes, a code editor), "
    "not from the ZCode IDE. There is no terminal or working directory unless this request defines tools: "
    "answer the user directly and follow the instructions below."
)

EFFORT_WIRE = {"minimal": "low", "low": "low", "medium": "high", "high": "high", "xhigh": "max", "max": "max"}

_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]+")
_OPAQUE_RE = re.compile(r"[A-Za-z0-9._~+/=-]{24,}")
_CODE_RE = re.compile(r'"code"\s*:\s*"?(\d+)')
_MODEL_LIKE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{1,63}$")
_TOOL_NAME_RE = re.compile(r"[^A-Za-z0-9_-]")

try:  # curl_cffi >= 0.6
    from curl_cffi import CurlHttpVersion as _CurlHttpVersion
    _HTTP11: Any = _CurlHttpVersion.V1_1
except Exception:  # pragma: no cover - older curl_cffi
    _HTTP11 = None

_sleep = asyncio.sleep  # the tests make retries instant

def origin() -> str:
    """The ZCode origin (``MEMUAI_ZCODE_ORIGIN`` points the tests at a local fake)."""
    return (os.environ.get("MEMUAI_ZCODE_ORIGIN") or DEFAULT_ORIGIN).rstrip("/")

def _err(cls, message: str, message_ru: str, *args: Any, **kwargs: Any) -> ProviderError:
    """A provider error that carries its own Russian text (``provider_error_texts`` prefers it)."""
    exc = cls(message, *args, **kwargs)
    exc.message_ru = message_ru
    return exc

def _ascii(text: Any, limit: int = 160) -> str:
    """Log-safe text: ASCII only, one line, bounded."""
    value = " ".join(str(text or "").split())
    return value.encode("ascii", "replace").decode("ascii")[:limit]

# --- tokens and the client identity ------------------------------------------------------------

def extract_token(raw: Any) -> str:
    """The ZCode JWT from what a person pastes: the bare token, «Bearer …», a JSON file or a cURL line."""
    text = str(raw or "").strip()
    if not text:
        return ""
    found = _JWT_RE.search(text)
    if found:
        return found.group(0)
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, dict):
            for key in ("token", "jwt", "access_token", "accessToken"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return extract_token(value) or value.strip()
            nested = data.get("data")
            if isinstance(nested, dict):
                return extract_token(json.dumps(nested))
        return ""
    if text.lower().startswith("bearer "):
        text = text[7:].strip()
    return text if _OPAQUE_RE.fullmatch(text) else ""

def jwt_claims(token: str) -> Dict[str, Any]:
    """The JWT payload without verification (only to read ``sub``, ``exp`` and the e-mail)."""
    parts = str(token or "").split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}

def token_expiry(token: str) -> float:
    value = jwt_claims(token).get("exp")
    try:
        return float(value) if value else 0.0
    except (TypeError, ValueError):
        return 0.0

def token_subject(token: str) -> str:
    claims = jwt_claims(token)
    return str(claims.get("sub") or claims.get("user_id") or claims.get("uid") or "")

def token_email(token: str) -> str:
    claims = jwt_claims(token)
    value = claims.get("email") or (claims.get("user") or {}).get("email") if isinstance(claims.get("user"), dict) \
        else claims.get("email")
    return str(value or "")

def device_mid(token: str) -> str:
    """A stable device id per account: the edge must see the same device on every request."""
    name = token_subject(token) or token
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, "zcode-gw." + name))

def session_id(mid: str) -> str:
    return "session_" + hashlib.sha256(("zcode-session." + (mid or "anon")).encode("utf-8")).hexdigest()[:32]

def norm(name: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())

def wire_name(model: str) -> str:
    """``glm-5.3-flash`` → ``GLM-5.3-Flash``: the casing the ZCode billing uses for model ids."""
    model = str(model or "").strip()
    if not model.lower().startswith("glm"):
        return model
    parts = model.split("-")
    out = [parts[0].upper()]
    for part in parts[1:]:
        out.append(part[:1].upper() + part[1:].lower() if part.isalpha() else part)
    return "-".join(out)

def chat_headers(token: str, mid: str, query_id: str, trace_id: str) -> Dict[str, str]:
    """The chat header set of ZCode 3.16.3 for macOS (order kept)."""
    return {
        "content-type": "application/json",
        "Authorization": f"Bearer {token}",
        "anthropic-beta": ANTHROPIC_BETA,
        "anthropic-version": "2023-06-01",
        "HTTP-Referer": APP_URL,
        "User-Agent": CHAT_UA,
        "x-api-key": token,
        "X-ZCode-App-Version": CLIENT_VERSION,
        "X-Title": "Z Code@electron",
        "X-ZCode-Agent": "glm",
        "X-Platform": PLATFORM,
        "X-Release-Channel": "production",
        "X-Client-Language": "en-US",
        "X-Client-Timezone": TIMEZONE,
        "X-Os-Category": OS_CATEGORY,
        "X-Os-Version": CHAT_OS_VERSION,
        "x-query-id": query_id,
        "x-session-id": session_id(mid),
        "x-request-id": str(uuid.uuid4()),
        "x-zcode-query-source": "main_turn",
        "x-zcode-session-type": "main",
        "x-zcode-trace-id": trace_id,
        "Connection": "close",
    }

def billing_headers(token: str, mid: str) -> Dict[str, str]:
    """The header set of the plan/billing calls of the official client."""
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "*/*",
        "Accept-Language": "*",
        "User-Agent": BILLING_UA,
        "HTTP-Referer": APP_URL,
        "X-Title": "Z Code@electron",
        "X-ZCode-App-Version": CLIENT_VERSION,
        "X-Os-Category": OS_CATEGORY,
        "X-Os-Version": BILLING_OS_VERSION,
        "X-Platform": PLATFORM,
        "X-Release-Channel": "production",
        "X-Client-Language": "en-US",
        "X-Client-Timezone": TIMEZONE,
        "X-Device-Mid": mid,
        "x-request-id": str(uuid.uuid4()),
        "sec-fetch-mode": "cors",
    }

def origin_code(text: Any) -> int:
    """The business code of a ZCode/Z.AI answer: ``{"code": 3012}``, ``{"error": {"code": "1308"}}``.

    A truncated head still yields the code: the origin puts it first.
    """
    raw = str(text or "").strip()
    if not raw.startswith("{"):
        return 0
    try:
        data = json.loads(raw)
    except ValueError:
        found = _CODE_RE.search(raw)
        return int(found.group(1)) if found else 0
    if not isinstance(data, dict):
        return 0
    inner = data.get("error") if isinstance(data.get("error"), dict) else {}
    for candidate in (data.get("code"), inner.get("code")):
        try:
            value = int(str(candidate).strip())
        except (TypeError, ValueError):
            continue
        if value:
            return value
    return 0

def origin_message(text: Any) -> str:
    raw = str(text or "").strip()
    try:
        data = json.loads(raw) if raw.startswith("{") else None
    except ValueError:
        data = None
    if isinstance(data, dict):
        inner = data.get("error") if isinstance(data.get("error"), dict) else {}
        for value in (data.get("msg"), data.get("message"), inner.get("message"), inner.get("msg"),
                      data.get("error") if isinstance(data.get("error"), str) else None):
            if isinstance(value, str) and value.strip():
                return " ".join(value.split())[:300]
    return " ".join(raw.split())[:200]

def _unix(value: Any) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        if isinstance(value, str) and value.strip():
            from datetime import datetime
            try:
                return int(datetime.fromisoformat(value.strip().replace("Z", "+00:00")).timestamp())
            except ValueError:
                return 0
        return 0
    return number // 1000 if number > 10 ** 12 else number

def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0

def parse_billing(data: Dict[str, Any]) -> Dict[str, Any]:
    """``billing/balance`` data → ``{plan, plan_id, plan_ends, plans, balances: [{name, remaining, used, total,
    period_end}]}``."""
    data = data if isinstance(data, dict) else {}
    plans = [p for p in data.get("plans") or [] if isinstance(p, dict)]
    first = plans[0] if plans else {}
    balances = []
    for row in data.get("balances") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("show_name") or row.get("entitlement_id") or row.get("name") or "").strip()
        if not name:
            continue
        balances.append({"name": name, "remaining": _int(row.get("remaining_units")),
                         "used": _int(row.get("used_units")), "total": _int(row.get("total_units")),
                         "period_end": _unix(row.get("period_end")),
                         "plan_id": str(row.get("plan_id") or "")})
    return {
        "plan": str(first.get("name") or ""),
        "plan_id": str(first.get("plan_id") or ""),
        "plan_ends": _unix(first.get("ends_at") or first.get("expires_at")),
        "plans": [str(p.get("plan_id") or "").lower() for p in plans if p.get("plan_id")],
        "balances": balances,
    }

def _model_names(snapshot: Dict[str, Any]) -> List[str]:
    names = [str(b.get("name") or "") for b in snapshot.get("balances") or []]
    return list(dict.fromkeys(n for n in names if _MODEL_LIKE_RE.match(n) and re.search(r"\d", n)))

# --- OpenAI messages → the Anthropic Messages body ---------------------------------------------

def _text_block(text: str) -> Optional[Dict[str, Any]]:
    return {"type": "text", "text": text} if str(text or "").strip() else None

def _image_block(part: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    image = part.get("image_url") or part.get("image") or part.get("url") or ""
    url = image.get("url", "") if isinstance(image, dict) else str(image or "")
    match = re.match(r"^data:(image/[A-Za-z0-9.+-]+);base64,(.+)$", url.strip(), re.S | re.I)
    if match:
        return {"type": "image", "source": {"type": "base64", "media_type": match.group(1).lower(),
                                            "data": re.sub(r"\s+", "", match.group(2))}}
    if re.match(r"^https?://", url.strip(), re.I):
        return {"type": "image", "source": {"type": "url", "url": url.strip()}}
    return None

def content_blocks(content: Any) -> List[Dict[str, Any]]:
    """OpenAI ``content`` (a string or parts) → Anthropic blocks; empty text is dropped (Anthropic refuses it)."""
    if content is None:
        return []
    if isinstance(content, str):
        block = _text_block(content)
        return [block] if block else []
    if not isinstance(content, list):
        block = _text_block(str(content))
        return [block] if block else []
    out: List[Dict[str, Any]] = []
    for part in content:
        if isinstance(part, str):
            block = _text_block(part)
        elif not isinstance(part, dict):
            continue
        elif part.get("type") in ("text", "input_text", "output_text"):
            block = _text_block(str(part.get("text") or ""))
        elif part.get("type") in ("image_url", "input_image", "image"):
            block = _image_block(part)
            if block is None:
                raise _err(ProviderServerError,
                           "ZCode accepts images as base64 data URLs or https links only.",
                           "ZCode принимает изображения только как data URL (base64) или ссылку https.", 400)
        elif part.get("type") in ("input_file", "file", "file_url"):
            raise _err(ProviderServerError,
                       "ZCode accepts images only, not files: send the document as text.",
                       "ZCode принимает только изображения, а не файлы: передайте документ текстом.", 400)
        else:
            continue
        if block:
            out.append(block)
    return out

def _arguments(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
    except ValueError:
        return {"input": text}
    return value if isinstance(value, dict) else {"value": value}

def _tool_name(name: Any) -> str:
    return _TOOL_NAME_RE.sub("_", str(name or "").strip())[:64] or "tool"

def convert_messages(messages: List[Dict[str, Any]]) -> Tuple[List[str], List[Dict[str, Any]]]:
    """OpenAI chat messages → (client system texts, Anthropic messages with merged turns).

    Tool calls become ``tool_use`` blocks, tool answers ``tool_result`` blocks in the next user turn;
    consecutive turns of one role are merged (the API wants them alternating).
    """
    system: List[str] = []
    out: List[Dict[str, Any]] = []

    def push(role: str, blocks: List[Dict[str, Any]]) -> None:
        if not blocks:
            return
        if out and out[-1]["role"] == role:
            if role == "user" and any(b.get("type") == "tool_result" for b in blocks):
                # tool results must open the user turn that answers the calls
                head = [b for b in out[-1]["content"] if b.get("type") == "tool_result"]
                tail = [b for b in out[-1]["content"] if b.get("type") != "tool_result"]
                out[-1]["content"] = head + blocks + tail
            else:
                out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": list(blocks)})

    for message in messages or []:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "user")
        if role in ("system", "developer"):
            text = content_to_text(message.get("content"))
            if text.strip():
                system.append(text.strip())
            continue
        if role in ("tool", "function"):
            content = message.get("content")
            blocks = content_blocks(content) if isinstance(content, list) else []
            text = content_to_text(content) if not isinstance(content, list) else ""
            result: Any = blocks if any(b.get("type") == "image" for b in blocks) else (
                text or "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text"))
            call_id = str(message.get("tool_call_id") or message.get("name") or "")
            push("user", [{"type": "tool_result", "tool_use_id": call_id, "content": result or "(empty)"}])
            continue
        if role == "assistant":
            blocks = [b for b in content_blocks(message.get("content")) if b.get("type") == "text"]
            calls = message.get("tool_calls") or []
            if not calls and isinstance(message.get("function_call"), dict):
                calls = [{"id": "", "function": message["function_call"]}]
            for call in calls:
                if not isinstance(call, dict):
                    continue
                function = call.get("function") if isinstance(call.get("function"), dict) else {}
                blocks.append({"type": "tool_use",
                               "id": str(call.get("id") or "") or "toolu_" + uuid.uuid4().hex[:24],
                               "name": _tool_name(function.get("name")),
                               "input": _arguments(function.get("arguments"))})
            push("assistant", blocks)
            continue
        push("user", content_blocks(message.get("content")))
    return system, _pair_tool_results(out)

def _pair_tool_results(out: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every ``tool_use`` gets a ``tool_result`` in the next user turn; orphan results become text."""
    fixed: List[Dict[str, Any]] = []
    pending: List[str] = []
    for message in out:
        blocks = list(message["content"])
        if message["role"] == "user":
            kept: List[Dict[str, Any]] = []
            answered = set()
            for block in blocks:
                if block.get("type") == "tool_result":
                    if block.get("tool_use_id") in pending and block.get("tool_use_id") not in answered:
                        answered.add(block.get("tool_use_id"))
                        kept.append(block)
                    else:
                        text = block.get("content")
                        if isinstance(text, list):
                            text = "\n".join(b.get("text", "") for b in text if isinstance(b, dict))
                        kept.append({"type": "text", "text": f"Tool result: {text or '(empty)'}"})
                else:
                    kept.append(block)
            missing = [{"type": "tool_result", "tool_use_id": call, "content": "(no result: the call was not run)"}
                       for call in pending if call not in answered]
            results = missing + [b for b in kept if b.get("type") == "tool_result"]
            others = [b for b in kept if b.get("type") != "tool_result"]
            fixed.append({"role": "user", "content": results + others})
            pending = []
        else:
            if pending:
                fixed.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": call, "content": "(no result: the call was not run)"}
                    for call in pending]})
            pending = [b["id"] for b in blocks if b.get("type") == "tool_use"]
            fixed.append({"role": "assistant", "content": blocks})
    if pending:
        fixed.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": call, "content": "(no result: the call was not run)"}
            for call in pending]})
    return fixed

def _flatten_tools(out: List[Dict[str, Any]]) -> None:
    """No tools in this request: earlier calls and results stay in the history as plain text."""
    for message in out:
        blocks: List[Dict[str, Any]] = []
        for block in message["content"]:
            kind = block.get("type")
            if kind == "tool_use":
                blocks.append({"type": "text", "text": f"[called tool {block.get('name')} with "
                                                      f"{json.dumps(block.get('input') or {}, ensure_ascii=False)}]"})
            elif kind == "tool_result":
                content = block.get("content")
                if isinstance(content, list):
                    content = "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
                blocks.append({"type": "text", "text": f"[tool result]\n{content or '(empty)'}"})
            else:
                blocks.append(block)
        message["content"] = blocks

def convert_tools(params: Dict[str, Any], has_tool_history: bool) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """OpenAI ``tools``/``tool_choice``/``parallel_tool_calls`` → Anthropic ``tools``/``tool_choice``."""
    tools: List[Dict[str, Any]] = []
    for tool in params.get("tools") or []:
        if not isinstance(tool, dict) or tool.get("type") not in (None, "function"):
            continue
        function = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        schema = function.get("parameters") if isinstance(function.get("parameters"), dict) else {}
        schema = dict(schema) if schema else {"type": "object", "properties": {}}
        schema.setdefault("type", "object")
        tools.append({"name": _tool_name(name), "description": str(function.get("description") or ""),
                      "input_schema": schema})
    if not tools:
        return [], None
    choice_raw = params.get("tool_choice")
    choice: Optional[Dict[str, Any]] = {"type": "auto"}
    if choice_raw == "none":
        if not has_tool_history:
            return [], None
        choice = {"type": "none"}
    elif choice_raw in ("required", "any"):
        choice = {"type": "any"}
    elif isinstance(choice_raw, dict):
        function = choice_raw.get("function") if isinstance(choice_raw.get("function"), dict) else {}
        name = function.get("name") or choice_raw.get("name")
        if name:
            choice = {"type": "tool", "name": _tool_name(name)}
    if choice and choice["type"] != "none" and params.get("parallel_tool_calls") is False:
        choice["disable_parallel_tool_use"] = True
    return tools, choice

# --- the provider ------------------------------------------------------------------------------

class ZCodeProvider(Provider):
    id = "zcode"
    title = "ZCode"
    icon = "Z"
    oauth = True
    title_en = "ZCode"
    description = ("ZCode (Z.AI): модели GLM-5.3 по плану ZCode для программистов. Вход через браузер "
                   "(аккаунт Z.AI) или вставкой JWT. Поддерживает вызовы инструментов.")
    description_en = ("ZCode (Z.AI): GLM-5.3 models of the ZCode coding plan. Sign in with a browser "
                      "(a Z.AI account) or paste a JWT. Supports tool calls.")

    def __init__(self) -> None:
        self._flows: Dict[str, Dict[str, Any]] = {}
        self._paused: Dict[str, Tuple[float, str, str]] = {}
        self._configs: Dict[str, Dict[str, Any]] = {}
        self._configs_at = 0.0

    # schema -----------------------------------------------------------------------------------

    def account_fields(self) -> List[Field]:
        return [
            Field("token", "JWT ZCode", "textarea", required=False, placeholder="eyJhbGciOi…",
                  help="Проще всего — кнопка «Войти через браузер». Либо вставьте JWT ZCode: строку eyJ…, "
                       "«Bearer eyJ…» или JSON с полем token.",
                  label_en="ZCode JWT", placeholder_en="eyJhbGciOi…",
                  help_en="The easiest way is the Sign in via browser button. Or paste a ZCode JWT: the eyJ… "
                          "string, “Bearer eyJ…” or a JSON with a token field."),
        ]

    def settings_fields(self) -> List[Field]:
        return [
            Field("effort", "Глубина размышлений по умолчанию", "select", required=False, default="auto",
                  options=["auto", "low", "high", "max"],
                  help="Когда запрос сам не выбирает уровень (пикер в чате, reasoning_effort). "
                       "auto — решает ZCode; приложение ZCode обычно просит max.",
                  label_en="Default thinking depth",
                  help_en="Used when the request sets no level (the chat picker, reasoning_effort). "
                          "auto lets ZCode decide; the ZCode app usually asks for max."),
            Field("max_output_tokens", "Максимум токенов ответа", "number", required=False,
                  default=str(DEFAULT_MAX_TOKENS),
                  help="Если запрос сам не задаёт max_tokens. Предел ZCode — 131072.",
                  label_en="Max output tokens",
                  help_en="Used when the request sets no max_tokens. The ZCode limit is 131072."),
            Field("client_note", "Пояснение для модели", "select", required=False, default="on",
                  options=["on", "off"],
                  help="on — Proxy дописывает к инструкциям короткое пояснение: запрос пришёл из MemuAI, а не "
                       "из ZCode IDE, поэтому модель отвечает как обычный ассистент. Системный промпт ZCode "
                       "остаётся на месте: без него ZCode отклоняет запрос (код 3012).",
                  label_en="Note for the model",
                  help_en="on — the Proxy adds a short note to the instructions: the request comes from MemuAI, "
                          "not from the ZCode IDE, so the model answers as a regular assistant. The ZCode system "
                          "prompt stays: without it ZCode refuses the request (code 3012)."),
        ]

    def seed_models(self) -> List[Tuple[str, str]]:
        return list(SEED)

    def model_note(self, model: str) -> str:
        meta = self._configs.get(norm(model)) or {}
        context = int(meta.get("context") or 0)
        name = str(meta.get("name") or "") or wire_name(model)
        if context:
            size = f"{context // 1000}K" if context < 1_000_000 else f"{context / 1_000_000:g}M"
            return f"{name} · ZCode · context {size}"
        return dict(SEED).get(str(model).lower(), f"{name} · ZCode")

    def validate_fields(self, fields: Dict[str, Any]) -> Optional[str]:
        token = extract_token(fields.get("token"))
        if not token:
            return ("Вставьте JWT ZCode (строку eyJ…) или войдите кнопкой «Войти через браузер» / "
                    "Paste a ZCode JWT (eyJ…) or use Sign in via browser")
        expires = token_expiry(token)
        if expires and expires < time.time():
            return "Этот JWT уже истёк — войдите через браузер заново / This JWT has expired — sign in again"
        fields["token"] = token
        return None

    # tokens, pauses, billing ------------------------------------------------------------------

    @staticmethod
    def _token(account) -> str:
        token = extract_token(account.fields.get("token")) or extract_token(account.extra.get("token"))
        if not token:
            raise _err(ProviderAuthError, "The account has no ZCode token — sign in again",
                       "У аккаунта нет токена ZCode — войдите заново")
        expires = token_expiry(token)
        if expires and expires < time.time() - 30:
            raise _err(ProviderAuthError, "The ZCode token expired — sign in again",
                       "Токен ZCode истёк — войдите заново (кнопка «Войти через браузер»)")
        return token

    def _pause(self, account, seconds: float, en: str, ru: str) -> None:
        self._paused[account.id] = (time.time() + seconds, en, ru)

    def _check_pause(self, account) -> None:
        until, en, ru = self._paused.get(account.id, (0.0, "", ""))
        left = until - time.time()
        if left <= 0:
            self._paused.pop(account.id, None)
            return
        minutes = max(1, int(left // 60))
        raise _err(ProviderRateLimitError, f"{en} Next try in about {minutes} min.",
                   f"{ru} Следующая попытка примерно через {minutes} мин.", retry_after=left)

    @staticmethod
    def _exhausted(account) -> Dict[str, float]:
        raw = account.extra.get("zcode_exhausted")
        return dict(raw) if isinstance(raw, dict) else {}

    def _check_exhausted(self, account, model: str) -> None:
        marks = self._exhausted(account)
        until = float(marks.get(norm(model)) or 0)
        if not until:
            return
        if until <= time.time():
            marks.pop(norm(model), None)
            self._save_extra(account, "zcode_exhausted", marks)
            return
        when = time.strftime("%d.%m %H:%M", time.localtime(until))
        raise _err(ProviderQuotaError, f"The ZCode limit for {wire_name(model)} is used up until {when}",
                   f"Лимит ZCode для {wire_name(model)} исчерпан до {when}")

    @staticmethod
    def _save_extra(account, key: str, value: Any) -> None:
        if value in (None, {}, []):
            account.extra.pop(key, None)
        else:
            account.extra[key] = value
        try:
            from ..state import state as app_state
            app_state.save_accounts_soon()
        except Exception:
            pass

    def _snapshot(self, account) -> Dict[str, Any]:
        snap = account.extra.get("zcode") if isinstance(account.extra.get("zcode"), dict) else {}
        try:
            token = extract_token(account.fields.get("token"))
        except Exception:
            token = ""
        if snap and token and snap.get("sub") and snap.get("sub") != token_subject(token):
            return {}
        return snap or {}

    def _remember(self, account, token: str, parsed: Dict[str, Any]) -> Dict[str, Any]:
        snap = dict(parsed, sub=token_subject(token), ts=int(time.time()))
        self._save_extra(account, "zcode", snap)
        marks = self._exhausted(account)
        if marks:
            for balance in parsed.get("balances") or []:
                if balance.get("remaining", 0) > 0:
                    marks.pop(norm(balance.get("name")), None)
            self._save_extra(account, "zcode_exhausted", marks)
        return snap

    async def _get_json(self, account, token: str, path: str, *, retries: int = 1) -> Dict[str, Any]:
        url = origin() + path
        mid = device_mid(token)
        last = ""
        for attempt in range(retries + 1):
            try:
                session = await http.get_session(self.proxy_for(account) if account is not None
                                                 else config_store.settings.default_proxy,
                                                 impersonate=False, url=url)
                kwargs: Dict[str, Any] = {"headers": billing_headers(token, mid), "timeout": 30}
                if _HTTP11 is not None:
                    kwargs["http_version"] = _HTTP11
                resp = await session.get(url, **kwargs)
            except Exception as exc:
                raise _err(ProviderNetworkError, f"ZCode: network error: {http.network_error_text(exc)}",
                           f"ZCode: сетевая ошибка: {http.network_error_text(exc)}") from exc
            status = resp.status_code
            try:
                text = str(resp.text or "")
            except Exception:
                text = ""
            if status in (502, 503, 504, 522, 524) and attempt < retries:
                last = f"HTTP {status}"
                await _sleep(1.0)
                continue
            code = origin_code(text)
            if status == 401 or code in AUTH_CODES:
                raise _err(ProviderAuthError, f"ZCode rejected the token (HTTP {status}) — sign in again",
                           f"ZCode отклонил токен (HTTP {status}) — войдите заново")
            if status != 200:
                raise _err(ProviderServerError, f"ZCode: HTTP {status}: {origin_message(text)}",
                           f"ZCode: HTTP {status}: {origin_message(text)}", status if status >= 500 else 502)
            try:
                body = json.loads(text)
            except ValueError as exc:
                raise _err(ProviderServerError, "ZCode answered with malformed JSON",
                           "ZCode ответил некорректным JSON") from exc
            if not isinstance(body, dict):
                raise _err(ProviderServerError, "ZCode answered with malformed JSON", "ZCode ответил некорректным JSON")
            if code:
                raise _err(ProviderServerError, f"ZCode: code {code}: {origin_message(text)}",
                           f"ZCode: код {code}: {origin_message(text)}")
            data = body.get("data")
            return data if isinstance(data, dict) else {}
        raise _err(ProviderServerError, f"ZCode: {last or 'no answer'}", f"ZCode: {last or 'нет ответа'}")

    async def _billing(self, account, token: str) -> Dict[str, Any]:
        data = await self._get_json(account, token, f"{BALANCE_PATH}?app_version={CLIENT_VERSION}")
        return self._remember(account, token, parse_billing(data))

    async def _offered_plans(self, account, token: str) -> List[str]:
        """Plans the origin offers to this account but it has not claimed (best effort, cached for an hour)."""
        cached = account.extra.get("zcode_offers") if isinstance(account.extra.get("zcode_offers"), dict) else {}
        if cached and time.time() - float(cached.get("ts") or 0) < PREVIEW_TTL:
            return list(cached.get("names") or [])
        names: List[str] = []
        try:
            data = await self._get_json(account, token,
                                        f"{PREVIEW_PATH}?app_version={CLIENT_VERSION}&platform={PLATFORM}", retries=0)
            owned = set(self._snapshot(account).get("plans") or [])
            for plan in data.get("plans") or []:
                if isinstance(plan, dict) and plan.get("plan_id") and str(plan["plan_id"]).lower() not in owned:
                    names.append(str(plan.get("name") or plan["plan_id"]))
        except ProviderError:
            return []
        self._save_extra(account, "zcode_offers", {"ts": int(time.time()), "names": names})
        return names

    async def _load_configs(self, account) -> None:
        """Model metadata of the official client (context window, output limit): public, no token."""
        if self._configs and time.time() - self._configs_at < CONFIGS_TTL:
            return
        url = f"{origin()}{CONFIGS_PATH}?app_version={CLIENT_VERSION}&platform={PLATFORM}"
        try:
            session = await http.get_session(self.proxy_for(account), impersonate=False, url=url)
            resp = await session.get(url, headers={"User-Agent": BILLING_UA, "Accept": "*/*"}, timeout=20)
            body = resp.json() if resp.status_code == 200 else {}
        except Exception:
            return
        found: Dict[str, Dict[str, Any]] = {}
        rows: List[Dict[str, Any]] = []
        for group in ((body or {}).get("data") or {}).get("providers") or []:
            for row in (group or {}).get("models") or []:
                if not isinstance(row, dict) or not row.get("modelId"):
                    continue
                model_id = str(row["modelId"])
                meta = {"name": str(row.get("name") or model_id), "context": _int(row.get("contextWindow")),
                        "output": _int(row.get("maxCompletionTokens"))}
                found[norm(model_id)] = meta
                rows.append({"id": model_id.lower(), "name": meta["name"], "context_window": meta["context"] or None,
                             "max_output_tokens": meta["output"] or None, "supports_tools": True,
                             "supports_reasoning": True})
        if not found:
            return
        self._configs, self._configs_at = found, time.time()
        try:
            from .. import model_capabilities, model_info
            model_capabilities.record_raw(self.id, rows, id_field="id", source="api:zcode")
            for row in rows:
                model_info.record(self.id, row["id"], context=row.get("context_window"),
                                  output=row.get("max_output_tokens"), source="api:zcode")
        except Exception:
            pass

    def _wire_model(self, account, model: str) -> str:
        for name in _model_names(self._snapshot(account)):
            if norm(name) == norm(model):
                return name
        meta = self._configs.get(norm(model))
        if meta and meta.get("name") and norm(meta["name"]) == norm(model):
            return str(meta["name"])
        return wire_name(model)

    # account card -----------------------------------------------------------------------------

    async def check(self, account) -> Tuple[Optional[bool], str]:
        self._paused.pop(account.id, None)
        self._save_extra(account, "zcode_exhausted", {})
        try:
            token = self._token(account)
            snap = await self._billing(account, token)
        except ProviderAuthError as exc:
            return False, getattr(exc, "message_ru", "") or str(exc)
        except ProviderError as exc:
            return None, getattr(exc, "message_ru", "") or str(exc)
        spawn_background(self._load_configs(account))
        if not snap.get("plan") and not snap.get("balances"):
            return None, ("токен работает, но у аккаунта нет плана ZCode: получите бесплатный план в "
                          "приложении ZCode тем же аккаунтом и проверьте снова")
        parts = [f"токен работает · план: {snap.get('plan') or '—'}"]
        for balance in snap.get("balances") or []:
            if balance.get("total"):
                parts.append(f"{balance['name']}: осталось {balance['remaining']} из {balance['total']}")
        return True, " · ".join(parts)

    async def dynamic_models(self, account) -> Optional[List[str]]:
        try:
            token = self._token(account)
            snap = await self._billing(account, token)
        except ProviderError:
            return None
        await self._load_configs(account)
        names = _model_names(snap)
        return [name.lower() for name in names] or None

    async def usage(self, account) -> Optional[Dict[str, Any]]:
        token = self._token(account)
        snap = await self._billing(account, token)
        email = str(account.extra.get("email") or token_email(token) or "")
        plan = snap.get("plan") or ""
        ends = int(snap.get("plan_ends") or 0)
        subscription = plan
        if plan and ends:
            subscription += " · до " + time.strftime("%d.%m.%Y", time.localtime(ends))
        rows = []
        resets = []
        for balance in snap.get("balances") or []:
            total, used, left = balance.get("total") or 0, balance.get("used") or 0, balance.get("remaining") or 0
            reset = int(balance.get("period_end") or 0)
            if reset > time.time():
                resets.append(reset)
            rows.append({
                "display": balance["name"], "display_en": balance["name"],
                "used": used if total else left, "limit": total or None,
                "unit": "ед.", "unit_en": "units", "reset": reset or None, "reset_at": reset or None,
                "note": f"осталось {left}", "note_en": f"{left} left",
            })
        result: Dict[str, Any] = {"subscription": subscription, "email": email, "rows": rows,
                                  "next_reset": min(resets) if resets else None}
        if not plan and not rows:
            offers = await self._offered_plans(account, token)
            offer = ", ".join(offers)
            result["note"] = ("У аккаунта нет плана ZCode. Откройте приложение ZCode, войдите тем же аккаунтом "
                              "и получите план" + (f" ({offer})" if offer else "") + " — MemuAI не проходит "
                              "проверку «я не робот» за вас. Затем нажмите «Проверить».")
            result["note_en"] = ("The account has no ZCode plan. Open the ZCode app, sign in with the same account "
                                 "and claim the plan" + (f" ({offer})" if offer else "") + " — MemuAI does not pass "
                                 "the “I am not a robot” check for you. Then choose Check.")
        elif plan and ends and ends < time.time() + 3 * 86400:
            result["notice"] = "План ZCode скоро закончится — продлите его в приложении ZCode."
            result["notice_en"] = "The ZCode plan ends soon — renew it in the ZCode app."
            result["notice_url"] = APP_URL
        return result

    # chat -------------------------------------------------------------------------------------

    def build_body(self, account, model: str, messages: List[Dict[str, Any]],
                   params: Dict[str, Any], mid: str) -> Dict[str, Any]:
        """The Anthropic Messages body the official client would send for this OpenAI request."""
        cfg = config_store.provider_cfg(self.id)
        system_texts, out = convert_messages(messages)
        has_tool_history = any(b.get("type") in ("tool_use", "tool_result") for m in out for b in m["content"])
        tools, choice = convert_tools(params, has_tool_history)
        if not tools and has_tool_history:
            _flatten_tools(out)
        if not out:
            out = [{"role": "user", "content": [{"type": "text", "text": "(empty message)"}]}]
        if out[0]["role"] != "user":
            out.insert(0, {"role": "user", "content": [{"type": "text", "text": "(conversation start)"}]})

        level = str(params.get("_reasoning_level") or "").lower()
        if not level:
            default = str(cfg.get("effort") or "auto").lower()
            level = default if default in EFFORT_WIRE else ""
        thinking_on = bool(level) and level != "off"
        if thinking_on and out[-1]["role"] == "assistant":
            out.append({"role": "user", "content": [{"type": "text", "text": "Continue."}]})
        last = out[-1]["content"][-1]
        last["cache_control"] = {"type": "ephemeral"}

        try:
            requested = int(params.get("max_tokens") or 0)
        except (TypeError, ValueError):
            requested = 0
        if requested <= 0:
            try:
                requested = int(float(cfg.get("max_output_tokens") or DEFAULT_MAX_TOKENS))
            except (TypeError, ValueError):
                requested = DEFAULT_MAX_TOKENS
        max_tokens = max(1, min(MAX_TOKENS_CAP, requested))

        notes = [CLIENT_NOTE] if str(cfg.get("client_note", "on")).lower() != "off" else []
        body: Dict[str, Any] = {
            "model": self._wire_model(account, model),
            "max_tokens": max_tokens,
            "system": system_blocks(notes + system_texts),
            "messages": out,
        }
        if tools:
            body["tools"] = tools
            if choice:
                body["tool_choice"] = choice
        if not thinking_on:
            for key in ("temperature", "top_p"):
                value = params.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    body[key] = max(0.0, min(1.0, float(value)))
        stop = params.get("stop")
        stops = [stop] if isinstance(stop, str) else (stop if isinstance(stop, list) else [])
        stops = [s for s in stops if isinstance(s, str) and s.strip()][:8]
        if stops:
            body["stop_sequences"] = stops
        if level == "off":
            body["thinking"] = {"type": "disabled"}
        elif level == "on":
            body["thinking"] = {"type": "enabled"}
        elif level:
            body["thinking"] = {"type": "enabled"}
            body["output_config"] = {"effort": EFFORT_WIRE.get(level, "high")}
        body["metadata"] = {"user_id": json.dumps({"device_id": mid, "account_uuid": "",
                                                   "session_id": session_id(mid)}, separators=(",", ":"))}
        body["stream"] = True
        return body

    async def _send(self, account, token: str, mid: str, payload: bytes, query_id: str, trace_id: str):
        url = origin() + MESSAGES_PATH
        try:
            session = await http.get_session(self.proxy_for(account), impersonate=False, url=url)
            kwargs: Dict[str, Any] = {"headers": chat_headers(token, mid, query_id, trace_id), "data": payload,
                                      "stream": True, "timeout": config_store.settings.request_timeout}
            if _HTTP11 is not None:
                kwargs["http_version"] = _HTTP11
            return await session.post(url, **kwargs)
        except Exception as exc:
            raise _err(ProviderNetworkError, f"ZCode: network error: {http.network_error_text(exc)}",
                       f"ZCode: сетевая ошибка: {http.network_error_text(exc)}") from exc

    async def _mark_exhausted(self, account, token: str, model: str, code: int, detail: str) -> ProviderError:
        """The plan quota of ``model`` is used up: skip it on this account until its window resets."""
        until = 0
        try:
            snap = await asyncio.wait_for(self._billing(account, token), 10)
        except Exception:
            snap = self._snapshot(account)
        for balance in snap.get("balances") or []:
            if norm(balance.get("name")) == norm(model) and int(balance.get("period_end") or 0) > time.time():
                until = int(balance["period_end"])
        if not until:
            ends = int(snap.get("plan_ends") or 0)
            until = ends if code == 1309 and ends > time.time() else int(time.time() + QUOTA_FALLBACK_PAUSE)
        marks = self._exhausted(account)
        marks[norm(model)] = until
        self._save_extra(account, "zcode_exhausted", marks)
        when = time.strftime("%d.%m %H:%M", time.localtime(until))
        log.info("zcode: quota used up acc=%s model=%s code=%s until=%s", account.id, _ascii(model), code, when)
        tail = f" ({detail})" if detail else ""
        if code == 1309:
            return _err(ProviderQuotaError, f"The ZCode plan has expired (code 1309){tail}",
                        f"План ZCode закончился (код 1309){tail}")
        return _err(ProviderQuotaError,
                    f"The ZCode limit for {wire_name(model)} is used up until {when} (code {code}){tail}",
                    f"Лимит ZCode для {wire_name(model)} исчерпан до {when} (код {code}){tail}")

    async def _verdict(self, account, token: str, model: str, status: int, text: str, headers: Any,
                       counters: Dict[str, int]) -> Tuple[float, Optional[ProviderError]]:
        """(pause before a retry, error to raise): exactly one of them is meaningful."""
        code = origin_code(text)
        detail = origin_message(text) if text.strip() else ""
        low = text.lower()
        label = f"HTTP {status}" + (f", code {code}" if code else "")
        label_ru = f"HTTP {status}" + (f", код {code}" if code else "")
        if status == 401 or code in AUTH_CODES:
            return 0, _err(ProviderAuthError, f"ZCode rejected the token ({label}) — sign in again",
                           f"ZCode отклонил токен ({label_ru}) — войдите заново")
        if code in QUOTA_CODES:
            return 0, await self._mark_exhausted(account, token, model, code, detail)
        if code == PLAN_LACKS_MODEL:
            return 0, ProviderModelUnavailableError(
                f"The ZCode plan of this account does not include {wire_name(model)} (code 1311)", model,
                message_ru=f"План ZCode этого аккаунта не включает {wire_name(model)} (код 1311)")
        if code in MODEL_CODES or status == 404:
            return 0, ProviderModelUnavailableError(
                f"ZCode does not serve {wire_name(model)} to this account ({label}): {detail}", model,
                message_ru=f"ZCode не даёт этому аккаунту модель {wire_name(model)} ({label_ru}): {detail}")
        if code == CONTENT_FILTER_CODE:
            return 0, _err(ProviderContentFilterError, f"ZCode refused to answer: the content filter (code 1301)",
                           "ZCode отказался отвечать: сработал фильтр содержимого (код 1301)")
        if code == ADMISSION_CODE and counters["busy"] < 2:
            counters["busy"] += 1
            return float(counters["busy"]), None
        if status == 529 or code in LOAD_CODES or status == 405:
            if code == WAF_CODE:
                counters["waf"] += 1
                if counters["waf"] >= 2:
                    self._pause(account, WAF_PAUSE,
                                "ZCode refuses this account for now (code 3012, unusual activity).",
                                "ZCode временно не принимает этот аккаунт (код 3012, «необычная активность»).")
                    return 0, _err(ProviderRateLimitError,
                                   "ZCode refuses this account for now (code 3012, unusual activity). MemuAI "
                                   "pauses it for 15 minutes; if it repeats, open the official ZCode app once.",
                                   "ZCode временно не принимает этот аккаунт (код 3012, «необычная активность»). "
                                   "MemuAI делает паузу 15 минут; если повторяется — откройте разок официальное "
                                   "приложение ZCode.", retry_after=WAF_PAUSE)
            if counters["load"] < 2:
                counters["load"] += 1
                return 1.0, None
            if code == CAPTCHA_CODE:
                return 0, _err(ProviderCaptchaError,
                               "ZCode asks for a robot check (code 3007): open the ZCode app with this account, "
                               "pass the check, then choose Check here",
                               "ZCode просит проверку «я не робот» (код 3007): откройте приложение ZCode этим "
                               "аккаунтом, пройдите проверку и нажмите здесь «Проверить»")
            return 0, _err(ProviderRateLimitError, f"ZCode is overloaded ({label}){': ' + detail if detail else ''}",
                           f"ZCode перегружен ({label_ru}){': ' + detail if detail else ''}", retry_after=5)
        if status in (500, 502, 503, 504) and counters["dead"] < 2:
            counters["dead"] += 1
            return 1.0, None
        if status == 429 or code in THROTTLE_CODES:
            wait = retry_after_seconds(headers)
            if wait is not None and wait <= 20 and counters["ra"] < 3:
                counters["ra"] += 1
                return max(0.5, wait), None
            return 0, _err(ProviderRateLimitError,
                           f"ZCode is throttling requests ({label}){': ' + detail if detail else ''}",
                           f"ZCode ограничивает частоту запросов ({label_ru}){': ' + detail if detail else ''}",
                           retry_after=wait if wait is not None else 3.0)
        if code == TOO_LONG_CODE or "prompt is too long" in low or "context length" in low:
            return 0, _err(ProviderServerError, f"The conversation is too long for {wire_name(model)} ({label})",
                           f"Диалог слишком длинный для {wire_name(model)} ({label_ru})", 400)
        if status in (400, 413, 422):
            if "image" in low:
                return 0, _err(ProviderServerError,
                               f"The model or API rejected the image (HTTP {status}). Pick a vision model or remove "
                               f"the attachment. {detail}",
                               f"Модель или API отклонили изображение (HTTP {status}). Выберите модель со зрением "
                               f"или уберите вложение. {detail}", status)
            return 0, _err(ProviderServerError, f"ZCode rejected the request ({label}): {detail}",
                           f"ZCode отклонил запрос ({label_ru}): {detail}", status)
        if status == 403:
            return 0, _err(ProviderServerError, f"ZCode refused the request ({label}): {detail}",
                           f"ZCode отказал в запросе ({label_ru}): {detail}", 502)
        return 0, _err(ProviderServerError, f"ZCode: {label}: {detail or 'no details'}",
                       f"ZCode: {label_ru}: {detail or 'без подробностей'}", status if status >= 500 else 502)

    async def chat_stream(self, account, model: str, messages: List[Dict[str, Any]],
                          params: Dict[str, Any]) -> AsyncIterator[Dict[str, Any]]:
        token = self._token(account)
        self._check_pause(account)
        self._check_exhausted(account, model)
        if not self._configs:
            spawn_background(self._load_configs(account))
        mid = device_mid(token)
        body = self.build_body(account, model, messages, params, mid)
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        query_id, trace_id = str(uuid.uuid4()), str(uuid.uuid4())
        counters = {"busy": 0, "load": 0, "waf": 0, "dead": 0, "ra": 0}
        while True:
            resp = await self._send(account, token, mid, payload, query_id, trace_id)
            status = resp.status_code
            if status == 200:
                lines = http.aiter_lines_str(resp, idle_timeout=config_store.settings.stream_idle_timeout)
                head = await self._first_line(lines)
                if head is None or not head.lstrip().startswith("{"):
                    log.info("zcode: chat ok acc=%s model=%s", account.id, _ascii(body["model"]))
                    try:
                        async for event in self._relay(lines, head, body["model"]):
                            yield event
                    finally:
                        await http.close_quietly(lines.aclose)
                        await http.close_response(resp)
                    return
                text = await self._rest(lines, head)
                await http.close_response(resp)
                if '"type"' in text and '"message"' in text and origin_code(text) == 0:
                    for event in self._message_events(text):
                        yield event
                    return
                pause, error = await self._verdict(account, token, model, 200, text, None, counters)
            else:
                text = await http.read_error_text(resp, 1500)
                headers = getattr(resp, "headers", None)
                await http.close_response(resp)
                pause, error = await self._verdict(account, token, model, status, text, headers, counters)
            log.info("zcode: acc=%s model=%s status=%s code=%s -> %s", account.id, _ascii(body["model"]), status,
                     origin_code(text), "retry" if error is None else type(error).__name__)
            if error is not None:
                raise error
            await _sleep(pause)

    @staticmethod
    async def _first_line(lines) -> Optional[str]:
        async for line in lines:
            if line.strip():
                return line
        return None

    @staticmethod
    async def _rest(lines, head: str, limit: int = 1 << 20) -> str:
        parts = [head]
        size = len(head)
        async for line in lines:
            parts.append(line)
            size += len(line)
            if size > limit:
                break
        await http.close_quietly(lines.aclose)
        return "\n".join(parts)

    def _message_events(self, text: str) -> List[Dict[str, Any]]:
        """A non-streamed Messages answer (rare): the same normalized events as the stream."""
        try:
            data = json.loads(text)
        except ValueError:
            raise _err(ProviderServerError, "ZCode answered with malformed JSON", "ZCode ответил некорректным JSON")
        events: List[Dict[str, Any]] = []
        tool = 0
        for block in data.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and block.get("text"):
                events.append({"type": "text", "text": str(block["text"])})
            elif block.get("type") == "thinking" and block.get("thinking"):
                events.append({"type": "reasoning", "text": str(block["thinking"])})
            elif block.get("type") == "tool_use":
                events.append({"type": "tool_call", "index": tool, "id": block.get("id"), "name": block.get("name"),
                               "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False)})
                tool += 1
        usage = data.get("usage") or {}
        events += self._usage_events(usage)
        reason = _finish(data.get("stop_reason"))
        if reason:
            events.append({"type": "finish", "reason": reason})
        return events

    @staticmethod
    def _usage_events(usage: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not isinstance(usage, dict):
            return []
        fresh, read, write = (_int(usage.get("input_tokens")), _int(usage.get("cache_read_input_tokens")),
                              _int(usage.get("cache_creation_input_tokens")))
        output = _int(usage.get("output_tokens"))
        if not (fresh or read or write or output):
            return []
        event: Dict[str, Any] = {"type": "usage", "prompt_tokens": fresh + read + write, "completion_tokens": output}
        if read:
            event["cached_tokens"] = read
        if write:
            event["cache_write_tokens"] = write
        return [event]

    async def _relay(self, lines, head: Optional[str], model: str) -> AsyncIterator[Dict[str, Any]]:
        """Anthropic SSE → normalized events (text, reasoning, tool_call, usage, finish)."""
        tools: Dict[int, int] = {}
        inputs: Dict[int, Any] = {}
        streamed: set = set()
        usage: Dict[str, Any] = {}
        stop_reason = ""
        produced = finished = False
        buffer = ""

        async def all_lines():
            if head is not None:
                yield head
            async for item in lines:
                yield item

        try:
            async for raw in all_lines():
                line = raw.rstrip("\r")
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if not chunk or chunk == "[DONE]":
                    continue
                buffer = buffer + chunk if buffer else chunk
                try:
                    event = json.loads(buffer)
                except ValueError:
                    if len(buffer) > 1 << 20:
                        raise _err(ProviderServerError, "ZCode sent a malformed stream event",
                                   "ZCode прислал повреждённое событие потока")
                    continue
                buffer = ""
                if not isinstance(event, dict):
                    continue
                kind = event.get("type")
                if kind == "message_start":
                    usage.update((event.get("message") or {}).get("usage") or {})
                elif kind == "content_block_start":
                    index = _int(event.get("index"))
                    block = event.get("content_block") or {}
                    btype = block.get("type")
                    if btype == "text" and block.get("text"):
                        produced = True
                        yield {"type": "text", "text": str(block["text"])}
                    elif btype == "thinking" and block.get("thinking"):
                        yield {"type": "reasoning", "text": str(block["thinking"])}
                    elif btype == "tool_use":
                        produced = True
                        tools[index] = len(tools)
                        inputs[index] = block.get("input")
                        yield {"type": "tool_call", "index": tools[index],
                               "id": str(block.get("id") or "") or "toolu_" + uuid.uuid4().hex[:24],
                               "name": str(block.get("name") or "tool"), "arguments": None}
                elif kind == "content_block_delta":
                    index = _int(event.get("index"))
                    delta = event.get("delta") or {}
                    dtype = delta.get("type")
                    if dtype == "text_delta" and delta.get("text"):
                        produced = True
                        yield {"type": "text", "text": str(delta["text"])}
                    elif dtype == "thinking_delta" and delta.get("thinking"):
                        yield {"type": "reasoning", "text": str(delta["thinking"])}
                    elif dtype == "input_json_delta" and index in tools and delta.get("partial_json"):
                        streamed.add(index)
                        yield {"type": "tool_call", "index": tools[index], "id": None, "name": None,
                               "arguments": str(delta["partial_json"])}
                elif kind == "content_block_stop":
                    index = _int(event.get("index"))
                    if index in tools and index not in streamed:
                        streamed.add(index)
                        yield {"type": "tool_call", "index": tools[index], "id": None, "name": None,
                               "arguments": json.dumps(inputs.get(index) or {}, ensure_ascii=False)}
                elif kind == "message_delta":
                    stop_reason = str((event.get("delta") or {}).get("stop_reason") or stop_reason)
                    for key, value in (event.get("usage") or {}).items():
                        if value is not None:
                            usage[key] = value
                elif kind == "message_stop":
                    finished = True
                    break
                elif kind == "error":
                    raise self._stream_error(event.get("error") or {}, produced)
        except ProviderError:
            raise
        except asyncio.TimeoutError:
            raise _err(ProviderNetworkError,
                       f"ZCode sent no data for over {config_store.settings.stream_idle_timeout} s",
                       f"ZCode не присылал данных дольше {config_store.settings.stream_idle_timeout} с")
        except Exception as exc:
            raise _err(ProviderServerError, f"The ZCode stream broke off: {type(exc).__name__}: {exc}",
                       f"Поток ZCode оборвался: {type(exc).__name__}: {exc}")
        for event in self._usage_events(usage):
            yield event
        if not finished and not stop_reason:
            if not produced:
                raise _err(ProviderServerError, "ZCode closed the stream without an answer",
                           "ZCode закрыл поток без ответа")
            raise _err(ProviderServerError, "The ZCode stream broke off before the end of the answer",
                       "Поток ZCode оборвался до конца ответа")
        reason = _finish(stop_reason)
        if reason:
            yield {"type": "finish", "reason": reason}

    @staticmethod
    def _stream_error(error: Dict[str, Any], produced: bool) -> ProviderError:
        kind = str(error.get("type") or "")
        message = " ".join(str(error.get("message") or kind or "error").split())[:300]
        code = origin_code(json.dumps(error))
        if kind == "authentication_error" or code in AUTH_CODES:
            return _err(ProviderAuthError, f"ZCode rejected the token: {message}",
                        f"ZCode отклонил токен: {message}")
        if not produced and (kind in ("overloaded_error", "rate_limit_error") or code in LOAD_CODES | THROTTLE_CODES):
            return _err(ProviderRateLimitError, f"ZCode is overloaded: {message}", f"ZCode перегружен: {message}",
                        retry_after=5)
        if code == CONTENT_FILTER_CODE:
            return _err(ProviderContentFilterError, f"ZCode refused to answer: {message}",
                        f"ZCode отказался отвечать: {message}")
        return _err(ProviderServerError, f"ZCode stream error: {message}", f"Ошибка потока ZCode: {message}")

    # sign-in --------------------------------------------------------------------------------

    async def oauth_start(self) -> Dict[str, Any]:
        for flow in self._flows.values():
            if flow.get("status") == "waiting":
                flow["status"] = "cancelled"
        self._flows = {key: flow for key, flow in self._flows.items()
                       if time.time() - float(flow.get("created") or 0) < LOGIN_TTL * 2}
        poll_token = secrets.token_hex(32)
        url = origin() + OAUTH_INIT_PATH
        try:
            session = await http.get_session(config_store.settings.default_proxy, impersonate=False, url=url)
            resp = await session.post(url, headers={"Authorization": f"Bearer {poll_token}",
                                                    "Content-Type": "application/json"},
                                      data=json.dumps({"provider": "zai"}), timeout=30)
        except Exception as exc:
            return {"ok": False, "message": f"ZCode: не удалось начать вход — {http.network_error_text(exc)}"}
        try:
            data = (resp.json() or {}).get("data") or {} if resp.status_code == 200 else {}
        except Exception:
            data = {}
        remote, link = str(data.get("flow_id") or ""), str(data.get("authorize_url") or "")
        if not remote or not re.match(r"^https?://", link):
            return {"ok": False, "message": f"ZCode не начал вход (HTTP {resp.status_code})"}
        flow_id = uuid.uuid4().hex[:12]
        flow = {"status": "waiting", "remote": remote, "poll": poll_token, "created": time.time(),
                "account": None, "error": "", "url": link}
        self._flows[flow_id] = flow
        spawn_background(self._poll_login(flow))
        try:
            webbrowser.open(link)
        except Exception:
            pass
        return {"ok": True, "flow_id": flow_id, "url": link, "expires_in": LOGIN_TTL}

    async def oauth_status(self, flow_id: str) -> Dict[str, Any]:
        flow = self._flows.get(flow_id)
        if not flow:
            return {"status": "error", "message": "Вход не найден — начните заново"}
        if flow["status"] == "done" and flow.get("account"):
            account = flow.pop("account")
            self._flows.pop(flow_id, None)
            return {"status": "success", "account": account}
        if flow["status"] == "cancelled":
            return {"status": "error", "message": "Вход отменён: начат новый"}
        return {"status": flow["status"], "message": flow.get("error", ""), "url": flow.get("url", "")}

    async def _poll_login(self, flow: Dict[str, Any]) -> None:
        url = origin() + OAUTH_POLL_PATH + quote(flow["remote"], safe="")
        interval = max(0.05, float(os.environ.get("MEMUAI_ZCODE_POLL_INTERVAL") or 2.0))
        deadline = float(flow["created"]) + LOGIN_TTL
        try:
            while flow["status"] == "waiting" and time.time() < deadline:
                await asyncio.sleep(interval)
                try:
                    session = await http.get_session(config_store.settings.default_proxy, impersonate=False, url=url)
                    resp = await session.get(url, headers={"Authorization": f"Bearer {flow['poll']}"}, timeout=30)
                    data = (resp.json() or {}).get("data") or {} if resp.status_code == 200 else {}
                except Exception:
                    continue
                state = str(data.get("status") or "").lower()
                if state == "failed":
                    flow["status"] = "error"
                    flow["error"] = str(data.get("message") or data.get("reason") or
                                        "Z.AI отклонил вход или он не был завершён")
                    return
                if state != "ready":
                    continue
                token = extract_token(str(data.get("token") or ""))
                if not token:
                    flow["status"], flow["error"] = "error", "Z.AI не вернул токен ZCode — попробуйте ещё раз"
                    return
                email = str((data.get("user") or {}).get("email") or "") if isinstance(data.get("user"), dict) else ""
                account = self._store_login(token, email)
                flow["account"] = account.public_dict()
                flow["status"] = "done"
                log.info("zcode: signed in acc=%s", account.id)
                return
            if flow["status"] == "waiting":
                flow["status"], flow["error"] = "error", "Ожидание входа истекло (5 минут) — начните заново"
        except Exception as exc:
            flow["status"], flow["error"] = "error", f"Ошибка входа: {type(exc).__name__}: {exc}"

    def _store_login(self, token: str, email: str):
        """A fresh sign-in refreshes the account of the same Z.AI user instead of adding a twin."""
        from ..state import ST_UNKNOWN, state as app_state
        subject = token_subject(token)
        for account in app_state.accounts_of(self.id):
            old = extract_token(account.fields.get("token"))
            same_user = (subject and old and token_subject(old) == subject) or (
                email and str(account.extra.get("email") or "").lower() == email.lower())
            if same_user:
                account.fields = dict(account.fields, token=token)
                if email:
                    account.extra["email"] = email
                for key in ("zcode", "zcode_exhausted", "zcode_offers"):
                    account.extra.pop(key, None)
                self._paused.pop(account.id, None)
                account.mark(ST_UNKNOWN, "вход обновлён — нужна проверка")
                app_state.save_accounts()
                return account
        account = app_state.add_account(self.id, fields={"token": token}, label=email or "ZCode",
                                        extra={"email": email} if email else {})
        account.order = app_state.next_order(self.id)
        app_state.save_accounts()
        app_state.seed_provider_models(self.id, self.id, self.seed_models())
        try:
            from ..model_refresh import refresher
            spawn_background(refresher.refresh(providers=[self.id], reason="account-added"))
        except Exception:
            pass
        return account

def _finish(stop_reason: Any) -> str:
    return {"max_tokens": "length", "tool_use": "tool_calls", "refusal": "content_filter",
            "model_context_window_exceeded": "length"}.get(str(stop_reason or ""), "")
