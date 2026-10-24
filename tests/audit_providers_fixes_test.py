"""Offline regressions for the provider-layer audit (app/providers, pricing, model_info)."""
from __future__ import annotations

import asyncio
import os
import time
import unittest
from unittest.mock import patch

from app.providers import http
from app.providers.antigravity import AntigravityProvider
from app.providers.base import (
    ProviderRateLimitError,
    ProviderServerError,
)
from app.providers.genspark import GensparkProvider, conversations
from app.providers.grok import GrokProvider
from app.providers.gs.client import GensparkClient
from app.providers.kiro import KiroProvider
from app.state import Account, state


class FakeResponse:
    def __init__(self, status=200, lines=(), payload=None, text=""):
        self.status_code = status
        self.lines = list(lines)
        self.payload = payload
        self.text = text
        self.closed = False
        self.headers = {"content-type": "text/event-stream"}

    async def aiter_lines(self):
        for line in self.lines:
            yield line

    async def aiter_content(self):
        if False:
            yield b""

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload

    async def aclose(self):
        self.closed = True


class CloseResponseTest(unittest.TestCase):
    def test_an_abandoned_curl_stream_is_aborted_not_drained(self):
        """curl_cffi's aclose() awaits the whole transfer; close_response must not."""
        os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")

        async def handler(reader, writer):
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                         b"Transfer-Encoding: chunked\r\n\r\n")
            try:
                for _ in range(100):           # a «generation» of ~20 s
                    writer.write(b"7\r\ndata:x\n\r\n")
                    await writer.drain()
                    await asyncio.sleep(0.2)
                writer.write(b"0\r\n\r\n")
                await writer.drain()
            except (ConnectionError, OSError):
                pass
            finally:
                writer.close()

        async def run():
            server = await asyncio.start_server(handler, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            try:
                session = await http.get_session("", impersonate=False)
                resp = await session.get(f"http://127.0.0.1:{port}/", stream=True, timeout=60)
                lines = http.aiter_lines_str(resp)
                self.assertEqual(await anext(lines), "data:x")
                await lines.aclose()
                started = time.monotonic()
                await http.close_response(resp)
                return time.monotonic() - started
            finally:
                server.close()
                await http.close_all()

        elapsed = asyncio.run(run())
        self.assertLess(elapsed, 3.0, "closing a stream must not wait for the upstream to finish")

    def test_plain_responses_are_still_closed(self):
        response = FakeResponse()
        asyncio.run(http.close_response(response))
        self.assertTrue(response.closed)
        asyncio.run(http.close_response(None))


class KiroHistoryTest(unittest.TestCase):
    def _body_for(self, messages):
        captured = {}

        class Session:
            async def post(self, _url, **kwargs):
                captured["body"] = kwargs.get("json")
                return FakeResponse(status=429, text="ThrottlingException")

        account = Account("kiro", {"region": "us-east-1"}, extra={"tokens": {
            "accessToken": "token", "expiresAt": time.time() + 3600}})

        async def run():
            with patch("app.providers.kiro.http.get_session", return_value=Session()):
                with self.assertRaises(ProviderRateLimitError):
                    await anext(KiroProvider().chat_stream(account, "m", messages, {}))

        asyncio.run(run())
        return captured["body"]["conversationState"]

    def test_history_alternates_and_never_ends_with_the_user(self):
        state_ = self._body_for([
            {"role": "system", "content": "sys"},
            {"role": "assistant", "content": "Hello, how can I help?"},
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": ""},
            {"role": "user", "content": "<files>context</files>"},
            {"role": "user", "content": "the question"},
        ])
        roles = [next(iter(item)) for item in state_["history"]]
        self.assertEqual(roles[0], "userInputMessage")
        self.assertEqual(roles[-1], "assistantResponseMessage")
        for left, right in zip(roles, roles[1:]):
            self.assertNotEqual(left, right, roles)
        for item in state_["history"]:
            self.assertTrue(next(iter(item.values()))["content"].strip())
        current = state_["currentMessage"]["userInputMessage"]["content"]
        self.assertIn("<files>context</files>", current)
        self.assertIn("the question", current)
        self.assertIn("sys", current)

    def test_simple_dialogue_is_unchanged(self):
        state_ = self._body_for([
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
        ])
        self.assertEqual(state_["history"], [
            {"userInputMessage": {"content": "a", "origin": "AI_EDITOR"}},
            {"assistantResponseMessage": {"content": "b"}},
        ])
        self.assertEqual(state_["currentMessage"]["userInputMessage"]["content"], "c")

    def test_millisecond_expiry_is_understood(self):
        future_ms = (time.time() + 3600) * 1000
        self.assertAlmostEqual(KiroProvider._expires_at({"expiresAt": future_ms}), future_ms / 1000, delta=1)
        self.assertEqual(KiroProvider._expires_at({"expiresAt": 1_700_000_000}), 1_700_000_000.0)

    def test_refresh_without_access_token_is_not_saved(self):
        class Session:
            async def post(self, *_args, **_kwargs):
                return FakeResponse(status=200, payload={"error": "nope"})

        account = Account("kiro", {}, extra={"tokens": {"refreshToken": "r", "expiresAt": 1}})

        async def run():
            with patch("app.providers.kiro.http.get_session", return_value=Session()):
                await KiroProvider()._refresh_token(account, dict(account.extra["tokens"]))

        with self.assertRaises(ProviderServerError):
            asyncio.run(run())
        self.assertEqual(account.extra["tokens"]["refreshToken"], "r")


class AntigravityFixesTest(unittest.TestCase):
    def test_in_stream_429_is_a_rate_limit_and_bad_shapes_do_not_crash(self):
        with self.assertRaises(ProviderRateLimitError):
            AntigravityProvider._parse_chunk({"error": {"code": 429, "message": "Too many",
                                                       "status": "RESOURCE_EXHAUSTED"}})
        with self.assertRaises(ProviderServerError) as caught:
            AntigravityProvider._parse_chunk({"error": "boom"})
        self.assertEqual(caught.exception.status, 502)
        with self.assertRaises(ProviderServerError) as caught:
            AntigravityProvider._parse_chunk({"error": {"code": 403, "message": "denied"}})
        self.assertEqual(caught.exception.status, 502)
        with self.assertRaises(ProviderServerError) as caught:
            AntigravityProvider._parse_chunk({"error": {"code": 400, "message": "bad"}})
        self.assertEqual(caught.exception.status, 400)

    def _refresh(self, payload):
        class Session:
            async def post(self, *_args, **_kwargs):
                return FakeResponse(status=200, payload=payload)

        account = Account("antigravity", {}, extra={"oauth": {
            "access_token": "", "refresh_token": "old", "expires_at": 0}})

        async def run():
            with patch("app.providers.antigravity.http.get_session", return_value=Session()), \
                    patch.object(state, "save_accounts"):
                return await AntigravityProvider()._ensure_token(account)

        return account, asyncio.run(run())

    def test_rotated_refresh_token_is_kept(self):
        account, token = self._refresh({"access_token": "new", "refresh_token": "rotated", "expires_in": 3600})
        self.assertEqual(token, "new")
        self.assertEqual(account.extra["oauth"]["refresh_token"], "rotated")

    def test_refresh_without_token_raises(self):
        with self.assertRaises(ProviderServerError):
            self._refresh({"expires_in": 3600})


class GrokFixesTest(unittest.TestCase):
    def test_server_trouble_on_auth_session_does_not_kill_the_account(self):
        class Session:
            def __init__(self, status):
                self.status = status

            async def get(self, *_args, **_kwargs):
                return FakeResponse(status=self.status)

        account = Account("grok", {"cookies": "sso=abc"})
        for status, error in ((503, ProviderServerError), (429, ProviderRateLimitError)):
            async def run():
                with patch("app.providers.grok.http.get_session", return_value=Session(status)):
                    await GrokProvider()._user_id(account)
            with self.assertRaises(error):
                asyncio.run(run())


class GensparkFixesTest(unittest.TestCase):
    def test_fingerprint_is_restored_when_every_fallback_gets_403(self):
        client = GensparkClient("https://api.test", impersonate="chrome136")
        ua = client.user_agent
        responses = []

        async def request_once(*_args, **_kwargs):
            response = FakeResponse(status=403)
            responses.append(response)
            return response

        client._request_once = request_once
        result = asyncio.run(client._request("GET", "/x"))
        self.assertEqual(result.status_code, 403)
        self.assertEqual((client.impersonate, client.user_agent), ("chrome136", ua))
        self.assertTrue(all(response.closed for response in responses[:-1]))

    def test_documents_do_not_break_server_side_context(self):
        provider = GensparkProvider()
        uploads = []
        modes = []

        async def upload(_cookie, _url, name, **_kwargs):
            uploads.append(name)
            return {"type": "private_file", "private_file": {
                "name": name, "private_storage_url": f"https://storage/{len(uploads)}"}}

        async def stream_chat(_cookie, payload, **_kwargs):
            modes.append(payload.get("project_id"))
            return FakeResponse(lines=[
                'data: {"type":"project_start","id":"proj-1"}',
                'data: {"type":"message_field_delta","field_name":"content","delta":"answer"}',
                'data: {"type":"message_result","content":"answer"}',
            ])

        provider.client.upload_private_file = upload
        provider.client.stream_chat = stream_chat
        account = Account("genspark", {"cookie": "abc"})
        doc = {"type": "image_url", "image_url": {"url": "data:application/pdf;base64,JVBERg=="},
               "filename": "a.pdf"}
        first = [{"role": "user", "content": [doc, {"type": "text", "text": "read it"}]}]

        async def run():
            conversations._items.clear()
            out1 = [e async for e in provider.chat_stream(account, "gpt-5.4", first, {})]
            second = first + [{"role": "assistant", "content": "answer"},
                              {"role": "user", "content": "and now?"}]
            out2 = [e async for e in provider.chat_stream(account, "gpt-5.4", second, {})]
            return out1, out2

        with patch("app.providers.genspark.config_store.provider_cfg",
                   return_value={"context_mode": "auto", "auto_delete_chats": "false"}):
            out1, out2 = asyncio.run(run())
        self.assertEqual(out1[-1]["text"], "answer")
        self.assertEqual(modes, [None, "proj-1"], "the second turn must continue the Genspark project")


class PricingFixesTest(unittest.TestCase):
    def test_version_first_claude_names_get_their_own_price(self):
        from app.model_info import lookup_known
        from app.pricing import lookup
        haiku = lookup("genspark-claude-4-5-haiku")
        self.assertEqual((haiku.input, haiku.output), (1.0, 5.0))
        self.assertEqual(lookup_known("claude-4-5-haiku")["output"], 64_000)
        self.assertIsNotNone(lookup("claude-4-opus"))
        self.assertIsNotNone(lookup("claude-4-sonnet"))
        self.assertEqual(lookup("claude-3-haiku").input, 0.25)


if __name__ == "__main__":
    unittest.main()
