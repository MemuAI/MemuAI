"""Cancellation, usage and token-limit regressions against real API handlers."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from app import anthropic_api, openai_api
from app.config import config_store
from app.state import state
from starlette.requests import Request


def request(body):
    payload = json.dumps(body).encode()

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    return Request({"type": "http", "method": "POST", "path": "/", "headers": []}, receive)


class APIStreamRegression(unittest.TestCase):
    def test_openai_cancel_with_full_queue_closes_provider(self):
        self._full_queue_cancel(openai_api, openai_api.chat_completions)

    def test_anthropic_cancel_with_full_queue_closes_provider(self):
        self._full_queue_cancel(anthropic_api, anthropic_api.messages)

    def _full_queue_cancel(self, module, handler):
        async def run():
            closed = asyncio.Event()

            async def fast(*_args, **_kwargs):
                try:
                    for _ in range(1000):
                        yield {"id": "test", "choices": [{"delta": {"content": "x"}}]}
                finally:
                    closed.set()

            with patch.object(module, "_check_auth", return_value=None), \
                 patch.object(module.chat_service, "stream_completion", fast), \
                 patch.object(state, "guard_stream", lambda stream, _trace: stream), \
                 patch.object(state, "commit_trace"):
                response = await handler(request({
                    "model": "test", "stream": True, "max_tokens": 1000,
                    "messages": [{"role": "user", "content": "hi"}],
                }))
                stream = response.body_iterator
                for _ in range(3):
                    await anext(stream)
                queue = stream.source.ag_frame.f_locals["queue"]
                for _ in range(20):
                    if queue.full():
                        break
                    await asyncio.sleep(0)
                self.assertTrue(queue.full(), "regression requires backpressure")
                await asyncio.wait_for(stream.aclose(), timeout=.5)
                self.assertTrue(closed.is_set(), "provider must close promptly")
                self.assertFalse(any("pump" in repr(task.get_coro())
                                     for task in asyncio.all_tasks()
                                     if task is not asyncio.current_task()))

        asyncio.run(run())

    def test_anthropic_terminal_usage_preserves_input_tokens(self):
        async def chunks(*_args, **_kwargs):
            yield {"id": "test", "choices": [{"delta": {"content": "answer"}}]}
            yield {"id": "test", "choices": [], "usage": {
                "prompt_tokens": 321, "completion_tokens": 7, "total_tokens": 328,
            }}

        async def run():
            with patch.object(anthropic_api, "_check_auth", return_value=None), \
                 patch.object(anthropic_api.chat_service, "stream_completion", chunks), \
                 patch.object(state, "commit_trace"):
                response = await anthropic_api.messages(request({
                    "model": "test", "stream": True, "max_tokens": 100,
                    "messages": [{"role": "user", "content": "hi"}],
                }))
                wire = "".join([part async for part in response.body_iterator])
            events = [json.loads(line[6:]) for line in wire.splitlines()
                      if line.startswith("data: ")]
            final = next(event for event in events if event["type"] == "message_delta")
            self.assertEqual(final["usage"], {"input_tokens": 321, "output_tokens": 7})
            self.assertEqual(events[-1]["type"], "message_stop")

        asyncio.run(run())

    def test_max_completion_tokens_is_forwarded(self):
        async def run():
            seen = []

            async def chunks(_model, _messages, params, **_kwargs):
                seen.append(dict(params))
                yield {"id": "test", "choices": [{"delta": {"content": "ok"}}]}

            with patch.object(openai_api, "_check_auth", return_value=None), \
                 patch.object(openai_api.chat_service, "stream_completion", chunks), \
                 patch.object(state, "commit_trace"):
                result = await openai_api.chat_completions(request({
                    "model": "test", "max_tokens": 3, "max_completion_tokens": 23,
                    "messages": [{"role": "user", "content": "hi"}],
                }))
            self.assertEqual(seen[0]["max_tokens"], 23)
            self.assertEqual(result["choices"][0]["message"]["content"], "ok")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
