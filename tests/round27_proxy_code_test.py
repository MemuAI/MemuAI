"""Round 27 bug hunt: MemuAI Proxy and MemuCode (tests without network)."""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from starlette.requests import Request

ROOT = Path(__file__).resolve().parents[1]

def _request(body, *, hang_up_after: float | None = None, path: str = "/", headers=None):
    """An ASGI request whose client stays connected (a real server blocks in receive) or hangs up."""
    payload = json.dumps(body).encode()
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": payload, "more_body": False}
        if hang_up_after is None:
            await asyncio.Event().wait()
        await asyncio.sleep(hang_up_after)
        return {"type": "http.disconnect"}

    extra = [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()]
    return Request({"type": "http", "method": "POST", "path": path,
                    "headers": [(b"content-type", b"application/json"), *extra]}, receive)

class ClientDisconnectTests(unittest.TestCase):
    def test_work_is_cancelled_when_the_client_hangs_up(self):
        from app.state import until_client_disconnects

        async def run():
            cancelled = asyncio.Event()

            async def endless():
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:
                    cancelled.set()
                    raise

            response = await asyncio.wait_for(
                until_client_disconnects(_request({}, hang_up_after=0.05), endless()), 5)
            return response, cancelled.is_set()

        response, cancelled = asyncio.run(run())
        self.assertEqual(response.status_code, 499)
        self.assertTrue(cancelled)

    def test_finished_work_is_returned_and_the_watcher_stops(self):
        from app.state import until_client_disconnects

        async def run():
            async def quick():
                await asyncio.sleep(0.01)
                return {"ok": True}
            return await asyncio.wait_for(until_client_disconnects(_request({}), quick()), 5)

        self.assertEqual(asyncio.run(run()), {"ok": True})

    def test_non_stream_chat_stops_reading_the_upstream(self):
        """/v1/chat/completions without stream: the provider generator is closed when the client leaves."""
        from app import openai_api
        from app.config import config_store

        async def run():
            closed = asyncio.Event()

            async def slow(*_args, **_kwargs):
                try:
                    yield {"id": "x", "choices": [{"delta": {"content": "a"}}]}
                    await asyncio.sleep(60)
                    yield {"id": "x", "choices": [{"delta": {"content": "b"}}]}
                finally:
                    closed.set()

            with patch.object(config_store, "authorize_api_request", return_value=True), \
                    patch.object(openai_api.chat_service, "stream_completion", slow):
                response = await asyncio.wait_for(openai_api.chat_completions(_request(
                    {"model": "m", "messages": [{"role": "user", "content": "hi"}]}, hang_up_after=0.1)), 5)
            await asyncio.wait_for(closed.wait(), 2)
            return response

        self.assertEqual(asyncio.run(run()).status_code, 499)

class CountTokensTests(unittest.TestCase):
    def test_count_tokens_route_estimates_messages_system_and_tools(self):
        from app import anthropic_api
        from app.config import config_store
        body = {"model": "m", "system": "You are helpful", "messages": [
            {"role": "user", "content": "hello " * 200}],
            "tools": [{"name": "read", "description": "Read a file", "input_schema": {"type": "object"}}]}

        async def run(payload):
            with patch.object(config_store, "authorize_api_request", return_value=True):
                return await anthropic_api.count_tokens(_request(payload))

        result = asyncio.run(run(body))
        self.assertGreater(result["input_tokens"], 200)
        without_tools = asyncio.run(run({k: v for k, v in body.items() if k != "tools"}))
        self.assertGreater(result["input_tokens"], without_tools["input_tokens"])
        self.assertEqual(asyncio.run(run({"model": "m"})).status_code, 400)

    def test_route_is_registered_on_both_listeners(self):
        from app.anthropic_api import router
        self.assertIn("/v1/messages/count_tokens", {route.path for route in router.routes})
        source = (ROOT / "app" / "public_api.py").read_text(encoding="utf-8")
        self.assertIn('@app.post("/v1/messages/count_tokens")', source)

class LoopbackTests(unittest.TestCase):
    CASES = {
        "http://127.0.0.1:8080/v1": True, "http://localhost:1234/v1": True, "http://[::1]:11434": True,
        "http://127.4.5.6/v1": True, "http://lmstudio.localhost/v1": True,
        "https://127.0.0.1.nip.io/v1": False, "https://127.example.com/v1": False,
        "https://api.openai.com/v1": False, "http://192.168.1.5:1234/v1": False, "": False, "http://[bad": False,
    }

    def test_only_literal_loopback_addresses_bypass_the_proxy(self):
        from app.cli.llm import _is_loopback_url
        from app.providers.http import is_loopback
        for url, expected in self.CASES.items():
            self.assertEqual(is_loopback(url), expected, url)
            self.assertEqual(_is_loopback_url(url), expected, url)

    def test_custom_connection_on_this_computer_ignores_the_default_proxy(self):
        from app.config import config_store
        from app.providers.custom import CustomProvider
        provider = CustomProvider()
        local = SimpleNamespace(proxy="", fields={"base_url": "http://127.0.0.1:1234/v1"})
        remote = SimpleNamespace(proxy="", fields={"base_url": "https://api.example.com/v1"})
        own = SimpleNamespace(proxy="http://own:1", fields={"base_url": "http://127.0.0.1:1234/v1"})
        with patch.object(config_store.settings, "default_proxy", "http://corp:3128"):
            self.assertEqual(provider.proxy_for(local), "")
            self.assertEqual(provider.proxy_for(remote), "http://corp:3128")
            self.assertEqual(provider.proxy_for(own), "http://own:1")

    def test_passthrough_uses_the_custom_transport(self):
        from app import passthrough
        from app.config import config_store
        from app.providers import http as hub_http
        from app.providers import init_registry
        init_registry()
        account = SimpleNamespace(id="a", provider="custom", enabled=True, proxy="",
                                  fields={"name": "lm", "base_url": "http://127.0.0.1:1234/v1"})
        seen = {}

        async def fake_session(proxy="", *, impersonate=True, url=""):
            seen.update(proxy=proxy, impersonate=impersonate, url=url)
            raise RuntimeError("stop here")

        async def run():
            request = Request({"type": "http", "method": "GET", "path": "/p/lm/models", "headers": [],
                               "query_string": b""}, lambda: asyncio.sleep(0, {"type": "http.request", "body": b""}))
            with patch.object(config_store, "authorize_api_request", return_value=True), \
                    patch.object(passthrough, "_find_custom", return_value=account), \
                    patch.object(config_store.settings, "default_proxy", "http://corp:3128"), \
                    patch.object(hub_http, "get_session", fake_session):
                return await passthrough.passthrough("lm", "models", request)

        response = asyncio.run(run())
        self.assertEqual(response.status_code, 502)
        self.assertEqual(seen, {"proxy": "", "impersonate": False, "url": "http://127.0.0.1:1234/v1"})

class AccountStatusLanguageTests(unittest.TestCase):
    def test_success_status_is_russian_with_an_english_phrase(self):
        from app.pool import pool
        from app.state import Account
        account = Account(id="t1", provider="custom", fields={"name": "x"})
        with patch("app.state.state.save_accounts"), patch("app.state.state.save_accounts_soon"):
            pool.report_success(account, ms=5)
        self.assertEqual(account.status_message, "ответил успешно")
        app_js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('["ответил успешно", "answered successfully"]', app_js)

@unittest.skipUnless(shutil.which("node"), "node is not installed")
class LowerBoundCostTests(unittest.TestCase):
    def test_incomplete_tiny_cost_is_a_readable_lower_bound(self):
        source = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
        function = re.search(r"function fmtUsd\(.*?\n}\n", source, re.S).group(0)
        script = ("const locale = () => 'en-US';\n" + function +
                  "console.log(JSON.stringify([fmtUsd(0.000094, true), fmtUsd(0.000094), fmtUsd(1.5, true),"
                  " fmtUsd(0, true)]));")
        out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out, ["≥ $0.000094", "<$0.0001", "≥ $1.5", "$0"])
        self.assertNotIn('"≥ ") + fmtUsd', source)

class MemuCodeProxyUsageTests(unittest.TestCase):
    def test_proxy_stream_keeps_cached_reasoning_and_billed_cost(self):
        from app.cli.llm import ChatRequest, ProxyBackend
        usage = {"prompt_tokens": 1041, "completion_tokens": 20, "estimated": False, "cost": 0.0021,
                 "prompt_tokens_details": {"cached_tokens": 1024},
                 "completion_tokens_details": {"reasoning_tokens": 7}}
        lines = [{"choices": [{"delta": {"content": "hi"}}]},
                 {"choices": [{"delta": {}, "finish_reason": "stop"}]},
                 {"choices": [], "usage": usage}]
        body = "".join(f"data: {json.dumps(line)}\n\n" for line in lines) + "data: [DONE]\n\n"

        async def run():
            backend = ProxyBackend({"proxy_url": "http://127.0.0.1:9"})
            backend._client = httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})))
            events = [event async for event in backend.stream(ChatRequest(model="m", system="", messages=[
                {"role": "user", "content": "hi"}]))]
            await backend.close()
            return [event.usage for event in events if event.kind == "usage"]

        (got,) = asyncio.run(run())
        self.assertEqual((got.input, got.output, got.cache_read, got.reasoning, got.estimated),
                         (1041, 20, 1024, 7, False))
        self.assertAlmostEqual(got.cost, 0.0021)

if __name__ == "__main__":
    unittest.main()

class RetryAfterTests(unittest.TestCase):
    """HTTP 429 with Retry-After: the Proxy waits out a short pause and passes a long one to the client."""

    def _complete(self, retry_after, *, accounts=("only",)):
        from app.chat import ChatService, CompletionError
        from app.config import config_store
        from app.providers.base import ProviderRateLimitError
        from app.routing import Resolved
        from app.state import Account

        calls = []

        class Provider:
            id = "test"

            async def chat_stream(self, account, *_args, **_kwargs):
                calls.append((account.id, asyncio.get_running_loop().time()))
                raise ProviderRateLimitError("Upstream rate limit (HTTP 429)", retry_after=retry_after)
                yield {}

        async def run():
            service = ChatService()
            started = asyncio.get_running_loop().time()
            with patch.object(config_store.settings, "proxy_enabled", True), \
                    patch.object(config_store.settings, "retry_delay_ms", 50), \
                    patch("app.chat.resolve", return_value=Resolved(None, "test", "model", "test")), \
                    patch.object(service, "_provider", return_value=Provider()), \
                    patch("app.chat.pool.ordered_candidates",
                          return_value=[Account("test", {}, id=name) for name in accounts]), \
                    patch("app.chat.pool.report_success"), patch("app.chat.pool.report_failure"):
                try:
                    async for _chunk in service.stream_completion("m", [{"role": "user", "content": "x"}], {}):
                        pass
                except CompletionError as exc:
                    return exc, [t - started for _name, t in calls], [name for name, _t in calls]
            raise AssertionError("no error")

        return asyncio.run(run())

    def test_retry_after_header_parsing(self):
        from email.utils import formatdate
        import time
        from app.providers.base import retry_after_seconds
        self.assertEqual(retry_after_seconds({"retry-after": "7"}), 7.0)
        self.assertEqual(retry_after_seconds({"retry-after-ms": "1500", "retry-after": "9"}), 1.5)
        self.assertAlmostEqual(retry_after_seconds({"retry-after": formatdate(time.time() + 30, usegmt=True)}), 30, delta=2)
        self.assertEqual(retry_after_seconds({"retry-after": "-5"}), 0.0)
        for headers in ({}, None, {"retry-after": "soon"}, {"retry-after": "nan"}):
            self.assertIsNone(retry_after_seconds(headers), headers)

    def test_short_pause_is_waited_out_before_the_retry(self):
        exc, times, _ = self._complete(0.4)
        self.assertEqual(len(times), 2)
        self.assertGreaterEqual(times[1] - times[0], 0.38)
        self.assertEqual(exc.status, 429)
        self.assertEqual(exc.headers, {"Retry-After": "1"})

    def test_long_pause_goes_to_the_client_without_a_blind_retry(self):
        exc, times, _ = self._complete(60)
        self.assertEqual(len(times), 1)
        self.assertLess(times[0], 1)
        self.assertEqual(exc.status, 429)
        self.assertEqual(exc.err_type, "rate_limit_error")
        self.assertIn(exc.headers["Retry-After"], {"59", "60"})

    def test_unknown_pause_keeps_the_configured_retry_delay(self):
        exc, times, _ = self._complete(None)
        self.assertEqual(len(times), 2)
        self.assertIsNone(exc.headers)

    def test_each_account_is_retried_only_after_its_own_pause(self):
        exc, _times, names = self._complete(60, accounts=("a", "b"))
        self.assertEqual(names, ["a", "b"])
        self.assertIn(exc.headers["Retry-After"], {"59", "60"})

    def test_rate_limit_header_reaches_every_api_shape(self):
        from app import anthropic_api, openai_api, responses_api
        from app.chat import CompletionError
        from app.config import config_store

        def failing(*_args, **_kwargs):
            async def gen():
                raise CompletionError("limit", status=429, err_type="rate_limit_error", retry_after=42)
                yield {}
            return gen()

        async def run(handler, body):
            with patch.object(config_store, "authorize_api_request", return_value=True), \
                    patch("app.chat.chat_service.stream_completion", failing):
                return await handler(_request(body))

        chat = {"model": "m", "max_tokens": 5, "messages": [{"role": "user", "content": "hi"}]}
        for handler, body in ((openai_api.chat_completions, chat), (openai_api.chat_completions, {**chat, "stream": True}),
                              (anthropic_api.messages, chat), (anthropic_api.messages, {**chat, "stream": True}),
                              (responses_api.responses, {"model": "m", "input": "hi"}),
                              (responses_api.responses, {"model": "m", "input": "hi", "stream": True})):
            response = asyncio.run(run(handler, body))
            self.assertEqual(response.status_code, 429, (handler.__module__, body.get("stream")))
            self.assertEqual(response.headers.get("retry-after"), "42", (handler.__module__, body.get("stream")))

    def test_custom_provider_reads_the_upstream_header(self):
        from app.providers.base import ProviderRateLimitError
        from app.providers.custom import CustomProvider
        with self.assertRaises(ProviderRateLimitError) as caught:
            asyncio.run(CustomProvider()._raise_for_status(429, "slow down", retry_after=3.0))
        self.assertEqual(caught.exception.retry_after, 3.0)
        source = (ROOT / "app" / "passthrough.py").read_text(encoding="utf-8")
        self.assertIn('"retry-after"', source)

class RevokedKeyTests(unittest.TestCase):
    """The only account's key is revoked (HTTP 401): one attempt, a clear error, the account marked."""

    def test_single_revoked_key_fails_at_once_and_marks_the_account(self):
        from app.chat import ChatService, CompletionError
        from app.config import config_store
        from app.providers.base import ProviderAuthError
        from app.routing import Resolved
        from app.state import ST_INVALID, Account

        account = Account("test", {"name": "rv"}, label="rv")
        calls = []

        class Provider:
            id = "test"

            async def chat_stream(self, *_args, **_kwargs):
                calls.append(1)
                raise ProviderAuthError('The API key was rejected (HTTP 401): {"error":{"message":"invalid api key"}}')
                yield {}

        async def run():
            service = ChatService()
            started = asyncio.get_running_loop().time()
            with patch.object(config_store.settings, "proxy_enabled", True), \
                    patch.object(config_store.settings, "retry_delay_ms", 1500), \
                    patch("app.chat.resolve", return_value=Resolved(None, "test", "model", "test")), \
                    patch.object(service, "_provider", return_value=Provider()), \
                    patch("app.chat.pool.ordered_candidates", return_value=[account]), \
                    patch("app.state.state.save_accounts"), patch("app.state.state.save_accounts_soon"):
                with self.assertRaises(CompletionError) as caught:
                    async for _chunk in service.stream_completion("m", [{"role": "user", "content": "x"}], {}):
                        pass
            return caught.exception, asyncio.get_running_loop().time() - started

        exc, elapsed = asyncio.run(run())
        self.assertEqual(calls, [1])
        self.assertLess(elapsed, 1.0)
        self.assertEqual(exc.status, 503)
        self.assertEqual(exc.err_type, "authentication_error")
        self.assertIn("invalid api key", exc.message)
        self.assertIn("invalid api key", exc.message_en)
        self.assertTrue(exc.hint and exc.hint_en and exc.hint != exc.hint_en)
        self.assertEqual(account.status, ST_INVALID)
        self.assertNotIn("{", account.status_message)
        self.assertIn("invalid api key", account.status_message)

class ErrorLanguageTests(unittest.TestCase):
    """Provider errors: Russian text fully Russian, English text fully English, API clients get English."""

    def test_russian_text_translates_provider_phrases(self):
        from app.chat import CompletionError
        exc = CompletionError("Все аккаунты «custom» недоступны (rv): The API key was rejected (HTTP 401): invalid api key",
                              status=503, err_type="authentication_error",
                              message_en="Every “custom” account failed (rv): The API key was rejected (HTTP 401): invalid api key")
        self.assertEqual(exc.message, "Все аккаунты «custom» недоступны (rv): API-ключ отклонён (HTTP 401): invalid api key")
        self.assertNotIn("Все", exc.message_en)
        plain = CompletionError("Upstream rate limit (HTTP 429)", status=429)
        self.assertEqual(plain.message, "Апстрим ограничил частоту запросов (HTTP 429)")
        self.assertEqual(plain.message_en, "Upstream rate limit (HTTP 429)")

    def test_english_provider_messages_have_no_cyrillic_units(self):
        for name in ("custom.py", "kiro.py"):
            source = (ROOT / "app" / "providers" / name).read_text(encoding="utf-8")
            self.assertNotIn("no data for over {config_store.settings.stream_idle_timeout} с", source)

    def test_api_clients_read_english_and_memuai_windows_their_language(self):
        from app import anthropic_api, openai_api
        from app.chat import CompletionError
        from app.config import config_store

        def failing(*_args, **_kwargs):
            async def gen():
                raise CompletionError("Все аккаунты «custom» недоступны (rv): The API key was rejected (HTTP 401)",
                                      status=503, err_type="authentication_error",
                                      message_en="Every “custom” account failed (rv): The API key was rejected (HTTP 401)")
                yield {}
            return gen()

        async def run(handler, headers):
            with patch.object(config_store, "authorize_api_request", return_value=True), \
                    patch("app.chat.chat_service.stream_completion", failing):
                response = await handler(_request(
                    {"model": "m", "max_tokens": 5, "messages": [{"role": "user", "content": "hi"}]}, headers=headers))
            return json.loads(response.body)["error"]

        for handler in (openai_api.chat_completions, anthropic_api.messages):
            english = asyncio.run(run(handler, {}))
            self.assertTrue(english["message"].startswith("Every “custom” account failed"), english)
            self.assertIn("API-ключ отклонён", english["message_ru"])
            if "hint" in english:
                self.assertNotRegex(english["hint"], "[а-яА-Я]")
                self.assertRegex(english["hint_ru"], "[а-яА-Я]")
            russian = asyncio.run(run(handler, {"X-MemuAI-Lang": "ru"}))
            self.assertTrue(russian["message"].startswith("Все аккаунты"), russian)
            self.assertNotIn("rejected", russian["message"])
            self.assertTrue(russian["message_en"].startswith("Every"))

    def test_memuai_chat_asks_the_proxy_for_its_interface_language(self):
        source = (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8")
        self.assertIn('"X-MemuAI-Lang": "en" if load_preferences().get("language") == "en" else "ru"', source)
