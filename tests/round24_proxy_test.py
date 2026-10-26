"""Round 24 — MemuAI Proxy: own API keys (hashes, limits, 401/403/429), /v1/models filtering,
capabilities "through this API" vs "per OpenRouter", and the Cloudflare Quick Tunnel helpers."""
from __future__ import annotations

import os
import tempfile

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

import datetime  # noqa: E402
import json  # noqa: E402
import os.path  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import patch  # noqa: E402

from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import model_capabilities as mc, openai_api, public_api, public_tunnel  # noqa: E402
from tests.model_capabilities_test import _Entry, fake_fetch, reset_state  # noqa: E402

class KeysCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.file = Path(self._temp.name) / "public.json"
        self._patches = [patch.object(public_api, "_path", lambda: self.file),
                         patch.object(public_api, "catalogue", lambda: ["alpha", "beta", "gamma"])]
        for item in self._patches:
            item.start()
        public_api.store.reload()
        public_api.limiter._windows.clear()
        public_api.limiter.active = 0
        public_api.limiter.active_by_key.clear()
        public_api.store.update({"models": ["alpha", "beta"], "rpm": 0})

    def tearDown(self) -> None:
        for item in reversed(self._patches):
            item.stop()
        public_api.store.reload()
        self._temp.cleanup()

    def client(self, usage=None) -> TestClient:
        async def handler(request):
            body = json.loads(await request.body())
            self.last_body = body
            return JSONResponse({"choices": [{"message": {"content": "hi"}}],
                                 "usage": usage or {"prompt_tokens": 30, "completion_tokens": 12, "total_tokens": 42}})
        patcher = patch.object(openai_api, "chat_completions", handler)
        patcher.start()
        self.addCleanup(patcher.stop)
        return TestClient(public_api.create_public_app())

    @staticmethod
    def ask(client, key, model="alpha", headers=None, **extra):
        return client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {key}", **(headers or {})},
                           json={"model": model, "messages": [{"role": "user", "content": "hi"}], **extra})

class StorageTest(KeysCase):
    def test_only_the_hash_is_stored_and_the_key_is_returned_once(self) -> None:
        created = public_api.store.add_key({"name": "app", "rpm": 0})
        plain = created["key"]
        self.assertTrue(plain.startswith("mpk-"))
        on_disk = self.file.read_text(encoding="utf-8")
        self.assertNotIn(plain, on_disk)
        self.assertIn(public_api.hash_key(plain), on_disk)
        view = public_api.public_view()
        self.assertNotIn("key", view["keys"][0])
        self.assertNotIn("key_hash", view["keys"][0])
        self.assertEqual(view["keys"][0]["key_hint"], f"{plain[:8]}…{plain[-4:]}")
        self.assertEqual(public_api.store.find(plain)["id"], created["id"])
        self.assertIsNone(public_api.store.find(plain + "x"))
        self.assertIsNone(public_api.store.find("ключ-кириллица"), "non-ASCII keys are a 401, not a crash")

    def test_rotation_returns_a_new_key_and_kills_the_old(self) -> None:
        created = public_api.store.add_key({"rpm": 0})
        rotated = public_api.store.edit_key(created["id"], {"regenerate": True})
        self.assertNotEqual(rotated["key"], created["key"])
        self.assertGreater(rotated["rotated"], 0)
        self.assertIsNone(public_api.store.find(created["key"]))
        self.assertIsNotNone(public_api.store.find(rotated["key"]))
        self.assertNotIn("key", public_api.store.edit_key(created["id"], {"name": "x"}))

    def test_migration_of_plaintext_rows_and_the_legacy_single_key(self) -> None:
        legacy = "mpk-legacy-single-key-0123456789"
        row_key = "mpk-row-plaintext-key-0123456789abc"
        self.file.write_text(json.dumps({"enabled": False, "models": ["alpha"], "key": legacy,
                                         "keys": [{"id": "a1", "key": row_key, "name": "old", "rpm": 5}]}), encoding="utf-8")
        public_api.store.reload()
        data = public_api.store.data()
        self.assertEqual({row["id"] for row in data["keys"]}, {"a1", "legacy"})
        self.assertEqual(public_api.store.find(row_key)["name"], "old")
        self.assertEqual(public_api.store.find(legacy)["id"], "legacy")
        text = self.file.read_text(encoding="utf-8")
        self.assertNotIn(row_key, text, "plaintext leaves the disk at once")
        self.assertNotIn(legacy, text)
        public_api.store.reload()
        self.assertEqual(len(public_api.store.data()["keys"]), 2, "migration is idempotent")

    def test_normalisation_of_new_limits(self) -> None:

        self.assertEqual(public_api.normalize_ips(["10.0.0.0/8", "bad", "192.168.1.5"]), ["10.0.0.0/8", "192.168.1.5/32"])
        row = public_api.store.add_key({"ips": ["10.0.0.0/8", "192.168.1.5"], "cost_budget": "1.5",
                                        "tpmonth": -1, "max_tokens": "abc", "concurrency": 3})
        self.assertEqual(row["ips"], ["10.0.0.0/8", "192.168.1.5/32"])
        self.assertEqual((row["cost_budget"], row["tpmonth"], row["max_tokens"], row["concurrency"]), (1.5, 0, 0, 3))
        self.assertTrue(public_api.ip_allowed(["10.0.0.0/8"], "10.2.3.4"))
        self.assertFalse(public_api.ip_allowed(["10.0.0.0/8"], "11.2.3.4"))
        self.assertTrue(public_api.ip_allowed([], "anything"))
        self.assertFalse(public_api.ip_allowed(["10.0.0.0/8"], "not-an-ip"))

class EndpointTest(KeysCase):
    def test_401_and_openai_error_shape(self) -> None:
        client = self.client()
        response = client.get("/v1/models")
        self.assertEqual(response.status_code, 401)
        error = response.json()["error"]
        self.assertEqual((error["code"], error["type"]), ("invalid_api_key", "authentication_error"))
        self.assertIn("message", error)
        self.assertIn("param", error)

    def test_models_list_and_403_for_a_model_the_key_may_not_use(self) -> None:
        client = self.client()
        narrow = public_api.store.add_key({"rpm": 0, "models": ["beta"]})
        everything = public_api.store.add_key({"rpm": 0})
        ids = lambda key: [row["id"] for row in client.get("/v1/models", headers={"Authorization": f"Bearer {key}"}).json()["data"]]  # noqa: E731
        self.assertEqual(ids(narrow["key"]), ["beta"])
        self.assertEqual(ids(everything["key"]), ["alpha", "beta"])
        denied = self.ask(client, narrow["key"], "alpha")
        self.assertEqual((denied.status_code, denied.json()["error"]["code"], denied.json()["error"]["type"]),
                         (403, "model_not_allowed", "permission_error"))
        hidden = self.ask(client, narrow["key"], "gamma")
        self.assertEqual((hidden.status_code, hidden.json()["error"]["code"]), (404, "model_not_found"))
        self.assertEqual(self.ask(client, narrow["key"], "beta").status_code, 200)

    def test_403_disabled_expired_and_ip(self) -> None:
        client = self.client()

        running = patch.object(public_tunnel.tunnel, "running", lambda: True)
        running.start()
        self.addCleanup(running.stop)
        key = public_api.store.add_key({"rpm": 0, "ips": ["203.0.113.0/24"]})
        far = self.ask(client, key["key"], headers={"CF-Connecting-IP": "198.51.100.9"})
        self.assertEqual((far.status_code, far.json()["error"]["code"]), (403, "ip_not_allowed"))
        near = self.ask(client, key["key"], headers={"CF-Connecting-IP": "203.0.113.77"})
        self.assertEqual(near.status_code, 200, "behind the local tunnel the real client address is used")
        self.assertEqual(public_api.store.find(key["key"])["usage"]["last_ip"], "203.0.113.77")
        public_api.store.edit_key(key["id"], {"ips": [], "enabled": False})
        self.assertEqual(self.ask(client, key["key"]).json()["error"]["code"], "key_disabled")
        public_api.store.edit_key(key["id"], {"enabled": True, "expires": "2001-01-01"})
        expired = self.ask(client, key["key"])
        self.assertEqual((expired.status_code, expired.json()["error"]["code"]), (403, "key_expired"))

    def test_429_limits_month_cost_and_concurrency(self) -> None:
        client = self.client()
        month = public_api.store.add_key({"rpm": 0, "tpmonth": 50})
        self.assertEqual([self.ask(client, month["key"]).status_code for _ in range(3)], [200, 200, 429])
        refused = self.ask(client, month["key"])
        self.assertEqual((refused.json()["error"]["code"], refused.json()["error"]["type"]), ("tpmonth", "insufficient_quota"))
        self.assertGreaterEqual(int(refused.headers["retry-after"]), 1)
        usage = public_api.store.find(month["key"])["usage"]
        self.assertEqual((usage["month"], usage["month_tokens"], usage["input_tokens"], usage["output_tokens"]),
                         (datetime.date.today().isoformat()[:7], 84, 60, 24))

        public_api.store.find(month["key"])["usage"]["month"] = "2000-01"
        self.assertEqual(self.ask(client, month["key"]).status_code, 200)

        cost = public_api.store.add_key({"rpm": 0, "cost_budget": 0.01})
        with patch("app.pricing.estimate", lambda *a, **k: type("E", (), {"cost": 0.02})()):
            self.assertEqual(self.ask(client, cost["key"]).status_code, 200)
        self.assertAlmostEqual(public_api.store.find(cost["key"])["usage"]["cost"], 0.02)
        self.assertEqual(self.ask(client, cost["key"]).json()["error"]["code"], "cost_budget")

        parallel = public_api.store.add_key({"rpm": 0, "concurrency": 1})
        public_api.limiter.active_by_key[parallel["id"]] = 1
        busy = self.ask(client, parallel["key"])
        self.assertEqual((busy.status_code, busy.json()["error"]["code"]), (429, "key_busy"))
        public_api.limiter.active_by_key.clear()
        self.assertEqual(self.ask(client, parallel["key"]).status_code, 200)
        self.assertEqual(public_api.limiter.active_by_key, {}, "the slot is released")
        self.assertEqual(public_api.limiter.active, 0)

        minute = public_api.store.add_key({"rpm": 1})
        self.assertEqual([self.ask(client, minute["key"]).status_code for _ in range(2)], [200, 429])

    def test_max_tokens_per_key(self) -> None:
        client = self.client()
        key = public_api.store.add_key({"rpm": 0, "max_tokens": 300})
        big = self.ask(client, key["key"], max_tokens=1000)
        self.assertEqual((big.status_code, big.json()["error"]["code"]), (400, "max_tokens_exceeded"))
        self.assertEqual(self.ask(client, key["key"]).status_code, 200)
        self.assertEqual(self.last_body["max_tokens"], 300, "a missing max_tokens gets the key's cap")
        public_api.store.update({"max_output_tokens": 200})
        self.assertEqual(self.ask(client, key["key"], max_tokens=250).status_code, 400, "the smaller cap wins")

    def test_counters_survive_concurrent_requests(self) -> None:
        import threading
        key = public_api.store.add_key({"rpm": 0})
        row = public_api.store.find(key["key"])
        threads = [threading.Thread(target=lambda: [public_api.store.count_request(row) or public_api.store.count_tokens(row, 2)
                                                    for _ in range(200)]) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        usage = public_api.store.find(key["key"])["usage"]
        self.assertEqual((usage["requests"], usage["tokens"]), (1600, 3200))
        public_api.store.flush(force=True)
        public_api.store.reload()
        self.assertEqual(public_api.store.find(key["key"])["usage"]["requests"], 1600)

class CapabilitySplitTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_state()
        with mc._lock:
            mc._overrides.clear()
        try:
            os.remove(mc._path(mc.OVERRIDES_FILE))
        except OSError:
            pass
        mc.refresh_catalog(fetch=fake_fetch)

    def tearDown(self) -> None:
        with mc._lock:
            mc._overrides.clear()

    def test_openrouter_alone_never_switches_vision_on(self) -> None:
        entry = _Entry("myapi", "custom", "claude-haiku-4-5")
        card = mc.describe(entry.id, entry=entry)
        self.assertIsNone(card["effective"]["vision"], "only OpenRouter knows about vision here")
        self.assertTrue(card["reference"]["vision"]["value"])
        self.assertEqual(card["reference"]["vision"]["kind"], "openrouter-reference")
        self.assertIsNone(mc.has_vision(entry.id, entry=entry))
        self.assertIsNone(card["summary"].get("vision"), "the badge does not borrow the catalogue's yes")
        self.assertTrue(card["summary"]["vision_ref"])
        self.assertTrue(card["fields"]["vision"]["value"], "the full card still lists the catalogue value")

    def test_provider_row_decides_and_differs_from_openrouter(self) -> None:
        entry = _Entry("myapi2", "custom", "claude-haiku-4-5")
        mc.record_raw("myapi2", [{"id": "claude-haiku-4-5", "input_modalities": ["text"], "context_length": 50000}])
        card = mc.describe(entry.id, entry=entry)
        self.assertEqual((card["effective"]["vision"]["value"], card["effective"]["vision"]["kind"]), (False, "provider"))
        self.assertTrue(card["reference"]["vision"]["value"])
        self.assertEqual(card["effective"]["context"]["value"], 50000)
        self.assertFalse(mc.has_vision(entry.id, entry=entry))
        self.assertEqual(mc.effective_capabilities(entry.id, entry=entry)["context"], 50000)

    def test_user_override_and_blind_adapter(self) -> None:
        entry = _Entry("myapi3", "custom", "totally-unknown-finetune")
        self.assertIsNone(mc.has_vision(entry.id, entry=entry))
        mc.set_override(entry.id, {"vision": True, "context": "128000", "bogus": 1})
        self.assertTrue(mc.has_vision(entry.id, entry=entry))
        card = mc.describe(entry.id, entry=entry)
        self.assertEqual(card["effective"]["vision"]["kind"], "user-override")
        self.assertEqual(card["effective"]["context"]["value"], 128000)
        self.assertEqual(card["override"], {"vision": True, "context": 128000})
        mc.set_override(entry.id, {"vision": None, "context": None})
        self.assertIsNone(mc.has_vision(entry.id, entry=entry))
        self.assertEqual(mc.get_override(entry.id), {})
        grok = _Entry("grok", "grok", "grok-4")
        self.assertFalse(mc.has_vision(grok.id, entry=grok), "an adapter that cannot pass images is a no")

    def test_probe_counts_as_this_api(self) -> None:
        entry = _Entry("myapi4", "custom", "deepseek-chat")
        mc._store_probe(entry.id, {"vision": True, "verdict": "correct", "ts": 1})
        self.assertTrue(mc.has_vision(entry.id, entry=entry))
        self.assertEqual(mc.describe(entry.id, entry=entry)["effective"]["vision"]["kind"], "api-probe")
        mc.forget_probe(entry.id)

class TunnelTest(unittest.TestCase):
    SAMPLE = """2026-10-08T10:00:00Z INF Requesting new quick Tunnel on trycloudflare.com...
2026-10-08T10:00:01Z INF +--------------------------------------------------------------------------------------------+
2026-10-08T10:00:01Z INF |  Your quick Tunnel has been created! Visit it at (it may take some time to be reachable):  |
2026-10-08T10:00:01Z INF |  https://quiet-river-sample-words.trycloudflare.com                                        |
2026-10-08T10:00:01Z INF +--------------------------------------------------------------------------------------------+"""

    def test_parse_quick_url(self) -> None:
        self.assertEqual(public_tunnel.parse_quick_url(self.SAMPLE), "https://quiet-river-sample-words.trycloudflare.com")
        self.assertEqual(public_tunnel.parse_quick_url("POST https://api.trycloudflare.com/tunnel failed"), "")
        self.assertEqual(public_tunnel.parse_quick_url("nothing here"), "")
        self.assertEqual(public_tunnel.parse_quick_url("https://evil.trycloudflare.com.attacker.net"), "",
                         "only a real trycloudflare.com host")

    def test_command_and_assets(self) -> None:
        self.assertEqual(public_tunnel.build_command("cf", 8320),
                         ["cf", "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:8320"])
        self.assertIn("--no-tls-verify", public_tunnel.build_command("cf", 8320, tls=True))
        self.assertEqual(public_tunnel.build_command("cf", 1, token="T" * 30)[-2:], ["--token", "T" * 30])
        self.assertEqual(public_tunnel.asset_name("Windows", "AMD64"), "cloudflared-windows-amd64.exe")
        self.assertEqual(public_tunnel.asset_name("Linux", "aarch64"), "cloudflared-linux-arm64")
        self.assertEqual(public_tunnel.asset_name("Darwin", "arm64"), "cloudflared-darwin-arm64.tgz")

    def test_a_fake_cloudflared_is_run_and_its_address_read(self) -> None:
        temp = tempfile.mkdtemp()
        fake = Path(temp) / "cloudflared"
        fake.write_text("#!/bin/sh\necho 'INF |  https://fake-words-here.trycloudflare.com  |'\nsleep 30\n", encoding="utf-8")
        fake.chmod(0o755)
        tunnel = public_tunnel.Tunnel()
        with patch.object(public_tunnel, "find_binary", lambda: str(fake)), \
                patch.object(public_tunnel, "_conf_path", lambda: Path(temp) / "conf.json"):
            status = tunnel.start(8320)
            try:
                self.assertEqual(status["state"], "running")
                self.assertEqual(status["api_base"], "https://fake-words-here.trycloudflare.com/v1")
                self.assertFalse(status["config"]["has_token"])
            finally:
                stopped = tunnel.stop()
            self.assertEqual(stopped["state"], "stopped")
            self.assertEqual(stopped["url"], "")

            tunnel.save_config({"mode": "named", "token": "A" * 40, "hostname": "https://api.example.com/"})
            status = tunnel.status()
            self.assertEqual((status["config"]["mode"], status["config"]["hostname"], status["config"]["has_token"]),
                             ("named", "api.example.com", True))
            self.assertNotIn("A" * 40, json.dumps(status))
            with self.assertRaises(ValueError):
                tunnel.save_config({"token": "bad token with spaces"})

    def test_missing_binary_is_a_clear_error(self) -> None:
        tunnel = public_tunnel.Tunnel()
        with patch.object(public_tunnel, "find_binary", lambda: ""):
            self.assertIn("cloudflared", tunnel.start(8320)["error"])

class StaticTest(unittest.TestCase):
    def test_ui_files_parse_and_are_wired(self) -> None:
        for name in ("public.js", "model_caps.js"):
            result = subprocess.run(["node", "--check", str(ROOT / "app" / "static" / name)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        html = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="/public.css"', html)
        css = (ROOT / "app" / "static" / "model_caps.css").read_text(encoding="utf-8")
        self.assertIn("#models-table td.actions", css)
        self.assertIn("position: sticky", css)
        for name in ("public.js", "public.css"):
            text = (ROOT / "app" / "static" / name).read_text(encoding="utf-8")
            for line in text.splitlines():
                if "MemuAI" in line:
                    self.assertIn("MemuAI Proxy", line, "Proxy-only build forbids a bare product name")

class TunnelReviewTest(unittest.TestCase):
    def test_download_host_check_is_exact(self) -> None:
        ok = public_tunnel.download_host_ok
        self.assertTrue(ok("github.com"))
        self.assertTrue(ok("objects.githubusercontent.com"))
        self.assertTrue(ok("release-assets.githubusercontent.com."))
        self.assertFalse(ok("evilgithub.com"))
        self.assertFalse(ok("githubusercontent.com.attacker.net"))
        self.assertFalse(ok("notgithubusercontent.com"))

    def test_named_tunnel_token_is_not_on_the_command_line(self) -> None:
        temp = tempfile.mkdtemp()
        fake = Path(temp) / "cloudflared"
        out = Path(temp) / "seen.txt"
        fake.write_text(f"#!/bin/sh\necho \"ARGS:$*\" > {out}\necho \"ENV:$TUNNEL_TOKEN\" >> {out}\nsleep 30\n",
                        encoding="utf-8")
        fake.chmod(0o755)
        tunnel = public_tunnel.Tunnel()
        token = "T" * 40
        with patch.object(public_tunnel, "find_binary", lambda: str(fake)), \
                patch.object(public_tunnel, "_conf_path", lambda: Path(temp) / "conf.json"):
            tunnel.save_config({"mode": "named", "token": token, "hostname": "api.example.com"})
            tunnel.start(8320)
            try:
                for _ in range(40):
                    if out.exists() and "ENV:" in out.read_text():
                        break
                    import time as _t
                    _t.sleep(0.05)
                seen = out.read_text()
            finally:
                proc = tunnel.proc
                tunnel.stop()
        self.assertNotIn(token, seen.splitlines()[0])
        self.assertIn("run", seen.splitlines()[0])
        self.assertIn("ENV:" + token, seen)
        self.assertIsNotNone(proc.returncode, "the stopped process is reaped (no zombie)")

class ConcurrencyReleaseReviewTest(KeysCase):
    def test_slot_is_released_when_the_handler_fails(self) -> None:
        key = public_api.store.add_key({"rpm": 0, "concurrency": 1})

        async def broken(_request):
            raise RuntimeError("upstream exploded")

        with patch.object(openai_api, "chat_completions", broken):
            client = TestClient(public_api.create_public_app(), raise_server_exceptions=False)
            for _ in range(2):
                self.ask(client, key["key"])
        self.assertEqual(public_api.limiter.active, 0)
        self.assertEqual(public_api.limiter.active_by_key.get(key["id"], 0), 0)

    def test_forwarded_address_is_ignored_from_a_non_local_peer(self) -> None:
        request = type("R", (), {})()
        request.client = type("C", (), {"host": "198.51.100.1"})()
        request.headers = {"cf-connecting-ip": "203.0.113.7", "x-forwarded-for": "203.0.113.8"}
        self.assertEqual(public_api.client_ip(request), "198.51.100.1")
        request.client.host = "127.0.0.1"
        with patch.object(public_tunnel.tunnel, "running", lambda: True):
            self.assertEqual(public_api.client_ip(request), "203.0.113.7")

if __name__ == "__main__":
    unittest.main()
