"""Клиент MCP (Model Context Protocol): общий пул MemuAI и MemuCode.

MCP — способ подключить чужие инструменты: базы, трекеры, браузеры, что
угодно. Сервер запускается локальным процессом и общается JSON-RPC по stdio.
Здесь ровно та часть протокола, которая нужна агенту: рукопожатие, список
инструментов и их вызов.

Серверы описываются в `%APPDATA%/MemuAI/memuai/mcp.json` в том же формате,
что у остальных клиентов MCP:

    {"servers": {"files": {"command": "npx", "args": ["-y", "@x/server"],
                            "env": {}, "enabled": true}}}
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import DATA_DIR
from .storage import load_json_with_recovery, save_json
from .version import APP_VERSION

log = logging.getLogger("memuai.mcp")

CONFIG_PATH = Path(DATA_DIR) / "memuai" / "mcp.json"
HANDSHAKE_TIMEOUT = 25.0
CALL_TIMEOUT = 120.0
MAX_RESULT_CHARS = 60000
PROTOCOL_VERSION = "2025-06-18"


#: Round 40 (r40-mcp): MCP hub settings kept on a server row (Settings → Integrations → MCP).
HUB_KEYS = ("read_only", "confirm", "tool_rules", "chat", "quick", "agents", "catalog_id", "secret_keys", "timeout")
TRANSPORTS = ("stdio", "http", "sse")


def _hub_fields(row: Dict[str, Any]) -> Dict[str, Any]:
    confirm = str(row.get("confirm") or "write")
    rules = row.get("tool_rules") if isinstance(row.get("tool_rules"), dict) else {}
    return {
        "read_only": row.get("read_only") is True,
        # never | write (tools that are not read-only ask first) | always
        "confirm": confirm if confirm in ("never", "write", "always") else "write",
        "tool_rules": {str(k)[:120]: str(v) for k, v in list(rules.items())[:200]
                       if str(v) in ("allow", "confirm", "deny")},
        "chat": row.get("chat") is True,
        "quick": row.get("quick") is True,
        "agents": [str(a)[:80] for a in (row.get("agents") or []) if str(a)][:50]
        if isinstance(row.get("agents"), list) else [],
        "catalog_id": str(row.get("catalog_id") or "")[:60],
        "secret_keys": [str(k)[:80] for k in (row.get("secret_keys") or []) if str(k)][:40]
        if isinstance(row.get("secret_keys"), list) else [],
        "timeout": max(5, min(int(row.get("timeout") or CALL_TIMEOUT), 900))
        if str(row.get("timeout") or "").isdigit() else int(CALL_TIMEOUT),
    }


def load_config() -> Dict[str, Any]:
    data = load_json_with_recovery(CONFIG_PATH, {"servers": {}},
                                   validator=lambda x: isinstance(x, dict))
    servers = data.get("servers") if isinstance(data, dict) else {}
    if not isinstance(servers, dict):
        servers = {}
    cleaned: Dict[str, Any] = {}
    for name, row in list(servers.items())[:40]:
        if not isinstance(row, dict):
            continue
        command = str(row.get("command") or "").strip()
        url = str(row.get("url") or "").strip()
        kind = str(row.get("type") or ("http" if url and not command else "stdio")).lower()
        kind = {"streamable-http": "http", "streamable_http": "http"}.get(kind, kind)
        kind = kind if kind in TRANSPORTS else "stdio"
        if (kind == "stdio" and not command) or (kind != "stdio" and not url):
            continue
        cleaned[str(name)[:60]] = {
            "type": kind,
            "command": command,
            "args": [str(a) for a in (row.get("args") or []) if str(a)][:40],
            "env": {str(k): str(v) for k, v in (row.get("env") or {}).items()
                    if str(k)} if isinstance(row.get("env"), dict) else {},
            "cwd": str(row.get("cwd") or ""),
            "url": url,
            "headers": {str(k): str(v) for k, v in (row.get("headers") or {}).items()
                        if str(k)} if isinstance(row.get("headers"), dict) else {},
            "enabled": row.get("enabled", True) is not False,
            "description": str(row.get("description") or "")[:300],
            **_hub_fields(row),
        }
    return {"servers": cleaned}


def save_config(config: Dict[str, Any]) -> None:
    """A row without ``env``/``cwd``/``headers`` (or the hub settings) keeps the saved ones.

    The MCP panel never receives ``env`` (it holds tokens) and posts the whole list back: switching or
    deleting any server used to wipe ``GITHUB_TOKEN`` & co. of every server. Round 40: the MemuCode panel
    does not know the hub settings (permissions, chat/agents) either — they survive its saves the same way."""
    servers = config.get("servers") or {}
    saved = load_config()["servers"] if isinstance(servers, dict) else {}
    keep = ("env", "cwd", "headers", "type", "url") + HUB_KEYS
    if isinstance(servers, dict):
        servers = {name: ({**{key: saved[name][key] for key in keep
                              if key not in row and saved[name].get(key) not in (None, "", [], {})}, **row}
                          if isinstance(row, dict) and name in saved else row)
                   for name, row in servers.items()}
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    save_json(CONFIG_PATH, {"servers": servers}, backup=True)


_SECRET_REF = re.compile(r"\$\{secret:([\w./:-]{1,160})\}")


def resolve_secrets(values: Optional[Dict[str, str]]) -> Dict[str, str]:
    """``${secret:mcp/<server>/<KEY>}`` → the value from the protected store (app/mcp_hub.py)."""
    out = dict(values or {})
    if not any(_SECRET_REF.search(str(v)) for v in out.values()):
        return out
    from .mcp_hub import secret_value
    return {k: _SECRET_REF.sub(lambda m: secret_value(m.group(1)), str(v)) for k, v in out.items()}


def redact(text: str, spec: Optional[Dict[str, Any]] = None) -> str:
    """A log/error line without the secrets of the server (env values, header values)."""
    if not text or not spec:
        return text
    try:
        values = list(resolve_secrets(spec.get("env")).values()) + list(resolve_secrets(spec.get("headers")).values())
    except Exception:
        values = []
    for value in sorted({v for v in values if len(str(v)) >= 6}, key=len, reverse=True):
        text = text.replace(str(value), "•••")
    return text


class MCPError(RuntimeError):
    """An MCP failure with the message in both interface languages (``str()`` is the Russian one)."""

    def __init__(self, ru: str, en: str = "") -> None:
        super().__init__(ru)
        self.en = en or ru

    def text(self, lang: str = "ru") -> str:
        return self.en if lang == "en" else str(self)


def error_text(exc: BaseException, lang: str = "ru") -> str:
    """The error for the user/model in ``lang`` (server-supplied texts stay as the server wrote them)."""
    return exc.text(lang) if isinstance(exc, MCPError) else str(exc)


class SharedStartup:
    """One startup task per server, including the time before a process exists.

    Cancelling one of several callers does not interrupt the others. When the
    last startup caller leaves, the startup is cancelled and its resources are
    cleaned up before cancellation reaches that caller.
    """

    def _init_startup(self) -> None:
        self._startup_task: Optional[asyncio.Task] = None
        self._startup_waiters = 0

    @property
    def starting(self) -> bool:
        return self._startup_task is not None and not self._startup_task.done()

    async def start(self) -> None:
        if self.running and self.ready.is_set():
            return
        task = self._startup_task
        if task is None or task.done():
            self.ready.clear()
            task = asyncio.create_task(self._start())
            self._startup_task = task
        self._startup_waiters += 1
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if self._startup_waiters == 1 and not task.done():
                if not task.cancelling():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                self.ready.set()
            raise
        finally:
            self._startup_waiters -= 1

    async def _cancel_startup(self) -> None:
        task = self._startup_task
        if task is not None and task is not asyncio.current_task():
            if not task.done() and not task.cancelling():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self._startup_task = None


class MCPServer(SharedStartup):
    """Один запущенный сервер MCP: процесс плюс JSON-RPC поверх его stdio."""

    def __init__(self, name: str, spec: Dict[str, Any]) -> None:
        self.name = name
        self.spec = spec
        self.process: Optional[asyncio.subprocess.Process] = None
        self.tools: List[Dict[str, Any]] = []
        self.error = ""
        self.error_en = ""
        self.started_at = 0.0
        self.last_used = 0.0
        self.resources: List[Dict[str, Any]] = []
        self.capabilities: Dict[str, Any] = {}
        self.server_info: Dict[str, Any] = {}
        # Round 40: the last stderr lines (secrets redacted) for «Logs» in Settings → Integrations → MCP.
        self.logs: "collections.deque[str]" = collections.deque(maxlen=200)
        self._stderr_task: Optional[asyncio.Task] = None
        self._next_id = 0
        self._lock = asyncio.Lock()
        # Рукопожатие завершено: до этого вызывать инструменты нельзя.
        self.ready = asyncio.Event()
        self._init_startup()

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.returncode is None

    async def _start(self) -> None:
        if self.running:
            return
        command = self.spec["command"]
        resolved = shutil.which(command) or command
        env = dict(os.environ)
        env.update(resolve_secrets(self.spec.get("env")))
        # Сервер не должен наследовать интерактивность: только протокол.
        env.setdefault("NO_COLOR", "1")
        # Протокол требует UTF-8. На Windows Python-серверы иначе пишут в
        # кодировке консоли, и первая же кириллица ломает разбор ответа.
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")
        try:
            self.process = await asyncio.create_subprocess_exec(
                resolved, *self.spec.get("args", []),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                # stderr читается постоянно (_read_stderr): серверы на npx пишут туда логи, и
                # непрочитанный буфер вешал сервер на каждом вызове до таймаута.
                stderr=asyncio.subprocess.PIPE,
                cwd=self.spec.get("cwd") or None,
                env=env,
                # Строка JSON-RPC с содержимым файла легко больше 64 КБ.
                limit=32 * 1024 * 1024,
                creationflags=(0x08000000 if sys.platform == "win32" else 0),
            )
        except (OSError, ValueError) as exc:
            self._fail(f"не удалось запустить «{command}»: {exc}", f"could not start “{command}”: {exc}")
            self.ready.set()  # ждущие вызовы получат эту ошибку, а не таймаут
            raise MCPError(self.error, self.error_en) from exc
        except asyncio.CancelledError:
            await self._stop_process()
            self.ready.set()
            raise
        self.started_at = self.last_used = time.time()
        self._stderr_task = asyncio.create_task(self._read_stderr(self.process))
        self._fail("", "")
        try:
            await asyncio.wait_for(self._handshake(), timeout=HANDSHAKE_TIMEOUT)
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                self._fail("запуск отменён", "start cancelled")
            else:
                # TimeoutError() has no text: say what timed out instead of «handshake failed: ».
                detail = error_text(exc, "ru") or f"нет ответа за {int(HANDSHAKE_TIMEOUT)} с"
                detail_en = error_text(exc, "en") or f"no answer in {int(HANDSHAKE_TIMEOUT)} s"
                self._fail(f"рукопожатие не прошло: {detail}", f"handshake failed: {detail_en}")
            await self._stop_process()
            self.ready.set()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise MCPError(self.error, self.error_en) from exc
        self.ready.set()

    def _fail(self, ru: str, en: str) -> None:
        self.error, self.error_en = ru, en
        if ru:
            self.log("error: " + (en or ru))

    def log(self, line: str) -> None:
        self.logs.append(time.strftime("%H:%M:%S ") + redact(line.rstrip(), self.spec)[:600])

    async def _read_stderr(self, process: asyncio.subprocess.Process) -> None:
        stream = process.stderr
        while stream is not None:
            try:
                line = await stream.readline()
            except (ValueError, asyncio.LimitOverrunError):
                continue
            except Exception:
                return
            if not line:
                return
            self.log(line.decode("utf-8", "replace"))

    def error_text(self, lang: str = "ru") -> str:
        return self.error_en if lang == "en" and self.error_en else self.error

    async def _handshake(self) -> None:
        await _handshake(self, "MemuCode")

    async def _send(self, payload: Dict[str, Any]) -> None:
        if not self.process or not self.process.stdin:
            raise _NotDelivered("сервер не запущен", "the server is not running")
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.process.stdin.write(body + b"\n")
            await self.process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise _NotDelivered("сервер MCP завершился", "the MCP server exited") from exc

    async def _answer_server_request(self, message: Dict[str, Any]) -> None:
        """The server may ask the client too (``ping``, ``roots/list``…). Its ids count from 1 like ours: such a
        request with our id used to be taken for the answer (an empty result), and an unanswered ``ping``
        made some servers drop the session."""
        if message.get("method") == "ping":
            reply: Dict[str, Any] = {"jsonrpc": "2.0", "id": message["id"], "result": {}}
        else:
            reply = {"jsonrpc": "2.0", "id": message["id"],
                     "error": {"code": -32601, "message": f"Method not supported: {str(message.get('method'))[:80]}"}}
        try:
            await self._send(reply)
        except RuntimeError:
            pass

    async def _notify(self, method: str, params: Dict[str, Any]) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def _request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        async with self._lock:
            self._next_id += 1
            request_id = self._next_id
            await self._send({"jsonrpc": "2.0", "id": request_id,
                              "method": method, "params": params})
            while True:
                try:
                    line = await self.process.stdout.readline()
                except (ValueError, asyncio.LimitOverrunError) as exc:
                    raise MCPError("ответ сервера MCP слишком большой", "the MCP server's answer is too large") from exc
                if not line:
                    # The pipe closes before asyncio records the exit code: without this the dead process still
                    # looked «running» and every next call failed with this same error instead of restarting it.
                    await self._stop_process()
                    raise MCPError("сервер закрыл поток", "the server closed the stream")
                try:
                    message = json.loads(line.decode("utf-8", "replace"))
                except ValueError:
                    continue            # сервер может писать логи в stdout
                if not isinstance(message, dict):
                    continue
                if message.get("method") is not None:
                    if message.get("id") is not None:
                        await self._answer_server_request(message)
                    continue            # уведомление или запрос сервера, а не ответ
                if message.get("id") != request_id:
                    continue            # чужой (опоздавший) ответ
                if message.get("error"):
                    detail = message["error"]
                    text = detail.get("message") if isinstance(detail, dict) else None
                    raise RuntimeError(str(text or detail)[:300])
                result = message.get("result")
                return result if isinstance(result, dict) else {}

    async def call(self, tool: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        return await _call_tool(self, tool, arguments)

    async def read_resource(self, uri: str) -> Dict[str, Any]:
        return await _read_resource(self, uri)

    async def stop(self) -> None:
        await self._cancel_startup()
        await self._stop_process()
        self.ready.set()

    async def _stop_process(self) -> None:
        process, self.process = self.process, None
        self.tools = []
        self.resources = []
        task, self._stderr_task = self._stderr_task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        if process is None or process.returncode is not None:
            return
        if sys.platform == "win32":
            # terminate() закрывает только cmd от npx.cmd, а node остаётся жить.
            try:
                killer = await asyncio.create_subprocess_exec(
                    "taskkill", "/T", "/F", "/PID", str(process.pid),
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                    creationflags=0x08000000)
                await asyncio.wait_for(killer.wait(), timeout=10)
            except Exception:
                pass
        try:
            process.terminate()
            await asyncio.wait_for(process.wait(), timeout=5)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass


def _tool_row(item: Dict[str, Any]) -> Dict[str, Any]:
    notes = item.get("annotations") if isinstance(item.get("annotations"), dict) else {}
    row = {"name": str(item.get("name") or ""),
           "description": str(item.get("description") or "")[:400],
           "schema": item.get("inputSchema") or item.get("input_schema") or {}}
    if notes:
        row["annotations"] = {k: notes[k] for k in ("title", "readOnlyHint", "destructiveHint", "idempotentHint",
                                                     "openWorldHint") if k in notes}
    return row


async def _paged(server: Any, method: str, key: str, cap: int) -> List[Dict[str, Any]]:
    raw: List[Any] = []
    cursor = None
    for _page in range(10):         # */list is paginated (nextCursor) on large servers
        listed = await server._request(method, {"cursor": cursor} if cursor else {})
        page = listed.get(key)
        raw += page if isinstance(page, list) else []
        cursor = listed.get("nextCursor")
        if not cursor or not isinstance(cursor, str) or len(raw) >= cap:
            break
    return [item for item in raw if isinstance(item, dict)][:cap]


async def _handshake(server: Any, client: str) -> None:
    """initialize → initialized → tools/list (+ resources/list when the server has resources)."""
    init = await server._request("initialize", {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": client, "version": APP_VERSION},
    })
    server.capabilities = init.get("capabilities") if isinstance(init.get("capabilities"), dict) else {}
    server.server_info = init.get("serverInfo") if isinstance(init.get("serverInfo"), dict) else {}
    await server._notify("notifications/initialized", {})
    server.tools = [_tool_row(item) for item in await _paged(server, "tools/list", "tools", 80) if item.get("name")]
    server.resources = []
    if "resources" in server.capabilities:
        try:
            server.resources = [{"uri": str(r.get("uri") or ""), "name": str(r.get("name") or r.get("title") or "")[:200],
                                 "description": str(r.get("description") or "")[:300],
                                 "mime": str(r.get("mimeType") or "")[:80]}
                                for r in await _paged(server, "resources/list", "resources", 200) if r.get("uri")]
        except Exception as exc:   # a broken resources/list must not cost the tools
            server.log(f"resources/list failed: {exc}")


async def _call_tool(server: Any, tool: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    timeout = float(server.spec.get("timeout") or CALL_TIMEOUT)
    for attempt in (0, 1):
        await server.start()
        server.last_used = time.time()
        try:
            result = await asyncio.wait_for(
                server._request("tools/call", {"name": tool, "arguments": arguments or {}}), timeout=timeout)
            break
        except asyncio.TimeoutError:
            # str(TimeoutError()) is empty: the model used to see just «MCP «name»: ».
            server.log(f"tools/call {tool}: timeout {int(timeout)} s")
            raise MCPError(f"инструмент «{tool}» не ответил за {int(timeout)} с",
                           f"tool “{tool}” did not answer in {int(timeout)} s") from None
        except _NotDelivered:
            # The process died before the request reached it (nothing ran): restart once and resend.
            if attempt:
                raise MCPError("сервер MCP завершился", "the MCP server exited") from None
            server.log("the server exited — restarting")
            await server._stop_process()
    return {"content": _flatten_content(result.get("content")),
            "is_error": bool(result.get("isError")),
            "raw_keys": sorted(result.keys())[:10]}


async def _read_resource(server: Any, uri: str) -> Dict[str, Any]:
    await server.start()
    server.last_used = time.time()
    try:
        result = await asyncio.wait_for(server._request("resources/read", {"uri": uri}),
                                        timeout=float(server.spec.get("timeout") or CALL_TIMEOUT))
    except asyncio.TimeoutError:
        raise MCPError(f"ресурс «{uri}» не прочитан за отведённое время", f"resource “{uri}” timed out") from None
    parts: List[str] = []
    for item in result.get("contents") or []:
        if isinstance(item, dict):
            parts.append(str(item.get("text") or (f"[binary {item.get('mimeType') or ''}]" if item.get("blob") else "")))
    return {"uri": uri, "text": "\n".join(p for p in parts if p)[:MAX_RESULT_CHARS]}


class _NotDelivered(MCPError):
    """The request never reached the server (broken pipe): safe to resend after a restart."""


class HttpMCPServer(SharedStartup):
    """Remote MCP over Streamable HTTP (``type: http``) or the legacy HTTP+SSE transport (``type: sse``)."""

    def __init__(self, name: str, spec: Dict[str, Any]) -> None:
        self.name = name
        self.spec = spec
        self.tools: List[Dict[str, Any]] = []
        self.resources: List[Dict[str, Any]] = []
        self.capabilities: Dict[str, Any] = {}
        self.server_info: Dict[str, Any] = {}
        self.logs: "collections.deque[str]" = collections.deque(maxlen=200)
        self.error = self.error_en = ""
        self.started_at = self.last_used = 0.0
        self.session_id = ""
        self._next_id = 0
        self._client: Any = None
        self._running = False
        self._sse_task: Optional[asyncio.Task] = None
        self._endpoint = ""
        self._waiting: Dict[Any, asyncio.Future] = {}
        self.ready = asyncio.Event()
        self._init_startup()

    @property
    def running(self) -> bool:
        return self._running

    def log(self, line: str) -> None:
        self.logs.append(time.strftime("%H:%M:%S ") + redact(line.rstrip(), self.spec)[:600])

    def error_text(self, lang: str = "ru") -> str:
        return self.error_en if lang == "en" and self.error_en else self.error

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": PROTOCOL_VERSION}
        headers.update(resolve_secrets(self.spec.get("headers")))
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def _http(self) -> Any:
        if self._client is None:
            import httpx    # lazily: only a remote MCP server needs it
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(CALL_TIMEOUT, connect=15), follow_redirects=True)
        return self._client

    async def _sse_reader(self, ready: asyncio.Future) -> None:
        """Legacy SSE: the GET stream announces the POST endpoint, then carries every answer."""
        from urllib.parse import urljoin
        try:
            async with self._http().stream("GET", self.spec["url"], headers={
                    **self._headers(), "Accept": "text/event-stream"}, timeout=None) as response:
                if response.status_code >= 400:
                    raise MCPError(f"HTTP {response.status_code}", f"HTTP {response.status_code}")
                event, data = "message", []
                async for line in response.aiter_lines():
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data.append(line[5:].strip())
                    elif not line and data:
                        payload, data = "\n".join(data), []
                        if event == "endpoint":
                            self._endpoint = urljoin(self.spec["url"], payload)
                            if not ready.done():
                                ready.set_result(True)
                        else:
                            self._dispatch(payload)
                        event = "message"
        except Exception as exc:
            if not ready.done():
                ready.set_exception(exc)
        finally:
            self._running = False
            for fut in self._waiting.values():
                if not fut.done():
                    fut.set_exception(MCPError("сервер закрыл поток", "the server closed the stream"))

    def _dispatch(self, payload: str) -> None:
        try:
            message = json.loads(payload)
        except ValueError:
            return
        for row in message if isinstance(message, list) else [message]:
            if isinstance(row, dict) and row.get("method") is None:
                fut = self._waiting.pop(row.get("id"), None)
                if fut is not None and not fut.done():
                    fut.set_result(row)

    async def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        expect = "id" in payload
        if self.spec.get("type") == "sse":
            fut = asyncio.get_running_loop().create_future() if expect else None
            if fut is not None:
                self._waiting[payload["id"]] = fut
            response = await self._http().post(self._endpoint, json=payload, headers=self._headers())
            if response.status_code >= 400:
                self._waiting.pop(payload.get("id"), None)
                raise RuntimeError(redact(f"HTTP {response.status_code}: {response.text[:300]}", self.spec))
            return await fut if fut is not None else {}
        async with self._http().stream("POST", self.spec["url"], json=payload, headers=self._headers()) as response:
            sid = response.headers.get("mcp-session-id")
            if sid:
                self.session_id = sid
            if response.status_code >= 400:
                body = (await response.aread()).decode("utf-8", "replace")[:300]
                raise RuntimeError(redact(f"HTTP {response.status_code}: {body}", self.spec))
            if not expect:
                return {}
            if "text/event-stream" in response.headers.get("content-type", ""):
                data: List[str] = []
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        data.append(line[5:].strip())
                    elif not line and data:
                        try:
                            message = json.loads("\n".join(data))
                        except ValueError:
                            message = None
                        data = []
                        if isinstance(message, dict) and message.get("id") == payload["id"]:
                            return message
                raise MCPError("сервер закрыл поток без ответа", "the server closed the stream without an answer")
            message = json.loads((await response.aread()) or b"{}")
            if isinstance(message, list):
                message = next((r for r in message if isinstance(r, dict) and r.get("id") == payload["id"]), {})
            return message if isinstance(message, dict) else {}

    async def _request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        self._next_id += 1
        message = await self._post({"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params})
        if message.get("error"):
            detail = message["error"]
            raise RuntimeError(str(detail.get("message") if isinstance(detail, dict) else detail)[:300])
        result = message.get("result")
        return result if isinstance(result, dict) else {}

    async def _notify(self, method: str, params: Dict[str, Any]) -> None:
        await self._post({"jsonrpc": "2.0", "method": method, "params": params})

    async def _start(self) -> None:
        if self._running:
            return
        self.error = self.error_en = ""
        try:
            if self.spec.get("type") == "sse":
                ready = asyncio.get_running_loop().create_future()
                self._sse_task = asyncio.create_task(self._sse_reader(ready))
                await asyncio.wait_for(ready, timeout=HANDSHAKE_TIMEOUT)
            self._running = True
            await asyncio.wait_for(_handshake(self, "MemuAI"), timeout=HANDSHAKE_TIMEOUT)
            self.started_at = self.last_used = time.time()
        except BaseException as exc:
            detail = redact(error_text(exc, "ru") or f"нет ответа за {int(HANDSHAKE_TIMEOUT)} с", self.spec)
            detail_en = redact(error_text(exc, "en") or f"no answer in {int(HANDSHAKE_TIMEOUT)} s", self.spec)
            self.error, self.error_en = f"подключение не удалось: {detail}"[:300], f"connection failed: {detail_en}"[:300]
            self.log("error: " + self.error_en)
            await self._stop_process()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise MCPError(self.error, self.error_en) from exc
        finally:
            self.ready.set()

    async def call(self, tool: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        return await _call_tool(self, tool, arguments)

    async def read_resource(self, uri: str) -> Dict[str, Any]:
        return await _read_resource(self, uri)

    async def stop(self) -> None:
        await self._cancel_startup()
        await self._stop_process()
        self.ready.set()

    async def _stop_process(self) -> None:
        self._running = False
        self.tools, self.resources = [], []
        task, self._sse_task = self._sse_task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        client, self._client = self._client, None
        if client is not None:
            try:
                if self.session_id and self.spec.get("type") == "http":
                    await client.delete(self.spec["url"], headers=self._headers(), timeout=5)
            except Exception:
                pass
            await client.aclose()
        self.session_id = ""


def _flatten_content(content: Any) -> str:
    """Ответ MCP — список блоков; агенту нужен связный текст."""
    if isinstance(content, str):
        return content[:MAX_RESULT_CHARS]
    parts: List[str] = []
    for item in content or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "text":
            parts.append(str(item.get("text") or ""))
        elif kind == "resource":
            resource = item.get("resource") or {}
            parts.append(str(resource.get("text") or resource.get("uri") or ""))
        elif kind == "image":
            parts.append("[image]")
    return "\n".join(part for part in parts if part)[:MAX_RESULT_CHARS]


#: Round 40: a server nobody called for this long is stopped (it restarts lazily on the next call).
IDLE_STOP = 20 * 60


def make_server(name: str, spec: Dict[str, Any]) -> Any:
    return HttpMCPServer(name, spec) if spec.get("type") in ("http", "sse") else MCPServer(name, spec)


class MCPRegistry:
    """Запущенные серверы (общий пул MemuAI). Процесс поднимается лениво — при первом обращении."""

    def __init__(self) -> None:
        self._servers: Dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    async def _reap_idle(self) -> None:
        now = time.time()
        idle = [s for n, s in self._servers.items() if s.running and s.last_used and now - s.last_used > IDLE_STOP]
        for server in idle:
            server.log("idle — stopped")
            await server.stop()

    async def server(self, name: str) -> Any:
        from .safe_mode import is_safe_mode
        if is_safe_mode():  # Round 29: MCP servers are not started in safe mode
            raise MCPError("Безопасный режим: серверы MCP не запускаются", "Safe mode: MCP servers are not started")
        self.loop = asyncio.get_running_loop()
        config = load_config()["servers"]
        spec = config.get(name)
        if spec is None:
            raise MCPError(f"Сервер MCP «{name}» не настроен", f"MCP server “{name}” is not configured")
        if not spec.get("enabled", True):
            raise MCPError(f"Сервер MCP «{name}» выключен", f"MCP server “{name}” is turned off")
        async with self._lock:
            await self._reap_idle()
            existing = self._servers.get(name)
            # A pending startup is reusable even before subprocess creation
            # returns. Completed failures/dead processes are retried lazily.
            if existing and existing.spec == spec and (existing.running or existing.starting
                                                       or not existing.ready.is_set()):
                pass
            else:
                existing = None
            if existing is None:
                stale = self._servers.get(name)
                if stale:
                    await stale.stop()
                server = make_server(name, spec)
                if stale is not None:
                    server.logs.extend(stale.logs)
                self._servers[name] = server
        if existing is not None:
            # Сервер ещё здоровается — ждём конца рукопожатия, а не шлём
            # вызов в полуготовый процесс.
            await existing.start()
            if not existing.running:
                raise MCPError(existing.error or f"Сервер MCP «{name}» не запустился",
                               existing.error_en or f"MCP server “{name}” did not start")
            return existing
        await server.start()
        return server

    def live(self, name: str) -> Any:
        """The pooled server object (running or not) without starting anything."""
        return self._servers.get(name)

    def row(self, name: str, spec: Dict[str, Any]) -> Dict[str, Any]:
        live = self._servers.get(name)
        running = bool(live and live.running)
        return {"name": name, "type": spec.get("type", "stdio"), "command": spec["command"], "args": spec["args"],
                "url": spec.get("url", ""), "enabled": spec["enabled"], "description": spec["description"],
                "running": running, "tools": live.tools if running else [],
                "resources": live.resources if running else [], "error": "",
                "server_info": live.server_info if running else {}}

    async def catalogue(self, *, probe: bool = False, lang: str = "ru") -> List[Dict[str, Any]]:
        """Список серверов для панели. `probe` поднимает их, чтобы узнать инструменты."""
        rows: List[Dict[str, Any]] = []
        for name, spec in load_config()["servers"].items():
            row = self.row(name, spec)
            if not row["running"] and probe and spec["enabled"]:
                try:
                    await self.server(name)
                    row = self.row(name, spec)
                except Exception as exc:
                    row["error"] = redact(error_text(exc, lang), spec)[:300]
            rows.append(row)
        return rows

    async def call(self, name: str, tool: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        server = await self.server(name)
        return await server.call(tool, arguments)

    async def read_resource(self, name: str, uri: str) -> Dict[str, Any]:
        server = await self.server(name)
        return await server.read_resource(uri)

    async def stop(self, name: str) -> None:
        async with self._lock:
            server = self._servers.get(name)
        if server is not None:
            await server.stop()

    async def stop_all(self) -> None:
        async with self._lock:
            servers = list(self._servers.values())
            self._servers.clear()
        for server in servers:
            await server.stop()


registry = MCPRegistry()
