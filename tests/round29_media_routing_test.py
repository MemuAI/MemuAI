"""Round 29 (r29b-media-routing): text / image / video routing in the chat.

Offline: heuristics on ~70 RU/EN messages, decision parsing, prompt composition, error texts, preferences,
Custom media detection, and the full path MemuAI window server → MemuAI Proxy → OpenAI-compatible media mock
(images API, chat image models, /videos jobs, Google AI Studio Veo / Gemini image shapes), file storage and serving.

Live (skipped without keys): the Decisions call with the fast Yandex model (latency + accuracy).
"""
from __future__ import annotations

import json
import os
import re
import statistics
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# (message, expected): "maybe" = must not be decided confidently by the heuristic.
CASES = [
    ("нарисуй кота в космосе", "image"), ("нарисуй таблицу расходов за месяц", "text"),
    ("покажи как выглядит Шрек", "image"), ("explain image generation", "text"),
    ("draw a cute fox in watercolor", "image"), ("сгенерируй логотип для кофейни", "image"),
    ("сделай короткое видео с волнами на закате", "video"), ("make a 5 second clip of a rocket launch", "video"),
    ("как работает генерация видео?", "text"), ("найди видео про ремонт ноутбука на ютубе", "text"),
    ("что изображено на этой картинке?", "text"), ("нарисуй график продаж по месяцам", "text"),
    ("draw a flowchart of the login process", "text"), ("напиши функцию на python для сортировки", "text"),
    ("привет, как дела?", "text"), ("сколько будет 2+2", "text"),
    ("create a wallpaper with mountains at night", "image"), ("хочу аватарку в стиле аниме", "image"),
    ("show me what a quokka looks like", "image"), ("анимируй этот логотип", "video"),
    ("сними ролик, как котёнок играет с клубком", "video"), ("опиши фото подробно", "text"),
    ("посоветуй нейросеть для генерации картинок", "text"), ("какие модели видео лучше: sora или veo?", "text"),
    ("напиши промпт для midjourney про дракона", "text"), ("сделай мем про понедельник", "image"),
    ("переведи текст на английский", "text"), ("изобрази схему солнечной системы", "text"),
    ("generate an image of a cyberpunk city", "image"), ("I need a poster for a jazz concert", "image"),
    ("design an icon for my app, minimal, blue", "image"), ("how do I draw a horse step by step?", "text"),
    ("сделай презентацию про Python", "text"), ("нарисуй его ещё раз, но ночью", "image"),
    ("визуализируй данные из таблицы", "text"), ("summarize this video for me", "text"),
    ("покажи фото Эйфелевой башни", "image"), ("придумай название для кафе", "text"),
    ("draw ascii art of a cat", "text"), ("render a 3d product shot of a perfume bottle", "image"),
    ("animate a bouncing ball", "video"), ("напиши сценарий для видео на ютуб", "text"),
    ("придумай идею для логотипа кофейни", "text"), ("сделай таблицу с ценами на фото-оборудование", "text"),
    ("покажи пример письма", "text"), ("можешь показать мне кота в шляпе?", "maybe"),
    ("а как выглядит тессеракт?", "maybe"), ("could you visualize a futuristic car?", "maybe"),
    ("напиши эссе про импрессионизм", "text"), ("сделай иллюстрацию к сказке про колобка", "image"),
]


class HeuristicTest(unittest.TestCase):
    def test_cases(self):
        from app.media_routing import heuristic
        wrong = []
        for text, want in CASES:
            kind, confident = heuristic(text)
            if want == "maybe":
                if confident:
                    wrong.append((text, kind, "confident"))
            elif confident and kind != want:
                wrong.append((text, kind, want))
        self.assertEqual(wrong, [])
        confident_share = sum(1 for t, w in CASES if heuristic(t)[1]) / len(CASES)
        self.assertGreater(confident_share, 0.8, "most messages must not need a model call")

    def test_follow_up_after_media_is_ambiguous(self):
        from app.media_routing import heuristic
        self.assertEqual(heuristic("а теперь сделай его синим")[0], "text")
        self.assertEqual(heuristic("а теперь сделай его синим", after_media=True), ("maybe", False))
        self.assertEqual(heuristic("make it night", after_media=True), ("maybe", False))
        self.assertEqual(heuristic("спасибо, круто!", after_media=True), ("text", True))
        self.assertEqual(heuristic("а что это за порода?", after_media=True), ("text", True))

    def test_commands(self):
        from app.media_routing import heuristic
        self.assertEqual(heuristic("/image кот"), ("image", True))
        self.assertEqual(heuristic("/video море"), ("video", True))
        self.assertEqual(heuristic(""), ("text", True))

    def test_parse_decision(self):
        from app.media_routing import parse_decision
        self.assertEqual(parse_decision("image"), "image")
        self.assertEqual(parse_decision(" Video."), "video")
        self.assertEqual(parse_decision("<think>maybe image or text</think>text"), "text")
        self.assertEqual(parse_decision("видео"), "video")
        self.assertEqual(parse_decision("hmm"), "text")


class PromptTest(unittest.TestCase):
    def test_generation_prompt(self):
        from app.media_routing import generation_prompt
        rows = [{"role": "user", "text": "расскажи про лис"}, {"role": "assistant", "text": "Лисы — хищники."}]
        self.assertEqual(generation_prompt("image", "нарисуй лису", rows=rows), "нарисуй лису")
        follow = generation_prompt("image", "а теперь сделай его синим", rows=rows, previous_prompt="a red cat on a roof")
        self.assertTrue(follow.startswith("a red cat on a roof"))
        self.assertIn("а теперь сделай его синим", follow)
        ctx = generation_prompt("image", "нарисуй это", rows=rows)
        self.assertIn("Context from the conversation", ctx)
        self.assertEqual(generation_prompt("image", "A detailed prompt.", rows=rows, previous_prompt="x", from_model=True),
                         "A detailed prompt.")

    def test_no_quality_boilerplate(self):
        from app import media_routing
        for name in ("DECISION_SYSTEM", "GENERATION_SYSTEM", "AUTOPROMPT_SEARCH_RULE", "AUTOPROMPT_RESULTS"):
            text = getattr(media_routing, name).lower()
            for word in ("8k", "masterpiece", "ultra detailed", "best quality"):
                self.assertNotIn(word, text, name)
        js = (ROOT / "app/pgstatic/media-routing.js").read_text(encoding="utf-8")
        block = js.split("PROMPT (r29b-media-routing)")[1].split("/PROMPT (r29b-media-routing)")[0]
        self.assertIn("memuai-image", block)
        self.assertIn("memuai-video", block)
        self.assertLess(len(media_routing.DECISION_SYSTEM), 900, "the decision prompt must stay tiny")

    def test_friendly_error(self):
        from app.media_routing import friendly_error
        raw = 'Upstream error (HTTP 400): {"error":{"message":"Rejected by the safety system."}}'
        self.assertEqual(friendly_error(raw, "en"), "Upstream error (HTTP 400): Rejected by the safety system.")
        self.assertEqual(friendly_error(raw, "ru"), "Ошибка сервиса (HTTP 400): Rejected by the safety system.")


class MediaGenNamesTest(unittest.TestCase):
    def test_names(self):
        from app import media_gen as g
        for name in ("sora-2", "veo-3.1-generate-preview", "kling-v2", "wan2.2-t2v", "minimax-video-01", "hailuo-02"):
            self.assertTrue(g.is_video_name(name), name)
        for name in ("qwen3-vl-plus", "video-llava-7b-chat", "gpt-5", "glm-4.6v", "qwen3-omni-video-understanding"):
            self.assertFalse(g.is_video_name(name), name)
        for name in ("gpt-image-1", "flux-2-pro", "gemini-2.5-flash-image", "imagen-4.0-generate-001", "nano-banana-pro"):
            self.assertTrue(g.is_image_name(name), name)
        for name in ("qwen3-vl-plus", "gpt-5", "deepseek-v4", "sora-2"):
            self.assertFalse(g.is_image_name(name), name)
        self.assertTrue(g.is_chat_image_model("google/gemini-2.5-flash-image-preview"))
        self.assertFalse(g.is_chat_image_model("gpt-image-1"))

    def test_custom_media_ids(self):
        from app import media_gen as g

        class Entry:
            def __init__(self, model):
                self.provider, self.model, self.id = "custom", model, "m-" + model

        class Acc:
            fields = {"name": "M", "image_models": "my-painter", "video_models": "clipper"}
        entries = [Entry(x) for x in ("chat", "my-painter", "clipper", "sora-2", "flux-pro", "gpt-image-1")]
        images, videos = g.custom_media_ids(entries, [Acc()])
        self.assertEqual(set(images), {"m-my-painter", "m-flux-pro", "m-gpt-image-1"})
        self.assertEqual(set(videos), {"m-clipper", "m-sora-2"})


class PreferencesTest(unittest.TestCase):
    def test_normalization(self):
        from app import pgdata
        normalize = getattr(pgdata, "normalize_preferences", None) or getattr(pgdata, "_normalize_preferences", None)
        out = normalize({"media_image_model": "  x  ", "media_video_model": None, "decision_model": 5,
                                            "media_autoprompt": "true", "media_autoprompt_search": False}) \
            if normalize else None
        if out is None:
            self.skipTest("no normalize_preferences")
        self.assertEqual(out["media_image_model"], "x")
        self.assertEqual(out["media_video_model"], "")
        self.assertEqual(out["decision_model"], "5")
        self.assertIs(out["media_autoprompt"], True)
        self.assertIs(out["media_autoprompt_search"], False)
        defaults = pgdata.DEFAULT_PREFERENCES
        for key in ("media_image_model", "media_video_model", "decision_model", "media_autoprompt", "media_autoprompt_search"):
            self.assertIn(key, defaults)


class EndToEndTest(unittest.TestCase):
    """MemuAI window server → Proxy → media mock, everything in this process."""

    @classmethod
    def setUpClass(cls):
        from tests.round29_media_harness import MediaServers
        from app import media_gen
        cls._poll = media_gen.VIDEO_POLL_SECONDS
        media_gen.VIDEO_POLL_SECONDS = 0.2
        cls.servers = MediaServers(tempfile.mkdtemp(prefix="memu-media-test-")).start(real=False)
        import httpx
        cls.client = httpx.Client(timeout=120, trust_env=False)

    @classmethod
    def tearDownClass(cls):
        from app import media_gen
        media_gen.VIDEO_POLL_SECONDS = cls._poll
        cls.client.close()
        cls.servers.stop()

    def gen(self, **body):
        events = []
        with self.client.stream("POST", self.servers.pg + "/api/pg/media/generate", json=body) as resp:
            self.assertEqual(resp.status_code, 200)
            for line in resp.iter_lines():
                if line.startswith("data: ") and line[6:] != "[DONE]":
                    item = json.loads(line[6:])
                    if item.get("status") != "generating":
                        events.append(item)
        return events

    def test_state_lists_generators(self):
        state = self.client.get(self.servers.pg + "/api/pg/state").json()
        self.assertEqual(set(state["image_models"]), {"media-gpt-image-1", "media-gemini-3-flash-image"})
        self.assertEqual(state["video_models"], ["media-sora-2"])
        rows = {row["id"]: row for row in state["models"]}
        self.assertTrue(rows["media-sora-2"]["caps"]["video"])
        self.assertIn("media-chat-small", rows)

    def test_images_api(self):
        events = self.gen(kind="image", model="media-gpt-image-1", prompt="a cat")
        self.assertEqual(events[0], {"status": "prompt", "prompt": "a cat"})
        done = [e for e in events if e.get("status") == "done"]
        self.assertTrue(done and done[0]["urls"][0].startswith("data:image/png;base64,"))

    def test_chat_image_model_gets_system_context_and_reference(self):
        from tests import round29_media_mock as mock
        ref = "data:image/png;base64," + __import__("base64").b64encode(mock.png_bytes()).decode()
        events = self.gen(kind="image", model="media-gemini-3-flash-image", prompt="make it blue", images=[ref],
                          context=[{"role": "user", "text": "draw a red bird"}])
        self.assertTrue(any(e.get("status") == "done" for e in events), events)
        body = mock.state["chats"][-1]
        self.assertEqual(body["modalities"], ["image", "text"])
        self.assertIn("MemuAI", body["messages"][0]["content"])
        self.assertIn("draw a red bird", body["messages"][0]["content"])
        self.assertEqual(body["messages"][1]["content"][1]["type"], "image_url")

    def test_follow_up_prompt_is_composed(self):
        events = self.gen(kind="image", model="media-gpt-image-1", request="а теперь синим", previous_prompt="a red cat")
        self.assertIn("a red cat", events[0]["prompt"])
        self.assertIn("а теперь синим", events[0]["prompt"])

    def test_honest_errors(self):
        events = self.gen(kind="image", model="media-gpt-image-1", prompt="FAIL", lang="ru")
        err = [e for e in events if e.get("status") == "error"]
        self.assertTrue(err)
        self.assertIn("Rejected", err[0]["error"].replace("rejected", "Rejected"))
        self.assertNotIn('{"error"', err[0]["error"])
        resp = self.client.post(self.servers.pg + "/api/pg/media/generate", json={"kind": "image", "model": "", "prompt": "x", "lang": "en"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("No generation model", resp.json()["error"])

    def test_video_job_stored_and_served(self):
        events = self.gen(kind="video", model="media-sora-2", prompt="waves", aspect="9:16")
        done = [e for e in events if e.get("status") == "done"]
        self.assertTrue(done, events)
        url = done[0]["urls"][0]
        self.assertRegex(url, r"^/api/pg/media/file/[0-9a-f]{64}\.webm\?profile_id=")
        resp = self.client.get(self.servers.pg + url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["content-type"], "video/webm")
        self.assertTrue(resp.content.startswith(b"\x1aE\xdf\xa3"))
        from tests import round29_media_mock as mock
        self.assertEqual(mock.state["video_bodies"][-1]["size"], "720x1280")
        # foreign profile / bad names are refused
        self.assertEqual(self.client.get(self.servers.pg + url.split("?")[0] + "?profile_id=other").status_code, 403)
        self.assertEqual(self.client.get(self.servers.pg + "/api/pg/media/file/../../x.mp4").status_code, 404)

    def test_video_failure(self):
        events = self.gen(kind="video", model="media-sora-2", prompt="FAIL", lang="en")
        self.assertTrue(any(e.get("status") == "error" and "Content policy" in e["error"] for e in events), events)

    def test_google_ai_studio_shapes(self):
        from app import media_gen
        original = media_gen._google_root
        media_gen._google_root = lambda base: self.servers.mock
        try:
            events = self.gen(kind="image", model="media-gemini-3-flash-image", prompt="a leaf")
            self.assertTrue(any(e.get("status") == "done" for e in events), events)
            events = self.gen(kind="video", model="media-sora-2", prompt="a river", aspect="16:9")
            done = [e for e in events if e.get("status") == "done"]
            self.assertTrue(done, events)
            from tests import round29_media_mock as mock
            veo = mock.state["video_bodies"][-1]
            self.assertEqual(veo["parameters"]["aspectRatio"], "16:9")
            self.assertEqual(veo["key"], "sk-media")
        finally:
            media_gen._google_root = original

    def test_decide_without_model_is_heuristic(self):
        r = self.client.post(self.servers.pg + "/api/pg/media/decide", json={"text": "нарисуй лису"}).json()
        self.assertEqual((r["kind"], r["source"]), ("image", "heuristic"))
        r = self.client.post(self.servers.pg + "/api/pg/media/decide", json={"text": "нарисуй таблицу"}).json()
        self.assertEqual(r["kind"], "text")
        r = self.client.post(self.servers.pg + "/api/pg/media/decide", json={"text": "сделай видео", "video": False}).json()
        self.assertEqual(r.get("unavailable"), "video")

    def test_decide_with_model_falls_back_honestly(self):
        # The mock chat model answers «text answer» → text; an unknown model → an error and the heuristic verdict.
        r = self.client.post(self.servers.pg + "/api/pg/media/decide",
                             json={"text": "можешь показать мне кота в шляпе?", "model": "media-chat-small"}).json()
        self.assertEqual((r["kind"], r["source"]), ("text", "model"))
        r = self.client.post(self.servers.pg + "/api/pg/media/decide",
                             json={"text": "можешь показать мне кота?", "model": "nope-model", "timeout": 10}).json()
        self.assertEqual(r["source"], "heuristic")
        self.assertTrue(r.get("error"))

    def test_save_to_downloads(self):
        events = self.gen(kind="video", model="media-sora-2", prompt="snow")
        url = [e for e in events if e.get("status") == "done"][0]["urls"][0]
        home = tempfile.mkdtemp(prefix="memu-home-")
        old = os.environ.get("HOME")
        os.environ["HOME"] = home
        try:
            r = self.client.post(self.servers.pg + "/api/pg/media/save", json={"url": url, "name": "clip"}).json()
            self.assertTrue(r.get("ok"), r)
            self.assertTrue(r["path"].startswith(home))
            self.assertTrue(r["name"].endswith(".webm"))
            bad = self.client.post(self.servers.pg + "/api/pg/media/save", json={"url": "/etc/passwd"})
            self.assertEqual(bad.status_code, 404)
        finally:
            if old is not None:
                os.environ["HOME"] = old


@unittest.skipUnless(os.environ.get("YANDEX_BASE") and os.environ.get("YANDEX_KEY"), "live: needs YANDEX_* keys")
class LiveDecisionTest(unittest.TestCase):
    def test_yandex_decision_speed_and_accuracy(self):
        from tests.round29_media_harness import MediaServers
        servers = MediaServers(tempfile.mkdtemp(prefix="memu-media-live-")).start()
        import httpx
        try:
            with httpx.Client(timeout=120, trust_env=False) as client:
                ids = [m["id"] for m in client.get(servers.pg + "/api/pg/state").json()["models"]]
                model = next(i for i in ids if i.startswith("ya-"))
                sample = [("можешь показать мне кота в шляпе?", "image"), ("нарисуй таблицу расходов", "text"),
                          ("сделай короткий ролик с волнами", "video"), ("explain image generation", "text"),
                          ("покажи, как выглядит Шрек", "image"), ("сделай гифку с танцующим котом", "video")]
                times, wrong = [], []
                for text, want in sample:
                    r = client.post(servers.pg + "/api/pg/media/decide", json={"text": text, "model": model, "force": True}).json()
                    times.append(r["ms"])
                    if r["kind"] != want:
                        wrong.append((text, r))
                self.assertLessEqual(len(wrong), 1, wrong)
                self.assertLess(statistics.median(times), 3000)
        finally:
            servers.stop()


if __name__ == "__main__":
    unittest.main()
