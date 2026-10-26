"""Round 38 (r38-quick-esc): Escape in Quick Access must close the window at once.

The bug: «closed with Escape — the menu turned white and shimmering for ~20 s, then closed». The page faded the card
out and waited for the server; the server's hide() first ran synchronous evaluate_js calls (pywebview waits up to
20 s for a page that is not ready) and the screen layer (its lock is held while the layer window is created) and only
then hid the window — meanwhile the transparent window showed bare Windows 11 glass.

Without Windows: the native manager is driven with fakes, the real quick/index.html runs in headless Chromium with
a mocked /api (slow server, pywebview bridge present / hanging, double Escape, Escape right after opening, a late
onHide of an older opening).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="memu-r38-quick-esc-"))

CHROME = os.environ.get("MEMU_TEST_CHROMIUM") or "/workspace/work/chromium/chrome"
QUICK = ROOT / "app" / "pgstatic" / "quick"

class Api:
    def __init__(self, log):
        self.log = log

    def ShowWindowAsync(self, hwnd, cmd):
        self.log.append(("ShowWindowAsync", hwnd, cmd))
        return True

    def take_foreground(self, hwnd):
        self.log.append(("focus", hwnd))
        return True

    def IsWindow(self, hwnd):
        return True

    def IsIconic(self, hwnd):
        return False

def manager(log, *, layer=None, push=None):
    from app.quick_ask import QuickAskManager
    m = QuickAskManager.__new__(QuickAskManager)
    m.api, m.hwnd, m.visible, m.pinned, m.lowered = Api(log), 100, True, False, False
    m._lock = threading.RLock()
    m._anim_token = m._selection_token = m._layer_token = 0
    m._open_seq = 3
    m.previous_hwnd = 0
    m.form = object()
    m.layer = layer
    m._on_ui = lambda fn: log.append(("form.Hide",))
    m._disarm_escape = lambda: None
    m._push = push or (lambda script: log.append(("push", script)))
    return m

class NativeHide(unittest.TestCase):
    def test_window_is_hidden_before_any_page_round_trip(self):
        log = []
        release = threading.Event()

        def stuck_push(script):
            release.wait(5)
            log.append(("push", script))
        m = manager(log, push=stuck_push)
        t0 = time.monotonic()
        self.assertTrue(m.hide(restore_focus=False))
        took = time.monotonic() - t0
        self.assertLess(took, 0.2, "hide() must not wait for evaluate_js")
        self.assertFalse(m.visible)
        self.assertEqual(log[:2], [("ShowWindowAsync", 100, 0), ("form.Hide",)])
        release.set()
        for _ in range(50):
            if any(e[0] == "push" for e in log):
                break
            time.sleep(0.02)
        self.assertIn(("push", "window.QuickAsk && QuickAsk.onHide(3)"), log)

    def test_slow_layer_never_keeps_the_window_on_screen(self):
        log = []
        gate = threading.Event()

        class SlowLayer:
            visible = True

            def hide(self):
                gate.wait(5)
                log.append(("layer.hide",))
                self.visible = False
                return True
        m = manager(log, layer=SlowLayer())
        t = threading.Thread(target=m.hide)
        t.start()
        time.sleep(0.15)
        self.assertEqual(log[:2], [("ShowWindowAsync", 100, 0), ("form.Hide",)],
                         "the popup is already off the screen while the layer is still busy")
        gate.set()
        t.join(3)
        names = [e[0] for e in log]
        self.assertLess(names.index("form.Hide"), names.index("layer.hide"))

    def test_late_onhide_is_skipped_when_shown_again(self):
        log = []
        m = manager(log)
        m.visible = True
        m._tell_hidden(2)
        self.assertEqual([e for e in log if e[0] == "push"], [])
        m.visible = False
        m._tell_hidden(2)
        self.assertEqual([e for e in log if e[0] == "push"], [("push", "window.QuickAsk && QuickAsk.onHide(2)")])

    def test_hidden_twice_is_harmless(self):
        log = []
        m = manager(log)
        m.visible = False
        self.assertTrue(m.hide())
        self.assertEqual(log, [], "an already hidden window: nothing is touched (double Escape: hook + page)")

    def test_layer_hide_does_not_wait_for_its_page_or_lock(self):
        from app.quick_layer import ScreenLayer
        log = []
        layer = ScreenLayer.__new__(ScreenLayer)
        layer.manager = type("M", (), {"api": Api(log)})()
        layer.hwnd, layer.visible, layer.edit, layer._region_key = 300, True, False, "k"
        layer._lock = threading.RLock()
        layer.form = object()
        layer._ui = lambda fn: log.append(("layer.form.Hide",))
        release = threading.Event()
        layer._push = lambda script: (release.wait(5), log.append(("layer.push", script)))
        layer._lock.acquire()
        t = threading.Thread(target=layer.hide)
        t.start()
        time.sleep(0.1)
        self.assertEqual(log, [("ShowWindowAsync", 300, 0)], "off the screen before the lock is free")
        layer._lock.release()
        t.join(2)
        self.assertFalse(t.is_alive(), "hide() returns without waiting for the layer page")
        self.assertFalse(layer.visible)
        release.set()

    def test_slow_close_is_logged_with_its_step(self):
        from app.quick_ask import slow_close_report
        self.assertIsNone(slow_close_report(0.0, [("async_hide", 0.01), ("form_hide", 0.05)], "server"))
        with self.assertLogs("memuai.quick", level=logging.WARNING) as cap:
            line = slow_close_report(0.0, [("async_hide", 0.01), ("lock", 0.02), ("layer", 20.5)], "server")
        self.assertIn("closing took 20500 ms", line)
        self.assertIn("slowest: layer", line)
        self.assertTrue(any("slowest: layer" in r for r in cap.output))

    def test_bridge_exposes_only_hide(self):
        from app.quick_ask import QuickBridge
        calls = []
        fake = type("M", (), {"hide": lambda self, restore=True: calls.append(restore) or True})()
        bridge = QuickBridge(fake)
        public = [n for n in dir(bridge) if not n.startswith("_")]

        self.assertEqual(public, ["hide", "resize"], "pywebview exposes every public attribute recursively — never the manager")
        self.assertTrue(bridge.hide(True))
        self.assertEqual(calls, [True])
        src = (ROOT / "app" / "quick_ask.py").read_text("utf-8")
        self.assertIn("js_api=QuickBridge(self)", src)

    def test_api_logs_a_slow_close_from_the_page(self):
        src = (ROOT / "app" / "quick_ask_api.py").read_text("utf-8")
        self.assertIn('if action == "close_slow":', src)

def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)

class PageEscape(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Path(CHROME).exists():
            raise unittest.SkipTest("no Chromium")
        try:
            import playwright  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("no Playwright")
        import functools
        import http.server

        class Quiet(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass

        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(ROOT / "app" / "pgstatic")))
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def run_page(self, script: str, *, hide_delay: float = 0.0, before: str = ""):
        from playwright.async_api import async_playwright
        from app import themes
        config = {"prefs": {"theme": "dark", "ui_design": "classic", "language": "ru"},
                  "status": {"native": True, "pinned": False}, "ui": {"onboarded": True}, "endpoint": {"source": "proxy"}}
        actions = []

        async def route(route_obj, request):
            path = "/" + request.url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]
            if path == "/themes.css":
                return await route_obj.fulfill(status=200, content_type="text/css", body=themes.theme_css())
            if path == "/api/quick/config":
                return await route_obj.fulfill(json=config)
            if path == "/api/quick/window":
                body = json.loads(request.post_data or "{}")
                actions.append(body)
                if body.get("action") == "hide" and hide_delay:
                    await asyncio.sleep(hide_delay)
                return await route_obj.fulfill(json={"ok": True})
            if path.startswith("/api/"):
                return await route_obj.fulfill(json={"items": [], "models": [], "image_models": [], "ok": True})
            return await route_obj.continue_()

        async def main():
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
                page = await browser.new_page(viewport={"width": 720, "height": 480})
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                await page.route("**/*", route)
                if before:
                    await page.add_init_script(before)
                await page.goto(self.base + "/quick/?native=1", wait_until="load")
                await page.wait_for_function("window.QuickAsk && window.MemuQuick")
                await page.evaluate("QuickAsk.onShow({fresh: true, maxHeight: 480, seq: 1, backdrop: 'acrylic'})")
                result = await page.evaluate(script)
                await browser.close()
                return result, errors
        result, errors = run(main())
        self.assertEqual(errors, [])
        return result, actions

    ESC = "document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true, cancelable: true}));"
    WAIT = "const wait = (ms) => new Promise((r) => setTimeout(r, ms));"

    def hides(self, actions):
        return [a for a in actions if a.get("action") == "hide"]

    def test_slow_server_never_leaves_bare_glass(self):
        script = """(async () => { %s await wait(500); %s
          await wait(80);
          const card = document.getElementById('qa-card'), inner = document.getElementById('qa-inner');
          const during = {card: getComputedStyle(card).opacity, leaving: card.classList.contains('qa-leaving')};
          await wait(700);
          return {during, after: {leaving: card.classList.contains('qa-leaving'), card: getComputedStyle(card).opacity,
                  inner: getComputedStyle(inner).opacity}}; })()""" % (self.WAIT, self.ESC)
        result, actions = self.run_page(script, hide_delay=3.0)
        self.assertEqual(result["during"], {"card": "1", "leaving": True}, "only the content fades — the card covers the glass")
        self.assertEqual(result["after"], {"leaving": False, "card": "1", "inner": "1"},
                         "the server did not answer in time: the content comes back instead of a white window")
        self.assertEqual(len(self.hides(actions)), 1)
        slow = [a for a in actions if a.get("action") == "close_slow"]
        self.assertEqual(len(slow), 1)
        self.assertIn("not-hidden", slow[0]["reason"])

    def test_bridge_hides_without_the_server(self):
        before = "window.__hides = 0; window.pywebview = {api: {hide: async () => { window.__hides++; return true; }}};"
        script = "(async () => { %s await wait(400); %s await wait(400); return window.__hides; })()" % (self.WAIT, self.ESC)
        result, actions = self.run_page(script, before=before)
        self.assertEqual(result, 1)
        self.assertEqual(self.hides(actions), [], "pywebview's channel, not the HTTP server")
        self.assertEqual([a for a in actions if a.get("action") == "close_slow"], [])

    def test_hanging_bridge_falls_back_to_the_server(self):
        before = "window.pywebview = {api: {hide: () => new Promise(() => {})}};"
        script = "(async () => { %s await wait(400); %s await wait(900); return document.getElementById('qa-card').classList.contains('qa-leaving'); })()" % (self.WAIT, self.ESC)
        result, actions = self.run_page(script, before=before)
        self.assertFalse(result)
        self.assertEqual(len(self.hides(actions)), 1)

    def test_double_escape_and_escape_right_after_opening(self):
        script = "(async () => { %s %s %s await wait(30); %s await wait(600); })()" % (self.WAIT, self.ESC, self.ESC, self.ESC)
        _, actions = self.run_page(script)
        self.assertEqual(len(self.hides(actions)), 1, "one close per Escape burst, even during the opening animation")

    def test_late_onhide_of_an_older_opening_is_ignored(self):
        script = """(async () => { %s await wait(300);
          QuickAsk.onShow({maxHeight: 480, seq: 2});
          const stale = QuickAsk.onHide(1);
          const card = document.getElementById('qa-card');
          const kept = !card.classList.contains('qa-closed');
          const fresh = QuickAsk.onHide(2);
          return {stale, kept, fresh, closed: card.classList.contains('qa-closed')}; })()""" % self.WAIT
        result, _ = self.run_page(script)
        self.assertEqual(result, {"stale": False, "kept": True, "fresh": True, "closed": True})

class StaticContracts(unittest.TestCase):
    def test_wiring(self):
        js = (QUICK / "quick.js").read_text("utf-8")
        css = (QUICK / "quick.css").read_text("utf-8")
        html = (QUICK / "index.html").read_text("utf-8")
        self.assertIn("CLOSE_SAFETY_MS = 300", js)
        self.assertNotIn("await new Promise((r) => setTimeout(r, 110)); }\n    await windowAction(\"hide\")", js)
        self.assertIn(':root[data-native="1"] .qa-card.qa-leaving .qa-inner { opacity: 0;', css)
        self.assertRegex(html, r'/quick/quick\.js\?v=[\w.]+-r(?:3[8-9]|4\d)"')
        self.assertIn(':root[data-native="1"]:not([data-backdrop])[data-theme="dark"]{background:#1b1c20}', html)

if __name__ == "__main__":
    unittest.main()
