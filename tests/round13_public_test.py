"""Round 13: the public endpoint — a separate listener with its own keys, limits and models."""
from __future__ import annotations

import os
import tempfile

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

import asyncio  # noqa: E402
import datetime  # noqa: E402
import json  # noqa: E402
import socket  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import patch  # noqa: E402

import httpx  # noqa: E402
from fastapi.responses import JSONResponse, StreamingResponse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import openai_api, public_api  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


class PublicCase(unittest.TestCase):
    """A fresh store in a temp folder and a catalogue of three enabled models."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self._patches = [
            patch.object(public_api, "_path", lambda: Path(self._temp.name) / "public.json"),
            patch.object(public_api, "catalogue", lambda: ["alpha", "beta", "gamma"]),
        ]
        for item in self._patches:
            item.start()
        public_api.store.reload()
        public_api.limiter._windows.clear()
        public_api.limiter.active = 0
        self.calls: list = []

    def tearDown(self) -> None:
        for item in reversed(self._patches):
            item.stop()
        public_api.store.reload()
        self._temp.cleanup()

    def key(self, **fields) -> dict:
        return public_api.store.add_key({"name": "friend", **fields})

    def client(self, answer=None) -> TestClient:
        async def handler(request):
            body = json.loads(await request.body())
            self.calls.append({"model": body["model"], "marked": getattr(request.state, "public_key", "")})
            return answer(body) if answer else JSONResponse({"choices": [{"message": {"content": "hi"}}],
                                                             "usage": {"prompt_tokens": 30, "completion_tokens": 12, "total_tokens": 42}})
        self._handler = patch.object(openai_api, "chat_completions", handler)
        self._handler.start()
        self.addCleanup(self._handler.stop)
        return TestClient(public_api.create_public_app())

    def ask(self, client, key, model="alpha", **extra):
        return client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {key['key']}"},
                           json={"model": model, "messages": [{"role": "user", "content": "hi"}], **extra})


class PublicEndpointTest(PublicCase):
    def test_only_the_model_api_is_served(self) -> None:
        public_api.store.update({"models": ["alpha"]})
        client = self.client()
        self.assertEqual(client.get("/health").json(), {"status": "ok"})
        for path in ("/", "/index.html", "/app.js", "/api/admin/settings", "/api/admin/accounts", "/p/x/y", "/v1/embeddings"):
            response = client.get(path)
            self.assertEqual(response.status_code, 404, path)
            self.assertEqual(response.json()["error"]["code"], "not_found")
        self.assertEqual(client.post("/api/admin/login", json={"password": "admin"}).status_code, 404)

    def test_a_key_is_required_and_sees_only_the_offered_models(self) -> None:
        public_api.store.update({"models": ["alpha", "beta", "retired-model"]})
        key = self.key()
        client = self.client()
        self.assertEqual(client.get("/v1/models").status_code, 401)
        self.assertEqual(client.get("/v1/models", headers={"Authorization": "Bearer mpk-wrong-wrong-wrong-wrong"}).status_code, 401)
        listed = client.get("/v1/models", headers={"x-api-key": key["key"]}).json()
        self.assertEqual([row["id"] for row in listed["data"]], ["alpha", "beta"], "a model that left the catalogue is not offered")
        self.assertTrue(key["key"].startswith("mpk-"))
        self.assertEqual(self.ask(client, key, "alpha").status_code, 200)
        self.assertEqual(self.calls[-1], {"model": "alpha", "marked": key["id"]})
        hidden = self.ask(client, key, "gamma")
        self.assertEqual((hidden.status_code, hidden.json()["error"]["code"]), (404, "model_not_found"))
        self.assertEqual(len(self.calls), 1, "a model outside the list never reaches the pipeline")
        narrow = self.key(models=["beta"])
        self.assertEqual([row["id"] for row in client.get("/v1/models", headers={"x-api-key": narrow["key"]}).json()["data"]], ["beta"])
        self.assertEqual(self.ask(client, narrow, "alpha").status_code, 403, "Round 24: offered by the endpoint, not allowed for this key")

    def test_usage_is_counted_for_plain_and_streamed_answers(self) -> None:
        public_api.store.update({"models": ["alpha"]})
        key = self.key()
        client = self.client()
        self.ask(client, key)
        usage = public_api.store.find(key["key"])["usage"]
        self.assertEqual((usage["requests"], usage["tokens"], usage["day_requests"], usage["day_tokens"]), (1, 42, 1, 42))
        self.assertEqual(usage["day"], datetime.date.today().isoformat())

        def stream(_body):
            async def events():
                yield b'data: {"choices":[{"delta":{"content":"hel"}}]}\n\n'
                yield b'data: {"choices":[{"delta":{}}],"usage":{"prompt_tokens":100,"completion_tokens":58,"total_tokens":158}}\n\n'
                yield b"data: [DONE]\n\n"
            return StreamingResponse(events(), media_type="text/event-stream")

        streamed = self.client(stream)
        body = self.ask(streamed, key, stream=True).text
        self.assertIn("[DONE]", body)
        usage = public_api.store.find(key["key"])["usage"]
        self.assertEqual((usage["requests"], usage["tokens"]), (2, 200))
        self.assertEqual(public_api.limiter.active, 0, "the slot is released when the stream ends")
        self.assertEqual(public_api.tokens_in(b'{"usage":{"input_tokens":70,"output_tokens":9}}'), 79, "the Anthropic shape")
        public_api.store.flush(force=True)
        public_api.store.reload()
        self.assertEqual(public_api.store.find(key["key"])["usage"]["tokens"], 200, "counters survive a restart")

    def test_limits_of_a_key(self) -> None:
        public_api.store.update({"models": ["alpha"], "rpm": 0})
        client = self.client()
        minute = self.key(rpm=2)
        self.assertEqual([self.ask(client, minute).status_code for _ in range(3)], [200, 200, 429])
        refused = self.ask(client, minute)
        self.assertEqual(refused.json()["error"]["code"], "rpm")
        self.assertGreaterEqual(int(refused.headers["retry-after"]), 1)
        daily = self.key(rpm=0, rpd=2)
        self.assertEqual([self.ask(client, daily).status_code for _ in range(3)], [200, 200, 429])
        self.assertEqual(self.ask(client, daily).json()["error"]["code"], "rpd")
        tokens = self.key(rpm=0, tpd=50)
        self.assertEqual([self.ask(client, tokens).status_code for _ in range(3)], [200, 200, 429],
                         "42 tokens are under 50, 84 are over")
        budget = self.key(rpm=0, budget=40)
        self.assertEqual([self.ask(client, budget).status_code for _ in range(2)], [200, 429])
        self.assertEqual(self.ask(client, budget).json()["error"]["code"], "budget")
        self.assertEqual(public_api.store.find(budget["key"])["usage"]["refused"], 2)
        # A new day clears the daily counters, not the total budget.
        row = public_api.store.find(daily["key"])
        row["usage"]["day"] = "2000-01-01"
        self.assertEqual(self.ask(client, daily).status_code, 200)
        self.assertEqual(self.ask(client, budget).status_code, 429)

    def test_disabled_expired_and_edited_keys(self) -> None:
        public_api.store.update({"models": ["alpha"]})
        client = self.client()
        key = self.key()
        public_api.store.edit_key(key["id"], {"enabled": False})
        self.assertEqual(self.ask(client, key).json()["error"]["code"], "key_disabled")
        public_api.store.edit_key(key["id"], {"enabled": True, "expires": "2001-01-01"})
        self.assertEqual(self.ask(client, key).json()["error"]["code"], "key_expired")
        tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        public_api.store.edit_key(key["id"], {"expires": tomorrow, "name": "renamed"})
        self.assertEqual(self.ask(client, key).status_code, 200)
        fresh = public_api.store.edit_key(key["id"], {"regenerate": True, "reset_usage": True})
        self.assertNotEqual(fresh["key"], key["key"])
        self.assertEqual((fresh["name"], fresh["usage"]["requests"]), ("renamed", 0))
        self.assertEqual(self.ask(client, key).status_code, 401, "the old key stops working at once")
        self.assertEqual(self.ask(client, fresh).status_code, 200)
        public_api.store.delete_key(key["id"])
        self.assertEqual(self.ask(client, fresh).status_code, 401)
        with self.assertRaises(ValueError):
            public_api.store.edit_key("missing", {})

    def test_limits_of_the_endpoint_as_a_whole(self) -> None:
        public_api.store.update({"models": ["alpha"], "rpm": 3, "max_output_tokens": 500})
        client = self.client()
        first, second = self.key(rpm=0), self.key(rpm=0)
        codes = [self.ask(client, first).status_code, self.ask(client, second).status_code,
                 self.ask(client, first).status_code, self.ask(client, second).status_code]
        self.assertEqual(codes, [200, 200, 200, 429])
        self.assertEqual(self.ask(client, second).json()["error"]["code"], "endpoint_rpm")
        public_api.limiter._windows.clear()
        big = self.ask(client, first, max_tokens=9000)
        self.assertEqual((big.status_code, big.json()["error"]["code"]), (400, "max_tokens_exceeded"))
        self.assertEqual(self.ask(client, first, max_tokens=400).status_code, 200)
        public_api.store.update({"concurrency": 1})
        public_api.limiter.active = 1
        self.assertEqual(self.ask(client, first).json()["error"]["code"], "busy")

    def test_settings_are_normalised_and_keys_are_never_replaced_by_a_settings_save(self) -> None:
        key = self.key(rpm="abc", rpd=-5, expires="tomorrow")
        self.assertEqual((key["rpm"], key["rpd"], key["expires"]), (20, 0, ""))
        saved = public_api.store.update({"port": 999999, "host": "bad host!", "address": "https://api.example.com/", "keys": [],
                                         "models": ["alpha", "alpha", ""], "enabled": "yes"})
        self.assertEqual((saved["port"], saved["host"], saved["address"], saved["models"], saved["enabled"]),
                         (65535, "0.0.0.0", "api.example.com", ["alpha"], False))
        self.assertEqual(len(saved["keys"]), 1, "keys are managed by their own calls")
        view = public_api.public_view()
        self.assertEqual(view["keys"][0]["id"], key["id"])
        self.assertIn({"kind": "address", "url": "http://api.example.com:65535/v1"}, view["links"])
        self.assertFalse(view["status"]["running"])
        self.assertEqual(view["catalogue"], ["alpha", "beta", "gamma"])

    def test_the_local_api_cannot_be_entered_with_the_public_marker(self) -> None:
        source = (ROOT / "app" / "openai_api.py").read_text(encoding="utf-8")
        self.assertIn('getattr(request.state, "public_key", "")', source)
        request = SimpleNamespace(state=SimpleNamespace(), headers={"x-public-key": "1", "public_key": "1"})
        with patch.object(openai_api.config_store, "authorize_api_request", lambda *a, **k: False):
            self.assertEqual(openai_api._check_auth(request).status_code, 401, "headers cannot set the marker")
            request.state.public_key = "abc"
            self.assertIsNone(openai_api._check_auth(request))


class PublicListenerTest(PublicCase):
    def test_the_listener_follows_the_settings(self) -> None:
        async def go():
            listener = public_api.Listener()
            port = free_port()
            public_api.store.update({"host": "127.0.0.1", "port": port, "models": ["alpha"], "enabled": False})
            self.assertFalse((await listener.apply())["running"])
            public_api.store.update({"enabled": True})
            status = await listener.apply()
            self.assertEqual((status["running"], status["listening"]["port"], status["error"]), (True, port, ""))
            async with httpx.AsyncClient(trust_env=False) as client:
                self.assertEqual((await client.get(f"http://127.0.0.1:{port}/health")).json(), {"status": "ok"})
                self.assertEqual((await client.get(f"http://127.0.0.1:{port}/api/admin/settings")).status_code, 404)
                self.assertEqual((await client.get(f"http://127.0.0.1:{port}/v1/models")).status_code, 401)
                # A new port: the old one closes, the new one answers.
                other = free_port()
                public_api.store.update({"port": other})
                self.assertEqual((await listener.apply())["listening"]["port"], other)
                self.assertEqual((await client.get(f"http://127.0.0.1:{other}/health")).status_code, 200)
                with self.assertRaises(httpx.HTTPError):
                    await client.get(f"http://127.0.0.1:{port}/health", timeout=2)
                # A busy port is reported, not silently ignored.
                taken = socket.socket()
                taken.bind(("127.0.0.1", 0))
                taken.listen(1)
                public_api.store.update({"port": taken.getsockname()[1]})
                busy = await listener.apply()
                taken.close()
                self.assertFalse(busy["running"])
                self.assertIn("занят", busy["error"])
                public_api.store.update({"port": other, "enabled": False})
                self.assertFalse((await listener.apply())["running"])
                with self.assertRaises(httpx.HTTPError):
                    await client.get(f"http://127.0.0.1:{other}/health", timeout=2)

        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
