"""Round 30: DFY proxy (app/dfy_proxy.py) — settings, encrypted login, the local relay and a real browser behind it.

Test upstream proxies run in this process: an HTTP proxy (CONNECT + absolute-form, Basic auth) and a
SOCKS5 proxy (user/password). Both resolve the made-up host ``test.example`` themselves (→ 127.0.0.1),
so a page that loads proves the browser never resolved it locally (no DNS leak). No network.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import struct
import tempfile
import threading
import unittest
from pathlib import Path

os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="r30-proxy-"))

from app import dfy_proxy as dp  # noqa: E402

CHROMIUM = os.environ.get("MEMU_TEST_CHROMIUM") or os.environ.get("MEMUAI_BROWSER_EXECUTABLE") or \
    ("/workspace/work/chromium/chrome" if os.path.exists("/workspace/work/chromium/chrome") else "")
FAKE_HOST = "test.example"

PAGE = """<!doctype html><html><head><meta charset=utf-8><title>start</title></head><body><h1>via proxy</h1><script>
const ws = new WebSocket('ws://' + location.host + '/ws');
ws.onopen = () => ws.send('ping');
ws.onmessage = (e) => { document.title = 'ws:' + e.data; };
window.rtc = (async () => {
  const pc = new RTCPeerConnection({iceServers: [{urls: 'stun:stun.l.google.com:19302'}]});
  const found = [];
  pc.onicecandidate = (e) => { if (e.candidate && e.candidate.candidate) found.push(e.candidate.candidate); };
  pc.createDataChannel('x');
  await pc.setLocalDescription(await pc.createOffer());
  await new Promise(r => setTimeout(r, 1500));
  return found;
})();
</script></body></html>"""

class Upstreams:
    """Target web+ws server and the two upstream proxies, on one asyncio loop in a thread."""

    def __init__(self, user="u", password="p@ss"):
        self.user, self.password = user, password
        self.log = []
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()
        self.ready.wait(10)

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._start())
        self.ready.set()
        self.loop.run_forever()

    async def _start(self):
        from http import HTTPStatus
        from websockets.asyncio.server import serve

        async def handler(ws):
            async for message in ws:
                await ws.send("pong" if message == "ping" else message)

        def process(connection, request):
            if request.path == "/ws":
                return None
            body = "203.0.113.7" if request.path == "/ip" else PAGE
            response = connection.respond(HTTPStatus.OK, body)
            response.headers["Content-Type"] = "text/html; charset=utf-8" if request.path != "/ip" else "text/plain"
            return response
        self.target = await serve(handler, "127.0.0.1", 0, process_request=process)
        self.target_port = self.target.sockets[0].getsockname()[1] if hasattr(self.target, "sockets") else \
            list(self.target.server.sockets)[0].getsockname()[1]
        self.http = await asyncio.start_server(self._http_proxy, "127.0.0.1", 0)
        self.http_port = self.http.sockets[0].getsockname()[1]
        self.socks = await asyncio.start_server(self._socks_proxy, "127.0.0.1", 0)
        self.socks_port = self.socks.sockets[0].getsockname()[1]

    def _resolve(self, host):
        return "127.0.0.1" if host in (FAKE_HOST, "127.0.0.1") else None

    async def _pipe(self, reader, writer):
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()

    async def _http_proxy(self, reader, writer):
        head = await reader.readuntil(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        method, target, _ = lines[0].split(" ")
        auth = next((l.split(":", 1)[1].strip() for l in lines[1:] if l.lower().startswith("proxy-authorization:")), "")
        good = "Basic " + base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
        if auth != good:
            self.log.append(("http", "denied", target, 0))
            writer.write(b"HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()
            return
        if method == "CONNECT":
            host, port = target.rsplit(":", 1)
            self.log.append(("http", "connect", host, int(port)))
            ip = self._resolve(host)
            if ip is None:
                writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                writer.close()
                return
            r2, w2 = await asyncio.open_connection(ip, int(port))
            writer.write(b"HTTP/1.1 200 OK\r\n\r\n")
            await writer.drain()
        else:
            import urllib.parse
            parts = urllib.parse.urlsplit(target)
            self.log.append(("http", "get", parts.hostname, parts.port or 80))
            if self._resolve(parts.hostname) is None:
                writer.close()
                return
            r2, w2 = await asyncio.open_connection(self._resolve(parts.hostname), parts.port or 80)
            rest = [l for l in lines[1:] if l and not l.lower().startswith("proxy-")]
            w2.write(f"{method} {parts.path or '/'} HTTP/1.1\r\n".encode() + "\r\n".join(rest).encode() + b"\r\n\r\n")
        await asyncio.gather(self._pipe(reader, w2), self._pipe(r2, writer))

    async def _socks_proxy(self, reader, writer):
        ver, n = await reader.readexactly(2)
        methods = await reader.readexactly(n)
        if 2 not in methods:
            writer.write(b"\x05\xff")
            writer.close()
            return
        writer.write(b"\x05\x02")
        await reader.readexactly(1)
        user = (await reader.readexactly((await reader.readexactly(1))[0])).decode()
        password = (await reader.readexactly((await reader.readexactly(1))[0])).decode()
        if (user, password) != (self.user, self.password):
            self.log.append(("socks", "denied", user, 0))
            writer.write(b"\x01\x01")
            writer.close()
            return
        writer.write(b"\x01\x00")
        _v, _cmd, _rsv, atyp = await reader.readexactly(4)
        if atyp == 3:
            host = (await reader.readexactly((await reader.readexactly(1))[0])).decode()
        else:
            raw = await reader.readexactly(4 if atyp == 1 else 16)
            import ipaddress
            host = str(ipaddress.ip_address(raw))
        port = struct.unpack(">H", await reader.readexactly(2))[0]
        self.log.append(("socks", "connect" if atyp == 3 else "connect-ip", host, port))
        if self._resolve(host) is None:
            writer.write(b"\x05\x04\x00\x01\x00\x00\x00\x00\x00\x00")
            writer.close()
            return
        r2, w2 = await asyncio.open_connection(self._resolve(host), port)
        writer.write(b"\x05\x00\x00\x01\x7f\x00\x00\x01" + struct.pack(">H", port))
        await writer.drain()
        await asyncio.gather(self._pipe(reader, w2), self._pipe(r2, writer))

    def stop(self):
        self.loop.call_soon_threadsafe(self.loop.stop)

class Settings(unittest.TestCase):
    def test_store_keeps_the_login_encrypted_and_write_only(self):
        root = Path(tempfile.mkdtemp(prefix="r30-proxy-store-"))
        store = dp.ProxyStore(root)
        saved = store.set("local", {"enabled": True, "address": "socks5h://ivan:Sup3rS3cret@10.0.0.5:1081"})
        self.assertEqual((saved["scheme"], saved["host"], saved["port"], saved["auth"]), ("socks5", "10.0.0.5", 1081, True))
        self.assertNotIn("Sup3rS3cret", json.dumps(saved))
        for path in root.rglob("*"):
            if path.is_file():
                self.assertNotIn(b"Sup3rS3cret", path.read_bytes(), path)
        upstream = store.upstream("local")
        self.assertEqual((upstream.username, upstream.password), ("ivan", "Sup3rS3cret"))

        store.set("local", {"username": "petr", "password": ""})
        self.assertEqual(store.upstream("local").password, "Sup3rS3cret")
        self.assertEqual(store.upstream("local").username, "petr")
        store.set("local", {"clear_auth": True})
        self.assertFalse(store.get("local")["auth"])

        store.set("pc-2", {"enabled": True, "scheme": "http", "host": "proxy.lan", "port": 3128})
        self.assertEqual(store.get("pc-2")["host"], "proxy.lan")
        self.assertIsNone(store.upstream("nope"))
        store.set("local", {"enabled": False})
        self.assertIsNone(store.upstream("local"))
        with self.assertRaises(dp.ProxyError):
            store.set("local", {"enabled": True, "host": "", "scheme": "http"})
        with self.assertRaises(dp.ProxyError):
            dp.normalize({"enabled": True, "host": "a.b", "port": 70000})

    def test_chromium_flags_close_the_leaks(self):
        args = " ".join(dp.chromium_args(4321))
        self.assertIn("--proxy-server=http://127.0.0.1:4321", args)
        self.assertIn("--proxy-bypass-list=<-loopback>", args)
        self.assertIn("MAP * ~NOTFOUND", args)
        self.assertIn("disable_non_proxied_udp", args)
        self.assertIn("--disable-quic", args)
        with self.assertRaises(dp.ProxyError):
            dp.direct_args(dp.Upstream("socks5", "h", 1, "u", "p"))
        self.assertIn("--proxy-server=socks5://h:1", dp.direct_args(dp.Upstream("socks5", "h", 1)))

class Relay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.up = Upstreams()

    @classmethod
    def tearDownClass(cls):
        cls.up.stop()

    def fetch(self, upstream, path="/"):
        import httpx

        async def go():
            relay = dp.ProxyRelay(upstream)
            port = await relay.start()
            try:
                async with httpx.AsyncClient(proxy=f"http://127.0.0.1:{port}", trust_env=False, timeout=10) as client:
                    response = await client.get(f"http://{FAKE_HOST}:{self.up.target_port}{path}")
                return response, relay.last_error
            finally:
                await relay.close()
        return asyncio.run(go())

    def test_http_and_socks5_upstreams_with_login(self):
        for scheme, port in (("http", self.up.http_port), ("socks5", self.up.socks_port)):
            with self.subTest(scheme):
                self.up.log.clear()
                response, error = self.fetch(dp.Upstream(scheme, "127.0.0.1", port, "u", "p@ss"))
                self.assertEqual(response.status_code, 200)
                self.assertIn("via proxy", response.text)
                self.assertIsNone(error)
                self.assertTrue(any(row[2] == FAKE_HOST for row in self.up.log), self.up.log)
                if scheme == "socks5":
                    self.assertTrue(all(row[1] != "connect-ip" for row in self.up.log), "socks5h: the name goes to the proxy")

    def test_wrong_password_is_reported(self):
        for scheme, port in (("http", self.up.http_port), ("socks5", self.up.socks_port)):
            with self.subTest(scheme):
                response, error = self.fetch(dp.Upstream(scheme, "127.0.0.1", port, "u", "wrong"))
                self.assertGreaterEqual(response.status_code, 400)
                if scheme == "socks5":
                    self.assertEqual(error.code, "auth")

    def test_unreachable_proxy_fails_closed(self):
        response, error = self.fetch(dp.Upstream("socks5", "127.0.0.1", 9, "u", "p"))
        self.assertEqual(response.status_code, 502)
        self.assertEqual(error.code, "refused")

    def test_check_reports_the_external_ip(self):
        services = (f"http://{FAKE_HOST}:{self.up.target_port}/ip",)
        result = asyncio.run(dp.check(dp.Upstream("socks5", "127.0.0.1", self.up.socks_port, "u", "p@ss"), services=services))
        self.assertEqual(result["ip"], "203.0.113.7")
        self.assertTrue(result["ok"])
        bad = asyncio.run(dp.check(dp.Upstream("socks5", "127.0.0.1", self.up.socks_port, "u", "nope"), services=services))
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["code"], "auth")

@unittest.skipUnless(CHROMIUM and os.path.exists(CHROMIUM), "no Chromium")
class BrowserBehindProxy(unittest.TestCase):
    """A real headless Chromium launched by DFYManager goes through the relay: page, WebSocket, no WebRTC leak."""

    def test_dfy_browser_traffic_goes_through_the_proxy(self):
        up = Upstreams()
        data = Path(tempfile.mkdtemp(prefix="r30-proxy-browser-"))
        os.environ["MEMUAI_BROWSER_EXECUTABLE"] = CHROMIUM
        os.environ.setdefault("MEMUAI_BROWSER_ARGS", "--no-sandbox")
        from app.dfy import DFYManager
        from app.dfy_browser import DFYBrowser

        async def scenario():
            async def completion(model, messages):
                return "{}"
            manager = DFYManager(completion, lambda: {"dfy_model": "v"}, data_root=data, vision_lookup=lambda _m: True,
                                 browser_factory=lambda _v: DFYBrowser(profile=data / "profile"))
            results = {}
            for scheme, port in (("socks5", up.socks_port), ("http", up.http_port)):
                manager.proxy.set("local", {"enabled": True, "scheme": scheme, "host": "127.0.0.1", "port": port,
                                            "username": "u", "password": "p@ss"})
                up.log.clear()
                browser = await manager._ensure_browser(False)
                self.assertTrue(browser.proxied)
                await browser.navigate(f"http://{FAKE_HOST}:{up.target_port}/")
                for _ in range(40):
                    if (await browser.evaluate("document.title")).startswith("ws:"):
                        break
                    await asyncio.sleep(0.1)
                candidates = await browser.evaluate("window.rtc")
                results[scheme] = (await browser.evaluate("document.title"), candidates, list(up.log))
            manager.proxy.set("local", {"enabled": False})
            await manager.shutdown()
            return results
        try:
            results = asyncio.run(scenario())
        finally:
            up.stop()
            shutil.rmtree(data, ignore_errors=True)
        for scheme, (title, candidates, log) in results.items():
            with self.subTest(scheme):
                self.assertEqual(title, "ws:pong", "the page and its WebSocket loaded through the proxy")
                hosts = {row[2] for row in log}
                self.assertIn(FAKE_HOST, hosts)
                self.assertEqual([c for c in candidates if " host " in c or " srflx " in c], [],
                                 "WebRTC must not reveal local or STUN addresses")

if __name__ == "__main__":
    unittest.main()
