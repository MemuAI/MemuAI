"""Exercise browser interception and cancellation in a real hidden Edge."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest

class BrowserBoundaryRegressionTest(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows Edge regression")
    def test_prompt_viewer_copy_and_safe_rendering(self):
        self._run("release_prompt_ui_runner.py")

    @unittest.skipUnless(os.name == "nt", "Windows Edge regression")
    def test_network_boundary_and_browser_cleanup(self):
        self._run("release_browser_runner.py")

    @unittest.skipUnless(os.name == "nt", "Windows Edge regression")
    def test_charset_gzip_and_opaque_frame_network_boundary(self):
        self._run("release_browser_charset_runner.py")

    def _run(self, script):
        from app.automation_runtime import _edge_executable
        try:
            _edge_executable()
        except RuntimeError as exc:
            self.skipTest(str(exc))
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, str(root / "tests" / script), str(root)],
            cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=150,
            env={**os.environ, "PYTHONUTF8": "1"},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS:", result.stdout)

if __name__ == "__main__":
    unittest.main()
