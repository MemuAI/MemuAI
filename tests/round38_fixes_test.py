# -*- coding: utf-8 -*-
"""Round 38 (fixes after the full run): the main page keeps no needless stream open.

The MemuMusic chat mini player (module-integration.js, widget «chat.composer») used to open its own SSE
/api/pg/music/player/events about 3 s after boot whenever the music module was on — even if no music ever played.
That stream never ends, so Playwright's «networkidle» sometimes never came (the module browser tests timed out on
page.goto) and the main window held a needless connection. Now the widget takes the state from the host player
(music-dock.js, event «memu-music-state»), which listens to the server only once there is music.
"""
from __future__ import annotations

import os
import shutil
import socket
import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _chromium() -> str:
    for candidate in (os.environ.get("MEMU_TEST_CHROMIUM", ""), "/usr/bin/chromium", "/workspace/work/chromium/chrome",
                      shutil.which("chromium") or "", shutil.which("chromium-browser") or ""):
        if candidate and Path(candidate).exists():
            return candidate
    return ""


class StaticTest(unittest.TestCase):
    def test_chat_widget_has_no_stream_of_its_own(self):
        main = (ROOT / "app/pgstatic/module-integration.js").read_text(encoding="utf-8")
        self.assertNotIn("EventSource", main)
        self.assertIn("memu-music-state", main)
        dock = (ROOT / "app/pgstatic/music-dock.js").read_text(encoding="utf-8")
        self.assertIn('new CustomEvent("memu-music-state"', dock)
        self.assertIn("nowPlaying:", dock)


@unittest.skipUnless(_chromium(), "needs Chromium")
class MainPageNetworkTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
            import uvicorn
        except Exception as exc:  # pragma: no cover
            raise unittest.SkipTest(f"playwright/uvicorn unavailable: {exc}")
        from app import modules_host, pgdata
        from app.playground_api import create_app
        root = modules_host.default_root()
        root.mkdir(parents=True, exist_ok=True)
        modules_host.configure_module(root, "music", enabled=True)
        pgdata.save_preferences({"profile_onboarded": True, "profile_name": "Test"})
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            cls.port = probe.getsockname()[1]
        cls.server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=cls.port, log_level="error"))
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        deadline = time.time() + 60
        while not cls.server.started and time.time() < deadline:
            time.sleep(0.1)
        if not cls.server.started:  # pragma: no cover
            raise unittest.SkipTest("playground server did not start")
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()
        cls.server.should_exit = True
        cls.thread.join(timeout=10)

    def test_no_music_stream_while_no_music_played(self):
        page = self.browser.new_page(viewport={"width": 1366, "height": 768})
        self.addCleanup(page.close)
        urls = []
        page.on("request", lambda req: urls.append(req.url))
        page.goto(f"http://127.0.0.1:{self.port}/", wait_until="networkidle", timeout=30000)
        deadline = time.time() + 20   # the page CSP forbids eval → poll with evaluate(), not wait_for_function
        while not page.evaluate("() => !!(window.MemuModInt && MemuModInt.state().modules > 0)") and time.time() < deadline:
            time.sleep(0.2)
        self.assertTrue(page.evaluate("() => MemuModInt.state().modules > 0"))
        page.wait_for_timeout(4000)   # the dock checks player.state once after idle (~2.5 s + idle)
        self.assertEqual([u for u in urls if "/api/pg/music/player/events" in u], [])
        self.assertFalse(page.evaluate("() => MemuMusicDock.state().booted"))


if __name__ == "__main__":
    unittest.main()
