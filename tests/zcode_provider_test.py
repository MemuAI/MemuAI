"""ZCode provider: the client identity, the Anthropic stream relay, quotas and the browser sign-in.

Everything runs against a local fake of the ZCode origin (``MEMUAI_ZCODE_ORIGIN``); no network.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import providers, quota_planner, tool_protocol
from app.providers import zcode, zcode_identity
from app.providers.base import (ProviderAuthError, ProviderModelUnavailableError, ProviderQuotaError,
                                ProviderRateLimitError)
from app.state import Account, ModelEntry, state

ROOT = Path(__file__).resolve().parents[1]

def b64(data) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

def jwt(sub="user-1", email="dev@example.com", exp=None) -> str:
    claims = {"sub": sub, "email": email, "exp": int(exp if exp is not None else time.time() + 3600)}
    return f"{b64({'alg': 'HS256', 'typ': 'JWT'})}.{b64(claims)}.c2lnbmF0dXJl"

def sse(*events) -> bytes:
    return b"".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events)

def stream_answer(*, tool=False, stop="end_turn") -> bytes:
    events = [{"type": "message_start", "message": {"usage": {"input_tokens": 11, "cache_read_input_tokens": 100,
                                                               "cache_creation_input_tokens": 5}}},
              {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
              {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "hmm"}},
              {"type": "content_block_stop", "index": 0},
              {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
              {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Привет"}},
              {"type": "content_block_stop", "index": 1}]
    if tool:
        events += [{"type": "content_block_start", "index": 2,
                    "content_block": {"type": "tool_use", "id": "toolu_1", "name": "get_weather", "input": {}}},
                   {"type": "content_block_delta", "index": 2,
                    "delta": {"type": "input_json_delta", "partial_json": '{"city": '}},
                   {"type": "content_block_delta", "index": 2,
                    "delta": {"type": "input_json_delta", "partial_json": '"Рим"}'}},
                   {"type": "content_block_stop", "index": 2}]
    events += [{"type": "message_delta", "delta": {"stop_reason": "tool_use" if tool else stop},
                "usage": {"output_tokens": 7}},
               {"type": "message_stop"}]
    return sse(*events)

class FakeOrigin:
    """The few ZCode endpoints MemuAI calls, with a queue of chat answers."""

    def __init__(self) -> None:
        self.requests = []
        self.chat = []
        self.balance = {"plans": [{"plan_id": "lite", "name": "Lite", "ends_at": int(time.time() + 30 * 86400)}],
                        "balances": [{"show_name": "GLM-5.3", "remaining_units": 40, "used_units": 60,
                                      "total_units": 100, "period_end": int(time.time() + 7200) * 1000},
                                     {"show_name": "GLM-5.3-Flash", "remaining_units": 5, "used_units": 0,
                                      "total_units": 5, "period_end": int(time.time() + 7200)}]}
        self.polls = 0
        self.login_token = jwt()
        origin = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def reply(self, status, body, ctype="application/json", headers=None):
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(data)

            def record(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                origin.requests.append({"method": self.command, "path": self.path, "headers": dict(self.headers),
                                        "body": json.loads(raw) if raw else None})

            def do_POST(self):
                self.record()
                if self.path == zcode.MESSAGES_PATH:
                    status, body, ctype, headers = origin.chat.pop(0) if origin.chat else (
                        200, stream_answer(), "text/event-stream", {})
                    return self.reply(status, body, ctype, headers)
                if self.path == zcode.OAUTH_INIT_PATH:
                    return self.reply(200, {"code": 0, "data": {"flow_id": "remote-1",
                                                                "authorize_url": "https://chat.z.ai/cli?flow=remote-1"}})
                self.reply(404, {"code": 404})

            def do_GET(self):
                self.record()
                if self.path.startswith(zcode.BALANCE_PATH):
                    return self.reply(200, {"code": 0, "data": origin.balance})
                if self.path.startswith(zcode.PREVIEW_PATH):
                    return self.reply(200, {"code": 0, "data": {"plans": [{"plan_id": "free", "name": "Free"}]}})
                if self.path.startswith(zcode.CONFIGS_PATH):
                    return self.reply(200, {"data": {"providers": [{"models": [
                        {"modelId": "GLM-5.3", "name": "GLM-5.3", "contextWindow": 200000,
                         "maxCompletionTokens": 131072}]}]}})
                if self.path.startswith(zcode.OAUTH_POLL_PATH):
                    origin.polls += 1
                    if origin.polls < 2:
                        return self.reply(200, {"code": 0, "data": {"status": "pending"}})
                    return self.reply(200, {"code": 0, "data": {"status": "ready", "token": origin.login_token,
                                                                "user": {"email": "dev@example.com"}}})
                self.reply(404, {"code": 404})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def chats(self):
        return [r for r in self.requests if r["path"] == zcode.MESSAGES_PATH]

    def close(self):
        self.server.shutdown()
        self.server.server_close()

class ZCodeTestCase(unittest.TestCase):
    def setUp(self):
        self.origin = FakeOrigin()
        self.addCleanup(self.origin.close)
        for item in (patch.dict(os.environ, {"MEMUAI_ZCODE_ORIGIN": self.origin.url,
                                             "MEMUAI_ZCODE_POLL_INTERVAL": "0.05"}),
                     patch.object(zcode, "_sleep", new=AsyncMock()),
                     patch.object(state, "save_accounts_soon"), patch.object(state, "save_accounts"),
                     patch.object(state, "save_models")):
            item.start()
            self.addCleanup(item.stop)
        self.provider = zcode.ZCodeProvider()
        self.account = Account("zcode", {"token": jwt()}, id="z1")

    def run_async(self, coro):
        return asyncio.run(coro)

    def collect(self, model="glm-5.3", messages=None, params=None, account=None):
        async def go():
            return [event async for event in self.provider.chat_stream(
                account or self.account, model, messages or [{"role": "user", "content": "hi"}], params or {})]
        return self.run_async(go())

class IdentityAndBodyTests(ZCodeTestCase):
    def test_pasted_tokens_are_normalized_and_expired_ones_refused(self):
        token = jwt()
        for raw in (token, f"Bearer {token}", json.dumps({"token": token}),
                    f"curl https://zcode.z.ai -H 'Authorization: Bearer {token}'"):
            with self.subTest(raw=raw[:20]):
                self.assertEqual(zcode.extract_token(raw), token)
        fields = {"token": f"  Bearer {token}\n"}
        self.assertIsNone(self.provider.validate_fields(fields))
        self.assertEqual(fields["token"], token)
        self.assertIn("истёк", self.provider.validate_fields({"token": jwt(exp=time.time() - 60)}))
        self.assertIsNotNone(self.provider.validate_fields({"token": ""}))
        self.assertEqual(zcode.token_email(token), "dev@example.com")
        self.assertEqual(zcode.device_mid(token), zcode.device_mid(jwt(email="other@example.com")))

    def test_the_body_carries_the_official_identity_and_client_instructions(self):
        messages = [{"role": "system", "content": "Отвечай кратко."},
                    {"role": "user", "content": [{"type": "text", "text": "Погода?"},
                                                 {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]},
                    {"role": "assistant", "content": None, "tool_calls": [
                        {"id": "call_1", "type": "function", "function": {"name": "get_weather",
                                                                          "arguments": '{"city":"Рим"}'}}]},
                    {"role": "tool", "tool_call_id": "call_1", "content": "+20"}]
        params = {"tools": [{"type": "function", "function": {"name": "get_weather", "description": "w",
                                                              "parameters": {"type": "object", "properties": {
                                                                  "city": {"type": "string"}}}}}],
                  "tool_choice": {"type": "function", "function": {"name": "get_weather"}},
                  "parallel_tool_calls": False, "temperature": 1.7, "stop": ["END"], "_reasoning_level": "max"}
        body = self.provider.build_body(self.account, "glm-5.3", messages, params, "mid-1")
        self.assertEqual(body["model"], "GLM-5.3")
        self.assertEqual(body["system"][0], {"type": "text", "text": zcode_identity.BLOCK_1})
        self.assertEqual(body["system"][1]["text"], zcode_identity.BLOCK_2)
        last = body["system"][2]["text"]
        self.assertNotIn(zcode_identity.CANNED_OPENER, last)
        self.assertTrue(last.endswith("Отвечай кратко."))
        self.assertIn(zcode.CLIENT_NOTE, last)
        roles = [m["role"] for m in body["messages"]]
        self.assertEqual(roles, ["user", "assistant", "user"])
        self.assertEqual(body["messages"][0]["content"][1]["source"]["media_type"], "image/png")
        self.assertEqual(body["messages"][1]["content"][0]["input"], {"city": "Рим"})
        self.assertEqual(body["messages"][2]["content"][0]["tool_use_id"], "call_1")
        self.assertEqual(body["messages"][-1]["content"][-1]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(body["tools"][0]["input_schema"]["properties"]["city"]["type"], "string")
        self.assertEqual(body["tool_choice"], {"type": "tool", "name": "get_weather", "disable_parallel_tool_use": True})
        self.assertNotIn("temperature", body, "thinking requests keep the default sampling")
        self.assertEqual(body["thinking"], {"type": "enabled"})
        self.assertEqual(body["output_config"], {"effort": "max"})
        self.assertEqual(body["stop_sequences"], ["END"])
        self.assertEqual(json.loads(body["metadata"]["user_id"])["device_id"], "mid-1")
        self.assertTrue(body["stream"])

        plain = self.provider.build_body(self.account, "glm-5.3-flash", [{"role": "user", "content": "x"}],
                                         {"temperature": 1.7}, "mid-1")
        self.assertEqual(plain["model"], "GLM-5.3-Flash")
        self.assertEqual(plain["temperature"], 1.0)
        self.assertNotIn("thinking", plain)

    def test_tool_history_without_tools_becomes_text(self):
        messages = [{"role": "user", "content": "q"},
                    {"role": "assistant", "content": "", "tool_calls": [
                        {"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]},
                    {"role": "tool", "tool_call_id": "c1", "content": "done"}]
        body = self.provider.build_body(self.account, "glm-5.3", messages, {}, "m")
        text = json.dumps(body["messages"], ensure_ascii=False)
        self.assertNotIn("tool_use", text)
        self.assertIn("done", text)

    def test_registered_in_the_proxy(self):
        self.assertIn("zcode", providers.builtin_ids())
        self.assertIn("zcode", tool_protocol.TOOL_PROVIDERS)
        self.assertIn("ZCode", tool_protocol.TOOL_PROVIDER_ERROR)
        self.assertIn("zcode", quota_planner.POLL_PROVIDERS)
        self.assertTrue((ROOT / "app" / "companion_kb" / "provider-zcode.md").is_file())
        self.assertTrue(zcode.ZCodeProvider.oauth)

class StreamTests(ZCodeTestCase):
    def test_text_thinking_tool_calls_and_usage_are_relayed(self):
        self.origin.chat.append((200, stream_answer(tool=True), "text/event-stream", {}))
        events = self.collect(params={"tools": [{"type": "function", "function": {"name": "get_weather",
                                                                                  "parameters": {}}}]})
        self.assertEqual("".join(e["text"] for e in events if e["type"] == "text"), "Привет")
        self.assertEqual("".join(e["text"] for e in events if e["type"] == "reasoning"), "hmm")
        calls = [e for e in events if e["type"] == "tool_call"]
        self.assertEqual((calls[0]["id"], calls[0]["name"]), ("toolu_1", "get_weather"))
        self.assertEqual(json.loads("".join(e["arguments"] or "" for e in calls)), {"city": "Рим"})
        usage = next(e for e in events if e["type"] == "usage")
        self.assertEqual((usage["prompt_tokens"], usage["completion_tokens"], usage["cached_tokens"]), (116, 7, 100))
        self.assertEqual(events[-1], {"type": "finish", "reason": "tool_calls"})
        sent = self.origin.chats()[0]
        headers = {k.lower(): v for k, v in sent["headers"].items()}
        self.assertEqual(headers["authorization"], "Bearer " + self.account.fields["token"])
        self.assertEqual(headers["x-api-key"], self.account.fields["token"])
        self.assertEqual(headers["x-zcode-app-version"], zcode.CLIENT_VERSION)
        self.assertEqual(headers["anthropic-beta"], zcode.ANTHROPIC_BETA)
        self.assertTrue(headers["user-agent"].startswith("ZCode/"))
        self.assertEqual(sent["body"]["model"], "GLM-5.3")

    def test_a_stream_without_its_end_is_an_error(self):
        broken = sse({"type": "message_start", "message": {"usage": {}}},
                     {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "часть"}})
        self.origin.chat.append((200, broken, "text/event-stream", {}))
        with self.assertRaises(Exception) as caught:
            self.collect()
        self.assertIn("оборвался", getattr(caught.exception, "message_ru", ""))

    def test_busy_and_dead_origin_are_retried_with_the_same_query_ids(self):
        self.origin.chat += [(429, {"code": 3010, "msg": "busy"}, "application/json", {}),
                             (503, b"upstream down", "text/plain", {}),
                             (200, stream_answer(), "text/event-stream", {})]
        events = self.collect()
        self.assertEqual("".join(e["text"] for e in events if e["type"] == "text"), "Привет")
        sent = self.origin.chats()
        self.assertEqual(len(sent), 3)
        ids = {({k.lower(): v for k, v in r["headers"].items()}["x-query-id"],
                {k.lower(): v for k, v in r["headers"].items()}["x-zcode-trace-id"]) for r in sent}
        self.assertEqual(len(ids), 1)

    def test_waf_refusal_twice_pauses_the_account(self):
        refusal = (405, {"code": 3012, "msg": "unusual activity"}, "application/json", {})
        self.origin.chat += [refusal, refusal]
        with self.assertRaises(ProviderRateLimitError) as caught:
            self.collect()
        self.assertIn("3012", caught.exception.message_ru)
        before = len(self.origin.chats())
        with self.assertRaises(ProviderRateLimitError):
            self.collect()
        self.assertEqual(len(self.origin.chats()), before, "a paused account must not reach the origin")
        ok, _ = self.run_async(self.provider.check(self.account))
        self.assertTrue(ok)
        self.assertNotIn(self.account.id, self.provider._paused)

    def test_used_up_quota_parks_only_that_model_until_the_reset(self):
        self.origin.balance["balances"][0]["remaining_units"] = 0
        self.origin.chat.append((429, {"error": {"code": "1308", "message": "Usage limit reached"}},
                                 "application/json", {}))
        with self.assertRaises(ProviderQuotaError) as caught:
            self.collect()
        self.assertIn("1308", caught.exception.message_ru)
        until = self.account.extra["zcode_exhausted"]["glm53"]
        self.assertAlmostEqual(until, self.origin.balance["balances"][0]["period_end"] // 1000, delta=1)
        before = len(self.origin.chats())
        with self.assertRaises(ProviderQuotaError):
            self.collect()
        self.assertEqual(len(self.origin.chats()), before)
        self.collect(model="glm-5.3-flash")
        self.assertEqual(len(self.origin.chats()), before + 1)
        self.origin.balance["balances"][0]["remaining_units"] = 10
        self.run_async(self.provider.usage(self.account))
        self.assertNotIn("zcode_exhausted", self.account.extra)

    def test_auth_model_and_throttle_verdicts(self):
        cases = [((401, {"code": 1001, "msg": "token expired"}, "application/json", {}), ProviderAuthError),
                 ((400, {"code": 1311, "msg": "plan"}, "application/json", {}), ProviderModelUnavailableError),
                 ((429, {"code": 1302, "msg": "slow down"}, "application/json", {"Retry-After": "120"}),
                  ProviderRateLimitError)]
        for answer, error in cases:
            with self.subTest(status=answer[0]):
                self.origin.chat.append(answer)
                with self.assertRaises(error):
                    self.collect()

class AccountTests(ZCodeTestCase):
    def test_check_usage_and_models_read_the_billing(self):
        ok, text = self.run_async(self.provider.check(self.account))
        self.assertTrue(ok)
        self.assertIn("Lite", text)
        self.assertIn("осталось 40 из 100", text)
        usage = self.run_async(self.provider.usage(self.account))
        self.assertTrue(usage["subscription"].startswith("Lite · до "))
        row = usage["rows"][0]
        self.assertEqual((row["display"], row["used"], row["limit"]), ("GLM-5.3", 60, 100))
        self.assertEqual(row["reset"], row["reset_at"])
        self.assertEqual(self.run_async(self.provider.dynamic_models(self.account)), ["glm-5.3", "glm-5.3-flash"])
        self.assertIn("context 200K", self.provider.model_note("glm-5.3"))

    def test_an_account_without_a_plan_is_sent_to_the_official_app(self):
        self.origin.balance = {"plans": [], "balances": []}
        ok, text = self.run_async(self.provider.check(self.account))
        self.assertIsNone(ok)
        self.assertIn("приложении ZCode", text)
        usage = self.run_async(self.provider.usage(self.account))
        self.assertEqual(usage["rows"], [])
        self.assertIn("Free", usage["note"])
        self.assertIn("не проходит", usage["note"])

    def test_browser_sign_in_adds_the_account_once_and_then_refreshes_it(self):
        async def sign_in():
            started = await self.provider.oauth_start()
            self.assertTrue(started["ok"])
            self.assertEqual(started["expires_in"], zcode.LOGIN_TTL)
            self.assertTrue(started["url"].startswith("https://"))
            for _ in range(200):
                status = await self.provider.oauth_status(started["flow_id"])
                if status["status"] != "waiting":
                    return status
                await asyncio.sleep(0.02)
            self.fail("the sign-in never finished")

        with patch.object(state, "accounts", {}), patch.object(state, "models", {}), \
                patch("app.providers.zcode.webbrowser.open") as opened, \
                patch("app.model_refresh.refresher.refresh", new=AsyncMock()):
            first = self.run_async(sign_in())
            self.assertEqual(first["status"], "success")
            opened.assert_called_once()
            created = state.accounts_of("zcode")
            self.assertEqual(len(created), 1)
            self.assertEqual(created[0].label, "dev@example.com")
            self.assertIn("zcode-glm-5.3", state.models)
            init = next(r for r in self.origin.requests if r["path"] == zcode.OAUTH_INIT_PATH)
            self.assertEqual(init["body"], {"provider": "zai"})

            self.origin.polls = 0
            self.origin.login_token = jwt(exp=time.time() + 7200)
            second = self.run_async(sign_in())
            self.assertEqual(second["status"], "success")
            self.assertEqual(len(state.accounts_of("zcode")), 1, "the same Z.AI user must not get a twin")
            self.assertEqual(state.accounts_of("zcode")[0].fields["token"], self.origin.login_token)

class ToolCallsThroughTheProxyTest(ZCodeTestCase):
    def test_openai_tool_calls_reach_zcode_and_come_back(self):
        from starlette.requests import Request
        from app import openai_api

        payload = json.dumps({"model": "zcode-glm-5.3", "stream": False,
                              "messages": [{"role": "user", "content": "Погода в Риме?"}],
                              "tools": [{"type": "function", "function": {
                                  "name": "get_weather", "parameters": {"type": "object", "properties": {
                                      "city": {"type": "string"}}}}}]}).encode()

        async def receive():
            return {"type": "http.request", "body": payload, "more_body": False}

        entry = ModelEntry("zcode", "zcode", "glm-5.3")
        self.origin.chat.append((200, stream_answer(tool=True), "text/event-stream", {}))
        with patch.object(state, "accounts", {"z1": self.account}), patch.object(state, "models", {entry.id: entry}), \
                patch.dict(providers._providers, {"zcode": self.provider}), \
                patch.object(openai_api, "_check_auth", return_value=None), patch.object(state, "commit_trace"):
            response = self.run_async(openai_api.chat_completions(
                Request({"type": "http", "method": "POST", "path": "/", "headers": []}, receive)))
        body = response if isinstance(response, dict) else json.loads(response.body)
        message = body["choices"][0]["message"]
        self.assertEqual(body["choices"][0]["finish_reason"], "tool_calls")
        self.assertEqual(message["tool_calls"][0]["function"]["name"], "get_weather")
        self.assertEqual(json.loads(message["tool_calls"][0]["function"]["arguments"]), {"city": "Рим"})
        sent = self.origin.chats()[-1]["body"]
        self.assertEqual(sent["tools"][0]["name"], "get_weather")

if __name__ == "__main__":
    unittest.main()
