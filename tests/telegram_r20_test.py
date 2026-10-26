"""Round 20: the reworked Telegram bot against a fake Bot API.

Everything runs offline: Telegram is an ``httpx.MockTransport`` that records
every call, MemuAI data lives in a temporary directory, models and the
completion stream are stubs.
"""
import asyncio
import base64
import json
import logging
import re
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app import pgdata, telegram_bot, telegram_format, telegram_media, telegram_pairing, telegram_stream
from app.telegram_api import BotApi, TelegramError, redact
from app.telegram_stream import StreamReply

TOKEN = "123456789:AAH" + "x" * 32
OWNER = 305551234

class FakeTelegram:
    """Records Bot API calls; per-method responders can return errors."""

    def __init__(self):
        self.calls = []
        self.responders = {}
        self.next_id = 100
        self.files = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "/file/bot" in path:
            name = path.rsplit("/", 1)[-1]
            return httpx.Response(200, content=self.files.get(name, b""))
        method = path.rsplit("/", 1)[-1]
        if request.headers.get("content-type", "").startswith("application/json"):
            payload = json.loads(request.content or b"{}")
        else:
            payload = {"_multipart": True}
        self.calls.append((method, payload))
        responder = self.responders.get(method)
        if responder is not None:
            result = responder(payload)
            if isinstance(result, httpx.Response):
                return result
            return httpx.Response(200, json={"ok": True, "result": result})
        if method in ("sendMessage", "editMessageText", "sendVoice", "sendAudio"):
            self.next_id += 1
            return httpx.Response(200, json={"ok": True, "result": {"message_id": payload.get("message_id") or self.next_id}})
        if method == "getMe":
            return httpx.Response(200, json={"ok": True, "result": {"id": 1, "username": "memu_test_bot", "first_name": "MemuAI"}})
        return httpx.Response(200, json={"ok": True, "result": True})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    def methods(self):
        return [method for method, _ in self.calls]

    def sent_texts(self):
        return [payload.get("text", "") for method, payload in self.calls if method == "sendMessage"]

    def last(self, method):
        return next((payload for name, payload in reversed(self.calls) if name == method), None)

def error(code, description, retry_after=None):
    body = {"ok": False, "error_code": code, "description": description}
    if retry_after is not None:
        body["parameters"] = {"retry_after": retry_after}
    return lambda payload: httpx.Response(code, json=body)

def message(text="", chat_id=OWNER, **extra):
    row = {"message_id": extra.pop("message_id", 7), "date": int(time.time()),
           "chat": {"id": chat_id, "type": extra.pop("chat_type", "private")},
           "from": {"id": extra.pop("user_id", chat_id), "first_name": "Yono", "username": "yono"}}
    if text:
        row["text"] = text
    row.update(extra)
    return row

def callback(data, chat_id=OWNER, message_id=50):
    return {"id": "cb1", "data": data, "from": {"id": chat_id},
            "message": {"message_id": message_id, "chat": {"id": chat_id, "type": "private"}}}

def buttons(markup):
    return [button for row in (markup or {}).get("inline_keyboard", []) for button in row]

def stream_of(*parts, fail=None):
    async def stream(*_args, **_kwargs):
        for part in parts:
            await asyncio.sleep(0)
            yield {"choices": [{"delta": {"content": part}}]}
        if fail:
            raise fail
    return stream

class BotTestCase(unittest.IsolatedAsyncioTestCase):
    """Temporary MemuAI data dir + paired owner + two fake models."""

    paired = True

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [patch.object(pgdata, "MEMU_DIR", Path(self.tmp.name))]
        for item in self.patches:
            item.start()
        prefs = {"telegram_enabled": True, "telegram_bot_token": TOKEN, "telegram_language": "ru",
                 "telegram_stream_mode": "off"}
        if self.paired:
            prefs["telegram_chat_id"] = str(OWNER)
        self.prefs = pgdata.save_preferences(prefs)
        self.models = [("genspark-gpt-5", "genspark"), ("genspark-claude", "genspark"),
                       ("kiro-sonnet", "kiro")]
        self.patches.append(patch.object(telegram_bot, "_model_entries", side_effect=lambda prefs=None: list(self.models)))
        self.patches[-1].start()
        self.fake = FakeTelegram()
        self.client = self.fake.client()
        telegram_bot._generations.clear()
        telegram_bot._unauthorized_replied.clear()
        telegram_bot._temp_history.clear()
        telegram_bot._webapp_cache["at"] = 0.0

    async def asyncTearDown(self):
        for task in list(telegram_bot._answer_tasks):
            task.cancel()
        await asyncio.gather(*telegram_bot._answer_tasks, return_exceptions=True)
        await self.client.aclose()
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    async def settle(self):
        for _ in range(50):
            if not telegram_bot._answer_tasks:
                return
            await asyncio.gather(*list(telegram_bot._answer_tasks), return_exceptions=True)

    async def send(self, text="", **extra):
        prefs = pgdata.load_preferences()
        await telegram_bot._handle_message(self.client, TOKEN, message(text, **extra), prefs)
        await self.settle()

    async def press(self, data, chat_id=OWNER):
        prefs = pgdata.load_preferences()
        await telegram_bot._handle_callback(self.client, TOKEN, callback(data, chat_id), prefs)
        await self.settle()

class PairingTest(BotTestCase):
    paired = False

    async def test_code_is_hashed_single_use_and_expires(self):
        code, expires = telegram_pairing.create_pairing_code()
        raw = (Path(self.tmp.name) / "telegram_state.json").read_text(encoding="utf-8")
        self.assertNotIn(code, raw)
        self.assertGreater(expires, time.time() + 500)
        self.assertEqual(telegram_pairing.extract_code(f"/start pair-{code}"), code)
        self.assertEqual(telegram_pairing.extract_code(telegram_pairing.format_code(code).lower()), code)
        self.assertTrue(telegram_pairing.check_pairing_code(code))
        self.assertFalse(telegram_pairing.check_pairing_code(code))
        code, _ = telegram_pairing.create_pairing_code()
        with patch("app.telegram_pairing.time.time", return_value=time.time() + 3600):
            self.assertFalse(telegram_pairing.check_pairing_code(code))

    async def test_brute_force_burns_the_code(self):
        code, _ = telegram_pairing.create_pairing_code()
        for _ in range(telegram_pairing.MAX_ATTEMPTS):
            self.assertFalse(telegram_pairing.check_pairing_code("AAAAAAAA"))
        self.assertFalse(telegram_pairing.pairing_active())
        self.assertFalse(telegram_pairing.check_pairing_code(code))

    async def test_deep_link_pairs_the_chat_and_shows_the_menu(self):
        code, _ = telegram_pairing.create_pairing_code()
        await self.send(f"/start pair-{code}")
        prefs = pgdata.load_preferences()
        self.assertEqual(prefs["telegram_chat_id"], str(OWNER))
        owner = pgdata.load_telegram_state()["owner"]
        self.assertEqual(owner["username"], "yono")
        welcome = next(p for m, p in self.fake.calls if m == "sendMessage" and "keyboard" in (p.get("reply_markup") or {}))
        self.assertIn("Готово", welcome["text"])
        self.assertTrue(welcome["reply_markup"]["is_persistent"])
        self.assertIn("setChatMenuButton", self.fake.methods())

    async def test_plain_code_message_also_pairs(self):
        code, _ = telegram_pairing.create_pairing_code()
        await self.send(telegram_pairing.format_code(code))
        self.assertEqual(pgdata.load_preferences()["telegram_chat_id"], str(OWNER))

    async def test_wrong_code_is_reported_and_not_paired(self):
        telegram_pairing.create_pairing_code()
        await self.send("/start pair-ZZZZ2222")
        self.assertEqual(pgdata.load_preferences()["telegram_chat_id"], "")
        self.assertTrue(any("Код не подошёл" in text for text in self.fake.sent_texts()))

    async def test_unpaired_bot_explains_how_to_connect_once(self):
        await self.send("hello")
        await self.send("hello again")
        texts = self.fake.sent_texts()
        self.assertEqual(len(texts), 1)
        self.assertIn("Подключить Telegram", texts[0])

    async def test_status_reports_phases(self):
        status = telegram_pairing.status()
        self.assertEqual(status["phase"], "offline")
        fingerprint = telegram_pairing.token_fingerprint(TOKEN)
        pgdata.update_telegram_state(lambda d: {**d, "bot": {"token_fp": fingerprint, "status": "ok",
                                                             "heartbeat": time.time(), "username": "b"}})
        self.assertEqual(telegram_pairing.status()["phase"], "unpaired")
        pgdata.save_preferences({"telegram_chat_id": "42"})
        status = telegram_pairing.status()
        self.assertEqual(status["phase"], "ready")
        self.assertEqual(status["bot"]["username"], "b")
        self.assertNotIn(TOKEN, json.dumps(status))

class AuthTest(BotTestCase):
    async def test_strangers_get_one_refusal_and_no_access(self):
        with patch.object(telegram_bot, "_answer_ai", AsyncMock()) as answer:
            await self.send("hi", chat_id=999)
            await self.send("hi", chat_id=999)
            answer.assert_not_awaited()
        texts = self.fake.sent_texts()
        self.assertEqual(len(texts), 1)
        self.assertIn("другому аккаунту", texts[0])

    async def test_groups_are_ignored_silently(self):
        await self.send("hi", chat_id=-100123, chat_type="supergroup")
        self.assertEqual(self.fake.calls, [])

    async def test_callbacks_from_strangers_do_nothing(self):
        await self.press("new", chat_id=999)
        self.assertEqual(self.fake.methods(), ["answerCallbackQuery"])

    async def test_whitelisted_user_in_own_private_chat(self):
        prefs = dict(pgdata.load_preferences(), telegram_allowed_users=["777"])
        self.assertTrue(telegram_bot._allowed(prefs, "777", "777"))
        self.assertFalse(telegram_bot._allowed(prefs, "-100", "777"))
        self.assertFalse(telegram_bot._allowed(prefs, "888", "888"))
        self.assertIn("777", telegram_bot.authorized_user_ids(prefs))
        self.assertEqual(telegram_bot.authorized_user_ids(prefs)[0], str(OWNER))

    async def test_allowed_users_are_normalised(self):
        saved = pgdata.save_preferences({"telegram_allowed_users": ["123456", " 42 ", "abc", "123456"]})
        self.assertEqual(saved["telegram_allowed_users"], ["123456"])

class MenuTest(BotTestCase):
    def seed_chats(self, count=3):
        for index in range(count):
            pgdata.upsert_chat({"id": f"c{index}", "title": f"Чат {index}", "updated": 1000 + index,
                                "messages": [{"role": "user", "content": f"вопрос {index} кот" if index == 1 else "q"},
                                             {"role": "assistant", "content": "ответ", "model": "genspark-gpt-5"}]}, "default")

    async def test_start_sends_reply_keyboard_and_home_card(self):
        await self.send("/start")
        keyboards = [p.get("reply_markup") for m, p in self.fake.calls if m == "sendMessage"]
        self.assertIn("keyboard", keyboards[0])
        labels = [b["text"] for row in keyboards[0]["keyboard"] for b in row]
        self.assertEqual(labels, ["💬 Чаты", "➕ Новый чат", "☰ Меню"])
        self.assertIn("chats:0", [b["callback_data"] for b in buttons(keyboards[1])])

    async def test_reply_keyboard_labels_are_actions_not_prompts(self):
        with patch.object(telegram_bot, "_answer_ai", AsyncMock()) as answer:
            await self.send("💬 Чаты")
            answer.assert_not_awaited()
        self.assertIn("Чаты", self.fake.last("sendMessage")["text"])

    async def test_chat_search_pagination_and_selection(self):
        self.seed_chats(12)
        await self.press("chats:0")
        markup = (self.fake.last("editMessageText") or self.fake.last("sendMessage"))["reply_markup"]
        self.assertEqual(sum(1 for b in buttons(markup) if b["callback_data"].startswith("open:")), 8)
        self.assertIn("chats:1", [b["callback_data"] for b in buttons(markup)])
        await self.press("csearch")
        await self.send("кот")
        found = self.fake.last("sendMessage")
        self.assertIn("найдено 1", found["text"])
        self.assertIn("open:c1", [b["callback_data"] for b in buttons(found["reply_markup"])])
        await self.press("open:c1")
        _, session = telegram_bot._session(str(OWNER))
        self.assertEqual(session["chat_by_profile"]["default"], "c1")

    async def test_rename_pin_and_delete(self):
        self.seed_chats(2)
        await self.press("ren:c0")
        await self.send("Новое имя")
        self.assertEqual(next(c for c in pgdata.load_chats("default") if c["id"] == "c0")["title"], "Новое имя")
        await self.press("pin:c0")
        self.assertTrue(next(c for c in pgdata.load_chats("default") if c["id"] == "c0")["pinned"])
        await self.press("del:c0")
        confirm = self.fake.last("editMessageText") or self.fake.last("sendMessage")
        self.assertIn("delok:c0", [b["callback_data"] for b in buttons(confirm["reply_markup"])])
        self.assertTrue(any(c["id"] == "c0" for c in pgdata.load_chats("default")))
        await self.press("delok:c0")
        self.assertFalse(any(c["id"] == "c0" for c in pgdata.load_chats("default")))

    async def test_long_ids_use_short_references(self):
        long_id = "chat-" + "я" * 40
        pgdata.upsert_chat({"id": long_id, "title": "Long", "updated": 5, "messages": []}, "default")
        await self.press("chats:0")
        markup = (self.fake.last("editMessageText") or self.fake.last("sendMessage"))["reply_markup"]
        data = [b["callback_data"] for b in buttons(markup) if b["callback_data"].startswith("open:")]
        self.assertTrue(data and all(len(d.encode()) <= 64 for d in data))
        await self.press(data[0])
        _, session = telegram_bot._session(str(OWNER))
        self.assertEqual(session["chat_by_profile"]["default"], long_id)

    async def test_models_grouped_with_favourites_and_selection(self):
        pgdata.save_preferences({"pinned_models": ["kiro-sonnet"]})
        await self.press("models")
        screen = self.fake.last("editMessageText") or self.fake.last("sendMessage")
        labels = [b["text"] for b in buttons(screen["reply_markup"])]

        self.assertTrue(any("⭐ " in label and "kiro-sonnet" in label for label in labels), labels)
        self.assertTrue(any(label.startswith("Genspark · 2") for label in labels))
        provider = next(b["callback_data"] for b in buttons(screen["reply_markup"]) if b["text"].startswith("Genspark"))
        await self.press(provider)
        listing = self.fake.last("editMessageText")
        choice = next(b["callback_data"] for b in buttons(listing["reply_markup"]) if "claude" in b["text"])
        await self.press(choice)
        _, session = telegram_bot._session(str(OWNER))
        self.assertEqual(session["model_by_profile"]["default"], "genspark-claude")
        self.assertEqual(session["recent_models"][0], "genspark-claude")

    async def test_settings_toggles_persist(self):
        await self.press("toggle:stream")
        self.assertEqual(pgdata.load_preferences()["telegram_stream_mode"], "draft")
        await self.press("toggle:vreply")
        self.assertEqual(pgdata.load_preferences()["telegram_voice_replies"], "voice")
        await self.press("toggle:notify")
        self.assertEqual(pgdata.load_preferences()["telegram_notify_mode"], "silent")

    async def test_pin_lockout_after_five_failures(self):
        profile_state = pgdata.load_profiles()
        state = {"sessions": {str(OWNER): {}}}
        session = {"profile_id": "default", "pending_pin_profile": "locked", "pin_failures": 0}
        with patch.object(telegram_bot, "_profiles", return_value=(profile_state, [{"id": "locked", "name": "L"}])), \
                patch.object(telegram_bot, "verify_profile_pin", return_value=False), \
                patch.object(telegram_bot, "_save_session"):
            for _ in range(5):
                await telegram_bot._handle_pending_pin(self.client, TOKEN, message("0000"), {}, state, session, "0000")
        self.assertGreater(session["pin_locked_until"], time.time())
        self.assertEqual(session["pending_pin_profile"], "")
        self.assertIn("deleteMessage", self.fake.methods())

    async def test_temporary_chat_is_not_saved(self):
        await self.press("temp")
        with patch("app.chat.chat_service.stream_completion", stream_of("Привет")):
            await self.send("секрет")
        self.assertEqual(pgdata.load_chats("default"), [])
        self.assertEqual(telegram_bot._temp_history[str(OWNER)][-1]["content"], "Привет")

    async def test_web_app_data_prompt(self):
        with patch.object(telegram_bot, "_answer_ai", AsyncMock()) as answer:
            await self.send(web_app_data={"data": json.dumps({"action": "prompt", "text": "из мини-аппа"}),
                                          "button_text": "MemuAI"})
            await asyncio.sleep(0)
        self.assertEqual(answer.await_args.args[3], "из мини-аппа")

class AnswerTest(BotTestCase):
    async def test_answer_is_saved_with_buttons(self):
        with patch("app.chat.chat_service.stream_completion", stream_of("**Ответ**", " готов")):
            await self.send("Вопрос")
        chats = pgdata.load_chats("default")
        self.assertEqual([m["role"] for m in chats[0]["messages"]], ["user", "assistant"])
        self.assertEqual(chats[0]["messages"][0]["via"], "telegram")
        final = self.fake.last("sendMessage")
        self.assertEqual(final["parse_mode"], "HTML")
        self.assertIn("<b>Ответ</b>", final["text"])
        data = [b["callback_data"] for b in buttons(final["reply_markup"])]
        self.assertTrue(any(d.startswith("retry:") for d in data))

        self.assertEqual(len(final["reply_markup"]["inline_keyboard"]), 1)
        expand = next(d for d in data if d.startswith("fx:"))
        await self.press(expand)
        unfolded = self.fake.last("editMessageReplyMarkup")["reply_markup"]
        self.assertTrue(any(b["callback_data"].startswith("cont:") for b in buttons(unfolded)))

    async def test_internal_blocks_never_reach_telegram(self):
        with patch("app.chat.chat_service.stream_completion",
                   stream_of("Ок", "\n```schedule\n{\"name\": \"x\"}\n```")):
            await self.send("напомни")
        self.assertNotIn("schedule", self.fake.last("sendMessage")["text"])

    async def test_busy_chat_offers_stop(self):
        telegram_bot._generations[str(OWNER)] = telegram_bot._Generation(str(OWNER))
        await telegram_bot._answer_ai(self.client, TOKEN, str(OWNER), "x", pgdata.load_preferences())
        self.assertIn("stop", [b["callback_data"] for b in buttons(self.fake.last("sendMessage")["reply_markup"])])

    async def test_stop_button_and_native_stop_update(self):
        release = asyncio.Event()

        async def slow(*_a, **_k):
            yield {"choices": [{"delta": {"content": "часть"}}]}
            await release.wait()
            yield {"choices": [{"delta": {"content": " ещё"}}]}

        with patch("app.chat.chat_service.stream_completion", slow):
            task = asyncio.create_task(telegram_bot._answer_ai(self.client, TOKEN, str(OWNER), "x",
                                                               pgdata.load_preferences()))
            for _ in range(20):
                await asyncio.sleep(0.01)
            generation = telegram_bot._generations[str(OWNER)]
            generation.draft_ids.add(55)
            self.assertFalse(telegram_bot._stop_generation(str(OWNER), draft_id=99))
            runner = telegram_bot._BotRunner(self.client)
            runner.token = TOKEN
            runner.dispatch({"update_id": 1, "stopped_message_generation": {"chat": {"id": OWNER}, "draft_id": 55}},
                            pgdata.load_preferences())
            release.set()
            await task
        final = self.fake.last("sendMessage")["text"]
        self.assertIn("часть", final)
        self.assertIn("Остановлено", final)
        self.assertNotIn(" ещё", final)

    async def test_edit_last_message_regenerates(self):
        with patch("app.chat.chat_service.stream_completion", stream_of("A1")):
            await self.send("первый", message_id=21)
        with patch("app.chat.chat_service.stream_completion", stream_of("A2")):
            await telegram_bot._handle_edited(self.client, TOKEN, message("исправленный", message_id=21),
                                              pgdata.load_preferences())
            await self.settle()
        rows = pgdata.load_chats("default")[0]["messages"]
        self.assertEqual([(r["role"], r["content"]) for r in rows],
                         [("user", "исправленный"), ("assistant", "A2")])

    async def test_retry_replaces_answer(self):
        with patch("app.chat.chat_service.stream_completion", stream_of("A1")):
            await self.send("вопрос")
        chat_id = pgdata.load_chats("default")[0]["id"]
        with patch("app.chat.chat_service.stream_completion", stream_of("A2")):
            await self.press(f"retry:{chat_id}")
        rows = pgdata.load_chats("default")[0]["messages"]
        self.assertEqual([r["content"] for r in rows], ["вопрос", "A2"])

    async def test_stale_message_asks_before_answering(self):
        with patch.object(telegram_bot, "_answer_ai", AsyncMock()) as answer:
            prefs = pgdata.load_preferences()
            old = message("старое", date=int(time.time()) - 3600)
            await telegram_bot._handle_message(self.client, TOKEN, old, prefs)
            answer.assert_not_awaited()
            self.assertIn("stale:send", [b["callback_data"] for b in buttons(self.fake.last("sendMessage")["reply_markup"])])
            await telegram_bot._handle_callback(self.client, TOKEN, callback("stale:send"), prefs)
            await asyncio.sleep(0)
        self.assertEqual(answer.await_args.args[3], "старое")

    async def test_known_errors_are_actionable_but_never_leak(self):
        class ProxyOff(Exception):
            err_type = "proxy_disabled"

        with patch("app.chat.chat_service.stream_completion", stream_of(fail=ProxyOff("sk-secret"))):
            await self.send("x")
        text = self.fake.last("sendMessage")["text"]
        self.assertIn("прокси выключен", text)
        self.assertNotIn("sk-secret", json.dumps(self.fake.calls, ensure_ascii=False))

    async def test_live_draft_streaming_end_to_end(self):
        pgdata.save_preferences({"telegram_stream_mode": "draft"})

        async def slow(*_a, **_k):
            for index in range(8):
                await asyncio.sleep(0.25)
                yield {"choices": [{"delta": {"content": f"часть {index}. "}}]}

        with patch("app.chat.chat_service.stream_completion", slow):
            await self.send("пиши")
        drafts = [p for m, p in self.fake.calls if m == "sendMessageDraft"]
        self.assertGreaterEqual(len(drafts), 2)
        self.assertEqual(len({p["draft_id"] for p in drafts}), 1)
        self.assertEqual(self.fake.calls[-1][0], "sendMessage")
        self.assertIn("часть 7.", self.fake.calls[-1][1]["text"])

    async def test_edit_streaming_with_stop_button(self):
        pgdata.save_preferences({"telegram_stream_mode": "edit"})
        gate = asyncio.Event()

        async def slow(*_a, **_k):
            yield {"choices": [{"delta": {"content": "начало"}}]}
            await asyncio.sleep(1.5)
            await gate.wait()
            yield {"choices": [{"delta": {"content": " конец"}}]}

        with patch("app.chat.chat_service.stream_completion", slow):
            task = asyncio.create_task(telegram_bot._answer_ai(self.client, TOKEN, str(OWNER), "x",
                                                               pgdata.load_preferences(), reply_to=7))
            await asyncio.sleep(1.6)
            placeholder = next(p for m, p in self.fake.calls if m == "sendMessage")
            self.assertEqual(placeholder["reply_markup"]["inline_keyboard"][0][0]["callback_data"], "stop")
            self.assertEqual(placeholder["reply_parameters"]["message_id"], 7)
            await telegram_bot._handle_callback(self.client, TOKEN, callback("stop"), pgdata.load_preferences())
            gate.set()
            await task
        final = self.fake.last("editMessageText")
        self.assertIn("начало", final["text"])
        self.assertIn("Остановлено", final["text"])
        self.assertTrue(any(b["callback_data"].startswith("retry:") for b in buttons(final["reply_markup"])))

    async def test_voice_reply_when_enabled(self):
        pgdata.save_preferences({"telegram_voice_replies": "always"})
        with patch("app.chat.chat_service.stream_completion", stream_of("Скажу")), \
                patch.object(telegram_media, "synthesize", AsyncMock(return_value=(b"ID3mp3", "audio/mpeg"))):
            await self.send("голосом")
        self.assertIn("sendVoice", self.fake.methods())

class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

class StreamingTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        telegram_stream._DRAFTS_UNSUPPORTED["value"] = False
        self.fake = FakeTelegram()
        self.client = self.fake.client()
        self.api = BotApi(self.client, TOKEN)

    async def asyncTearDown(self):
        await self.client.aclose()
        telegram_stream._DRAFTS_UNSUPPORTED["value"] = False

    async def test_draft_frames_are_throttled(self):
        clock = FakeClock()
        stream = StreamReply(self.api, "1", mode="draft", clock=clock)
        await stream._draft("")
        for index in range(10):
            stream.update("слово " * (index + 1))
            if clock() >= stream.next_frame:
                await stream.flush()
            clock.now += 0.2
        drafts = [p for m, p in self.fake.calls if m == "sendMessageDraft"]
        self.assertLessEqual(len(drafts), 4)
        self.assertTrue(all(p["can_stop"] for p in drafts))
        self.assertEqual(drafts[0]["text"], "")
        await stream.finish("слово " * 10)
        self.assertEqual(self.fake.calls[-1][0], "sendMessage")

    async def test_429_pushes_the_next_frame_back(self):
        clock = FakeClock()
        self.fake.responders["sendMessageDraft"] = error(429, "Too Many Requests: retry after 7", 7)
        stream = StreamReply(self.api, "1", mode="draft", clock=clock)
        stream.update("text")
        await stream.flush()
        self.assertGreaterEqual(stream.next_frame, clock() + 7)
        self.assertGreater(stream.interval, telegram_stream.DRAFT_INTERVAL)

    async def test_unsupported_drafts_fall_back_to_edits(self):
        self.fake.responders["sendMessageDraft"] = error(404, "Not Found: method not found")
        stream = StreamReply(self.api, "1", mode="draft", stop_markup={"inline_keyboard": [[{"text": "⏹", "callback_data": "stop"}]]})
        stream.update("hello")
        await stream.flush()
        self.assertEqual(stream.mode, "edit")
        self.assertIn("editMessageText", self.fake.methods())
        edit = self.fake.last("editMessageText")
        self.assertEqual(edit["reply_markup"]["inline_keyboard"][0][0]["callback_data"], "stop")
        await stream.finish("hello world", {"inline_keyboard": [[{"text": "↻", "callback_data": "retry:x"}]]})
        final = self.fake.last("editMessageText")
        self.assertEqual(final["text"], "hello world")
        self.assertEqual(final["reply_markup"]["inline_keyboard"][0][0]["callback_data"], "retry:x")
        self.assertTrue(telegram_stream._DRAFTS_UNSUPPORTED["value"])

    async def test_edit_mode_skips_frames_on_429_without_losing_the_answer(self):
        clock = FakeClock()
        stream = StreamReply(self.api, "1", mode="edit", clock=clock)
        await stream._placeholder()
        self.fake.responders["editMessageText"] = error(429, "Too Many Requests: retry after 3", 3)
        stream.update("partial")
        await stream.flush()
        self.assertGreaterEqual(stream.next_frame, clock() + 3)
        del self.fake.responders["editMessageText"]
        await stream.finish("final answer")
        self.assertEqual(self.fake.last("editMessageText")["text"], "final answer")

    async def test_long_answer_is_committed_in_parts(self):
        stream = StreamReply(self.api, "1", mode="draft")
        paragraphs = "\n\n".join(f"Абзац {i}: " + "текст " * 60 for i in range(40))
        stream.update(paragraphs)
        await stream.flush()
        committed = [p["text"] for m, p in self.fake.calls if m == "sendMessage"]
        self.assertGreaterEqual(len(committed), 3)
        await stream.finish(paragraphs, {"inline_keyboard": [[{"text": "↻", "callback_data": "retry:x"}]]})
        messages = [p for m, p in self.fake.calls if m == "sendMessage"]
        self.assertTrue(all(telegram_format.utf16_len(p["text"]) <= 4096 for p in messages))
        joined = " ".join(telegram_format.html_to_plain(p["text"]) for p in messages)
        self.assertEqual(joined.split(), paragraphs.split())
        self.assertIn("reply_markup", messages[-1])

    async def test_code_fence_spanning_parts_is_reopened(self):
        stream = StreamReply(self.api, "1", mode="off")
        code = "```python\n" + "\n".join(f"x_{i} = {i}" for i in range(600)) + "\n```\nend"
        stream.update(code)
        await stream.flush()
        await stream.finish(code)
        messages = [p["text"] for m, p in self.fake.calls if m == "sendMessage"]
        self.assertGreater(len(messages), 1)
        for text in messages[:-1]:
            self.assertTrue(text.startswith('<pre><code class="language-python">'), text[:60])

    async def test_parse_error_falls_back_to_plain_text(self):
        calls = {"n": 0}

        def respond(payload):
            calls["n"] += 1
            if payload.get("parse_mode"):
                return httpx.Response(400, json={"ok": False, "error_code": 400,
                                                 "description": "Bad Request: can't parse entities"})
            return {"message_id": 5}

        self.fake.responders["sendMessage"] = respond
        stream = StreamReply(self.api, "1", mode="off")
        await stream.finish("**bold** <tag>")
        self.assertEqual(self.fake.calls[-1][1]["text"], "bold <tag>")

def balanced(text):
    stack = []
    for closing, name in re.findall(r"<(/?)([\w-]+)[^>]*>", text):
        if closing:
            if not stack or stack[-1] != name:
                return False
            stack.pop()
        else:
            stack.append(name)
    return not stack

class FormatTest(unittest.TestCase):
    def test_tables_lists_math_and_code(self):
        html = telegram_format.markdown_to_telegram_html(
            "| A | Число |\n|---|---:|\n| x | 5 |\n\n- a\n  - b\n    - c\n\n$x^2 \\le \\alpha$ and $5\n\n"
            "```js\nif (a < b) {}\n```")
        self.assertIn("<pre>A │ Число", html)
        self.assertIn("\u00a0\u00a0\u00a0◦ b", html)
        self.assertIn("<code>x² ≤ α</code>", html)
        self.assertIn("$5", html)
        self.assertIn('<pre><code class="language-js">if (a &lt; b) {}</code></pre>', html)

    def test_wide_tables_become_cards(self):
        html = telegram_format.markdown_to_telegram_html(
            "| Название | Очень длинное описание колонки | Ещё одна колонка |\n|---|---|---|\n"
            "| Первый | значение которое очень длинное | третья |")
        self.assertNotIn("<pre>", html)
        self.assertIn("<b>Первый</b>", html)
        self.assertIn("Очень длинное описание колонки: значение", html)

    def test_split_never_breaks_tags_or_entities(self):
        source = ("**" + "жирный &amp; текст " * 50 + "**\n\n") * 20 + "```py\n" + "a = '<&>'\n" * 900 + "```"
        html = telegram_format.markdown_to_telegram_html(source)
        parts = telegram_format.split_telegram_html(html)
        self.assertGreater(len(parts), 3)
        for part in parts:
            self.assertLessEqual(telegram_format.utf16_len(part), telegram_format.MAX_LENGTH)
            self.assertTrue(balanced(part), part[:80])
            self.assertIsNone(re.search(r"&[#\w]*$", part))

    def test_emoji_count_as_two_units(self):
        parts = telegram_format.split_telegram_html("😀" * 3000, limit=4000)
        self.assertTrue(all(telegram_format.utf16_len(p) <= 4000 for p in parts))
        self.assertEqual(sum(len(p) for p in parts), 3000)

    def test_partial_render_closes_open_fence(self):
        html = telegram_format.render_partial("Код:\n```py\nprint(1)\n")
        self.assertTrue(balanced(html))
        self.assertIn('<code class="language-py">print(1)</code>', html)

    def test_raw_html_from_model_is_escaped(self):
        html = telegram_format.markdown_to_telegram_html("<script>alert(1)</script> [x](javascript:alert(1))")
        self.assertNotIn("<script>", html)
        self.assertNotIn('href="javascript', html)

class AttachmentTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fake = FakeTelegram()
        self.fake.responders["getFile"] = lambda p: {"file_path": "docs/" + p["file_id"], "file_size": 10}
        self.client = self.fake.client()
        self.api = BotApi(self.client, TOKEN)

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_photo_becomes_vision_input_and_stored_file(self):
        self.fake.files["p1"] = b"\xff\xd8jpeg"
        out = await telegram_media.collect(self.api, {"photo": [{"file_id": "p0", "file_size": 5},
                                                                {"file_id": "p1", "file_size": 9}]}, {}, "ru")
        self.assertEqual(out.images[0], "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8jpeg").decode())
        self.assertEqual(out.files[0]["name"], "telegram-photo.jpg")

    async def test_text_document_is_read_as_untrusted(self):
        self.fake.files["d1"] = "привет из файла".encode("utf-8")
        out = await telegram_media.collect(self.api, {"document": {"file_id": "d1", "file_name": "notes.md",
                                                                   "mime_type": "text/markdown"}}, {}, "ru")
        self.assertIn("<untrusted_file>\nпривет из файла", out.text)

    async def test_docx_text_is_extracted(self):
        import io, zipfile
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("word/document.xml", "<w:document><w:p>Первый</w:p><w:p>Второй</w:p></w:document>")
        self.assertEqual(telegram_media.extract_document_text("a.docx", "", buffer.getvalue()), "Первый\nВторой")

    async def test_too_large_attachment_is_refused_before_download(self):
        out = await telegram_media.collect(self.api, {"document": {"file_id": "big", "file_name": "a.bin",
                                                                   "file_size": 50 * 1024 * 1024}}, {}, "en")
        self.assertTrue(out.errors)
        self.assertNotIn("getFile", self.fake.methods())

    async def test_voice_is_transcribed(self):
        self.fake.files["v1"] = b"OggS"
        with patch.object(telegram_media, "transcribe", AsyncMock(return_value="расшифровка")) as stt:
            out = await telegram_media.collect(self.api, {"voice": {"file_id": "v1", "duration": 3,
                                                                    "mime_type": "audio/ogg"}}, {}, "ru")
        self.assertEqual(out.transcript, "расшифровка")
        self.assertTrue(out.voice)
        self.assertEqual(stt.await_args.args[1], "audio/ogg")

    async def test_voice_input_can_be_disabled(self):
        out = await telegram_media.collect(self.api, {"voice": {"file_id": "v1", "duration": 3}},
                                           {"telegram_voice_input": False}, "en")
        self.assertEqual(out.transcript, "")
        self.assertNotIn("getFile", self.fake.methods())

class NotificationTest(unittest.IsolatedAsyncioTestCase):
    base = {"telegram_enabled": True, "telegram_bot_token": TOKEN, "telegram_chat_id": "42",
            "telegram_notify_mode": "sound"}

    async def run_notify(self, prefs, **kwargs):
        sent = []

        async def fake_send(_client, _token, chat_id, body, keyboard=None, **kw):
            sent.append({"chat": chat_id, "body": body, "keyboard": keyboard, **kw})

        with patch.object(telegram_bot, "load_preferences", return_value=prefs), \
                patch.object(telegram_bot, "_send", fake_send), \
                patch.object(telegram_bot, "_find_chat_any_profile", AsyncMock(return_value=(None, ""))):
            await telegram_bot.send_notification("Готово", kwargs.pop("text", "текст"), **kwargs)
        return sent

    async def test_categories_can_be_switched_off(self):
        self.assertEqual(await self.run_notify(dict(self.base, telegram_notify_schedule=False), kind="schedule"), [])
        self.assertEqual(len(await self.run_notify(dict(self.base), kind="schedule")), 1)
        self.assertEqual(await self.run_notify(dict(self.base, telegram_notify_work=False), kind="work"), [])

    async def test_modules_keep_round19_semantics(self):
        sent = await self.run_notify(dict(self.base, telegram_notify_mode="off"), kind="module")
        self.assertEqual(len(sent), 1)
        self.assertTrue(sent[0]["silent"])
        self.assertEqual(await self.run_notify(dict(self.base, telegram_notify_modules=False), kind="module"), [])

    async def test_quiet_hours_make_notifications_silent(self):
        prefs = dict(self.base, telegram_quiet_hours=True, telegram_quiet_from="00:00", telegram_quiet_to="23:59")
        sent = await self.run_notify(prefs)
        self.assertTrue(sent[0]["silent"])
        self.assertFalse((await self.run_notify(dict(self.base)))[0]["silent"])

    def test_quiet_hours_across_midnight(self):
        from datetime import datetime
        prefs = {"telegram_quiet_hours": True, "telegram_quiet_from": "23:00", "telegram_quiet_to": "08:00"}
        self.assertTrue(telegram_bot._in_quiet_hours(prefs, datetime(2026, 1, 1, 23, 30)))
        self.assertTrue(telegram_bot._in_quiet_hours(prefs, datetime(2026, 1, 1, 7, 59)))
        self.assertFalse(telegram_bot._in_quiet_hours(prefs, datetime(2026, 1, 1, 12, 0)))

    async def test_long_completion_is_a_preview_with_open_button(self):
        sent = await self.run_notify(dict(self.base), text="слово " * 2000, source_chat_id="c-1")
        self.assertLess(len(sent[0]["body"]), 2200)
        self.assertIn("nopen:c-1", [b["callback_data"] for b in buttons(sent[0]["keyboard"])])

    async def test_schedule_results_are_sent_in_full(self):
        sent = await self.run_notify(dict(self.base), kind="schedule", text="слово " * 2000)
        self.assertGreater(len(sent[0]["body"]), 10000)

class MiniAppHookTest(BotTestCase):
    def install_stub(self, url):
        telegram_bot._webapp_cache["at"] = 0.0
        module = types.ModuleType("app.tg_webapp")
        module.webapp_url = lambda: url
        module.webapp_enabled = lambda: url is not None
        self.patches.append(patch.dict(sys.modules, {"app.tg_webapp": module}))
        self.patches[-1].start()
        import app
        self.patches.append(patch.object(app, "tg_webapp", module, create=True))
        self.patches[-1].start()

    async def test_absent_module_is_tolerated(self):
        self.assertIsNone(telegram_bot._webapp_url())

    async def test_url_sets_menu_button_and_inline_button(self):
        self.install_stub("https://mini.example/app")
        self.assertEqual(telegram_bot._webapp_url(), "https://mini.example/app")
        runner = telegram_bot._BotRunner(self.client)
        runner.token = TOKEN
        await runner.check_webapp(pgdata.load_preferences(), force=True)
        menu = self.fake.last("setChatMenuButton")
        self.assertEqual(menu["menu_button"], {"type": "web_app", "text": "MemuAI",
                                               "web_app": {"url": "https://mini.example/app"}})
        self.assertEqual(menu["chat_id"], str(OWNER))

        keyboard = telegram_bot._reply_keyboard(pgdata.load_preferences(), telegram_bot._webapp_url())
        self.assertFalse(any("web_app" in b for row in keyboard["keyboard"] for b in row))
        inline = telegram_bot._nav_keyboard(pgdata.load_preferences(), url=telegram_bot._webapp_url())
        self.assertIn({"text": "📱 Открыть MemuAI", "web_app": {"url": "https://mini.example/app"}},
                      [b for row in inline["inline_keyboard"] for b in row])

    async def test_plain_http_urls_are_rejected(self):
        self.install_stub("http://insecure.example/")
        self.assertIsNone(telegram_bot._webapp_url())

    async def test_url_change_signal_is_thread_safe(self):
        runner = telegram_bot._BotRunner(self.client)
        telegram_bot._webapp_signal["loop"] = asyncio.get_running_loop()
        telegram_bot._webapp_signal["event"] = runner.event
        import threading
        threading.Thread(target=telegram_bot.on_webapp_url_changed, args=("https://x.example",)).start()
        await asyncio.wait_for(runner.event.wait(), 2)
        telegram_bot._webapp_signal.update(loop=None, event=None)

class LoopTest(BotTestCase):
    async def test_offset_is_persisted_and_restored(self):
        self.fake.responders["getUpdates"] = lambda p: [{"update_id": 41, "message": message("/help")}]
        runner = telegram_bot._BotRunner(self.client)
        await runner.activate(TOKEN, pgdata.load_preferences())
        await runner.poll_once(pgdata.load_preferences())
        await asyncio.gather(*runner.handlers, return_exceptions=True)
        self.assertEqual(pgdata.load_telegram_state()["poll"]["offset"], 42)
        poll = self.fake.last("getUpdates")
        self.assertIn("stopped_message_generation", poll["allowed_updates"])
        self.assertEqual(poll["timeout"], 25)
        fresh = telegram_bot._BotRunner(self.client)
        await fresh.activate(TOKEN, pgdata.load_preferences())
        self.assertEqual(fresh.offset, 42)
        self.assertEqual(pgdata.load_telegram_state()["bot"]["username"], "memu_test_bot")

    async def test_unauthorized_token_is_reported(self):
        self.fake.responders["getMe"] = error(401, "Unauthorized")
        runner = telegram_bot._BotRunner(self.client)
        with self.assertRaises(TelegramError):
            await runner.activate(TOKEN, pgdata.load_preferences())
        self.assertEqual(pgdata.load_telegram_state()["bot"]["status"], "unauthorized")
        self.assertEqual(telegram_pairing.status()["phase"], "bad_token")

    async def test_backoff_honours_retry_after_and_grows(self):
        self.assertEqual(telegram_bot._poll_backoff(1, TelegramError("x", error_code=429, retry_after=9)), 9)
        self.assertEqual(telegram_bot._poll_backoff(9, RuntimeError("down")), 60)

    async def test_essential_calls_wait_out_short_flood_limits(self):
        attempts = {"n": 0}

        def respond(payload):
            attempts["n"] += 1
            if attempts["n"] == 1:
                return httpx.Response(429, json={"ok": False, "error_code": 429, "description": "Too Many Requests",
                                                 "parameters": {"retry_after": 0.01}})
            return {"message_id": 9}

        self.fake.responders["sendMessage"] = respond
        result = await BotApi(self.client, TOKEN).call("sendMessage", essential=True, chat_id=1, text="x")
        self.assertEqual(result["message_id"], 9)
        self.assertEqual(attempts["n"], 2)

    async def test_shutdown_marks_bot_offline(self):
        runner = telegram_bot._BotRunner(self.client)
        await runner.activate(TOKEN, pgdata.load_preferences())
        self.assertTrue(telegram_pairing.status()["online"])
        await runner.shutdown()
        self.assertFalse(telegram_pairing.status()["online"])

class RedactionTest(unittest.TestCase):
    def test_tokens_never_reach_logs_or_errors(self):
        text = f"POST https://api.telegram.org/bot{TOKEN}/getUpdates failed"
        self.assertNotIn(TOKEN, redact(text))
        self.assertNotIn(TOKEN.split(":")[1], TelegramError(text).description)
        records = []

        class Keep(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        logger = logging.getLogger("httpx")
        handler = Keep()
        logger.addHandler(handler)
        old = logger.level
        logger.setLevel(logging.INFO)
        try:
            from app.telegram_api import install_log_redaction
            install_log_redaction()
            logger.info("HTTP Request: %s", text)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old)
        self.assertTrue(records)
        self.assertNotIn(TOKEN.split(":")[1], records[0])

if __name__ == "__main__":
    unittest.main()
