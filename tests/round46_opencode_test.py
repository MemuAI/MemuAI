"""OpenCode integration: fixture-only entitlements, protocols and lifecycle."""
from __future__ import annotations

import asyncio
import copy
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.providers import get, init_registry, builtin_ids
from app.providers import http, opencode_catalog as catalog
from app.providers.opencode import OpenCodeProvider
from app.providers.opencode_native import request_body
from app.providers.base import ProviderAuthError, ProviderModelUnavailableError, ProviderRateLimitError, ProviderServerError
from app.state import Account, ModelEntry, State
from app import model_refresh, tool_protocol


def account(mode="free", **fields):
    return Account("opencode", {"mode": mode, "api_key": "test-opencode-key" if mode != "free" else "", **fields}, id="round46-open")


def metadata():
    def row(name, price=0, **extras):
        return {"id": name, "name": "Readable " + name, "cost": {"input": price, "output": price},
                "limit": {"context": 200000, "output": 8192}, "tool_call": True, **extras}
    return {"opencode": {"npm": "@ai-sdk/openai-compatible", "api": "https://opencode.ai/inference/openai/v1", "models": {
                "genuine-zero": row("genuine-zero"),
                "pretend-free": row("pretend-free", 5),
                "retired-zero": row("retired-zero", status="deprecated"),
                "paid": row("paid", 5, provider={"npm": "@ai-sdk/openai"}),
                "claude": row("claude", 5, provider={"npm": "@ai-sdk/anthropic", "api": "https://opencode.ai/inference/anthropic/v1"}),
                "gemini": row("gemini", 5, provider={"npm": "@ai-sdk/google", "api": "https://opencode.ai/inference/google/v1beta"})}},
            "opencode-go": {"npm": "@ai-sdk/openai-compatible", "api": "https://opencode.ai/inference/go/openai/v1", "models": {
                "subscription-model": row("subscription-model", 2), "removed-go": row("removed-go", 1)}}}


def usage(percent=25):
    return {"usage": {key: {"status": "rate-limited" if percent == 100 else "ok", "percent": percent,
                            "resetsAt": "2026-10-24T13:00:00Z"} for key in ("rolling", "weekly", "monthly")}}


class Response:
    def __init__(self, payload=None, *, status=200, lines=None, headers=None):
        self.payload = payload
        self.status_code = status
        self.headers = headers or {"content-type": "application/json" if lines is None else "text/event-stream"}
        self.content = json.dumps(payload).encode() if payload is not None else b""
        self.text = self.content.decode()
        self.lines = lines if lines is not None else self.text.splitlines()
        self.closed = False

    def json(self):
        return self.payload

    async def aiter_content(self):
        for line in self.lines:
            yield line.encode()

    async def aiter_lines(self):
        for line in self.lines:
            yield line

    async def aclose(self):
        self.closed = True


def sse(*values):
    return [line for value in values for line in ("data: " + json.dumps(value), "")]


def seed(acc, model="m", dialect="chat", api=None):
    acc.extra["opencode_catalog"] = {"checked": time.time(), "fingerprint": catalog.fingerprint(acc.fields),
                                    "models": {model: {"id": model, "protocol": dialect,
                                     "api": api or ("https://opencode.ai/inference/google/v1beta" if dialect == "gemini"
                                                     else "https://opencode.ai/inference/anthropic/v1" if dialect == "messages"
                                                     else "https://opencode.ai/inference/openai/v1")}}}
    return acc


class CatalogTests(unittest.TestCase):
    def test_registry_and_native_tools(self):
        init_registry()
        self.assertIn("opencode", builtin_ids())
        self.assertIsInstance(get("opencode"), OpenCodeProvider)
        self.assertIn("opencode", tool_protocol.TOOL_PROVIDERS)
        self.assertEqual(OpenCodeProvider().seed_models(), [])

    def test_free_uses_zero_pricing_never_name_suffix_or_retired_models(self):
        self.assertEqual(set(catalog.metadata_models(metadata(), "free")), {"genuine-zero"})

    def test_protocol_is_model_metadata_not_name(self):
        rows = catalog.metadata_models(metadata(), "zen")
        self.assertEqual({key: rows[key]["protocol"] for key in ("paid", "claude", "gemini")},
                         {"paid": "responses", "claude": "messages", "gemini": "gemini"})

    def test_go_catalog_never_includes_zen_by_name_or_price(self):
        self.assertEqual(set(catalog.metadata_models(metadata(), "go")), {"subscription-model", "removed-go"})

    def test_metadata_cannot_exfiltrate_user_key(self):
        data = metadata()
        data["opencode"]["models"]["paid"]["provider"]["api"] = "https://attacker.test/v1"
        self.assertNotIn("paid", catalog.metadata_models(data, "zen"))
        for value in ("https://opencode.ai@attacker.test/inference/v1", "https://opencode.ai/inference/../secret", "https://opencode.ai/inference/go/openai/v1"):
            self.assertEqual(catalog.safe_api(value, go=False), "")

    def test_invalid_model_rows_ignored_and_empty_is_authoritative(self):
        rows = catalog.model_ids({"data": [{"id": "ok"}, {"id": "bad\nname"}, {"id": "disabled", "available": False}]})
        self.assertEqual([row["id"] for row in rows], ["ok"])
        self.assertEqual(catalog.model_ids({"data": []}), [])
        with self.assertRaises(ValueError):
            catalog.model_ids({"unrelated": []})

    def test_validate_modes_keys_and_gateway_transport(self):
        provider = OpenCodeProvider()
        self.assertIsNone(provider.validate_fields(account().fields))
        self.assertIsNotNone(provider.validate_fields({"mode": "go"}))
        self.assertIsNotNone(provider.validate_fields({"mode": "zen", "api_key": "two keys"}))
        for url in ("http://public.test/v1", "https://user:pass@host/v1", "https://host/v1?key=secret", "https://host/../v1"):
            self.assertIsNotNone(provider.validate_fields({"mode": "gateway", "base_url": url}))
        self.assertIsNone(provider.validate_fields({"mode": "gateway", "base_url": "http://127.0.0.1:8080/v1"}))
        self.assertIsNone(provider.validate_fields({"mode": "gateway", "base_url": "https://my-gateway.test/v1"}))

    def test_quota_validation_and_zero_remaining_are_truthful(self):
        windows = catalog.quota_windows(usage(100))
        self.assertEqual(windows[0]["remaining_percent"], 0)
        self.assertGreater(windows[0]["reset_at"], 0)
        for invalid in (float("nan"), -1, 101, True):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                catalog.quota_windows(usage(invalid))
        data = usage()
        del data["usage"]["rolling"]
        with self.assertRaises(ValueError):
            catalog.quota_windows(data)

    def test_native_tools_and_image_round_trip(self):
        messages = [{"role": "system", "content": "identity"},
                    {"role": "user", "content": [{"type": "text", "text": "look"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,YQ=="}}]},
                    {"role": "assistant", "content": None, "tool_calls": [{"id": "c", "function": {"name": "read", "arguments": '{"path":"x"}'}}]},
                    {"role": "tool", "tool_call_id": "c", "content": "value"}]
        params = {"tools": [{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}], "tool_choice": "required"}
        ant = request_body("m", messages, params, "messages")
        self.assertEqual(ant["messages"][0]["content"][1]["source"]["media_type"], "image/png")
        self.assertEqual(ant["messages"][-1]["content"][0]["tool_use_id"], "c")
        self.assertEqual(ant["tool_choice"], {"type": "any"})
        gem = request_body("m", messages, params, "gemini")
        self.assertEqual(gem["contents"][0]["parts"][1]["inlineData"]["data"], "YQ==")
        self.assertEqual(gem["contents"][-1]["parts"][0]["functionResponse"]["name"], "read")
        self.assertEqual(gem["toolConfig"]["functionCallingConfig"]["mode"], "ANY")


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_free_intersects_live_available_list_with_zero_price_catalog(self):
        responses = [Response({"data": [{"id": value} for value in ("genuine-zero", "pretend-free", "retired-zero", "not-in-metadata")]}), Response(metadata())]
        session = SimpleNamespace(get=AsyncMock(side_effect=responses))
        acc = account()
        with patch.object(http, "get_session", AsyncMock(return_value=session)), patch(
                "app.opencode_runtime.runtime.ensure", AsyncMock(return_value={"base_url": "http://127.0.0.1:18080/v1", "key": "local-only"})):
            ids = await OpenCodeProvider().dynamic_models(acc)
        self.assertEqual(ids, ["genuine-zero"])
        self.assertNotIn("Authorization", session.get.call_args_list[0].kwargs["headers"])
        self.assertEqual(session.get.call_args_list[0].args[0], catalog.MODELS_URLS["free"])
        self.assertNotIn("Authorization", session.get.call_args_list[1].kwargs["headers"])
        self.assertTrue(all(row.closed for row in responses))

    async def test_zen_forwards_key_only_to_authenticated_model_endpoint(self):
        session = SimpleNamespace(get=AsyncMock(side_effect=[Response({"data": [{"id": "paid"}, {"id": "claude"}]}), Response(metadata())]))
        acc = account("zen")
        with patch.object(http, "get_session", AsyncMock(return_value=session)):
            ids = await OpenCodeProvider().dynamic_models(acc)
        self.assertEqual(ids, ["paid", "claude"])
        self.assertEqual(session.get.call_args_list[0].kwargs["headers"]["Authorization"], "Bearer test-opencode-key")
        self.assertNotIn("Authorization", session.get.call_args_list[1].kwargs["headers"])

    async def test_go_proves_subscription_before_listing_models(self):
        session = SimpleNamespace(get=AsyncMock(side_effect=[Response(usage()), Response({"data": [{"id": "subscription-model"}, {"id": "paid"}]}), Response(metadata())]))
        acc = account("go")
        with patch.object(http, "get_session", AsyncMock(return_value=session)):
            ids = await OpenCodeProvider().dynamic_models(acc)
        self.assertEqual(ids, ["subscription-model"])
        self.assertEqual(session.get.call_args_list[0].args[0], catalog.USAGE_URL)

    async def test_no_go_subscription_revokes_previous_catalog_without_paid_probes(self):
        response = Response({"error": {"message": "OpenCode Go subscription required"}}, status=403)
        session = SimpleNamespace(get=AsyncMock(return_value=response))
        acc = seed(account("go"))
        provider = OpenCodeProvider()
        with patch.object(http, "get_session", AsyncMock(return_value=session)):
            with self.assertRaises(ProviderAuthError):
                await provider.dynamic_models(acc)
        self.assertFalse(provider.account_model_available(acc, "m"))
        self.assertEqual(session.get.await_count, 1)

    async def test_401_rejects_key_and_429_preserves_last_good_catalog(self):
        for status in (401, 429):
            acc = seed(account("zen"))
            response = Response({"error": {"message": "rejected"}}, status=status, headers={"retry-after": "7"})
            session = SimpleNamespace(get=AsyncMock(return_value=response))
            provider = OpenCodeProvider()
            with patch.object(http, "get_session", AsyncMock(return_value=session)):
                with self.assertRaises(ProviderAuthError if status == 401 else ProviderRateLimitError) as caught:
                    await provider.dynamic_models(acc)
            self.assertEqual(provider.account_model_available(acc, "m"), status == 429)
            if status == 429:
                self.assertEqual(caught.exception.retry_after, 7)
            self.assertTrue(response.closed)

    async def test_gateway_models_are_exact_and_do_not_fetch_public_catalog(self):
        acc = account("gateway", base_url="http://127.0.0.1:8080/v1")
        response = Response({"data": [{"id": "opencode/special"}, {"id": "blocked", "available": False}]})
        session = SimpleNamespace(get=AsyncMock(return_value=response))
        with patch.object(http, "get_session", AsyncMock(return_value=session)):
            ids = await OpenCodeProvider().dynamic_models(acc)
        self.assertEqual(ids, ["opencode/special"])
        self.assertEqual(session.get.await_count, 1)

    async def test_unknown_or_old_credentials_cannot_route_guessed_model(self):
        provider = OpenCodeProvider()
        acc = seed(account("zen"))
        self.assertTrue(provider.account_model_available(acc, "m"))
        self.assertFalse(provider.account_model_available(acc, "guessed"))
        acc.fields["api_key"] = "different-key"
        self.assertFalse(provider.account_model_available(acc, "m"))
        self.assertFalse(provider.model_availability("m", [acc])["available"])

    async def test_usage_cached_one_minute_is_sanitized_and_compatible_with_proxy_cards(self):
        acc = account("go")
        session = SimpleNamespace(get=AsyncMock(return_value=Response(usage())))
        provider = OpenCodeProvider()
        with patch.object(http, "get_session", AsyncMock(return_value=session)):
            first = await provider.usage(acc)
            second = await provider.usage(acc)
        self.assertEqual(session.get.await_count, 1)
        self.assertEqual(first, second)
        self.assertEqual(first["windows"][0]["remaining_percent"], 75)
        self.assertEqual(first["rows"][0]["used"], 25)
        self.assertNotIn("test-opencode-key", json.dumps(first))
        for mode in ("free", "zen", "gateway"):
            unavailable = await provider.usage(account(mode))
            self.assertEqual(unavailable["status"], "unavailable")
            self.assertEqual(unavailable["windows"], [])

    async def test_redirect_does_not_forward_key(self):
        session = SimpleNamespace(get=AsyncMock(return_value=Response({}, status=302)))
        with patch.object(http, "get_session", AsyncMock(return_value=session)):
            with self.assertRaisesRegex(ProviderServerError, "redirect"):
                await OpenCodeProvider().dynamic_models(account("zen"))
        self.assertFalse(session.get.call_args.kwargs["allow_redirects"])

    async def test_catalog_limit_aborts_before_reading_unbounded_stream(self):
        class Oversized(Response):
            async def aiter_content(self):
                yield b"x" * 150
                raise AssertionError("Must abort before consuming any more of the response")
        response = Oversized()
        session = SimpleNamespace(get=AsyncMock(return_value=response))
        with patch.object(http, "get_session", AsyncMock(return_value=session)):
            with self.assertRaisesRegex(ProviderServerError, "too large"):
                await OpenCodeProvider()._get_json(account(), catalog.MODELS_URLS["free"], limit=100)
        self.assertTrue(response.closed)
        self.assertTrue(session.get.call_args.kwargs["stream"])

    async def test_empty_success_prunes_old_entries_but_not_another_accounts_models(self):
        accounts = [account("free"), Account("opencode", {"mode": "zen", "api_key": "key"}, id="other")]
        provider = OpenCodeProvider()
        for acc in accounts:
            seed(acc)
        accounts[0].extra["opencode_catalog"]["models"] = {}
        other = accounts[1]
        fake = SimpleNamespace(_lock=threading.RLock(), accounts={acc.id: acc for acc in accounts},
                               models={"opencode-m": ModelEntry("opencode", "opencode", "m")},
                               save_models=lambda: None, prune_orphan_models=lambda _wanted: 0)
        eligible = [(acc, "opencode", "opencode") for acc in accounts]
        with patch.object(model_refresh, "state", fake), patch.object(model_refresh, "get_provider", return_value=provider):
            result = model_refresh.ModelRefresher._apply([(accounts[0], "opencode", "opencode", [], ""),
                        (other, "opencode", "opencode", ["m"], "")], eligible, prune=True, wanted={"opencode"})
            self.assertIn("opencode-m", fake.models)
            self.assertTrue(fake.models["opencode-m"].availability["available"])
            other.extra["opencode_catalog"]["models"] = {}
            result = model_refresh.ModelRefresher._apply([(accounts[0], "opencode", "opencode", [], ""),
                        (other, "opencode", "opencode", [], "")], eligible, prune=True, wanted={"opencode"})
        self.assertEqual(result["removed"], 1)
        self.assertEqual(result["updated"], 2)


class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def run_source(self, dialect, response, *, acc=None, messages=None, params=None):
        acc = seed(acc or account("zen"), dialect=dialect)
        session = SimpleNamespace(post=AsyncMock(return_value=response))
        with patch.object(http, "get_session", AsyncMock(return_value=session)):
            result = [event async for event in OpenCodeProvider().chat_stream(acc, "m", messages or [{"role": "user", "content": "hello"}], params or {})]
        self.assertTrue(response.closed)
        return result, session

    async def test_chat_uses_existing_tool_usage_and_cache_accounting(self):
        response = Response(lines=sse({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c", "function": {"name": "read", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}],
                                               "usage": {"prompt_tokens": 13, "completion_tokens": 2, "prompt_tokens_details": {"cached_tokens": 5}}}))
        events, session = await self.run_source("chat", response)
        self.assertIn("tool_call", [event["type"] for event in events])
        self.assertEqual(events[0]["cached_tokens"], 5)
        self.assertIn("x-opencode-session", session.post.call_args.kwargs["headers"])

    async def test_responses_protocol_uses_existing_response_adapter(self):
        response = Response(lines=sse({"type": "response.output_text.delta", "delta": "answer"},
            {"type": "response.completed", "response": {"status": "completed", "output": [], "usage": {"input_tokens": 11, "output_tokens": 3}}}))
        events, session = await self.run_source("responses", response)
        self.assertEqual(events[0], {"type": "text", "text": "answer"})
        self.assertTrue(session.post.call_args.args[0].endswith("/responses"))
        self.assertIn("input", session.post.call_args.kwargs["json"])

    async def test_messages_stream_preserves_tools_thoughts_cache_usage(self):
        response = Response(lines=sse({"type": "message_start", "message": {"usage": {"input_tokens": 10, "cache_read_input_tokens": 4, "cache_creation_input_tokens": 2}}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "think"}},
            {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "c", "name": "read", "input": {}}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "{}"}},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 3}}, {"type": "message_stop"}))
        events, session = await self.run_source("messages", response)
        self.assertEqual(events[0], {"type": "reasoning", "text": "think"})
        self.assertEqual(events[-1]["prompt_tokens"], 16)
        self.assertEqual(events[-1]["cached_tokens"], 4)
        self.assertEqual(events[-1]["cache_write_tokens"], 2)
        self.assertEqual(events[-1]["completion_tokens"], 3)
        self.assertEqual(session.post.call_args.kwargs["headers"]["anthropic-version"], "2023-06-01")

    async def test_messages_non_streaming_json_is_not_empty(self):
        response = Response({"type": "message", "content": [{"type": "text", "text": "normal JSON"}],
                             "stop_reason": "end_turn", "usage": {"input_tokens": 9, "output_tokens": 4}})
        events, _ = await self.run_source("messages", response)
        self.assertEqual(events[0]["text"], "normal JSON")
        self.assertEqual(events[-1]["completion_tokens"], 4)

    async def test_gemini_stream_tool_and_reasoning_usage(self):
        response = Response(lines=sse({"candidates": [{"content": {"parts": [{"text": "thought", "thought": True}, {"functionCall": {"name": "read", "args": {"path": "x"}}}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 14, "candidatesTokenCount": 3, "thoughtsTokenCount": 2, "cachedContentTokenCount": 6}}))
        events, session = await self.run_source("gemini", response)
        self.assertEqual(events[0]["type"], "reasoning")
        self.assertEqual(events[1]["name"], "read")
        self.assertEqual(events[-1]["completion_tokens"], 5)
        self.assertEqual(events[-1]["cached_tokens"], 6)
        self.assertIn(":streamGenerateContent?alt=sse", session.post.call_args.args[0])

    async def test_gemini_tool_round_trip_restores_private_thought_signature(self):
        provider = OpenCodeProvider()
        acc = seed(account("zen"), dialect="gemini")
        first = Response(lines=sse({"candidates": [{"content": {"parts": [{"functionCall": {"id": "native-c", "name": "read", "args": {"path": "x"}},
                                                                                       "thoughtSignature": "opaque-provider-signature"}]}, "finishReason": "STOP"}]}))
        second = Response(lines=sse({"candidates": [{"content": {"parts": [{"text": "done"}]}, "finishReason": "STOP"}]}))
        session = SimpleNamespace(post=AsyncMock(side_effect=[first, second]))
        messages = [{"role": "user", "content": "read x"}]
        with patch.object(http, "get_session", AsyncMock(return_value=session)):
            events = [event async for event in provider.chat_stream(acc, "m", messages, {})]
            tool = next(event for event in events if event["type"] == "tool_call")
            messages += [{"role": "assistant", "content": None, "tool_calls": [{"id": tool["id"], "type": "function",
                          "function": {"name": tool["name"], "arguments": tool["arguments"]}}]},
                         {"role": "tool", "tool_call_id": tool["id"], "content": "file value"}]
            source = provider.chat_stream(acc, "m", messages, {})
            await anext(source)
            await source.aclose()
        sent = session.post.call_args.kwargs["json"]["contents"]
        self.assertEqual(sent[1]["parts"][0]["thoughtSignature"], "opaque-provider-signature")
        self.assertEqual(sent[2]["parts"][0]["functionResponse"]["id"], "native-c")
        self.assertNotIn("opaque-provider-signature", json.dumps(acc.public_dict()))
        # A different key cannot borrow signatures from the previous account state.
        acc.fields["api_key"] = "replaced-key"
        self.assertFalse(provider.account_model_available(acc, "m"))

    async def test_native_error_frames_are_classified_and_closed(self):
        for error, cls in (({"type": "authentication_error", "message": "bad key"}, ProviderAuthError),
                           ({"type": "rate_limit_error", "message": "wait"}, ProviderRateLimitError)):
            response = Response(lines=sse({"type": "error", "error": error}))
            with self.assertRaises(cls):
                await self.run_source("messages", response)
            self.assertTrue(response.closed)

    async def test_broken_native_stream_never_reports_success(self):
        response = Response(lines=sse({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "partial"}}))
        with self.assertRaisesRegex(ProviderServerError, "completion"):
            await self.run_source("messages", response)
        self.assertTrue(response.closed)

    async def test_stop_aborts_both_native_and_compatible_transport(self):
        class WaitingResponse(Response):
            async def aiter_lines(self):
                for line in self.lines:
                    yield line
                await asyncio.Event().wait()
        for dialect, lines in (("messages", sse({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "first"}})),
                               ("chat", sse({"choices": [{"delta": {"content": "first"}}]}))):
            response = WaitingResponse(lines=lines)
            session = SimpleNamespace(post=AsyncMock(return_value=response))
            acc = seed(account("zen"), dialect=dialect)
            with patch.object(http, "get_session", AsyncMock(return_value=session)):
                source = OpenCodeProvider().chat_stream(acc, "m", [{"role": "user", "content": "hello"}], {})
                self.assertEqual((await anext(source))["text"], "first")
                await asyncio.wait_for(source.aclose(), 1)
            self.assertTrue(response.closed)

    async def test_unknown_model_fails_without_network(self):
        acc = seed(account("zen"))
        session = AsyncMock()
        with patch.object(http, "get_session", session):
            with self.assertRaises(ProviderModelUnavailableError):
                await anext(OpenCodeProvider().chat_stream(acc, "unknown", [], {}))
        session.assert_not_awaited()

    async def test_session_id_stable_across_tool_turns_and_explicit_chats_separate(self):
        requests = []
        async def run(messages, params):
            response = Response(lines=sse({"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}))
            _, session = await self.run_source("chat", response, messages=messages, params=params)
            requests.append(session.post.call_args.kwargs["headers"]["x-opencode-session"])
        first = [{"role": "user", "content": "start"}]
        await run(first, {})
        await run(first + [{"role": "assistant", "content": "later"}], {})
        await run(first, {"session_id": "different-conversation"})
        self.assertEqual(requests[0], requests[1])
        self.assertNotEqual(requests[0], requests[2])

    async def test_actual_loopback_gateway_catalog_and_tool_stream(self):
        received = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_GET(self):
                received.append((self.path, self.headers.get("Authorization")))
                body = json.dumps({"data": [{"id": "opencode/mock"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                received.append((self.path, payload))
                body = ("data: " + json.dumps({"choices": [{"delta": {"content": "loopback"}, "finish_reason": "stop"}],
                            "usage": {"prompt_tokens": 2, "completion_tokens": 1}}) + "\n\n").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        provider = OpenCodeProvider()
        acc = account("gateway", base_url=f"http://127.0.0.1:{server.server_port}/v1")
        try:
            self.assertEqual(await provider.dynamic_models(acc), ["opencode/mock"])
            events = [event async for event in provider.chat_stream(acc, "opencode/mock", [{"role": "user", "content": "hello"}], {})]
            self.assertEqual(next(event["text"] for event in events if event["type"] == "text"), "loopback")
            self.assertEqual(received[0][1], "Bearer test-opencode-key")
            self.assertEqual(received[1][0], "/v1/chat/completions")
        finally:
            await http.close_all()
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join(timeout=2)


class AdminLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from fastapi import FastAPI
        import httpx
        from app import admin_api

        class MemoryState(State):
            def __init__(self):
                self._lock = threading.RLock()
                self.accounts, self.models = {}, {}
                self._unparsed_accounts, self._unparsed_models = [], []
            def save_accounts(self, **_):
                pass
            def save_models(self):
                pass

        self.state = MemoryState()
        self.provider = OpenCodeProvider()

        async def discover(acc):
            model = "free-active" if catalog.mode_of(acc.fields) == "free" else "paid-active"
            seed(acc, model=model)
            return [model]

        self.provider.dynamic_models = AsyncMock(side_effect=discover)
        self.patches = [patch.object(admin_api, "state", self.state), patch.object(model_refresh, "state", self.state),
                        patch.object(admin_api, "_auth", return_value=None),
                        patch.object(admin_api, "get_provider", return_value=self.provider),
                        patch.object(model_refresh, "get_provider", return_value=self.provider),
                        patch.object(model_refresh, "refresher", model_refresh.ModelRefresher())]
        for item in self.patches:
            item.start()
        app = FastAPI()
        app.include_router(admin_api.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        for item in reversed(self.patches):
            item.stop()

    async def test_create_change_mode_refresh_check_usage_and_delete(self):
        added = await self.client.post("/api/admin/accounts", json={"provider": "opencode", "fields": {"mode": "free"}})
        self.assertEqual(added.status_code, 200, added.text)
        ident = added.json()["account"]["id"]
        self.assertEqual(set(self.state.models), {"opencode-free-active"})
        updated = await self.client.put(f"/api/admin/accounts/{ident}", json={"fields": {"mode": "zen", "api_key": "real-test-key"}})
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(set(self.state.models), {"opencode-paid-active"})
        checked = await self.client.post(f"/api/admin/accounts/{ident}/check")
        self.assertTrue(checked.json()["ok"], checked.text)
        usage_response = await self.client.get(f"/api/admin/accounts/{ident}/usage")
        self.assertEqual(usage_response.json()["usage"]["status"], "unavailable")
        self.assertNotIn("real-test-key", usage_response.text)
        deleted = await self.client.delete(f"/api/admin/accounts/{ident}")
        self.assertTrue(deleted.json()["ok"])
        self.assertEqual(self.state.models, {})

    async def test_reject_invalid_mode_and_missing_subscription_key(self):
        for fields in ({"mode": "other"}, {"mode": "go"}, {"mode": "gateway", "base_url": "http://public.test/v1"}):
            response = await self.client.post("/api/admin/accounts", json={"provider": "opencode", "fields": fields})
            self.assertEqual(response.status_code, 400)
        self.provider.dynamic_models.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
