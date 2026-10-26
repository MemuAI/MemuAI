"""MCP servers for MemuCode.

Servers come from two places:

* ``%APPDATA%\\MemuAI\\memuai\\mcp.json`` — shared with the desktop MemuCode;
* ``<project>/.mcp.json`` — the project's own servers in the common
  ``{"mcpServers": {...}}`` format. They start only in a trusted folder.

Both stdio servers (``command`` + ``args``) and remote servers over the
Streamable HTTP transport (``"type": "http", "url": …``) are supported. Every
tool of a running server becomes an agent tool named ``mcp__<server>__<tool>``.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ... import mcp_client
from ...version import APP_VERSION
from ..i18n import language, tr
from .base import Tool, ToolError, ToolResult

if TYPE_CHECKING:
    import httpx

PROTOCOL_VERSION = mcp_client.PROTOCOL_VERSION

def _clean_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or ""))[:60] or "server"

def load_project_servers(root: Path) -> Dict[str, Dict[str, Any]]:
    path = Path(root) / ".mcp.json"
    try:

        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    servers = data.get("mcpServers") or data.get("servers") or {}
    rows: Dict[str, Dict[str, Any]] = {}
    if not isinstance(servers, dict):
        return rows
    for name, spec in list(servers.items())[:40]:
        if not isinstance(spec, dict):
            continue
        kind = str(spec.get("type") or ("http" if spec.get("url") else "stdio")).lower()
        env = {str(k): os.path.expandvars(str(v)) for k, v in (spec.get("env") or {}).items()} \
            if isinstance(spec.get("env"), dict) else {}
        headers = {str(k): os.path.expandvars(str(v)) for k, v in (spec.get("headers") or {}).items()} \
            if isinstance(spec.get("headers"), dict) else {}
        rows[_clean_name(name)] = {
            "type": "http" if kind in ("http", "streamable-http", "sse") else "stdio",
            "command": str(spec.get("command") or ""), "args": [str(a) for a in spec.get("args") or []],
            "env": env, "cwd": str(spec.get("cwd") or root), "url": str(spec.get("url") or ""),
            "headers": headers, "enabled": spec.get("enabled", True) is not False,
            "description": str(spec.get("description") or "")[:300], "source": "project",
        }
    return rows

def _server_error(server: Any) -> str:
    """Last start error of a stdio (bilingual) or HTTP server, in the CLI language."""
    text = getattr(server, "error_text", None)
    return text(language()) if callable(text) else str(getattr(server, "error", "") or "")

class HttpMCPServer(mcp_client.SharedStartup):
    """Minimal Streamable HTTP MCP client: initialize, tools/list, tools/call."""

    def __init__(self, name: str, spec: Dict[str, Any]) -> None:
        self.name = name
        self.spec = spec
        self.tools: List[Dict[str, Any]] = []
        self.error = ""
        self.session_id = ""
        self._next = 0
        self._client: Optional[httpx.AsyncClient] = None
        self.ready = asyncio.Event()
        self._running = False
        self._init_startup()

    @property
    def running(self) -> bool:
        return self._running

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": PROTOCOL_VERSION}
        headers.update(mcp_client.resolve_secrets(self.spec.get("headers")))
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    async def _post(self, payload: Dict[str, Any], *, expect: bool = True) -> Dict[str, Any]:
        if self._client is None:
            import httpx

            from ..llm import http_client
            self._client = http_client(httpx.Timeout(mcp_client.CALL_TIMEOUT, connect=15), url=str(self.spec.get("url") or ""))
        async with self._client.stream("POST", self.spec["url"], json=payload, headers=self._headers()) as response:
            sid = response.headers.get("mcp-session-id")
            if sid:
                self.session_id = sid
            if response.status_code >= 400:
                body = (await response.aread()).decode("utf-8", "replace")[:300]
                raise RuntimeError(f"HTTP {response.status_code}: {body}")
            if not expect:
                return {}
            kind = response.headers.get("content-type", "")
            if "text/event-stream" in kind:
                data_lines: List[str] = []
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        data_lines.append(line[5:].strip())
                    elif not line and data_lines:
                        try:
                            message = json.loads("\n".join(data_lines))
                        except ValueError:
                            message = None
                        data_lines = []
                        if isinstance(message, dict) and message.get("id") == payload.get("id"):
                            return message
                raise RuntimeError("the server closed the stream without a response")
            raw = await response.aread()
            message = json.loads(raw or b"{}")
            if isinstance(message, list):
                message = next((row for row in message if isinstance(row, dict) and row.get("id") == payload.get("id")), {})
            return message

    async def _request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        self._next += 1
        message = await self._post({"jsonrpc": "2.0", "id": self._next, "method": method, "params": params})
        if message.get("error"):
            detail = message["error"]
            raise RuntimeError(str(detail.get("message") if isinstance(detail, dict) else detail)[:300])
        return message.get("result") or {}

    async def _start(self) -> None:
        if self._running:
            return
        self.error = ""
        try:
            await asyncio.wait_for(self._request("initialize", {
                "protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}},
                "clientInfo": {"name": "MemuCode CLI", "version": APP_VERSION}}), timeout=mcp_client.HANDSHAKE_TIMEOUT)
            await self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}, expect=False)
            listed = await self._request("tools/list", {})
            self.tools = [{"name": str(item.get("name") or ""), "description": str(item.get("description") or "")[:400],
                           "schema": item.get("inputSchema") or {}} for item in listed.get("tools") or []
                          if isinstance(item, dict) and item.get("name")][:80]
            self._running = True
        except BaseException as exc:
            self.error = str(exc)[:300]
            await self._stop_client()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise RuntimeError(self.error) from exc
        finally:
            self.ready.set()

    async def call(self, tool: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        await self.start()
        result = await asyncio.wait_for(self._request("tools/call", {"name": tool, "arguments": arguments or {}}),
                                        timeout=mcp_client.CALL_TIMEOUT)
        return {"content": mcp_client._flatten_content(result.get("content")), "is_error": bool(result.get("isError"))}

    async def stop(self) -> None:
        await self._cancel_startup()
        await self._stop_client()
        self.ready.set()

    async def _stop_client(self) -> None:
        self._running = False
        self.tools = []
        if self._client is not None:
            try:
                if self.session_id:
                    await self._client.delete(self.spec["url"], headers=self._headers(), timeout=5)
            except Exception:
                pass
            await self._client.aclose()
            self._client = None
        self.session_id = ""

class CliMCP:
    """Registry of MCP servers for one MemuCode process."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self.servers: Dict[str, Any] = {}
        self.errors: Dict[str, str] = {}
        self._lock = asyncio.Lock()

    def config(self) -> Dict[str, Dict[str, Any]]:
        rows: Dict[str, Dict[str, Any]] = {}
        for name, spec in mcp_client.load_config()["servers"].items():
            if spec.get("type") == "sse":
                continue
            rows[_clean_name(name)] = {**spec, "source": "user", "original": name}
        if self.runtime.settings.is_trusted():
            for name, spec in load_project_servers(self.runtime.root).items():
                rows[name] = spec
        return rows

    async def server(self, name: str) -> Any:
        spec = self.config().get(name)
        if spec is None:
            raise RuntimeError(f"MCP server {name!r} is not configured")
        if not spec.get("enabled", True):
            raise RuntimeError(f"MCP server {name!r} is disabled")
        async with self._lock:
            current = self.servers.get(name)
            if (current is not None and current.spec == spec
                    and (current.running or current.starting or not current.ready.is_set())):
                server = current
            else:
                if current is not None:
                    await current.stop()
                server = HttpMCPServer(name, spec) if spec.get("type") == "http" else mcp_client.MCPServer(name, spec)
                self.servers[name] = server
        await server.start()
        return server

    async def start_all(self) -> None:
        """Start enabled servers and register their tools (errors are kept for /mcp)."""
        names = [name for name, spec in self.config().items() if spec.get("enabled", True)]

        async def one(name: str) -> None:
            try:
                await asyncio.wait_for(self.server(name), timeout=mcp_client.HANDSHAKE_TIMEOUT + 5)
                self.errors.pop(name, None)
            except Exception as exc:
                self.errors[name] = mcp_client.error_text(exc, language())[:300]

        await asyncio.gather(*(one(name) for name in names))
        self.register_tools()

    def register_tools(self) -> None:
        registry = self.runtime.tools
        registry.remove_prefix("mcp__")
        for name, server in self.servers.items():
            if not server.running:
                continue
            for tool in server.tools:
                if server.spec.get("source") == "user":

                    from ... import mcp_hub
                    if mcp_hub.policy(server.spec, tool) == "deny":
                        continue
                registry.add(MCPTool(name, tool))

    async def call(self, server: str, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
        instance = await self.server(server)
        return await instance.call(tool, args)

    def catalogue(self) -> List[Dict[str, Any]]:
        rows = []
        for name, spec in self.config().items():
            live = self.servers.get(name)
            rows.append({"name": name, "source": spec.get("source"), "type": spec.get("type", "stdio"),
                         "command": spec.get("command") or spec.get("url"), "args": spec.get("args") or [],
                         "enabled": spec.get("enabled", True), "running": bool(live and live.running),
                         "tools": [tool["name"] for tool in (live.tools if live and live.running else [])],
                         "error": self.errors.get(name) or (_server_error(live) if live else "")})
        return rows

    async def restart(self, name: str) -> None:
        live = self.servers.pop(name, None)
        if live is not None:
            await live.stop()
        await self.server(name)
        self.register_tools()

    async def stop_all(self) -> None:
        for server in list(self.servers.values()):
            try:
                await server.stop()
            except Exception:
                pass
        self.servers.clear()

_PORTABLE_KEYS = ("type", "description", "properties", "items", "required", "enum")
_NOTE_KEYS = ("format", "minimum", "maximum", "minLength", "maxLength", "pattern", "default", "minItems", "maxItems")

def portable_schema(schema: Any, depth: int = 0) -> Dict[str, Any]:
    if not isinstance(schema, dict) or depth > 12:
        return {"type": "string"}
    variants = schema.get("anyOf") or schema.get("oneOf") or schema.get("allOf")
    if isinstance(variants, list) and variants and not schema.get("type"):

        real = [item for item in variants if isinstance(item, dict) and item.get("type") != "null"]
        merged = dict(real[0]) if real else {"type": "string"}
        if schema.get("description") and not merged.get("description"):
            merged["description"] = schema["description"]
        schema = merged
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((item for item in kind if item != "null"), "string")
    if kind not in ("object", "array", "string", "number", "integer", "boolean"):
        kind = "object" if isinstance(schema.get("properties"), dict) else "array" if "items" in schema else "string"
    out: Dict[str, Any] = {"type": kind}
    notes = [f"{key}: {schema[key]}" for key in _NOTE_KEYS if key in schema and not isinstance(schema[key], (dict, list))]
    description = str(schema.get("description") or "").strip()
    if notes:
        description = (description + " (" + ", ".join(notes) + ")").strip()
    if description:
        out["description"] = description[:1000]
    if isinstance(schema.get("enum"), list) and schema["enum"]:
        out["enum"] = [item for item in schema["enum"] if item is not None][:200]
        if all(isinstance(item, str) for item in out["enum"]):
            out["type"] = "string"
    if kind == "object":
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        out["properties"] = {str(name): portable_schema(value, depth + 1) for name, value in properties.items()}
        required = [str(name) for name in schema.get("required") or [] if str(name) in out["properties"]]
        if required:
            out["required"] = required
    elif kind == "array":
        out["items"] = portable_schema(schema.get("items") if isinstance(schema.get("items"), dict) else {}, depth + 1)
    return out

class MCPTool(Tool):
    kind = "mcp"
    plan_safe = False

    def __init__(self, server: str, spec: Dict[str, Any]) -> None:
        self.server = server
        self.tool = str(spec.get("name") or "")
        name = f"mcp__{_clean_name(server)}__{_clean_name(self.tool)}"
        if len(name) > 64:

            import hashlib
            name = name[:55] + "_" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
        self.name = name
        self.aliases = (f"mcp_{server}_{self.tool}",)
        self.description = str(spec.get("description") or f"MCP tool {self.tool} of {server}")
        schema = spec.get("schema") if isinstance(spec.get("schema"), dict) else {}
        self.parameters = portable_schema(schema) if schema else {"type": "object", "properties": {}}
        self.required = tuple(schema.get("required") or ()) if schema else ()
        self.short = (self.description.splitlines()[0] if self.description else self.tool)[:160]

    def schema(self) -> Dict[str, Any]:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}

    def permission_key(self, runtime, args):
        return self.name

    def display(self, args):
        text = json.dumps(args, ensure_ascii=False)
        return text if len(text) <= 90 else text[:87] + "…"

    async def run(self, ctx, args, prepared):
        try:
            result = await ctx.runtime.mcp.call(self.server, self.tool, args)
        except Exception as exc:
            raise ToolError(f"MCP {self.server}/{self.tool}: {mcp_client.error_text(exc, language())}") from exc
        content = str(result.get("content") or "")
        if result.get("is_error"):
            return ToolResult(ok=False, error=content[:4000] or "MCP tool error", output=content,
                              summary=tr("Ошибка MCP", "MCP error"))
        return ToolResult(ok=True, output=f"(untrusted output of MCP server {self.server})\n{content}",
                          summary=tr(f"{len(content)} символов", f"{len(content)} chars"), detail=content[:2000])
