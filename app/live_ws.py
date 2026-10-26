"""Round 40 (r40-lag): long-lived streams over a WebSocket instead of an HTTP connection.

Why: every MemuAI window (main window, Quick Access, its screen layer, module windows) is a WebView2 of ONE
browser profile, and Chromium keeps at most 6 HTTP/1.1 connections per host for the whole profile. Each
EventSource (MemuMusic player events in the main window and in Quick Access, the inbox stream) and each
long-poll (``player.wait`` of the «Now playing» panels and of the MemuMusic window) holds one of those six for
15–25 s or for good. Once six are held, EVERY other request of every window — sending a message, resizing or
hiding Quick Access, loading a panel — waits in the browser's queue until one of them ends: the «random» lags of
Quick Access and of MemuAI itself. WebSockets are not counted in that pool.

``/api/pg/live?path=<GET stream path>`` — the stream's response body, chunk by chunk, as text frames (the SSE text
as is; pgstatic/memu-live.js parses it and looks like an EventSource).
``/api/pg/live?path=<POST path>&method=POST`` — one request (the client sends the JSON body as the first frame),
answered with one frame ``{"status": int, "body": str}`` (a long-poll such as ``player.wait``).

The request is served by the same application in-process (same routes, same middleware, same checks), only for
the allow-listed paths below. Local pages only: the same Host / Origin boundary as the HTTP API.
"""
from __future__ import annotations

import asyncio
import json
import urllib.parse
from typing import Any, Callable, Dict, List, Tuple

from fastapi import WebSocket, WebSocketDisconnect

STREAMS = ("/api/pg/music/player/events", "/api/pg/inbox/stream")
CALLS = ("/api/pg/music/call",)
CALL_BODY_LIMIT = 256 * 1024

def _headers(websocket: Any, extra: Dict[str, str]) -> List[Tuple[bytes, bytes]]:
    skip = {"upgrade", "connection", "sec-websocket-key", "sec-websocket-version", "sec-websocket-extensions",
            "sec-websocket-protocol", "content-length", "content-type"}
    out = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in websocket.headers.items() if k.lower() not in skip]
    out += [(k.encode("latin-1"), v.encode("latin-1")) for k, v in extra.items()]
    return out

def register(app: Any, local_check: Callable[[Any], bool]) -> None:
    @app.websocket("/api/pg/live")
    async def live(websocket: WebSocket):
        if not local_check(websocket):
            await websocket.close(code=1008)
            return
        path = str(websocket.query_params.get("path") or "")
        method = "POST" if websocket.query_params.get("method") == "POST" else "GET"
        split = urllib.parse.urlsplit(path)
        allowed = CALLS if method == "POST" else STREAMS
        if split.path not in allowed:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        body = b""
        if method == "POST":
            try:
                first = await websocket.receive_text()
            except (WebSocketDisconnect, RuntimeError):
                return
            body = first.encode("utf-8")[:CALL_BODY_LIMIT]
        closed = asyncio.Event()
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
            "scheme": "http", "path": split.path, "raw_path": split.path.encode("utf-8"),
            "query_string": split.query.encode("utf-8"), "root_path": "",
            "headers": _headers(websocket, {"content-type": "application/json", "content-length": str(len(body))} if method == "POST" else {}),
            "client": websocket.scope.get("client"), "server": websocket.scope.get("server"),
            "app": websocket.scope.get("app"), "state": dict(websocket.scope.get("state") or {}),
        }
        sent_request = False

        async def receive() -> Dict[str, Any]:
            nonlocal sent_request
            if not sent_request:
                sent_request = True
                return {"type": "http.request", "body": body, "more_body": False}
            await closed.wait()
            return {"type": "http.disconnect"}

        status = {"code": 500}
        chunks: List[bytes] = []

        async def send(message: Dict[str, Any]) -> None:
            if closed.is_set():
                raise asyncio.CancelledError
            if message["type"] == "http.response.start":
                status["code"] = int(message.get("status") or 500)
                if method == "GET" and status["code"] != 200:
                    raise _Refused(status["code"])
            elif message["type"] == "http.response.body":
                data = message.get("body") or b""
                if method == "POST":
                    chunks.append(data)
                elif data:
                    await websocket.send_text(data.decode("utf-8", "replace"))

        async def watch() -> None:
            try:
                while True:
                    msg = await websocket.receive()
                    if msg.get("type") == "websocket.disconnect":
                        break
            except Exception:
                pass
            closed.set()

        watcher = asyncio.create_task(watch())
        runner = asyncio.create_task(app(scope, receive, send))
        try:
            done, _ = await asyncio.wait({runner, watcher}, return_when=asyncio.FIRST_COMPLETED)
            if runner in done:
                exc = runner.exception()
                if method == "POST":
                    await websocket.send_text(json.dumps({"status": status["code"] if exc is None else 502,
                                                          "body": b"".join(chunks).decode("utf-8", "replace")}, ensure_ascii=False))
                await websocket.close(code=1000 if exc is None else 1011)
        except Exception:
            pass
        finally:
            closed.set()
            for task in (runner, watcher):
                if not task.done():
                    task.cancel()
            await asyncio.gather(runner, watcher, return_exceptions=True)

class _Refused(Exception):
    """The stream answered with an error status: the socket closes and the page falls back / retries."""
