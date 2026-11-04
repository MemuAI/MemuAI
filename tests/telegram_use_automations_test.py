"""Telegram Use automations and several accounts. No live Telegram account or model is used."""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import os
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app import telegram_use as use
from app import telegram_use_ai as ai
from app import telegram_use_automations as automations
from app import telegram_use_config as config
from app import telegram_use_images as tg_images

NOW = dt.datetime.now(dt.timezone.utc)
# A 1x1 transparent PNG, the smallest valid image for tests.
PNG_1X1 = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8Xw8AAwMCAO+ip1sAAAAASUVORK5CYII=")

def message(mid, text="", *, sender=7, chat=7, outgoing=False, voice=False, photo=False, grouped=None, age=0, mentioned=False):
    media = object() if (voice or photo) else None
    row = types.SimpleNamespace(id=mid, raw_text=text, sender_id=sender, chat_id=chat, out=outgoing,
                                date=NOW - dt.timedelta(seconds=age), media=media, document=None,
                                photo=object() if photo else None, voice=object() if voice else None, audio=None, video=None,
                                video_note=None, sticker=None, gif=None, poll=None, geo=None, contact=None,
                                reply_to_msg_id=None, fwd_from=None, grouped_id=grouped, mentioned=mentioned, post=False,
                                entities=[], sender=None)
    row.get_reply_message = AsyncMock(return_value=None)
    return row

def person(uid=7, name="Иван", username="ivan", bot=False):
    return types.SimpleNamespace(id=uid, first_name=name, last_name="", username=username, bot=bot, title="")

def event(msg, chat=7, *, private=True, group=False, channel=False, chat_entity=None, sender=None):
    return types.SimpleNamespace(message=msg, chat_id=chat, sender_id=msg.sender_id, is_private=private, is_group=group,
                                 is_channel=channel, chat=chat_entity or person(), sender=sender or person(msg.sender_id))

def dialog(did, name, *, kind="user", username=""):
    entity = types.SimpleNamespace(username=username, is_self=kind == "saved", bot=kind == "bot", broadcast=kind == "channel")
    return types.SimpleNamespace(id=did, name=name, entity=entity, input_entity=did, unread_count=1, pinned=False, archived=False,
                                 is_group=kind == "group", is_channel=kind in ("channel", "group") and did < -1000)

class Client:
    def __init__(self, me_id=42, username="me", name="Me"):
        self.me_id, self.username, self.name = me_id, username, name
        self.connected = self.authorized = True
        self.rows: dict[int, list] = {}
        self.dialogs = [dialog(me_id, name, kind="saved"), dialog(7, "Иван", username="ivan")]
        self.next_id = 500
        self.send_message = AsyncMock(side_effect=self._send)
        self.send_file = AsyncMock(side_effect=self._send_file)
        self.forward_messages = AsyncMock(return_value=[types.SimpleNamespace(id=900)])
        self.send_read_acknowledge = AsyncMock()
        self.log_out = AsyncMock()
        self.sign_in = AsyncMock(side_effect=lambda **kwargs: setattr(self, "authorized", True))
        self.send_code_request = AsyncMock(return_value=types.SimpleNamespace(phone_code_hash="hash"))
    async def _send(self, chat, text, **kwargs):
        self.next_id += 1
        return types.SimpleNamespace(id=self.next_id)
    async def _send_file(self, chat, files, **kwargs):
        self.next_id += 1
        rows = files if isinstance(files, list) else [files]
        return [types.SimpleNamespace(id=self.next_id + i) for i, _ in enumerate(rows)]
    def is_connected(self): return self.connected
    async def connect(self): self.connected = True
    async def disconnect(self): self.connected = False
    async def is_user_authorized(self): return self.authorized
    async def get_me(self):
        return types.SimpleNamespace(id=self.me_id, first_name=self.name, last_name="", username=self.username, premium=False)
    async def get_input_entity(self, value):
        if value in ("me", "self"): return self.me_id
        if isinstance(value, int) or str(value).lstrip("-").isdigit(): return int(value)
        raise ValueError("Unknown")
    async def iter_dialogs(self, limit=1000):
        for row in self.dialogs[:limit]: yield row
    async def iter_messages(self, chat, limit=50, offset_id=0, search="", min_id=0):
        rows = [m for m in self.rows.get(int(chat), []) if m.id > min_id and (not offset_id or m.id < offset_id)
                and (not search or search in m.raw_text)]
        for row in rows[:limit]: yield row
    async def get_messages(self, chat, ids):
        return next((m for rows in self.rows.values() for m in rows if m.id == ids), None)

async def settle(runtime):
    for _ in range(100):
        tasks = [t for r in [runtime, *runtime.children.values()] for t in r._tasks]
        if not tasks:
            break
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0)

def chat(cid=7, name="Иван", kind="user", username="ivan"):
    return {"id": cid, "name": name, "kind": kind, "username": username}

class Rules(unittest.TestCase):
    def test_defaults_follow_the_event(self):
        voice = automations.normalize({"when": {"type": "voice", "chats": [7]}})
        self.assertEqual((voice["do"]["action"], voice["do"]["to"]), ("voice", "reply"))
        post = automations.normalize({"when": {"type": "post", "scope": "any"}})
        self.assertEqual((post["when"]["scope"], post["do"]["action"], post["do"]["to"]), ("channels", "summary", "saved"))
        answer = automations.normalize({"when": {"type": "message", "chats": [7]}, "do": {"action": "answer"}})
        self.assertEqual((answer["do"]["context"], answer["limits"]["cooldown"]), (20, 30))
        digest = automations.normalize({"when": {"type": "schedule", "times": ["9:05", "25:00"], "chats": [7]}, "do": {"action": "digest", "to": "reply"}})
        self.assertEqual((digest["when"]["times"], digest["do"]["to"], digest["do"]["context"]), (["09:05"], "saved", 300))
        self.assertEqual(automations.normalize({"when": {"type": "voice", "chats": [7]}})["id"],
                         automations.normalize({"when": {"type": "voice", "chats": [7]}})["id"])

    def test_invalid_rules_raise_or_pause(self):
        for broken in ({"when": {"type": "message"}}, {"when": {"type": "keyword", "scope": "any"}},
                       {"when": {"type": "schedule", "chats": [7]}, "do": {"action": "generate"}},
                       {"when": {"type": "message", "chats": [7]}, "do": {"action": "command", "command": "ask"}},
                       {"when": {"type": "message", "chats": [7]}, "do": {"action": "send", "to": "chat"}}):
            with self.subTest(rule=broken):
                with self.assertRaises(ValueError):
                    automations.normalize(broken)
                self.assertFalse(automations.normalize(broken, strict=False)["enabled"])
        with self.assertRaisesRegex(ValueError, "account"):
            automations.normalize({"when": {"type": "voice", "chats": [7]}, "account": "abcdef12"}, accounts={"main"})
        rows = automations.normalize_list([{"id": "aaaaaa1", "when": {"type": "voice", "chats": [7]}}] * 2 + ["bad"])
        self.assertEqual(len(rows), 1)

    def test_matching_scopes_senders_words_and_window(self):
        rule = automations.normalize({"when": {"type": "message", "scope": "groups", "senders": [9], "keywords": "отчёт, report",
                                                "exclude": ["спам"]}, "do": {"action": "notify"}})
        facts = {"chat_id": -5, "chat_kind": "group", "sender_id": 9, "text": "Готов ОТЧЁТ", "kinds": {"text"}}
        self.assertTrue(automations.matches(rule, facts))
        for change in ({"sender_id": 8}, {"chat_kind": "user"}, {"text": "отчёт спам"}, {"text": "привет"}, {"own": True},
                       {"from_bot": True}):
            with self.subTest(change=change):
                self.assertFalse(automations.matches(rule, {**facts, **change}))
        saved = automations.normalize({"when": {"type": "voice", "chats": [42]}})
        self.assertTrue(automations.matches(saved, {"chat_id": 42, "chat_kind": "saved", "own": True, "kinds": {"voice"}}))
        night = {**rule, "only": {"from": "22:00", "to": "07:00", "days": []}}
        at = lambda h: time.mktime((2025, 6, 2, h, 30, 0, 0, 0, -1))
        self.assertTrue(automations.matches(night, facts, at(23)))
        self.assertTrue(automations.matches(night, facts, at(3)))
        self.assertFalse(automations.matches(night, facts, at(12)))

    def test_keyword_wildcard_matches_declensions(self):
        rule = automations.normalize({"when": {"type": "keyword", "scope": "chats", "chats": [-5],
                                                "keywords": "счёт*, книг*", "exclude": ["*фактур*"]},
                                      "do": {"action": "send", "text": "x", "to": "reply"}})
        facts = {"chat_id": -5, "chat_kind": "group", "sender_id": 9, "kinds": {"text"}}
        for text in ("выставь счёт", "оплата счёта", "по счёту", "книга", "книгу", "книги"):
            with self.subTest(text=text):
                self.assertTrue(automations.matches(rule, {**facts, "text": text}))
        for text in ("считать", "счета-фактуры", "привет"):
            with self.subTest(text=text):
                self.assertFalse(automations.matches(rule, {**facts, "text": text}))
        # Plain keywords keep the old substring match; '.' stays literal, not a regex.
        plain = automations.normalize({"when": {"type": "keyword", "scope": "chats", "chats": [-5],
                                                 "keywords": "a.c"}, "do": {"action": "send", "text": "x", "to": "reply"}})
        self.assertTrue(automations.matches(plain, {**facts, "text": "a.c"}))
        self.assertFalse(automations.matches(plain, {**facts, "text": "abc"}))
        # An own message still needs include_own, wildcard or not.
        own = {**facts, "text": "выставь счёт", "own": True}
        self.assertFalse(automations.matches(rule, own))
        included = automations.normalize({"when": {"type": "keyword", "scope": "chats", "chats": [-5],
                                                    "keywords": "счёт*", "include_own": True},
                                          "do": {"action": "send", "text": "x", "to": "reply"}})
        self.assertTrue(automations.matches(included, own))

    def test_images_are_kept_sanitized_and_capped(self):
        good = {"hash": "a" * 64, "name": "cat.png", "size": 100, "ext": "png"}
        rule = automations.normalize({"when": {"type": "keyword", "scope": "chats", "chats": [-5], "keywords": "x"},
                                      "do": {"action": "send", "text": "hi", "images": [
                                          good, {**good, "name": "dup.png"},  # duplicate hash: dropped
                                          {"hash": "zz", "name": "bad.png", "size": 1, "ext": "png"},
                                          {"hash": "b" * 64, "name": "bad.exe", "size": 1, "ext": "exe"},
                                          {"hash": "c" * 64, "name": "huge.png", "size": 11 * 1024 * 1024, "ext": "png"},
                                          {"hash": "d" * 64, "name": "no-size.png", "ext": "png"},
                                          *[{"hash": f"{i:064x}", "name": f"{i}.png", "size": 1, "ext": "png"} for i in range(20)]]}})
        self.assertEqual(rule["do"]["images"], [good] + [{"hash": f"{i:064x}", "name": f"{i}.png", "size": 1, "ext": "png"} for i in range(9)])

    def test_send_allows_images_without_text(self):
        base = {"when": {"type": "keyword", "scope": "chats", "chats": [-5], "keywords": "x"}}
        img = {"hash": "a" * 64, "name": "cat.png", "size": 100, "ext": "png"}
        rule = automations.normalize({**base, "do": {"action": "send", "text": "", "images": [img]}})
        self.assertEqual(rule["do"]["images"], [img])
        with self.assertRaises(ValueError):
            automations.normalize({**base, "do": {"action": "send", "text": "  "}})

    def test_schedule_due_once_within_grace(self):
        rule = automations.normalize({"when": {"type": "schedule", "times": ["09:00"], "chats": [7]}, "do": {"action": "digest"}})
        slot = time.mktime((2025, 6, 2, 9, 0, 0, 0, 0, -1))
        self.assertEqual(automations.due(rule, slot - 60, slot + 30), slot)
        self.assertEqual(automations.due(rule, slot + 30, slot + 90), 0)
        self.assertEqual(automations.due(rule, slot - 60, slot + 3 * 3600), 0)
        weekend = {**rule, "when": {**rule["when"], "days": [5, 6]}}
        self.assertEqual(automations.due(weekend, slot - 60, slot + 30), 0)

    def test_render_header_and_links(self):
        facts = {"text": "пост", "sender_name": "Анна", "chat_name": "Команда", "chat_kind": "group",
                 "link": automations.message_link(-1001234, 55)}
        self.assertEqual(facts["link"], "https://t.me/c/1234/55")
        self.assertEqual(automations.render("{sender}: {text} {link}", facts), "Анна: пост https://t.me/c/1234/55")
        rule = automations.normalize({"when": {"type": "message", "chats": [7]}, "do": {"action": "summary", "to": "saved"}})
        self.assertEqual(automations.header(rule, facts), "**Пересказ** · Команда · Анна\nhttps://t.me/c/1234/55")
        self.assertEqual(automations.describe({"voice", "text"}), "Голосовое сообщение")

class Images(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="memuai-tgu-img-")
        self.patch = patch.object(config, "path", return_value=Path(self.tmp.name) / "telegram_use.json")
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(config._stores.clear)

    def test_save_sniffs_type_rejects_non_images_and_dedupes(self):
        ref = tg_images.save_image(PNG_1X1, "dot.png")
        self.assertEqual((ref["ext"], ref["size"], ref["name"]), ("png", len(PNG_1X1), "dot.png"))
        self.assertEqual(tg_images.save_image(PNG_1X1, "other.png")["hash"], ref["hash"])
        with self.assertRaises(ValueError):
            tg_images.save_image(b"not an image", "x.png")
        with self.assertRaises(ValueError):
            tg_images.save_image(b"\x89PNG\r\n\x1a\n" + b"0" * (10 * 1024 * 1024), "big.png")
        self.assertEqual(tg_images.resolve(ref).name, ref["hash"] + ".png")
        self.assertIsNone(tg_images.resolve({"hash": "a" * 64, "name": "ghost.png", "size": 1, "ext": "png"}))
        self.assertIsNone(tg_images.resolve_by_name("../telegram_use.json"))

    def test_prune_removes_only_old_orphans(self):
        ref = tg_images.save_image(PNG_1X1, "dot.png")
        orphan = tg_images.save_image(b"GIF89a" + b"\x00" * 10, "o.gif")
        self.assertEqual(tg_images.prune({ref["hash"]}, grace_seconds=0), 1)
        self.assertTrue(tg_images.resolve(ref).exists())
        self.assertIsNone(tg_images.resolve(orphan))

class Settings(unittest.TestCase):
    def test_old_defaults_migrate_but_custom_values_stay(self):
        old = {"context_chars": 32000, "commands": [
            {"name": "question", "trigger": "!//question", "kind": "question", "context_messages": 30},
            {"name": "summary", "trigger": "!//summary", "kind": "summary", "context_messages": 100, "arguments": [
                {"name": "count", "type": "integer", "required": True, "min": 1, "max": 1000},
                {"name": "chat", "type": "chat", "required": False, "default": ""}]}]}
        out = config.normalize(old)
        self.assertEqual(out["context_chars"], config.CONTEXT_CHARS)
        self.assertEqual(out["commands"][0]["context_messages"], config.QUESTION_CONTEXT)
        self.assertEqual(out["commands"][1]["arguments"][0]["max"], config.MAX_CONTEXT_MESSAGES)
        kept = config.normalize({**old, "config_version": 2})
        self.assertEqual((kept["context_chars"], kept["commands"][0]["context_messages"]), (32000, 30))
        self.assertEqual(config.command({"trigger": "!//x", "context_messages": 5000})["context_messages"], 2000)

    def test_accounts_main_first_and_bounded(self):
        rows = config.accounts([{"id": "bad id"}, {"id": "abcdef12", "label": "Работа", "commands": False},
                                {"id": "abcdef12"}] + [{"id": f"acct{i:04d}"} for i in range(20)])
        self.assertEqual(rows[0]["id"], "main")
        self.assertEqual(rows[1], {"id": "abcdef12", "label": "Работа", "commands": False})
        self.assertEqual(len(rows), 10)
        self.assertEqual((config.session_key("main"), config.session_key("abcdef12")), ("session", "session.abcdef12"))

class Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="memuai-tgu-auto-")
        self.patches = [patch.object(config, "path", return_value=Path(self.tmp.name) / "telegram_use.json"),
                        patch.object(use, "ui_lang", return_value="ru")]
        for item in self.patches:
            item.start()
        config.save({"enabled": True, "api_id": 123, "model": "fake-model"})
        config.secrets().set("api_hash", "b" * 32)
        config.secrets().set("session", "fake")
        self.client = Client()
        self.runtime = use.Runtime(lambda: self.client)
        await self.runtime._connect()
        self.runtime._load_usage()
    async def asyncTearDown(self):
        await self.runtime._action("close", {})
        for item in reversed(self.patches):
            item.stop()
        config._stores.clear()
        self.tmp.cleanup()
    async def save(self, rule):
        return (await self.runtime.action("automation_save", {"rule": rule}))["automation"]
    def sent(self):
        return [(c.args[0], c.args[1], c.kwargs.get("reply_to")) for c in self.client.send_message.call_args_list]

class Runtime(Base):
    async def test_voice_from_a_person_gets_a_transcript_reply_without_the_command(self):
        rule = await self.save({"name": "Голосовые Ивана", "when": {"type": "voice", "chats": [chat()]}, "do": {"action": "voice"}})
        with patch.object(self.runtime, "media", AsyncMock(return_value={"kind": "transcript", "text": "Привет, как дела?"})) as media:
            await self.runtime._on_event(event(message(20, voice=True)))
            await self.runtime._on_event(event(message(21, "просто текст")))
            await settle(self.runtime)
        self.assertEqual(media.await_args.kwargs, {"speech": True})
        self.assertEqual(self.sent(), [(7, "Привет, как дела?", 20)])
        log = (await self.runtime.action("automations_log", {"rule": rule["id"]}))["log"]
        self.assertTrue(log[0]["ok"])
        self.assertEqual(log[0]["result"], "Привет, как дела?")
        self.assertEqual(self.runtime.status()["automation_stats"][rule["id"]]["today"], 1)

    async def test_channel_post_is_summarized_into_saved_messages(self):
        news = chat(-1001234, "AI News", "channel", "ainews")
        await self.save({"name": "Новости", "when": {"type": "post", "chats": [news]}, "do": {"action": "summary"}})
        post = message(55, "Длинный пост: вышла новая модель, цены снижены вдвое.", sender=-1001234, chat=-1001234)
        entity = types.SimpleNamespace(id=-1001234, title="AI News", username="ainews", broadcast=True)
        with patch.object(ai, "complete", AsyncMock(return_value="Вышла новая модель, цены ниже.")) as completion:
            await self.runtime._on_event(event(post, -1001234, private=False, channel=True, chat_entity=entity, sender=entity))
            await settle(self.runtime)
        self.assertFalse(completion.await_args.kwargs["allow_tools"])
        self.assertIn("Russian", completion.await_args.args[1][0]["content"])
        [(target, text, reply)] = self.sent()
        self.assertEqual((target, reply), ("me", None))
        self.assertTrue(text.startswith("Пересказ · AI News\nhttps://t.me/ainews/55"))
        self.assertIn("Вышла новая модель", text)

    async def test_own_results_never_trigger_rules_again(self):
        await self.save({"when": {"type": "message", "chats": [chat()], "include_own": True},
                         "do": {"action": "send", "text": "Я сейчас занят, отвечу позже", "to": "reply"}})
        await self.runtime._on_event(event(message(30, "Ты тут?")))
        await settle(self.runtime)
        self.assertEqual(self.sent(), [(7, "Я сейчас занят, отвечу позже", None)])
        echo = message(self.client.next_id, "Я сейчас занят, отвечу позже", sender=42, outgoing=True)
        early = message(777, "Я сейчас занят, отвечу позже", sender=42, outgoing=True)
        for item in (echo, early):
            await self.runtime._on_event(event(item, sender=person(42, "Me", "me")))
        await settle(self.runtime)
        self.assertEqual(len(self.sent()), 1)

    async def test_rate_limit_logs_once_and_notifications_go_to_inbox(self):
        rule = await self.save({"name": "Сообщения Ивана", "when": {"type": "message", "chats": [chat()]},
                                "do": {"action": "notify"}, "limits": {"per_hour": 1}})
        with patch("app.inbox.post", MagicMock(return_value="ib_1")) as post:
            for mid in (40, 41, 42):
                await self.runtime._on_event(event(message(mid, f"сообщение {mid}")))
                await settle(self.runtime)
        post.assert_called_once()
        self.assertEqual(post.call_args.args[:3], ("notice", "Сообщения Ивана · Иван", "сообщение 40"))
        log = (await self.runtime.action("automations_log", {"rule": rule["id"]}))["log"]
        self.assertEqual([e["result"] for e in log].count(automations.LIMIT_TEXT), 1)
        self.client.send_message.assert_not_awaited()

    async def test_skip_answer_command_and_disabled_writes(self):
        await self.save({"when": {"type": "message", "chats": [chat()]}, "do": {"action": "answer", "prompt": "Отвечай только на вопросы"},
                         "limits": {"cooldown": 0}})
        self.client.rows[7] = [message(49, "раньше было так")]
        with patch.object(ai, "complete", AsyncMock(side_effect=["[SKIP]", "Да, буду в 18:00"])) as completion:
            await self.runtime._on_event(event(message(50, "ок")))
            await settle(self.runtime)
            self.assertEqual(self.sent(), [])
            await self.runtime._on_event(event(message(51, "Ты придёшь вечером?")))
            await settle(self.runtime)
        system, content = (m["content"] for m in completion.await_args.args[1])
        self.assertIn("Отвечай только на вопросы", system)
        self.assertIn("раньше было так", content)
        self.assertFalse(completion.await_args.kwargs["general"])
        self.assertEqual(self.sent(), [(7, "Да, буду в 18:00", None)])
        config.save({"allow_writes": False})
        with patch("app.inbox.post", MagicMock(return_value="ib_2")) as post, \
                patch.object(ai, "complete", AsyncMock(return_value="ответ")):
            await self.runtime._on_event(event(message(52, "Ещё вопрос?")))
            await settle(self.runtime)
        self.assertEqual(len(self.sent()), 1)
        self.assertIn("не сработала", post.call_args.args[1])

    async def test_command_action_runs_a_configured_command(self):
        await self.save({"when": {"type": "mention", "scope": "groups"}, "do": {"action": "command", "command": "!//question"}})
        team = types.SimpleNamespace(id=-500, title="Команда", username="", broadcast=False)
        ask = message(60, "@me сколько стоит подписка?", sender=9, chat=-500, mentioned=True)
        with patch.object(ai, "complete", AsyncMock(return_value="299 ₽ в месяц")) as completion:
            await self.runtime._on_event(event(ask, -500, private=False, group=True, chat_entity=team, sender=person(9, "Анна", "anna")))
            await settle(self.runtime)
        self.assertIn("Answer the user's question", completion.await_args.args[1][0]["content"])
        self.assertIn("сколько стоит подписка", completion.await_args.args[1][1]["content"])
        self.assertEqual(self.sent(), [(-500, "299 ₽ в месяц", 60)])

    async def test_album_is_forwarded_once(self):
        await self.save({"when": {"type": "photo", "chats": [chat()]}, "do": {"action": "forward"}})
        for mid in (70, 71):
            await self.runtime._on_event(event(message(mid, "фото" if mid == 70 else "", photo=True, grouped=99)))
        await settle(self.runtime)
        self.client.forward_messages.assert_awaited_once()
        self.assertEqual(self.client.forward_messages.await_args.args, ("me", [70, 71]))

    async def test_schedule_digest_runs_once_and_keeps_a_cursor(self):
        slot = time.strftime("%H:%M", time.localtime(time.time() - 60))
        rule = await self.save({"name": "Сводка", "when": {"type": "schedule", "times": [slot], "chats": [chat()]},
                                "do": {"action": "digest"}})
        self.runtime._auto_state["last"][rule["id"]] = time.time() - 3600
        self.client.rows[7] = [message(3, "третье"), message(2, "второе"), message(1, "первое", age=90000)]
        with patch.object(ai, "complete", AsyncMock(return_value="Иван написал два сообщения.")) as completion:
            for _ in range(2):
                self.runtime._tick_schedules(config.load())
                await settle(self.runtime)
        completion.assert_awaited_once()
        content = completion.await_args.args[1][1]["content"]
        self.assertIn("второе", content)
        self.assertNotIn("первое", content)
        [(target, text, _)] = self.sent()
        self.assertEqual(target, "me")
        self.assertTrue(text.startswith("Сводка · Иван"))
        other = use.Runtime(lambda: self.client)
        other._load_usage()
        self.assertEqual(other._auto_state["cursor"][rule["id"]], {"7": 3})

    async def test_dry_run_uses_the_newest_matching_message_and_sends_nothing(self):
        self.client.rows[7] = [message(81, "текст"), message(80, voice=True, age=30)]
        rule = {"when": {"type": "voice", "chats": [chat()]}, "do": {"action": "voice"}}
        with patch.object(self.runtime, "media", AsyncMock(return_value={"text": "расшифровка"})):
            result = await self.runtime.action("automation_test", {"rule": rule})
        self.assertTrue(result["found"])
        self.assertEqual((result["result"], result["message"]["text"]), ("расшифровка", "Голосовое сообщение"))
        self.client.send_message.assert_not_awaited()
        self.client.rows[7] = [message(82, "только текст")]
        self.assertFalse((await self.runtime.action("automation_test", {"rule": rule}))["found"])

    async def send_rule(self, text, images, name="С картинками"):
        rule = await self.save({"name": name, "when": {"type": "keyword", "scope": "chats", "chats": [chat()], "keywords": ["asd"]},
                                "do": {"action": "send", "text": text, "images": images}})
        await self.runtime._on_event(event(message(20, "asd")))
        await settle(self.runtime)
        return rule

    async def test_send_rule_with_images_sends_album_with_caption(self):
        ref = tg_images.save_image(PNG_1X1, "dot.png")
        rule = await self.send_rule("Привет!", [ref])
        self.assertEqual(len(self.client.send_file.call_args_list), 1)
        call = self.client.send_file.call_args_list[0]
        self.assertEqual(call.kwargs["caption"], "Привет!")
        # A send in a private chat does not quote the trigger message (_target).
        self.assertIsNone(call.kwargs["reply_to"])
        self.assertTrue(str(call.args[1][0]).endswith(f"{ref['hash']}.png"))
        self.client.send_message.assert_not_awaited()
        log = (await self.runtime.action("automations_log", {"rule": rule["id"]}))["log"]
        self.assertIn("Отправлено изображений: 1", log[0]["result"])

    async def test_send_rule_with_images_and_long_text_sends_album_then_text(self):
        ref = tg_images.save_image(PNG_1X1, "dot.png")
        await self.send_rule("x" * 1100, [ref])
        call = self.client.send_file.call_args_list[0]
        self.assertIsNone(call.kwargs["caption"])
        self.assertEqual(self.client.send_message.call_args_list[0].args[1], "x" * 1100)

    async def test_missing_image_file_falls_back_to_text(self):
        ghost = {"hash": "a" * 64, "name": "ghost.png", "size": 10, "ext": "png"}
        rule = await self.send_rule("Только текст", [ghost])
        self.client.send_file.assert_not_awaited()
        self.assertEqual(self.sent(), [(7, "Только текст", None)])
        log = (await self.runtime.action("automations_log", {"rule": rule["id"]}))["log"]
        self.assertIn("Не отправлено изображений: 1", log[0]["result"])

    async def test_images_only_send_without_text(self):
        ref = tg_images.save_image(PNG_1X1, "dot.png")
        await self.send_rule("", [ref])
        self.assertIsNone(self.client.send_file.call_args_list[0].kwargs["caption"])
        self.client.send_message.assert_not_awaited()

    async def test_dry_run_reports_images_without_sending(self):
        ref = tg_images.save_image(PNG_1X1, "dot.png")
        self.client.rows[7] = [message(81, "asd")]
        rule = {"when": {"type": "keyword", "scope": "chats", "chats": [chat()], "keywords": ["asd"]},
                "do": {"action": "send", "text": "Привет!", "images": [ref]}}
        result = await self.runtime.action("automation_test", {"rule": rule})
        self.assertTrue(result["found"])
        self.assertEqual(result["images"], ["dot.png"])
        self.assertEqual(result["images_missing"], 0)
        self.assertIn("Отправлено изображений: 1", result["result"])
        self.client.send_file.assert_not_awaited()
        self.client.send_message.assert_not_awaited()

    async def test_dialogs_report_kinds_with_saved_first(self):
        self.client.dialogs = [dialog(7, "Иван", username="ivan"), dialog(-1001, "News", kind="channel", username="news"),
                               dialog(-1002, "Team", kind="group"), dialog(55, "Helper", kind="bot", username="helper_bot"),
                               dialog(42, "Me", kind="saved")]
        rows = (await self.runtime.action("dialogs", {}))["chats"]
        self.assertEqual([(r["id"], r["kind"]) for r in rows],
                         [(42, "saved"), (7, "user"), (-1001, "channel"), (-1002, "group"), (55, "bot")])
        self.assertEqual([r["id"] for r in (await self.runtime.action("dialogs", {"query": "ива"}))["chats"]], [7])

class Accounts(Base):
    async def test_second_account_signs_in_routes_tools_and_leaves(self):
        clients = {"main": self.client}
        def factory(account="main"):
            return clients.setdefault(account, Client(77, "work", "Work"))
        self.runtime.client_factory = factory
        pending = (await self.runtime.action("add_account", {}))["pending_account"]
        clients[pending] = second = Client(77, "work", "Work")
        second.authorized = False
        status = await self.runtime.action("phone", {"account": pending, "phone": "+7 999 000-00-00"})
        self.assertEqual(status["accounts"][1]["flow"]["phase"], "code")
        self.assertEqual(status["flow"]["phase"], "connected")
        status = await self.runtime.action("code", {"account": pending, "code": "12345"})
        second.sign_in.assert_awaited_once()
        row = status["accounts"][1]
        self.assertEqual((row["id"], row["connected"], row["account"]["username"]), (pending, True, "work"))
        config.secrets().set(config.session_key(pending), "fake-2")
        self.assertEqual((await self.runtime.tool("telegram_use_status", {"account": "@work"}))["account"]["username"], "work")
        await self.runtime.tool("telegram_use_send", {"chat": "7", "text": "hi", "account": "Work"})
        second.send_message.assert_awaited_once()
        self.client.send_message.assert_not_awaited()
        with self.assertRaisesRegex(use.TelegramUseError, "не найден"):
            await self.runtime.tool("telegram_use_send", {"chat": "7", "text": "hi", "account": "nobody"})
        await self.runtime.action("account_update", {"account": pending, "commands": False, "label": "Работа"})
        child = self.runtime.children[pending]
        self.assertFalse(child._commands_on())
        self.assertEqual(child.label(), "Работа")
        third = (await self.runtime.action("add_account", {}))["pending_account"]
        clients[third] = Client(42, "me", "Me")
        clients[third].authorized = False
        await self.runtime.action("phone", {"account": third, "phone": "+7 999 000-00-01"})
        status = await self.runtime.action("code", {"account": third, "code": "11111"})
        duplicate = next(a for a in status["accounts"] if a["id"] == third)
        self.assertEqual(duplicate["flow"]["phase"], "duplicate")
        await self.runtime.action("cancel", {"account": third})
        status = await self.runtime.action("disconnect", {"account": pending})
        self.assertEqual([a["id"] for a in status["accounts"]], ["main"])
        self.assertEqual([a["id"] for a in config.load()["accounts"]], ["main"])
        self.assertTrue(status["config"]["enabled"])

    async def test_automation_runs_on_its_own_account(self):
        clients = {"main": self.client}
        self.runtime.client_factory = lambda account="main": clients.setdefault(account, Client(77, "work", "Work"))
        pending = (await self.runtime.action("add_account", {}))["pending_account"]
        child = self.runtime.children[pending]
        await child._connect()
        rule = await self.save({"account": pending, "when": {"type": "message", "chats": [chat()]},
                                "do": {"action": "send", "text": "Принято: {text}"}})
        await self.runtime._on_event(event(message(90, "заказ")))
        await settle(self.runtime)
        self.client.send_message.assert_not_awaited()
        await child._on_event(event(message(91, "заказ")))
        await settle(self.runtime)
        self.assertEqual(clients[pending].send_message.await_args.args[:2], (7, "Принято: заказ"))
        await self.runtime.action("disconnect", {"account": pending})
        orphan = next(r for r in config.load()["automations"] if r["id"] == rule["id"])
        self.assertEqual((orphan["account"], orphan["enabled"]), (pending, False))
        with self.assertRaisesRegex(use.TelegramUseError, "account"):
            await self.runtime.action("automation_toggle", {"id": rule["id"], "enabled": True})
        await self.runtime._on_event(event(message(92, "ещё один заказ")))
        await settle(self.runtime)
        self.client.send_message.assert_not_awaited()

class Browser(unittest.TestCase):
    def test_orphaned_rule_requires_an_account_and_new_chat_choices(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("Playwright is unavailable")
        script = Path(__file__).resolve().parents[1] / "app" / "pgstatic" / "plan-telegram.js"
        page_html = """<!doctype html><main></main><script>
            const PG_LANG = "en";
            function panel(html) { document.querySelector("main").innerHTML = html; }
            </script><script src="/plan-telegram.js"></script>"""
        rule = automations.normalize({"id": "rule001", "account": "orphan42", "enabled": False,
                                      "when": {"type": "message", "chats": [chat(9, "Old source")],
                                               "senders": [chat(10, "Old sender")]},
                                      "do": {"action": "send", "text": "hello", "to": "chat",
                                             "to_chat": chat(11, "Old destination")}})
        status = {"configured": True, "accounts": [{"id": "main", "main": True, "connected": True, "has_session": True}],
                  "config": {"automations": [rule], "enabled": True, "allow_writes": True}}

        def route_page(route):
            url = route.request.url
            if url.endswith("/plan-telegram.js"):
                route.fulfill(path=str(script), content_type="text/javascript")
            elif url.endswith("/api/pg/integrations/telegram-use"):
                route.fulfill(json=status)
            elif url == "http://automation.test/":
                route.fulfill(body=page_html, content_type="text/html")
            else:
                route.abort()

        with sync_playwright() as playwright:
            executable = os.environ.get("MEMU_TEST_CHROMIUM") or playwright.chromium.executable_path
            if not Path(executable).is_file():
                self.skipTest("Headless Chromium is unavailable")
            browser = playwright.chromium.launch(executable_path=executable, headless=True)
            try:
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.route("**/*", route_page)
                page.goto("http://automation.test/")
                page.evaluate("(id) => window.MemuPlanTelegram.openBuilder({id})", rule["id"])
                account = page.locator('[data-f="account"]')
                summary = page.locator("#plt-pane .plw-sentence")
                save = page.locator('[data-plt="save"]')
                self.assertEqual(account.input_value(), rule["account"])
                self.assertIn("Old destination", summary.inner_text())
                self.assertTrue(save.is_disabled())
                account.select_option("main")
                for name in ("Old source", "Old sender", "Old destination"):
                    self.assertNotIn(name, summary.inner_text())
                self.assertTrue(save.is_disabled())
                self.assertEqual(errors, [])
            finally:
                browser.close()

class Api(unittest.TestCase):
    def test_new_actions_dialogs_route_and_catalog_titles(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import connected_services, schedule_integrations
        from app import telegram_use_api as api
        app = FastAPI()
        api.register(app)
        calls = []
        fake = types.SimpleNamespace(submit=lambda *a: calls.append(a) or {"chats": []})
        with tempfile.TemporaryDirectory() as folder, patch.object(config, "path", return_value=Path(folder) / "telegram_use.json"), \
                patch.object(use, "start", return_value=fake):
            client = TestClient(app, base_url="http://127.0.0.1")
            self.assertEqual(client.post("/api/pg/integrations/telegram-use", json={"action": "automation_test", "rule": {}}).status_code, 200)
            self.assertEqual(calls[-1][0], "automation_test")
            self.assertEqual(calls[-1][2], 440)
            self.assertEqual(client.post("/api/pg/integrations/telegram-use", json={"action": "evil"}).status_code, 400)
            self.assertEqual(client.get("/api/pg/integrations/telegram-use/dialogs?account=main&query=ив&fresh=1").status_code, 200)
            self.assertEqual(calls[-1][:2], ("dialogs", {"account": "main", "query": "ив", "limit": 100, "fresh": True}))
            config._stores.clear()
        rows = [{"service": "telegram_use", "connected": True, "tools": use.TOOLS}]
        with patch.object(connected_services, "status_rows", return_value=rows), patch.object(schedule_integrations, "_accounts", return_value=[]):
            catalog = {row["name"]: row for row in schedule_integrations.catalog()}
        send = catalog["telegram_use_send"]
        self.assertEqual((send["title"]["ru"], send["group"]), ("Отправить сообщение", "Telegram"))
        self.assertEqual(send["parameters"]["properties"]["chat"]["title"]["en"], "Chat or contact")
        self.assertEqual(catalog["telegram_use_status"]["title"]["en"], "Check the connection")

    def test_automation_image_upload_and_get(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import telegram_use_api as api
        app = FastAPI()
        api.register(app)
        payload = "data:image/png;base64," + base64.b64encode(PNG_1X1).decode()
        with tempfile.TemporaryDirectory() as folder, patch.object(config, "path", return_value=Path(folder) / "telegram_use.json"):
            client = TestClient(app, base_url="http://127.0.0.1")
            res = client.post("/api/pg/integrations/telegram-use/automation-image", json={"name": "dot.png", "data": payload})
            self.assertEqual(res.status_code, 200, res.text)
            ref = res.json()["image"]
            self.assertEqual((ref["ext"], ref["size"], ref["name"]), ("png", len(PNG_1X1), "dot.png"))
            got = client.get(f"/api/pg/integrations/telegram-use/automation-image/{ref['hash']}.png")
            self.assertEqual(got.status_code, 200)
            self.assertEqual(got.content, PNG_1X1)
            self.assertEqual(got.headers["content-type"], "image/png")
            bad = client.post("/api/pg/integrations/telegram-use/automation-image",
                              json={"name": "x.txt", "data": "data:text/plain;base64," + base64.b64encode(b"hello").decode()})
            self.assertEqual(bad.status_code, 400)
            self.assertEqual(client.get("/api/pg/integrations/telegram-use/automation-image/" + "f" * 64 + ".png").status_code, 404)
            self.assertEqual(client.get("/api/pg/integrations/telegram-use/automation-image/" + "f" * 64 + ".exe").status_code, 404)
            config._stores.clear()

if __name__ == "__main__":
    unittest.main()
