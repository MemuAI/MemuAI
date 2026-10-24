"""Focused contracts for Voice, TTS, and MemuAI Live."""
from __future__ import annotations

import base64
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import pgdata
from app.voice_runtime import VoiceRuntime, decode_audio_data, prepare_tts_text, validate_api_base_url


ROOT = Path(__file__).resolve().parents[1]


class VoiceRuntimeContracts(unittest.TestCase):
    def test_audio_data_url_has_type_and_size_validation(self) -> None:
        audio = b"RIFF" + b"x" * 20
        value = "data:audio/wav;base64," + base64.b64encode(audio).decode("ascii")
        self.assertEqual(decode_audio_data(value), (audio, "audio/wav"))
        with self.assertRaises(ValueError):
            decode_audio_data("data:text/plain;base64,SGVsbG8=")

    def test_api_base_url_rejects_credentials_and_non_http(self) -> None:
        self.assertEqual(validate_api_base_url("https://api.openai.com/v1/"), "https://api.openai.com/v1")
        with self.assertRaises(ValueError):
            validate_api_base_url("file:///tmp/audio")
        with self.assertRaises(ValueError):
            validate_api_base_url("https://secret@example.com/v1")

    def test_tts_cleanup_handles_markdown_tables_code_and_math(self) -> None:
        source = "# Итог\n\n| Цена | 25% |\n|---|---|\n| A | $x^2$ |\n```python\nprint('secret')\n```"
        result = prepare_tts_text(source)
        self.assertIn("Цена. 25%", result)
        self.assertIn("Фрагмент кода опущен", result)
        self.assertNotIn("```", result)
        self.assertNotIn("|---|", result)

    def test_catalog_only_accepts_known_model_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runtime = VoiceRuntime(temp)
            self.assertTrue(any(row["id"] == "small" for row in runtime.catalog("small")))
            with self.assertRaises(ValueError):
                runtime.delete_model("../../escape")

    def test_download_installs_atomically_without_loading_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runtime = VoiceRuntime(temp)
            def fake_transfer(_files, directory, _state, _cancel):
                (directory / "model.bin").write_bytes(b"model")
                (directory / "config.json").write_text("{}", encoding="utf-8")
            # The downloader now owns streamed/cancellable transfer instead of
            # faster-whisper's blocking helper. Keep this contract offline.
            with patch("app.voice_downloads.manifest", return_value=[]), patch("app.voice_downloads.transfer", side_effect=fake_transfer):
                runtime._download_local_model("tiny")
            self.assertTrue(runtime._is_downloaded("tiny"))
            self.assertIsNone(runtime._model)

    def test_voice_runtime_never_launches_a_shell(self) -> None:
        source = (ROOT / "app" / "voice_runtime.py").read_text(encoding="utf-8").casefold()
        self.assertNotIn("import subprocess", source)
        self.assertNotIn("subprocess.", source)

    @unittest.skipUnless(os.name == "nt", "Windows SAPI contract")
    def test_windows_tts_produces_a_real_wave(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runtime = VoiceRuntime(temp)
            audio, mime = runtime.speak_windows("MemuAI voice test")
            self.assertEqual(mime, "audio/wav")
            self.assertEqual(audio[:4], b"RIFF")
            self.assertGreater(len(audio), 1000)

    def test_voice_preferences_are_normalized(self) -> None:
        prefs = pgdata._normalize_preferences({"voice_stt_provider": "bad", "voice_tts_rate": 999,
                                               "voice_stt_local_model": "unknown"})
        self.assertEqual(prefs["voice_stt_provider"], "local")
        self.assertEqual(prefs["voice_stt_local_model"], "small")
        self.assertEqual(prefs["voice_tts_rate"], 10)
        self.assertIn(prefs["voice_tts_provider"], ("windows", "local", "api"))
        self.assertEqual(prefs["work_access_mode"], "ask")


class VoiceUiContracts(unittest.TestCase):
    def test_voice_controls_settings_and_endpoints_are_present(self) -> None:
        html = (ROOT / "app" / "pgstatic" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        api = (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8")
        for element_id in ("btn-mic", "btn-live", "live-call", "live-talk"):
            self.assertIn(f'id="{element_id}"', html)
        for endpoint in ("/api/voice/catalog", "/api/voice/transcribe", "/api/voice/speak",
                         "/api/voice/models/download"):
            self.assertIn(endpoint, api)
        self.assertIn("MediaRecorder", js)
        self.assertIn("voice-live", js)
        self.assertIn("speakMessage", js)


if __name__ == "__main__":
    unittest.main()
