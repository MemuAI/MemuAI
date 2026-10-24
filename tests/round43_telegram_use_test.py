"""Personal-account Telegram Use contract tests. No live Telegram/API account is used."""
from __future__ import annotations

import asyncio
import datetime as dt
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import telegram_use as use
from app import telegram_use_ai as ai
from app import telegram_use_config as config


def message(mid=1, text="hello", *, sender=7, outgoing=False, media=None):
    row = types.SimpleNamespace(id=mid, raw_text=text, sender_id=sender, chat_id=7, out=outgoing,
        date=dt.datetime.now(dt.timezone.utc), media=media, document=None, photo=None, voice=None,
        audio=None, video=None, reply_to_msg_id=None, fwd_from=None)
    row.get_reply_message = AsyncMock(return_value=None)
    return row


class Client:
    def __init__(self):
        self.connected = True
        self.authorized = True
        self.rows = [message(3, "third"), message(2, "second"), message(1, "first")]
        self.dialogs = [types.SimpleNamespace(id=7, name="Иван Иванович", entity=types.SimpleNamespace(username="ivan"),
            input_entity="peer7", unread_count=2, is_group=False, is_channel=False)]
        self.send_message = AsyncMock(return_value=types.SimpleNamespace(id=101))
        self.edit_message = AsyncMock(return_value=types.SimpleNamespace(id=102))
        self.delete_messages = AsyncMock()
        self.send_read_acknowledge = AsyncMock()
        self.forward_messages = AsyncMock(return_value=[types.SimpleNamespace(id=103)])
        self.log_out = AsyncMock()
        self.sign_in = AsyncMock()
        self.send_code_request = AsyncMock(return_value=types.SimpleNamespace(phone_code_hash="volatile-code-hash"))
    def is_connected(self): return self.connected
    async def connect(self): self.connected = True
    async def disconnect(self): self.connected = False
    async def is_user_authorized(self): return self.authorized
    async def get_me(self): return types.SimpleNamespace(id=42, first_name="Me", last_name="", username="me", premium=False)
    async def get_input_entity(self, value):
        if value in (7, "@ivan", "me"): return "peer7"
        raise ValueError("Unknown")
    async def iter_dialogs(self, limit=1000):
        for row in self.dialogs[:limit]: yield row
    async def iter_messages(self, chat, limit=50, offset_id=0, search=""):
        rows = [m for m in self.rows if (not offset_id or m.id < offset_id) and (not search or search in m.raw_text)]
        for row in rows[:limit]: yield row
    async def get_messages(self, chat, ids):
        return next((m for m in self.rows if m.id == ids), None)
    async def download_media(self, msg, file): file.write(b"x" * 1000)


class TempConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="memuai-tguse-")
        self.root = Path(self.tmp.name)
        self.patcher = patch.object(config, "path", return_value=self.root / "telegram_use.json")
        self.patcher.start()
    def tearDown(self):
        self.patcher.stop(); config._stores.clear(); self.tmp.cleanup()


class ConfigCase(TempConfig):
    def test_proxy_protocols(self):
        p = config.parse_proxy("tg://proxy?server=example.com&port=443&secret=" + "a" * 32)
        self.assertEqual((p["kind"], p["port"]), ("mtproto", 443))
        self.assertEqual(config.parse_proxy("https://t.me/proxy?server=a&port=22&secret=dd" + "a" * 32)["kind"], "mtproto")
        self.assertEqual(config.parse_proxy("socks5://user:p%40ss@127.0.0.1:1080")["password"], "p@ss")
        self.assertEqual(config.parse_proxy("tg://socks?server=a&port=1080&user=u&pass=p")["username"], "u")
        self.assertEqual(config.parse_proxy("http://localhost:8080")["kind"], "http")
    def test_invalid_proxy_fails_explicitly(self):
        for url in ("tg://proxy?server=a&port=0&secret=" + "a" * 32, "tg://proxy?server=a&port=1&secret=ee" + "a" * 32,
                    "https://evil.example/proxy?server=a&port=2", "socks5://a:99999", "socks5://a", "file:///x"):
            with self.subTest(url=url), self.assertRaises(ValueError): config.parse_proxy(url)
    def test_commands_normalized_and_duplicate_rejected(self):
        c = config.command({"trigger":"!//custom", "allowed_users":"7, 7 8", "context_messages":99999})
        self.assertEqual(c["allowed_users"], [7, 8]); self.assertEqual(c["context_messages"], 200)
        with self.assertRaises(ValueError): config.normalize({"commands":[c, {**c, "trigger":"!//CUSTOM"}]})
        with self.assertRaises(ValueError): config.command({"trigger":"send anything"})
    def test_secrets_protected_and_public_config_clean(self):
        secret = "private-session-value-abcdef"
        config.secrets().set("session", secret); config.secrets().set("api_hash", "b" * 32)
        config.save({"api_id":123, "enabled":True, "api_hash":"should-not-save"})
        self.assertEqual(config.secrets().get("session"), secret)
        self.assertNotIn(secret, config.secrets().path.read_text())
        self.assertNotIn("api_hash", config.load())
        result = use.integration_status()
        self.assertTrue(result["connected"]); self.assertIn("telegram_use_media{", " ".join(result["tools"]))
    def test_html_entity_offsets_basic_spoiler_no_premium(self):
        plain, entities = use.parse_html("😀 <b>bold</b> <tg-spoiler>hidden</tg-spoiler> <code>x</code> <tg-emoji emoji-id='123'>🙂</tg-emoji>")
        kinds = {type(e).__name__:e for e in entities}
        self.assertEqual(kinds["MessageEntityBold"].offset, 3)
        self.assertIn("MessageEntitySpoiler", kinds)
        self.assertNotIn("MessageEntityCustomEmoji", kinds)
        self.assertIn("hidden", plain)
    def test_no_secret_in_unknown_error(self):
        class NetworkFailure(Exception): pass
        self.assertNotIn("very-secret", use.friendly(NetworkFailure("very-secret")))
    def test_runtime_thread_stops_and_releases_owner(self):
        config.save({"enabled":False})
        runtime = use.Runtime(lambda:Client()).start()
        self.assertFalse(runtime.submit("status")["connected"])
        with patch.object(use,"_instance",runtime):
            use.stop()
        self.assertFalse(runtime.thread.is_alive())


class RuntimeCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="memuai-tguse-runtime-")
        self.pathpatch = patch.object(config, "path", return_value=Path(self.tmp.name) / "telegram_use.json")
        self.pathpatch.start()
        config.save({"enabled":True, "api_id":123, "commands_enabled":True, "model":"fake-model"})
        config.secrets().set("api_hash", "b" * 32)
        self.client = Client()
        self.runtime = use.Runtime(lambda: self.client)
        await self.runtime._connect()
        self.runtime._load_usage()
    async def asyncTearDown(self):
        await self.runtime._action("close", {})
        self.pathpatch.stop(); config._stores.clear(); self.tmp.cleanup()
    def event(self, text="!//question hello", *, own=True, sender=42, chat=7, private=True, mid=10):
        msg = message(mid, text, sender=sender, outgoing=own)
        self.client.rows.append(msg)
        return types.SimpleNamespace(message=msg, sender_id=sender, chat_id=chat, is_private=private)
    async def settle(self):
        if self.runtime._tasks: await asyncio.gather(*self.runtime._tasks)
        await asyncio.sleep(0)
    async def test_tools_resolve_history_search_budget_and_ambiguity(self):
        rows = await self.runtime.tool("telegram_use_dialogs", {"query":"Иван"})
        self.assertEqual(rows["chats"][0]["id"], 7)
        result = await self.runtime.tool("telegram_use_history", {"chat":"Иван Иванович","limit":2})
        self.assertEqual([m["id"] for m in result["messages"]], [3, 2])
        self.client.dialogs.append(types.SimpleNamespace(**{**vars(self.client.dialogs[0]), "id":8,"input_entity":"peer8"}))
        with self.assertRaisesRegex(use.TelegramUseError, "Ambiguous"):
            await self.runtime.resolve("Иван Иванович")
        self.client.rows = [message(1,"x" * 40000), message(2,"y" * 40000)]
        result = await self.runtime.tool("telegram_use_search", {"query":"x"})
        self.assertLessEqual(sum(len(m["text"]) for m in result["messages"]), 32000)
    async def test_send_format_split_edit_ownership_and_ids(self):
        result = await self.runtime.tool("telegram_use_send", {"chat":"7","text":"**bold** " + "😀" * 5000})
        self.assertGreater(result["parts"], 1)
        kwargs = self.client.send_message.call_args_list[0].kwargs
        self.assertTrue(kwargs["formatting_entities"])
        with self.assertRaisesRegex(ValueError, "Only own"):
            await self.runtime.tool("telegram_use_edit", {"chat":"7","message_id":1,"text":"edit"})
        self.client.rows[0].out = True
        await self.runtime.tool("telegram_use_edit", {"chat":"7","message_id":3,"text":"edit"})
        self.client.edit_message.assert_awaited_once()
        with self.assertRaises(ValueError):
            await self.runtime.tool("telegram_use_delete", {"chat":"7","message_ids":[-1]})
    async def test_schedule_write_requires_explicit_setting(self):
        with self.assertRaises(use.TelegramUseError):
            await self.runtime.tool("telegram_use_send", {"chat":"7","text":"hello"}, scheduled=True)
        config.save({"schedule_writes":True})
        await self.runtime.tool("telegram_use_send", {"chat":"7","text":"hello"}, scheduled=True)
        config.save({"allow_writes":False})
        with self.assertRaises(use.TelegramUseError):
            await self.runtime.tool("telegram_use_mark_read", {"chat":"7"})
    async def test_outgoing_command_context_replacement_and_dedup(self):
        event = self.event()
        with patch.object(ai, "complete", AsyncMock(return_value="**answer**")) as completion:
            await self.runtime._on_event(event); await self.runtime._on_event(event); await self.settle()
            self.assertEqual(completion.await_count, 1)
            context = completion.call_args.args[1][1]["content"]
            self.assertIn("third", context); self.assertNotIn("!//question", context)
        self.client.edit_message.assert_awaited_once()
        self.assertEqual(self.client.edit_message.call_args.args[1:3], (10,"answer"))
    async def test_external_allowlist_private_reply_and_group_scoping(self):
        external = self.event(own=False, sender=9)
        with patch.object(ai,"complete",AsyncMock(return_value="answer")) as completion:
            await self.runtime._on_event(external); await self.settle(); completion.assert_not_awaited()
            cfg = config.load(); cfg["commands"][0]["allowed_users"] = [9]; config.save(cfg)
            await self.runtime._on_event(external); await self.settle(); completion.assert_awaited_once()
            self.client.send_message.assert_awaited_once(); self.client.edit_message.assert_not_awaited()
            group = self.event(own=False,sender=9,private=False,chat=-100,mid=11)
            await self.runtime._on_event(group); await self.settle(); self.assertEqual(completion.await_count,1)
    async def test_quota_persists_across_restart_and_forwarded_ignored(self):
        cfg = config.load(); cfg["commands"][0]["max_per_hour"] = 1; config.save(cfg)
        with patch.object(ai,"complete",AsyncMock(return_value="answer")) as completion:
            await self.runtime._on_event(self.event()); await self.settle()
            await self.runtime._on_event(self.event(mid=11)); await self.settle()
            self.assertEqual(completion.await_count,1)
            other = use.Runtime(lambda:self.client); other._load_usage()
            self.assertTrue(other._rates[(42,"!//question")])
            forwarded = self.event("!//summary",mid=12); forwarded.message.fwd_from = object()
            await self.runtime._on_event(forwarded); await self.settle(); self.assertEqual(completion.await_count,1)
    async def test_changed_command_or_message_cancels_delivery(self):
        gate = asyncio.Event()
        async def delayed(*args): await gate.wait(); return "answer"
        event = self.event()
        with patch.object(ai,"complete",delayed):
            await self.runtime._on_event(event); await asyncio.sleep(0)
            cfg = config.load(); cfg["commands"][0]["allowed_users"] = [99]; config.save(cfg)
            gate.set(); await self.settle()
        self.client.edit_message.assert_not_awaited()
    async def test_voice_reuses_shared_transcription(self):
        voice = message(media=object()); voice.voice = object(); voice.document = types.SimpleNamespace(mime_type="audio/ogg",size=1000)
        with patch("app.telegram_voice.transcribe", AsyncMock(return_value="transcript")) as transcribe:
            result = await self.runtime.media(voice)
            self.assertEqual(result["text"], "transcript")
            transcribe.assert_awaited_once()
    async def test_media_limit_enforced_while_downloading(self):
        config.save({"max_media_mb":1})
        voice = message(media=object()); voice.document = types.SimpleNamespace(size=2*1024*1024)
        with self.assertRaises(ValueError): await self.runtime._download(voice)
        voice.document.size = 0
        async def oversized(msg,file): file.write(b"x" * (1024*1024+1))
        self.client.download_media = oversized
        with self.assertRaises(ValueError): await self.runtime._download(voice)
    async def test_login_code_and_password_flow_no_credentials_in_status(self):
        self.runtime.me = None; self.client.authorized = False
        result = await self.runtime.action("phone", {"phone":"+79991234567"})
        self.assertEqual(result["flow"]["phase"],"code")
        self.assertNotIn("79991234567", str(result)); self.assertNotIn("volatile-code-hash",str(result))
        SessionPasswordNeededError = type("SessionPasswordNeededError", (Exception,), {})
        self.client.sign_in.side_effect = SessionPasswordNeededError()
        result = await self.runtime.action("code", {"code":"12345"})
        self.assertEqual(result["flow"]["phase"],"password")
        self.client.sign_in.side_effect = None
        await self.runtime.action("password", {"password":"private-pass"})
        self.assertEqual(self.runtime.flow["phase"],"connected")
        self.assertFalse(self.runtime._phone_hash)
    async def test_bad_settings_atomic_no_secret_mutation(self):
        before = config.load()
        with self.assertRaises(use.TelegramUseError):
            await self.runtime.action("settings", {"api_hash":"a" * 32,"proxy":"socks5://bad:99999","commands_enabled":False})
        self.assertEqual(config.load(), before); self.assertEqual(config.secrets().get("api_hash"),"b"*32)
    async def test_memuai_delivery_does_not_send_to_telegram(self):
        cfg = config.load(); cfg["commands"][0]["output"] = "memuai"; config.save(cfg)
        with patch.object(ai,"complete",AsyncMock(return_value="answer")), patch.object(ai,"save_result",AsyncMock(return_value={"saved":True})) as saved:
            await self.runtime._on_event(self.event()); await self.settle(); saved.assert_awaited_once()
        self.client.send_message.assert_not_awaited(); self.client.edit_message.assert_not_awaited()
    async def test_qr_login_completes_and_hides_login_token(self):
        self.runtime.me = None; self.client.authorized=False
        qr=types.SimpleNamespace(url="tg://login?token=private-login-token",expires=dt.datetime.now(dt.timezone.utc)+dt.timedelta(seconds=30))
        async def accepted(timeout): self.client.authorized=True
        qr.wait=accepted; qr.recreate=AsyncMock()
        self.client.qr_login=AsyncMock(return_value=qr)
        result=await self.runtime.action("qr",{})
        self.assertEqual(result["flow"]["phase"],"qr")
        await self.runtime._qr_task
        self.assertTrue(self.runtime.status()["connected"])
        self.assertNotIn("private-login-token",str(self.runtime.status()))
    async def test_editing_same_text_is_idempotent_and_command_quote_does_not_loop(self):
        self.client.rows[0].out=True
        self.client.edit_message.side_effect=type("MessageNotModifiedError",(Exception,),{})()
        result=await self.runtime.tool("telegram_use_edit",{"chat":"7","message_id":3,"text":"third"})
        self.assertEqual(result["message_ids"],[3])
        await self.runtime.tool("telegram_use_send",{"chat":"7","text":"!//question quoted command"})
        self.assertTrue(self.client.send_message.call_args.args[1].startswith("\u2060!//"))
    async def test_user_edit_during_generation_preserves_new_message(self):
        gate=asyncio.Event()
        async def delayed(*args): await gate.wait(); return "answer"
        event=self.event()
        with patch.object(ai,"complete",delayed):
            await self.runtime._on_event(event); await asyncio.sleep(0)
            self.client.rows=[message(10,"manually edited",outgoing=True) if m.id==10 else m for m in self.client.rows]
            gate.set(); await self.settle()
        self.client.edit_message.assert_not_awaited()
    async def test_image_media_uses_chosen_vision_model(self):
        photo=message(media=object()); photo.photo=object()
        with patch.object(ai,"image_content",return_value={"type":"image_url","image_url":{"url":"data:image/jpeg;base64,eA=="}}), patch.object(ai,"complete",AsyncMock(return_value="blue picture")) as complete:
            result=await self.runtime.media(photo,"What is here?","vision-model")
        self.assertEqual(result["text"],"blue picture")
        self.assertEqual(complete.call_args.args[0],"vision-model")
        self.assertEqual(complete.call_args.args[1][0]["content"][1]["type"],"image_url")
    async def test_memuai_output_persists_standard_chat(self):
        from app import pgdata
        with patch.object(pgdata,"MEMU_DIR",Path(self.tmp.name)):
            result=await ai.save_result("Telegram result","question","answer","model")
            rows=pgdata.load_chats()
        row=next(r for r in rows if r["id"]==result["chat_id"])
        self.assertEqual(row["messages"][1]["content"],"answer")
        self.assertEqual(row["model"],"model")
    async def test_disconnect_forgets_session_even_when_telegram_is_offline(self):
        config.secrets().set("session","fake-private-session")
        self.client.log_out.side_effect=OSError("offline")
        result=await self.runtime.action("disconnect",{})
        self.assertFalse(result["config"]["enabled"])
        self.assertFalse(result["has_session"])
        self.assertIn("Devices",result["last_error"])


class APICase(TempConfig):
    def test_api_origin_and_private_bridge_permissions(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import telegram_use_api as api
        app = FastAPI(); api.register(app)
        fake = types.SimpleNamespace(submit=lambda *a: {"flow":{"phase":"idle"},"connected":False})
        with patch.object(use,"start",return_value=fake):
            client = TestClient(app,base_url="http://127.0.0.1")
            self.assertEqual(client.get("/api/pg/integrations/telegram-use").status_code,200)
            self.assertEqual(client.get("/api/pg/integrations/telegram-use",headers={"Origin":"https://evil.example"}).status_code,403)
            self.assertEqual(client.get("/api/pg/integrations/telegram-use",headers={"Host":"evil.example"}).status_code,403)
            self.assertEqual(client.post("/api/pg/integrations/telegram-use/tool",json={"tool":"telegram_use_send"}).status_code,403)
            self.assertEqual(client.post("/api/pg/integrations/telegram-use/tool",json={"tool":"telegram_use_status"},headers={"X-MemuAI-Telegram-Use":api.bridge_token()}).status_code,200)


class AICase(unittest.IsolatedAsyncioTestCase):
    async def test_real_proxy_sse_parser_uses_selected_model_and_filters_thinking(self):
        import httpx
        original = httpx.AsyncClient
        requests = []
        def respond(request):
            import json
            requests.append(json.loads(request.content))
            return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"<thi"}}]}\n\ndata: {"choices":[{"delta":{"content":"nk>private reasoning</think>answer"}}]}\n\ndata: [DONE]\n\n', headers={"Content-Type":"text/event-stream"})
        def client(**kwargs): return original(transport=httpx.MockTransport(respond), **kwargs)
        with patch.object(ai.httpx,"AsyncClient",client):
            result = await ai.complete("chosen-model", [{"role":"user","content":"question"}])
        self.assertEqual(result,"answer")
        self.assertEqual(requests[0]["model"],"chosen-model")
        self.assertTrue(requests[0]["tools_available"])
        self.assertTrue(requests[0]["prompt_caching"])
    async def test_proxy_failure_is_not_a_blank_success(self):
        import httpx
        original = httpx.AsyncClient
        def client(**kwargs): return original(transport=httpx.MockTransport(lambda request:httpx.Response(401)),**kwargs)
        with patch.object(ai.httpx,"AsyncClient",client), self.assertRaisesRegex(RuntimeError,"401"):
            await ai.complete("model",[{"role":"user","content":"question"}])
    async def test_image_reencoded_and_video_sampled(self):
        import io
        from PIL import Image
        import av
        raw = io.BytesIO(); Image.new("RGB",(64,48),"blue").save(raw,"PNG")
        image = ai.image_content(raw.getvalue(),"image/png")
        self.assertTrue(image["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        video = io.BytesIO()
        with av.open(video,mode="w",format="mp4") as container:
            stream = container.add_stream("mpeg4",rate=6); stream.width=64; stream.height=48; stream.pix_fmt="yuv420p"
            for index in range(12):
                frame = av.VideoFrame.from_image(Image.new("RGB",(64,48),(index*20,0,0)))
                for packet in stream.encode(frame): container.mux(packet)
            for packet in stream.encode(): container.mux(packet)
        frames = ai.video_content(video.getvalue())
        self.assertGreaterEqual(len(frames),2); self.assertLessEqual(len(frames),12)
        self.assertEqual(frames[0]["type"],"text")


if __name__ == "__main__": unittest.main()
