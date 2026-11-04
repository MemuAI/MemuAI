"""Voice & Live: built-in OpenRouter, HeyGen pcm fix, automatic
model/voice lists, new Live settings.

HeyGen (heygen/heygen-voice-1-turbo:free) streams 44.1 kHz mono PCM only, but
the app hardcoded response_format "mp3" and misreported the rejection as a
voice problem. OpenRouter is now a built-in TTS/STT provider, voices come
from the provider's own /models payload (supported_voices) via
POST /api/voice/fetch-lists, and Live gained default instructions plus a
transcription-model setting.
"""
from __future__ import annotations

import asyncio
import io
import json
import threading
import unittest
import wave

import httpx

from app import stt_providers
from app.realtime_live import LiveConfig, OpenAIRealtimeAdapter, catalog
from app.tts_providers import (
    HEYGEN_VOICES,
    TTSApiError,
    build_request,
    check_settings,
    error_message,
    fetch_provider_lists,
    pcm_rate_for,
    provider_for_url,
    response_format_for,
    synthesize,
)

HEYGEN = "heygen/heygen-voice-1-turbo:free"
OPENROUTER = "https://openrouter.ai/api/v1"


def _run(coro):
    """Run a coroutine on a fresh loop.

    ``asyncio.run()`` — and even ``new_event_loop().run_until_complete()`` —
    refuse with ``RuntimeError`` when some earlier test in the same process
    left an event loop running in this thread (47 tests across the suite hit
    this in the split full run; it is order-dependent, the same class of
    intermittent test failure). A helper thread with its own loop
    is unaffected by the thread-local running loop, so use it then.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
    result = {}

    def target():
        loop = asyncio.new_event_loop()
        try:
            result["value"] = loop.run_until_complete(coro)
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            result["error"] = exc
        finally:
            loop.close()

    thread = threading.Thread(target=target)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result["value"]


class FormatForModelTest(unittest.TestCase):
    def test_heygen_defaults_to_pcm(self):
        self.assertEqual(response_format_for(HEYGEN), "pcm")
        self.assertEqual(response_format_for(HEYGEN, "auto"), "pcm")
        self.assertEqual(response_format_for(HEYGEN, ""), "pcm")

    def test_other_models_default_to_mp3(self):
        self.assertEqual(response_format_for("gpt-4o-mini-tts"), "mp3")
        self.assertEqual(response_format_for("kokoro"), "mp3")

    def test_explicit_format_wins(self):
        self.assertEqual(response_format_for(HEYGEN, "wav"), "wav")
        self.assertEqual(response_format_for("gpt-4o-mini-tts", "pcm"), "pcm")

    def test_pcm_rate(self):
        self.assertEqual(pcm_rate_for(HEYGEN), 44100)
        self.assertEqual(pcm_rate_for("gpt-4o-mini-tts"), 24000)


class OpenRouterProviderTest(unittest.TestCase):
    def test_tts_provider_for_url(self):
        self.assertEqual(provider_for_url(OPENROUTER), "openrouter")
        self.assertEqual(provider_for_url(OPENROUTER + "/audio/speech"), "openrouter")

    def test_stt_provider_for_url(self):
        self.assertEqual(stt_providers.provider_for_url(OPENROUTER), "openrouter")

    def test_tts_catalog_entry(self):
        from app.tts_providers import PROVIDERS

        entry = PROVIDERS["openrouter"]
        self.assertEqual(entry["default_model"], HEYGEN)
        self.assertEqual(entry["default_voice"], "nadine")
        self.assertIn("openrouter.ai", entry["base_url"])

    def test_stt_catalog_entry(self):
        entry = stt_providers.PROVIDERS["openrouter"]
        self.assertIn("openai/whisper-large-v3", entry["models"])
        self.assertIn("openrouter.ai", entry["base_url"])

    def test_heygen_voices(self):
        self.assertEqual(len(HEYGEN_VOICES), 10)
        self.assertEqual(list(HEYGEN_VOICES[:3]), ["nadine", "tabitha", "bram"])


class HeyGenValidationTest(unittest.TestCase):
    def test_placeholder_voice_rejected_with_list(self):
        with self.assertRaises(TTSApiError) as caught:
            check_settings("openrouter", HEYGEN, "голос")
        message = str(caught.exception)
        self.assertIn("nadine", message)
        self.assertIn("10", message)

    def test_heygen_voice_ok(self):
        model, voice = check_settings("openrouter", HEYGEN, "nadine")
        self.assertEqual((model, voice), (HEYGEN, "nadine"))

    def test_custom_base_with_heygen_model_also_validated(self):
        with self.assertRaises(TTSApiError):
            check_settings("custom", HEYGEN, "not-a-voice")

    def test_build_request_sends_pcm_for_heygen(self):
        request = build_request("openrouter", OPENROUTER, "key", HEYGEN, "nadine", "hi")
        self.assertEqual(request["json"]["response_format"], "pcm")
        self.assertEqual(request["_format"], "pcm")
        self.assertEqual(request["_pcm_rate"], 44100)

    def test_build_request_sends_mp3_for_other_models(self):
        request = build_request("openrouter", OPENROUTER, "key", "gpt-4o-mini-tts", "alloy", "hi")
        self.assertEqual(request["json"]["response_format"], "mp3")

    def test_error_message_names_format_before_voice(self):
        body = json.dumps({"error": {"message": 'HeyGen Voice only supports response_format "pcm".'}}).encode()
        message = error_message("custom", 400, body, model=HEYGEN, voice="голос")
        self.assertIn("формат", message.lower())
        self.assertNotIn("не подходит", message)


class SynthesizePcmTest(unittest.TestCase):
    def test_raw_pcm_wrapped_into_wav_at_44100(self):
        pcm = bytes(range(256)) * 100

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertTrue(str(request.url).endswith("/audio/speech"))
            self.assertEqual(json.loads(request.content)["response_format"], "pcm")
            return httpx.Response(200, content=pcm, headers={"content-type": "application/octet-stream"})

        audio, mime = _run(synthesize(
            "Привет", base_url=OPENROUTER, api_key="x", model=HEYGEN, voice="nadine",
            transport=httpx.MockTransport(handler)))
        self.assertEqual(mime, "audio/wav")
        self.assertEqual(audio[:4], b"RIFF")
        with wave.open(io.BytesIO(audio), "rb") as wav:
            self.assertEqual(wav.getnchannels(), 1)
            self.assertEqual(wav.getframerate(), 44100)
            self.assertEqual(wav.getsampwidth(), 2)
            self.assertEqual(wav.getnframes(), len(pcm) // 2)

    def test_mp3_body_passes_through(self):
        fake_mp3 = b"ID3" + b"\x00" * 100

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=fake_mp3, headers={"content-type": "audio/mpeg"})

        audio, mime = _run(synthesize(
            "Hi", base_url="https://api.openai.com/v1", api_key="x",
            model="gpt-4o-mini-tts", voice="alloy",
            transport=httpx.MockTransport(handler)))
        self.assertEqual((audio, mime), (fake_mp3, "audio/mpeg"))


def _openrouter_models_payload() -> dict:
    return {"data": [
        {"id": HEYGEN, "supported_voices": ["nadine", "tabitha", "bram"]},
        {"id": "openai/gpt-4o-mini-tts", "supported_voices": ["alloy", "ash"]},
        {"id": "meta/llama-3", "supported_voices": []},
    ]}


class FetchListsTest(unittest.TestCase):
    def setUp(self):
        from app.tts_providers import _LISTS_CACHE

        _LISTS_CACHE.clear()

    def run_fetch(self, handler, **kwargs):
        return _run(fetch_provider_lists(
            transport=httpx.MockTransport(handler), **kwargs))

    def test_openrouter_tts_lists_with_supported_voices(self):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, json=_openrouter_models_payload())

        result = self.run_fetch(handler, base_url=OPENROUTER, kind="tts", model=HEYGEN)
        self.assertTrue(result["live"])
        self.assertEqual([m["id"] for m in result["models"]][:2],
                         [HEYGEN, "openai/gpt-4o-mini-tts"])
        self.assertEqual(result["model"], HEYGEN)
        self.assertEqual(result["voices"], ["nadine", "tabitha", "bram"])
        self.assertIn("output_modalities=speech", seen[0])

    def test_openrouter_stt_lists(self):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, json={"data": [
                {"id": "openai/whisper-large-v3"},
                {"id": "deepgram/nova-3"},
            ]})

        result = self.run_fetch(handler, base_url=OPENROUTER, kind="stt")
        self.assertTrue(result["live"])
        self.assertEqual([m["id"] for m in result["models"]],
                         ["openai/whisper-large-v3", "deepgram/nova-3"])
        self.assertIn("output_modalities=transcription", seen[0])

    def test_realtime_kind_filters_to_realtime_models(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [
                {"id": "gpt-realtime"},
                {"id": "gpt-4o-mini"},
                {"id": "gemini-2.5-flash-live"},
                {"id": "gpt-4o-mini-tts"},
            ]})

        result = self.run_fetch(handler, base_url="https://api.openai.com/v1",
                                kind="realtime", voices_from="openai")
        self.assertEqual([m["id"] for m in result["models"]],
                         ["gpt-realtime", "gemini-2.5-flash-live"])
        self.assertEqual(result["voices"], ["alloy", "ash", "ballad", "coral",
                                            "echo", "fable", "nova", "onyx",
                                            "sage", "shimmer", "verse", "marin",
                                            "cedar"])

    def test_audio_voices_fallback_when_models_carry_no_voices(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url).endswith("/audio/voices"):
                return httpx.Response(200, json={"voices": [{"voice_id": "v1"}, {"voice_id": "v2"}]})
            return httpx.Response(200, json={"data": [{"id": "tts-1"}]})

        result = self.run_fetch(handler, base_url="https://example.com/v1",
                                kind="tts", model="tts-1")
        self.assertEqual(result["voices"], ["v1", "v2"])

    def test_second_call_uses_cache(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(200, json=_openrouter_models_payload())

        self.run_fetch(handler, base_url=OPENROUTER, kind="tts", model=HEYGEN)
        self.run_fetch(handler, base_url=OPENROUTER, kind="tts", model=HEYGEN)
        self.assertEqual(len(calls), 1)

    def test_invalid_base_url_rejected(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": []})

        with self.assertRaises(TTSApiError):
            self.run_fetch(handler, base_url="not a url", kind="tts")


class LiveSettingsTest(unittest.TestCase):
    def test_catalog_has_transcription_models(self):
        rows = {row["id"]: row for row in catalog()}
        self.assertEqual(rows["openai-live"]["transcription_models"],
                         ["gpt-4o-mini-transcribe", "gpt-4o-transcribe", "whisper-1"])
        self.assertEqual(rows["custom"]["transcription_models"],
                         ["gpt-4o-mini-transcribe", "gpt-4o-transcribe", "whisper-1"])
        self.assertEqual(rows["gemini"]["transcription_models"], [])

    def test_instructions_fall_back_to_settings(self):
        prefs = {"voice_realtime_provider": "openai-realtime",
                 "voice_realtime_api_keys": {"openai-realtime": "k"},
                 "voice_realtime_instructions": "Отвечай кратко."}
        config = LiveConfig.from_preferences(prefs)
        self.assertEqual(config.instructions, "Отвечай кратко.")
        config2 = LiveConfig.from_preferences(prefs, instructions="Свои инструкции")
        self.assertEqual(config2.instructions, "Свои инструкции")

    def test_transcription_model_from_settings(self):
        prefs = {"voice_realtime_provider": "openai-realtime",
                 "voice_realtime_api_keys": {"openai-realtime": "k"},
                 "voice_realtime_transcription_model": "gpt-4o-transcribe"}
        config = LiveConfig.from_preferences(prefs)
        self.assertEqual(config.transcription_model, "gpt-4o-transcribe")

    def test_ga_session_uses_configured_transcription_model(self):
        config = LiveConfig(provider="openai-realtime", model="gpt-realtime",
                            voice="alloy", api_key="k", url="wss://example.com",
                            transcription_model="gpt-4o-transcribe")
        messages = OpenAIRealtimeAdapter(config=config).setup_messages()
        session = messages[0]["session"]
        self.assertEqual(session["audio"]["input"]["transcription"]["model"],
                         "gpt-4o-transcribe")

    def test_ga_session_defaults_when_not_configured(self):
        config = LiveConfig(provider="openai-realtime", model="gpt-realtime",
                            voice="alloy", api_key="k", url="wss://example.com")
        messages = OpenAIRealtimeAdapter(config=config).setup_messages()
        session = messages[0]["session"]
        self.assertEqual(session["audio"]["input"]["transcription"]["model"],
                         "gpt-4o-mini-transcribe")

    def test_beta_session_uses_configured_transcription_model(self):
        config = LiveConfig(provider="openai-realtime", model="gpt-realtime",
                            voice="alloy", api_key="k", url="wss://example.com",
                            dialect="beta", transcription_model="whisper-1")
        messages = OpenAIRealtimeAdapter(config=config).setup_messages()
        session = messages[0]["session"]
        self.assertEqual(session["input_audio_transcription"]["model"], "whisper-1")


class PreferencesWhitelistTest(unittest.TestCase):
    """The new keys must survive save/load — pgdata
    silently drops every key that is not in DEFAULT_PREFERENCES."""

    def setUp(self):
        import tempfile
        from pathlib import Path as _Path
        from unittest.mock import patch as _patch

        from app import pgdata as _pgdata

        self._tmp = tempfile.TemporaryDirectory()
        self._patch = _patch.object(_pgdata, "MEMU_DIR", _Path(self._tmp.name))
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.addCleanup(self._tmp.cleanup)
        self.pgdata = _pgdata

    def test_new_keys_round_trip(self):
        saved = self.pgdata.save_preferences({
            "voice_tts_api_format": "pcm",
            "voice_tts_local_format": "wav",
            "voice_tts_api_profiles": {"openrouter": {"url": "https://openrouter.ai/api/v1",
                                                      "key": "sk-or-x", "model": "heygen/heygen-voice-1-turbo:free",
                                                      "voice": "nadine"}},
            "voice_realtime_instructions": "Отвечай кратко.",
            "voice_realtime_transcription_model": "gpt-4o-transcribe",
        })
        self.assertEqual(saved["voice_tts_api_format"], "pcm")
        self.assertEqual(saved["voice_tts_local_format"], "wav")
        self.assertEqual(saved["voice_realtime_instructions"], "Отвечай кратко.")
        self.assertEqual(saved["voice_realtime_transcription_model"], "gpt-4o-transcribe")
        self.assertEqual(saved["voice_tts_api_profiles"]["openrouter"]["voice"], "nadine")
        loaded = self.pgdata.load_preferences()
        self.assertEqual(loaded["voice_tts_api_format"], "pcm")
        self.assertEqual(loaded["voice_realtime_instructions"], "Отвечай кратко.")

    def test_tts_profiles_normalized_and_openrouter_kept(self):
        saved = self.pgdata.save_preferences({
            "voice_tts_api_profiles": {
                "openrouter": {"url": "https://openrouter.ai/api/v1", "key": "k", "model": "m", "voice": "v", "junk": 1},
                "not-a-provider": {"url": "x"},
            },
            "voice_stt_api_profiles": {
                "openrouter": {"url": "https://openrouter.ai/api/v1", "key": "k", "model": "m"},
            },
        })
        self.assertEqual(list(saved["voice_tts_api_profiles"]), ["openrouter"])
        self.assertNotIn("junk", saved["voice_tts_api_profiles"]["openrouter"])
        self.assertIn("openrouter", saved["voice_stt_api_profiles"])


WINDOW = {"host": "127.0.0.1:8601", "origin": "http://127.0.0.1:8601",
        "sec-fetch-site": "same-origin"}


class FetchListsEndpointTest(unittest.TestCase):
    """The realtime branch of /api/voice/fetch-lists must accept an HTTP API
    root (built-in providers) as well as a wss:// URL (custom providers)."""

    def setUp(self):
        import tempfile
        from pathlib import Path as _Path
        from unittest.mock import patch as _patch

        from app import pgdata as _pgdata

        self._tmp = tempfile.TemporaryDirectory()
        self._patch = _patch.object(_pgdata, "MEMU_DIR", _Path(self._tmp.name))
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.addCleanup(self._tmp.cleanup)

    def _client(self):
        from fastapi.testclient import TestClient

        from app.playground_api import create_app

        return TestClient(create_app())

    def test_http_base_passes_through_for_realtime(self):
        from unittest.mock import AsyncMock, patch as _patch

        from app import tts_providers

        seen = {}

        async def fake_fetch(base_url, **kwargs):
            seen["base"] = base_url
            seen["kind"] = kwargs.get("kind")
            return {"live": True, "models": [{"id": "gpt-realtime", "voices": None}],
                    "model": "gpt-realtime", "voices": ["alloy"], "cached": False}

        with _patch.object(tts_providers, "fetch_provider_lists", new=AsyncMock(side_effect=fake_fetch)):
            response = self._client().post("/api/voice/fetch-lists", json={
                "base": "https://api.openai.com/v1", "kind": "realtime", "voices_from": "openai"},
                headers=WINDOW)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(seen["base"], "https://api.openai.com/v1")
        self.assertEqual(seen["kind"], "realtime")
        self.assertTrue(response.json()["live"])

    def test_wss_base_derives_https_root_for_realtime(self):
        from unittest.mock import AsyncMock, patch as _patch

        from app import tts_providers

        seen = {}

        async def fake_fetch(base_url, **kwargs):
            seen["base"] = base_url
            return {"live": True, "models": [], "model": "", "voices": [], "cached": False}

        with _patch.object(tts_providers, "fetch_provider_lists", new=AsyncMock(side_effect=fake_fetch)):
            response = self._client().post("/api/voice/fetch-lists", json={
                "base": "wss://example.com/v1/realtime", "kind": "realtime"},
                headers=WINDOW)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(seen["base"], "https://example.com/v1")


if __name__ == "__main__":
    unittest.main()
