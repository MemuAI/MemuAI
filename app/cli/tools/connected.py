"""Connected Telegram Use / Chrome extension tools through MemuAI's loopback API.

Discovery only reads public integration status. Mutations are sent only by run(),
after the ordinary MemuCode permission gate has approved the prepared action.
"""
from __future__ import annotations

import base64
import json
import re
import time
from typing import Any, Dict

import httpx

from ..i18n import language
from ..llm import _runtime_url
from .base import Prepared, Tool, ToolError, ToolResult

SERVICES = {"telegram_use": "telegram_use_", "chrome_extension": "chrome_"}
READ_TOOLS = {"chrome_tabs", "chrome_read", "chrome_screenshot"} | {
    "telegram_use_" + name for name in ("status", "dialogs", "history", "message", "search", "media")}
INT_FIELDS = {"limit", "before_id", "message_id", "reply_to", "tab_id", "dy"}
BOOL_FIELDS = {"silent", "revoke", "active"}
DESCRIPTIONS = {
    "status": "Telegram personal account connection status",
    "dialogs": "Find Telegram chats or contacts by name, username or ID",
    "history": "Read recent Telegram messages, optionally before an ID or matching a search",
    "message": "Read one Telegram message by ID",
    "search": "Search Telegram messages across chats or in one chat",
    "media": "Describe a Telegram image/video or transcribe a voice message using MemuAI models",
    "send": "Send a Telegram message with supported formatting",
    "edit": "Edit an outgoing Telegram message",
    "delete": "Delete Telegram messages (revoke can also remove them for recipients)",
    "forward": "Forward Telegram messages to another chat",
    "react": "React to a Telegram message",
    "mark_read": "Mark a Telegram chat as read",
    "send_file": "Send a local file to a Telegram chat",
    "tabs": "List tabs in the user's paired Chrome browser",
    "read": "Read page text and interactive elements from a Chrome tab",
    "screenshot": "Capture a Chrome tab as an image",
    "open": "Open a URL in a Chrome tab (active=false avoids switching the user's tab)",
    "navigate": "Navigate a Chrome tab to a URL",
    "close": "Close a Chrome tab",
    "action": "Operate an interactive page element in a Chrome tab",
}
REQUIRED = {
    "telegram_use_history": ("chat",), "telegram_use_message": ("chat", "message_id"),
    "telegram_use_search": ("query",), "telegram_use_media": ("chat", "message_id"),
    "telegram_use_send": ("chat", "text"), "telegram_use_edit": ("chat", "message_id", "text"),
    "telegram_use_delete": ("chat", "message_ids"),
    "telegram_use_forward": ("chat", "from_chat", "message_ids"),
    "telegram_use_react": ("chat", "message_id", "emoji"), "telegram_use_mark_read": ("chat",),
    "telegram_use_send_file": ("chat", "path"), "chrome_open": ("url",),
    "chrome_navigate": ("tab_id", "url"), "chrome_close": ("tab_id",), "chrome_action": ("action",),
}


def register(registry) -> None:
    """The actual tools arrive from discovery; keep their ownership separate from MCP."""
    registry._connected_names = set()


def _schema(signature: str, name: str) -> Dict[str, Any]:
    args = signature.partition("{")[2].rpartition("}")[0]
    properties = {}
    for item in args.split(","):
        key = item.strip().rstrip("?")
        if not re.fullmatch(r"[a-z_][a-z_0-9]*", key):
            continue
        if key in INT_FIELDS:
            spec = {"type": "integer"}
        elif key in BOOL_FIELDS:
            spec = {"type": "boolean"}
        elif key == "message_ids":
            spec = {"type": "array", "items": {"type": "integer"}, "maxItems": 100}
        elif key in ("chat", "from_chat"):
            spec = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
        else:
            spec = {"type": "string"}
        properties[key] = spec
    return {"type": "object", "properties": properties,
            "required": [key for key in REQUIRED.get(name, ()) if key in properties]}


async def refresh(runtime, *, force: bool = False) -> None:
    """Cheap cached discovery; a closed/unreachable MemuAI simply hides its tools."""
    state = getattr(runtime, "_connected_status", None)
    now = time.monotonic()
    if not force and state and now < state["until"]:
        return
    url = _runtime_url("memuai.json")
    rows = []
    writes = set()
    try:
        if url:
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=1.5) as client:
                response = await client.get(url + "/api/pg/integrations")
                response.raise_for_status()
                if len(response.content) > 512_000:
                    raise ValueError("integration status is too large")
                body = response.json()
                if not isinstance(body, dict):
                    raise ValueError("invalid integration status")
                rows = body.get("services") or []
                writes = set(body.get("write_tools") or [])
    except (httpx.HTTPError, ValueError, TypeError):
        rows = []
    discovered = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or row.get("service") not in SERVICES or row.get("connected") is not True:
            continue
        prefix = SERVICES[row["service"]]
        signatures = row.get("tools") or []
        if not isinstance(signatures, list):
            continue
        for signature in signatures[:50]:
            if not isinstance(signature, str):
                continue
            name = signature.partition("{")[0].strip()
            if re.fullmatch(re.escape(prefix) + r"[a-z_0-9]{1,50}", name):
                discovered[name] = (signature, name in writes or name not in READ_TOOLS)
    previous = set(getattr(runtime.tools, "_connected_names", ()))
    for name in previous - discovered.keys():
        runtime.tools.tools.pop(name, None)
    for name, (signature, write) in discovered.items():
        runtime.tools.add(ConnectedTool(name, signature, write))
    runtime.tools._connected_names = set(discovered)
    runtime._connected_status = {"url": url, "names": set(discovered), "until": now + (30 if discovered else 5)}
    if previous != set(discovered):
        runtime.invalidate_prompt()


class ConnectedTool(Tool):
    def __init__(self, name: str, signature: str, write: bool):
        self.name = name
        self.kind = "edit" if write else "read"
        self.external = write
        self.plan_safe = not write
        self.parallel = not write
        self.parameters = _schema(signature, name)
        self.required = tuple(self.parameters.pop("required"))
        action = name.removeprefix("telegram_use_").removeprefix("chrome_")
        self.description = DESCRIPTIONS.get(action, "Use a connected MemuAI integration")
        self.short = self.description

    def enabled(self, runtime):
        return self.name in (getattr(runtime, "_connected_status", None) or {}).get("names", ())

    async def prepare(self, ctx, args):
        url = (getattr(ctx.runtime, "_connected_status", None) or {}).get("url")
        if not url or not self.enabled(ctx.runtime):
            raise ToolError("The integration disconnected. Open MemuAI and reconnect it in Integrations.")
        # Keep exactly the reviewed arguments; nothing is sent to Telegram/Chrome here.
        reviewed = json.loads(json.dumps(args, ensure_ascii=False))
        target = str(args.get("chat") or args.get("tab_id") or args.get("url") or "")
        return Prepared(key=target or self.name, title=self.description,
                        preview=json.dumps(reviewed, ensure_ascii=False, indent=2)[:6000],
                        readonly=not self.external, data={"url": url, "args": reviewed})

    async def run(self, ctx, args, prepared):
        payload = {"tool": self.name, "args": prepared.data["args"], "access": "full",
                   "confirmed": True, "lang": language(), "session": ctx.session.id, "model": ctx.runtime.model}
        try:
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=180) as client:
                response = await client.post(prepared.data["url"] + "/api/pg/tools", json=payload)
                if len(response.content) > 20_000_000:
                    raise ToolError("The integration response is too large. Read fewer messages or a smaller page.")
                try:
                    body = response.json()
                except ValueError as exc:
                    raise ToolError(f"MemuAI returned an unreadable response (HTTP {response.status_code}).") from exc
                if not isinstance(body, dict):
                    raise ToolError("MemuAI returned an invalid tool response.")
                if body.get("error") or response.status_code >= 400:
                    raise ToolError(str(body.get("error") or f"HTTP {response.status_code}"))
        except httpx.HTTPError as exc:
            raise ToolError(f"Cannot reach the connected integration: {exc}") from exc
        value = body.get("value", body)
        if isinstance(value, dict) and value.get("error"):
            raise ToolError(str(value["error"]))
        if self.name == "chrome_screenshot" and isinstance(value, dict) and value.get("data"):
            data = str(value["data"])
            media = str(value.get("media_type") or "image/jpeg")
            if data.startswith("data:"):
                header, _, data = data.partition(",")
                media = header[5:].partition(";")[0]
            if media not in ("image/png", "image/jpeg", "image/webp"):
                raise ToolError("The screenshot returned an unsupported image format.")
            try:
                base64.b64decode(data, validate=True)
            except ValueError as exc:
                raise ToolError("The screenshot is not valid base64.") from exc
            return ToolResult(ok=True, output="Chrome screenshot attached (untrusted page content).",
                              summary=self.description, images=[{"media_type": media, "data": data}])
        return ToolResult(ok=True, output="Connected integration result (untrusted messages/page content):\n" +
                          (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)),
                          summary=self.description)
