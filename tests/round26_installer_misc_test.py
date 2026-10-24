"""Round 26 — installer/build and Proxy tails: bilingual auth/passthrough errors, Custom-specific
hints, «which account to add», whitespace content next to tool calls."""
from __future__ import annotations

import os
import tempfile

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

import asyncio  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import patch  # noqa: E402

from starlette.requests import Request  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import anthropic_api, openai_api, passthrough, responses_api  # noqa: E402
from app.config import config_store  # noqa: E402
from app.state import state  # noqa: E402

TOOLS_OPENAI = [{"type": "function", "function": {"name": "get", "parameters": {"type": "object"}}}]
TOOL_DELTAS = [
    {"id": "t", "choices": [{"delta": {"content": "\n\n"}}]},
    {"id": "t", "choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                                       "function": {"name": "get", "arguments": "{}"}}]}}]},
    {"id": "t", "choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
]


def _request(body, headers=None):
    payload = json.dumps(body).encode()

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "POST", "path": "/", "headers": raw,
                    "query_string": b""}, receive)


def _chunks(items):
    async def gen(*_a, **_k):
        for item in items:
            yield item
    return gen


class BilingualAuthErrors(unittest.TestCase):
    def test_openai_and_anthropic_hub_key_errors_have_english(self):
        with patch.object(config_store, "authorize_api_request", return_value=False):
            body = json.loads(openai_api._check_auth(_request({})).body)
            self.assertIn("Settings → API", body["error"]["message_en"])
            # Round 27: API clients read ``message`` in English; the Russian text rides along.
            self.assertEqual(body["error"]["message_en"], body["error"]["message"])
            self.assertIn("Настройки → API", body["error"]["message_ru"])
            body = json.loads(anthropic_api._check_auth(_request({})).body)
            self.assertEqual(body["error"]["type"], "authentication_error")
            self.assertIn("Invalid hub API key", body["error"]["message_en"])

    def test_anthropic_validation_errors_have_english(self):
        with patch.object(anthropic_api, "_check_auth", return_value=None):
            response = asyncio.run(anthropic_api.messages(_request({"messages": [{"role": "user", "content": "x"}]})))
        error = json.loads(response.body)["error"]
        self.assertEqual(error["message_en"], "The 'model' field is required")

    def test_passthrough_errors_have_english(self):
        with patch.object(config_store, "authorize_api_request", return_value=False):
            body = json.loads(asyncio.run(passthrough.passthrough("x", "v1/models", _request({}))).body)
        self.assertTrue(body["error_en"] and body["error_en"] != body["error"])
        with patch.object(config_store, "authorize_api_request", return_value=True), \
                patch.object(passthrough, "_find_custom", return_value=None):
            response = asyncio.run(passthrough.passthrough("mysite", "v1/models", _request({})))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(json.loads(response.body)["error_en"], "Connection “mysite” not found")


class ProviderAwareHints(unittest.TestCase):
    def test_custom_auth_hint_talks_about_the_api_key(self):
        from app.chat import CompletionError
        custom = CompletionError("x", status=503, err_type="authentication_error", provider="custom")
        self.assertIn("API-ключ", custom.hint)
        self.assertNotIn("Кука", custom.hint)
        self.assertIn("API key", custom.hint_en)
        other = CompletionError("x", status=503, err_type="authentication_error", provider="kiro")
        self.assertIn("Кука или токен", other.hint)

    def _no_accounts(self, provider_id, prefix):
        from app.chat import ChatService, CompletionError
        from app.providers.custom import CustomProvider
        from app.providers.genspark import GensparkProvider
        from app.routing import Resolved
        provider = {"custom": CustomProvider, "genspark": GensparkProvider}[provider_id]()

        async def run():
            service = ChatService()
            with patch.object(config_store.settings, "proxy_enabled", True), \
                    patch("app.chat.resolve", return_value=Resolved(None, prefix, "m1", provider_id)), \
                    patch.object(service, "_provider", return_value=provider), \
                    patch("app.chat.pool.ordered_candidates", return_value=[]), \
                    patch("app.chat.state.usable_accounts", return_value=[]), \
                    patch("app.chat.state.accounts_of", return_value=[]):
                try:
                    async for _ in service.stream_completion(f"{prefix}-m1", [{"role": "user", "content": "x"}], {}):
                        pass
                except CompletionError as exc:
                    return exc
            return None

        return asyncio.run(run())

    def test_missing_account_names_the_provider_to_add(self):
        exc = self._no_accounts("genspark", "genspark")
        self.assertEqual(exc.err_type, "service_unavailable")
        self.assertIn("нужен аккаунт Genspark", exc.message)
        self.assertIn("needs a Genspark account", exc.message_en)
        exc = self._no_accounts("custom", "mysite")
        self.assertIn("префиксом «mysite»", exc.message)
        self.assertIn("“Custom API”", exc.message_en)
        self.assertIn("Собственный API", exc.hint)


class WhitespaceBeforeToolCall(unittest.TestCase):
    def setUp(self):
        self.patches = [patch.object(state, "commit_trace"), patch.object(state, "discard_trace"),
                        patch.object(state, "guard_stream", lambda stream, _trace: stream)]
        for module in (openai_api, anthropic_api, responses_api):
            self.patches += [patch.object(module, "_check_auth", return_value=None),
                             patch.object(module, "tool_provider_supported", return_value=True),
                             patch.object(module.chat_service, "stream_completion", _chunks(TOOL_DELTAS))]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()

    def test_openai_content_is_null(self):
        result = asyncio.run(openai_api.chat_completions(_request({
            "model": "m", "tools": TOOLS_OPENAI, "messages": [{"role": "user", "content": "hi"}]})))
        message = result["choices"][0]["message"]
        self.assertIsNone(message["content"])
        self.assertEqual(message["tool_calls"][0]["function"]["name"], "get")

    def _openai_stream(self, items=None):
        async def run():
            with patch.object(openai_api.chat_service, "stream_completion", _chunks(items or TOOL_DELTAS)):
                response = await openai_api.chat_completions(_request({
                    "model": "m", "stream": True, "tools": TOOLS_OPENAI,
                    "messages": [{"role": "user", "content": "hi"}]}))
                return "".join([part async for part in response.body_iterator])
        rows = [json.loads(line[6:]) for line in asyncio.run(run()).splitlines()
                if line.startswith("data: ") and line != "data: [DONE]"]
        return rows, "".join(str(row["choices"][0]["delta"].get("content") or "")
                             for row in rows if row.get("choices"))

    def test_openai_stream_drops_whitespace_before_the_call(self):
        rows, text = self._openai_stream()        # live Yandex streams exactly this
        self.assertEqual(text, "")
        self.assertEqual(rows[0]["choices"][0]["delta"].get("role"), "assistant")
        self.assertTrue(any(row["choices"][0]["delta"].get("tool_calls") for row in rows if row.get("choices")))

    def test_openai_stream_keeps_whitespace_of_real_text(self):
        _rows, text = self._openai_stream([
            {"id": "t", "choices": [{"delta": {"content": "\n"}}]},
            {"id": "t", "choices": [{"delta": {"content": "Hi"}}]},
            {"id": "t", "choices": [{"delta": {}, "finish_reason": "stop"}]}])
        self.assertEqual(text, "\nHi")
        _rows, text = self._openai_stream([{"id": "t", "choices": [{"delta": {"content": " "}}]}])
        self.assertEqual(text, " ")

    def _anthropic(self, stream):
        return anthropic_api.messages(_request({
            "model": "m", "max_tokens": 10, "stream": stream,
            "tools": [{"name": "get", "input_schema": {"type": "object"}}],
            "messages": [{"role": "user", "content": "hi"}]}))

    def test_anthropic_has_no_whitespace_text_block(self):
        result = asyncio.run(self._anthropic(False))
        body = result if isinstance(result, dict) else json.loads(result.body)
        self.assertEqual([block["type"] for block in body["content"]], ["tool_use"])

        async def wire():
            response = await self._anthropic(True)
            return "".join([part async for part in response.body_iterator])
        events = [json.loads(line[6:]) for line in asyncio.run(wire()).splitlines() if line.startswith("data: ")]
        starts = [e for e in events if e["type"] == "content_block_start"]
        self.assertEqual([(e["index"], e["content_block"]["type"]) for e in starts], [(0, "tool_use")])

    def test_anthropic_stream_keeps_leading_whitespace_of_real_text(self):
        items = [{"id": "t", "choices": [{"delta": {"content": "\n"}}]},
                 {"id": "t", "choices": [{"delta": {"content": "Hi"}}]}]

        async def wire():
            with patch.object(anthropic_api.chat_service, "stream_completion", _chunks(items)):
                response = await anthropic_api.messages(_request({
                    "model": "m", "max_tokens": 10, "stream": True,
                    "messages": [{"role": "user", "content": "hi"}]}))
                return "".join([part async for part in response.body_iterator])
        events = [json.loads(line[6:]) for line in asyncio.run(wire()).splitlines() if line.startswith("data: ")]
        text = "".join(e["delta"]["text"] for e in events if e["type"] == "content_block_delta")
        self.assertEqual(text, "\nHi")

    def _responses(self, stream, items=None):
        async def run():
            with patch.object(responses_api.chat_service, "stream_completion", _chunks(items or TOOL_DELTAS)):
                response = await responses_api.responses(_request({
                    "model": "m", "stream": stream, "input": "hi",
                    "tools": [{"type": "function", "name": "get", "parameters": {"type": "object"}}]}))
                if not stream:
                    return response
                return "".join([part async for part in response.body_iterator])
        return asyncio.run(run())

    def test_responses_output_is_only_the_call(self):
        result = self._responses(False)
        body = result if isinstance(result, dict) else json.loads(result.body)
        self.assertEqual([item["type"] for item in body["output"]], ["function_call"])
        events = [json.loads(line[6:]) for line in self._responses(True).splitlines() if line.startswith("data: ")]
        added = [e["item"]["type"] for e in events if e["type"] == "response.output_item.added"]
        self.assertEqual(added, ["function_call"])
        final = events[-1]["response"]
        self.assertEqual([item["type"] for item in final["output"]], ["function_call"])

    def test_responses_whitespace_only_answer_is_still_a_message(self):
        items = [{"id": "t", "choices": [{"delta": {"content": "\n\n"}, "finish_reason": "stop"}]}]
        events = [json.loads(line[6:]) for line in self._responses(True, items).splitlines()
                  if line.startswith("data: ")]
        kinds = [e["type"] for e in events]
        self.assertIn("response.output_item.added", kinds)
        self.assertLess(kinds.index("response.output_item.added"), kinds.index("response.output_text.done"))
        self.assertEqual(events[-1]["response"]["output"][0]["type"], "message")


class InstallerRetry(unittest.TestCase):
    """A failed attempt must not leak into the retry: exit code and warnings."""

    def setUp(self):
        from tests.installer_engine_test import FakePlatform, make_setup
        from installer import payload, products
        from installer.engine import Engine
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.os = FakePlatform(base / "pc")
        pak = payload.Payload(make_setup(base, "2.0"))
        self.addCleanup(pak.close)
        self.engine = Engine(products.SUITE, self.os, pak, close_wait=0.05)
        self.dir = str(Path(self.os.envs["LOCALAPPDATA"]) / "Programs" / "MemuAI")

    def _wait(self, bridge):
        import time
        for _ in range(800):
            if bridge.poll()["status"] != "running":
                break
            time.sleep(0.01)
        return bridge.poll()

    def test_successful_retry_exits_with_zero(self):
        from installer.bridge import Bridge
        codes = []
        bridge = Bridge(self.engine, "install", "en", on_exit=codes.append)
        bridge.init()
        opts = {"dir": self.dir, "components": ["proxy"], "tasks": []}
        with patch.object(self.os, "disk_free", return_value=1):
            bridge.install(opts)
            self.assertEqual(self._wait(bridge)["status"], "error")
        bridge.install(opts)
        self.assertEqual(self._wait(bridge)["status"], "done")
        bridge.close()
        self.assertEqual(codes, [0])

    def test_warnings_of_a_previous_attempt_are_not_repeated(self):
        from installer.engine import Options
        self.os.webview = ""
        first = self.engine.install(Options(self.dir, ["proxy", "memuai"], [], "en"))
        self.assertEqual(len(first["warnings"]), 1)
        self.os.webview = "130.0"
        second = self.engine.install(Options(self.dir, ["proxy", "memuai"], [], "en"))
        self.assertEqual(second["warnings"], [])


def _chrome() -> str:
    for candidate in (os.environ.get("MEMU_TEST_CHROMIUM", ""), "/workspace/work/chromium/chrome"):
        if candidate and os.path.exists(candidate):
            return candidate
    return ""


def _playwright_ok() -> bool:
    try:
        import playwright.async_api  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return bool(_chrome())


@unittest.skipUnless(_playwright_ok(), "needs Playwright + Chromium")
class UninstallDoneText(unittest.TestCase):
    """«Удалить личные данные» must not end with «данные сохранены в …»."""

    def _text(self, query):
        from playwright.async_api import async_playwright
        page_url = (ROOT / "installer" / "ui" / "index.html").as_uri() + "?mock=1&mode=uninstall&lang=ru&speed=0.3" + query

        async def run():
            async with async_playwright() as p:
                browser = await p.chromium.launch(executable_path=_chrome(), args=["--no-sandbox"])
                page = await browser.new_page(viewport={"width": 900, "height": 600})
                await page.goto(page_url)
                async def until(js):
                    for _ in range(300):
                        if await page.evaluate("() => " + js):
                            return
                        await page.wait_for_timeout(50)
                    self.fail("timed out: " + js)
                await until("!!window.__memuUi")
                await page.evaluate("""remove => { var k = document.getElementById('opt-keep');
                    k.checked = !remove; k.dispatchEvent(new Event('change'));
                    document.getElementById('btn-un-go').click(); }""", "&remove" in query)
                await until("document.documentElement.getAttribute('data-screen') === 'un-done'")
                text = await page.inner_text("#un-done-text")
                await browser.close()
                return text
        return asyncio.run(run())

    def test_texts_follow_the_choice(self):
        self.assertIn("Личные данные тоже удалены", self._text("&remove"))
        self.assertIn("сохранены", self._text(""))
        # Nothing to delete is not «your data is kept in …».
        self.assertIn("Личные данные тоже удалены", self._text("&remove&data=none"))


if __name__ == "__main__":
    unittest.main()
