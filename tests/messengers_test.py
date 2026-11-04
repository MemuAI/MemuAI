"""Discord and WhatsApp bots: settings, pairing, the shared engine, rendering and the signed webhook."""
from __future__ import annotations

import os
import tempfile

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

import asyncio  # noqa: E402
import hashlib  # noqa: E402
import hmac  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import time  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Dict, List, Optional  # noqa: E402
from unittest.mock import patch  # noqa: E402

from app import discord_bot, messenger_core as core, messenger_prefs, pgdata, whatsapp_bot  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "pgstatic"


def reset_state() -> None:
    path = pgdata.MEMU_DIR / "messengers_state.json"
    if path.exists():
        path.unlink()


class FakeAdapter(core.Adapter):
    platform = "discord"
    limit = 200
    can_edit = True

    def __init__(self) -> None:
        self.sent: List[core.Card] = []
        self.edits: List[core.Card] = []
        self.typed = 0

    async def send(self, peer: str, card: core.Card) -> Optional[str]:
        self.sent.append(card)
        return f"m{len(self.sent)}"

    async def edit(self, peer: str, message_id: str, card: core.Card) -> bool:
        self.edits.append(card)
        return True

    async def typing(self, peer: str) -> None:
        self.typed += 1


class FakeJob:
    def __init__(self, events: List[Dict[str, Any]]) -> None:
        self.events = events
        self.done = False

    async def wait(self, offset: int, timeout: float) -> List[Dict[str, Any]]:
        await asyncio.sleep(0)
        chunk = self.events[offset:offset + 1]
        if offset + len(chunk) >= len(self.events):
            self.done = True
        return chunk


class PrefsTest(unittest.TestCase):
    def test_defaults_and_normalization(self) -> None:
        defaults = pgdata.DEFAULT_PREFERENCES
        for key in messenger_prefs.PREF_DEFAULTS:
            self.assertIn(key, defaults)
        out = messenger_prefs.normalize_prefs({
            "discord_enabled": "true", "discord_allowed_users": ["123456789012345678", "x", "12", "123456789012345678"],
            "whatsapp_mode": "evil", "whatsapp_waha_url": "javascript:alert(1)", "whatsapp_allowed_users": "+7 (999) 123-45-67, 1",
            "whatsapp_waha_session": "../etc", "whatsapp_phone_id": "12a34", "discord_language": "de",
            "whatsapp_verify_token": "abc def<>", "discord_bot_token": " tok\x00en "})
        self.assertTrue(out["discord_enabled"])
        self.assertEqual(out["discord_allowed_users"], ["123456789012345678"])
        self.assertEqual(out["whatsapp_mode"], "cloud")
        self.assertEqual(out["whatsapp_waha_url"], "http://127.0.0.1:3000")
        self.assertEqual(out["whatsapp_allowed_users"], ["79991234567"])
        self.assertEqual(out["whatsapp_waha_session"], "default")
        self.assertEqual(out["whatsapp_phone_id"], "1234")
        self.assertEqual(out["discord_language"], "ru")
        self.assertEqual(out["whatsapp_verify_token"], "abcdef")
        self.assertEqual(out["discord_bot_token"], "token")
        self.assertEqual(messenger_prefs.normalize_prefs({"whatsapp_waha_url": "http://10.0.0.5:3000/"})["whatsapp_waha_url"],
                         "http://10.0.0.5:3000")

    def test_secret_keys_are_kept_out_of_sync(self) -> None:
        from app import gdrive_sync
        for key in ("discord_bot_token", "whatsapp_access_token", "whatsapp_app_secret", "whatsapp_verify_token", "whatsapp_waha_api_key"):
            self.assertTrue(gdrive_sync._secret_key(key), key)


class PairingTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_state()

    def test_code_attempts_and_owner(self) -> None:
        self.assertFalse(core.is_allowed("discord", "42", {}))
        code = core.new_pairing_code("discord")["code"]
        self.assertRegex(code, r"^[A-Z2-9]{6}$")
        self.assertEqual(core.try_pair("discord", "42", "Ann", "ZZZZZZ"), (False, "wrong"))
        self.assertEqual(core.try_pair("discord", "42", "Ann", code.lower()), (True, "ok"))
        self.assertTrue(core.is_allowed("discord", "42", {}))
        self.assertFalse(core.is_allowed("discord", "43", {}))
        self.assertTrue(core.is_allowed("discord", "43", {"discord_allowed_users": ["43"]}))
        self.assertEqual(core.try_pair("discord", "42", "Ann", code), (False, "expired"))
        self.assertIsNone(core.public_status("discord")["pairing"])
        core.unpair("discord")
        self.assertFalse(core.is_allowed("discord", "42", {}))
        self.assertFalse(core.is_allowed("discord", "43", {"discord_allowed_users": ["43"]}))

    def test_lock_after_attempts(self) -> None:
        code = core.new_pairing_code("whatsapp")["code"]
        for _ in range(core.PAIR_ATTEMPTS - 1):
            self.assertEqual(core.try_pair("whatsapp", "7999", "", "AAAAAA")[1], "wrong")
        self.assertEqual(core.try_pair("whatsapp", "7999", "", "AAAAAA")[1], "locked")
        self.assertEqual(core.try_pair("whatsapp", "7999", "", code)[1], "locked")
        self.assertTrue(core.try_pair("whatsapp", "8000", "", code)[0])

    def test_expired_code(self) -> None:
        code = core.new_pairing_code("discord")["code"]

        def age(data):
            data["discord"]["pairing"]["expires"] = time.time() - 1
        core.update_state(age)
        self.assertEqual(core.try_pair("discord", "42", "", code), (False, "expired"))

    def test_status_codes(self) -> None:
        core.set_status("whatsapp", "reconnecting", "boom", code="waha_unreachable")
        self.assertEqual(core.public_status("whatsapp")["code"], "waha_unreachable")
        core.set_status("whatsapp", "online", "")
        self.assertEqual(core.public_status("whatsapp")["code"], "")
        self.assertTrue(core.public_status("whatsapp")["alive"])


class TextTest(unittest.TestCase):
    def test_split_keeps_fences(self) -> None:
        text = "intro\n```python\n" + "\n".join(f"line {i}" for i in range(60)) + "\n```\nend"
        parts = core.split_text(text, 200)
        self.assertGreater(len(parts), 1)
        for part in parts:
            self.assertLessEqual(len(part), 200)
            self.assertEqual(part.count("```") % 2, 0, part)

    def test_whatsapp_formatting(self) -> None:
        out = core.to_whatsapp("# Title\n**bold** and ~~gone~~ [site](https://example.com)")
        self.assertIn("*Title*", out)
        self.assertIn("*bold*", out)
        self.assertNotIn("**", out)
        self.assertIn("~gone~", out)
        self.assertIn("https://example.com", out)

    def test_model_traits(self) -> None:
        row = {"badges": ["free"], "vision_state": "yes", "reasoning_levels": ["auto", "off", "high"]}
        self.assertEqual(core.model_traits(row, "en"), "free · vision · reasoning")
        self.assertEqual(core.model_traits({"reasoning_levels": ["auto", "off"]}, "ru"), "")


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class EngineTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_state()
        self.adapter = FakeAdapter()
        self.engine = core.Engine(self.adapter)

    def pair(self) -> None:
        code = core.new_pairing_code("discord")["code"]
        run(self.engine.on_text("42", "Ann", "c1", f"/pair {code}"))

    def test_private_until_paired(self) -> None:
        run(self.engine.on_text("42", "Ann", "c1", "hello"))
        self.assertIn("/pair", self.adapter.sent[-1].text)
        self.pair()
        self.assertTrue(core.is_allowed("discord", "42", {}))
        run(self.engine.on_text("99", "Eve", "c2", "/menu"))
        self.assertIn("приват", self.adapter.sent[-1].text.lower() + " private")
        run(self.engine.on_action("99", "c2", "new"))
        self.assertEqual(len([c for c in self.adapter.sent if "Новый чат" in c.text or "New chat" in c.text]), 0)

    def test_bare_code_pairs_only_before_an_owner(self) -> None:
        code = core.new_pairing_code("discord")["code"]
        run(self.engine.on_text("42", "Ann", "c1", code))
        self.assertTrue(core.is_allowed("discord", "42", {}))

    def test_menu_settings_and_numbers(self) -> None:
        self.pair()
        rows = [{"id": f"m{i}", "name": f"Model {i}", "badges": [], "reasoning_levels": []} for i in range(14)]
        with patch("app.tgapp_chat.chat_models", return_value=rows), \
                patch("app.tgapp_chat.pick_model", side_effect=lambda model, prefs: model or "m0"):
            run(self.engine.on_text("42", "Ann", "c1", "/model"))
            card = self.adapter.sent[-1]
            self.assertEqual(len(card.choices), core.LIST_PAGE)
            pending = core.user_state("discord", "42")["pending"]
            self.assertEqual(pending["3"], "pick:m2")
            self.assertTrue(any(value.startswith("model:") for value in pending.values()))
            run(self.engine.on_text("42", "Ann", "c1", "3"))
            self.assertEqual(core.user_state("discord", "42")["model"], "m2")
        run(self.engine.on_text("42", "Ann", "c1", "/settings"))
        ids = [b.id for line in self.adapter.sent[-1].buttons for b in line]
        self.assertIn("web", ids)
        run(self.engine.on_action("42", "c1", "web"))
        self.assertIsNotNone(core.user_state("discord", "42")["web"])
        run(self.engine.on_text("42", "Ann", "c1", "/меню"))
        self.assertTrue(self.adapter.sent[-1].buttons)

    def test_chat_streams_and_saves_the_chat(self) -> None:
        self.pair()
        events = [{"type": "start", "chat_id": "dsc-1"}] + [{"type": "delta", "text": "word "} for _ in range(5)] + \
            [{"type": "message", "message": {"content": "word word word word word"}}, {"type": "done", "chat_id": "dsc-1"}]
        captured: Dict[str, Any] = {}

        def start_send(**kwargs):
            captured.update(kwargs)
            return FakeJob(events)
        with patch("app.tgapp_chat.start_send", side_effect=start_send), \
                patch("app.tgapp_chat.profiles", return_value=("default", [])), \
                patch.object(core, "STREAM_EDIT_SECONDS", 0.0):
            run(self.engine.on_text("42", "Ann", "c1", "Hi there"))
        self.assertEqual(captured["owner"], "discord:42")
        self.assertEqual(captured["via"], "discord")
        self.assertEqual(captured["text"], "Hi there")
        final = (self.adapter.edits or self.adapter.sent)[-1]
        self.assertIn("word word", final.text)
        self.assertIn("regen", [b.id for line in final.buttons for b in line])
        self.assertEqual(core.user_state("discord", "42")["chat"], "dsc-1")

    def test_temp_chat_keeps_history_locally(self) -> None:
        self.pair()
        run(self.engine.on_text("42", "Ann", "c1", "/temp"))
        self.assertTrue(core.user_state("discord", "42")["temp"])
        events = [{"type": "delta", "text": "ok"}, {"type": "done"}]
        with patch("app.tgapp_chat.start_send", return_value=FakeJob(events)) as start, \
                patch("app.tgapp_chat.profiles", return_value=("default", [])):
            run(self.engine.on_text("42", "Ann", "c1", "secret"))
        self.assertTrue(start.call_args.kwargs["temp"])
        self.assertEqual(start.call_args.kwargs["chat_id"], "")
        history = core.user_state("discord", "42")["temp_history"]
        self.assertEqual([row["role"] for row in history], ["user", "assistant"])


class DiscordTest(unittest.TestCase):
    def test_token_shape_and_redaction(self) -> None:
        token = ".".join(("a" * 20, "b" * 4, "c" * 20))
        self.assertTrue(discord_bot.valid_token(token))
        self.assertFalse(discord_bot.valid_token("123:abc"))
        self.assertNotIn(token, discord_bot.redact(f"failed with {token}"))

    def test_slash_commands(self) -> None:
        commands = discord_bot.slash_commands("en")
        names = [c["name"] for c in commands]
        for name in ("menu", "new", "chats", "model", "settings", "temp", "regen", "stop", "status", "help", "pair"):
            self.assertIn(name, names)
        for command in commands:
            self.assertRegex(command["name"], r"^[a-z]{1,32}$")
            self.assertLessEqual(len(command["description"]), 100)
            self.assertIn(1, command["contexts"])
        self.assertEqual(discord_bot.slash_commands("ru", user_install=False)[0]["integration_types"], [0])

    def test_render_limits(self) -> None:
        card = core.Card("x" * 2500, [[core.Button(f"b{i}", f"Button {i}") for i in range(7)] for _ in range(7)],
                         [core.Choice(f"pick:{i}", f"Choice {i}", "d") for i in range(30)], "pick", "Pick")
        body = discord_bot.render(card)
        self.assertLessEqual(len(body["content"]), 2000)
        self.assertLessEqual(len(body["components"]), 5)
        self.assertLessEqual(len(body["components"][0]["components"][0]["options"]), 25)
        for row in body["components"][1:]:
            self.assertLessEqual(len(row["components"]), 5)
        self.assertEqual(body["allowed_mentions"], {"parse": []})

    def test_invite_links(self) -> None:
        links = discord_bot.invite_links("123456789012")
        self.assertIn("integration_type=1", links["user"])
        self.assertIn("integration_type=0", links["server"])
        self.assertEqual(discord_bot.invite_links("abc"), {})


class DiscordGatewayTest(unittest.TestCase):
    """A local fake of the Discord gateway and REST API: identify, a DM, a server message, a slash command."""

    def test_overlapping_slash_commands_keep_their_own_deferred_replies(self):
        import httpx

        calls = []

        def handler(request):
            body = json.loads(request.content) if request.content else {}
            calls.append((request.method, request.url.path, body))
            return httpx.Response(200, json={"id": f"msg{len(calls)}"})

        async def scenario():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                rest = discord_bot.Rest(client, "token")
                adapter = discord_bot.DiscordAdapter(rest)
                adapter.app_id = "222"
                bot = discord_bot.DiscordBot("token")
                first_started = asyncio.Event()
                second_started = asyncio.Event()
                first_sent = asyncio.Event()

                class Engine:
                    async def on_text(self, user_id, name, peer, text):
                        if text == "/menu":
                            first_started.set()
                            await second_started.wait()
                            await adapter.send(peer, core.Card("menu"))
                            first_sent.set()
                        else:
                            second_started.set()
                            await first_sent.wait()
                            await adapter.send(peer, core.Card("status"))

                engine = Engine()
                data = {"type": 2, "channel_id": "c1", "user": {"id": "42"}}
                first = asyncio.create_task(bot._interaction(engine, adapter, rest, {
                    **data, "id": "i1", "token": "first", "data": {"name": "menu"}}))
                await first_started.wait()
                await bot._interaction(engine, adapter, rest, {
                    **data, "id": "i2", "token": "second", "data": {"name": "status"}})
                await first

        run(asyncio.wait_for(scenario(), 5))
        replies = {path: body["content"] for method, path, body in calls if method == "PATCH"}
        self.assertEqual(replies, {"/api/v10/webhooks/222/first/messages/@original": "menu",
                                   "/api/v10/webhooks/222/second/messages/@original": "status"})

    def test_dm_pairing_and_slash_command(self) -> None:
        import httpx
        import websockets
        reset_state()
        code = core.new_pairing_code("discord")["code"]
        calls: List[Any] = []

        def handler(request):
            body = json.loads(request.content) if request.content else None
            calls.append((request.method, request.url.path, body))
            path = request.url.path
            if path.endswith("/users/@me"):
                return httpx.Response(200, json={"id": "111", "username": "memubot"})
            if path.endswith("/oauth2/applications/@me"):
                return httpx.Response(200, json={"id": "222"})
            if path.endswith("/commands"):
                return httpx.Response(200, json=[])
            if path.endswith("/typing"):
                return httpx.Response(204)
            return httpx.Response(200, json={"id": f"msg{len(calls)}"})

        async def scenario():
            finished = asyncio.Event()

            async def gateway(ws):
                await ws.send(json.dumps({"op": 10, "d": {"heartbeat_interval": 45000}}))
                identify = json.loads(await ws.recv())
                self.assertEqual(identify["op"], 2)
                self.assertEqual(identify["d"]["intents"], discord_bot.INTENTS)
                await ws.send(json.dumps({"op": 0, "s": 1, "t": "READY", "d": {"session_id": "s1"}}))
                await ws.send(json.dumps({"op": 0, "s": 2, "t": "MESSAGE_CREATE", "d": {
                    "author": {"id": "42", "username": "ann"}, "channel_id": "c1", "content": f"/pair {code}"}}))
                await ws.send(json.dumps({"op": 0, "s": 3, "t": "MESSAGE_CREATE", "d": {
                    "guild_id": "g1", "author": {"id": "42"}, "channel_id": "c9", "content": "/menu"}}))
                await asyncio.sleep(0.4)
                await ws.send(json.dumps({"op": 0, "s": 4, "t": "INTERACTION_CREATE", "d": {
                    "id": "i1", "token": "itok", "type": 2, "channel_id": "c1", "user": {"id": "42", "username": "ann"},
                    "data": {"name": "status"}}}))
                await asyncio.wait_for(finished.wait(), 10)
                await ws.close()

            async with websockets.serve(gateway, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                with patch.object(discord_bot, "GATEWAY", f"ws://127.0.0.1:{port}"):
                    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                        task = asyncio.create_task(discord_bot.DiscordBot("token").run(client))
                        for _ in range(200):
                            await asyncio.sleep(0.05)
                            if any(path.endswith("/webhooks/222/itok/messages/@original") for _, path, _ in calls):
                                break
                        finished.set()
                        return await asyncio.wait_for(task, 10)

        reason = run(scenario())
        self.assertEqual(reason, "reconnect")
        paths = [(method, path) for method, path, _ in calls]
        self.assertIn(("PUT", "/api/v10/applications/222/commands"), paths)
        self.assertTrue(core.is_allowed("discord", "42", {}))
        self.assertIn(("POST", "/api/v10/channels/c1/messages"), paths)
        self.assertNotIn(("POST", "/api/v10/channels/c9/messages"), paths)
        callback = next(body for method, path, body in calls if path == "/api/v10/interactions/i1/itok/callback")
        self.assertEqual(callback, {"type": 5})
        self.assertIn(("PATCH", "/api/v10/webhooks/222/itok/messages/@original"), paths)
        self.assertEqual(core.public_status("discord")["bot"]["name"], "memubot")


class WhatsAppTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_state()

    def test_render_cloud(self) -> None:
        card = core.Card("**Models**", [[core.Button("model:1", "More"), core.Button("menu", "Menu")]],
                         [core.Choice(f"pick:m{i}", f"Model number {i} with a long name", "free") for i in range(12)], "pick", "Pick a model", "Models")
        messages = whatsapp_bot.render_cloud("7999", card)
        listing = messages[0]["interactive"]
        self.assertEqual(listing["type"], "list")
        rows = listing["action"]["sections"][0]["rows"]
        self.assertEqual(len(rows), 10)
        self.assertTrue(all(len(row["title"]) <= 24 for row in rows))
        self.assertLessEqual(len(listing["action"]["button"]), 20)
        self.assertEqual(messages[1]["interactive"]["type"], "button")
        plain = whatsapp_bot.render_cloud("7999", core.Card("hello", [[core.Button("a", "A"), core.Button("b", "B"), core.Button("c", "C"), core.Button("d", "D")]]))
        self.assertLessEqual(len(plain[0]["interactive"]["action"]["buttons"]), 3)

    def test_render_plain_numbers_paging(self) -> None:
        card = core.Card("Pick", [[core.Button("model:1", "More"), core.Button("menu", "Menu")]],
                         [core.Choice("pick:a", "A"), core.Choice("pick:b", "B", "free")], "pick")
        text = whatsapp_bot.render_plain(card, "en")
        self.assertIn("*1.* A", text)
        self.assertIn("*2.* B — free", text)
        self.assertIn("*3.* More", text)
        self.assertIn("/menu", text)

    def test_parse_webhook(self) -> None:
        payload = {"entry": [{"changes": [{"value": {
            "contacts": [{"wa_id": "7999", "profile": {"name": "Ann"}}],
            "messages": [{"id": "w1", "from": "7999", "type": "text", "text": {"body": "hi"}},
                         {"id": "w2", "from": "7999", "type": "interactive", "interactive": {"type": "list_reply", "list_reply": {"id": "pick:m1"}}},
                         {"id": "w3", "from": "7999", "type": "interactive", "interactive": {"type": "button_reply", "button_reply": {"id": "menu"}}}]}}]}]}
        rows = whatsapp_bot.parse_webhook(payload)
        self.assertEqual([r["id"] for r in rows], ["w1", "w2", "w3"])
        self.assertEqual(rows[0]["text"], "hi")
        self.assertEqual(rows[1]["action"], "pick:m1")
        self.assertEqual(rows[2]["action"], "menu")
        self.assertEqual(rows[0]["name"], "Ann")
        self.assertEqual(whatsapp_bot.parse_webhook({"entry": "junk"}), [])

    def test_waha_chat_ids_and_echo(self) -> None:
        self.assertEqual(whatsapp_bot.waha_chat_id("+7 (999) 123-45-67"), "79991234567@c.us")
        self.assertEqual(whatsapp_bot.waha_chat_id("79991234567@c.us"), "79991234567@c.us")
        self.assertEqual(whatsapp_bot.waha_chat_id("12"), "")
        adapter = whatsapp_bot.WahaAdapter(None, "http://127.0.0.1:3000", "", "default")
        adapter.sent.append("BAE5000001")
        adapter.sent_texts.append(whatsapp_bot._norm_text("Answer  text"))
        self.assertTrue(adapter.is_echo({"id": "true_79991234567@c.us_BAE5000001", "fromMe": True}, True))
        self.assertTrue(adapter.is_echo({"id": "x", "fromMe": True, "body": "other"}, False))
        self.assertTrue(adapter.is_echo({"id": "y", "fromMe": True, "body": "Answer text"}, True))
        self.assertFalse(adapter.is_echo({"id": "z", "fromMe": True, "body": "my question"}, True))
        self.assertFalse(adapter.is_echo({"id": "u", "fromMe": False, "body": "hi"}, False))

    def test_signature(self) -> None:
        body = b'{"a":1}'
        good = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
        self.assertTrue(core.verify_signature("secret", body, good))
        self.assertFalse(core.verify_signature("secret", body, "sha256=00"))
        self.assertFalse(core.verify_signature("", body, good))
        self.assertFalse(core.verify_signature("secret", body, ""))


class WebhookRoutesTest(unittest.TestCase):
    def setUp(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        reset_state()
        app = FastAPI()
        whatsapp_bot.register(app)
        self.client = TestClient(app)
        self.prefs = dict(pgdata.load_preferences(), whatsapp_enabled=True, whatsapp_mode="cloud", whatsapp_access_token="EAAtoken",
                          whatsapp_phone_id="123", whatsapp_app_secret="topsecret", whatsapp_verify_token="v" * 24)

    def test_verify_handshake(self) -> None:
        with patch("app.pgdata.load_preferences", return_value=self.prefs):
            ok = self.client.get("/messengers/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "v" * 24, "hub.challenge": "42"})
            bad = self.client.get("/messengers/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "42"})
        self.assertEqual((ok.status_code, ok.text), (200, "42"))
        self.assertEqual(bad.status_code, 403)
        with patch("app.pgdata.load_preferences", return_value=dict(self.prefs, whatsapp_verify_token="short")):
            weak = self.client.get("/messengers/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "short", "hub.challenge": "1"})
        self.assertEqual(weak.status_code, 403)

    def test_signed_messages_only(self) -> None:
        payload = json.dumps({"entry": [{"changes": [{"value": {"messages": [{"id": "w9", "from": "7999", "type": "text", "text": {"body": "hi"}}]}}]}]}).encode()
        handled: List[Any] = []
        with patch("app.pgdata.load_preferences", return_value=self.prefs), \
                patch.object(whatsapp_bot.cloud, "handle", side_effect=lambda data, prefs: handled.append(data) or 1):
            unsigned = self.client.post("/messengers/whatsapp/webhook", content=payload)
            forged = self.client.post("/messengers/whatsapp/webhook", content=payload, headers={"X-Hub-Signature-256": "sha256=" + "0" * 64})
            signature = "sha256=" + hmac.new(b"topsecret", payload, hashlib.sha256).hexdigest()
            signed = self.client.post("/messengers/whatsapp/webhook", content=payload, headers={"X-Hub-Signature-256": signature})
            huge = self.client.post("/messengers/whatsapp/webhook", content=b"x" * 1_000_001, headers={"X-Hub-Signature-256": signature})
        self.assertEqual(unsigned.status_code, 403)
        self.assertEqual(forged.status_code, 403)
        self.assertEqual(signed.status_code, 200)
        self.assertEqual(huge.status_code, 413)
        self.assertEqual(len(handled), 1)

    def test_proxy_registers_the_webhook_before_static_files(self) -> None:
        source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        self.assertLess(source.index("register_whatsapp(app)"), source.index('app.mount("/"'))
        self.assertIn("messengers_loop()", source)


class SettingsApiTest(unittest.TestCase):
    def setUp(self) -> None:
        import httpx
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.messenger_settings_api import register_messenger_routes
        reset_state()
        app = FastAPI()
        register_messenger_routes(app, httpx.AsyncClient(), proxy_url=lambda: "http://127.0.0.1:1")
        self.client = TestClient(app)

    def test_status_pair_unpair(self) -> None:
        status = self.client.get("/api/pg/messengers/status").json()
        self.assertIn("discord", status)
        self.assertEqual(status["whatsapp"]["webhook_path"], "/messengers/whatsapp/webhook")
        self.assertEqual(status["whatsapp"]["local_webhook"], "http://127.0.0.1:1/messengers/whatsapp/webhook")
        self.assertEqual(self.client.post("/api/pg/messengers/pair", json={"platform": "telegram"}).status_code, 400)
        code = self.client.post("/api/pg/messengers/pair", json={"platform": "discord"}).json()
        self.assertRegex(code["code"], r"^[A-Z2-9]{6}$")
        # The code is shown only once, in the /pair answer: the status endpoint
        # reports the pairing as active (with its expiry) but never the code
        # itself — only its salted hash is stored.
        pairing = self.client.get("/api/pg/messengers/status").json()["discord"]["pairing"]
        self.assertNotIn("code", pairing)
        self.assertGreater(pairing["expires"], 0)
        self.assertTrue(core.try_pair("discord", "42", "Ann", code["code"])[0])
        self.assertEqual(self.client.get("/api/pg/messengers/status").json()["discord"]["owner"]["id"], "42")
        self.assertTrue(self.client.post("/api/pg/messengers/unpair", json={"platform": "discord"}).json()["ok"])
        self.assertEqual(self.client.get("/api/pg/messengers/status").json()["discord"]["owner"], {})

    def test_verify_token_and_test_errors(self) -> None:
        token = self.client.post("/api/pg/messengers/verify-token", json={}).json()["token"]
        self.assertGreaterEqual(len(token), 24)
        self.assertEqual(pgdata.load_preferences()["whatsapp_verify_token"], token)
        bad = self.client.post("/api/pg/messengers/test", json={"platform": "discord", "token": "nope", "lang": "en"})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("Discord", bad.json()["error"])
        cloud = self.client.post("/api/pg/messengers/test", json={"platform": "whatsapp", "mode": "cloud", "lang": "en"})
        self.assertEqual(cloud.status_code, 400)
        waha = self.client.post("/api/pg/messengers/test", json={"platform": "whatsapp", "mode": "waha", "url": "http://127.0.0.1:9", "lang": "ru"})
        self.assertEqual(waha.status_code, 502)
        self.assertIn("WAHA", waha.json()["error"])


class UiTest(unittest.TestCase):
    def test_static_wiring(self) -> None:
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertRegex(html, r'src="/messengers-settings\.js\?v=[^"]+"')
        self.assertRegex(html, r'href="/messengers-settings\.css\?v=[^"]+"')
        self.assertLess(html.index("/telegram-settings.js"), html.index("/messengers-settings.js"))
        app_js = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn("MemuMessengers.wrap(messengersSettingsContext(), telegram)", app_js)
        self.assertIn('MemuMessengers.bind(messengersSettingsContext())', app_js)
        for key in messenger_prefs.PREF_DEFAULTS:
            self.assertIn(f"{key}:", app_js, key)

    def test_no_emoji_in_settings_ui(self) -> None:
        source = (STATIC / "messengers-settings.js").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", source))


if __name__ == "__main__":
    unittest.main()
