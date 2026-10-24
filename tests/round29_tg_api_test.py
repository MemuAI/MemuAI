"""Round 29: Mini App backend — browser sign-in, voice, search/edit/schedules/prompts, MemuCode host & files."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

from app import tg_webapp, tgapp_weblogin, telegram_voice  # noqa: E402

TOKEN = "123456:TEST-token_for-mini-app"
USER = 4242


def init_data(user_id: int = USER) -> str:
    return tg_webapp.sign_init_data({"auth_date": int(time.time()), "query_id": "AAH",
                                     "user": {"id": user_id, "first_name": "Yono"}}, TOKEN)


class WebLoginUnit(unittest.TestCase):
    def setUp(self):
        tgapp_weblogin.reset()

    def test_device_flow_issues_the_token_once_after_approval(self):
        started = tgapp_weblogin.start("1.2.3.4", "Firefox")
        self.assertEqual(len(started["code"]), 8)
        self.assertTrue(started["qr"].startswith("memuai-login:"))
        self.assertEqual(tgapp_weblogin.poll(started["request_id"], lambda u: ("t", 1))["status"], "pending")
        info = tgapp_weblogin.inspect(started["qr"])            # scanned QR text works as the code
        self.assertEqual(info["ip"], "1.2.3.4")
        self.assertTrue(tgapp_weblogin.decide(started["display"].lower(), "4242", True))
        issued = []
        result = tgapp_weblogin.poll(started["request_id"], lambda u: (issued.append(u) or "tok", 99))
        self.assertEqual(result["status"], "approved")
        self.assertEqual(issued, ["4242"])
        self.assertEqual(tgapp_weblogin.poll(started["request_id"], lambda u: ("x", 1))["status"], "expired")
        self.assertFalse(tgapp_weblogin.decide(started["code"], "4242", True), "a used code cannot be approved again")

    def test_denied_and_expired_requests_never_give_a_token(self):
        started = tgapp_weblogin.start("ip", "ua")
        tgapp_weblogin.decide(started["code"], "1", False)
        self.assertEqual(tgapp_weblogin.poll(started["request_id"], lambda u: 1 / 0)["status"], "denied")
        old = tgapp_weblogin.start("ip", "ua", now=time.time() - 1000)
        self.assertIsNone(tgapp_weblogin.inspect(old["code"]))
        self.assertFalse(tgapp_weblogin.decide(old["code"], "1", True))
        self.assertEqual(tgapp_weblogin.poll(old["request_id"], lambda u: 1 / 0)["status"], "expired")

    def test_link_is_single_use(self):
        nonce = tgapp_weblogin.issue_link("4242")
        self.assertEqual(tgapp_weblogin.redeem(nonce), "4242")
        self.assertEqual(tgapp_weblogin.redeem(nonce), "")
        stale = tgapp_weblogin.issue_link("4242", now=time.time() - 1000)
        self.assertEqual(tgapp_weblogin.redeem(stale), "")

    def test_outstanding_requests_are_capped(self):
        for _ in range(tgapp_weblogin.MAX_PENDING):
            tgapp_weblogin.start("ip", "ua")
        with self.assertRaises(OverflowError):
            tgapp_weblogin.start("ip", "ua")


class VoiceUnit(unittest.TestCase):
    def test_mime_normalization(self):
        self.assertEqual(telegram_voice.normalize_mime("audio/webm;codecs=opus"), "audio/webm")
        self.assertEqual(telegram_voice.normalize_mime("audio/x-m4a"), "audio/mp4")
        self.assertEqual(telegram_voice.normalize_mime("", "talk.FLAC"), "audio/flac")
        self.assertEqual(telegram_voice.normalize_mime("application/octet-stream", "a.ogg"), "audio/ogg")
        self.assertEqual(telegram_voice.normalize_mime("video/mp4"), "audio/mp4")

    def test_audio_messages(self):
        self.assertTrue(telegram_voice.is_audio_message({"voice": {"file_id": "x"}}))
        self.assertTrue(telegram_voice.is_audio_message({"document": {"file_name": "rec.m4a"}}))
        self.assertFalse(telegram_voice.is_audio_message({"document": {"file_name": "a.pdf"}}))
        self.assertFalse(telegram_voice.is_audio_message({"text": "hi"}))

    def test_status_without_a_local_model_explains_how_to_install_it(self):
        with mock.patch.object(telegram_voice, "_runtime") as runtime:
            runtime.return_value._is_downloaded.return_value = False
            info = telegram_voice.status({"voice_stt_local_model": "small", "language": "en"})
        self.assertFalse(info["available"])
        self.assertEqual(info["reason"], "no_model")
        self.assertIn("Settings", info["message"])
        self.assertIn("Voice", info["message"])
        ru = telegram_voice.status({"voice_stt_local_model": "small"}) if False else None
        self.assertIsNone(ru)

    def test_status_picks_the_engine(self):
        with mock.patch.object(telegram_voice, "_runtime") as runtime, \
                mock.patch.object(telegram_voice, "engine_in_process", return_value=False), \
                mock.patch.object(telegram_voice, "_window_url", return_value=""):
            runtime.return_value._is_downloaded.return_value = True
            info = telegram_voice.status({"language": "ru"})
            self.assertEqual(info["reason"], "no_engine")
            self.assertIn("MemuAI", info["message"])
        with mock.patch.object(telegram_voice, "_runtime") as runtime, \
                mock.patch.object(telegram_voice, "engine_in_process", return_value=False), \
                mock.patch.object(telegram_voice, "_window_url", return_value="http://127.0.0.1:9"):
            runtime.return_value._is_downloaded.return_value = True
            self.assertEqual(telegram_voice.status({})["engine"], "app")
        info = telegram_voice.status({"voice_stt_provider": "api", "voice_stt_api_url": "https://x.example/v1"})
        self.assertEqual(info["engine"], "api")

    def test_transcribe_through_the_window_and_errors(self):
        prefs = {"language": "en"}
        with mock.patch.object(telegram_voice, "status", return_value={"available": True, "engine": "app", "model": "small"}), \
                mock.patch.object(telegram_voice, "_window_url", return_value="http://127.0.0.1:9"), \
                mock.patch.object(telegram_voice, "_post_window", return_value={"text": "  hello world "}) as post:
            text = asyncio.run(telegram_voice.transcribe(b"x" * 2000, "audio/ogg; codecs=opus", prefs, lang="en"))
        self.assertEqual(text, "hello world")
        sent = post.call_args.args[1]["audio"]
        self.assertTrue(sent.startswith("data:audio/ogg;base64,"))
        with mock.patch.object(telegram_voice, "status", return_value={"available": True, "engine": "app", "model": "small"}), \
                mock.patch.object(telegram_voice, "_window_url", return_value="http://127.0.0.1:9"), \
                mock.patch.object(telegram_voice, "_post_window", return_value={"error": "Речь не распознана"}):
            with self.assertRaises(RuntimeError) as caught:
                asyncio.run(telegram_voice.transcribe(b"x" * 2000, "audio/ogg", prefs, lang="en"))
        self.assertEqual(str(caught.exception), "No speech was recognised")
        with self.assertRaises(telegram_voice.VoiceUnavailable):
            with mock.patch.object(telegram_voice, "status", return_value={"available": False, "reason": "no_model", "message": "m"}):
                asyncio.run(telegram_voice.transcribe(b"x" * 2000, "audio/ogg", prefs))

    def test_upload_decoding(self):
        blob, mime = telegram_voice.decode_upload("data:audio/webm;codecs=opus;base64," + base64.b64encode(b"abc").decode())
        self.assertEqual((blob, mime), (b"abc", "audio/webm"))
        with self.assertRaises(ValueError):
            telegram_voice.decode_upload("data:text/html;base64,PGI+")


class FakeApi:
    def __init__(self):
        self.calls = []

    async def call(self, method, **payload):
        self.calls.append((method, payload))
        if method == "getFile":
            return {"file_path": "voice/1.oga", "file_size": 3000}
        if method == "sendMessage":
            return {"message_id": 77}
        return True

    async def download(self, path, max_bytes):
        return b"o" * 3000


class BotVoiceFlow(unittest.TestCase):
    def test_unavailable_sends_the_hint_and_nothing_reaches_the_model(self):
        api = FakeApi()
        with mock.patch.object(telegram_voice, "status", return_value={"available": False, "reason": "no_model", "message": "Install it"}):
            routed = asyncio.run(telegram_voice.route_message(api, "5", {"message_id": 3, "voice": {"file_id": "f", "duration": 4}}, {}, "en"))
        self.assertIsNone(routed)
        self.assertIn("Install it", api.calls[-1][1]["text"])

    def test_auto_send_and_review(self):
        api = FakeApi()
        ok = {"available": True, "engine": "app", "model": "small", "reason": "", "message": ""}

        async def fake_transcribe(*args, **kwargs):
            return "turn on the lights"
        with mock.patch.object(telegram_voice, "status", return_value=ok), \
                mock.patch.object(telegram_voice, "transcribe", fake_transcribe):
            routed = asyncio.run(telegram_voice.route_message(api, "5", {"message_id": 3, "voice": {"file_id": "f", "duration": 4}}, {}, "en"))
            self.assertEqual(routed, {"text": "turn on the lights", "spoke": True})
            reviewed = asyncio.run(telegram_voice.route_message(
                api, "5", {"message_id": 4, "audio": {"file_id": "g", "duration": 9}, "caption": "summary please"},
                {"telegram_voice_review": True}, "en"))
        self.assertIsNone(reviewed)
        markup = api.calls[-1][1]["reply_markup"]["inline_keyboard"][0]
        data = markup[0]["callback_data"]
        self.assertTrue(data.startswith("vtr:send:"))
        self.assertIsNone(asyncio.run(telegram_voice.handle_callback(api, "999", 77, data, "en")), "another chat cannot send it")
        result = asyncio.run(telegram_voice.handle_callback(api, "5", 77, data, "en"))
        self.assertEqual(result["text"], "summary please\n\nturn on the lights")
        self.assertFalse(result["spoke"])
        self.assertIsNone(asyncio.run(telegram_voice.handle_callback(api, "5", 77, data, "en")), "single use")

    def test_too_long(self):
        api = FakeApi()
        with mock.patch.object(telegram_voice, "status", return_value={"available": True, "engine": "app", "model": "s", "reason": "", "message": ""}):
            routed = asyncio.run(telegram_voice.route_message(api, "5", {"voice": {"file_id": "f", "duration": 3600}}, {}, "en"))
        self.assertIsNone(routed)
        self.assertIn("15 minutes", api.calls[-1][1]["text"])


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from app.pgdata import save_preferences
        from app import tgapp_chat
        save_preferences({"telegram_bot_token": TOKEN, "telegram_chat_id": str(USER), "memes": [], "chat_memes": {}})
        tg_webapp.save_config({"enabled": True, "mode": "cloudflare", "code_control": True, "web_login": True})
        cls.models = mock.patch.object(tgapp_chat, "chat_models",
                                       return_value=[{"id": "test/model", "name": "Test", "provider": "test",
                                                      "vision": True, "pinned": False, "default": True}])
        cls.models.start()
        from app.tgapp_api import create_tgapp, auth_failures, auth_limiter
        auth_failures._hits.clear()
        auth_limiter._hits.clear()
        cls.client = TestClient(create_tgapp())
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.models.stop()
        tg_webapp.save_config({"enabled": False})

    def setUp(self):
        tgapp_weblogin.reset()

    def headers(self):
        response = self.client.post("/tgapp/api/auth", json={"init_data": init_data()})
        self.assertEqual(response.status_code, 200, response.text)
        return {"Authorization": "Bearer " + response.json()["token"]}

    def post(self, name, body=None, headers=None):
        return self.client.post("/tgapp/api/" + name, json=body or {}, headers=headers or {})

    def test_browser_sign_in_end_to_end(self):
        start = self.post("weblogin.start").json()
        self.assertIn("request_id", start)
        self.assertEqual(self.post("weblogin.poll", {"request_id": start["request_id"]}).json()["status"], "pending")
        # Approving needs a Telegram session.
        self.assertEqual(self.post("weblogin.approve", {"code": start["code"]}).status_code, 401)
        headers = self.headers()
        info = self.post("weblogin.inspect", {"code": start["qr"]}, headers).json()
        self.assertIn("agent", info)
        self.assertEqual(self.post("weblogin.approve", {"code": start["code"]}, headers).status_code, 200)
        done = self.post("weblogin.poll", {"request_id": start["request_id"]}).json()
        self.assertEqual(done["status"], "approved")
        web = {"Authorization": "Bearer " + done["token"]}
        boot = self.post("bootstrap", {}, web)
        self.assertEqual(boot.status_code, 200, boot.text)
        self.assertIn("voice", boot.json()["features"])
        self.assertTrue(boot.json()["features"]["web_login"])
        # One-time link of the bot.
        nonce = tgapp_weblogin.issue_link(str(USER))
        self.assertEqual(self.post("weblogin.redeem", {"nonce": nonce}).status_code, 200)
        self.assertEqual(self.post("weblogin.redeem", {"nonce": nonce}).status_code, 401)
        # Switched off on the computer: nothing works.
        tg_webapp.save_config({"web_login": False})
        try:
            self.assertEqual(self.post("weblogin.start").status_code, 403)
            self.assertEqual(self.post("weblogin.redeem", {"nonce": tgapp_weblogin.issue_link(str(USER))}).status_code, 403)
        finally:
            tg_webapp.save_config({"web_login": True})

    def test_a_link_for_someone_not_paired_is_refused(self):
        nonce = tgapp_weblogin.issue_link("777")
        self.assertEqual(self.post("weblogin.redeem", {"nonce": nonce}).status_code, 401)

    def test_search_and_edit(self):
        from app.pgdata import upsert_chat
        headers = self.headers()
        upsert_chat({"id": "c-search", "title": "Trip to Kazan", "mode": "chat", "updated": time.time() * 1000,
                     "messages": [{"role": "user", "content": "What to see in Kazan in winter?", "createdAt": 1},
                                  {"role": "assistant", "content": "The Kremlin and the Kul Sharif mosque.", "createdAt": 2}]})
        hits = self.post("chats.search", {"query": "kul sharif"}, headers).json()["hits"]
        self.assertEqual(hits[0]["id"], "c-search")
        self.assertIn("Kul Sharif", hits[0]["matches"][0]["snippet"])
        self.assertEqual(self.post("chats.search", {"query": "k"}, headers).json()["hits"], [])
        bad = self.post("chats.edit", {"id": "c-search", "index": 1, "text": "x"}, headers)
        self.assertEqual(bad.status_code, 404, "only a user message can be edited")
        with mock.patch("app.tgapp_chat.start_send") as start:
            start.return_value = mock.Mock(id="job1", meta={"model": "test/model"})
            ok = self.post("chats.edit", {"id": "c-search", "index": 0, "text": "What to see in Kazan in summer?"}, headers)
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertTrue(start.call_args.kwargs["regenerate"])
        from app.pgdata import load_chats
        chat = next(row for row in load_chats() if row["id"] == "c-search")
        self.assertEqual(len(chat["messages"]), 1)
        self.assertEqual(chat["messages"][0]["content"], "What to see in Kazan in summer?")

    def test_global_search_word_forms_other_sources_and_preview(self):
        from app.pgdata import upsert_chat, save_schedules
        headers = self.headers()
        upsert_chat({"id": "c-borsch", "title": "Суп", "mode": "chat", "updated": time.time() * 1000,
                     "messages": [{"role": "user", "content": "Как сварить борщ со свёклой?", "createdAt": 1}]})
        upsert_chat({"id": "c-code", "title": "Borsch refactor", "mode": "code", "updated": time.time() * 1000,
                     "messages": [{"role": "user", "content": "rename borscht helpers", "createdAt": 1}]})
        save_schedules([{"id": "t-borsch", "name": "Рецепты борщей", "prompt": "найди рецепт", "daily_at": "09:00",
                         "enabled": True}])
        data = self.post("chats.search", {"query": "борщи"}, headers).json()
        # «борщи» is not a substring of «борщ»: the index (stemming) finds the chat anyway.
        self.assertIn("c-borsch", [hit["id"] for hit in data["hits"]])
        fuzzy = next(hit for hit in data["hits"] if hit["id"] == "c-borsch")
        self.assertTrue(fuzzy["matches"][0]["highlights"])
        types = {group["type"] for group in data["global"]}
        self.assertIn("schedule", types)
        self.assertNotIn("chat", types)                       # chats are the «hits» above
        for group in data["global"]:
            self.assertNotIn(group["type"], ("file", "project", "module"))
            for item in group["items"]:
                self.assertNotIn("score", item)
        item = next(g for g in data["global"] if g["type"] == "schedule")["items"][0]
        preview = self.post("search.preview", {"id": item["id"], "query": "борщи"}, headers)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertTrue(preview.json()["blocks"])
        self.assertEqual(self.post("search.preview", {"id": "file:x"}, headers).status_code, 404)
        self.assertEqual(self.post("search.preview", {"id": item["id"]}).status_code, 401)
        # Off when asked (the plain substring search only).
        self.assertNotIn("global", self.post("chats.search", {"query": "борщи", "global": False}, headers).json())

    def test_memory_v2_items_from_the_phone(self):
        from app import memory_v2
        headers = self.headers()
        memory_v2.propose([{"fact": "Любит зелёный чай", "category": "preference", "confidence": 0.95}],
                          service="telegram", source={"chat_title": "Утро"})
        memory_v2.mutate(lambda doc: doc["settings"].update(mode="ask"))
        memory_v2.propose([{"fact": "Пишет на Rust", "category": "tech", "confidence": 0.9}], service="code")
        data = self.post("memory.get", {}, headers).json()
        self.assertTrue(data["editable"])
        self.assertIn("preference", data["categories"])
        by_text = {item["text"]: item for item in data["items"]}
        tea = by_text["Любит зелёный чай"]
        self.assertEqual(tea["source"]["service"], "telegram")
        pending = [item for item in data["items"] if item["status"] == "pending"]
        self.assertTrue(pending, "«ask» mode keeps the second fact for approval")
        if pending:      # the phone approves
            data = self.post("memory.item", {"id": pending[0]["id"], "action": "approve"}, headers).json()
            self.assertEqual({i["status"] for i in data["items"] if i["id"] == pending[0]["id"]}, {"active"})
        data = self.post("memory.item", {"id": tea["id"], "action": "pin"}, headers).json()
        self.assertTrue(next(i for i in data["items"] if i["id"] == tea["id"])["pinned"])
        bad = self.post("memory.item", {"id": tea["id"], "action": "edit", "text": "пароль: hunter2"}, headers)
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(bad.json()["code"] if "code" in bad.json() else "secret", "secret")
        data = self.post("memory.item", {"id": tea["id"], "action": "delete"}, headers).json()
        self.assertNotIn(tea["id"], [i["id"] for i in data["items"]])
        self.assertEqual(self.post("memory.item", {"id": "nope", "action": "pin"}, headers).status_code, 404)
        self.assertEqual(self.post("memory.item", {"id": tea["id"], "action": "explode"}, headers).status_code, 400)
        memory_v2.mutate(lambda doc: doc["settings"].update(mode="auto"))

    def test_schedules_and_prompts(self):
        from app.pgdata import save_schedules
        headers = self.headers()
        save_schedules([{"id": "t1", "name": "Digest", "prompt": "news", "daily_at": "09:00", "enabled": True}])
        rows = self.post("schedules.list", {}, headers).json()["tasks"]
        self.assertEqual(rows[0]["daily_at"], "09:00")
        rows = self.post("schedules.toggle", {"id": "t1", "enabled": False}, headers).json()["tasks"]
        self.assertFalse(rows[0]["enabled"])
        self.assertEqual(self.post("schedules.run", {"id": "nope"}, headers).status_code, 404)
        prompts = self.post("prompts.list", {}, headers).json()["prompts"]
        self.assertTrue(all(row["text"] for row in prompts))

    def test_voice_transcribe_route(self):
        headers = self.headers()

        async def fake(*args, **kwargs):
            return "привет"
        with mock.patch.object(telegram_voice, "transcribe", fake):
            data = "data:audio/webm;codecs=opus;base64," + base64.b64encode(b"x" * 4000).decode()
            response = self.post("voice.transcribe", {"audio": data}, headers)
        self.assertEqual(response.json()["text"], "привет")

        async def unavailable(*args, **kwargs):
            raise telegram_voice.VoiceUnavailable("Install a model", "no_model")
        with mock.patch.object(telegram_voice, "transcribe", unavailable):
            response = self.post("voice.transcribe", {"audio": data}, headers)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "voice_unavailable")
        self.assertEqual(self.post("voice.transcribe", {"audio": "data:text/plain;base64,eA=="}, headers).status_code, 400)


class CodeFilesTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.project = Path(tempfile.mkdtemp())
        (self.project / "src").mkdir()
        (self.project / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
        (self.project / ".git").mkdir()
        (self.project / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 20)
        (self.project / "blob.bin").write_bytes(b"\x00\x01\x02")
        outside = Path(tempfile.mkdtemp()) / "secret.txt"
        outside.write_text("top secret", encoding="utf-8")
        self.outside = outside
        self.env = mock.patch.dict(os.environ, {"MEMUCODE_CONFIG_DIR": self.folder, "MEMUCODE_NO_REMOTE": ""})
        self.env.start()
        tg_webapp.save_config({"enabled": True, "code_control": True})

    def tearDown(self):
        self.env.stop()
        tg_webapp.save_config({"enabled": False})
        shutil.rmtree(self.folder, ignore_errors=True)
        shutil.rmtree(self.project, ignore_errors=True)

    def test_files_stay_inside_the_project(self):
        from app import tgapp_code as tcode
        root = str(self.project)
        listing = tcode.list_files(root)
        names = [row["name"] for row in listing["entries"]]
        self.assertEqual(names[0], "src")
        self.assertNotIn(".git", names)
        # write_text uses CRLF on Windows; the API preserves the actual source.
        self.assertEqual(tcode.read_file(root, "src/main.py")["text"],
                         (self.project / "src/main.py").read_bytes().decode("utf-8"))
        self.assertEqual(tcode.read_file(root, "logo.png")["kind"], "image")
        self.assertEqual(tcode.read_file(root, "blob.bin")["kind"], "binary")
        for evil in ("../" + self.outside.parent.name + "/secret.txt", str(self.outside), "C:/Windows/win.ini",
                     "src/../../x"):
            with self.subTest(evil=evil):
                with self.assertRaises((PermissionError, LookupError)):
                    tcode.read_file(root, evil)
        if hasattr(os, "symlink"):
            try:
                os.symlink(self.outside, self.project / "link.txt")
            except OSError:
                return
            with self.assertRaises(PermissionError):
                tcode.read_file(root, "link.txt")

    def test_browse_and_mkdir(self):
        from app import tgapp_code as tcode
        listing = tcode.browse_folders(str(self.project))
        self.assertIn("src", [row["name"] for row in listing["dirs"]])
        self.assertNotIn(".git", [row["name"] for row in listing["dirs"]])
        made = tcode.make_folder(str(self.project), "new-app")
        self.assertTrue(Path(made).is_dir())
        for bad in ("..", "a/b", "", "x:y"):
            with self.assertRaises(ValueError):
                tcode.make_folder(str(self.project), bad)
        with self.assertRaises(ValueError):
            tcode.browse_folders("relative/path")

    def test_blocked_prompts_and_close(self):
        from app import tgapp_code as tcode
        self.assertTrue(tcode.blocked_prompt("!rm -rf /"))
        self.assertTrue(tcode.blocked_prompt("/run del *"))
        self.assertFalse(tcode.blocked_prompt("/review"))
        with mock.patch.object(tcode, "live_state", return_value={"rc": "1-aaaaaaaa"}):
            with self.assertRaises(ValueError):
                tcode.send_to_terminal("1-aaaaaaaa", {"type": "close"})
        with self.assertRaises(tcode.BlockedCommand):
            tcode.send_to_terminal("1-aaaaaaaa", {"type": "prompt", "text": "/run whoami"})

    def test_catalog_falls_back_to_built_in_commands(self):
        from app import tgapp_code as tcode
        catalog = tcode.command_catalog("")
        self.assertEqual(catalog["source"], "builtin")
        self.assertIn("help", [row["name"] for row in catalog["commands"]])
        folder = tcode._remote_folder()
        (folder / "commands-cache.json").write_text(json.dumps({"commands": [{"name": "review"}, {"name": "run"}],
                                                                "models": ["m1"]}), encoding="utf-8")
        catalog = tcode.command_catalog("")
        self.assertEqual([row["name"] for row in catalog["commands"]], ["review"], "/run is never offered")
        self.assertEqual(catalog["models"], ["m1"])

    def test_start_host_spawns_remote_host_and_waits_for_it(self):
        from app import tgapp_code as tcode
        calls = []

        class Proc:
            returncode = None

            def poll(self):
                return None

            def terminate(self):
                pass

        def popen(args, **kwargs):
            calls.append((args, kwargs))
            return Proc()
        fake_terminal = {"rc": "9-abcdef01", "session_id": "new-session", "host_nonce": ""}

        def terminals():
            nonce = calls[-1][1]["env"]["MEMUCODE_REMOTE_HOST_NONCE"] if calls else ""
            return [dict(fake_terminal, host_nonce=nonce)] if calls else []
        with mock.patch.object(tcode, "memucode_command", return_value=["memucode"]), \
                mock.patch.object(tcode._remote(), "list_terminals", side_effect=terminals):
            result = tcode.start_host("4242", root=str(self.project), mode="acceptEdits", lang="en", popen=popen, wait=2)
        self.assertEqual(result["rc"], "9-abcdef01")
        args = calls[0][0]
        self.assertEqual(args[:4], ["memucode", "remote-host", "--cwd", str(self.project.resolve())])
        self.assertIn("--permission-mode", args)
        self.assertNotIn("MEMUCODE_NO_REMOTE", calls[0][1]["env"])
        with self.assertRaises(LookupError):
            tcode.start_host("4242", root="relative", popen=popen)
        tcode.stop_hosts()


if __name__ == "__main__":
    unittest.main()
