"""Quick Ask: the model source — MemuAI Proxy (default) or the user's own OpenAI-compatible endpoint.

Round 23. Settings → Быстрый доступ → «Модель» has an explicit choice:

* «Через MemuAI Proxy» — as before: the answer streams through ``/api/admin/playground/chat`` of the
  local Proxy, models and their capabilities come from the Proxy.
* «Свой эндпоинт» — any OpenAI-compatible server (Ollama, LM Studio, vLLM, OpenRouter, OpenAI, a company
  gateway…): ``<base>/chat/completions`` (streaming SSE) and ``<base>/models``.

Where things live:

* preferences (``preferences.json``, normalized by :func:`app.quick_ask_core.normalize_prefs`):
  ``quick_ask_source`` (``proxy`` | ``custom``), ``quick_ask_endpoint_url``, ``quick_ask_endpoint_model``,
  ``quick_ask_endpoint_advanced_model``, ``quick_ask_endpoint_vision`` (``auto`` | ``yes`` | ``no``) —
  nothing secret;
* the API key — ``memuai/[profiles/<id>/]quick_endpoint.json`` protected with DPAPI for the current Windows
  user (like the other secrets of the app); never returned by any route, never logged. Off Windows (dev) it
  is only base64 and the status says so (``scheme: "plain"``);
* the last ``/models`` answer — the same file (``models``), so the popup opens without a network round trip.

Errors are honest and specific: wrong address, nothing listens, timeout, 401/403 (key), 404 (wrong path —
most servers want ``…/v1``), not JSON, the model is unknown.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional
from urllib.parse import urlsplit

log = logging.getLogger("memuai.quick.endpoint")

IS_WINDOWS = sys.platform == "win32"
SOURCES = ("proxy", "custom")
VISION = ("auto", "yes", "no")
MAX_MODELS = 400
CONNECT_TIMEOUT = 6.0
READ_TIMEOUT = 120.0
_ENTROPY = b"MemuAI.quick.endpoint.v1"


# ------------------------------------------------------------------ settings

def normalize_url(value: Any) -> str:
    """A base URL as the user pasted it → ``http(s)://host[:port]/path`` without a trailing slash.

    Pasted full paths are cut back to the base (``…/v1/chat/completions`` → ``…/v1``). Anything that is not
    http(s) becomes ``""`` (the settings show it as an error before saving)."""
    text = str(value or "").strip()
    if not text:
        return ""
    if not re.match(r"^[a-z][a-z0-9+.-]*://", text, re.I):
        text = "http://" + text if re.match(r"^(localhost|127\.|\[?::1|\d+\.\d+\.\d+\.\d+)", text) else "https://" + text
    parts = urlsplit(text)
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc or any(ch.isspace() for ch in text):
        return ""
    try:
        port_ok = parts.port is None or 0 < parts.port < 65536
    except ValueError:          # «javascript:alert(1)» / «host:abc» → https://javascript:alert(1) had a bogus port
        port_ok = False
    if not port_ok or not parts.hostname:
        return ""
    path = re.sub(r"/+$", "", parts.path or "")
    path = re.sub(r"/(chat/completions|completions|models|responses)$", "", path, flags=re.I)
    return f"{parts.scheme.lower()}://{parts.netloc}{path}"[:500]


def normalize_prefs(out: Dict[str, Any]) -> Dict[str, Any]:
    source = str(out.get("quick_ask_source") or "proxy").strip().lower()
    out["quick_ask_source"] = source if source in SOURCES else "proxy"
    out["quick_ask_endpoint_url"] = normalize_url(out.get("quick_ask_endpoint_url"))
    for key in ("quick_ask_endpoint_model", "quick_ask_endpoint_advanced_model"):
        out[key] = str(out.get(key) or "").strip()[:300]
    if out["quick_ask_endpoint_advanced_model"] == out["quick_ask_endpoint_model"]:
        out["quick_ask_endpoint_advanced_model"] = ""
    vision = str(out.get("quick_ask_endpoint_vision") or "auto").strip().lower()
    out["quick_ask_endpoint_vision"] = vision if vision in VISION else "auto"
    return out


def active(prefs: Dict[str, Any]) -> bool:
    """The user chose their own endpoint and gave an address."""
    return str(prefs.get("quick_ask_source") or "") == "custom" and bool(normalize_url(prefs.get("quick_ask_endpoint_url")))


def model_settings(prefs: Dict[str, Any]) -> Dict[str, Any]:
    """The preferences as the answer loop sees them: with a custom endpoint its models take the places of
    ``quick_ask_model`` / ``quick_ask_advanced_model`` / ``default_model`` (the Proxy choices stay untouched
    in preferences and come back when the user switches back)."""
    if not active(prefs):
        return prefs
    out = dict(prefs)
    out["quick_ask_model"] = str(prefs.get("quick_ask_endpoint_model") or "")
    out["default_model"] = out["quick_ask_model"]
    out["quick_ask_advanced_model"] = str(prefs.get("quick_ask_endpoint_advanced_model") or "")
    return out


def host_of(url: str) -> str:
    try:
        return urlsplit(url).netloc
    except ValueError:
        return ""


def label_of(url: str) -> str:
    """A friendly name for the chip: known services by host, otherwise host[:port]."""
    host = host_of(url).lower()
    known = (("openrouter.ai", "OpenRouter"), ("api.openai.com", "OpenAI"), ("api.groq.com", "Groq"),
             ("api.deepseek.com", "DeepSeek"), ("api.mistral.ai", "Mistral"), ("api.together.xyz", "Together"),
             ("generativelanguage.googleapis.com", "Google AI"), ("api.x.ai", "xAI"), ("api.anthropic.com", "Anthropic"))
    for needle, name in known:
        if needle in host:
            return name
    if host.endswith(":11434"):
        return "Ollama"
    if host.endswith(":1234"):
        return "LM Studio"
    return host or "endpoint"


# ------------------------------------------------------------------ secret storage

def _store_path() -> Path:
    from .pgdata import MEMU_DIR
    try:
        from .pgdata import _profile_data_name
        name = _profile_data_name("quick_endpoint.json")
    except Exception:
        name = "quick_endpoint.json"
    return Path(MEMU_DIR) / name


def _read() -> Dict[str, Any]:
    try:
        data = json.loads(_store_path().read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(data: Dict[str, Any]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def _dpapi(data: bytes, *, protect: bool) -> bytes:  # pragma: no cover - Windows only
    """CryptProtectData / CryptUnprotectData for the current Windows user (explicit 64-bit-safe signatures)."""
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(DATA_BLOB), (wintypes.LPCWSTR if protect else ctypes.POINTER(wintypes.LPWSTR)),
                   ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
    fn.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    src = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(src, ctypes.POINTER(ctypes.c_char)))
    ent = ctypes.create_string_buffer(_ENTROPY, len(_ENTROPY))
    blob_ent = DATA_BLOB(len(_ENTROPY), ctypes.cast(ent, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    if not fn(ctypes.byref(blob_in), None, ctypes.byref(blob_ent), None, None, 0x1, ctypes.byref(blob_out)):
        raise OSError(f"DPAPI error {ctypes.get_last_error()}")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


def _protect(text: str) -> Dict[str, str]:
    raw = text.encode("utf-8")
    if IS_WINDOWS:
        try:
            return {"scheme": "dpapi", "blob": base64.b64encode(_dpapi(raw, protect=True)).decode("ascii")}
        except Exception as exc:  # noqa: BLE001
            log.warning("quick endpoint: DPAPI protect failed: %s", type(exc).__name__)
    return {"scheme": "plain", "blob": base64.b64encode(raw).decode("ascii")}


def _unprotect(record: Dict[str, Any]) -> str:
    blob = base64.b64decode(str(record.get("blob") or "").encode("ascii"))
    if record.get("scheme") == "dpapi":
        return _dpapi(blob, protect=False).decode("utf-8")
    return blob.decode("utf-8")


def set_key(key: str) -> Dict[str, Any]:
    key = str(key or "").strip()
    if not key:
        return clear_key()
    if len(key) > 8000 or re.search(r"\s", key):
        raise ValueError("Ключ не должен содержать пробелов и переводов строк")
    data = _read()
    data["key"] = {**_protect(key), "hint": _hint(key), "at": int(time.time())}
    _write(data)
    return key_status()


def clear_key() -> Dict[str, Any]:
    data = _read()
    if data.pop("key", None) is not None:
        _write(data)
    return key_status()


def get_key() -> str:
    record = _read().get("key")
    if not isinstance(record, dict):
        return ""
    try:
        return _unprotect(record)
    except Exception as exc:  # noqa: BLE001 - another Windows user / a damaged file
        log.warning("quick endpoint: the key cannot be decrypted: %s", type(exc).__name__)
        return ""


def _hint(key: str) -> str:
    return ("…" + key[-4:]) if len(key) >= 12 else "…"


def key_status() -> Dict[str, Any]:
    record = _read().get("key")
    if not isinstance(record, dict):
        return {"set": False}
    return {"set": True, "scheme": str(record.get("scheme") or "plain"), "hint": str(record.get("hint") or "…")}


# ------------------------------------------------------------------ models

def vision_of(model: str, override: str = "auto") -> Optional[bool]:
    """``True``/``False`` when known, ``None`` when nothing says (the window then allows a picture)."""
    if override == "yes":
        return True
    if override == "no":
        return False
    try:
        from . import model_capabilities
        value = model_capabilities.heuristics(model).get("vision")
        return value if isinstance(value, bool) else None
    except Exception:
        return None


def model_rows(ids: List[str], override: str = "auto") -> List[Dict[str, Any]]:
    rows = []
    for model in ids[:MAX_MODELS]:
        vision = vision_of(model, override)
        caps: Dict[str, Any] = {"vision": vision} if vision is not None else {}
        if vision is False and override != "no":
            caps["vision_guess"] = True       # Round 31: a guess from the name — the window offers «try» / «probe»
        try:   # Round 34: the reasoning levels this model takes (the window's «Размышления» row)
            from . import reasoning_levels
            prof = reasoning_levels.public(reasoning_levels.profile(model, "custom", model))
            if prof.get("kind") in ("levels", "toggle"):
                caps["reasoning_profile"] = prof
        except Exception:  # noqa: BLE001
            pass
        rows.append({"id": model, "caps": caps})
    return rows


def cached_models(url: str) -> List[str]:
    data = _read()
    cache = data.get("models") if isinstance(data.get("models"), dict) else {}
    if cache.get("url") != url:
        return []
    return [str(m) for m in (cache.get("ids") or []) if isinstance(m, str)][:MAX_MODELS]


def _save_models(url: str, ids: List[str]) -> None:
    data = _read()
    data["models"] = {"url": url, "ids": ids[:MAX_MODELS], "at": int(time.time())}
    try:
        _write(data)
    except OSError:
        pass


def public_config(prefs: Dict[str, Any]) -> Dict[str, Any]:
    """What the popup and the settings may know (never the key)."""
    url = normalize_url(prefs.get("quick_ask_endpoint_url"))
    source = "custom" if active(prefs) else "proxy"
    out: Dict[str, Any] = {"source": source, "chosen": str(prefs.get("quick_ask_source") or "proxy"), "url": url,
                           "host": host_of(url), "label": label_of(url) if url else "", "key": key_status()}
    if source == "custom":
        # Round 34: the models chosen in Settings come with their capabilities even before /models was read
        chosen = [m for m in (str(prefs.get("quick_ask_endpoint_model") or "").strip(),
                              str(prefs.get("quick_ask_endpoint_advanced_model") or "").strip()) if m]
        ids = list(dict.fromkeys(chosen + cached_models(url)))
        out["models"] = model_rows(ids, str(prefs.get("quick_ask_endpoint_vision") or "auto"))
    return out


# ------------------------------------------------------------------ HTTP

def _headers(key: str) -> Dict[str, str]:
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
               "User-Agent": "MemuAI-QuickAsk/3.0"}
    if key:
        headers["Authorization"] = "Bearer " + key
    return headers


def explain(status: int, body: str, *, url: str, lang: str = "ru", what: str = "") -> str:
    """An HTTP error of the endpoint → one clear sentence (plus the server's own message when short)."""
    en = lang == "en"
    detail = ""
    try:
        data = json.loads(body or "{}")
        err = data.get("error") if isinstance(data, dict) else None
        detail = str((err.get("message") if isinstance(err, dict) else err) or data.get("message") or data.get("detail") or "")
    except ValueError:
        detail = re.sub(r"<[^>]+>", " ", body or "")
    detail = " ".join(detail.split())[:240]
    # Round 26: «model X does not exist» came as «HTTP 404: на сервере нет /chat/completions» — the path is fine.
    unknown_model = status in (400, 404, 422) and what == "/chat/completions" and "parameter" not in detail.lower() and bool(
        re.search(r"model\b[^.]{0,80}?(?:not (?:exist|found|available)|does ?n[o']t exist|is unknown)|"
                  r"(?:unknown|invalid|no such|unsupported) model|failed to get model|model_not_found", detail, re.I))
    if unknown_model:
        text = (f"The server does not know this model (HTTP {status}): check the model name in Settings → Quick Ask."
                if en else f"Сервер не знает эту модель (HTTP {status}): проверьте имя модели в Настройки → Быстрый доступ.")
    elif status in (401, 403):
        text = ("The endpoint rejected the API key (HTTP %d). Check the key in Settings → Quick Ask." % status if en else
                "Эндпоинт не принял API-ключ (HTTP %d). Проверьте ключ в Настройки → Быстрый доступ." % status)
    elif status == 404:
        base_has_v1 = bool(re.search(r"/v\d+[a-z]*$", urlsplit(url).path or ""))
        hint = "" if base_has_v1 else (" Most servers expect the address to end with /v1." if en else
                                       " Обычно адрес должен заканчиваться на /v1.")
        text = ((f"Not found (HTTP 404): {what or 'this path'} does not exist on the server." if en else
                 f"Не найдено (HTTP 404): на сервере нет {what or 'такого пути'}.") + hint)
    elif status == 429:
        text = "Too many requests (HTTP 429): the endpoint asks to wait." if en else "Слишком много запросов (HTTP 429): эндпоинт просит подождать."
    elif status >= 500:
        text = f"The endpoint failed (HTTP {status})." if en else f"Ошибка на стороне эндпоинта (HTTP {status})."
    else:
        text = f"The endpoint answered HTTP {status}." if en else f"Эндпоинт ответил HTTP {status}."
    return text + (f" {detail}" if detail and detail not in text else "")


def network_error(exc: BaseException, *, url: str, lang: str = "ru") -> str:
    import httpx
    en = lang == "en"
    host = host_of(url) or url
    if isinstance(exc, httpx.ConnectTimeout) or isinstance(exc, httpx.ReadTimeout) or isinstance(exc, httpx.TimeoutException):
        return f"{host} did not answer in time." if en else f"{host} не ответил вовремя."
    if isinstance(exc, httpx.ProxyError):
        return (f"The proxy server refused the connection to {host}: {exc}" if en else
                f"Прокси-сервер не пропустил соединение с {host}: {exc}")[:300]
    if isinstance(exc, httpx.ConnectError):
        text = str(exc)
        if re.search(r"name|resolve|getaddrinfo|nodename", text, re.I):
            return f"Unknown host {host}: check the address." if en else f"Адрес {host} не найден: проверьте его."
        if re.search(r"ssl|certificate", text, re.I):
            return f"TLS/certificate error at {host}: {text[:160]}" if en else f"Ошибка TLS/сертификата у {host}: {text[:160]}"
        return (f"Nothing is listening at {host}: is the server running?" if en else
                f"По адресу {host} никто не отвечает: запущен ли сервер?")
    return (f"Network error: {exc}" if en else f"Сетевая ошибка: {exc}")[:300]


def is_local(url: str) -> bool:
    """A server on this machine or the LAN (Ollama, LM Studio, llama.cpp…)."""
    import ipaddress
    from urllib.parse import urlsplit
    host = (urlsplit(str(url or "")).hostname or "").strip("[]").lower()
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local


def _client(timeout_read: float = READ_TIMEOUT, url: str = ""):
    """Round 26: a local endpoint never goes through the system/env proxy. On Windows httpx takes the registry
    proxy (a VPN client's 127.0.0.1:port) but not its «bypass for local addresses» list, so Ollama at
    localhost:11434 answered with the proxy's 404/502 («на сервере нет /models»)."""
    import httpx
    return httpx.AsyncClient(timeout=httpx.Timeout(timeout_read, connect=CONNECT_TIMEOUT), follow_redirects=True,
                             trust_env=not is_local(url))


async def fetch_models(url: str, key: str, *, lang: str = "ru", save: bool = True) -> Dict[str, Any]:
    """``GET <base>/models`` → ``{ok, models: [ids]}`` or ``{ok: False, error}``."""
    url = normalize_url(url)
    en = lang == "en"
    if not url:
        return {"ok": False, "error": "Enter the endpoint address (http:// or https://)." if en else
                "Укажите адрес эндпоинта (http:// или https://)."}
    try:
        async with _client(20.0, url) as client:
            response = await client.get(url + "/models", headers=_headers(key))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": network_error(exc, url=url, lang=lang)}
    if response.status_code != 200:
        return {"ok": False, "status": response.status_code,
                "error": explain(response.status_code, response.text[:4000], url=url, lang=lang, what="/models")}
    try:
        data = response.json()
    except ValueError:
        return {"ok": False, "error": ("The answer of /models is not JSON — is this an OpenAI-compatible address?" if en else
                                       "Ответ /models — не JSON. Это точно OpenAI-совместимый адрес?")}
    rows = data.get("data") if isinstance(data, dict) else data
    if isinstance(data, dict) and not isinstance(rows, list):
        rows = data.get("models")
    ids: List[str] = []
    for row in rows if isinstance(rows, list) else []:
        model = row.get("id") or row.get("name") or row.get("model") if isinstance(row, dict) else row
        if isinstance(model, str) and model.strip() and model.strip() not in ids:
            ids.append(model.strip()[:300])
    ids.sort(key=str.lower)
    if save:
        _save_models(url, ids)
    return {"ok": True, "models": ids}


def openai_body(payload: Dict[str, Any], *, stream: bool, url: str = "") -> Dict[str, Any]:
    """The Proxy-playground payload of the answer loop → a plain OpenAI chat-completions body."""
    body = {"model": str(payload.get("model") or ""), "messages": payload.get("messages") or [], "stream": stream}
    if payload.get("max_tokens"):
        body["max_tokens"] = int(payload["max_tokens"])
    if payload.get("effort_user"):
        # Round 34 (r34-quick-core): a reasoning level the user picked in the window is sent in the endpoint's dialect
        # (reasoning_effort / enable_thinking+thinking_budget / thinking / reasoning{}); before, an own endpoint never
        # got any — a thinking model could not be made faster.
        try:
            from . import reasoning_levels
            model = body["model"]
            prof = reasoning_levels.profile(model, "custom", model)
            level = reasoning_levels.resolve(prof, payload.get("thinking"), payload.get("effort"))
            reasoning_levels.apply_openai_compatible(body, prof, level, url)
        except Exception as exc:  # noqa: BLE001 - the answer matters more than the level
            log.debug("quick endpoint: reasoning level not applied: %s", exc)
    return body


def _reasoning_rejected(status: int, raw: str, body: Dict[str, Any]) -> bool:
    """Round 34: the endpoint refused a request that carried our reasoning fields (400/422 naming them)."""
    from . import reasoning_levels
    if status not in (400, 422) or not any(key in body for key in reasoning_levels.OWN_FIELDS):
        return False
    return bool(re.search(r"reason|effort|think|budget|unsupported|not supported|unknown|invalid|extra", raw or "", re.I))


class ReasoningFilter:
    """Round 26: the visible answer of a foreign endpoint without the model's service markup.

    The MemuAI Proxy scrubs it for the chats; an own endpoint streamed it straight into the popup and the saved
    chat: an inline ``<think>…</think>`` (llama.cpp, older Ollama/LM Studio builds, DeepSeek-R1 distills — only
    before the answer starts) and DeepSeek's ``<ds_safety>…</ds_safety>`` verdict (anywhere). Tags may be split
    across chunks, so a possible tag start at the end of a chunk is held back. ``feed`` → ``(visible, reasoning)``.
    """

    THINK = ("think", "thinking", "reasoning")
    SERVICE = ("ds_safety",)

    def __init__(self) -> None:
        self._pending = ""
        self._inside = ""
        self._started = False       # visible text was shown: a later <think> is the answer's own text

    def _tags(self) -> tuple:
        return self.SERVICE if self._started else self.THINK + self.SERVICE

    def feed(self, text: str) -> tuple:
        buf = self._pending + str(text or "")
        self._pending = ""
        out: List[str] = []
        hidden: List[str] = []
        while buf:
            if self._inside:
                close = f"</{self._inside}>"
                end = buf.find(close)
                if end < 0:
                    keep = _partial_tail(buf, close)
                    if self._inside in self.THINK:
                        hidden.append(buf[:len(buf) - len(keep)])
                    self._pending = keep
                    break
                if self._inside in self.THINK:
                    hidden.append(buf[:end])
                buf = buf[end + len(close):]
                if self._inside in self.THINK:
                    buf = buf.lstrip()
                self._inside = ""
                continue
            start = buf.find("<")
            visible = buf if start < 0 else buf[:start]
            if visible:
                out.append(visible)
                self._started = self._started or bool(visible.strip())
            if start < 0:
                break
            buf = buf[start:]
            match = re.match(r"<([a-z_]+)(\s[^>]*)?(/?)>", buf)
            if match and match.group(1) in self._tags():
                if not match.group(3):
                    self._inside = match.group(1)
                buf = buf[match.end():]
                continue
            if ">" not in buf and len(buf) < 40 and any(("<" + tag).startswith(buf[:len(tag) + 1]) or
                                                        buf.startswith("<" + tag) for tag in self._tags()):
                self._pending = buf
                break
            out.append("<")
            self._started = True
            buf = buf[1:]
        return "".join(out), "".join(hidden)

    def finish(self) -> str:
        """Held-back text at the end of the stream; an unclosed service/think block is dropped."""
        tail = "" if self._inside else self._pending
        self._pending = self._inside = ""
        return tail

    def clean(self, text: str) -> str:
        """A whole (non-streamed) answer."""
        visible, _reasoning = self.feed(text)
        return (visible + self.finish()).strip()


def _partial_tail(text: str, needle: str) -> str:
    for size in range(min(len(text), len(needle) - 1), 0, -1):
        if needle.startswith(text[-size:]):
            return text[-size:]
    return ""


def _filter_line(line: str, filt: ReasoningFilter) -> List[str]:
    """One SSE line of the endpoint with its ``delta.content`` passed through ``filt`` (other lines as they are)."""
    if not line.startswith("data:"):
        return [line]
    raw = line[5:].strip()
    if raw == "[DONE]":
        tail = filt.finish()
        return ([_delta_line(tail)] if tail else []) + [line]
    try:
        item = json.loads(raw)
        delta = item["choices"][0]["delta"]
    except (ValueError, KeyError, IndexError, TypeError):
        return [line]
    if not isinstance(delta, dict) or not isinstance(delta.get("content"), str):
        return [line]
    visible, reasoning = filt.feed(delta["content"])
    delta["content"] = visible
    if reasoning and not delta.get("reasoning_content"):
        delta["reasoning_content"] = reasoning
    return ["data: " + json.dumps(item, ensure_ascii=False)]


def _delta_line(text: str) -> str:
    return "data: " + json.dumps({"choices": [{"delta": {"content": text}}]}, ensure_ascii=False)


async def stream_lines(url: str, key: str, payload: bytes, *, lang: str = "ru") -> AsyncIterator[str]:
    """SSE lines of ``POST <base>/chat/completions`` (stream) — the same shape the answer loop reads from the
    Proxy (``data: {choices:[{delta:{content}}]}``). Errors become one ``data: {"error": …}`` line."""
    url = normalize_url(url)
    try:
        body = openai_body(json.loads(payload.decode("utf-8")), stream=True, url=url)
    except ValueError as exc:
        yield "data: " + json.dumps({"error": {"message": str(exc)}})
        return
    try:
        async with _client(url=url) as client:
            for attempt in (0, 1):
                async with client.stream("POST", url + "/chat/completions", content=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                         headers=_headers(key)) as response:
                    if response.status_code != 200:
                        raw = (await response.aread()).decode("utf-8", "replace")
                        if not attempt and _reasoning_rejected(response.status_code, raw, body):
                            # Round 34: the endpoint does not take the picked reasoning level — answer without it
                            from . import reasoning_levels
                            for field in reasoning_levels.OWN_FIELDS:
                                body.pop(field, None)
                            continue
                        yield "data: " + json.dumps({"error": {"message": explain(response.status_code, raw[:4000], url=url, lang=lang,
                                                                                  what="/chat/completions")}}, ensure_ascii=False)
                        return
                    kind = response.headers.get("content-type", "")
                    if "text/event-stream" not in kind:
                        # A server that ignores "stream": one JSON answer.
                        raw = (await response.aread()).decode("utf-8", "replace")
                        try:
                            data = json.loads(raw)
                            text = (((data.get("choices") or [{}])[0].get("message") or {}).get("content")) or ""
                        except (ValueError, AttributeError, IndexError):
                            text = ""
                        text = ReasoningFilter().clean(text) if isinstance(text, str) else ""
                        if text:
                            yield _delta_line(text)
                        else:
                            yield "data: " + json.dumps({"error": {"message": "Empty answer from the endpoint." if lang == "en" else "Эндпоинт вернул пустой ответ."}}, ensure_ascii=False)
                        yield "data: [DONE]"
                        return
                    filt = ReasoningFilter()
                    done = False
                    async for line in response.aiter_lines():
                        for out in _filter_line(line, filt):
                            done = done or out.startswith("data: [DONE]") or out.strip() == "data:[DONE]"
                            yield out
                    tail = "" if done else filt.finish()
                    if tail:            # a server that closed the stream without [DONE]
                        yield _delta_line(tail)
                    return
    except Exception as exc:  # noqa: BLE001
        yield "data: " + json.dumps({"error": {"message": network_error(exc, url=url, lang=lang)}}, ensure_ascii=False)


async def complete(url: str, key: str, model: str, messages: List[Dict[str, Any]], *, max_tokens: int = 0,
                   lang: str = "ru") -> str:
    """One non-streaming answer (custom routines' «ask AI» step). Raises RuntimeError with a clear message."""
    url = normalize_url(url)
    body = {"model": model, "messages": messages, "stream": False}
    if max_tokens:
        body["max_tokens"] = max_tokens
    try:
        async with _client(url=url) as client:
            response = await client.post(url + "/chat/completions", content=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                         headers=_headers(key))
            if response.status_code == 400 and max_tokens and "max_completion_tokens" in response.text[:4000]:
                # Round 26: OpenAI reasoning models (o-series, GPT-5) reject max_tokens — «Проверить подключение» failed.
                body["max_completion_tokens"] = max(max_tokens, 64)
                body.pop("max_tokens", None)
                response = await client.post(url + "/chat/completions", content=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                             headers=_headers(key))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(network_error(exc, url=url, lang=lang)) from exc
    if response.status_code != 200:
        raise RuntimeError(explain(response.status_code, response.text[:4000], url=url, lang=lang, what="/chat/completions"))
    try:
        data = response.json()
        return ReasoningFilter().clean(str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""))
    except (ValueError, AttributeError, IndexError) as exc:
        raise RuntimeError("The endpoint answer is not OpenAI-compatible." if lang == "en" else
                           "Ответ эндпоинта не похож на OpenAI-совместимый.") from exc


async def test(url: str, key: str, model: str = "", *, lang: str = "ru") -> Dict[str, Any]:
    """Settings → «Проверить подключение»: /models, then (with a model) a 1-word completion."""
    started = time.monotonic()
    listed = await fetch_models(url, key, lang=lang)
    out: Dict[str, Any] = {"ok": False, "models": listed.get("models") or [], "steps": []}
    out["steps"].append({"step": "models", "ok": bool(listed.get("ok")), "error": listed.get("error", ""),
                         "count": len(listed.get("models") or [])})
    if not model:
        out["ok"] = bool(listed.get("ok"))
        out["error"] = listed.get("error", "")
        out["ms"] = int((time.monotonic() - started) * 1000)
        return out
    try:
        answer = await complete(url, key, model, [{"role": "user", "content": "Reply with the single word: OK"}],
                                max_tokens=8, lang=lang)
        out["steps"].append({"step": "chat", "ok": True, "answer": answer.strip()[:60]})
        out["ok"] = True
    except RuntimeError as exc:
        out["steps"].append({"step": "chat", "ok": False, "error": str(exc)})
        out["error"] = str(exc)
        if listed.get("ok") and model not in (listed.get("models") or []) and listed.get("models"):
            out["error"] += (" The model «%s» is not in the list of /models." % model if lang == "en" else
                             " Модели «%s» нет в списке /models." % model)
    out["ms"] = int((time.monotonic() - started) * 1000)
    return out
