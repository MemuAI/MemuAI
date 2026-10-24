"""Isolated proofs: early stream close, ASGI start failure, bounded accounting."""
import asyncio
import json
import unittest
from unittest.mock import patch

from app import anthropic_api, openai_api, public_api, responses_api
from app.state import ClosingStreamingResponse, LogEntry, state
from starlette.background import BackgroundTask
from starlette.requests import Request


def request(body, path="/v1/chat/completions", headers=()):
    async def receive():
        return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}
    return Request({"type": "http", "method": "POST", "path": path, "headers": list(headers)}, receive)


class StreamLifecycleRegression(unittest.TestCase):
    def test_cancel_trace_preserves_finished_trace_and_commits_cancel_once(self):
        entry = LogEntry()
        with patch.object(state, "commit_trace") as commit:
            state.cancel_trace(entry, {"input_tokens":10, "output_tokens":2})
            state.cancel_trace(entry, {"input_tokens":90, "output_tokens":3})
            self.assertEqual(entry.status, 499)
            self.assertEqual(entry.error_code, "client_disconnected")
            self.assertEqual((entry.prompt_tokens, entry.completion_tokens), (10, 2))
            self.assertGreaterEqual(entry.ms, 0)
            commit.assert_called_once_with(entry)
            done = LogEntry(status=200)
            state.cancel_trace(done)
            self.assertEqual(done.status, 200)
            self.assertEqual(commit.call_count, 1)

    def test_cancel_trace_suppresses_persistence_error(self):
        entry = LogEntry()
        with patch.object(state, "commit_trace", side_effect=OSError("disk unavailable")):
            state.cancel_trace(entry, {"prompt_tokens":4, "completion_tokens":3})
        self.assertEqual(entry.status, 499)
        self.assertEqual((entry.prompt_tokens, entry.completion_tokens), (4, 3))

    def test_prefetch_cancellation_finishes_trace_and_closes_provider(self):
        self._handler_cancellation(True)

    def test_nonstream_cancellation_finishes_trace_with_observed_usage(self):
        self._handler_cancellation(False)

    def _handler_cancellation(self, streaming):
        async def run():
            for module, handler, body in (
                (openai_api, openai_api.chat_completions, {"model":"test", "stream":True,
                    "messages":[{"role":"user", "content":"hi"}]}),
                (anthropic_api, anthropic_api.messages, {"model":"test", "stream":True,
                    "messages":[{"role":"user", "content":"hi"}]}),
                (responses_api, responses_api.responses, {"model":"test", "stream":True, "input":"hi"}),
            ):
                with self.subTest(module=module.__name__):
                    body = dict(body, stream=streaming)
                    started, closed = asyncio.Event(), asyncio.Event()
                    async def source(*args, **kwargs):
                        try:
                            if not streaming:
                                yield {"choices": [], "usage": {
                                    "prompt_tokens":10,"completion_tokens":2,"total_tokens":12}}
                            started.set()
                            await asyncio.Event().wait()
                            yield {}
                        finally:
                            closed.set()
                    trace = LogEntry()
                    with patch.object(module, "_check_auth", return_value=None), \
                         patch.object(module.chat_service, "stream_completion", source), \
                         patch.object(state, "open_trace", return_value=trace), \
                         patch.object(state, "commit_trace"):
                        task = asyncio.create_task(handler(request(body)))
                        await started.wait()
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await task
                    self.assertTrue(closed.is_set())
                    self.assertEqual(trace.status, 499)
                    if not streaming:
                        self.assertEqual((trace.prompt_tokens, trace.completion_tokens), (10, 2))
        asyncio.run(run())

    def test_prefetched_usage_is_available_before_any_response_iteration(self):
        async def run():
            for module, handler, body in (
                (openai_api, openai_api.chat_completions, {"model":"test", "stream":True,
                    "messages":[{"role":"user", "content":"hi"}]}),
                (anthropic_api, anthropic_api.messages, {"model":"test", "stream":True,
                    "messages":[{"role":"user", "content":"hi"}]}),
                (responses_api, responses_api.responses, {"model":"test", "stream":True, "input":"hi"}),
            ):
                with self.subTest(module=module.__name__):
                    closed = asyncio.Event()
                    async def source(*args, **kwargs):
                        try:
                            yield {"choices": [], "usage": {"prompt_tokens":10,"completion_tokens":2,"total_tokens":12}}
                            await asyncio.Event().wait()
                        finally:
                            closed.set()
                    req = request(body)
                    with patch.object(module, "_check_auth", return_value=None), \
                         patch.object(module.chat_service, "stream_completion", source), \
                         patch.object(state, "commit_trace"):
                        response = await handler(req)
                        await response.body_iterator.aclose()
                    self.assertTrue(closed.is_set())
                    self.assertEqual(getattr(req.state, "completion_usage", None),
                        {"prompt_tokens":10,"completion_tokens":2,"total_tokens":12})
        asyncio.run(run())

    def test_nonstream_error_accounts_already_observed_usage(self):
        self._failed_handler_accounting(False)

    def test_canceled_handler_accounts_already_observed_usage(self):
        self._failed_handler_accounting(True)

    def _failed_handler_accounting(self, canceled):
        async def run():
            store, limiter = public_api.Store(), public_api.Limiter()
            store.update({"models": ["test"]})
            key = store.add_key({"rpm":0})
            async def handler(req):
                req.state.completion_usage = {"prompt_tokens":10,"completion_tokens":2,"total_tokens":12}
                if canceled:
                    raise asyncio.CancelledError()
                return public_api.JSONResponse({"error":{"message":"upstream closed"}}, status_code=502)
            with patch.object(public_api, "store", store), \
                 patch.object(public_api, "limiter", limiter), \
                 patch.object(public_api, "catalogue", return_value=["test"]), \
                 patch.object(openai_api, "chat_completions", handler):
                endpoint = next(route.endpoint for route in public_api.create_public_app().routes
                    if route.path == "/v1/chat/completions")
                req = request({"model":"test"}, headers=[
                    (b"authorization", ("Bearer " + key["key"]).encode())])
                if canceled:
                    with self.assertRaises(asyncio.CancelledError):
                        await endpoint(req)
                else:
                    response = await endpoint(req)
                    self.assertEqual(response.status_code, 502)
                self.assertEqual(limiter.active, 0)
                self.assertEqual(store.find(key["key"])["usage"]["tokens"], 12)
        asyncio.run(run())

    def test_unstarted_guard_closes_primed_inner_and_logs_once(self):
        async def run():
            closed = asyncio.Event()
            async def source():
                try:
                    yield b"first"
                    await asyncio.Event().wait()
                finally:
                    closed.set()
            inner = source()
            await anext(inner)
            trace = LogEntry()
            with patch.object(state, "commit_trace") as commit:
                guard = state.guard_stream(inner, trace)
                await guard.aclose()
                await guard.aclose()
                self.assertTrue(closed.is_set())
                self.assertEqual(trace.status, 499)
                commit.assert_called_once_with(trace)
        asyncio.run(run())

    def test_unstarted_response_closes_prefetched_source(self):
        self._response_lifecycle("explicit", "2.4")

    def test_normal_guard_completion_preserves_success_without_cancel_log(self):
        async def run():
            async def source():
                yield b"first"
            trace = LogEntry(status=200)
            with patch.object(state, "commit_trace") as commit:
                guard = state.guard_stream(source(), trace)
                self.assertEqual([chunk async for chunk in guard], [b"first"])
                await guard.aclose()
                self.assertEqual(trace.status, 200)
                commit.assert_not_called()
        asyncio.run(run())

    def test_response_start_error_closes_prefetched_source(self):
        self._response_lifecycle("error", "2.4")

    def test_response_start_cancel_closes_prefetched_source(self):
        self._response_lifecycle("cancel", "2.4")

    def test_legacy_response_start_error_closes_prefetched_source(self):
        self._response_lifecycle("error", "2.0")

    def test_legacy_response_start_cancel_closes_prefetched_source(self):
        self._response_lifecycle("cancel", "2.0")

    def _response_lifecycle(self, trigger, spec):
        async def run():
            closed = asyncio.Event()
            async def source():
                try:
                    yield b"first"
                    await asyncio.Event().wait()
                finally:
                    closed.set()
            inner = source()
            await anext(inner)
            async def unstarted():
                async for chunk in inner:
                    yield chunk
            calls = []
            async def close():
                calls.append(1)
                await inner.aclose()
            response = ClosingStreamingResponse(unstarted(), closer=close)
            async def receive():
                await asyncio.Event().wait()
            async def send(message):
                self.assertEqual(message["type"], "http.response.start")
                if trigger == "cancel":
                    raise asyncio.CancelledError()
                raise RuntimeError("response start failed")
            if trigger == "explicit":
                await response.body_iterator.aclose()
            else:
                try:
                    await asyncio.wait_for(response({"type": "http", "asgi": {"spec_version": spec}}, receive, send), .5)
                except BaseException as exc:
                    self.assertNotIsInstance(exc, TimeoutError)
            await response.aclose()
            self.assertTrue(closed.is_set())
            self.assertEqual(calls, [1])
        asyncio.run(run())

    def test_public_unstarted_close_preserves_inner_cleanup_and_response_metadata(self):
        self._public_lifecycle(False)

    def test_public_normal_stream_counts_usage_and_closes_once(self):
        self._public_lifecycle(True)

    def test_public_oversized_responses_event_uses_actual_usage_fallback(self):
        self._public_lifecycle("oversized")

    def test_public_response_start_error_releases_slot_and_closes_once(self):
        self._public_lifecycle("error")

    def test_public_response_start_cancel_releases_slot_and_closes_once(self):
        self._public_lifecycle("cancel")

    def _public_lifecycle(self, trigger):
        async def run():
            store, limiter = public_api.Store(), public_api.Limiter()
            store.update({"models": ["test"]})
            key = store.add_key({"rpm": 0})
            closed = asyncio.Event()
            async def source():
                try:
                    yield b"prefetched"
                    if trigger == "oversized":
                        yield b'data: ' + json.dumps({"response": {
                            "output": "x" * (public_api.MAX_SSE_EVENT_BYTES + 100),
                            "usage": {"input_tokens":10,"output_tokens":5}}}).encode() + b"\n\n"
                    else:
                        yield b'data: {"usage":{"input_tokens":10,"output_tokens":2}}\n\n'
                finally:
                    closed.set()
            inner = source()
            await anext(inner)
            calls = []
            async def close():
                calls.append(1)
                await inner.aclose()
            background = BackgroundTask(lambda: None)
            async def handler(req):
                req.state.completion_usage = {"input_tokens":10,"output_tokens":5}
                response = ClosingStreamingResponse(inner, closer=close, status_code=201,
                    media_type="text/event-stream", background=background)
                response.raw_headers.extend([(b"x-proof", b"one"), (b"x-proof", b"two")])
                return response
            with patch.object(public_api, "store", store), \
                 patch.object(public_api, "limiter", limiter), \
                 patch.object(public_api, "catalogue", return_value=["test"]), \
                 patch.object(openai_api, "chat_completions", handler), \
                 patch.object(store, "count_tokens", wraps=store.count_tokens) as count:
                app = public_api.create_public_app()
                endpoint = next(route.endpoint for route in app.routes if route.path == "/v1/chat/completions")
                response = await endpoint(request({"model": "test"}, headers=[
                    (b"authorization", ("Bearer " + key["key"]).encode())]))
                self.assertEqual(limiter.active, 1)
                self.assertEqual(response.status_code, 201)
                self.assertIs(response.background, background)
                self.assertEqual(response.raw_headers[-2:], [(b"x-proof", b"one"), (b"x-proof", b"two")])
                if trigger is True or trigger == "oversized":
                    chunks = [chunk async for chunk in response.body_iterator]
                    self.assertEqual(len(chunks), 1)
                elif trigger is False:
                    await response.body_iterator.aclose()
                else:
                    async def receive():
                        await asyncio.Event().wait()
                    async def send(message):
                        if trigger == "cancel":
                            raise asyncio.CancelledError()
                        raise RuntimeError("send failed")
                    with self.assertRaises(BaseException):
                        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
                await response.aclose()
                self.assertEqual(calls, [1])
                self.assertTrue(closed.is_set())
                self.assertEqual(limiter.active, 0)
                self.assertEqual(store.find(key["key"])["usage"]["tokens"], 15)
                count.assert_called_once()
        asyncio.run(run())

    def test_usage_buffer_bounds_long_line_and_recovers_after_separator(self):
        counter = public_api.TokenUsage()
        for _ in range(64):
            counter.feed(b"x" * 65536)
            self.assertLessEqual(len(counter._buffer), public_api.MAX_SSE_EVENT_BYTES)
        counter.feed(b'\r\n\r\ndata: {"usage":{"total_tokens":7}}\n\n')
        self.assertEqual(counter.finish(), 7)

    def test_usage_buffer_bounds_multiline_event_and_recovers(self):
        counter = public_api.TokenUsage()
        for _ in range(64):
            counter.feed(b"data: " + b"x" * 65536 + b"\n")
            self.assertLessEqual(sum(map(len, counter._data)) + len(counter._buffer), public_api.MAX_SSE_EVENT_BYTES)
        counter.feed(b'\ndata: {"usage":{"total_tokens":8}}\n\n')
        self.assertEqual(counter.finish(), 8)

    def test_fragmented_usage_ignores_fake_content_and_boolean_counts(self):
        counter = public_api.TokenUsage()
        wire = (b'data: {"usage":\r\ndata: {"input_tokens":10,"output_tokens":2}}\r\n\r\n'
                b'data: {"delta":{"text":"usage total_tokens 9999"},"usage":{"total_tokens":true}}\n\n')
        for byte in wire:
            counter.feed(bytes([byte]))
        self.assertEqual(counter.finish(), 12)


if __name__ == "__main__":
    unittest.main()
