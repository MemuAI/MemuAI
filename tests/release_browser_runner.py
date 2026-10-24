"""Real hidden Edge boundary regression; isolated test servers and profile only."""
import asyncio
import http.server
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
sys.path.insert(0, str(root))
isolated = tempfile.TemporaryDirectory(prefix="memuai-browser-boundary-")
os.environ["APPDATA"] = str(Path(isolated.name) / "appdata")
os.environ["LOCALAPPDATA"] = str(Path(isolated.name) / "localappdata")
os.environ["MEMUAI_DATA_DIR"] = str(Path(isolated.name) / "data")
from app import automation_runtime
from app.automation_runtime import EdgeAutomation, _safe_navigation_url

instances = []

class TrackedEdge(EdgeAutomation):
    def __init__(self):
        super().__init__()
        instances.append(self)

private_hits = []
allowed_hits = []
slow_started = threading.Event()
slow_release = threading.Event()

class QuietServer(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass

class Private(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        private_hits.append(self.path)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"PRIVATE_SENTINEL")
    def log_message(self, *args):
        pass

private = QuietServer(("127.0.0.2", 0), Private)
private_url = f"http://127.0.0.2:{private.server_port}"

class Allowed(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        allowed_hits.append(self.path)
        if self.path == "/slow":
            slow_started.set()
            slow_release.wait(15)
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", private_url + "/redirect-target")
            self.end_headers()
            return
        if self.path in ("/worker.js", "/sw.js"):
            content = ("fetch(" + json.dumps(private_url + "/worker-fetch") + ");").encode()
            mime = "text/javascript"
        elif self.path == "/assets":
            content = (f'''<!doctype html><title>Allowed page</title><body>LOCAL_OK
                <a href="{private_url}/click-target">Private link</a>
                <button onclick="window.open('{private_url}/popup-target','_blank')">Popup</button>
                <button onclick="new WebSocket('{private_url.replace('http:', 'ws:')}/clicked-socket')">Socket</button>
                <img src="{private_url}/image"><script src="{private_url}/script"></script>
                <iframe src="{private_url}/frame"></iframe><img src="/positive-asset">
                <script>fetch('{private_url}/fetch').catch(()=>{{}});
                try {{ new WebSocket('{private_url.replace('http:', 'ws:')}/page-socket'); }} catch(e) {{}}
                try {{ new Worker('/worker.js'); }} catch(e) {{}}
                try {{ new SharedWorker('/worker.js'); }} catch(e) {{}}
                navigator.serviceWorker.register('/sw.js').catch(()=>{{}});</script>
                </body>''').encode()
            mime = "text/html"
        else:
            content, mime = b"LOCAL_OK", "text/plain"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        if self.path == "/download":
            self.send_header("Content-Disposition", 'attachment; filename="memuai-boundary-download.txt"')
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)
    def log_message(self, *args):
        pass

allowed = QuietServer(("127.0.0.1", 0), Allowed)
allowed_url = f"http://127.0.0.1:{allowed.server_port}"
for server in (private, allowed):
    threading.Thread(target=server.serve_forever, daemon=True).start()

async def check():
    edge = TrackedEdge()
    try:
        try:
            _safe_navigation_url(private_url)
            raise AssertionError("direct private URL accepted")
        except ValueError:
            pass
        await edge.navigate(allowed_url + "/redirect")
        assert private_hits == [], ("redirect crossed boundary", private_hits)
        await edge.navigate(allowed_url + "/assets")
        state = await edge.snapshot(screenshot=False)
        assert state["title"] == "Allowed page", state
        assert "/positive-asset" in allowed_hits, allowed_hits
        assert private_hits == [], ("subresources crossed boundary", private_hits)
        # Commands run concurrently while the receiver dispatches request events.
        values = await asyncio.gather(*[
            edge.command("Runtime.evaluate", {
                "expression": "fetch('/positive-data').then(r=>r.text())",
                "awaitPromise": True, "returnByValue": True,
            }) for _ in range(4)])
        assert all(value["result"]["value"] == "LOCAL_OK" for value in values), values
        # An actual clicked link gets the same network policy as navigate().
        link = next(item for item in state["elements"] if item["tag"] == "a")
        await edge.perform({"action": "click", "element": link["id"]})
        assert private_hits == [], ("click crossed boundary", private_hits)
        await edge.navigate(allowed_url + "/assets")
        # Force a user gesture so the popup test exercises CDP, not popup blocking.
        await edge.command("Runtime.evaluate", {
            "expression": f"window.open({json.dumps(private_url + '/popup-target')},'_blank')",
            "userGesture": True,
        })
        await asyncio.sleep(.6)
        assert private_hits == [], ("popup crossed boundary", private_hits)
        targets = await edge.command("Target.getTargets")
        assert len([t for t in targets["targetInfos"] if t["type"] == "page"]) == 1, targets
        await edge.command("Runtime.evaluate", {
            "expression": "try { new Worker('/worker.js'); } catch(e) {} try { new SharedWorker('/worker.js'); } catch(e) {} navigator.serviceWorker.register('/sw.js').catch(()=>{});",
        })
        await asyncio.sleep(.6)
        assert private_hits == [], ("worker crossed boundary", private_hits)
        assert edge.socket is not None, edge.logs
        state = await edge.snapshot(screenshot=False)
        socket_button = next(item for item in state["elements"] if item["text"] == "Socket")
        await edge.perform({"action": "click", "element": socket_button["id"]})
        await asyncio.sleep(.4)
        assert private_hits == [], ("WebSocket crossed boundary", private_hits)
        state = await edge.snapshot(screenshot=False)
        assert "LOCAL_OK" in state["text"], state
        await edge.navigate(allowed_url + "/download")
        assert not list(Path(isolated.name).rglob("memuai-boundary-download.txt")), "download allowed"
        print("PASS: real Edge redirect, assets, fetch, click, popup, workers, WebSocket blocked; localhost, concurrent commands preserved")
        print(json.dumps({"allowed_requests": allowed_hits, "private_requests": private_hits, "blocked": [l["message"] for l in edge.logs if l["kind"] == "warn"]}, ensure_ascii=False))
        process, profile = edge.process, edge.profile
        await edge.socket.close()
        for _ in range(80):
            if not profile.exists():
                break
            await asyncio.sleep(.1)
        assert process.poll() is not None and not profile.exists(), "receiver loss leaked browser/profile"
        assert edge.socket is None and not edge._pending and not edge._event_tasks, "receiver loss leaked jobs"
    finally:
        await edge.close_browser()

    # Startup failure must clean the process/profile even after CDP connects.
    failed = TrackedEdge()
    captured = []
    async def fail_protection(session_id):
        captured.append((failed.process, failed.profile))
        raise RuntimeError("forced startup failure")
    failed._protect_page = fail_protection
    try:
        await failed.start()
        raise AssertionError("startup failure ignored")
    except RuntimeError as exc:
        assert "forced startup failure" in str(exc), exc
    assert captured and captured[0][0].poll() is not None and not captured[0][1].exists(), "startup failure leaked browser/profile"
    assert failed.socket is None and not failed._pending, "startup failure leaked CDP"

    # Cancel during CDP discovery, before a websocket receiver exists.
    launching = TrackedEdge()
    discovery = threading.Event()
    release_discovery = threading.Event()
    real_read = automation_runtime._read_json_url
    def delayed_read(url, timeout=1):
        if url.endswith("/json/list"):
            discovery.set()
            release_discovery.wait(10)
        return real_read(url, timeout)
    with patch.object(automation_runtime, "_read_json_url", delayed_read):
        start_task = asyncio.create_task(launching.start())
        for _ in range(100):
            if discovery.is_set():
                break
            await asyncio.sleep(.02)
        assert discovery.is_set(), "discovery not reached"
        process, profile = launching.process, launching.profile
        await launching.stop()
        release_discovery.set()
        result = await asyncio.gather(start_task, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError), result
        assert process.poll() is not None and not profile.exists(), "startup cancellation leaked browser/profile"

    # Cancel a pending main-document load and its intercepted response.
    running = TrackedEdge()
    await running.start()
    process, profile = running.process, running.profile
    navigation = asyncio.create_task(running.navigate(allowed_url + "/slow"))
    for _ in range(100):
        if slow_started.is_set():
            break
        await asyncio.sleep(.02)
    assert slow_started.is_set(), "slow navigation not reached"
    await running.stop()
    slow_release.set()
    await asyncio.gather(navigation, return_exceptions=True)
    assert process.poll() is not None and not profile.exists(), "navigation cancellation leaked browser/profile"
    assert running.socket is None and not running._pending and not running._event_tasks, "navigation cancellation leaked CDP"
    # Interrupt the caller after cleanup has taken ownership of the profile;
    # cleanup must finish and a second caller must await that same work.
    closing = TrackedEdge()
    await closing.start()
    process, profile = closing.process, closing.profile
    deleting = threading.Event()
    release_delete = threading.Event()
    real_remove = automation_runtime.shutil.rmtree
    def delayed_remove(*args, **kwargs):
        if Path(args[0]) == profile:
            deleting.set()
            release_delete.wait(10)
        return real_remove(*args, **kwargs)
    with patch.object(automation_runtime.shutil, "rmtree", delayed_remove):
        cleanup = asyncio.create_task(closing.close_browser())
        for _ in range(100):
            if deleting.is_set():
                break
            await asyncio.sleep(.02)
        assert deleting.is_set(), "cleanup not reached"
        cleanup.cancel()
        result = await asyncio.gather(cleanup, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError), result
        release_delete.set()
        await closing.close_browser()
    assert process.poll() is not None and not profile.exists(), "cancelled cleanup leaked browser/profile"
    print("PASS: download deny, receiver-loss cleanup, forced startup failure, startup/navigation cancellation, cancelled cleanup")

async def suite():
    try:
        await check()
    finally:
        slow_release.set()
        await asyncio.gather(*(item.close_browser() for item in instances), return_exceptions=True)

try:
    asyncio.run(suite())
finally:
    for server in (allowed, private):
        server.shutdown()
        server.server_close()
    isolated.cleanup()
