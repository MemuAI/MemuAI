"""Model backends of MemuCode.

``ProxyBackend`` — the default: every model of the user's MemuAI accounts
through the local MemuAI Proxy (``/api/admin/playground/chat``, the same
bridge the desktop app uses). The Proxy is discovered through
``memuai/proxy.json`` and started in the tray when it is not running.

``OpenAIBackend`` / ``AnthropicBackend`` — direct API connections configured
in MemuCode (``/proxy add``). Their models appear as ``@<connection>:<model>``
next to the Proxy models and support native tool calling.

Every backend turns its wire format into the same stream of
:class:`StreamEvent` objects, so the agent loop never sees a provider.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from .. import model_info as model_catalog
from ..config import DATA_DIR
from .i18n import tr

SUBSCRIPTION_PROVIDERS = {"genspark", "kiro", "antigravity", "geminiweb", "grok"}


class LazyModule:
    """Модуль, который импортируется при первом обращении к атрибуту.

    Round 21 (производительность): httpx (~70–120 мс вместе с его CLI-частью)
    больше не импортируется до первого кадра интерфейса — его подгружает
    фоновый поток прогрева (``TerminalUI._warm_up``) или первый сетевой
    запрос. После загрузки имя в модуле-владельце заменяется настоящим модулем.
    """

    def __init__(self, name: str, namespace: Dict[str, Any], alias: str = "") -> None:
        self._name, self._namespace, self._alias = name, namespace, alias or name

    def __getattr__(self, attribute: str) -> Any:
        import importlib
        module = importlib.import_module(self._name)
        self._namespace[self._alias] = module
        return getattr(module, attribute)


httpx = LazyModule("httpx", globals())

_SSL_LOCK = threading.Lock()
_SSL_CONTEXT: Any = None


def shared_ssl_context() -> Any:
    """One TLS context for every https client: loading the CA bundle costs ~0.25 s, so it is built once
    (the start-up warm-up thread does it) instead of once per client on the interface thread."""
    global _SSL_CONTEXT
    with _SSL_LOCK:
        if _SSL_CONTEXT is None:
            _SSL_CONTEXT = httpx.create_ssl_context()
        return _SSL_CONTEXT


def _is_loopback_url(url: str) -> bool:
    from urllib.parse import urlsplit
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    import ipaddress
    try:
        # A literal address only: "127.0.0.1.nip.io" or "127.example.com" is a public name and must keep
        # going through the user's proxy.
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def http_client(timeout: Any, *, url: str = "", **kwargs: Any) -> httpx.AsyncClient:
    """An async client; plain-http (the local Proxy) needs no TLS context at all.

    A server on this computer (LM Studio, Ollama, a local MCP server) is never reached through the system
    HTTP(S)_PROXY: with a corporate proxy and no NO_PROXY the request went to the proxy, which answered
    "HTTP 404: not found" for 127.0.0.1."""
    if _is_loopback_url(url):
        kwargs.setdefault("trust_env", False)
    if url.startswith("http://"):
        return httpx.AsyncClient(timeout=timeout, verify=False, **kwargs)
    return httpx.AsyncClient(timeout=timeout, verify=shared_ssl_context(), **kwargs)


# ---------------------------------------------------------------- data

@dataclass
class ModelInfo:
    id: str
    provider: str = ""
    backend: str = "proxy"          # proxy | direct
    connection: str = ""
    raw: str = ""                   # id at the backend
    label: str = ""
    context: int = 0
    output: int = 0
    context_source: str = ""
    vision: bool = False
    vision_source: str = ""         # provider = the model list itself says so; api = known API; empty = a guess
    thinking: bool = False
    search: bool = False
    image: bool = False
    native_tools: bool = False
    #: Round 33 (r33-thinking): the reasoning levels the Proxy says this model has ("auto" first; [] — none).
    reasoning_options: Optional[List[str]] = None
    #: Round 33 (r33c-models-lists): the readable name (Proxy's catalogue row ``name``, else app.model_names).
    name: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class Usage:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    reasoning: int = 0
    cost: Optional[float] = None
    estimated: bool = True
    requests: int = 0

    def add(self, other: "Usage") -> None:
        self.input += other.input
        self.output += other.output
        self.cache_read += other.cache_read
        self.cache_write += other.cache_write
        self.reasoning += other.reasoning
        self.requests += other.requests or 1
        if other.cost is not None:
            self.cost = (self.cost or 0.0) + other.cost
        self.estimated = self.estimated and other.estimated

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class StreamEvent:
    kind: str                       # text | reasoning | tool_call | usage | search | status | done | accepted | alive
    text: str = ""
    call: Optional[Dict[str, Any]] = None
    usage: Optional[Usage] = None
    finish: str = ""
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatRequest:
    model: str
    system: str
    messages: List[Dict[str, Any]]
    tools: Optional[List[Dict[str, Any]]] = None
    effort: str = "auto"
    thinking: bool = False
    web_search: bool = False
    max_tokens: int = 0
    temperature: Optional[float] = None
    session: str = ""
    show_thinking: bool = False
    tool_choice: str = ""            # "" = the provider decides; "none" = answer in text (tools stay declared)


#: The fields of a chat message every OpenAI-compatible API accepts. Session bookkeeping (``is_error``,
#: ``_anthropic``) is not sent: strict servers answer an unknown field with HTTP 400.
_OPENAI_MESSAGE_KEYS = ("role", "content", "tool_calls", "tool_call_id", "name")


def openai_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for message in messages:
        row = {key: message[key] for key in _OPENAI_MESSAGE_KEYS if key in message}
        if row.get("role") == "tool":
            row.pop("name", None)
            if message.get("is_error") and isinstance(row.get("content"), str) \
                    and not row["content"].startswith("Error"):
                row["content"] = "Error: " + row["content"]
            if row.get("content") in (None, ""):
                row["content"] = "(no output)"
        out.append(row)
    return out


def tool_arguments(raw: str) -> Dict[str, Any]:
    """The arguments of a native tool call from the JSON text a provider streamed. Strict JSON first, then the
    forgiving reader of the text protocol (raw line breaks, a trailing comma); arguments that stay unreadable or
    were cut off come back as ``{"_invalid_json": <text>}`` so the agent can tell the model exactly that."""
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
    except ValueError:
        value = None
        try:
            from .protocol import read_json
            read = read_json(text, 0)
            if not read.incomplete and isinstance(read.value, dict):
                value = read.value
        except Exception:
            value = None
        if value is None:
            return {"_invalid_json": text[:2000]}
    if isinstance(value, str):                     # arguments double-encoded as a JSON string
        try:
            value = json.loads(value)
        except ValueError:
            return {"_invalid_json": text[:2000]}
    return value if isinstance(value, dict) else {"_invalid_json": text[:2000]}


class LLMError(Exception):
    def __init__(self, message: str, *, status: int = 0, kind: str = "", hint: str = "",
                 retryable: bool = False, provider: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.kind = kind
        self.hint = hint
        self.retryable = retryable
        #: Round 32: the upstream's own text without the hint, and who answered — for an open error report
        #: (app/cli/errors.py) instead of a guess from the status code.
        self.raw_message = str(message)
        self.provider = provider
        #: Round 33: when the provider said its limit ends (``ratelimit.py``; ``time.time()`` seconds, 0 = unknown).
        self.retry_at = 0.0

    def __str__(self) -> str:
        base = super().__str__()
        return f"{base}\n{self.hint}" if self.hint else base


# ---------------------------------------------------------------- stream filters

class ThinkTagFilter:
    """Providers configured with ``reasoning_output = think_tags`` send the
    reasoning inside ``<think>…</think>`` in the content. Split it out."""

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self.inside = False
        self.pending = ""
        self.started = False
        self.service = ServiceTagFilter()

    def feed(self, text: str) -> List[Tuple[str, str]]:
        out: List[Tuple[str, str]] = []
        data = self.pending + self.service.feed(text)
        self.pending = ""
        while data:
            tag = self.CLOSE if self.inside else self.OPEN
            index = data.find(tag)
            if index >= 0:
                if index:
                    out.append(("reasoning" if self.inside else "text", data[:index]))
                data = data[index + len(tag):]
                if not self.inside and data.startswith("\n"):
                    data = data[1:]
                self.inside = not self.inside
                if not self.inside:
                    data = data.lstrip("\n")
                continue
            # keep a possible partial tag for the next chunk
            keep = 0
            for size in range(min(len(tag) - 1, len(data)), 0, -1):
                if tag.startswith(data[-size:]):
                    keep = size
                    break
            if keep:
                self.pending = data[-keep:]
                data = data[:-keep]
            if data:
                out.append(("reasoning" if self.inside else "text", data))
            data = ""
        return out

    def flush(self) -> List[Tuple[str, str]]:
        rest, self.pending = self.pending + self.service.flush(), ""
        if not rest:
            return []
        return [("reasoning" if self.inside else "text", rest)]


class ServiceTagFilter:
    """Drops provider service markup from the content: some DeepSeek deployments (Yandex Cloud) append
    ``<ds_safety>…</ds_safety>`` to the answer. The Proxy strips it (``app.chat``); a direct connection
    printed it into the terminal and the session. A possible tag start at the end of a chunk waits for the next."""

    TAGS = ("ds_safety",)

    def __init__(self) -> None:
        self.pending = ""
        self.inside = ""

    def feed(self, text: str) -> str:
        data, self.pending = self.pending + text, ""
        out: List[str] = []
        while data:
            if self.inside:
                close = f"</{self.inside}>"
                end = data.find(close)
                if end < 0:
                    self.pending = next((data[-size:] for size in range(min(len(close) - 1, len(data)), 0, -1)
                                         if close.startswith(data[-size:])), "")
                    break
                data, self.inside = data[end + len(close):], ""
                continue
            start = data.find("<")
            if start < 0:
                out.append(data)
                break
            out.append(data[:start])
            data = data[start:]
            tag = next((tag for tag in self.TAGS if data.startswith("<" + tag)
                        and len(data) > len(tag) + 1 and data[len(tag) + 1] in "> /"), "")
            if tag:
                gt = data.find(">")
                if gt < 0:
                    self.pending = data
                    break
                if data[gt - 1] != "/":
                    self.inside = tag
                data = data[gt + 1:]
            elif any(("<" + name).startswith(data) for name in self.TAGS):
                self.pending = data
                break
            else:
                out.append("<")
                data = data[1:]
        return "".join(out)

    def flush(self) -> str:
        """Text held back at the end; an unclosed service block is dropped."""
        rest = "" if self.inside else self.pending
        self.pending, self.inside = "", ""
        return rest


def _sse_json(line: str) -> Optional[Any]:
    if not line.startswith("data:"):
        return None
    raw = line[5:].strip()
    if not raw or raw == "[DONE]":
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


# ---------------------------------------------------------------- base

class Backend:
    name = "base"
    native_tools = False

    async def list_models(self) -> List[ModelInfo]:
        return []

    def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:  # pragma: no cover
        raise NotImplementedError

    async def complete(self, request: ChatRequest) -> Tuple[str, Usage]:
        """Whole answer as text (background jobs: titles, compaction, classifiers)."""
        parts: List[str] = []
        usage = Usage()
        async for event in self.stream(request):
            if event.kind == "text":
                parts.append(event.text)
            elif event.kind == "usage" and event.usage:
                usage = event.usage
        text = "".join(parts).strip()
        if not text:
            raise LLMError(tr("Модель вернула пустой ответ", "The model returned an empty answer"),
                           kind="empty")
        return text, usage

    async def close(self) -> None:
        return None


# ---------------------------------------------------------------- MemuAI Proxy

def _runtime_url(name: str) -> str:
    try:
        data = json.loads((Path(DATA_DIR) / "memuai" / name).read_text(encoding="utf-8"))
        url = str(data.get("url") or "").rstrip("/")
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme == "http" and (parsed.hostname or "").lower() in ("127.0.0.1", "localhost", "::1")
                and parsed.port and not parsed.username and parsed.path in ("", "/")):
            return url
    except (OSError, ValueError, TypeError):
        pass
    return ""


def _proxy_candidates() -> List[List[str]]:
    """How to start MemuAI Proxy from here: installed layout first, then source."""
    frozen = bool(getattr(sys, "frozen", False))
    rows: List[List[str]] = []
    if frozen:
        here = Path(sys.executable).resolve().parent
        for folder in (here, here.parent / "MemuAIProxy", here.parent):
            exe = folder / "MemuAIProxy.exe"
            if exe.is_file():
                rows.append([str(exe), "--background"])
    root = Path(__file__).resolve().parents[2]
    main = root / "main.py"
    if main.is_file():
        python = Path(sys.executable)
        if not frozen and os.name == "nt":
            windowless = python.with_name("pythonw.exe")
            if windowless.is_file():
                python = windowless
        if not frozen:
            rows.append([str(python), str(main), "--background"])
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "MemuAI" / "MemuAIProxy" / "MemuAIProxy.exe"
    if local.is_file() and [str(local), "--background"] not in rows:
        rows.append([str(local), "--background"])
    return rows


class ProxyBackend(Backend):
    name = "proxy"
    native_tools = False

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self._client: Optional[httpx.AsyncClient] = None
        self._url = ""
        self.last_error = ""
        self.launched = False

    # -------- discovery
    def base_url(self) -> str:
        configured = str(self.settings.get("proxy_url") or os.environ.get("MEMUAI_PROXY_URL") or "").rstrip("/")
        return configured or self._url or _runtime_url("proxy.json") or "http://127.0.0.1:8317"

    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = http_client(httpx.Timeout(900, connect=8), url=self.base_url(), trust_env=False)
        return self._client

    def headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        token = os.environ.get("MEMUAI_TOKEN", "")
        if token:
            headers["Authorization"] = "Bearer " + token
        return headers

    async def health(self, url: str = "") -> Optional[Dict[str, Any]]:
        target = (url or self.base_url()).rstrip("/")
        try:
            response = await self.client().get(target + "/health", timeout=3)
            data = response.json()
            if response.status_code == 200 and data.get("service") == "memuai-proxy":
                return data
        except Exception:
            return None
        return None

    async def ensure(self, *, launch: bool = True, wait: float = 20.0) -> Optional[Dict[str, Any]]:
        """A running Proxy, started in the tray if needed. None when unreachable."""
        data = await self.health()
        if data:
            return data
        discovered = _runtime_url("proxy.json")
        if discovered and discovered != self._url:
            self._url = discovered
            data = await self.health(discovered)
            if data:
                return data
        autostart = self.settings.get("proxy_autostart", True) and os.environ.get("MEMUCODE_NO_PROXY_LAUNCH") != "1"
        if not launch or not autostart or self.settings.get("proxy_url"):
            return None
        for command in _proxy_candidates():
            try:
                flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                subprocess.Popen(command, cwd=str(Path(command[-2] if len(command) > 2 else command[0]).parent),
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True)
                self.launched = True
                break
            except OSError as exc:
                self.last_error = str(exc)
        else:
            return None
        deadline = time.time() + wait
        while time.time() < deadline:
            await asyncio.sleep(0.4)
            url = _runtime_url("proxy.json")
            if url:
                self._url = url
            data = await self.health()
            if data:
                return data
        return None

    # -------- models
    async def list_models(self) -> List[ModelInfo]:
        if not await self.ensure(launch=True):
            raise LLMError(tr("MemuAI Proxy не запущен и не запускается",
                              "MemuAI Proxy is not running and could not be started"),
                           kind="offline",
                           hint=tr("Запустите MemuAI Proxy из меню «Пуск» или настройте прямой API командой /proxy add.",
                                   "Start MemuAI Proxy from the Start menu or set up a direct API with /proxy add."))
        response = await self.client().get(self.base_url() + "/api/admin/pg/state", headers=self.headers(),
                                           timeout=20)
        if response.status_code == 401:
            raise LLMError(tr("Proxy требует вход (он открыт в сеть)", "The Proxy requires sign-in (it listens on the network)"),
                           status=401, kind="proxy_auth")
        response.raise_for_status()
        data = response.json()
        rows: List[ModelInfo] = []
        for row in data.get("models") or []:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            caps = row.get("caps") or {}
            context = row.get("context") if isinstance(row.get("context"), dict) else {}
            rows.append(ModelInfo(
                id=str(row["id"]), provider=str(row.get("provider") or ""), backend="proxy",
                raw=str(row.get("raw_model") or row["id"]), label=str(context.get("label") or ""),
                context=int(context.get("context") or 0), output=int(context.get("output") or 0),
                context_source=str(context.get("source") or ""),
                vision=bool(caps.get("vision")), thinking=bool(caps.get("thinking")),
                search=bool(caps.get("search")), image=bool(caps.get("image")),
                reasoning_options=[str(x) for x in (caps["reasoning_profile"].get("options") or [])]
                if isinstance(caps.get("reasoning_profile"), dict) else None,
                name=str(row.get("name") or "")))
        return rows

    # -------- chat
    def _body(self, request: ChatRequest) -> Dict[str, Any]:
        messages = list(request.messages)
        if request.system:
            messages = [{"role": "system", "content": request.system}, *messages]
        body: Dict[str, Any] = {"model": request.model, "messages": messages,
                                "web_search": bool(request.web_search), "thinking": bool(request.thinking),
                                "prompt_caching": True, "tools_available": True}
        if request.effort and request.effort != "auto":
            body["effort"] = request.effort
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.max_tokens:
            body["max_tokens"] = int(request.max_tokens)
        return body

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        body = self._body(request)
        attempts = 0
        while True:
            attempts += 1
            started_output = False
            think = ThinkTagFilter()
            try:
                async with self.client().stream("POST", self.base_url() + "/api/admin/playground/chat",
                                                json=body, headers=self.headers()) as response:
                    if response.status_code != 200:
                        raw = (await response.aread()).decode("utf-8", "replace")
                        error = _proxy_http_error(response.status_code, raw)
                        error.retry_at = _limit_reset(response.headers, raw)       # Round 33: /auto-continue auto
                        raise error
                    yield StreamEvent("accepted")             # the Proxy took the request and is on it
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        if line.startswith(":"):
                            yield StreamEvent("alive", text=line[1:].strip())      # a keep-alive ping while it works
                            continue
                        item = _sse_json(line)
                        if item is None:
                            continue
                        if item.get("error"):
                            raise _proxy_stream_error(item["error"])
                        if item.get("object") == "memuai.event":
                            yield StreamEvent("search", data=item.get("web_search") or {})
                            continue
                        choice = ((item.get("choices") or [{}])[0]) if isinstance(item.get("choices"), list) else {}
                        delta = choice.get("delta") or {}
                        if delta.get("reasoning_content"):
                            started_output = True
                            yield StreamEvent("reasoning", text=str(delta["reasoning_content"]))
                        if delta.get("content"):
                            started_output = True
                            for kind, text in think.feed(str(delta["content"])):
                                yield StreamEvent(kind, text=text)
                        if isinstance(item.get("usage"), dict):
                            usage = item["usage"]
                            # Round 27: the Proxy forwards the provider's cache/reasoning counts and billed cost
                            # (app/chat.py); they were dropped here, so /cost showed «Cached 0» and priced cached
                            # input at the full rate.
                            details = usage.get("prompt_tokens_details") or {}
                            out_details = usage.get("completion_tokens_details") or {}
                            cost = usage.get("cost")
                            yield StreamEvent("usage", usage=Usage(
                                input=int(usage.get("prompt_tokens") or 0),
                                output=int(usage.get("completion_tokens") or 0),
                                cache_read=int(details.get("cached_tokens") or 0) if isinstance(details, dict) else 0,
                                cache_write=int(details.get("cache_write_tokens") or 0) if isinstance(details, dict) else 0,
                                reasoning=int(out_details.get("reasoning_tokens") or 0)
                                if isinstance(out_details, dict) else 0,
                                cost=float(cost) if isinstance(cost, (int, float)) and cost >= 0 else None,
                                # The Proxy says whose numbers these are: the provider's or its own estimate.
                                estimated=bool(usage.get("estimated", True)), requests=1))
                        if choice.get("finish_reason"):
                            for kind, text in think.flush():
                                yield StreamEvent(kind, text=text)
                            yield StreamEvent("done", finish=str(choice["finish_reason"]))
                for kind, text in think.flush():
                    yield StreamEvent(kind, text=text)
                return
            except httpx.TransportError as exc:
                if started_output or attempts > 2:
                    raise LLMError(tr("Связь с MemuAI Proxy прервалась", "Lost the connection to MemuAI Proxy")
                                   + f" ({type(exc).__name__})", kind="network", retryable=True) from exc
                yield StreamEvent("status", text=tr("Переподключаюсь к Proxy…", "Reconnecting to the Proxy…"))
                if not await self.ensure(launch=True):
                    raise LLMError(tr("MemuAI Proxy не отвечает", "MemuAI Proxy does not respond"), kind="offline",
                                   hint=tr("Запустите MemuAI Proxy или настройте прямой API (/proxy add).",
                                           "Start MemuAI Proxy or configure a direct API (/proxy add).")) from exc

    async def websearch(self, query: str, limit: int = 6) -> List[Dict[str, Any]]:
        response = await self.client().post(self.base_url() + "/api/admin/websearch",
                                            json={"query": query, "limit": limit},
                                            headers=self.headers(), timeout=60)
        if response.status_code != 200:
            try:
                detail = response.json().get("error") or f"HTTP {response.status_code}"
            except Exception:
                detail = f"HTTP {response.status_code}"
            raise LLMError(str(detail), status=response.status_code)
        return [row for row in (response.json().get("results") or []) if isinstance(row, dict)]

    async def accounts(self) -> List[Dict[str, Any]]:
        """Account cards for /usage (limits, status) — no secrets are returned."""
        response = await self.client().get(self.base_url() + "/api/admin/accounts", headers=self.headers(),
                                           timeout=15)
        if response.status_code != 200:
            return []
        data = response.json()
        rows = data.get("accounts") if isinstance(data, dict) else data
        return [row for row in rows or [] if isinstance(row, dict)]

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _proxy_http_error(status: int, raw: str) -> LLMError:
    from .i18n import language
    try:
        data = json.loads(raw or "{}")
        message = data.get("error") if isinstance(data, dict) else data
        if isinstance(data, dict) and language() == "en" and isinstance(data.get("error_en"), str) and data["error_en"]:
            message = data["error_en"]          # Round 32: the Proxy's English text for an English interface
        if isinstance(message, dict):
            message = message.get("message") or message.get("detail") or json.dumps(message, ensure_ascii=False)
    except ValueError:
        message = raw.strip()[:400] or f"HTTP {status}"
    if status == 401:
        return LLMError(tr("MemuAI Proxy отклонил запрос (401)", "MemuAI Proxy rejected the request (401)"),
                        status=401, kind="proxy_auth",
                        hint=tr("Proxy открыт в сеть и требует пароль. Верните хост 127.0.0.1 в настройках Proxy.",
                                "The Proxy listens on the network and requires a password. Set its host back to 127.0.0.1."))
    return LLMError(str(message)[:1200], status=status, kind="http", retryable=status >= 500,
                    provider="MemuAI Proxy")


def _proxy_stream_error(error: Any) -> LLMError:
    if isinstance(error, dict):
        from .i18n import language
        english = language() == "en"
        message = str((error.get("message_en") if english else None) or error.get("message") or error)
        hint = str((error.get("hint_en") if english else None) or error.get("hint") or "")
        status = int(error.get("status") or 0)
        kind = str(error.get("type") or "")
        result = LLMError(message, status=status, kind=kind, hint=hint, provider=str(error.get("provider") or ""),
                          retryable=kind in ("network", "timeout", "network_error", "rate_limit_error")
                          or status in (502, 503, 504))
        # Round 33: the Proxy passes the upstream's Retry-After (seconds) for /auto-continue auto.
        try:
            seconds = float(error.get("retry_after") or 0)
        except (TypeError, ValueError):
            seconds = 0.0
        result.retry_at = time.time() + seconds if seconds > 0 else _limit_reset(None, message)
        return result
    return LLMError(str(error))


def _limit_reset(headers: Any, text: str) -> float:
    """Round 33: when a rate limit ends, from the response headers or the error text (0.0 = nobody said)."""
    try:
        from .ratelimit import reset_at
        return reset_at(text, headers=headers)
    except Exception:
        return 0.0


# ---------------------------------------------------------------- OpenAI-compatible

def _data_url_parts(url: str) -> Tuple[str, str]:
    match = re.match(r"data:([^;,]+);base64,(.*)", url or "", re.S)
    if not match:
        return "", ""
    return match.group(1), match.group(2)


class OpenAIBackend(Backend):
    name = "openai"
    native_tools = True

    def __init__(self, connection: Dict[str, Any]) -> None:
        self.connection = connection
        self.base = str(connection.get("base_url") or "https://api.openai.com/v1").rstrip("/")
        self.key = str(connection.get("api_key") or "")
        self._client: Optional[httpx.AsyncClient] = None
        self.openrouter = "openrouter.ai" in self.base

    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = http_client(httpx.Timeout(900, connect=15), url=self.base)
        return self._client

    def headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = "Bearer " + self.key
        if self.openrouter:
            headers["HTTP-Referer"] = "https://memuai.local"
            headers["X-Title"] = "MemuCode"
        extra = self.connection.get("headers")
        if isinstance(extra, dict):
            headers.update({str(k): str(v) for k, v in extra.items()})
        return headers

    async def list_models(self) -> List[ModelInfo]:
        name = str(self.connection.get("name"))
        configured = [str(item) for item in self.connection.get("models") or [] if str(item).strip()]
        rows: Dict[str, ModelInfo] = {}
        try:
            response = await self.client().get(self.base + "/models", headers=self.headers(), timeout=20)
            if response.status_code == 200:
                data = response.json()
                for row in (data.get("data") if isinstance(data, dict) else data) or []:
                    if not isinstance(row, dict) or not row.get("id"):
                        continue
                    raw = str(row["id"])
                    context = int(row.get("context_length") or row.get("max_model_len") or 0)
                    arch = row.get("architecture") or {}
                    modalities = arch.get("input_modalities") if isinstance(arch, dict) else None
                    vision = "image" in (modalities or [])
                    rows[raw] = ModelInfo(id=f"@{name}:{raw}", provider=name, backend="direct", connection=name,
                                          raw=raw, context=context, context_source="provider" if context else "",
                                          vision=vision, vision_source="provider" if modalities else "",
                                          native_tools=True, label=str(row.get("name") or ""))
        except (httpx.HTTPError, ValueError, TypeError):
            pass
        if configured:
            rows = {raw: rows.get(raw) or ModelInfo(id=f"@{name}:{raw}", provider=name, backend="direct",
                                                     connection=name, raw=raw, native_tools=True)
                    for raw in configured}
        for info in rows.values():
            if not info.context:
                known = model_catalog.lookup_known(info.raw) or {}
                info.context = int(known.get("context") or 0)
                info.output = int(known.get("output") or 0)
                info.context_source = str(known.get("source") or "")
        return list(rows.values())

    def _body(self, request: ChatRequest) -> Dict[str, Any]:
        messages = openai_messages(request.messages)
        if request.system:
            messages = [{"role": "system", "content": request.system}, *messages]
        body: Dict[str, Any] = {"model": request.model, "messages": messages, "stream": True,
                                "stream_options": {"include_usage": True}}
        if request.tools:
            body["tools"] = [{"type": "function", "function": {
                "name": tool["name"], "description": tool.get("description", ""),
                "parameters": tool.get("parameters") or {"type": "object", "properties": {}}}}
                for tool in request.tools]
            if request.tool_choice == "none":
                body["tool_choice"] = "none"
        if request.max_tokens:
            body["max_tokens"] = int(request.max_tokens)
        if request.temperature is not None:
            body["temperature"] = request.temperature
        # Round 33 (r33-thinking): the level clamped to what this model supports, in this API's own fields
        # (reasoning_effort / enable_thinking / thinking.type / OpenRouter reasoning) — app/reasoning_levels.py.
        from .. import reasoning_levels as levels
        profile = levels.profile(request.model, "custom", request.model, use_catalog=False)
        if self.openrouter:
            body["usage"] = {"include": True}
            if profile["kind"] == "none" and (request.effort not in (None, "", "auto") or request.thinking):
                profile = levels.guess_profile(request.model, True)    # OpenRouter knows the model; we may not
                profile["options"] = levels.options(profile)
        level = levels.resolve(profile, request.thinking, request.effort)
        levels.apply_openai_compatible(body, profile, level, self.base)
        return body

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        body = self._body(request)
        delay = 1.0
        for attempt in range(4):
            calls: Dict[int, Dict[str, Any]] = {}
            slots: Dict[int, int] = {}          # provider index → key in calls (see the tool_calls loop)
            started = False
            think = ThinkTagFilter()
            try:
                async with self.client().stream("POST", self.base + "/chat/completions", json=body,
                                                headers=self.headers()) as response:
                    if response.status_code != 200:
                        raw = (await response.aread()).decode("utf-8", "replace")
                        error = _openai_error(response.status_code, raw)
                        error.retry_at = _limit_reset(response.headers, raw)       # Round 33: /auto-continue auto
                        if error.retryable and attempt < 3:
                            retry = response.headers.get("retry-after")
                            wait = float(retry) if retry and retry.replace(".", "", 1).isdigit() else delay
                            yield StreamEvent("status", text=tr(f"Повтор через {wait:.0f} с…", f"Retrying in {wait:.0f}s…"))
                            await asyncio.sleep(min(30.0, wait))
                            delay *= 2
                            continue
                        raise error
                    finish = ""
                    yield StreamEvent("accepted")
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        if line.startswith(":"):
                            yield StreamEvent("alive", text=line[1:].strip())     # e.g. OpenRouter's ": PROCESSING"
                            continue
                        item = _sse_json(line)
                        if item is None:
                            continue
                        if item.get("error"):
                            error = item["error"]
                            raise LLMError(str(error.get("message") if isinstance(error, dict) else error))
                        for choice in item.get("choices") or []:
                            delta = choice.get("delta") or {}
                            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                            if isinstance(reasoning, str) and reasoning:
                                started = True
                                yield StreamEvent("reasoning", text=reasoning)
                            if delta.get("content"):
                                started = True
                                for kind, text in think.feed(str(delta["content"])):
                                    yield StreamEvent(kind, text=text)
                            for part in delta.get("tool_calls") or []:
                                started = True
                                index = int(part.get("index") or 0)
                                key = slots.get(index, index)
                                slot = calls.get(key)
                                if slot is not None and part.get("id") and slot["id"] and part["id"] != slot["id"]:
                                    # Some OpenAI-compatible APIs (Gemini, several proxies) send every call
                                    # with index 0. A new id means a new call: without this the arguments
                                    # of parallel calls were glued into one unreadable JSON.
                                    key = max(calls) + 1 if calls else index
                                    while key in calls:
                                        key += 1
                                    slot = None
                                slots[index] = key
                                if slot is None:
                                    slot = calls.setdefault(key, {"id": "", "name": "", "arguments": ""})
                                if part.get("id"):
                                    slot["id"] = part["id"]
                                function = part.get("function") or {}
                                name = function.get("name")
                                if isinstance(name, str) and name:
                                    # Several servers repeat the whole name in every delta: appending made
                                    # "run_commandrun_command" (same fix as app/tool_protocol.collect_tool_delta).
                                    slot["name"] = name if slot["name"] and name.startswith(slot["name"]) \
                                        else slot["name"] + name
                                arguments = function.get("arguments")
                                if arguments not in (None, ""):
                                    # A few servers send the arguments as an object instead of JSON text.
                                    slot["arguments"] += arguments if isinstance(arguments, str) \
                                        else json.dumps(arguments, ensure_ascii=False)
                            if choice.get("finish_reason"):
                                finish = str(choice["finish_reason"])
                        usage = item.get("usage")
                        if isinstance(usage, dict) and (usage.get("prompt_tokens") or usage.get("completion_tokens")):
                            details = usage.get("prompt_tokens_details") or {}
                            out_details = usage.get("completion_tokens_details") or {}
                            cost = usage.get("cost")
                            yield StreamEvent("usage", usage=Usage(
                                input=int(usage.get("prompt_tokens") or 0),
                                output=int(usage.get("completion_tokens") or 0),
                                cache_read=int(details.get("cached_tokens") or 0),
                                cache_write=int(details.get("cache_write_tokens") or 0),
                                reasoning=int(out_details.get("reasoning_tokens") or 0),
                                cost=float(cost) if isinstance(cost, (int, float)) else None,
                                estimated=False, requests=1))
                    for kind, text in think.flush():
                        yield StreamEvent(kind, text=text)
                    for index in sorted(calls):
                        slot = calls[index]
                        args = tool_arguments(slot["arguments"])
                        if finish == "length" and "_invalid_json" not in args and slot["arguments"].strip() \
                                and not slot["arguments"].rstrip().endswith("}"):
                            args = {"_invalid_json": slot["arguments"][:2000]}
                        yield StreamEvent("tool_call", call={"id": slot["id"] or "call_" + uuid.uuid4().hex[:12],
                                                             "name": slot["name"], "args": args})
                    yield StreamEvent("done", finish=finish or ("tool_calls" if calls else "stop"))
                    return
            except httpx.TransportError as exc:
                if started or attempt >= 3:
                    raise LLMError(f"{type(exc).__name__}: {exc}", kind="network", retryable=True,
                                   hint=tr(f"Нет связи с {self.base} — проверьте адрес подключения, интернет или прокси.",
                                           f"Cannot reach {self.base} — check the connection address, the internet "
                                           "or the proxy.")) from exc
                yield StreamEvent("status", text=tr("Сеть недоступна, повторяю…", "Network error, retrying…"))
                await asyncio.sleep(delay)
                delay *= 2
        raise LLMError(tr("Запрос не удался после повторов", "The request failed after retries"))

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _openai_error(status: int, raw: str) -> LLMError:
    try:
        data = json.loads(raw)
        error = data.get("error", data)
        message = error.get("message") if isinstance(error, dict) else str(error)
    except ValueError:
        message = raw[:400]
    hints = {401: tr("Проверьте API-ключ подключения (/proxy).", "Check the connection API key (/proxy)."),
             402: tr("На счёте закончились средства.", "The account is out of credits."),
             404: tr("Нет такой модели или адреса API.", "No such model or API address."),
             429: tr("Превышен лимит запросов.", "Rate limit exceeded.")}
    return LLMError(f"HTTP {status}: {message}"[:1200], status=status, kind="http", hint=hints.get(status, ""),
                    retryable=status in (408, 409, 429) or status >= 500)


# ---------------------------------------------------------------- Anthropic (official SDK)

_ADAPTIVE_RE = re.compile(r"(opus|sonnet)-?(4[-.][6-9]|5)|fable|mythos")
_NO_EFFORT_RE = re.compile(r"haiku|sonnet-?4[-.]5|claude-3|opus-?4(?:[-.]1)?(?![-.\d])|sonnet-?4(?![-.\d])")


def _to_anthropic_content(content: Any) -> Any:
    if isinstance(content, str):
        return content
    blocks: List[Dict[str, Any]] = []
    for part in content or []:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            blocks.append({"type": "text", "text": str(part.get("text") or "")})
        elif part.get("type") == "image_url":
            media, data = _data_url_parts(str((part.get("image_url") or {}).get("url") or ""))
            if data:
                blocks.append({"type": "image", "source": {"type": "base64", "media_type": media, "data": data}})
    return blocks or ""


def to_anthropic_messages(messages: List[Dict[str, Any]], model: str) -> List[Dict[str, Any]]:
    """OpenAI chat format (as produced by the agent) → Anthropic Messages."""
    out: List[Dict[str, Any]] = []

    def push(role: str, blocks: Any) -> None:
        if isinstance(blocks, str):
            blocks = [{"type": "text", "text": blocks}] if blocks else []
        if not blocks:
            return
        if out and out[-1]["role"] == role:
            current = out[-1]["content"]
            if isinstance(current, str):
                current = [{"type": "text", "text": current}] if current else []
            out[-1]["content"] = [*current, *blocks]
        else:
            out.append({"role": role, "content": list(blocks)})

    for message in messages:
        role = message.get("role")
        if role == "system":
            push("user", [{"type": "text", "text": "<system-reminder>\n" + str(message.get("content") or "")
                           + "\n</system-reminder>"}])
        elif role == "user":
            push("user", _to_anthropic_content(message.get("content")))
        elif role == "assistant":
            native = message.get("_anthropic")
            if isinstance(native, dict) and native.get("model") == model and native.get("content"):
                push("assistant", native["content"])
                continue
            blocks: List[Dict[str, Any]] = []
            text = message.get("content")
            if isinstance(text, str) and text.strip():
                blocks.append({"type": "text", "text": text})
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                try:
                    args = json.loads(function.get("arguments") or "{}")
                except ValueError:
                    args = {}
                blocks.append({"type": "tool_use", "id": call.get("id"), "name": function.get("name"),
                               "input": args if isinstance(args, dict) else {}})
            push("assistant", blocks or [{"type": "text", "text": "(no text)"}])
        elif role == "tool":
            content = message.get("content")
            result: Dict[str, Any] = {"type": "tool_result", "tool_use_id": message.get("tool_call_id"),
                                      "content": _to_anthropic_content(content) if not isinstance(content, str)
                                      else str(content)}
            if message.get("is_error"):
                result["is_error"] = True
            push("user", [result])
    if out and out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": [{"type": "text", "text": "(continue)"}]})
    return out


class AnthropicBackend(Backend):
    name = "anthropic"
    native_tools = True

    def __init__(self, connection: Dict[str, Any]) -> None:
        self.connection = connection
        self._client = None

    def client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise LLMError(tr("Не установлен пакет anthropic", "The anthropic package is not installed"),
                               hint="pip install anthropic") from exc
            kwargs: Dict[str, Any] = {"api_key": str(self.connection.get("api_key") or ""), "max_retries": 3}
            base = str(self.connection.get("base_url") or "").rstrip("/")
            if base and base != "https://api.anthropic.com":
                kwargs["base_url"] = base
            self._client = anthropic.AsyncAnthropic(**kwargs)
        return self._client

    async def list_models(self) -> List[ModelInfo]:
        name = str(self.connection.get("name"))
        configured = [str(item) for item in self.connection.get("models") or [] if str(item).strip()]
        rows: Dict[str, ModelInfo] = {}
        try:
            page = await self.client().models.list(limit=100)
            for model in getattr(page, "data", []) or []:
                raw = str(getattr(model, "id", ""))
                if not raw:
                    continue
                rows[raw] = ModelInfo(
                    id=f"@{name}:{raw}", provider=name, backend="direct", connection=name, raw=raw,
                    label=str(getattr(model, "display_name", "") or ""),
                    context=int(getattr(model, "max_input_tokens", 0) or 0),
                    output=int(getattr(model, "max_tokens", 0) or 0), context_source="provider",
                    vision=True, vision_source="api", thinking=True, native_tools=True)
        except Exception:
            pass
        if configured:
            rows = {raw: rows.get(raw) or ModelInfo(id=f"@{name}:{raw}", provider=name, backend="direct",
                                                     connection=name, raw=raw, vision=True,
                                                     vision_source="api", thinking=True,
                                                     native_tools=True) for raw in configured}
        if not rows:
            for raw in ("claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"):
                rows[raw] = ModelInfo(id=f"@{name}:{raw}", provider=name, backend="direct", connection=name,
                                      raw=raw, vision=True, vision_source="api", thinking=True, native_tools=True)
        for info in rows.values():
            if not info.context:
                known = model_catalog.lookup_known(info.raw) or {}
                info.context = int(known.get("context") or 200_000)
                info.output = int(known.get("output") or 64_000)
        return list(rows.values())

    def _params(self, request: ChatRequest) -> Dict[str, Any]:
        model = request.model
        known = model_catalog.lookup_known(model) or {}
        max_tokens = int(request.max_tokens or min(64_000, int(known.get("output") or 32_000)))
        params: Dict[str, Any] = {
            "model": model, "max_tokens": max_tokens,
            "messages": to_anthropic_messages(request.messages, model),
            # Automatic caching of the growing conversation tail.
            "cache_control": {"type": "ephemeral"},
        }
        if request.system:
            # One explicit breakpoint on the static system prefix.
            params["system"] = [{"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}]
        if request.tools:
            params["tools"] = [{"name": tool["name"], "description": tool.get("description", ""),
                                "input_schema": tool.get("parameters") or {"type": "object", "properties": {}}}
                               for tool in request.tools]
            if request.tool_choice == "none":
                params["tool_choice"] = {"type": "none"}
        canonical = model_catalog.canonical(model)
        adaptive = bool(_ADAPTIVE_RE.search(canonical))
        if request.thinking or request.show_thinking:
            if adaptive:
                params["thinking"] = {"type": "adaptive", "display": "summarized"}
            elif "haiku-4.5" in canonical or "3.7" in canonical or "4.5" in canonical:
                budget = max(1024, min(max_tokens - 1024, 16_000))
                if budget >= 1024 and max_tokens > budget:
                    params["thinking"] = {"type": "enabled", "budget_tokens": budget}
        # Round 33 (r33-thinking): only an effort this Claude model accepts (xhigh only on Opus 4.7+, max on 4.6+).
        from .. import reasoning_levels as levels
        clamped = levels.clamp(request.effort, levels.profile(model, "anthropic", model, use_catalog=False))
        effort = clamped if clamped in ("low", "medium", "high", "xhigh", "max") else ""
        if effort and not _NO_EFFORT_RE.search(canonical):
            params["output_config"] = {"effort": effort}
        if request.temperature is not None and not adaptive:
            params["temperature"] = request.temperature
        return params

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        try:
            import anthropic
        except ImportError as exc:
            raise LLMError(tr("Не установлен пакет anthropic", "The anthropic package is not installed"),
                           hint="pip install anthropic") from exc
        params = self._params(request)
        try:
            async with self.client().messages.stream(**params) as stream:
                async for event in stream:
                    kind = getattr(event, "type", "")
                    if kind == "message_start":
                        yield StreamEvent("accepted")
                    elif kind == "ping":
                        yield StreamEvent("alive")
                    if kind == "text":
                        yield StreamEvent("text", text=str(getattr(event, "text", "") or ""))
                    elif kind == "thinking":
                        yield StreamEvent("reasoning", text=str(getattr(event, "thinking", "") or ""))
                final = await stream.get_final_message()
        except anthropic.APIStatusError as exc:
            status = int(getattr(exc, "status_code", 0) or 0)
            error = LLMError(f"HTTP {status}: {getattr(exc, 'message', exc)}"[:1200], status=status, kind="http",
                             retryable=status == 429 or status >= 500,
                             hint=tr("Проверьте ключ и модель подключения (/proxy).",
                                     "Check the connection key and model (/proxy).") if status in (401, 403, 404) else "")
            error.retry_at = _limit_reset(getattr(getattr(exc, "response", None), "headers", None), str(error.raw_message))
            raise error from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(tr("Нет соединения с API Anthropic", "Cannot reach the Anthropic API"),
                           kind="network", retryable=True) from exc
        stop = str(getattr(final, "stop_reason", "") or "")
        blocks = []
        for block in getattr(final, "content", []) or []:
            try:
                blocks.append(block.model_dump(exclude_none=True))
            except Exception:
                pass
            if getattr(block, "type", "") == "tool_use":
                args = getattr(block, "input", {})
                if stop == "max_tokens":
                    args = {"_invalid_json": "tool input was cut off by max_tokens"}
                yield StreamEvent("tool_call", call={"id": block.id, "name": block.name,
                                                     "args": args if isinstance(args, dict) else {}})
        usage = getattr(final, "usage", None)
        if usage is not None:
            fresh = int(getattr(usage, "input_tokens", 0) or 0)
            read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
            write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
            yield StreamEvent("usage", usage=Usage(input=fresh + read + write,
                                                   output=int(getattr(usage, "output_tokens", 0) or 0),
                                                   cache_read=read, cache_write=write, estimated=False, requests=1))
        if stop == "refusal":
            details = getattr(final, "stop_details", None)
            category = getattr(details, "category", "") if details else ""
            yield StreamEvent("text", text="\n\n" + tr("[Модель отказалась отвечать", "[The model declined")
                              + (f": {category}" if category else "") + "]")
        yield StreamEvent("done", finish=stop, data={"anthropic_content": blocks, "model": request.model})

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                pass
            self._client = None


# ---------------------------------------------------------------- router

class Router:
    """Picks the backend for a model id and caches the model list."""

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.proxy = ProxyBackend(settings)
        self._direct: Dict[str, Backend] = {}
        self._models: List[ModelInfo] = []
        self._models_at = 0.0
        self.errors: Dict[str, str] = {}

    def connections(self) -> List[Dict[str, Any]]:
        return self.settings.connections()

    def direct(self, name: str) -> Backend:
        name = str(name)
        backend = self._direct.get(name)
        if backend is None:
            connection = next((row for row in self.connections() if str(row.get("name")) == name), None)
            if connection is None:
                raise LLMError(tr(f"Нет подключения «{name}»", f"No connection named “{name}”"),
                               hint=tr("Добавьте его командой /proxy add.", "Add it with /proxy add."))
            fmt = str(connection.get("format") or "openai").lower()
            backend = AnthropicBackend(connection) if fmt == "anthropic" else OpenAIBackend(connection)
            self._direct[name] = backend
        return backend

    def reset(self) -> None:
        self._direct.clear()
        self._models_at = 0.0

    def resolve(self, model: str) -> Tuple[Backend, str]:
        model = str(model or "")
        if model.startswith("@") and ":" in model:
            name, raw = model[1:].split(":", 1)
            return self.direct(name), raw
        if self.settings.get("backend") == "direct":
            rows = self.connections()
            if rows:
                return self.direct(str(rows[0]["name"])), model
        return self.proxy, model

    def uses_native_tools(self, model: str) -> bool:
        mode = str(self.settings.get("tool_protocol") or "auto")
        if mode == "text":
            return False
        backend, _raw = self.resolve(model)
        if mode == "native":
            return backend.native_tools
        return backend.native_tools

    async def models(self, *, refresh: bool = False) -> List[ModelInfo]:
        if not refresh and self._models and time.time() - self._models_at < 120:
            return self._models
        rows: List[ModelInfo] = []
        self.errors = {}
        sources = []
        if self.settings.get("backend") != "direct":
            sources.append(("proxy", self.proxy))
        for connection in self.connections():
            name = str(connection.get("name"))
            sources.append((name, self.direct(name)))
        async def load(name, backend):
            try:
                return await asyncio.wait_for(backend.list_models(), 20)
            except Exception as exc:
                self.errors[name] = str(exc)
                return []
        for found in await asyncio.gather(*(load(name, backend) for name, backend in sources)):
            rows.extend(found)
        # A refresh that only failed (the Proxy is restarting) keeps the list that was known.
        if rows or (refresh and not self.errors):
            self._models = rows
            self._models_at = time.time()
        return rows

    def cached_models(self) -> List[ModelInfo]:
        return list(self._models)

    def effort_support(self, model: str) -> Tuple[bool, str]:
        """Does ``/effort`` change anything for this model, and what exactly happens to the value.

        The level is always saved, but only some connections have a parameter to put it in; the
        others never see it. The answer mirrors what the backends really send.
        """
        model = str(model or "")
        try:
            backend, raw = self.resolve(model)
        except LLMError:
            return False, tr("модель не подключена", "the model is not connected")
        if isinstance(backend, AnthropicBackend):
            if _NO_EFFORT_RE.search(model_catalog.canonical(raw)):
                return False, tr("у этой модели Claude нет уровня усилия в API — значение не отправляется",
                                 "this Claude model has no effort level in the API — the value is not sent")
            return True, tr("уходит в API Anthropic как output_config.effort", "sent to the Anthropic API as output_config.effort")
        if isinstance(backend, OpenAIBackend):
            if backend.openrouter:
                return True, tr("уходит в OpenRouter как reasoning (effort / enabled) (модель без рассуждений его пропустит)",
                                "sent to OpenRouter as reasoning (effort / enabled) (a model without reasoning ignores it)")
            options = self.effort_options(model)
            if options:
                return True, tr("уровни этой модели: ", "this model's levels: ") + ", ".join(options[1:])
            return False, tr("у этой модели нет выбора глубины размышлений — значение не отправляется",
                             "this model has no reasoning level to choose — the value is not sent")
        info = self.info(model)
        if info is not None and info.reasoning_options is not None:
            # Round 33 (r33-thinking): the Proxy knows the model's real levels and clamps the value to them.
            if info.reasoning_options:
                return True, tr("уровни этой модели: ", "this model's levels: ") + ", ".join(info.reasoning_options[1:]) \
                    + tr(" (другое значение заменяется ближайшим)", " (another value becomes the nearest one)")
            return False, tr("модель не даёт выбрать глубину размышлений — значение не отправляется",
                             "the model has no reasoning level to choose — the value is not sent")
        provider = info.provider if info else model.split("-", 1)[0]
        name = (info.raw if info and info.raw else model)
        if name.startswith(provider + "-"):
            name = name[len(provider) + 1:]
        if provider == "antigravity":
            from ..providers import antigravity_models as names
            upstream = names.upstream_id(name)
            if name.lower().startswith("claude") and names.is_thinking(upstream):
                return True, tr("задаёт бюджет размышлений Claude: low 2048, medium 8192, high 16384, max 32000 токенов, minimal — выкл",
                                "sets Claude's thinking budget: low 2048, medium 8192, high 16384, max 32000 tokens, minimal turns it off")
            if names.request_level(name, "high"):
                if names.request_level(name, ""):
                    return False, tr("уровень уже выбран именем модели (-low / -medium / -high) — возьмите модель без уровня в имени",
                                     "the level is fixed by the model name (-low / -medium / -high) — pick the name without a level")
                return True, tr("задаёт уровень размышлений Gemini: minimal и low — Low, medium — Medium, high и max — High; auto — выбирает Google",
                                "sets Gemini's thinking level: minimal and low — Low, medium — Medium, high and max — High; auto lets Google choose")
            if names.family_level(name) in names.LEVELS:
                return False, tr("у Gemini уровень — часть имени модели: выберите соседнюю модель (-low / -medium / -high)",
                                 "for Gemini the level is part of the model name: pick the sibling model (-low / -medium / -high)")
        return False, tr(f"провайдер «{provider}» не принимает уровень рассуждений: значение сохраняется, но на эту модель не влияет",
                         f"the “{provider}” provider takes no reasoning level: the value is saved but does not affect this model")

    def effort_options(self, model: str) -> List[str]:
        """Round 33 (r33-thinking): the levels ``/effort`` offers for this model ("auto" first; [] — no choice)."""
        info = self.info(model)
        if info is not None and info.reasoning_options is not None:
            return list(info.reasoning_options)
        from .. import reasoning_levels as levels
        try:
            backend, raw = self.resolve(model)
        except LLMError:
            return []
        provider = "anthropic" if isinstance(backend, AnthropicBackend) else "custom"
        return levels.profile(model, provider, raw or model, use_catalog=False)["options"]

    def info(self, model: str) -> Optional[ModelInfo]:
        for row in self._models:
            if row.id == model:
                return row
        return None

    def context_window(self, model: str) -> Tuple[int, str]:
        override = int(self.settings.get("context_window") or 0)
        if override:
            return override, "settings"
        try:
            prefs = json.loads((Path(DATA_DIR) / "memuai" / "preferences.json").read_text(encoding="utf-8"))
            manual = int(((prefs.get("model_context_limits") or {}).get(model)) or 0)
            if manual:
                return manual, "memuai"
        except (OSError, ValueError, TypeError):
            pass
        info = self.info(model)
        if info and info.context:
            return info.context, info.context_source or "provider"
        known = model_catalog.lookup_known(model.split(":", 1)[-1]) or {}
        if known.get("context"):
            return int(known["context"]), str(known.get("source") or "catalog")
        return 128_000, "default"

    async def close(self) -> None:
        await self.proxy.close()
        for backend in self._direct.values():
            await backend.close()
