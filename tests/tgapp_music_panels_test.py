"""Tests for the Telegram Mini App (MemuAI): music parity, the Now Playing
"..." menu, and panels (clock + reorder).

Two layers:
  1. `node --check` for every changed JS file, plus a CRLF check for the
     tgapp sources (they must keep CRLF line endings).
  2. The jsdom DOM harness in tests/tgapp_dom_harness.mjs, which boots the
     real tgapp scripts (app.js, music.js, panels.js) against fixture data
     and replays clicks: menu dedup/z-index/close, lyrics romanize/offset/
     tap-sync/words, panels clock tick/reorder/retry, network-failure
     guards, and a zero-unhandled-exceptions check.

The harness needs the `jsdom` package (see tests/tgapp_dom_harness.mjs for
where it looks for it). When jsdom is unavailable the harness reports a
single skipped result and this test skips instead of failing.

Run:  python3 -m unittest tests.tgapp_music_panels_test -v
      (from the MemuAI checkout root)
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TGAPP = ROOT / "app" / "tgapp"

JS_FILES = ["app.js", "music.js", "panels.js"]
CRLF_FILES = ["app.js", "music.js", "panels.js", "app.css", "music.css"]


class SyntaxAndEndingsTests(unittest.TestCase):
    def test_node_check_changed_js(self):
        for name in JS_FILES:
            path = TGAPP / name
            proc = subprocess.run(
                ["node", "--check", str(path)],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(proc.returncode, 0, f"node --check {name}: {proc.stderr}")

    def test_crlf_preserved(self):
        for name in CRLF_FILES:
            raw = (TGAPP / name).read_bytes()
            self.assertIn(b"\r\n", raw, f"{name} lost its CRLF endings")
            self.assertEqual(
                raw.count(b"\n"), raw.count(b"\r\n"),
                f"{name} has bare LF line endings mixed in",
            )


class DomHarnessTests(unittest.TestCase):
    def test_dom_harness_all_scenarios_pass(self):
        harness = ROOT / "tests" / "tgapp_dom_harness.mjs"
        proc = subprocess.run(
            ["node", str(harness)],
            capture_output=True, text=True, timeout=180, cwd=str(ROOT),
        )
        try:
            results = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"harness did not emit JSON (exit {proc.returncode}):\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
        if len(results) == 1 and "skip" in str(results[0].get("name", "")).lower():
            self.skipTest(f"jsdom unavailable: {results[0].get('details', '')}")
        self.assertEqual(proc.returncode, 0, f"harness exit {proc.returncode}: {proc.stderr[-1000:]}")
        failed = [r for r in results if not r.get("pass")]
        self.assertEqual(failed, [], "failing scenarios:\n" + "\n".join(
            f"- {r['name']}: {r.get('details', '')}" for r in failed))
        self.assertGreater(len(results), 10, "harness ran suspiciously few scenarios")


if __name__ == "__main__":
    unittest.main()
