"""Round 27: хвосты Round 26 (r27-leftovers, r27-leftovers-2) — регрессионные тесты без сети.

Запуск: NO_PROXY=127.0.0.1,localhost MEMUAI_DATA_DIR=/tmp/x python -m unittest tests.round27_leftovers_test
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
import types
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="r27lo-test-"))

from app import chat_titles  # noqa: E402

def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")

def _stream_of(*texts, reasoning=""):
    async def stream(model, messages, params):
        stream.calls.append((model, messages, params))
        if reasoning:
            yield {"choices": [{"delta": {"reasoning_content": reasoning}}]}
        for text in texts:
            yield {"choices": [{"delta": {"content": text}}]}
    stream.calls = []
    return stream

class ChatTitles(unittest.TestCase):
    def test_marker_ignores_retold_instruction(self):
        self.assertEqual(chat_titles.title_from_marker("…append [TITLE: …] at the end"), "")
        self.assertEqual(chat_titles.title_from_marker("x [TITLE: Хранение сухарей] y [TITLE: …]"), "Хранение сухарей")

    def test_reply_cleanup(self):
        self.assertEqual(chat_titles.title_from_reply('<think>hmm</think>\n«Полив фикуса зимой».'), "Полив фикуса зимой")
        self.assertEqual(chat_titles.title_from_reply("Title: Watering a ficus\nmore"), "Watering a ficus")
        self.assertEqual(chat_titles.title_from_reply("<ds_safety>x</ds_safety>"), "")

    def test_request_has_no_system_role(self):
        rows = chat_titles.title_messages("q" * 5000, "a" * 5000)
        self.assertEqual([row["role"] for row in rows], ["user"])
        self.assertLess(len(rows[0]["content"]), chat_titles.USER_LIMIT + chat_titles.ANSWER_LIMIT + 600)

    def test_needs_title(self):
        prefs = {"chat_titles": "ai"}
        self.assertTrue(chat_titles.needs_title({"title": "abc"}, prefs))
        self.assertFalse(chat_titles.needs_title({"titleManual": True}, prefs))
        self.assertFalse(chat_titles.needs_title({"titleAuto": True}, prefs))
        self.assertFalse(chat_titles.needs_title({"temp": True}, prefs))
        self.assertFalse(chat_titles.needs_title({}, {"chat_titles": "off"}))

    def test_generate_title_uses_content_only(self):
        stream = _stream_of("Хранение ", "сухарей", reasoning="[TITLE: wrong]")
        title = asyncio.run(chat_titles.generate_title(stream, "m", "Как хранить сухари?", "В банке."))
        self.assertEqual(title, "Хранение сухарей")
        self.assertEqual(stream.calls[0][2], {"web_search": False, "thinking": False})
        self.assertEqual(asyncio.run(chat_titles.generate_title(stream, "m", "  ")), "")

class DesktopChatTitles(unittest.TestCase):
    """app.js: no system prompt → no title instruction in the request, a separate request after the answer."""

    def setUp(self):
        self.js = _read("app/pgstatic/app.js")

    def test_title_instruction_only_with_system_prompt(self):
        self.assertIn('if (titleRequest && sys) sys += "\\n\\n" + titleRequest;', self.js)
        self.assertIn("const integrationsBlock = sys ? integrationsPromptBlock() : \"\";", self.js)

    def test_background_title_after_the_answer(self):
        self.assertIn('api("/api/pg/chat/title"', self.js)
        finally_part = self.js[self.js.index("const turnContinues = !localController.signal.aborted"):]
        self.assertIn("!turnContinues && !assistant.error", finally_part[:6000])
        self.assertIn("requestChatTitle(sourceChatId", finally_part[:6000])
        self.assertIn("extractChatTitle(assistant.content, sourceChatId, assistant.reasoning)", self.js)

    def test_cache_bust(self):
        self.assertNotIn("/app.js?v=1.21-r26", _read("app/pgstatic/index.html"))
        self.assertNotIn("app.js?v=26-r26", _read("app/tgapp/index.html"))

    def test_playground_endpoint(self):
        from fastapi.testclient import TestClient
        from app.playground_api import create_app
        client = TestClient(create_app(), base_url="http://127.0.0.1")
        self.assertEqual(client.post("/api/pg/chat/title", json={"model": "m", "text": "", "lang": "en"}).json(),
                         {"error": "Nothing to name"})
        self.assertEqual(client.post("/api/pg/chat/title", content=b"[1]").status_code, 400)

class MiniAppTitles(unittest.TestCase):
    def _prompts(self, default_id="memuai-default", disabled=()):
        return {"prompts": [{"id": "memuai-default", "text": "BASE"}, {"id": "empty", "text": ""}],
                "default_id": default_id, "disabled_for": list(disabled)}

    def test_no_system_prompt_keeps_the_title_for_an_agent(self):
        from app import tgapp_chat
        with patch("app.pgdata.load_prompts", return_value=self._prompts("empty")):
            plain = tgapp_chat.build_system_prompt(model="x-m", prefs={}, lang="ru", temp=False, meme=None,
                                                   has_profile_context=False, title_prompt="<chat_title>T</chat_title>")
            self.assertEqual(plain, "")
            with patch.object(tgapp_chat, "meme_prompt_block", return_value="<agent>A</agent>"):
                agent = tgapp_chat.build_system_prompt(model="x-m", prefs={}, lang="ru", temp=False, meme={"id": "a"},
                                                       has_profile_context=False, title_prompt="<chat_title>T</chat_title>")
        self.assertEqual(agent, "<agent>A</agent>\n\n<chat_title>T</chat_title>")

    def test_late_title_event_flow(self):
        from app import tgapp_chat
        job = tgapp_chat.Job("chat", "o")
        job.meta = {"chat_id": "c1", "profile_id": "default", "model": "m", "temp": False, "meme_id": ""}
        chat = {"id": "c1", "title": "", "messages": []}
        user_row = {"role": "user", "content": "Как поливать фикус?", "createdAt": 1}
        fake = types.SimpleNamespace(stream_completion=None)
        answer_stream = _stream_of("Умеренно.")
        title_stream = _stream_of("Полив фикуса")

        def stream_completion(model, messages, params):
            return (title_stream if messages[0]["content"].startswith("Give a short") else answer_stream)(model, messages, params)
        fake.stream_completion = stream_completion
        saved = {"id": "c1", "title": "Как поливать фикус?", "messages": []}
        with patch("app.chat.chat_service", fake), \
                patch.object(tgapp_chat, "_persist", return_value=saved), \
                patch.object(tgapp_chat, "_preferences", return_value={"chat_titles": "ai"}), \
                patch.object(tgapp_chat, "_apply_title", return_value=True) as apply:
            asyncio.run(tgapp_chat._generate(job, chat, [], user_row, [{"role": "user", "content": "q"}], "m",
                                             False, False, {}, "ru", False, ""))
        kinds = [event["type"] for event in job.events]
        self.assertEqual(kinds[-3:], ["done", "title", "settled"])
        done = next(event for event in job.events if event["type"] == "done")
        self.assertTrue(done["title_pending"])
        self.assertTrue(job.done)
        apply.assert_called_once_with("c1", "Полив фикуса", "default")

    def test_settled_job_is_not_running(self):
        from app import tgapp_chat
        job = tgapp_chat.Job("chat", "o")
        job.meta = {"chat_id": "c9", "settled": True}
        tgapp_chat.jobs.add(job)
        try:
            self.assertIsNone(tgapp_chat.jobs.active_for_chat("c9"))
            self.assertFalse(asyncio.run(tgapp_chat.cancel_job(job)))
        finally:
            tgapp_chat.jobs.jobs.pop(job.id, None)

    def test_client_follows_until_settled(self):
        js = _read("app/tgapp/app.js")
        self.assertIn('(event.type === "done" && !event.title_pending) || event.type === "settled"', js)
        self.assertIn('!["done", "lost", "title", "settled"].includes(event.type)', js)

class TelegramTitles(unittest.TestCase):
    def test_rename_is_manual(self):
        from app import telegram_bot
        stored = []
        with patch.object(telegram_bot, "_chat_rows", return_value=[{"id": "c", "title": "a", "titleAuto": True}]), \
                patch.object(telegram_bot, "upsert_chat", side_effect=lambda row, pid: stored.append(row)):
            self.assertTrue(telegram_bot._rename_chat("default", "c", "Мой чат"))
        self.assertTrue(stored[0]["titleManual"])
        self.assertNotIn("titleAuto", stored[0])

    def test_name_chat_in_background(self):
        from app import pgdata, telegram_bot
        fake = types.SimpleNamespace(stream_completion=_stream_of("Рецепт борща"))
        pid, tag = "default", uuid.uuid4().hex[:6]
        pgdata.upsert_chat({"id": "tg-1" + tag, "title": "Как сварить борщ", "messages": [{}], "updated": 5}, pid)
        with patch.object(telegram_bot, "load_preferences", return_value={}):
            asyncio.run(telegram_bot._name_chat(fake, pid, "tg-1" + tag, "m", "Как сварить борщ", "Так."))
        row = next(r for r in pgdata.load_chats(pid) if r["id"] == "tg-1" + tag)
        self.assertEqual((row["title"], row["titleAuto"], row["updated"]), ("Рецепт борща", True, 6))

        pgdata.upsert_chat({"id": "tg-2" + tag, "title": "Мой", "titleManual": True, "messages": [{}]}, pid)
        with patch.object(telegram_bot, "load_preferences", return_value={}):
            asyncio.run(telegram_bot._name_chat(fake, pid, "tg-2" + tag, "m", "Как сварить борщ", "Так."))
        self.assertEqual(next(r for r in pgdata.load_chats(pid) if r["id"] == "tg-2" + tag)["title"], "Мой")

        with patch.object(telegram_bot, "load_preferences", return_value={}):
            asyncio.run(telegram_bot._name_chat(fake, pid, "tg-gone" + tag, "m", "Как сварить борщ", "Так."))
        self.assertFalse(any(r["id"] == "tg-gone" + tag for r in pgdata.load_chats(pid)))

    def test_only_the_first_turn_of_a_new_chat(self):
        src = _read("app/telegram_bot.py")

        self.assertIn('first_turn = not chat.get("messages") and not retry and not edited', src)
        self.assertIn("_spawn_answer(_name_chat(", src)

class BrowserAgentLanguage(unittest.TestCase):
    def test_english_log(self):
        from app import automation_runtime as ar
        runtime = ar.EdgeAutomation()
        runtime.start = AsyncMock()
        runtime.snapshot = AsyncMock(return_value={"url": "https://a", "title": "A", "text": "", "elements": []})

        async def completion(model, messages):
            return json.dumps({"action": "done", "report": "ok"})
        asyncio.run(ar.run_browser_agent(runtime, completion, model="m", task="t", lang="en"))
        text = " ".join(row["message"] for row in runtime.logs)
        self.assertNotRegex(text, "[А-Яа-яЁё]")
        self.assertIn("The browser agent finished the task", text)

    def test_errors_follow_the_language(self):
        from app import automation_runtime as ar
        with self.assertRaisesRegex(ValueError, "HTTP\\(S\\) addresses"):
            ar._safe_navigation_url("ftp://x", "en")
        with self.assertRaisesRegex(ValueError, "только HTTP"):
            ar._safe_navigation_url("ftp://x")
        runtime = ar.EdgeAutomation()
        runtime.lang = "en"
        with self.assertRaisesRegex(RuntimeError, "not running"):
            asyncio.run(runtime.command("Page.navigate"))

    def test_callers_pass_the_language(self):
        self.assertIn("lang: PG_LANG } });", _read("app/pgstatic/app.js"))
        self.assertIn("max_steps=18, lang=language()))", _read("app/cli/tools/automation.py"))
        self.assertIn("max_steps=max(2, min(40, int(body.get(\"max_steps\") or 16))), lang=lang,",
                      _read("app/playground_api.py"))

class ElevenLabsMusic(unittest.TestCase):
    def test_error_reason_is_shown(self):
        from app import music_runtime
        exc = ValueError("API HTTP 400")
        exc.body = json.dumps({"detail": {"status": "bad_prompt", "message": "x",
                                          "data": {"prompt_suggestion": "An epic rock ballad"}}})
        text = music_runtime._error_text(exc)
        self.assertTrue(text.startswith("API HTTP 400: "))
        self.assertIn("An epic rock ballad", text)
        exc.body = json.dumps({"detail": [{"loc": ["body", "music_length_ms"], "msg": "must be ≥ 3000"}]})
        self.assertEqual(music_runtime._error_text(exc), "API HTTP 400: must be ≥ 3000")
        exc.body = "<html>"
        self.assertEqual(music_runtime._error_text(exc), "API HTTP 400")

    def test_start_error_keeps_the_body(self):
        from app import module_media
        failure = ValueError("API HTTP 422")
        failure.body = '{"detail": {"message": "bad"}}'
        with patch.object(module_media, "public_request", AsyncMock(side_effect=failure)) as request:
            with self.assertRaises(module_media.MusicStartError) as caught:
                asyncio.run(module_media.generate_config(
                    {"endpoint": "https://api.elevenlabs.io/v1/music", "preset": "elevenlabs"}, {"prompt": "p"}, headers={}))
        self.assertEqual(caught.exception.status, 422)
        self.assertIn("bad", caught.exception.body)
        self.assertEqual(request.call_args.kwargs["timeout"], 600)

    def test_limits_match_the_api(self):
        from app import music_runtime
        caps = music_runtime.capabilities({"preset": "elevenlabs"})
        self.assertEqual((caps["prompt_limit"], caps["duration_max"]), (4100, 600))
        config, _ = music_runtime._adapter({"preset": "elevenlabs"}, {"prompt": "calm piano", "duration": 600}, "music_v2_5")
        self.assertEqual(config["template"]["music_length_ms"], 600_000)

class EarlierLeftovers(unittest.TestCase):
    """Fixes of r27-leftovers (the first agent)."""

    def test_spawn_background_keeps_a_reference(self):
        from app.providers import base

        async def main():
            done = asyncio.Event()

            async def job():
                done.set()
            task = base.spawn_background(job())
            self.assertIn(task, base._BACKGROUND)
            await done.wait()
            await asyncio.sleep(0)
            self.assertNotIn(task, base._BACKGROUND)
        asyncio.run(main())

    def test_loopback_bypasses_proxies(self):
        from app.providers import http
        from app.tts_providers import trusts_env
        self.assertTrue(http.is_loopback("http://127.0.0.1:8080/v1"))
        self.assertTrue(http.is_loopback("http://localhost:1234"))
        self.assertFalse(http.is_loopback("https://api.openai.com"))
        self.assertFalse(trusts_env("http://127.0.0.1:5000"))
        self.assertTrue(trusts_env("https://api.anthropic.com"))
        self.assertIn("trust_env=trusts_env(url)", _read("app/panels_usage.py"))

    def test_mcp_and_tts_errors_in_english(self):
        from app import mcp_client, tts_process
        error = mcp_client.MCPError("Сервер не ответил", "The server did not answer")
        self.assertEqual(mcp_client.error_text(error, "en"), "The server did not answer")
        self.assertEqual(mcp_client.error_text(error), "Сервер не ответил")
        for code in tts_process.MESSAGES:
            self.assertNotRegex(tts_process.LocalTTSError(code).text("en"), "[А-Яа-яЁё]")

    def test_cli_mcp_httpx_annotation(self):
        src = _read("app/cli/tools/mcp.py")
        self.assertIn("if TYPE_CHECKING:", src)

    def test_dead_helpers_removed(self):
        panels = _read("app/pgstatic/panels.js")
        for name in ("todoPanelsOf", "closeTodoPop", "function renderLive("):
            self.assertNotIn(name, panels)
        self.assertNotIn("const prev = input.onchange", _read("app/tgapp/app.js"))

    def test_quick_computer_card_hides_the_model_report_on_failure(self):
        js = _read("app/pgstatic/quick/plugins/commands.js")
        self.assertIn('const report = status === "done" ? String(st.report || "") : "";', js)

    def test_telegram_panels_in_the_bot_language(self):
        from app import telegram_panels
        panel = {"id": "p", "kind": "weather", "source": {"type": "weather", "params": {}}, "spec": {"old": True}}
        with patch("app.panels._lang", return_value="ru"), \
                patch("app.panels.typed_spec", return_value=("Weather", {"en": True})) as typed:
            out = telegram_panels._in_language(panel, "en", {"lang": "ru"})
        self.assertEqual(out["spec"], {"en": True})
        self.assertEqual(typed.call_args.args[2]["lang"], "en")
        ai = {"id": "a", "kind": "custom", "source": {"type": "ai"}, "spec": {"x": 1}}
        self.assertIs(telegram_panels._in_language(ai, "en", {"lang": "ru"}), ai)

if __name__ == "__main__":
    unittest.main()
