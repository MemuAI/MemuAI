"""Round 40 (r40-lag): «Quick Access lags a lot, MemuAI itself stutters at random; the actions list of Quick Access is cut
off under the search field».

Causes found:
* every MemuAI window is a WebView2 of one browser profile — Chromium keeps 6 HTTP/1.1 connections per host for the
  whole profile. The MemuMusic player stream (main window + Quick Access), the inbox stream and the 25 s
  ``player.wait`` long-polls of the «Now playing» panels / MemuMusic window held them; with six held, every other
  request of every window queued (sending, resizing / hiding Quick Access, panels). Now they run over a WebSocket
  (app/live_ws.py + pgstatic/memu-live.js), which is not in that pool;
* the equalizer ring around Quick Access woke on every display frame and stroked ~500 bars one by one;
* the actions / model / chats lists revealed the active row with scrollIntoView, which also scrolled the page while
  the window was still shorter than its content — the input row went off the top, the list slid under the search
  field; the view also kept a stale data-view (the canvas strip stayed on screen); the resize went through the HTTP
  server only.

    MEMU_TEST_CHROMIUM=/path/to/chrome python -m pytest -q tests/round40_lag_test.py
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="memu-r40-lag-"))
os.environ.setdefault("MEMUAI_PROXY_URL", "http://127.0.0.1:9")
sys.modules.setdefault("webview", types.ModuleType("webview"))

STATIC = ROOT / "app" / "pgstatic"
CHROME = os.environ.get("MEMU_TEST_CHROMIUM") or "/workspace/work/chromium/chrome"

def read(rel: str) -> str:
    return (STATIC / rel).read_text("utf-8")

class SourceTest(unittest.TestCase):
    def test_long_lived_streams_use_the_websocket(self):
        for rel in ("music-dock.js", "inbox.js", "quick/plugins/modules.js"):
            src = read(rel)
            for line in src.splitlines():
                if "new EventSource(" in line and ("player/events" in line or "inbox/stream" in line):
                    self.assertIn("MemuLive", line, f"{rel}: an HTTP EventSource holds one of the 6 connections: {line.strip()}")
        for rel in ("modules.js", "module-integration.js"):
            self.assertRegex(read(rel), r'player\.wait"\s*&&\s*window\.MemuLive', f"{rel}: player.wait must go over MemuLive")
        for rel in ("index.html", "quick/index.html", "quick/layer.html"):
            self.assertIn('src="/memu-live.js', read(rel), rel)

    def test_quick_lists_never_scroll_the_page(self):
        src = read("quick/quick.js")
        self.assertNotIn(".scrollIntoView(", src)
        for fn in ("openMenu", "openPicker", "openChats"):
            body = src[src.index(f"function {fn}("):][:500]
            self.assertIn("setView()", body, fn)
        self.assertIn("bridge.resize", src)

    def test_ring_is_paced_and_batched(self):
        src = read("quick/plugins/modules.js")
        self.assertNotIn("now - S.last >= 33", src)
        self.assertIn("setTimeout(() => { S.ringTimer = 0; S.raf = requestAnimationFrame(frame); }, 50)", src)
        self.assertIn("new Path2D()", src)

    def test_bridge_resize(self):
        from app.quick_ask import QuickBridge
        calls = []
        fake = type("M", (), {"resize": lambda self, h, w=None, animate=True: calls.append((h, w, animate)) or {"ok": True}})()
        self.assertTrue(QuickBridge(fake).resize(321, False))
        self.assertEqual(calls, [(321.0, None, False)])
        self.assertFalse(QuickBridge(fake).resize("x"))

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

class LiveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import uvicorn
        from app.playground_api import create_app
        cls.port = free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=cls.port, log_level="error"))
        threading.Thread(target=cls.server.run, daemon=True).start()
        import httpx
        deadline = time.time() + 90
        while time.time() < deadline:
            try:
                if httpx.get(cls.base + "/quick/", timeout=2).status_code == 200:
                    break
            except Exception:
                time.sleep(0.3)
        cls.http = httpx.Client(base_url=cls.base, timeout=10)
        cls.http.post("/api/pg/modules/configure", json={"id": "music", "enabled": True})
        cls.http.post("/api/pg/music/call", json={"op": "player.update", "args": {"track_id": "abcdefabcdef", "playing": True,
                      "position": 3, "duration": 100000, "queue": ["abcdefabcdef"], "index": 0, "device": "pc"}})

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True

    def ws(self, query, headers=None):
        import websockets
        return websockets.connect(f"ws://127.0.0.1:{self.port}/api/pg/live?{query}", additional_headers=headers or {})

    def test_websocket_stream_call_and_boundary(self):
        async def run():
            async with self.ws("path=/api/pg/music/player/events") as ws:
                text = ""
                while "\n\n" not in text.split("data: ", 1)[-1]:
                    text += await asyncio.wait_for(ws.recv(), 10)
                line = next(x for x in text.splitlines() if x.startswith("data: "))
                self.assertEqual(json.loads(line[6:])["state"]["track_id"], "abcdefabcdef")
            async with self.ws("path=/api/pg/music/call&method=POST") as ws:
                await ws.send(json.dumps({"op": "player.state", "args": {}}))
                out = json.loads(await asyncio.wait_for(ws.recv(), 10))
                self.assertEqual(out["status"], 200)
                self.assertEqual(json.loads(out["body"])["result"]["track_id"], "abcdefabcdef")
            async with self.ws("path=/api/pg/music/call&method=POST") as ws:
                await ws.send(json.dumps({"op": "player.wait", "args": {"since": 0, "timeout": 5}}))
                out = json.loads(await asyncio.wait_for(ws.recv(), 10))
                self.assertEqual(out["status"], 200)
                self.assertTrue(json.loads(out["body"])["result"]["changed"])
            for query, headers in (("path=/api/pg/preferences", None), ("path=/api/pg/music/call", None),
                                   ("path=/api/pg/inbox/stream", {"Origin": "http://evil.example"})):
                with self.assertRaises(Exception, msg=query):
                    async with self.ws(query, headers) as ws:
                        await asyncio.wait_for(ws.recv(), 5)
        asyncio.run(run())

    @unittest.skipUnless(Path(CHROME).exists(), "no Chromium")
    def test_browser(self):
        from playwright.async_api import async_playwright

        async def run():
            async with async_playwright() as p:
                browser = await p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
                ctx = await browser.new_context(viewport={"width": 720, "height": 230})
                http_streams = []
                ctx.on("request", lambda r: ("/player/events" in r.url or "/inbox/stream" in r.url) and http_streams.append(r.url))
                main = await ctx.new_page()
                await main.goto(self.base + "/", wait_until="load")
                quick = await ctx.new_page()
                t0 = time.perf_counter()
                await quick.goto(self.base + "/quick/?native=1", wait_until="load")
                await quick.evaluate("QuickAsk.onShow({fresh: true, maxHeight: 700, seq: 1})")
                opened_ms = (time.perf_counter() - t0) * 1000
                self.assertLess(opened_ms, 8000)

                await main.evaluate("for (let i = 0; i < 8; i++) MemuLive.stream('/api/pg/music/player/events')")
                await main.wait_for_timeout(2500)
                took = await quick.evaluate("(async () => { const t = performance.now(); await fetch('/api/quick/config?x=' + Math.random()); return performance.now() - t; })()")
                self.assertLess(took, 2000, "a plain request waited behind held connections")
                self.assertEqual(http_streams, [], "streams must not be HTTP EventSources")
                self.assertTrue(await quick.evaluate("!!(window.MemuQuickModules && MemuQuickModules.state().es)"))

                await quick.evaluate("""() => { const p = MemuQuick.registerPanel('r40-card', (el) => { el.innerHTML = '<div style="height:64px">card</div>'; return {}; }, { slot: 'top' }); p.show(); }""")
                await quick.keyboard.press("Control+k")
                for _ in range(14):
                    await quick.keyboard.press("ArrowDown")
                await quick.wait_for_timeout(300)
                geo = await quick.evaluate("""() => ({ scrolled: document.scrollingElement.scrollTop, view: document.getElementById('qa-card').dataset.view,
                  input: document.getElementById('qa-input').getBoundingClientRect().top,
                  head: document.querySelector('#qa-menu .qa-view-head').getBoundingClientRect().bottom,
                  list: document.getElementById('qa-menu-list').getBoundingClientRect().top,
                  listScroll: document.getElementById('qa-menu-list').scrollTop })""")
                self.assertEqual(geo["scrolled"], 0, geo)
                self.assertEqual(geo["view"], "menu", geo)
                self.assertGreaterEqual(geo["input"], 0, geo)
                self.assertGreaterEqual(geo["list"], geo["head"], geo)
                self.assertGreater(geo["listScroll"], 0, "the active row is revealed inside the list")
                await browser.close()
        asyncio.run(run())

if __name__ == "__main__":
    unittest.main()
