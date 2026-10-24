"""Round 41b (r41b-gemini): Gemini Web — attachments in (upload → f.req), generated images / Veo video out (download with
the account cookies → /v1/geminiweb/media), /v1/images/generations, account limits (GetUsageInfo / CheckGeminiQuota /
CheckQuota), «limit reached» → 429 with the reset time, Thinking level on the wire. No network: every Google answer is a
recorded-format fixture (batchexecute / StreamGenerate length-prefixed frames, field positions of the public web client)."""
import asyncio
import base64
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.providers import geminiweb_limits as limits
from app.providers import geminiweb_media as media
from app.providers import geminiweb_proto as proto
from app.providers.base import ProviderError, ProviderRateLimitError
from tests.round41_gemini_web_test import (PAGE, FakeHeaders, account, answer_frames, batch_response, inner_of,
                                           make_provider, paid_status)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x01" * 64
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x02" * 64
PDF = b"%PDF-1.7\n" + b"x" * 32
IMG_URL = "https://lh3.googleusercontent.com/gg-dl/AJfQ9KfixtureImage=s1024-rj"
VID_URL = "https://contribution.usercontent.google.com/download?c=fixtureVideo"
THUMB_URL = "https://lh3.googleusercontent.com/fixtureThumb"


def b64(data):
    return base64.b64encode(data).decode()


def media_candidate(text, *, images=(), video=None, sparse=False):
    """Candidate with c[12] rich content: field 7 = generated images, field 59 = Veo video (positional or sparse)."""
    cand = [None] * 38
    cand[0] = "rc_media"
    cand[1] = [text]
    cand[8] = [2]
    gen = [[[[None, None, None, [None, None, f"alt {i}", url]], [f"img_id_{i}"]] for i, url in enumerate(images)]]
    vid = [[[[[None] * 7 + [list(video)]]]]] if video else None
    if sparse:
        rich = [None, None, {"8": gen, "60": vid} if vid else {"8": gen}]
    else:
        rich = [None] * 60
        rich[7] = gen if images else None
        rich[59] = vid
    cand[12] = rich
    return cand


def stream_of(cand, cid="c_media", rid="r_media"):
    inner = [None, [cid, rid], None, None, [cand]]
    body = json.dumps([["wrb.fr", None, json.dumps(inner)]])
    return f")]}}'\n\n{len(body) + 1}\n{body}\n"


class Resp:
    def __init__(self, status=200, text="", content=b"", headers=None):
        self.status_code, self.text, self.content = status, text, content
        self.url = "https://gemini.google.com/app"
        self.headers = FakeHeaders(headers or {})
        self._lines = text.split("\n")

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aclose(self):
        pass


class MediaSession:
    """/app page, batchexecute per rpcid, upload, StreamGenerate answers, media downloads by URL."""

    def __init__(self, answers, *, batch=None, downloads=None, upload=None):
        self.answers, self.batch, self.downloads = list(answers), dict(batch or {}), dict(downloads or {})
        self.upload = upload or (lambda url, kw: Resp(text="/contrib_service/ttl_1d/fixture_ref"))
        self.calls = []
        self.cookies = SimpleNamespace(clear=lambda: None)

    async def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        if "gemini.google.com" in url:
            return Resp(text=PAGE)
        answer = self.downloads.get(url)
        if callable(answer):
            return answer()
        return answer or Resp(status=404)

    async def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        if "content-push.googleapis.com" in url or url.startswith("https://upload.fixture"):
            return self.upload(url, kw)
        if "batchexecute" in url:
            for rpcid, text in self.batch.items():
                if f"rpcids={rpcid}" in url:
                    return Resp(text=text(kw) if callable(text) else text)
            if f"rpcids={proto.RPC_USER_STATUS}" in url:
                return Resp(text=paid_status())
            return Resp(text=batch_response(proto.RPC_DELETE_CHAT, []))
        return Resp(text=self.answers.pop(0))

    async def close(self):
        pass

    def generate_calls(self):
        return [c for c in self.calls if c[0] == "POST" and "StreamGenerate" in c[1]]


async def chat(provider, acc, model, messages, params=None):
    events = []
    with patch("app.providers.geminiweb.GeminiWebProvider._seed_catalog", staticmethod(lambda status: None)), \
            patch("app.providers.geminiweb.asyncio.sleep", return_value=None):
        async for event in provider.chat_stream(acc, model, messages, params or {}):
            events.append(event)
    return events


def text_of(events):
    return "".join(e["text"] for e in events if e["type"] == "text")


class TempMediaDir(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch_dir = patch.object(media, "MEDIA_DIR", Path(self.tmp.name) / "geminiweb_media")
        self.patch_dir.start()

    def tearDown(self):
        self.patch_dir.stop()
        self.tmp.cleanup()


# ------------------------------------------------------------------ input


class AttachmentTest(TempMediaDir):
    async def test_every_attachment_shape(self):
        messages = [{"role": "user", "content": "old", }, {"role": "assistant", "content": "ok"}, {"role": "user", "content": [
            {"type": "text", "text": "Что на фото и в файле?"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64(PNG)}"}},
            {"type": "input_image", "image_url": "https://example.com/pic.jpg"},
            {"type": "file", "file": {"file_data": f"data:application/pdf;base64,{b64(PDF)}", "filename": "report.pdf"}},
            {"type": "input_file", "file_data": b64(PDF), "filename": "notes.pdf"},
            {"type": "video_url", "video_url": {"url": f"data:video/mp4;base64,{b64(MP4)}"}},
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64(JPG)}},
        ]}]
        fetched = []

        async def fetch(url):
            fetched.append(url)
            return JPG
        items = await media.load_attachments(messages, fetch)
        self.assertEqual(fetched, ["https://example.com/pic.jpg"])
        self.assertEqual([(i.mime, i.name) for i in items], [
            ("image/png", "image1.png"), ("image/jpeg", "pic.jpg"), ("application/pdf", "report.pdf"),
            ("application/pdf", "notes.pdf"), ("video/mp4", "video5.mp4"), ("image/jpeg", "image6.jpg")])
        self.assertEqual(items[0].data, PNG)

    async def test_bad_base64_and_unsafe_url_are_clear_errors(self):
        with self.assertRaises(media.AttachmentError):
            media.decode_source("data:image/png;base64,")
        with self.assertRaises(media.AttachmentError):   # SSRF guard of media_cache: private addresses refused
            await media.load_attachments([{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "http://127.0.0.1:8317/secret.png"}}]}], media.fetch_public)

    async def test_upload_multipart_and_freq_refs(self):
        session = MediaSession([answer_frames("Это кот.")])
        acc = account(delete_chats="false")
        messages = [{"role": "user", "content": [
            {"type": "text", "text": "Что тут?"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64(PNG)}"}},
            {"type": "file", "file": {"file_data": f"data:application/pdf;base64,{b64(PDF)}", "filename": "a.pdf"}}]}]
        events = await chat(make_provider(session), acc, "gemini-3.8-flash", messages)
        self.assertEqual(text_of(events), "Это кот.")
        uploads = [c for c in session.calls if "content-push" in c[1]]
        self.assertEqual(len(uploads), 2)
        self.assertIn("multipart", uploads[0][2])
        self.assertEqual(uploads[0][2]["headers"]["X-Tenant-Id"], "bard-storage")
        self.assertEqual(uploads[0][2]["headers"]["Push-ID"], proto.DEFAULT_PUSH_ID)
        inner = inner_of(session.generate_calls()[0])
        self.assertEqual(inner[0][3], [[["/contrib_service/ttl_1d/fixture_ref"], "image1.png"],
                                       [["/contrib_service/ttl_1d/fixture_ref"], "a.pdf"]])
        self.assertIn("Что тут?", inner[0][0])

    async def test_upload_falls_back_to_resumable(self):
        def upload(url, kw):
            if "multipart" in kw:
                return Resp(status=500)
            if kw["headers"].get("X-Goog-Upload-Command") == "start":
                return Resp(headers={"x-goog-upload-url": "https://upload.fixture/session1"})
            return Resp(text="/contrib_service/ttl_1d/resumable_ref")
        session = MediaSession([answer_frames("ok")], upload=upload)
        messages = [{"role": "user", "content": [{"type": "text", "text": "?"},
                                                 {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64(PNG)}"}}]}]
        await chat(make_provider(session), account(delete_chats="false"), "gemini-3.8-flash", messages)
        self.assertEqual(inner_of(session.generate_calls()[0])[0][3][0][0], ["/contrib_service/ttl_1d/resumable_ref"])

    async def test_upload_refused_is_readable(self):
        session = MediaSession([answer_frames("ok")], upload=lambda url, kw: Resp(status=413))
        messages = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64(PNG)}"}}]}]
        with self.assertRaises(ProviderError) as ctx:
            await chat(make_provider(session), account(delete_chats="false"), "gemini-3.8-flash", messages)
        self.assertIn("HTTP 413", str(ctx.exception))
        self.assertIn("не принял загрузку", ctx.exception.message_ru)


# ------------------------------------------------------------------ output


class GeneratedMediaTest(TempMediaDir):
    def test_rich_media_positional_and_sparse(self):
        for sparse in (False, True):
            cand = media_candidate("Вот", images=[IMG_URL], video=(THUMB_URL, VID_URL), sparse=sparse)
            snap = proto.parse_part(json.loads(stream_of(cand).split("\n", 3)[3])[0])
            self.assertEqual([(m.kind, m.url) for m in snap.media], [("image", IMG_URL), ("video", VID_URL)], sparse)
            self.assertEqual(snap.media[0].alt, "alt 0")
            self.assertEqual(snap.media[1].thumb, THUMB_URL)
            self.assertEqual((snap.cid, snap.rid), ("c_media", "r_media"))
            self.assertEqual(snap.images, [])          # generated ≠ web images

    def test_full_size_suffix(self):
        self.assertTrue(media.full_size(IMG_URL).endswith("=s2048-rj"))
        self.assertEqual(media.full_size("https://lh3.googleusercontent.com/x"), "https://lh3.googleusercontent.com/x=s2048-rj")

    async def test_generated_image_is_downloaded_and_served_by_proxy(self):
        full = media.full_size(IMG_URL)
        session = MediaSession([stream_of(media_candidate("Вот кот: http://googleusercontent.com/image_generation_content/0",
                                                          images=[IMG_URL]))],
                               downloads={full: Resp(content=PNG, headers={"content-type": "image/png"})})
        settings = SimpleNamespace(provider_settings={}, request_timeout=60, stream_idle_timeout=60, host="0.0.0.0", port=8455)
        with patch("app.providers.geminiweb.config_store", SimpleNamespace(settings=settings)), \
                patch("app.config.config_store", SimpleNamespace(settings=settings)):
            events = await chat(make_provider(session), account(delete_chats="false"), "gemini-3.8-flash",
                                [{"role": "user", "content": "Нарисуй кота"}])
        text = text_of(events)
        self.assertNotIn("image_generation_content", text)
        self.assertRegex(text, r"Вот кот:\s*\n\n!\[Gemini image 1\]\(http://127\.0\.0\.1:8455/v1/geminiweb/media/[0-9a-f]{64}\.png\)")
        download = next(c for c in session.calls if c[1] == full)
        self.assertIn("__Secure-1PSID=", download[2]["headers"]["Cookie"])
        url = media.urls_in(text)[0]
        self.assertEqual(media.local_path(url).read_bytes(), PNG)

    async def test_custom_media_base_and_download_failure_fallback(self):
        session = MediaSession([stream_of(media_candidate("", images=[IMG_URL]))],
                               downloads={media.full_size(IMG_URL): Resp(content=b"<html>login</html>"),
                                          IMG_URL: Resp(status=403)})
        events = await chat(make_provider(session), account(delete_chats="false"), "gemini-3.8-flash",
                            [{"role": "user", "content": "draw"}])
        self.assertEqual(text_of(events), f"![Gemini image 1]({IMG_URL})")    # HTML instead of a picture → Google link kept
        self.assertEqual(media.public_base({"media_base_url": "https://my.tunnel.example/"}), "https://my.tunnel.example")

    async def test_veo_video_polls_206_then_serves_mp4_and_thumbnail(self):
        answers = [Resp(status=206), Resp(status=206), Resp(content=MP4, headers={"content-type": "video/mp4"})]
        session = MediaSession([stream_of(media_candidate("Видео готово.", video=(THUMB_URL, VID_URL)))],
                               downloads={VID_URL: lambda: answers.pop(0),
                                          media.full_size(THUMB_URL): Resp(content=JPG)})
        events = await chat(make_provider(session), account(delete_chats="false"), "gemini-3.8-flash",
                            [{"role": "user", "content": "Сделай видео"}])
        text = text_of(events)
        self.assertRegex(text, r"!\[Gemini video 1\]\(http://[^)]+/v1/geminiweb/media/[0-9a-f]{64}\.jpg\)")
        self.assertRegex(text, r"\[▶ Gemini video 1 \(mp4\)\]\(http://[^)]+/v1/geminiweb/media/[0-9a-f]{64}\.mp4\)")
        self.assertEqual(len([c for c in session.calls if c[1] == VID_URL]), 3)
        self.assertTrue(any("Veo" in e["text"] for e in events if e["type"] == "reasoning"))

    async def test_media_route_images_api_and_chat_cache(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import geminiweb_api, media_cache
        name = media.store(PNG, "image/png")
        app = FastAPI()
        app.include_router(geminiweb_api.public)
        client = TestClient(app)
        got = client.get(f"/v1/geminiweb/media/{name}")
        self.assertEqual((got.status_code, got.headers["content-type"], got.content), (200, "image/png", PNG))
        self.assertEqual(client.get("/v1/geminiweb/media/../../config.json").status_code, 404)
        self.assertEqual(client.get("/v1/geminiweb/media/" + "0" * 64 + ".png").status_code, 404)
        # the MemuAI chat keeps a copy in the profile: media_cache reads the stored file directly
        self.assertEqual(await media_cache.download_image(f"http://127.0.0.1:8317/v1/geminiweb/media/{name}"), PNG)

        url = f"http://127.0.0.1:8317/v1/geminiweb/media/{name}"

        async def fake_stream(model, messages, params):
            self.assertEqual(messages[0]["content"], "Generate an image: a red fox (aspect/size: 1024x1024)")
            yield {"choices": [{"delta": {"content": f"Вот лиса\n\n![Gemini image 1]({url})"}}]}

        async def no_image(model, messages, params):
            yield {"choices": [{"delta": {"content": "I can't create images for you right now."}}]}
        gemini = SimpleNamespace(provider="geminiweb")
        with patch("app.openai_api._check_auth", lambda request: None), \
                patch("app.routing.resolve", lambda model: gemini if model.startswith("gemini") else
                      SimpleNamespace(provider="custom")), \
                patch("app.chat.chat_service", SimpleNamespace(stream_completion=fake_stream)):
            body = client.post("/v1/images/generations", json={"model": "gemini-3.8-flash", "prompt": "a red fox",
                                                               "size": "1024x1024"}).json()
            self.assertEqual(body["data"][0]["url"], url)
            body = client.post("/v1/images/generations", json={"model": "gemini-3.8-flash", "prompt": "a red fox",
                                                               "size": "1024x1024", "response_format": "b64_json"}).json()
            self.assertEqual(base64.b64decode(body["data"][0]["b64_json"]), PNG)
            bad = client.post("/v1/images/generations", json={"model": "gpt-4o", "prompt": "x"})
            self.assertEqual(bad.status_code, 400)
            self.assertIn("Gemini Web", bad.json()["error"]["message_en"])
        with patch("app.openai_api._check_auth", lambda request: None), \
                patch("app.routing.resolve", lambda model: gemini), \
                patch("app.chat.chat_service", SimpleNamespace(stream_completion=no_image)):
            none = client.post("/v1/images/generations", json={"model": "gemini-3.8-flash", "prompt": "x"})
            self.assertEqual(none.status_code, 422)
            self.assertIn("can't create images", none.json()["error"]["message_en"])


# ------------------------------------------------------------------ limits

NOW = 1_792_000_000            # fixed «now» (2026-10) for reset maths


def usage_body():
    # [tier, [[remaining credits, used fraction, type, [[reset]]], …], overage]
    return [2, [[120, 0.37, 1, [[NOW + 3 * 3600]]], [900, 0.81, 2, [[NOW + 4 * 86400]]], [50, None, 3]], False]


def quota_body(pro_left=0):
    # [[[[ids…, action], _, used %, [reset], total, remaining], …]]
    return [[[[1, 4], None, 100.0 if not pro_left else 40.0, [NOW + 2 * 3600], 100, pro_left],
             [[1, 15], None, 0, None, 0, 0]]]


def limits_batch(pro_left=0):
    return {limits.RPC_USAGE_INFO: batch_response(limits.RPC_USAGE_INFO, usage_body()),
            limits.RPC_GEMINI_QUOTA: batch_response(limits.RPC_GEMINI_QUOTA, quota_body(pro_left)),
            limits.RPC_EXTRA_QUOTA: batch_response(limits.RPC_EXTRA_QUOTA, [False, 0.25, [NOW + 86400]])}


class LimitsTest(unittest.IsolatedAsyncioTestCase):
    def test_parsers(self):
        usage = limits.parse_usage_info(batch_response(limits.RPC_USAGE_INFO, usage_body()))
        self.assertEqual(usage["plan"], "Google AI Pro")
        self.assertEqual([(w["window"], w["used_pct"], w["remaining"]) for w in usage["windows"]],
                         [("5h", 37, 120), ("week", 81, 900)])
        self.assertEqual(usage["ai_credits"], 50)
        quotas = limits.parse_gemini_quota(batch_response(limits.RPC_GEMINI_QUOTA, quota_body()))
        self.assertEqual([(q["action"], q["total"], q["remaining"], q["unlimited"]) for q in quotas],
                         [(4, 100, 0, False), (15, 0, 0, True)])
        extra = limits.parse_extra_quota(batch_response(limits.RPC_EXTRA_QUOTA, [True, 1.0, [str((NOW + 60) * 1000)]]))
        self.assertEqual(extra, {"blocked": True, "used_pct": 100, "reset": float(NOW + 60)})
        self.assertEqual(limits.parse_usage_info("garbage"), {})

    def test_summary_rows_and_soonest_reset(self):
        usage = limits.parse_usage_info(batch_response(limits.RPC_USAGE_INFO, usage_body()))
        quotas = limits.parse_gemini_quota(batch_response(limits.RPC_GEMINI_QUOTA, quota_body()))
        out = limits.summary(usage, quotas, {"blocked": False, "used_pct": 25, "reset": NOW + 86400}, now=NOW)
        self.assertEqual(out["subscription"], "Google AI Pro")
        rows = {r["display_en"]: r for r in out["rows"]}
        self.assertEqual((rows["Gemini — 5-hour window"]["used"], rows["Gemini — 5-hour window"]["limit"]), (37, 100))
        self.assertEqual((rows["Pro"]["used"], rows["Pro"]["limit"], rows["Pro"]["reset"]), (100, 100, NOW + 2 * 3600))
        self.assertEqual(rows["Flash Thinking / extended thinking"]["unit_en"], "unlimited")
        self.assertEqual(out["exhausted"], ["Pro"])
        self.assertEqual(limits.soonest_reset(out, now=NOW), NOW + 2 * 3600)
        empty = limits.summary({}, [], {}, paid=False)
        self.assertEqual(empty["rows"], [])
        self.assertIn("1037", empty["notice"])

    def test_limit_reply_detection_and_reset_text(self):
        for text in ("You've reached your limit for Pro models. Your limit resets at 5:30 PM.",
                     "You've reached your daily image generation limit.",
                     "Вы достигли лимита для модели Pro. Лимит сбросится через 3 часа.",
                     "I can't create more images for you today."):
            self.assertTrue(limits.is_limit_reply(text), text)
        self.assertFalse(limits.is_limit_reply("Speed limits in Germany are 50 km/h in town." * 20))
        self.assertFalse(limits.is_limit_reply("Привет! Чем помочь?"))
        self.assertEqual(limits.reset_from_text("Лимит сбросится через 3 часа", now=NOW), NOW + 3 * 3600)
        self.assertEqual(limits.reset_from_text("resets in 45 minutes", now=NOW), NOW + 45 * 60)
        self.assertIsNotNone(limits.reset_from_text("resets at 5:30 PM", now=NOW))

    async def test_usage_card_and_batch_requests(self):
        session = MediaSession([], batch=limits_batch(pro_left=60))
        provider = make_provider(session)
        acc = account()
        with patch("app.providers.geminiweb_limits.time.time", return_value=NOW):
            card = await provider.usage(acc)
        self.assertEqual(card["subscription"], "Google AI Pro")
        self.assertEqual(card["email"], "me@example.com")
        self.assertIn({"display": "Pro", "display_en": "Pro", "used": 40, "limit": 100, "unit": "запросов",
                       "unit_en": "requests", "reset": NOW + 2 * 3600}, card["rows"])
        self.assertIs(acc.extra["gw_limits"], card)
        usage_call = next(c for c in session.calls if f"rpcids={limits.RPC_USAGE_INFO}" in c[1])
        self.assertIn("source-path=%2Fusage", usage_call[1])
        quota_payloads = [json.loads(json.loads(__import__("urllib.parse").parse.parse_qs(
            c[2]["data"].decode())["f.req"][0])[0][0][1]) for c in session.calls if f"rpcids={limits.RPC_GEMINI_QUOTA}" in c[1]]
        self.assertEqual(quota_payloads, [[[[1, 11], [2, 11], [6, 11]]], [[[1, 4], [6, 6], [1, 15]]]])
        guest = await make_provider(MediaSession([])).usage(account(cookie=""))
        self.assertEqual(guest["rows"], [])

    async def test_1037_is_a_429_with_reset_time(self):
        err = ["wrb.fr", None, None, None, None, [3, None, [["type.googleapis.com/x", [1037]]]]]
        body = json.dumps([err])
        session = MediaSession([f")]}}'\n\n{len(body) + 1}\n{body}\n"])
        provider = make_provider(session)
        acc = account(delete_chats="false")
        reset = time.time() + 7200
        acc.extra["gw_limits"] = {"rows": [{"display": "Pro", "used": 100, "limit": 100, "reset": reset}]}
        with self.assertRaises(ProviderRateLimitError) as ctx:
            await chat(provider, acc, "gemini-3.1-pro", [{"role": "user", "content": "hi"}])
        self.assertAlmostEqual(ctx.exception.retry_after, 7200, delta=5)
        self.assertIn("Resets at", str(ctx.exception))
        self.assertIn("Сброс в", ctx.exception.message_ru)

    async def test_limit_text_reply_is_a_429_not_an_answer(self):
        session = MediaSession([answer_frames("You've reached your limit for 3.1 Pro. Your limit resets in 2 hours.")])
        with self.assertRaises(ProviderRateLimitError) as ctx:
            await chat(make_provider(session), account(delete_chats="false"), "gemini-3.1-pro",
                       [{"role": "user", "content": "hi"}])
        self.assertAlmostEqual(ctx.exception.retry_after, 7200, delta=5)
        self.assertIn("исчерпании лимита", ctx.exception.message_ru)

    async def test_diagnosis_shows_limits(self):
        session = MediaSession([answer_frames("Привет! Рад помочь.")], batch=limits_batch())
        provider = make_provider(session)
        with patch("app.providers.geminiweb.GeminiWebProvider._seed_catalog", staticmethod(lambda status: None)):
            result = await provider.diagnose(account(delete_chats="false"))
        step = next(s for s in result["steps"] if s["id"] == "limits")
        self.assertEqual(step["status"], "warn")                  # Pro is used up
        self.assertIn("Google AI Pro", step["detail_ru"])
        self.assertIn("Исчерпано: Pro", step["detail_ru"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["limits"]["subscription"], "Google AI Pro")


# ------------------------------------------------------------------ thinking level on the wire


class EffortWireTest(unittest.IsolatedAsyncioTestCase):
    async def test_level_reaches_freq_and_model_header(self):
        """low → inner[17]=[[4]], inner[80]=1, header tail 1; medium → [[0]]/1; high → [[0]]/2 + header tail 2."""
        for level, think, ext in (("low", [[4]], 1), ("medium", [[0]], 1), ("high", [[0]], 2)):
            session = MediaSession([answer_frames("ok")])
            await chat(make_provider(session), account(delete_chats="false"), "gemini-3.1-pro",
                       [{"role": "user", "content": "2+2?"}], {"_reasoning_level": level})
            call = session.generate_calls()[0]
            inner = inner_of(call)
            header = json.loads(call[2]["headers"][proto.MODEL_HEADER])
            self.assertEqual((inner[17], inner[80], header[15]), (think, ext, ext), level)
            self.assertEqual(inner[79], header[14])                  # model number in both places


if __name__ == "__main__":
    unittest.main()
