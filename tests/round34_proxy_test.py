"""Round 34 (r34-proxy): MemuAI Proxy fixes — API parameters, owner-text scrubbing, error texts, vision rules,
model picker and the «limit reached» account badge. No network: providers and the chat service are mocked."""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-r34-proxy-")

from starlette.requests import Request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
STATIC = ROOT / "app" / "static"

def _request(body):
    payload = json.dumps(body).encode()
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": payload, "more_body": False}
        await asyncio.Event().wait()

    return Request({"type": "http", "method": "POST", "path": "/",
                    "headers": [(b"content-type", b"application/json")]}, receive)

def _chunk(delta, finish=None):
    return {"object": "chat.completion.chunk", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}

def _call(handler, body, chunks=()):
    """Run an API handler against a fake chat service; returns (response, params the service got)."""
    from app.config import config_store
    seen = {}

    def fake(_model, _messages, params, trace=None):
        seen.update(params)

        async def gen():
            for item in chunks:
                yield item
        return gen()

    async def run():
        with patch.object(config_store, "authorize_api_request", return_value=True), \
                patch("app.chat.chat_service.stream_completion", fake):
            response = await handler(_request(body))
            if hasattr(response, "body_iterator"):
                parts = []
                async for part in response.body_iterator:
                    parts.append(part if isinstance(part, str) else part.decode())
                return response, "".join(parts)
            return response, None

    response, streamed = asyncio.run(run())
    return response, seen, streamed

def _sse_events(text):
    events = []
    for block in text.split("\n\n"):
        data = [line[5:].strip() for line in block.split("\n") if line.startswith("data:")]
        if data and data[0] != "[DONE]":
            events.append(json.loads(data[0]))
    return events

class OpenAIParamsTest(unittest.TestCase):
    """#1: stop / seed / penalties / response_format reach the provider."""

    def test_sampling_and_format_options_pass_through(self):
        from app import openai_api
        body = {"model": "m", "messages": [{"role": "user", "content": "hi"}], "stop": ["5"], "seed": 7,
                "frequency_penalty": 0.5, "presence_penalty": 0, "top_k": 20,
                "response_format": {"type": "json_object"}}
        _response, params, _ = _call(openai_api.chat_completions, body, [_chunk({"content": "{}"}, "stop")])
        for key in ("stop", "seed", "frequency_penalty", "presence_penalty", "top_k", "response_format"):
            self.assertEqual(params.get(key), body[key], key)

    def test_wrong_types_are_ignored(self):
        from app import openai_api
        body = {"model": "m", "messages": [{"role": "user", "content": "hi"}], "seed": True, "stop": 5,
                "response_format": "json", "top_k": "20"}
        _response, params, _ = _call(openai_api.chat_completions, body, [_chunk({"content": "x"}, "stop")])
        for key in ("seed", "stop", "response_format", "top_k"):
            self.assertNotIn(key, params)

    def test_custom_provider_forwards_response_format(self):
        source = (ROOT / "app" / "providers" / "custom.py").read_text(encoding="utf-8")
        self.assertIn('("response_format", "response_format")', source)

class AnthropicParamsTest(unittest.TestCase):
    """#2: stop_sequences / top_k / thinking; thinking blocks come back."""

    BODY = {"model": "m", "max_tokens": 50, "messages": [{"role": "user", "content": "hi"}]}
    CHUNKS = [_chunk({"reasoning_content": "Let me think."}), _chunk({"content": "Answer"}), _chunk({}, "stop")]

    def test_params_are_mapped(self):
        from app import anthropic_api
        body = {**self.BODY, "stop_sequences": ["END", "STOP"], "top_k": 5,
                "thinking": {"type": "enabled", "budget_tokens": 2000}, "output_config": {"effort": "HIGH"}}
        _r, params, _ = _call(anthropic_api.messages, body, self.CHUNKS)
        self.assertEqual(params["stop"], ["END", "STOP"])
        self.assertEqual(params["top_k"], 5)
        self.assertIs(params["thinking"], True)
        self.assertEqual(params["effort"], "high")
        _r, params, _ = _call(anthropic_api.messages, {**self.BODY, "thinking": {"type": "disabled"},
                                                       "stop_sequences": ["", 3]}, self.CHUNKS)
        self.assertIs(params["thinking"], False)
        self.assertEqual(params["effort"], "off")
        self.assertNotIn("stop", params)

    def test_thinking_block_in_a_plain_response(self):
        from app import anthropic_api
        response, _p, _ = _call(anthropic_api.messages, {**self.BODY, "thinking": {"type": "enabled"}}, self.CHUNKS)
        blocks = response["content"]
        self.assertEqual([b["type"] for b in blocks], ["thinking", "text"])
        self.assertEqual(blocks[0]["thinking"], "Let me think.")

        response, _p, _ = _call(anthropic_api.messages, self.BODY, self.CHUNKS)
        self.assertEqual([b["type"] for b in response["content"]], ["text"])

    def test_thinking_block_in_a_stream_is_numbered_first(self):
        from app import anthropic_api
        _r, _p, text = _call(anthropic_api.messages, {**self.BODY, "stream": True, "thinking": {"type": "enabled"}},
                             self.CHUNKS)
        events = _sse_events(text)
        starts = [(e["index"], e["content_block"]["type"]) for e in events if e["type"] == "content_block_start"]
        self.assertEqual(starts, [(0, "thinking"), (1, "text")])
        stops = [e["index"] for e in events if e["type"] == "content_block_stop"]
        self.assertEqual(stops, [0, 1])
        deltas = [e["delta"]["type"] for e in events if e["type"] == "content_block_delta"]
        self.assertEqual(deltas, ["thinking_delta", "signature_delta", "text_delta"])

class PublicScrubTest(unittest.TestCase):
    """#3: the public server does not name the owner's accounts, hosts or hints."""

    def test_error_payload_is_scrubbed(self):
        from app.public_api import scrub_error_payload, scrub_sse_chunk
        payload = {"error": {"message": "Every “custom” account failed (xkiro · api.xkiro.com (pool)): quota exceeded",
                             "message_ru": "Все аккаунты «custom» недоступны (xkiro · api.xkiro.com): лимит",
                             "hint": "Добавьте второй аккаунт", "hint_en": "Add a second account", "type": "x"}}
        self.assertTrue(scrub_error_payload(payload))
        err = payload["error"]
        self.assertEqual(err["message"], "The model service failed: quota exceeded")
        self.assertEqual(err["message_ru"], "Сервис модели недоступен: лимит")
        self.assertNotIn("hint", err)
        self.assertNotIn("hint_en", err)
        self.assertNotIn("xkiro", json.dumps(payload, ensure_ascii=False))
        chunk = ('data: ' + json.dumps({"error": {"message": "No usable “custom” accounts.", "hint": "h"}}) + "\n\n").encode()
        out = scrub_sse_chunk(chunk).decode()
        self.assertIn("The model is temporarily unavailable.", out)
        self.assertNotIn('"hint"', out)
        plain = b'data: {"choices": [{"delta": {"content": "error-free"}}]}\n\n'
        self.assertEqual(scrub_sse_chunk(plain), plain)
        self.assertFalse(scrub_error_payload({"choices": []}))

class ErrorTextTest(unittest.TestCase):
    """#4 the first-byte timeout speaks English to API clients; #5 no libcurl boilerplate tail."""

    def test_first_byte_timeout_has_english_and_russian_text(self):
        from app.chat import ChatService, CompletionError
        from app.config import config_store
        from app.routing import Resolved
        from app.state import Account

        class Hanging:
            id = "test"

            def chat_stream(self, *_args, **_kwargs):
                class It:
                    def __aiter__(self):
                        return self

                    async def __anext__(self):
                        raise asyncio.TimeoutError()
                return It()

        async def run():
            service = ChatService()
            with patch.object(config_store.settings, "proxy_enabled", True), \
                    patch.object(config_store.settings, "retry_delay_ms", 0), \
                    patch("app.chat.resolve", return_value=Resolved(None, "test", "model", "test")), \
                    patch.object(service, "_provider", return_value=Hanging()), \
                    patch("app.chat.pool.ordered_candidates", return_value=[Account("test", {}, id="a")]), \
                    patch("app.chat.pool.report_success"), patch("app.chat.pool.report_failure"), \
                    patch("app.state.state.save_accounts"), patch("app.state.state.save_accounts_soon"):
                try:
                    async for _chunk in service.stream_completion("m", [{"role": "user", "content": "x"}], {}):
                        pass
                except CompletionError as exc:
                    return exc
            raise AssertionError("no error")

        exc = asyncio.run(run())
        self.assertIn("did not start answering", exc.message_en)
        self.assertNotRegex(exc.message_en, "[А-Яа-я]")
        self.assertIn("не начал отвечать", exc.message)

    def test_curl_tail_is_dropped(self):
        from app.providers.base import safe_provider_error
        text = ("Failed to perform, curl: (28) Operation timed out after 30000 milliseconds. "
                "See https://curl.se/libcurl/c/libcurl-errors.html first for more details.")
        self.assertEqual(safe_provider_error(text), "Failed to perform, curl: (28) Operation timed out after 30000 milliseconds.")

class VisionRulesTest(unittest.TestCase):
    """#6: vision hints checked live in October 2026."""

    def test_vision_hints(self):
        from app.model_info import vision_hint
        for model in ("kimi-k2.5", "kimi-k2.6", "moonshotai/kimi-k3", "deepseek-v4.1-flash", "glm-5.3-flash",
                      "glm-5.3-flashx", "glm-5v", "glm-4.5v"):
            self.assertIs(vision_hint(model), True, model)
        for model in ("kimi-k2", "kimi-k2-0905", "glm-5.3", "deepseek-chat"):
            self.assertIs(vision_hint(model), False, model)
        self.assertIsNot(vision_hint("deepseek-v4-flash"), True)

class StaticUiTest(unittest.TestCase):
    """#9 «Only free» in the model picker; #10/#15 the limit badge; cache version."""

    def setUp(self):
        self.js = (STATIC / "app.js").read_text(encoding="utf-8")

    def test_only_free_button(self):
        self.assertIn('id="mp-free"', self.js)
        self.assertIn('"Only free"', self.js)
        self.assertIn('"Только бесплатные"', self.js)
        is_free = re.compile(r"(:free|[-_]free)$", re.I)
        self.assertTrue(is_free.search("deepseek/deepseek-r1:free"))
        self.assertFalse(is_free.search("freedom-7b"))

    def test_limit_badge_reads_402_and_429(self):
        self.assertIn("HTTP (402|429)", self.js)
        for text in ('"limit reached"', '"лимит исчерпан"', '"no balance"', '"нет средств"'):
            self.assertIn(text, self.js)

    def test_app_js_is_cache_busted(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertRegex(html, r'src="/app\.js\?v=3\.(5-r3[4-9]|6-r4[0-9])')

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_app_js_parses(self):
        subprocess.run(["node", "--check", str(STATIC / "app.js")], check=True)

class BackgroundCheckTest(unittest.TestCase):
    """#15: the background account check keeps «limit reached» instead of turning the card green."""

    def test_message_helper(self):
        from app.main import background_ok_message
        limited = "подключено · ключ принят, но сейчас исчерпан лимит запросов (HTTP 429)"
        broke = "подключено · ключ принят, но на балансе нет средств (HTTP 402)"
        self.assertEqual(background_ok_message(limited), limited)
        self.assertEqual(background_ok_message(broke), broke)
        self.assertEqual(background_ok_message("подключено · 12 моделей"), "фоновая проверка")
        self.assertEqual(background_ok_message(""), "фоновая проверка")
        self.assertEqual(background_ok_message("HTTP 4290 tokens"), "фоновая проверка")

    def test_checker_loop_keeps_the_limit_message(self):
        from app import main
        from app.state import ST_OK, Account

        acc = Account("custom", {}, id="xkiro")
        acc.mark(ST_OK, "старое")
        message = "подключено · ключ принят, но сейчас исчерпан лимит запросов (HTTP 429)"

        class Provider:
            async def check(self, _account):
                return True, message

        class Stop(Exception):
            pass

        async def fake_sleep(_seconds):
            if fake_sleep.calls:
                raise Stop()
            fake_sleep.calls += 1
        fake_sleep.calls = 0

        async def run():
            with patch.object(main.config_store.settings, "cookie_check_interval", 30), \
                    patch.dict(main.state.accounts, {"xkiro": acc}, clear=True), \
                    patch.object(main, "all_providers", return_value={"custom": Provider()}), \
                    patch.object(main.state, "save_accounts"), \
                    patch("app.main.asyncio.sleep", fake_sleep):
                with self.assertRaises(Stop):
                    await main._cookie_checker()

        asyncio.run(run())
        self.assertEqual(acc.status, ST_OK)
        self.assertEqual(acc.status_message, message)

class DesignFixesTest(unittest.TestCase):
    """Design items: one accounts hint, no duplicate «Add account», equal tiles, no empty «Note» column,
    public step text, «Per page», the assistant button in a narrow window."""

    def setUp(self):
        self.html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.css = (STATIC / "proxy-r34.css").read_text(encoding="utf-8")
        self.js = (STATIC / "app.js").read_text(encoding="utf-8")

    def test_stylesheet_is_linked_with_a_cache_version(self):
        self.assertIn('href="/proxy-r34.css?v=3.5-r34"', self.html)

    def test_accounts_hints_and_empty_state(self):
        self.assertIn('tab.classList.toggle("acc-selecting"', self.js)
        self.assertIn("#tab-accounts.acc-selecting .hint-strip", self.css)
        self.assertIn("#tab-accounts.is-empty .row:has(> #acc-add-btn) { display: none; }", self.css)

    def test_note_column_is_gone(self):
        self.assertNotIn('t("set.models.note")', self.js)
        self.assertIn('class="pm-note"', self.js)
        caps = (STATIC / "model_caps.css").read_text(encoding="utf-8")
        self.assertNotIn("nth-child(7)", caps)

    def test_public_models_show_readable_names(self):
        public = (STATIC / "public.js").read_text(encoding="utf-8")
        self.assertIn("proxyModelCell({ id })", public)
        self.assertIn("#public-root .pub-step .sub", self.css)

    def test_narrow_window_assistant_button(self):
        self.assertIn('id="companion-top"', self.html)
        self.assertIn('$("#companion-top").addEventListener("click"', self.js)
        self.assertRegex(self.css, r"@media \(max-width: 900px\)[^}]*companion-top")

    def test_provider_tiles_share_a_height(self):
        self.assertIn(".pick-grid { grid-auto-rows: 1fr; }", self.css)

if __name__ == "__main__":
    unittest.main()
