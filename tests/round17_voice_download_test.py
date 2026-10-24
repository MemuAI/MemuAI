from pathlib import Path
import shutil
import subprocess
import unittest

class VoiceDownloadTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node runtime unavailable")
    def test_voice_file_bridge_and_profile_guards(self):
        runner = Path(__file__).with_name("round17_voice_download_runner.cjs")
        result = subprocess.run(["node", str(runner)], capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS:", result.stdout)
