# -*- coding: utf-8 -*-
"""Round 29: offline PDF (and element snapshots) through a headless Microsoft Edge.

The export HTML (fonts, KaTeX and images embedded) is written to a temporary file and opened by an
isolated headless Edge — the same engine as the MemuAI window (WebView2), so the PDF looks exactly
like the preview. ``Page.printToPDF`` gives real paged output: page size, margins, backgrounds,
repeated table headers and a header/footer with page numbers.

The browser has no network at all (every request goes to a dead proxy, loopback included) and a
throw-away profile; it only renders the file it was given. The same session can also take PNG
snapshots of marked elements (``data-mx-raster``) for the DOCX export — interactive visualizations
and diagrams that Word cannot draw itself.

Edge is part of every supported Windows; when it is missing (Linux CI, a stripped system) the
window falls back to its own print dialog («Save as PDF»).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("memuai.export")

PAPER_INCHES = {"a4": (8.27, 11.69), "letter": (8.5, 11.0)}
START_TIMEOUT = 20.0
LOAD_TIMEOUT = 45.0
_LOCK = asyncio.Lock()


def edge_path() -> str:
    from .automation_runtime import _edge_executable
    return _edge_executable()


def available() -> bool:
    try:
        return bool(edge_path())
    except Exception:
        return False


def _work_dir() -> Path:
    try:
        from .config import DATA_DIR
        base = Path(DATA_DIR) / "memuai" / "export-tmp"
    except Exception:
        base = Path(tempfile.gettempdir()) / "memuai-export"
    base.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - 3600
    for stale in base.glob("x-*"):
        try:
            if stale.stat().st_mtime < cutoff:
                shutil.rmtree(stale, True) if stale.is_dir() else stale.unlink()
        except OSError:
            pass
    return base


class EdgeSession:
    """One headless Edge with one page, driven over the DevTools protocol."""

    def __init__(self) -> None:
        self.dir = _work_dir() / f"x-{uuid.uuid4().hex[:12]}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.process: Optional[subprocess.Popen] = None
        self.socket: Any = None
        self.receiver: Optional[asyncio.Task] = None
        self.pending: Dict[int, asyncio.Future] = {}
        self.events: List[Dict[str, Any]] = []
        self.event_flag = asyncio.Event()
        self.counter = 0

    async def __aenter__(self) -> "EdgeSession":
        await self.start()
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.close()

    async def start(self) -> None:
        from .automation_runtime import _connect_cdp, _read_json_url
        profile = self.dir / "profile"
        profile.mkdir(exist_ok=True)
        port_file = profile / "DevToolsActivePort"
        command = [
            edge_path(), "--headless=new", "--remote-debugging-port=0", f"--user-data-dir={profile}",
            "--no-first-run", "--no-default-browser-check", "--disable-sync", "--disable-extensions",
            "--disable-background-networking", "--disable-component-update", "--disable-default-apps",
            "--disable-features=Translate,msEdgeSidebarV2,MediaRouter", "--hide-scrollbars", "--mute-audio",
            # No network: everything (loopback too) goes to a dead proxy. The document is self-contained.
            "--proxy-server=127.0.0.1:9", "--proxy-bypass-list=<-loopback>",
            "--allow-file-access-from-files", "about:blank",
        ]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, creationflags=flags)
        deadline = time.monotonic() + START_TIMEOUT
        port = 0
        while time.monotonic() < deadline and self.process.poll() is None:
            try:
                port = int(port_file.read_text(encoding="utf-8").splitlines()[0].strip())
                if port:
                    break
            except (OSError, ValueError, IndexError):
                pass
            await asyncio.sleep(0.1)
        if not port:
            raise RuntimeError("Edge did not start")
        target = None
        for _ in range(100):
            try:
                targets = await asyncio.to_thread(_read_json_url, f"http://127.0.0.1:{port}/json/list")
                target = next((item for item in targets or [] if item.get("type") == "page"
                               and item.get("webSocketDebuggerUrl")), None)
                if target:
                    break
            except Exception:
                pass
            await asyncio.sleep(0.1)
        if not target:
            raise RuntimeError("Edge page is not available")
        self.socket = await _connect_cdp(target["webSocketDebuggerUrl"], max_size=512 * 1024 * 1024)
        self.receiver = asyncio.create_task(self._receive())
        await self.command("Page.enable")
        await self.command("Runtime.enable")

    async def _receive(self) -> None:
        try:
            async for raw in self.socket:
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                if "id" in message:
                    future = self.pending.pop(int(message["id"]), None)
                    if future is not None and not future.done():
                        if "error" in message:
                            future.set_exception(RuntimeError(str(message["error"].get("message") or message["error"])))
                        else:
                            future.set_result(message.get("result") or {})
                elif message.get("method"):
                    self.events.append(message)
                    self.event_flag.set()
        except Exception:
            pass
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(RuntimeError("Edge closed"))
            self.pending.clear()

    async def command(self, method: str, params: Optional[Dict[str, Any]] = None, *, timeout: float = 60.0) -> Dict[str, Any]:
        self.counter += 1
        ident = self.counter
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending[ident] = future
        await self.socket.send(json.dumps({"id": ident, "method": method, "params": params or {}}))
        return await asyncio.wait_for(future, timeout)

    async def wait_event(self, name: str, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if any(event.get("method") == name for event in self.events):
                return True
            self.event_flag.clear()
            try:
                await asyncio.wait_for(self.event_flag.wait(), max(0.05, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                break
        return any(event.get("method") == name for event in self.events)

    async def load_html(self, document: str, *, width: int = 900) -> None:
        path = self.dir / "document.html"
        path.write_text(document, encoding="utf-8")
        await self.command("Emulation.setDeviceMetricsOverride",
                           {"width": int(width), "height": 1200, "deviceScaleFactor": 1, "mobile": False})
        self.events.clear()
        await self.command("Page.navigate", {"url": path.resolve().as_uri()})
        await self.wait_event("Page.loadEventFired", LOAD_TIMEOUT)
        # Fonts (KaTeX, embedded) and images must be decoded before printing; inline visualizations
        # (sandboxed iframes) get a moment to draw.
        script = ("Promise.race([Promise.all([document.fonts ? document.fonts.ready : 1,"
                  " ...Array.from(document.images).map(i => i.decode ? i.decode().catch(() => 0) : 0)]),"
                  " new Promise(r => setTimeout(r, 15000))]).then(() => new Promise(r => setTimeout(r,"
                  " document.querySelector('iframe') ? 1200 : 150))).then(() => 1)")
        try:
            await self.command("Runtime.evaluate", {"expression": script, "awaitPromise": True, "returnByValue": True},
                               timeout=30)
        except Exception:
            pass

    async def pdf(self, options: Dict[str, Any]) -> bytes:
        paper = PAPER_INCHES.get(str(options.get("page") or "a4"), PAPER_INCHES["a4"])
        landscape = options.get("orientation") == "landscape"
        margin_in = max(0.0, min(2.0, float(options.get("margin_mm") or 20) / 25.4))
        header = str(options.get("header_html") or "")
        footer = str(options.get("footer_html") or "")
        show = bool(header or footer)
        params = {
            "landscape": landscape, "printBackground": True, "preferCSSPageSize": False,
            "paperWidth": paper[0], "paperHeight": paper[1],
            "marginTop": max(margin_in, 0.45 if header else 0), "marginBottom": max(margin_in, 0.45 if footer else 0),
            "marginLeft": margin_in, "marginRight": margin_in,
            "displayHeaderFooter": show, "headerTemplate": header or "<span></span>",
            "footerTemplate": footer or "<span></span>", "generateDocumentOutline": True, "generateTaggedPDF": True,
        }
        try:
            result = await self.command("Page.printToPDF", params, timeout=180)
        except RuntimeError as exc:
            # Older Edge builds reject the outline / tagged flags.
            if "generate" not in str(exc).lower() and "invalid" not in str(exc).lower():
                raise
            params.pop("generateDocumentOutline", None)
            params.pop("generateTaggedPDF", None)
            result = await self.command("Page.printToPDF", params, timeout=180)
        return base64.b64decode(result.get("data") or "")

    async def snapshots(self, selector: str = "[data-mx-raster]", *, scale: float = 2.0) -> Dict[str, str]:
        """PNG data URLs of the marked elements, keyed by their ``data-mx-raster`` id."""
        script = ("JSON.stringify(Array.from(document.querySelectorAll(%s)).map(e => {const r = e.getBoundingClientRect();"
                  " return {id: e.getAttribute('data-mx-raster'), x: r.left + scrollX, y: r.top + scrollY, w: r.width, h: r.height};}))"
                  % json.dumps(selector))
        result = await self.command("Runtime.evaluate", {"expression": script, "returnByValue": True})
        try:
            boxes = json.loads((result.get("result") or {}).get("value") or "[]")
        except ValueError:
            boxes = []
        shots: Dict[str, str] = {}
        for box in boxes[:200]:
            if not box.get("id") or box.get("w", 0) < 2 or box.get("h", 0) < 2:
                continue
            try:
                shot = await self.command("Page.captureScreenshot", {
                    "format": "png", "captureBeyondViewport": True, "fromSurface": True,
                    "clip": {"x": box["x"], "y": box["y"], "width": min(box["w"], 4000), "height": min(box["h"], 6000),
                             "scale": scale}}, timeout=60)
                if shot.get("data"):
                    shots[str(box["id"])] = "data:image/png;base64," + shot["data"]
            except Exception as exc:  # noqa: BLE001
                log.info("export: snapshot failed (%s)", type(exc).__name__)
        return shots

    async def close(self) -> None:
        try:
            if self.socket is not None:
                try:
                    await asyncio.wait_for(self.command("Browser.close", timeout=3), 4)
                except Exception:
                    pass
                await self.socket.close()
        except Exception:
            pass
        if self.receiver is not None:
            self.receiver.cancel()
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.terminate()
                await asyncio.to_thread(self.process.wait, 5)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
        await asyncio.sleep(0.2)
        shutil.rmtree(self.dir, ignore_errors=True)


async def html_to_pdf(document: str, options: Dict[str, Any]) -> bytes:
    async with _LOCK:                      # one Edge at a time: exports are rare and heavy
        async with EdgeSession() as session:
            await session.load_html(document, width=int(options.get("viewport") or 900))
            return await session.pdf(options)


async def rasterize(document: str, *, width: int = 820) -> Dict[str, str]:
    async with _LOCK:
        async with EdgeSession() as session:
            await session.load_html(document, width=width)
            return await session.snapshots()
