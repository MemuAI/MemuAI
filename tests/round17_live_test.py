"""Regression proof for short voice clips and persona transport, without keys."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

from app import realtime_live

class LiveVoiceRegressionTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node runtime unavailable")
    def test_short_recordings_and_behavioral_personality(self):
        runner = Path(__file__).with_name("round17_live_runner.cjs")
        result = subprocess.run([shutil.which("node"), str(runner)], capture_output=True,
                                encoding="utf-8", timeout=30, env={**os.environ, "PYTHONUTF8": "1"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Round17 Live PASS", result.stdout)

    def test_selected_personality_reaches_every_provider_setup(self):

        persona = '<live_personality primary="romantic">PRIMARY BEHAVIOR: tender, personal and gently playful. USER-CHOSEN NUANCE: no pet names.</live_personality>'
        for provider in ("openai-realtime", "openai-live", "gemini", "xai"):
            with self.subTest(provider=provider):
                config = realtime_live.LiveConfig.from_preferences({
                    "voice_realtime_provider": provider,
                    "voice_realtime_api_keys": {provider: "fixture-no-real-key"},
                    "voice_language": "ru",
                }, persona)
                adapter = realtime_live.build_adapter(config)
                setup = adapter.setup_messages()[0]
                if provider == "gemini":
                    instructions = setup["setup"]["systemInstruction"]["parts"][0]["text"]
                else:
                    instructions = setup["session"]["instructions"]
                self.assertTrue(instructions.startswith(persona))
                self.assertNotIn("warmly", instructions[len(persona):])
                self.assertIn("no pet names", json.dumps(setup))

if __name__ == "__main__":
    unittest.main()
