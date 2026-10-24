"""Acceptance checks against the actual rendered desktop application."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


class SurfaceTest(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows Edge UI acceptance")
    def test_short_voice_menu_tool_state_and_responsive_settings(self):
        result = subprocess.run([sys.executable, str(Path(__file__).with_name("round17_surface_runner.py"))],
                                capture_output=True, text=True, encoding="utf-8", timeout=90,
                                env={**os.environ, "PYTHONUTF8": "1"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS:", result.stdout)
