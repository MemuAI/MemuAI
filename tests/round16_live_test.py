"""Run production JavaScript turn/ownership checks and provider-ID regression."""
import shutil
import os
import sys
import subprocess
import unittest
from pathlib import Path

from app import realtime_live

class LiveVoiceTest(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows Edge regression")
    def test_module_browser_pages(self):
        from app.automation_runtime import _edge_executable
        try:
            _edge_executable()
        except RuntimeError as exc:
            self.skipTest(str(exc))
        runner = Path(__file__).with_name("round16_browser_runner.py")
        result = subprocess.run([sys.executable, str(runner)], capture_output=True, text=True,
                                encoding="utf-8", timeout=60, env={**os.environ, "PYTHONUTF8": "1"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS:", result.stdout)

    @unittest.skipUnless(shutil.which("node"), "Node runtime unavailable")
    def test_live_turns_and_async_ownership(self):
        runner = Path(__file__).with_name("round16_live_runner.cjs")
        result = subprocess.run([shutil.which("node"), str(runner)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)

    def test_provider_item_ids_survive_normalization(self):
        config = realtime_live.LiveConfig.from_preferences({"voice_realtime_provider": "openai-realtime", "voice_realtime_api_keys": {"openai-realtime": "fixture"}}, "Persona")
        adapter = realtime_live.build_adapter(config)
        events = [
            {"type": "input_audio_buffer.speech_started", "item_id": "u1"},
            {"type": "input_audio_buffer.speech_stopped", "item_id": "u1"},
            {"type": "conversation.item.input_audio_transcription.completed", "item_id": "u1", "transcript": "Hi"},
            {"type": "response.output_audio.delta", "item_id": "a1", "delta": "AAAA"},
            {"type": "response.output_audio_transcript.delta", "item_id": "a1", "delta": "Hello"},
        ]
        for event in events:
            with self.subTest(event=event):
                self.assertEqual(adapter.translate(event)[0]["turn_id"], event["item_id"])

if __name__ == "__main__":
    unittest.main()
