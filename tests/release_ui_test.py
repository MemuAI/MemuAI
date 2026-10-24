"""Run the real UI functions with controllable chat and media lifecycles."""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


class UIAsyncOwnershipRegressionTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required for UI runtime regressions")
    def test_async_results_belong_to_source_chat_and_live_session(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([shutil.which("node"), str(root / "tests" / "release_ui_runner.cjs"), str(root)],
                                capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS:", result.stdout)


if __name__ == "__main__":
    unittest.main()
