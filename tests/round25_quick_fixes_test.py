"""Round 25 (a6-quick, second pass): Quick Ask page fixes, voice alignment with tts_providers (a7-tts),
test isolation, DFY secret contract in the R24 window checks (a3-dfy).

    cd /workspace/work/memuai && PYTHONUTF8=1 python3 -m unittest tests.round25_quick_fixes_test
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from app import assistant_commands
from app import assistant_intents as intents
from app import assistant_r24 as r24

ROOT = Path(__file__).resolve().parents[1]
QUICK = ROOT / "app" / "pgstatic" / "quick"

def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")

class PageFixes(unittest.TestCase):
    def setUp(self) -> None:
        self.js = read(QUICK / "quick.js")

    def test_stream_tail_is_delivered(self) -> None:
        block = self.js[self.js.index("async function readEvents"):self.js.index("async function runClientTool")]
        self.assertIn("buffer += decoder.decode();", block, "the decoder is flushed")
        self.assertIn("if (buffer.trim()) dispatch(buffer);", block, "the last event without a blank line is not lost")

    def test_retry_while_busy_keeps_the_turn(self) -> None:
        block = self.js[self.js.index("function retry("):self.js.index("function askAdvanced(")]
        self.assertLess(block.index("if (S.busy) return;"), block.index("S.turns.splice("))

    def test_screen_idea_needs_the_screenshot(self) -> None:
        self.assertIn("takeScreenshot(false).then((ok) => { if (ok) {", self.js)
        block = self.js[self.js.index("async function takeScreenshot"):self.js.index("function readImageFile")]
        self.assertIn("return true;", block)
        self.assertIn("return false;", block)

    def test_unreadable_image_is_reported(self) -> None:

        self.assertIn("Картинка слишком большая (больше 10 МБ)", self.js)
        self.assertIn("The image is too large (over 10 MB)", self.js)

    def test_fresh_window_tells_plugins(self) -> None:
        block = self.js[self.js.index("if (payload.fresh) {"):]
        block = block[:block.index("if (!S.chatId)")]
        self.assertIn('emit("chat-new"', block, "old command cards are dropped with the old conversation")

    def test_cache_busters(self) -> None:
        html = read(QUICK / "index.html")
        for old in ('quick-dfy.js?v=1.0"', 'quick-dfy.css?v=1.0"', 'dfy-client.js?v=1"', 'quick.js?v=3.1"', 'quick.css?v=3.1"'):
            self.assertNotIn(old, html, old)

    def test_base64_clipboard_phrase(self) -> None:
        found = intents.match("base64 буфер", now=datetime(2026, 10, 8, 12, 0))
        self.assertEqual((found["action"], found["params"]), ("text.encode", {"codec": "base64", "op": "auto"}))
        self.assertIsNone(intents.match("base64", now=datetime(2026, 10, 8, 12, 0)), "a bare word stays a question")

    def test_js_syntax(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node missing")
        result = subprocess.run([node, "--check", str(QUICK / "quick.js")], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

class VoiceAlignment(unittest.TestCase):
    """a7-tts: the voice status names the cloud service and needs its key; the voice override of Quick Access
    reaches the provider actually used."""

    def test_status_names_service_and_needs_key(self) -> None:
        prefs = {"voice_tts_api_url": "https://generativelanguage.googleapis.com", "voice_tts_api_model": "gemini-3.8-flash-lite-tts"}
        st = r24.voice_status(prefs, {"tts_provider": "api"}, [], [], [], "win32")["tts"]
        self.assertFalse(st["ready"])
        self.assertEqual((st["service"], st["fix"], st["voice"]), ("gemini", "open_voice", "Kore"))
        self.assertIn("Google Gemini", st["label"])
        self.assertIn("API-ключ", st["problem"])
        st = r24.voice_status(dict(prefs, voice_tts_api_key="k"), {"tts_provider": "api"}, [], [], [], "win32")["tts"]
        self.assertTrue(st["ready"])
        custom = r24.voice_status({"voice_tts_api_url": "http://127.0.0.1:8880/v1"}, {"tts_provider": "api"}, [], [], [], "win32")["tts"]
        self.assertTrue(custom["ready"], "an OpenAI-compatible server of your own may need no key")
        nourl = r24.voice_status({}, {"tts_provider": "api"}, [], [], [], "win32")["tts"]
        self.assertIn("адрес", nourl["problem"])

    def test_voice_override_goes_to_the_provider_in_use(self) -> None:
        class Store:
            def __init__(self, voice):
                self.voice = voice

            def load(self):
                return {"settings": {"voice": self.voice}}
        base = {"voice_tts_provider": "local", "voice_tts_api_voice": "alloy", "voice_tts_local_voice": "af_bella"}
        out = assistant_commands.voice_prefs(base, {"quick": True}, Store({"tts_api_voice": "am_adam"}))
        self.assertEqual((out["voice_tts_local_voice"], out["voice_tts_api_voice"]), ("am_adam", "alloy"))
        out = assistant_commands.voice_prefs(base, {"quick": True}, Store({"tts_provider": "api", "tts_api_voice": "nova"}))
        self.assertEqual((out["voice_tts_api_voice"], out["voice_tts_local_voice"]), ("nova", "af_bella"))

class Contracts(unittest.TestCase):
    def test_assistant_tests_leave_no_global_tool(self) -> None:
        src = read(ROOT / "tests" / "assistant_commands_test.py")
        self.assertIn("def tearDownModule", src)
        self.assertIn("quick_ask_api.EXTRA_TOOLS.clear()", src)

    def test_r24_window_checks_use_the_secret_contract(self) -> None:
        src = read(ROOT / "tests" / "round24_quick_window_shots.py")
        self.assertIn('.get("secret") or {}).get("fields", {}).get("password") == "hunter2"', src)

class Headless(unittest.TestCase):
    def test_flow(self) -> None:
        """tests/round25_quick_fixes_shots.py when Playwright + Chromium are available."""
        chrome = "/usr/bin/chromium" if os.path.exists("/usr/bin/chromium") else "/workspace/work/chromium/chrome"
        try:
            import playwright  # noqa: F401
        except Exception:
            self.skipTest("playwright missing")
        if not os.path.exists(chrome):
            self.skipTest("chromium missing")
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out, True)
        env = dict(os.environ, NO_PROXY="*", no_proxy="*", PYTHONUTF8="1")
        result = subprocess.run([sys.executable, str(ROOT / "tests" / "round25_quick_fixes_shots.py"), out],
                                capture_output=True, text=True, timeout=110, env=env, cwd=str(ROOT))
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])

if __name__ == "__main__":
    unittest.main()
