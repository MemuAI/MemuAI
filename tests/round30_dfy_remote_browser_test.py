"""Round 30 — DFY's browser on a remote machine (items 1a + 1b + 1c).

A real agent (``app/dfy_agent.py serve``, a COPY in a temporary folder) starts headless Chromium
"on the machine"; MemuAI drives it through the agent's signed CDP tunnel
(``app/dfy_remote_browser.py``): the normal DFY browser loop, the takeover, the machine's proxy.
``site.test.example`` exists only for the machine's browser (host-resolver rule / the proxy),
so a page that opens proves the traffic went the machine's way. No network beyond 127.0.0.1.
"""
import asyncio
import http.server
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from app.dfy_vault import SecretVault
from tests.round30_dfy_remote_test import ScriptedModel, start_agent

CHROMIUM = os.environ.get("MEMU_TEST_CHROMIUM", "/workspace/work/chromium/chrome")

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Машина</title></head><body style="margin:0;font:16px sans-serif">
<h1 style="margin:8px">Привет с машины</h1>
<form onsubmit="event.preventDefault();o.textContent='sent:'+q.value">
<input id="q" aria-label="Запрос" style="position:absolute;left:20px;top:60px;width:300px;height:30px">
<button id="b" style="position:absolute;left:20px;top:110px;width:120px;height:30px">Найти</button></form>
<p id="o" style="position:absolute;left:20px;top:160px"></p></body></html>"""


class _Site(http.server.BaseHTTPRequestHandler):
    """Both the site and a plain HTTP proxy: a proxied request carries the absolute URL."""
    seen = []

    def do_GET(self):  # noqa: N802
        self.seen.append((self.path, self.headers.get("Host")))
        body = PAGE.encode("utf-8") if "favicon" not in self.path else b""
        self.send_response(200 if body else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


@unittest.skipUnless(Path(CHROMIUM).exists(), "Chromium for the machine's browser")
class RemoteBrowserTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="r30-rb-")
        self.root = Path(self.tmp.name)
        _Site.seen = []
        self.site = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Site)
        threading.Thread(target=self.site.serve_forever, daemon=True).start()
        self.prefs = {"dfy_enabled": True, "dfy_model": "any/model", "dfy_max_steps": 12}
        self._env = {k: os.environ.get(k) for k in ("MEMUAI_BROWSER_EXECUTABLE", "MEMUAI_BROWSER_ARGS")}

    def tearDown(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.site.shutdown()
        self.tmp.cleanup()

    def agent(self, *, resolve_site: bool):
        # The agent inherits these: its Chromium, flags for a container, and the machine-only DNS name.
        os.environ["MEMUAI_BROWSER_EXECUTABLE"] = CHROMIUM
        rules = f" '--host-resolver-rules=MAP site.test.example 127.0.0.1:{self.site.server_port}'" if resolve_site else ""
        # --no-proxy-server: Chromium on Linux would otherwise follow HTTP(S)_PROXY of the test machine
        os.environ["MEMUAI_BROWSER_ARGS"] = "--no-sandbox" + (" --no-proxy-server" + rules if resolve_site else "")
        process, code, _script = start_agent(self.root)
        self.addCleanup(self._stop, process)
        return code

    @staticmethod
    def _stop(process):
        process.terminate()
        try:
            process.wait(10)
        except Exception:
            process.kill()
        process.stdout.close()

    def manager(self, model):
        from app.dfy import DFYManager
        # The local browser must never be used for a «<machine>/browser» task.
        return DFYManager(model, lambda: dict(self.prefs), data_root=self.root / "memu",
                          vision_lookup=lambda _m: True, browser_factory=lambda _v: self.fail("local browser used"),
                          vault=SecretVault(self.root / "memu", use_dpapi=False))

    async def wait(self, task, statuses, timeout=60.0):
        deadline = time.monotonic() + timeout
        while task.status not in statuses:
            if time.monotonic() > deadline:
                self.fail(f"status {task.status} never became {statuses}; steps={task.steps}; error={task.error}")
            await asyncio.sleep(0.05)

    async def test_browser_loop_runs_on_the_machine_and_takeover_works(self):
        code = self.agent(resolve_site=True)
        seen_text = []
        took = {}

        def look(messages):
            seen_text.append(json.dumps(messages, ensure_ascii=False))
            return {"action": "type", "element": self.element_id(messages, "Запрос"), "text": "кофе", "submit": True}

        def after(messages):
            seen_text.append(json.dumps(messages, ensure_ascii=False))
            return {"action": "done", "result": "ok"}

        model = ScriptedModel([{"action": "navigate", "url": "http://site.test.example/"}, look, after])
        mgr = self.manager(model)
        machine = mgr.machines.store.add({"code": code, "name": "box"})
        targets = mgr.machines.targets("ru")
        self.assertIn({"id": machine["id"] + "/browser", "browser": True, "name": "box — браузер"},
                      [{k: t[k] for k in ("id", "browser", "name")} for t in targets if t.get("browser")])

        original = mgr._loop

        async def loop_with_takeover(task, browser):
            # The user takes over the machine's browser before the agent starts: frames come through the tunnel.
            from app.dfy_remote_browser import RemoteDFYBrowser
            took["remote"] = isinstance(browser, RemoteDFYBrowser)
            session = await mgr.takeover_start(task.id)
            await session.attach_viewer()
            took["frame"] = bool(session.frame)
            await mgr.takeover_end(task.id)
            return await original(task, browser)
        mgr._loop = loop_with_takeover

        task = await mgr.create("найди кофе на сайте", target=machine["id"] + "/browser")
        await self.wait(task, ("done", "failed"))
        self.assertEqual(task.status, "done", task.error)
        self.assertTrue(took.get("remote"), "the task drives RemoteDFYBrowser")
        self.assertTrue(took.get("frame"), "the takeover got a frame of the machine's browser")
        self.assertIn("Привет с машины", seen_text[0], "the model saw the page opened on the machine")
        self.assertIn("sent:кофе", seen_text[1], "typing and Enter reached the page on the machine")
        self.assertTrue(any(path == "/" for path, _host in _Site.seen))
        public = task.public()
        self.assertTrue(public["target"]["browser"])
        self.assertIsNone(task.browser, "the machine's browser is closed after the task")

    @staticmethod
    def element_id(messages, label):
        text = json.dumps(messages, ensure_ascii=False)
        import re
        match = re.search(r'\[(\d+)\][^\[]*' + label, text) or re.search(r'"id":\s*(\d+)[^}]*' + label, text)
        return int(match.group(1)) if match else 1

    async def test_machine_proxy_without_login_carries_the_browser(self):
        code = self.agent(resolve_site=False)
        mgr = self.manager(ScriptedModel([{"action": "navigate", "url": "http://site.test.example/"},
                                          {"action": "done", "result": "ok"}]))
        machine = mgr.machines.store.add({"code": code, "name": "box"})
        mgr.proxy.set(machine["id"], {"scheme": "http", "host": "127.0.0.1", "port": self.site.server_port, "enabled": True})
        task = await mgr.create("открой сайт", target=machine["id"] + "/browser")
        await self.wait(task, ("done", "failed"))
        self.assertEqual(task.status, "done", task.error)
        self.assertIn(("http://site.test.example/", "site.test.example"), _Site.seen,
                      "the machine's browser asked the proxy for the name (no local DNS)")

    async def test_proxy_with_login_and_ssh_are_refused_clearly(self):
        code = self.agent(resolve_site=False)
        mgr = self.manager(ScriptedModel([]))
        machine = mgr.machines.store.add({"code": code, "name": "box"})
        mgr.proxy.set(machine["id"], {"scheme": "socks5", "host": "127.0.0.1", "port": 1080,
                                      "username": "u", "password": "p", "enabled": True})
        task = await mgr.create("открой сайт", target=machine["id"] + "/browser")
        await self.wait(task, ("done", "failed"))
        self.assertEqual(task.status, "failed")
        self.assertIn("без логина", task.error)
        ssh = mgr.machines.store.add({"kind": "ssh", "host": "example.invalid", "user": "root", "name": "ssh-box"})
        with self.assertRaises(ValueError):
            await mgr.create("открой сайт", target=ssh["id"] + "/browser")


class MachineProxyApiTests(unittest.TestCase):
    """Settings → DFY → machine → «Proxy» (dfy-remote.js) talks to /api/dfy/proxy?machine=ID (dfy_api.py)."""

    def test_machine_proxy_round_trip(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.dfy import DFYManager
        from app.dfy_api import build_router
        from app.dfy_remote_api import build_router as build_remote
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mgr = DFYManager(ScriptedModel([]), lambda: {"dfy_enabled": True}, data_root=root / "memu",
                             vision_lookup=lambda _m: True, browser_factory=lambda _v: None,
                             vault=SecretVault(root / "memu", use_dpapi=False))
            app = FastAPI()
            app.include_router(build_router(mgr))
            app.include_router(build_remote(mgr))
            client = TestClient(app)
            row = mgr.machines.store.add({"kind": "http", "host": "10.0.0.5", "port": 8765, "token": "t" * 32, "name": "srv"})
            mid = row["id"]
            self.assertEqual(client.put("/api/dfy/proxy?machine=nope", json={"host": "p.example", "port": 1}).status_code, 404)
            saved = client.put(f"/api/dfy/proxy?machine={mid}", json={"scheme": "socks5", "host": "p.example", "port": 1080,
                                                                       "username": "u", "password": "hunter2", "enabled": True}).json()
            self.assertTrue(saved["ok"] and saved["enabled"] and saved["auth"])
            got = client.get(f"/api/dfy/proxy?machine={mid}").json()
            self.assertEqual((got["host"], got["port"], got["scheme"]), ("p.example", 1080, "socks5"))
            self.assertNotIn("hunter2", json.dumps(got))
            self.assertNotIn("status", got, "the local relay state is not the machine's")
            self.assertFalse(client.get("/api/dfy/proxy").json()["enabled"], "the local proxy is separate")
            self.assertEqual(mgr.proxy.upstream(mid).password, "hunter2")
            self.assertTrue(client.delete(f"/api/dfy/remote/machines/{mid}").json().get("ok"))
            self.assertIsNone(mgr.proxy.upstream(mid), "the proxy goes away with the machine")


class RouteTests(unittest.TestCase):
    def test_takeover_buttons_follow_browser_targets(self):
        root = Path(__file__).resolve().parents[1]
        client = (root / "app/pgstatic/dfy-client.js").read_text("utf-8")
        self.assertIn("const onMachine = (task) => !!(task.target && !task.target.browser);", client)
        self.assertIn('"target":"<id>/browser"', client)
        quick = (root / "app/pgstatic/quick/quick-dfy.js").read_text("utf-8")
        self.assertIn("!(task.target && !task.target.browser)", quick)
        remote = (root / "app/pgstatic/dfy-remote.js").read_text("utf-8")
        self.assertIn('T("браузер", "browser")', remote)


if __name__ == "__main__":
    unittest.main()
