"""Round 29 (r29c-media): images and video in the Telegram bot.

Real MemuAI Proxy + MemuAI window server + the offline media mock (tests/round29_media_harness.py) in one process;
the Telegram Bot API is an httpx.MockTransport that records every call (multipart uploads included).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

USER = "424242"
TOKEN = "123456:TEST-token-for-media"

class FakeTelegram:
    def __init__(self) -> None:
        self.calls = []
        self.next_id = 500

    def handler(self, request):
        import httpx
        method = request.url.path.rsplit("/", 1)[-1]
        ctype = request.headers.get("content-type", "")
        files = {}
        if "multipart" in ctype:
            body = request.read()
            for name in ("photo", "video", "document"):
                if f'name="{name}"'.encode() in body:
                    files[name] = len(body)
            payload = {}
            for part in body.split(b"--")[1:]:
                head, _, value = part.partition(b"\r\n\r\n")
                if b"filename=" in head or b'name="' not in head:
                    continue
                key = head.split(b'name="', 1)[1].split(b'"', 1)[0].decode()
                payload[key] = value.rsplit(b"\r\n", 1)[0].decode("utf-8", "replace")
        else:
            payload = json.loads(request.content or b"{}")
        self.calls.append({"method": method, "payload": payload, "files": files})
        self.next_id += 1
        result = True if method in ("sendChatAction", "deleteMessage", "answerCallbackQuery") else \
            {"message_id": self.next_id, "chat": {"id": int(USER)}, "date": int(time.time())}
        return httpx.Response(200, json={"ok": True, "result": result})

    def methods(self):
        return [c["method"] for c in self.calls]

class TelegramMediaTest(unittest.TestCase):
    servers = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.data = tempfile.mkdtemp(prefix="memu-tgmedia-")
        os.environ["MEMUAI_DATA_DIR"] = cls.data
        from tests.round29_media_harness import MediaServers
        cls.servers = MediaServers(cls.data).start(real=False)
        from tests import round29_media_mock
        cls.mock = round29_media_mock.state
        cls.mock["delay"] = 0.05
        cls.mock["polls"] = 1

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.servers:
            cls.servers.stop()

    def setUp(self) -> None:
        from app import pgdata, telegram_media_gen
        telegram_media_gen._models_cache.update(at=0.0, value=None)
        self.prefs = {**pgdata.load_preferences(), "language": "ru", "telegram_chat_id": USER,
                      "default_model": "media-chat-small", "media_image_model": "", "media_video_model": "",
                      "decision_model": "", "media_autoprompt": False, "telegram_stream_mode": "edit"}
        pgdata.save_preferences(self.prefs)
        self.mock["chat_reply"] = ""
        self.tg = FakeTelegram()

    def run_async(self, coro):
        return asyncio.run(asyncio.wait_for(coro, 120))

    def client(self):
        import httpx
        return httpx.AsyncClient(transport=httpx.MockTransport(self.tg.handler))

    def chats(self):
        from app import pgdata
        return pgdata.load_chats("default")

    def test_extract_and_prompt_block(self) -> None:
        from app import telegram_media_gen as g
        self.assertEqual(g.extract('Рисую.\n```memuai-image\n{"prompt": "a red fox"}\n```'),
                         {"kind": "image", "prompt": "a red fox"})
        self.assertEqual(g.extract("```memuai-video\n{\"prompt\": \"waves\"}\n```")["kind"], "video")
        self.assertIsNone(g.extract("no block"))
        block = g.prompt_block(True, False)
        self.assertIn("memuai-image", block)
        self.assertNotIn("```memuai-video", block)
        self.assertIn("Never claim", g.prompt_block(False, False))
        self.assertNotIn("8k,", block.replace("(8k, masterpiece)", ""))

    def test_pick_model_matches_the_desktop_rules(self) -> None:
        from app import telegram_media_gen as g
        lists = {"image": ["a", "b"], "video": ["v"]}
        self.assertEqual(g.pick_model("image", {}, lists), "a")
        self.assertEqual(g.pick_model("image", {"pinned_models": ["b"]}, lists), "b")
        self.assertEqual(g.pick_model("image", {"media_image_model": "off"}, lists), "")
        self.assertEqual(g.pick_model("image", {"media_image_model": "off"}, lists, explicit=True), "a")
        self.assertEqual(g.pick_model("image", {"media_image_model": "gone"}, lists), "")
        self.assertEqual(g.pick_model("video", {"media_video_model": "v"}, lists), "v")
        self.assertEqual(g.detect_aspect("вертикальное видео", "video"), "9:16")

    def test_models_come_from_the_proxy(self) -> None:
        from app import telegram_media_gen as g
        lists = self.run_async(g.media_models(force=True))
        self.assertIn("media-gpt-image-1", lists["image"])
        self.assertIn("media-sora-2", lists["video"])
        self.assertIn("media-chat-small", lists["text"])

    def test_image_command_sends_a_photo_and_saves_the_turn(self) -> None:
        from app import telegram_bot, telegram_media_gen as g

        async def go():
            async with self.client() as client:
                message = {"message_id": 7, "date": int(time.time()), "chat": {"id": int(USER), "type": "private"},
                           "from": {"id": int(USER)}, "text": "/image кот-астронавт"}
                await g.bot_command(client, TOKEN, USER, self.prefs, "/image", "кот-астронавт", reply_to=7, message=message)
        self.run_async(go())
        methods = self.tg.methods()
        self.assertIn("sendPhoto", methods, methods)
        photo = next(c for c in self.tg.calls if c["method"] == "sendPhoto")
        self.assertIn("photo", photo["files"])
        self.assertIn("кот-астронавт", photo["payload"].get("caption", ""))
        self.assertIn("media:again", photo["payload"].get("reply_markup", ""))

        self.assertIn("deleteMessage", methods)
        chat = next(c for c in self.chats() if any(m.get("mediaCommand") == "image" for m in c.get("messages", [])))
        answer = chat["messages"][-1]
        self.assertEqual(answer["media"]["status"], "done")
        self.assertTrue(answer["images"][0].startswith("data:image/png"))
        self.assertEqual(answer["media"]["via"], "telegram")
        self.assertNotIn(USER, telegram_bot._generations)

    def test_video_command_sends_a_video_file(self) -> None:
        from app import telegram_media_gen as g

        async def go():
            async with self.client() as client:
                await g.bot_command(client, TOKEN, USER, self.prefs, "/video", "волны на закате", reply_to=0)
        self.run_async(go())
        sent = [c for c in self.tg.calls if c["method"] in ("sendVideo", "sendDocument")]
        self.assertTrue(sent, self.tg.methods())
        self.assertIn("upload_video", json.dumps([c["payload"] for c in self.tg.calls if c["method"] == "sendChatAction"]))
        chat = next(c for c in self.chats() if any(m.get("mediaCommand") == "video" for m in c.get("messages", [])))
        url = chat["messages"][-1]["videos"][0]
        self.assertIn("/api/pg/media/file/", url)

    def test_empty_command_explains_the_usage(self) -> None:
        from app import telegram_media_gen as g

        async def go():
            async with self.client() as client:
                await g.bot_command(client, TOKEN, USER, self.prefs, "/image", "")
        self.run_async(go())
        self.assertEqual(self.tg.methods(), ["sendMessage"])
        self.assertIn("/image", self.tg.calls[0]["payload"]["text"])

    def test_generation_error_is_honest_with_retry(self) -> None:
        from app import telegram_media_gen as g

        async def go():
            async with self.client() as client:
                await g.bot_command(client, TOKEN, USER, self.prefs, "/image", "FAIL please")
        self.run_async(go())
        last = [c for c in self.tg.calls if c["method"] == "sendMessage"][-1]
        self.assertIn("⚠️", last["payload"]["text"])
        self.assertIn("media:again", json.dumps(last["payload"].get("reply_markup")))
        self.assertNotIn("sendPhoto", self.tg.methods())

    def test_another_one_repeats_the_last_request(self) -> None:

        from app import pgdata, telegram_media_gen as g

        async def go():
            async with self.client() as client:
                await g.bot_command(client, TOKEN, USER, self.prefs, "/image", "лиса в лесу")
                await g.bot_again(client, TOKEN, USER, self.prefs)
        self.run_async(go())
        self.assertEqual(self.tg.methods().count("sendPhoto"), 2, self.tg.methods())
        texts = " ".join(str(c["payload"].get("text") or "") for c in self.tg.calls)
        self.assertNotIn("Нечего повторить", texts)
        last = pgdata.load_telegram_state()["sessions"][str(USER)]["last_media"]
        self.assertEqual((last["kind"], last["request"]), ("image", "лиса в лесу"))

    def test_missing_model_texts(self) -> None:
        from app import telegram_media_gen as g
        lists = {"image": [], "video": ["v"]}
        self.assertIn("missing-model", g.missing_text("video", {"media_video_model": "missing-model"}, lists, "en"))
        self.assertIn("Genspark", g.missing_text("image", {}, lists, "en"))
        self.assertNotRegex(g.missing_text("image", {}, lists, "en"), "[а-яА-Я]")

        self.assertEqual(g.pick_model("video", {"media_video_model": "missing-model"}, lists, explicit=True), "v")

    def test_main_model_hidden_block_generates_after_the_answer(self) -> None:
        from app import telegram_bot
        self.mock["chat_reply"] = 'Рисую лису.\n```memuai-image\n{"prompt": "a red fox in snow"}\n```'
        telegram_bot._temp_history.clear()

        async def go():
            async with self.client() as client:
                await telegram_bot._new_chat(client, TOKEN, USER, self.prefs)
                await telegram_bot._answer_ai(client, TOKEN, USER, "нарисуй лису", self.prefs, reply_to=3)
        self.run_async(go())
        methods = self.tg.methods()
        self.assertIn("sendPhoto", methods, methods)
        texts = " ".join(str(c["payload"].get("text", "")) for c in self.tg.calls)
        self.assertIn("Рисую лису", texts)
        self.assertNotIn("memuai-image", texts)
        photo = next(c for c in self.tg.calls if c["method"] == "sendPhoto")
        self.assertIn("a red fox in snow", photo["payload"]["caption"])

        chat = next(c for c in self.chats() if any("лису" in str(m.get("content")) for m in c.get("messages", [])))
        answer = chat["messages"][-1]
        self.assertEqual(answer["media"]["source"], "model")
        self.assertTrue(answer["images"])

        systems = [m.get("content") for body in self.mock["chats"] for m in body.get("messages", []) if m.get("role") == "system"]
        self.assertTrue(any("<media_generation>" in str(text) for text in systems), systems[-1:] if systems else self.mock["chats"][-1:])

    def test_decisions_model_routes_before_the_answer(self) -> None:
        from app import pgdata, telegram_bot
        prefs = dict(self.prefs, decision_model="media-chat-small")
        pgdata.save_preferences(prefs)
        before = len(self.mock["chats"])

        async def go():
            async with self.client() as client:
                await telegram_bot._answer_ai(client, TOKEN, USER, "нарисуй горный пейзаж акварелью", prefs, reply_to=4)
        self.run_async(go())
        self.assertIn("sendPhoto", self.tg.methods())

        self.assertEqual(len(self.mock["chats"]), before)

    def test_plain_question_is_not_routed(self) -> None:
        from app import pgdata, telegram_bot
        prefs = dict(self.prefs, decision_model="media-chat-small")
        pgdata.save_preferences(prefs)

        async def go():
            async with self.client() as client:
                await telegram_bot._answer_ai(client, TOKEN, USER, "сколько будет 2+2?", prefs, reply_to=5)
        self.run_async(go())
        self.assertNotIn("sendPhoto", self.tg.methods())

    def _app_send(self, text, **kw):
        from app import tgapp_chat, telegram_media_gen

        async def go():
            await telegram_media_gen.media_models(force=True)
            job = tgapp_chat.start_send(owner="u1", profile_id="default", chat_id="", text=text, attachments=[],
                                        model="media-chat-small", web_search=False, thinking=False, temp=False,
                                        meme_id="", regenerate=False, temp_history=None, lang="ru")
            await asyncio.wait_for(job.task, 90)
            for _ in range(100):
                if job.done:
                    break
                await asyncio.sleep(0.05)
            return job
        return self.run_async(go())

    def test_mini_app_image_command(self) -> None:
        job = self._app_send("/image маяк на скале")
        kinds = [e["type"] for e in job.events]
        self.assertIn("media", kinds)
        message = next(e["message"] for e in job.events if e["type"] == "message")
        self.assertTrue(message["images"][0].startswith("data:image/png"))
        self.assertEqual(message["media"]["kind"], "image")
        self.assertNotIn("error", kinds)
        chat = next(c for c in self.chats() if c["id"] == job.meta["chat_id"])
        self.assertEqual(chat["messages"][0].get("mediaCommand"), "image")
        self.assertTrue(chat["messages"][-1]["images"])

    def test_mini_app_video_is_served_through_media_file(self) -> None:
        from app import tgapp_api, telegram_media_gen
        job = self._app_send("/video облака над горами")
        message = next(e["message"] for e in job.events if e["type"] == "message")
        url = message["videos"][0]
        self.assertIn("/api/pg/media/file/", url)
        data = self.run_async(telegram_media_gen.app_media_file(url, "default"))
        self.assertTrue(data["data"].startswith("data:video/"))
        with self.assertRaises(Exception):
            self.run_async(telegram_media_gen.app_media_file("https://example.com/x.mp4", "default"))
        chat = next(c for c in self.chats() if c["id"] == job.meta["chat_id"])
        out = tgapp_api._message_out(chat["messages"][-1], 1)
        self.assertEqual(out["videos"], [url])
        self.assertEqual(out["media"]["kind"], "video")

    def test_mini_app_hidden_block(self) -> None:
        self.mock["chat_reply"] = 'Сейчас нарисую.\n```memuai-image\n{"prompt": "a lighthouse at night"}\n```'
        job = self._app_send("нарисуй маяк ночью")
        message = next(e["message"] for e in job.events if e["type"] == "message")
        self.assertEqual(message["content"], "Сейчас нарисую.")
        self.assertEqual(message["media"]["prompt"], "a lighthouse at night")
        self.assertTrue(message["images"])
        systems = [m.get("content") for body in self.mock["chats"] for m in body.get("messages", []) if m.get("role") == "system"]
        self.assertTrue(any("<media_generation>" in str(t) for t in systems))
        self.assertFalse(any("image generation are not available" in str(t) for t in systems))

    def test_mini_app_client_renders_media(self) -> None:
        source = (ROOT / "app" / "tgapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn("function mediaHtml(m)", source)
        self.assertIn('api("media.file"', source)
        self.assertIn('case "media":', source)
        self.assertIn("memuai-(?:image|video)", source)
        css = (ROOT / "app" / "tgapp" / "app.css").read_text(encoding="utf-8")
        self.assertIn(".gen-video", css)

    def test_media_router_internal_prompt_is_used(self) -> None:
        from app import media_routing, pgdata
        self.assertEqual(media_routing.DECISION_SYSTEM, pgdata.MEDIA_ROUTER_PROMPT)
        row = next(r for r in pgdata.INTERNAL_PROMPTS if r["id"] == "media-router")
        self.assertEqual(row["text"], pgdata.MEDIA_ROUTER_PROMPT)
        self.assertFalse(any(r["id"] == "image-generation-router" for r in pgdata.INTERNAL_PROMPTS))
        self.assertIn("decision_system()", (ROOT / "app" / "media_routing.py").read_text(encoding="utf-8"))

    def test_video_placeholder_in_the_composer(self) -> None:
        import subprocess
        source = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        start = source.index("function imaginePlaceholder()")
        body = source[start:source.index("\n}\n", start) + 3]
        script = ("const S={videoModels:['v1']}; let cur='v1'; const currentModel=()=>cur; "
                  "const memeText=(ru,en)=>en;\n" + body +
                  "\nconsole.log(imaginePlaceholder()); cur='img'; console.log(imaginePlaceholder());")
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30).stdout.split()
        self.assertEqual(" ".join(out), "Describe a video… Describe an image…")
        self.assertNotIn('memeText("Опишите изображение…", "Describe an image…") : pt("message")', source)

    def test_generated_media_is_backed_up_with_chats(self) -> None:
        from app import gdrive_sync
        self.assertEqual(gdrive_sync.classify("memuai/generated_media/" + "a" * 64 + ".mp4"), "chats")
        self.assertEqual(gdrive_sync.classify("memuai/profiles/work/generated_media/" + "b" * 64 + ".mp4"), "chats")

    def test_commands_and_callbacks_are_wired(self) -> None:
        from app import telegram_bot
        names = [c["command"] for c in telegram_bot._commands("en")]
        self.assertIn("image", names)
        self.assertIn("video", names)
        source = (ROOT / "app" / "telegram_bot.py").read_text(encoding="utf-8")
        self.assertIn('data == "media:again"', source)
        self.assertIn("telegram_media_gen.COMMANDS", source)
        self.assertIn("/image", telegram_bot._help_text({"language": "en"}))

if __name__ == "__main__":
    unittest.main()
