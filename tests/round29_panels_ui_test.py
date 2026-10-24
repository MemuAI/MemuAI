"""Round 29 (r29-panels): the headless checks of tests/round29_panels_shots.py (see its docstring) as a unit test.

Skipped without Playwright or a Chromium build (MEMU_TEST_CHROMIUM, /usr/bin/chromium).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Headless(unittest.TestCase):
    def setUp(self):
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("playwright is not installed")
        chrome = os.environ.get("MEMU_TEST_CHROMIUM") or "/workspace/work/chromium/chrome"
        if not os.path.exists(chrome) and not os.path.exists("/usr/bin/chromium"):
            self.skipTest("no Chromium")

    def test_headless_panels(self):
        out = Path(tempfile.mkdtemp(prefix="r29-panels-shots-"))
        try:
            env = dict(os.environ, PYTHONUTF8="1", NO_PROXY="*")
            env.pop("MEMUAI_DATA_DIR", None)
            if not os.environ.get("MEMU_TEST_CHROMIUM") and os.path.exists("/usr/bin/chromium"):
                env["MEMU_TEST_CHROMIUM"] = "/usr/bin/chromium"
            result = subprocess.run([sys.executable, str(ROOT / "tests" / "round29_panels_shots.py"), str(out)],
                                    capture_output=True, text=True, timeout=300, env=env)
            self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-3000:])
        finally:
            shutil.rmtree(out, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
