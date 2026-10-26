"""Runtime regressions for public API accounting, limits and request framing."""
from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from fastapi.responses import StreamingResponse
from starlette.requests import Request

from app import anthropic_api, openai_api, public_api, responses_api
from tests.round13_public_test import PublicCase

class PublicUsageRegressionTest(PublicCase):
    def test_plain_dict_usage_enforces_budget_in_all_protocols(self):
        public_api.store.update({"models": ["alpha"], "rpm": 0})
        for module, handler, path, usage in (
                (openai_api, "chat_completions", "/v1/chat/completions", {"total_tokens": 42}),
                (responses_api, "responses", "/v1/responses", {"input_tokens": 30, "output_tokens": 12}),
                (anthropic_api, "messages", "/v1/messages", {"input_tokens": 30, "output_tokens": 12})):
            with self.subTest(path=path):
                key = self.key(rpm=0, budget=40)

                async def answer(_request):
                    return {"usage": usage, "content": "ok"}

                with patch.object(module, handler, answer):
                    from fastapi.testclient import TestClient
                    client = TestClient(public_api.create_public_app())
                    headers = {"Authorization": "Bearer " + key["key"]}
                    self.assertEqual(client.post(path, headers=headers, json={"model": "alpha"}).status_code, 200)
                    refused = client.post(path, headers=headers, json={"model": "alpha"})
                    self.assertEqual(refused.status_code, 429)
                    self.assertEqual(refused.json()["error"]["code"], "budget")
                    self.assertEqual(public_api.store.find(key["key"])["usage"]["tokens"], 42)

    def test_disk_flush_and_settings_edits_preserve_inflight_accounting(self):
        key = self.key(rpm=0)
        first = public_api.store.find(key["key"])
        second = public_api.store.find(key["key"])
        public_api.store.count_tokens(first, 100)
        public_api.store.flush(force=True)
        public_api.store.update({"rpm": 0})
        public_api.store.edit_key(key["id"], {"name": "renamed"})
        public_api.store.count_tokens(second, 200)
        public_api.store.flush(force=True)
        public_api.store.reload()
        stored = public_api.store.find(key["key"])
        self.assertEqual(stored["usage"]["tokens"], 300)
        self.assertEqual(stored["name"], "renamed")

    def test_large_split_stream_counts_first_and_last_usage_events(self):
        public_api.store.update({"models": ["alpha"]})
        key = self.key(rpm=0)

        def stream(_body):
            async def events():
                wire = (
                    'event: message_start\r\ndata: {"message":{"usage":{"input_tokens":1000,"output_tokens":0}}}\r\n\r\n'
                    + 'data: ' + json.dumps({"delta": {"text": "x" * 9000 + '\n"total_tokens":999999'}}) + '\n\n'
                    + 'event: message_delta\ndata: {"usage":{"output_tokens":25}}\n\n'
                ).encode()
                for offset in range(0, len(wire), 13):
                    yield wire[offset:offset + 13]
            return StreamingResponse(events(), media_type="text/event-stream")

        self.assertEqual(self.ask(self.client(stream), key, stream=True).status_code, 200)
        self.assertEqual(public_api.store.find(key["key"])["usage"]["tokens"], 1025)
        self.assertEqual(public_api.limiter.active, 0)

    def test_assistant_content_cannot_supply_accounting_values(self):
        self.assertEqual(public_api.tokens_in(json.dumps({
            "choices": [{"message": {"content": '"total_tokens":999999'}}],
            "usage": {"prompt_tokens": 30, "completion_tokens": 12}
        }).encode()), 42)
        self.assertEqual(public_api.tokens_in(b'data: {"delta":{"total_tokens":999999}}\n\n'), 0)

    def test_omitted_output_limit_is_applied_to_each_protocol(self):
        public_api.store.update({"models": ["alpha"], "max_output_tokens": 10})
        key = self.key(rpm=0)
        for module, handler, path, field in (
                (openai_api, "chat_completions", "/v1/chat/completions", "max_tokens"),
                (responses_api, "responses", "/v1/responses", "max_output_tokens"),
                (anthropic_api, "messages", "/v1/messages", "max_tokens")):
            with self.subTest(path=path):
                seen = []

                async def answer(request):
                    seen.append(await request.json())
                    self.assertEqual(json.loads(await request.body()), seen[-1])
                    return {"usage": {"total_tokens": 1}}

                from fastapi.testclient import TestClient
                with patch.object(module, handler, answer):
                    client = TestClient(public_api.create_public_app())
                    headers = {"Authorization": "Bearer " + key["key"]}
                    self.assertEqual(client.post(path, headers=headers, json={"model": "alpha"}).status_code, 200)
                    self.assertEqual(seen[-1][field], 10)
                    self.assertEqual(client.post(path, headers=headers, json={"model": "alpha", field: 4}).status_code, 200)
                    self.assertEqual(seen[-1][field], 4)
                    for invalid in (0, -1, "50", True, 1.5):
                        self.assertEqual(client.post(path, headers=headers, json={"model": "alpha", field: invalid}).status_code, 400)

    def test_chunked_body_limit_is_enforced_before_handler(self):
        public_api.store.update({"models": ["alpha"]})
        key = self.key(rpm=0)
        client = self.client()
        payload = json.dumps({"model": "alpha", "messages": [{"role": "user", "content": "x" * 300}]}).encode()
        with patch.object(public_api, "MAX_BODY_BYTES", 200):
            response = client.post("/v1/chat/completions", headers={"Authorization": "Bearer " + key["key"]},
                                   content=iter([payload[:100], payload[100:]]))
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.calls, [])
        self.assertEqual(public_api.limiter.active, 0)

    def test_cancelled_public_wrapper_closes_inner_stream(self):
        async def run():
            closed = asyncio.Event()

            async def inner():
                try:
                    yield b'data: {"usage":{"total_tokens":42}}\n\n'
                    await asyncio.Event().wait()
                finally:
                    closed.set()

            public_api.store.update({"models": ["alpha"]})
            key = self.key(rpm=0)

            async def answer(_request):
                return StreamingResponse(inner())

            async def receive():
                return {"type": "http.request", "body": b'{"model":"alpha"}', "more_body": False}

            request = Request({"type": "http", "method": "POST", "path": "/v1/chat/completions",
                               "headers": [(b"authorization", ("Bearer " + key["key"]).encode())]}, receive)
            with patch.object(openai_api, "chat_completions", answer):
                app = public_api.create_public_app()
                endpoint = next(route.endpoint for route in app.routes if route.path == "/v1/chat/completions")
                response = await endpoint(request)
                await anext(response.body_iterator)
                await response.body_iterator.aclose()
            self.assertTrue(closed.is_set())
            self.assertEqual(public_api.limiter.active, 0)
            self.assertEqual(public_api.store.find(key["key"])["usage"]["tokens"], 42)

        asyncio.run(run())

if __name__ == "__main__":
    unittest.main()
