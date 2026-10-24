"""Round 33 (r33d-media): generated pictures are stored as files instead of links that expire in ~24 h.

Offline (mocked HTTP, httpx.MockTransport):
* download → validated → stored in ``generated_media`` (sha256 name, the video mechanism), served by /api/pg/media/file;
* SSRF guard: private / loopback addresses, credentials, a redirect to a private address, a non-public peer;
* limits: size (declared and streamed), not-an-image body, expired statuses (403/404/410) vs transient (5xx);
* ``localize_urls`` keeps the link on failure, ``localize_sse`` rewrites only the ``done`` event;
* POST /api/pg/media/cache (lazy migration): ``{url: local}`` / ``{expired: true}`` / 502 retry;
* Genspark links through the configured bridge; read_local / local_data_url honour the active profile only;
* the window side: media-cache.js in a node vm (migration swap, expired card, forModel), tags, spec/build, docs.

Live (skipped without REFORM_BASE/REFORM_KEY): wanx2.1-t2i-turbo returns a link → it is cached as a real picture.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="memu-r33-media-"))

import httpx  # noqa: E402

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
NODE = shutil.which("node")


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class MockedNet:
    """Patches DNS, the peer check and the HTTP client of app.media_cache with a MockTransport."""

    def __init__(self, handler, resolve=None, peer_ok=True):
        from app import media_cache
        self.mc = media_cache
        self.handler = handler
        self.resolve = resolve or (lambda host, port: ["93.184.216.34"])
        self.peer_ok = peer_ok
        self.requests = []

    def __enter__(self):
        mc = self.mc
        self.saved = (mc._resolve, mc._check_peer, mc._client)

        def handler(request):
            self.requests.append(str(request.url))
            return self.handler(request)
        mc._resolve = self.resolve
        if self.peer_ok:
            mc._check_peer = lambda response: None
        else:
            def bad(response):
                raise mc.Unreachable("the server redirected to a local address")
            mc._check_peer = bad
        mc._client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
        return self

    def __exit__(self, *exc):
        self.mc._resolve, self.mc._check_peer, self.mc._client = self.saved


def png_handler(request):
    return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})


class BackendTest(unittest.TestCase):
    def setUp(self):
        from app import media_cache, media_routing
        self.mc = media_cache
        self.folder = media_routing.media_folder()

    def test_sniff(self):
        self.assertEqual(self.mc.sniff_image(PNG), "image/png")
        self.assertEqual(self.mc.sniff_image(JPEG), "image/jpeg")
        self.assertEqual(self.mc.sniff_image(b"GIF89a....."), "image/gif")
        self.assertEqual(self.mc.sniff_image(b"RIFF\x00\x00\x00\x00WEBPVP8 "), "image/webp")
        self.assertIsNone(self.mc.sniff_image(b"<html>expired</html>"))
        self.assertIsNone(self.mc.sniff_image(b"<svg xmlns='http://www.w3.org/2000/svg'/>"))

    def test_cache_stores_like_videos(self):
        with MockedNet(png_handler):
            stored = run(self.mc.cache_url("https://dashscope-result.oss-cn-beijing.aliyuncs.com/a.png?Expires=1&Signature=x"))
        self.assertRegex(stored["url"], r"^/api/pg/media/file/[0-9a-f]{64}\.png\?profile_id=default$")
        name = stored["url"].split("/")[-1].split("?")[0]
        self.assertEqual((self.folder / name).read_bytes(), PNG)
        self.assertTrue(self.mc.is_local(stored["url"]))
        raw, mime = self.mc.read_local(stored["url"])
        self.assertEqual((raw, mime), (PNG, "image/png"))
        self.assertTrue(self.mc.local_data_url(stored["url"]).startswith("data:image/png;base64,"))
        # another profile's address is not readable from the active one
        self.assertIsNone(self.mc.read_local(stored["url"].replace("default", "other")))
        self.assertIsNone(self.mc.read_local("/api/pg/media/file/../../secret.png"))

    def test_ssrf_guard(self):
        for url in ("http://127.0.0.1/x.png", "http://localhost/x.png", "ftp://example.com/x.png", "file:///etc/passwd",
                    "https://user:pw@example.com/x.png"):
            with MockedNet(png_handler, resolve=lambda h, p: ["127.0.0.1"] if h in ("127.0.0.1", "localhost") else ["93.184.216.34"]) as net:
                with self.assertRaises(self.mc.Unreachable, msg=url):
                    run(self.mc.download_image(url))
                self.assertEqual(net.requests, [], url)
        for private in ("10.0.0.5", "192.168.1.2", "169.254.169.254", "::1", "fd00::1", "224.0.0.1"):
            with MockedNet(png_handler, resolve=lambda h, p, ip=private: [ip]) as net:
                with self.assertRaises(self.mc.Unreachable):
                    run(self.mc.download_image("https://evil.example/x.png"))
                self.assertEqual(net.requests, [])

    def test_redirect_to_private_is_blocked_and_peer_is_checked(self):
        def handler(request):
            if request.url.host == "cdn.example":
                return httpx.Response(302, headers={"location": "http://internal.example/x.png"})
            return png_handler(request)
        resolve = lambda h, p: ["10.1.2.3"] if h == "internal.example" else ["93.184.216.34"]
        with MockedNet(handler, resolve=resolve) as net:
            with self.assertRaises(self.mc.Unreachable):
                run(self.mc.download_image("https://cdn.example/x.png"))
            self.assertEqual(len(net.requests), 1)
        with MockedNet(png_handler, peer_ok=False):
            with self.assertRaises(self.mc.Unreachable):
                run(self.mc.download_image("https://cdn.example/x.png"))
        # a public redirect is followed; too many is an error
        hops = {"n": 0}

        def loop(request):
            hops["n"] += 1
            return httpx.Response(302, headers={"location": f"https://cdn.example/{hops['n']}"})
        with MockedNet(loop):
            with self.assertRaises(self.mc.Unreachable):
                run(self.mc.download_image("https://cdn.example/0"))
        self.assertEqual(hops["n"], self.mc.MAX_REDIRECTS + 1)

    def test_limits_and_statuses(self):
        old = self.mc.MAX_IMAGE_BYTES
        self.mc.MAX_IMAGE_BYTES = 1000
        try:
            with MockedNet(lambda r: httpx.Response(200, content=PNG + b"\x00" * 2000)):
                with self.assertRaises(self.mc.Unreachable):
                    run(self.mc.download_image("https://cdn.example/big.png"))
            with MockedNet(lambda r: httpx.Response(200, content=PNG, headers={"content-length": "999999"})):
                with self.assertRaises(self.mc.Unreachable):
                    run(self.mc.download_image("https://cdn.example/big.png"))
        finally:
            self.mc.MAX_IMAGE_BYTES = old
        for status in (403, 404, 410):
            with MockedNet(lambda r, s=status: httpx.Response(s, text="<Error><Code>AccessDenied</Code>Request has expired</Error>")):
                with self.assertRaises(self.mc.Expired):
                    run(self.mc.cache_url("https://cdn.example/old.png"))
        with MockedNet(lambda r: httpx.Response(503)):
            with self.assertRaises(self.mc.Unreachable):
                run(self.mc.cache_url("https://cdn.example/x.png"))
        with MockedNet(lambda r: httpx.Response(200, text="<html>not here</html>", headers={"content-type": "image/png"})):
            with self.assertRaises(self.mc.Expired):
                run(self.mc.cache_url("https://cdn.example/x.png"))

    def test_timeout(self):
        old = self.mc.TIMEOUT
        self.mc.TIMEOUT = 0.2

        async def slow(*a, **k):
            await asyncio.sleep(2)
        saved = self.mc._download_public
        self.mc._download_public = slow
        try:
            with self.assertRaises(self.mc.Unreachable):
                run(self.mc.download_image("https://cdn.example/x.png"))
        finally:
            self.mc._download_public = saved
            self.mc.TIMEOUT = old

    def test_localize_urls_keeps_link_on_failure(self):
        def handler(request):
            return png_handler(request) if "good" in str(request.url) else httpx.Response(503)
        with MockedNet(handler):
            out = run(self.mc.localize_urls(["https://cdn.example/good.png", "https://cdn.example/bad.png",
                                             "data:image/png;base64,AAAA", "/api/pg/media/file/" + "a" * 64 + ".png"]))
        self.assertTrue(self.mc.is_local(out[0]))
        self.assertEqual(out[1:], ["https://cdn.example/bad.png", "data:image/png;base64,AAAA", "/api/pg/media/file/" + "a" * 64 + ".png"])

    def test_localize_sse_rewrites_only_done(self):
        async def stream():
            yield b'data: {"status": "queued"}\n\n'
            yield b'data: {"status": "generating", "ms": 10}\n\ndata: {"status": "do'
            yield b'ne", "urls": ["https://cdn.example/r.png"], "ms": 900}\n\n'
            yield b"data: [DONE]\n\n"

        async def collect():
            return b"".join([c async for c in self.mc.localize_sse(stream())])
        with MockedNet(png_handler):
            body = run(collect()).decode()
        events = [e for e in body.split("\n\n") if e]
        self.assertEqual(events[0], 'data: {"status": "queued"}')
        done = json.loads(events[2][6:])
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["ms"], 900)
        self.assertRegex(done["urls"][0], r"^/api/pg/media/file/[0-9a-f]{64}\.png")
        self.assertEqual(events[-1], "data: [DONE]")

    def test_genspark_through_bridge(self):
        calls = []

        async def bridge(url):
            calls.append(url)
            return 200, JPEG, "image/jpeg"
        url = "https://www.genspark.ai/api/files/s/abc123"
        stored = run(self.mc.cache_url(url, genspark_fetch=bridge))
        self.assertTrue(stored["url"].endswith(".jpg?profile_id=default"))
        self.assertEqual(calls, [url])

        async def gone(url):
            return 404, b"", ""
        with self.assertRaises(self.mc.Expired):
            run(self.mc.cache_url(url, genspark_fetch=gone))
        old = self.mc._genspark_fetch
        self.mc._genspark_fetch = None
        try:
            self.assertEqual(run(self.mc.localize_urls([url])), [url])   # no bridge → kept, not lost
        finally:
            self.mc._genspark_fetch = old

    def test_cache_route(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        self.mc.register(app)
        client = TestClient(app)

        def handler(request):
            path = request.url.path
            if path.endswith("ok.png"):
                return png_handler(request)
            if path.endswith("dead.png"):
                return httpx.Response(403, text="Request has expired")
            return httpx.Response(502)
        with MockedNet(handler):
            ok = client.post("/api/pg/media/cache", json={"url": "https://cdn.example/ok.png"}).json()
            dead = client.post("/api/pg/media/cache", json={"url": "https://cdn.example/dead.png"}).json()
            flaky = client.post("/api/pg/media/cache", json={"url": "https://cdn.example/flaky.png"})
        self.assertRegex(ok["url"], r"^/api/pg/media/file/[0-9a-f]{64}\.png")
        self.assertTrue(dead["expired"])
        self.assertEqual(flaky.status_code, 502)
        self.assertTrue(flaky.json()["retry"])
        self.assertEqual(client.post("/api/pg/media/cache", json={"url": "javascript:alert(1)"}).status_code, 400)
        self.assertEqual(client.post("/api/pg/media/cache", json={"url": ok["url"]}).json()["local"], True)

    def test_media_routing_generate_localizes(self):
        """/api/pg/media/generate: a remote picture of the Proxy stream becomes a stored file."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import media_routing

        async def stream_lines(path, raw):
            yield 'data: {"status": "queued"}'
            yield 'data: {"status": "done", "urls": ["https://dashscope.example/out.png?Expires=1"]}'
        app = FastAPI()
        media_routing.register(app, stream_lines=stream_lines)
        client = TestClient(app)
        with MockedNet(png_handler):
            body = client.post("/api/pg/media/generate", json={"kind": "image", "model": "wanx2.1-t2i-turbo", "prompt": "a cat"}).text
        done = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: {") and '"done"' in line][0]
        self.assertRegex(done["urls"][0], r"^/api/pg/media/file/[0-9a-f]{64}\.png")
        name = done["urls"][0].split("/")[-1].split("?")[0]
        served = client.get("/api/pg/media/file/" + name)
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served.content, PNG)

    def test_telegram_stored_urls(self):
        from app import telegram_media_gen as tmg
        with MockedNet(png_handler):
            urls = run(tmg.stored_urls("image", ["https://cdn.example/tg.png"], "default"))
        self.assertTrue(self.mc.is_local(urls[0]))
        blob, mime = run(tmg.fetch_bytes(urls[0]))   # the bot / Mini App read the stored file
        self.assertEqual((blob, mime), (PNG, "image/png"))
        self.assertEqual(run(tmg.stored_urls("video", ["https://cdn.example/v.mp4"], "default")), ["https://cdn.example/v.mp4"])


class WiringTest(unittest.TestCase):
    def test_hooks_tags_spec(self):
        text = (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8")
        self.assertIn('media_cache.localize_sse(proxy_sse("/api/admin/pg/image", raw))', text)
        self.assertIn("media_cache.register(app)", text)
        self.assertEqual(text.count("media_cache.local_data_url(url) if media_cache.is_local(url)"), 3)
        self.assertIn("media_cache.localize_urls(urls, profile_id)", (ROOT / "app" / "media_routing.py").read_text(encoding="utf-8"))
        self.assertIn("media_cache.localize_urls", (ROOT / "app" / "assistant_commands.py").read_text(encoding="utf-8"))
        html = (ROOT / "app" / "pgstatic" / "index.html").read_text(encoding="utf-8")
        self.assertIn('src="/media-cache.js?v=', html)
        self.assertIn('href="/media-cache.css?v=', html)
        app_js = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        # Round 34: renderMessages hydrates only the re-rendered message nodes (or the whole box after a full rebuild)
        for hook in ("MemuMediaCache.expiredHtml(i, u)", "(fresh || [box]).forEach((node) => MemuMediaCache.hydrate(node))", "MemuMediaCache.forModel(imgList, m)"):
            self.assertIn(hook, app_js)
        for spec in ("MemuAI.spec", "MemuAIProxy.spec"):
            self.assertIn("'app.media_cache'", (ROOT / spec).read_text(encoding="utf-8"))
        self.assertEqual((ROOT / "build.py").read_text(encoding="utf-8").count('"app.media_cache"'), 3)

    def test_docs(self):
        ru = (ROOT / "app" / "docs" / "ru" / "media.md").read_text(encoding="utf-8")
        en = (ROOT / "app" / "docs" / "en" / "media.md").read_text(encoding="utf-8")
        self.assertIn("Изображение истекло", ru)
        self.assertIn("Image expired", en)

    def test_english_strings_have_no_cyrillic(self):
        import re
        js = (ROOT / "app" / "pgstatic" / "media-cache.js").read_text(encoding="utf-8")
        for ru, en in re.findall(r't\("([^"]*)",\s*"([^"]*)"\)', js):
            self.assertRegex(ru, "[А-Яа-я]")
            self.assertNotRegex(en, "[А-Яа-яЁё]")


@unittest.skipUnless(NODE, "node is not installed")
class FrontTest(unittest.TestCase):
    def test_media_cache_js(self):
        harness = r"""
const fs = require("fs"), vm = require("vm");
const posted = [];
const replies = {"https://cdn.example/ok.png": {url: "/api/pg/media/file/" + "b".repeat(64) + ".png?profile_id=default"},
                 "https://cdn.example/dead.png": {expired: true}, "https://cdn.example/down.png": {}};
let renders = 0, saves = 0, again = [];
const S = { currentId: "c1", imageModels: ["wanx2.1-t2i-turbo"], messages: [
  { role: "user", content: "a cat" },
  { role: "assistant", images: ["https://cdn.example/ok.png", "https://cdn.example/dead.png", "https://cdn.example/down.png", "https://cdn.example/blocked.png"],
    blockedImages: ["https://cdn.example/blocked.png"], media: { kind: "image", history: [{ urls: ["https://cdn.example/ok.png"] }] } },
  { role: "assistant", streaming: true, images: ["https://cdn.example/live.png"] },
]};
const ctx = { window: {}, document: { documentElement: { lang: "en" } }, PG_LANG: "en", S,
  icon: (n) => "<i>" + n + "</i>", renderMessages: () => renders++, persistChat: () => saves++, setTimeout,
  generationJobs: new Map(), MemuMedia: { again: (i) => again.push(i) },
  FileReader: class { readAsDataURL(b) { this.result = "data:image/png;base64,QQ=="; this.onload(); } },
  fetch: async (url, opts) => {
    if (url === "/api/pg/media/cache") { const u = JSON.parse(opts.body).url; posted.push(u); return { json: async () => replies[u] || {} }; }
    return { ok: true, blob: async () => ({}) };
  } };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), ctx);
const MC = ctx.window.MemuMediaCache;
(async () => {
  MC.scan(); MC.scan();
  await new Promise((r) => setTimeout(r, 30));
  const m = S.messages[1];
  const html = MC.expiredHtml(1, "https://cdn.example/dead.png");
  const model = await MC.forModel(m.images, m);
  const before = JSON.parse(JSON.stringify({ images: m.images, expired: m.expiredImages }));
  MC.regenerate(1);
  console.log(JSON.stringify({ posted, images: before.images, history: m.media.history[0].urls, expired: before.expired, renders, saves,
    html, model, again, after: m.images, afterExpired: m.expiredImages }));
})();
"""
        res = subprocess.run([NODE, "-e", harness, str(ROOT / "app" / "pgstatic" / "media-cache.js")],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout)
        local = "/api/pg/media/file/" + "b" * 64 + ".png?profile_id=default"
        # one request per link; never for a picture not yet allowed, never while the answer streams
        self.assertEqual(sorted(out["posted"]), ["https://cdn.example/dead.png", "https://cdn.example/down.png", "https://cdn.example/ok.png"])
        self.assertEqual(out["images"][0], local)
        self.assertEqual(out["history"], [local])
        self.assertEqual(out["expired"], ["https://cdn.example/dead.png"])
        self.assertIn("https://cdn.example/down.png", out["images"])   # transient trouble: left for next time
        self.assertGreaterEqual(out["saves"], 2)
        self.assertIn("Image expired", out["html"])
        self.assertIn('data-mc-regen="1"', out["html"])
        self.assertNotRegex(out["html"], "[А-Яа-я]")
        self.assertEqual(out["model"][0], "data:image/png;base64,QQ==")   # stored file → data: for the model
        self.assertNotIn("https://cdn.example/dead.png", out["model"])
        self.assertEqual(out["again"], [1])
        self.assertNotIn("https://cdn.example/dead.png", out["after"])
        self.assertEqual(out["afterExpired"], [])


@unittest.skipUnless(os.environ.get("REFORM_BASE") and os.environ.get("REFORM_KEY"), "REFORM_BASE/REFORM_KEY not set")
class LiveReformTest(unittest.TestCase):
    def test_wanx_link_is_cached(self):
        base = os.environ["REFORM_BASE"].rstrip("/")
        resp = httpx.post(base + "/images/generations", timeout=180,
                          headers={"Authorization": "Bearer " + os.environ["REFORM_KEY"],
                                   "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                          json={"model": "wanx2.1-t2i-turbo", "prompt": "a small red apple on a white table", "n": 1})
        self.assertEqual(resp.status_code, 200, resp.text[:300])
        item = (resp.json().get("data") or [{}])[0]
        url = item.get("url")
        if not url:
            self.skipTest("the model answered with b64_json, not a link")
        from app import media_cache
        stored = run(media_cache.cache_url(url))
        self.assertRegex(stored["url"], r"^/api/pg/media/file/[0-9a-f]{64}\.(png|jpg|webp)")
        self.assertGreater(stored["size"], 1000)


if __name__ == "__main__":
    unittest.main()
