"""Regression tests for the core audit fixes (pool, chat retries, protocols, auth)."""
from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from app.chat import ChatService, CompletionError
from app.config import config_store
from app.openai_api import chat_completions
from app.pool import AccountPool
from app.providers.base import (ProviderModelUnavailableError, ProviderNetworkError,
                                ProviderServerError)
from app.responses_api import responses
from app.routing import Resolved
from app.state import ST_OK, ST_UNKNOWN, Account, state
from app.tool_protocol import collect_tool_delta, ordered_tool_calls
from tests.protocol_contract_test import make_request


def _chunks(finish_reason="stop"):
    async def generate(*_args, **_kwargs):
        yield {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "m",
               "choices": [{"index": 0, "delta": {"content": "hi"}, "finish_reason": None}]}
        yield {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "m",
               "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
               "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}
    return generate


async def _body(response) -> str:
    return b"".join([chunk if isinstance(chunk, bytes) else chunk.encode()
                     async for chunk in response.body_iterator]).decode()


class ToolDeltaTests(unittest.TestCase):
    def test_repeated_full_name_is_not_duplicated(self):
        calls = {}
        collect_tool_delta(calls, {"tool_calls": [{"index": 0, "id": "a",
                                                   "function": {"name": "run", "arguments": "{"}}]})
        collect_tool_delta(calls, {"tool_calls": [{"index": 0,
                                                   "function": {"name": "run", "arguments": "}"}}]})
        self.assertEqual(calls[0]["function"], {"name": "run", "arguments": "{}"})

    def test_missing_index_keeps_parallel_calls_apart(self):
        calls = {}
        collect_tool_delta(calls, {"tool_calls": [{"id": "a", "function": {"name": "one", "arguments": "{}"}}]})
        collect_tool_delta(calls, {"tool_calls": [{"id": "b", "function": {"name": "two", "arguments": "{"}}]})
        collect_tool_delta(calls, {"tool_calls": [{"function": {"arguments": "}"}}]})
        ordered = ordered_tool_calls(calls)
        self.assertEqual([c["id"] for c in ordered], ["a", "b"])
        self.assertEqual(ordered[1]["function"]["arguments"], "{}")

    def test_missing_call_id_gets_stable_id(self):
        calls = {}
        collect_tool_delta(calls, {"tool_calls": [{"index": 0, "function": {"name": "x", "arguments": "{}"}}]})
        first = ordered_tool_calls(calls)[0]["id"]
        self.assertTrue(first.startswith("call_"))
        self.assertEqual(ordered_tool_calls(calls)[0]["id"], first)


class PoolTests(unittest.TestCase):
    def test_first_available_prefers_ok_status(self):
        bad = Account("custom", {"name": "a"}, id="bad", order=0, status=ST_UNKNOWN)
        good = Account("custom", {"name": "a"}, id="good", order=1, status=ST_OK)
        with patch.object(state, "usable_accounts", return_value=[bad, good]), \
                patch.object(config_store.settings, "balancing", "first-available"):
            order = [a.id for a in AccountPool().ordered_candidates("custom")]
        self.assertEqual(order, ["good", "bad"])

    def test_round_robin_is_even_inside_one_custom_connection(self):
        accounts = [Account("custom", {"name": "a"}, id="a1", order=0),
                    Account("custom", {"name": "b"}, id="b1", order=1),
                    Account("custom", {"name": "a"}, id="a2", order=2)]
        pool = AccountPool()
        with patch.object(state, "usable_accounts", return_value=accounts), \
                patch.object(config_store.settings, "balancing", "round-robin"):
            firsts = [pool.ordered_candidates("custom", "m", prefix="a")[0].id for _ in range(4)]
        self.assertEqual(firsts, ["a1", "a2", "a1", "a2"])


class ChatRetryTests(unittest.TestCase):
    def _run(self, provider, accounts):
        async def run():
            service = ChatService()
            with patch.object(config_store.settings, "proxy_enabled", True), \
                    patch.object(config_store.settings, "retry_delay_ms", 0), \
                    patch.object(config_store.settings, "max_retry_passes", 2), \
                    patch("app.chat.resolve", return_value=Resolved(None, "test", "model", "test")), \
                    patch.object(service, "_provider", return_value=provider), \
                    patch("app.chat.pool.ordered_candidates", return_value=accounts), \
                    patch("app.chat.pool.report_success"), patch("app.chat.pool.report_failure"):
                return [c async for c in service.stream_completion(
                    "test-model", [{"role": "user", "content": "x"}], {})]
        return asyncio.run(run())

    def test_model_unavailable_is_not_double_counted_across_passes(self):
        class Provider:
            id = "test"
            attempts = []

            async def chat_stream(self, account, *_args, **_kwargs):
                self.attempts.append(account.id)
                if account.id == "nomodel":
                    raise ProviderModelUnavailableError("no model", "model")
                raise ProviderNetworkError("timeout")
                yield  # pragma: no cover

        provider = Provider()
        with self.assertRaises(CompletionError) as caught:
            self._run(provider, [Account("test", {}, id="nomodel"), Account("test", {}, id="net")])
        self.assertEqual(caught.exception.err_type, "network_error")
        self.assertEqual(provider.attempts.count("nomodel"), 1)

    def test_upstream_429_as_server_error_fails_over(self):
        class Provider:
            id = "test"

            async def chat_stream(self, account, *_args, **_kwargs):
                if account.id == "first":
                    raise ProviderServerError("busy", 429)
                yield {"type": "text", "text": "ok"}

        chunks = self._run(Provider(), [Account("test", {}, id="first"), Account("test", {}, id="second")])
        self.assertEqual(chunks[0]["choices"][0]["delta"]["content"], "ok")


class OpenAIProtocolTests(unittest.TestCase):
    def test_n_greater_than_one_is_rejected(self):
        async def run():
            with patch("app.openai_api._check_auth", return_value=None):
                return await chat_completions(make_request({
                    "model": "m", "n": 2, "messages": [{"role": "user", "content": "x"}]}))
        self.assertEqual(asyncio.run(run()).status_code, 400)

    def test_include_usage_sends_trailing_usage_chunk(self):
        async def run():
            with patch("app.openai_api._check_auth", return_value=None), \
                    patch("app.openai_api.chat_service.stream_completion", new=_chunks()), \
                    patch.object(state, "commit_trace"):
                response = await chat_completions(make_request({
                    "model": "m", "stream": True, "stream_options": {"include_usage": True},
                    "messages": [{"role": "user", "content": "x"}]}))
                return await _body(response)
        text = asyncio.run(run())
        events = [json.loads(line[6:]) for line in text.splitlines()
                  if line.startswith("data: {")]
        self.assertEqual(events[0]["choices"][0]["delta"]["role"], "assistant")
        self.assertEqual(events[-1]["choices"], [])
        self.assertEqual(events[-1]["usage"]["total_tokens"], 5)
        self.assertIsNone(events[-2]["usage"])
        self.assertTrue(text.rstrip().endswith("data: [DONE]"))


class ResponsesProtocolTests(unittest.TestCase):
    def test_length_finish_is_incomplete(self):
        async def run():
            with patch("app.responses_api._check_auth", return_value=None), \
                    patch("app.responses_api.chat_service.stream_completion", new=_chunks("length")), \
                    patch.object(state, "commit_trace"):
                return await responses(make_request({"model": "m", "input": "x"}))
        result = asyncio.run(run())
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["incomplete_details"], {"reason": "max_output_tokens"})


class AuthTests(unittest.TestCase):
    def test_non_ascii_key_is_rejected_not_crashing(self):
        with patch.object(config_store.settings, "api_keys", ["secret"]):
            self.assertFalse(config_store.check_api_key("ключ"))
            self.assertTrue(config_store.check_api_key("secret"))

    def test_browser_with_foreign_host_is_rejected_without_key(self):
        with patch.object(config_store.settings, "api_keys", []), \
                patch.object(config_store, "_runtime_host", "127.0.0.1"):
            self.assertFalse(config_store.authorize_api_request(
                None, host="evil.example:8317", fetch_site="same-origin"))
            self.assertTrue(config_store.authorize_api_request(None, host="evil.example:8317"))
            self.assertTrue(config_store.authorize_api_request(
                None, host="127.0.0.1:8317", fetch_site="none"))

    def test_public_store_find_accepts_non_ascii_probe(self):
        from app.public_api import Store
        store = Store()
        store._data = {"keys": [{"key": "mpk-" + "a" * 30, "id": "x"}]}
        self.assertIsNone(store.find("ключ"))
        self.assertEqual(store.find("mpk-" + "a" * 30)["id"], "x")


if __name__ == "__main__":
    unittest.main()
