"""Round 25: official TTS providers (OpenAI, Gemini, ElevenLabs, Azure), readable errors, chunking."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import shutil
import subprocess
import unittest
import wave
from pathlib import Path

import httpx

from app import tts_providers as tp
from app.voice_runtime import VoiceRuntime

ROOT = Path(__file__).resolve().parents[1]
GOOGLE_404_HTML = (b"<!DOCTYPE html><html lang=en><meta charset=utf-8><title>Error 404 (Not Found)!!1</title>"
                   b"<style>*{margin:0}</style><p><b>404.</b> <ins>That's an error.</ins><p>The requested URL "
                   b"<code>/v1beta/openai/audio/speech</code> was not found on this server.</html>")


def run(coro):
    return asyncio.run(coro)


def pcm(samples: int = 2400) -> bytes:
    return b"\x01\x00" * samples


def synth(handler, text="Привет, мир.", **kw):
    tp.CACHE.items.clear()
    params = {"base_url": "https://api.openai.com/v1", "api_key": "sk-test", "model": "", "voice": ""}
    params.update(kw)
    return run(tp.synthesize(text, transport=httpx.MockTransport(handler), **params))


class ProviderDetection(unittest.TestCase):
    def test_url_maps_to_service_and_unknown_hosts_stay_openai_compatible(self):
        self.assertEqual(tp.provider_for_url("https://api.openai.com/v1"), "openai")
        self.assertEqual(tp.provider_for_url("https://generativelanguage.googleapis.com/v1beta/openai"), "gemini")
        self.assertEqual(tp.provider_for_url("https://api.elevenlabs.io"), "elevenlabs")
        self.assertEqual(tp.provider_for_url("https://westeurope.tts.speech.microsoft.com"), "azure")
        self.assertEqual(tp.provider_for_url("http://127.0.0.1:8880/v1"), "custom")
        self.assertEqual(tp.provider_for_url("https://openrouter.example/v1"), "custom")

    def test_public_descriptors_have_gemini_voices_and_key_links(self):
        rows = {row["id"]: row for row in tp.public_providers()}
        self.assertEqual(set(rows), {"openai", "gemini", "elevenlabs", "azure", "custom"})
        self.assertEqual(len(rows["gemini"]["voices"]), 30)
        self.assertIn("gemini-3.8-flash-tts", rows["gemini"]["models"])
        self.assertTrue(rows["gemini"]["key_url"].startswith("https://aistudio.google.com"))
        self.assertIn("gpt-4o-mini-tts", rows["openai"]["models"])
        self.assertNotIn("chunk", rows["openai"])


class GeminiNative(unittest.TestCase):
    def test_reported_bug_google_url_uses_generate_content_not_audio_speech(self):
        seen = {}

        def handler(request: httpx.Request):
            seen["url"] = str(request.url)
            seen["key"] = request.headers.get("x-goog-api-key")
            seen["body"] = json.loads(request.content)
            data = base64.b64encode(pcm()).decode()
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [
                {"inlineData": {"mimeType": "audio/L16;codec=pcm;rate=24000", "data": data}}]}}]})

        audio, mime = synth(handler, base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                            api_key="AIzaKEY", model="gemini-3.8-flash-tts", voice="kore")
        self.assertEqual(seen["url"], "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash-tts:generateContent")
        self.assertEqual(seen["key"], "AIzaKEY")
        cfg = seen["body"]["generationConfig"]
        self.assertEqual(cfg["responseModalities"], ["AUDIO"])
        self.assertEqual(cfg["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"], "Kore")
        self.assertEqual(mime, "audio/wav")
        with wave.open(io.BytesIO(audio)) as w:
            self.assertEqual((w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()), (24000, 1, 2, 2400))

    def test_gemini_38_wav_output_is_not_wrapped_twice(self):
        wav = tp.pcm_to_wav(pcm(100))
        handler = lambda r: httpx.Response(200, json={"candidates": [{"content": {"parts": [
            {"inlineData": {"mimeType": "audio/wav", "data": base64.b64encode(wav).decode()}}]}}]})
        audio, mime = synth(handler, base_url="https://generativelanguage.googleapis.com", model="gemini-3.8-flash-lite-tts", voice="Puck")
        self.assertEqual(audio, wav)

    def test_openai_voice_with_gemini_is_explained_before_any_request(self):
        def handler(request):
            raise AssertionError("no request expected")
        with self.assertRaises(tp.TTSApiError) as ctx:
            synth(handler, base_url="https://generativelanguage.googleapis.com", model="gemini-3.8-flash-tts", voice="Alloy")
        self.assertIn("Alloy", str(ctx.exception))
        self.assertIn("Kore", str(ctx.exception))

    def test_non_tts_gemini_model_is_rejected_with_suggestion(self):
        with self.assertRaises(tp.TTSApiError) as ctx:
            synth(lambda r: None, base_url="https://generativelanguage.googleapis.com", model="gemini-3.8-flash", voice="Kore")
        self.assertIn("TTS", str(ctx.exception))

    def test_unknown_model_404_is_short_and_lists_models(self):
        handler = lambda r: httpx.Response(404, json={"error": {"code": 404, "status": "NOT_FOUND",
            "message": "models/gemini-9-tts is not found for API version v1beta"}})
        with self.assertRaises(tp.TTSApiError) as ctx:
            synth(handler, base_url="https://generativelanguage.googleapis.com", model="gemini-9-tts", voice="Kore", api_key="k")
        text = str(ctx.exception)
        self.assertIn("не найдена", text)
        self.assertIn("gemini-3.8-flash-tts", text)
        self.assertLess(len(text), 600)

    def test_no_audio_is_retried_once_then_explained(self):
        calls = []

        def handler(request):
            calls.append(1)
            return httpx.Response(200, json={"candidates": [{"finishReason": "OTHER", "content": {"parts": [{"text": "hi"}]}}]})
        with self.assertRaises(tp.TTSApiError):
            synth(handler, base_url="https://generativelanguage.googleapis.com", model="gemini-3.8-flash-tts", voice="Kore")
        self.assertEqual(len(calls), 2)


class ReadableErrors(unittest.TestCase):
    def test_html_error_page_is_never_shown(self):
        message = tp.error_message("custom", 404, GOOGLE_404_HTML, model="m", voice="v")
        self.assertNotIn("<", message)
        self.assertNotIn("That's an error", message)
        self.assertIn("Base URL", message)

    def test_status_codes_map_to_human_text_in_both_languages(self):
        self.assertIn("API-ключ", tp.error_message("openai", 401, b'{"error":{"message":"Incorrect API key provided: sk-abc123456"}}', secret="sk-abc123456"))
        self.assertNotIn("sk-abc123456", tp.error_message("openai", 401, b'{"error":{"message":"Incorrect API key provided: sk-abc123456"}}', secret="sk-abc123456"))
        self.assertIn("limit", tp.error_message("openai", 429, b"", lang="en"))
        self.assertIn("временно", tp.error_message("elevenlabs", 503, b""))
        self.assertIn("voice", tp.error_message("openai", 400, b'{"error":{"message":"Invalid voice"}}', voice="x", lang="en"))

    def test_connection_errors_are_readable(self):
        def handler(request):
            raise httpx.ConnectError("boom", request=request)
        with self.assertRaises(tp.TTSApiError) as ctx:
            synth(handler, base_url="http://127.0.0.1:8880/v1", api_key="")
        self.assertIn("127.0.0.1", str(ctx.exception))

    def test_missing_key_points_to_where_to_get_one(self):
        with self.assertRaises(tp.TTSApiError) as ctx:
            synth(lambda r: None, api_key="")
        self.assertIn("platform.openai.com", str(ctx.exception))

    def test_non_audio_200_is_an_error(self):
        handler = lambda r: httpx.Response(200, content=b"<html>login</html>", headers={"content-type": "text/html"})
        with self.assertRaises(tp.TTSApiError):
            synth(handler, base_url="https://proxy.example/v1", api_key="")


class OtherProviders(unittest.TestCase):
    def test_openai_body_speed_and_mp3(self):
        seen = {}

        def handler(request):
            seen["url"] = str(request.url); seen["body"] = json.loads(request.content)
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, content=b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\xff\xfb" * 10, headers={"content-type": "audio/mpeg"})
        audio, mime = synth(handler, model="gpt-4o-mini-tts", voice="Nova", speed=tp.speed_from_rate(10))
        self.assertEqual(seen["url"], "https://api.openai.com/v1/audio/speech")
        self.assertEqual(seen["body"]["voice"], "nova")
        self.assertEqual(seen["body"]["speed"], 2.0)
        self.assertEqual(seen["auth"], "Bearer sk-test")
        self.assertEqual(mime, "audio/mpeg")

    def test_gemini_voice_with_openai_is_explained(self):
        with self.assertRaises(tp.TTSApiError):
            synth(lambda r: None, voice="Kore")

    def test_elevenlabs_request(self):
        seen = {}

        def handler(request):
            seen["url"] = str(request.url); seen["key"] = request.headers.get("xi-api-key"); seen["body"] = json.loads(request.content)
            return httpx.Response(200, content=b"\xff\xfb" * 20)
        synth(handler, base_url="https://api.elevenlabs.io", api_key="xi", model="eleven_flash_v2_5", voice="JBFqnCBsd6RMkjVDRZzb")
        self.assertEqual(seen["url"], "https://api.elevenlabs.io/v1/text-to-speech/JBFqnCBsd6RMkjVDRZzb?output_format=mp3_44100_128")
        self.assertEqual(seen["key"], "xi")
        self.assertEqual(seen["body"], {"text": "Привет, мир.", "model_id": "eleven_flash_v2_5"})

    def test_azure_ssml_is_escaped(self):
        seen = {}

        def handler(request):
            seen["url"] = str(request.url); seen["body"] = request.content.decode(); seen["h"] = request.headers
            return httpx.Response(200, content=b"\xff\xfb" * 20)
        synth(handler, text="A & <b>", base_url="https://westeurope.tts.speech.microsoft.com", api_key="az", voice="ru-RU-SvetlanaNeural")
        self.assertEqual(seen["url"], "https://westeurope.tts.speech.microsoft.com/cognitiveservices/v1")
        self.assertIn("A &amp; &lt;b&gt;", seen["body"])
        self.assertIn("name='ru-RU-SvetlanaNeural'", seen["body"])
        self.assertEqual(seen["h"]["ocp-apim-subscription-key"], "az")
        self.assertEqual(seen["h"]["content-type"], "application/ssml+xml")
        with self.assertRaises(tp.TTSApiError):
            synth(handler, base_url="https://westeurope.tts.speech.microsoft.com", api_key="az", voice="alloy")


class LongText(unittest.TestCase):
    def test_split_respects_limit_and_keeps_all_words(self):
        text = ("Это предложение номер один. " * 300 + "\n\n" + "слово " * 2000).strip()
        chunks = tp.split_text(text, 4000)
        self.assertTrue(all(0 < len(c) <= 4000 for c in chunks))
        self.assertEqual(" ".join(text.split()), " ".join(" ".join(chunks).split()))

    def test_long_openai_text_is_sent_in_chunks_and_mp3_concatenated(self):
        sizes = []

        def handler(request):
            sizes.append(len(json.loads(request.content)["input"]))
            return httpx.Response(200, content=b"\xff\xfb" * 4, headers={"content-type": "audio/mpeg"})
        audio, mime = synth(handler, text="Предложение для проверки. " * 600)
        self.assertGreater(len(sizes), 1)
        self.assertTrue(all(size <= 4000 for size in sizes))
        self.assertEqual(len(audio), 8 * len(sizes))

    def test_wav_chunks_are_merged_into_one_valid_wav(self):
        merged, mime = tp.merge_audio([(tp.pcm_to_wav(pcm(10)), "audio/wav"), (tp.pcm_to_wav(pcm(15)), "audio/wav")])
        with wave.open(io.BytesIO(merged)) as w:
            self.assertEqual(w.getnframes(), 25)

    def test_repeat_is_served_from_cache(self):
        calls = []

        def handler(request):
            calls.append(1)
            return httpx.Response(200, content=b"\xff\xfb" * 4)
        tp.CACHE.items.clear()
        for _ in range(2):
            run(tp.synthesize("Повтор", base_url="https://api.openai.com/v1", api_key="k", model="tts-1",
                              voice="alloy", transport=httpx.MockTransport(handler)))
        self.assertEqual(len(calls), 1)


class SttApi(unittest.TestCase):
    def test_gemini_stt_uses_native_endpoint_and_html_is_hidden(self):
        runtime = VoiceRuntime.__new__(VoiceRuntime)
        calls = []
        def google(request):
            calls.append(str(request.url))
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "Test transcript"}]}}]})
        result = run(runtime.transcribe_api(b"x", "audio/webm", base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                     api_key="k", model="gemini-3.8-flash", transport=httpx.MockTransport(google)))
        self.assertEqual(result, "Test transcript")
        self.assertEqual(calls, ["https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent"])
        handler = lambda r: httpx.Response(404, content=GOOGLE_404_HTML, headers={"content-type": "text/html"})
        with self.assertRaises(RuntimeError) as ctx:
            run(runtime.transcribe_api(b"x", "audio/webm", base_url="https://stt.example/v1", api_key="k",
                                       model="whisper-1", transport=httpx.MockTransport(handler)))
        self.assertNotIn("<", str(ctx.exception))


class SpeakRoute(unittest.TestCase):
    def test_reported_settings_return_a_short_explanation_not_html(self):
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        from app import pgdata, playground_api
        prefs = dict(pgdata.DEFAULT_PREFERENCES, voice_tts_provider="api", language="ru",
                     voice_tts_api_url="https://generativelanguage.googleapis.com/v1beta/openai",
                     voice_tts_api_key="AIza-test", voice_tts_api_model="gemini-3.8-flash-tts", voice_tts_api_voice="Alloy")
        with patch.object(pgdata, "load_preferences", lambda: prefs), \
                TestClient(playground_api.create_app(), base_url="http://127.0.0.1") as client:
            response = client.post("/api/voice/speak", json={"text": "Привет"})
            self.assertEqual(response.status_code, 400)
            error = response.json()["error"]
            self.assertIn("Alloy", error)
            self.assertNotIn("<", error)
            catalog = client.get("/api/voice/catalog").json()
            self.assertEqual(catalog["tts_api_provider"], "gemini")
            self.assertEqual(len(catalog["tts_api_providers"]), 5)


class UiContracts(unittest.TestCase):
    def test_settings_ui_uses_provider_presets_and_test_button(self):
        js = (ROOT / "app" / "pgstatic" / "voice-settings.js").read_text(encoding="utf-8")
        html = (ROOT / "app" / "pgstatic" / "index.html").read_text(encoding="utf-8")
        app_js = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        self.assertIn("voice-settings.js", html)
        self.assertIn("tts_api_providers", js)
        self.assertIn("MemuVoiceSettings", app_js)
        self.assertIn("key_url", js)
        api = (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8")
        self.assertIn("tts_api_providers", api)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_reported_alloy_with_gemini_is_flagged_in_settings(self):
        script = r"""
        const vm = require("vm"); const fs = require("fs");
        const ctxWin = { window: {}, document: {}, URL }; vm.createContext(ctxWin);
        vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), ctxWin);
        const VS = ctxWin.window.MemuVoiceSettings;
        const join = (o) => Object.values(o).filter((v) => typeof v === "string").join(" ");
        const K = { section: join, field: join, row: join, select: () => "", secret: () => "", slider: () => "", note: (t) => t };
        const prefs = JSON.parse(process.argv[2]);
        const ctx = { en: false, K, esc: (v) => String(v), pref: (k, d) => (k in prefs ? prefs[k] : d),
                      catalog: { tts_api_providers: JSON.parse(process.argv[3]) }, localEngines: [] };
        process.stdout.write(JSON.stringify({ html: VS.ttsHtml(ctx), label: VS.sourceLabel(ctx),
          kind: VS.providerForUrl(prefs.voice_tts_api_url) }));
        """
        prefs = {"voice_tts_provider": "api", "voice_tts_api_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                 "voice_tts_api_model": "gemini-3.8-flash-tts", "voice_tts_api_voice": "Alloy"}
        out = subprocess.run(["node", "-e", script, str(ROOT / "app" / "pgstatic" / "voice-settings.js"), json.dumps(prefs),
                              json.dumps(tp.public_providers())], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        data = json.loads(out.stdout)
        self.assertEqual(data["kind"], "gemini")
        self.assertEqual(data["label"], "Google Gemini (AI Studio)")
        self.assertIn("«Alloy» — голос OpenAI", data["html"])
        self.assertIn("aistudio.google.com/apikey", data["html"])
        prefs["voice_tts_api_voice"] = "Kore"
        out = subprocess.run(["node", "-e", script, str(ROOT / "app" / "pgstatic" / "voice-settings.js"), json.dumps(prefs),
                              json.dumps(tp.public_providers())], capture_output=True, text=True, timeout=30)
        self.assertNotIn("голос OpenAI", json.loads(out.stdout)["html"])


if __name__ == "__main__":
    unittest.main()
