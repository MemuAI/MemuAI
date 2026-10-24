"""Charge upstream usage even when its queued SSE event is never delivered."""
import asyncio
import json
import unittest
from unittest.mock import patch

from app import openai_api, public_api
from app.state import state
from starlette.requests import Request


class QueueAccountingRegressionTest(unittest.TestCase):
    def test_client_send_failure_keeps_usage_received_by_provider_pump(self):
        async def run():
            for path in ("/v1/chat/completions", "/v1/messages"):
                with self.subTest(path=path):
                    store, limiter = public_api.Store(), public_api.Limiter()
                    store.update({"models": ["test"]})
                    key = store.add_key({"rpm": 0})
                    produced, closed = asyncio.Event(), asyncio.Event()

                    async def source(*args, **kwargs):
                        try:
                            yield {"choices": [{"delta": {"content": "first"}}]}
                            yield {"choices": [{"delta": {"content": "second"}}]}
                            produced.set()
                            yield {"choices": [], "usage": {
                                "prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}}
                        finally:
                            closed.set()

                    async def receive():
                        return {"type": "http.request", "body": json.dumps({"model": "test", "stream": True,
                            "messages": [{"role": "user", "content": "hi"}]}).encode(), "more_body": False}

                    req = Request({"type": "http", "method": "POST", "path": path,
                        "headers": [(b"authorization", ("Bearer " + key["key"]).encode())]}, receive)
                    with patch.object(public_api, "store", store), patch.object(public_api, "limiter", limiter), \
                         patch.object(public_api, "catalogue", return_value=["test"]), \
                         patch.object(openai_api.chat_service, "stream_completion", source), \
                         patch.object(state, "commit_trace"):
                        endpoint = next(route.endpoint for route in public_api.create_public_app().routes
                                        if route.path == path)
                        response = await endpoint(req)
                        chunks = 0

                        async def send(message):
                            nonlocal chunks
                            if message["type"] == "http.response.body":
                                chunks += 1
                                if chunks == 2:
                                    self.assertTrue(produced.is_set())
                                    raise RuntimeError("client stopped reading")

                        with self.assertRaises(RuntimeError):
                            await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
                        self.assertTrue(closed.is_set())
                        self.assertEqual(limiter.active, 0)
                        self.assertEqual(store.find(key["key"])["usage"]["tokens"], 120)
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
