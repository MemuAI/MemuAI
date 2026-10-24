"""Round 20: custom title bar page logic in headless Chromium.

window.pywebview.api is mocked; every call is recorded so the test can check
what the page asks the native side to do. Skipped when Playwright or a
Chromium build is not installed.
"""
from __future__ import annotations

import os
import shutil
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "pgstatic"
ORIGIN = "http://memu.test"

FIXTURE = """<!DOCTYPE html><html lang="ru" data-theme="dark" data-design="classic"><head>
<meta charset="utf-8">
<style>
 html,body{margin:0;height:100%} #app{display:flex;height:100vh}
 .side{width:240px} .main{flex:1;display:flex;flex-direction:column;margin:8px}
 .topbar.appbar{display:flex;align-items:center;gap:6px;min-height:54px;padding:8px 14px;position:relative;box-sizing:border-box}
 .grow{flex:1} button{width:36px;height:36px;cursor:pointer} .bar-icon{cursor:pointer}
 .content{flex:1}
</style>
<link rel="stylesheet" href="/titlebar.css"></head><body>
<div id="app">
 <aside class="side"><div class="side-top"><div class="brand"><span class="brand-dot"></span>MemuAI</div></div>
   <div class="chat-row" id="chat-row" style="height:40px">chat</div></aside>
 <main class="main">
  <header class="topbar appbar">
   <button class="bar-icon" id="bar-menu">m</button>
   <label class="chat-search"><input id="chat-search"></label>
   <div class="dropdown" id="model-dd"><span id="model-current">Model</span></div>
   <span id="plain-text">text</span>
   <div class="grow" id="grow"></div>
   <button class="iconbtn" id="btn-theme">t</button>
  </header>
  <div class="content" id="content">content</div>
 </main>
</div>
<div class="panel-back" id="overlay" style="position:fixed;inset:0;z-index:200;display:none;background:rgba(0,0,0,.4)">
 <div class="panel" style="margin:auto;width:300px;height:200px;background:#333"></div></div>
<script>
 window.__calls = [];
 window.__state = null;
 window.installMock = function (state) {
   window.__state = Object.assign({ ok: true, supported: true, mode: "custom", installed: true, pending: false,
     maximized: false, fullscreen: false, active: true, os: "win11", doubleClickMs: 500, forced: false }, state || {});
   const rec = (name) => (...args) => { window.__calls.push([name, ...args]); return Promise.resolve(true); };
   window.pywebview = { api: {
     titlebar_state: (...a) => { window.__calls.push(["titlebar_state"]); return Promise.resolve(Object.assign({}, window.__state)); },
     titlebar_set_mode: (mode) => { window.__calls.push(["titlebar_set_mode", mode]);
       window.__state.installed = mode === "custom"; window.__state.mode = mode; return Promise.resolve(Object.assign({}, window.__state)); },
     titlebar_drag: rec("titlebar_drag"), titlebar_command: rec("titlebar_command"),
     titlebar_system_menu: rec("titlebar_system_menu"),
   } };
   window.dispatchEvent(new Event("pywebviewready"));
 };
</script>
<script src="/titlebar.js"></script>
</body></html>"""


def _chromium() -> str:
    for candidate in (os.environ.get("MEMU_TEST_CHROMIUM", ""), "/usr/bin/chromium", "/workspace/work/chromium/chrome",
                      shutil.which("chromium") or "", shutil.which("chromium-browser") or ""):
        if candidate and Path(candidate).exists():
            return candidate
    return ""


@unittest.skipUnless(_chromium(), "Chromium is not installed")
class TitlebarBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # pragma: no cover
            raise unittest.SkipTest(f"playwright unavailable: {exc}")
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()

    def setUp(self):
        self.page = self.browser.new_page(viewport={"width": 1200, "height": 700})
        self.errors = []
        self.page.on("pageerror", lambda exc: self.errors.append(str(exc)))

        def route(r):
            path = r.request.url[len(ORIGIN):].split("?")[0]
            if path in ("/", "/index.html"):
                return r.fulfill(status=200, content_type="text/html", body=FIXTURE)
            file = STATIC / path.lstrip("/")
            if file.is_file():
                kind = "text/css" if file.suffix == ".css" else "application/javascript"
                return r.fulfill(status=200, content_type=kind, body=file.read_bytes())
            return r.fulfill(status=404, body="")
        self.page.route(f"{ORIGIN}/**", route)
        self.page.goto(ORIGIN + "/")

    def tearDown(self):
        self.page.close()
        self.assertEqual(self.errors, [])

    # helpers
    def install(self, **state):
        self.page.evaluate("(s) => installMock(s)", state)
        self.page.wait_for_function("document.documentElement.dataset.titlebar === 'custom'", timeout=3000)
        self.page.wait_for_timeout(50)

    def calls(self, name=None):
        rows = self.page.evaluate("window.__calls")
        return [row for row in rows if name is None or row[0] == name]

    def clear(self):
        self.page.evaluate("window.__calls.length = 0")

    def press(self, selector, button="left", position=None):
        box = self.page.locator(selector).bounding_box()
        x = box["x"] + (position[0] if position else box["width"] / 2)
        y = box["y"] + (position[1] if position else box["height"] / 2)
        self.page.mouse.move(x, y)
        self.page.mouse.down(button=button)
        self.page.mouse.up(button=button)

    # tests
    def test_browser_preview_has_no_controls(self):
        self.page.wait_for_timeout(200)
        self.assertIsNone(self.page.evaluate("document.documentElement.dataset.titlebar || null"))
        self.assertEqual(self.page.locator("#memu-wincontrols").count(), 0)

    def test_hook_not_installed_keeps_native(self):
        self.page.evaluate("installMock({installed: false})")
        self.page.wait_for_timeout(200)
        self.assertEqual(self.page.locator("#memu-wincontrols").count(), 0)
        self.assertEqual(len(self.calls("titlebar_state")), 1)

    def test_pending_state_is_polled_until_installed(self):
        self.page.evaluate("installMock({installed: false, pending: true})")
        self.page.wait_for_timeout(300)
        self.assertEqual(self.page.locator("#memu-wincontrols").count(), 0)
        self.page.evaluate("window.__state.pending = false; window.__state.installed = true")
        self.page.wait_for_function("!!document.getElementById('memu-wincontrols')", timeout=3000)

    def test_controls_render_and_reserve_space(self):
        self.install()
        self.assertEqual(self.page.locator("#memu-wincontrols .memu-wc-btn").count(), 3)
        geometry = self.page.evaluate("""() => {
          const wc = document.getElementById('memu-wincontrols').getBoundingClientRect();
          const bar = document.querySelector('.topbar.appbar').getBoundingClientRect();
          const theme = document.getElementById('btn-theme').getBoundingClientRect();
          return {wcRight: wc.right, wcTop: wc.top, wcH: wc.height, wcW: wc.width,
                  barRight: bar.right, barTop: bar.top, themeRight: theme.right};
        }""")
        self.assertAlmostEqual(geometry["wcRight"], geometry["barRight"], delta=1)
        self.assertAlmostEqual(geometry["wcTop"], geometry["barTop"], delta=1)
        self.assertEqual(geometry["wcW"], 46 * 3)
        self.assertLessEqual(geometry["themeRight"], geometry["wcRight"] - geometry["wcW"])
        labels = self.page.eval_on_selector_all("[data-wc]", "els => els.map(e => e.getAttribute('aria-label'))")
        self.assertEqual(labels, ["Свернуть", "Развернуть", "Закрыть"])

    def test_drag_on_empty_bar_not_on_controls(self):
        self.install()
        self.clear()
        self.press("#grow")
        self.assertEqual(self.calls("titlebar_drag"), [["titlebar_drag", "caption"]])
        self.page.wait_for_timeout(600)   # leave the double-click window
        for selector in ("#bar-menu", "#chat-search", "#model-current", "#btn-theme", "#content", "#chat-row"):
            self.clear()
            self.press(selector)
            self.assertEqual(self.calls("titlebar_drag"), [], selector)
        # plain text inside the bar and the sidebar brand drag the window
        for selector in ("#plain-text", ".brand"):
            self.page.wait_for_timeout(600)
            self.clear()
            self.press(selector)
            self.assertEqual(self.calls("titlebar_drag"), [["titlebar_drag", "caption"]], selector)

    def test_right_button_and_modifiers_do_not_drag(self):
        self.install()
        self.clear()
        self.press("#grow", button="right")
        self.assertEqual(self.calls("titlebar_drag"), [])
        self.assertEqual(self.calls("titlebar_system_menu"), [["titlebar_system_menu", "mouse"]])

    def test_double_press_toggles_maximize(self):
        self.install()
        self.clear()
        self.press("#grow")
        self.press("#grow")
        self.assertEqual(self.calls("titlebar_drag"), [["titlebar_drag", "caption"]])
        self.assertEqual(self.calls("titlebar_command"), [["titlebar_command", "toggle"]])

    def test_buttons_send_commands(self):
        self.install()
        self.clear()
        self.page.click("[data-wc=minimize]")
        self.page.click("[data-wc=maximize]")
        self.page.click("[data-wc=close]")
        self.assertEqual(self.calls("titlebar_command"), [
            ["titlebar_command", "minimize"], ["titlebar_command", "toggle"], ["titlebar_command", "close"]])
        self.assertEqual(self.calls("titlebar_drag"), [])

    def test_maximized_icon_and_label_follow_state(self):
        self.install()
        self.assertFalse(self.page.evaluate("document.querySelector('[data-wc=maximize]').classList.contains('is-maximized')"))
        self.page.evaluate("window.__state.maximized = true")
        self.page.set_viewport_size({"width": 1210, "height": 700})   # a maximize resizes the page
        self.page.wait_for_function("document.querySelector('[data-wc=maximize]').classList.contains('is-maximized')", timeout=2000)
        self.assertEqual(self.page.get_attribute("[data-wc=maximize]", "aria-label"), "Восстановить")
        self.assertEqual(self.page.evaluate("document.documentElement.dataset.titlebarMax"), "true")
        self.assertFalse(self.page.locator(".memu-tb-resize").is_visible())
        restore_visible = self.page.evaluate("getComputedStyle(document.querySelector('.memu-wc-ico.restore')).display")
        self.assertNotEqual(restore_visible, "none")

    def test_top_edge_resize_strip(self):
        self.install()
        self.clear()
        self.page.mouse.move(600, 2)
        self.assertEqual(self.page.evaluate("getComputedStyle(document.elementFromPoint(600, 2)).cursor"), "ns-resize")
        self.page.mouse.down(); self.page.mouse.up()
        self.page.mouse.move(1195, 2)
        self.page.mouse.down(); self.page.mouse.up()
        self.page.mouse.move(3, 1)
        self.page.mouse.down(); self.page.mouse.up()
        self.assertEqual(self.calls("titlebar_drag"), [
            ["titlebar_drag", "top"], ["titlebar_drag", "topright"], ["titlebar_drag", "topleft"]])

    def test_alt_space_opens_system_menu(self):
        self.install()
        self.clear()
        self.page.keyboard.press("Alt+Space")
        self.assertEqual(self.calls("titlebar_system_menu"), [["titlebar_system_menu", "keyboard"]])

    def test_inactive_and_fullscreen_flags(self):
        self.install(active=False, fullscreen=True)
        self.assertEqual(self.page.evaluate("document.documentElement.dataset.titlebarActive"), "false")
        self.assertFalse(self.page.locator("#memu-wincontrols").is_visible())

    def test_switch_to_native_removes_everything(self):
        self.install()
        self.page.evaluate("MemuTitlebar.setMode('native')")
        self.page.wait_for_function("!document.documentElement.dataset.titlebar", timeout=2000)
        self.assertEqual(self.page.locator("#memu-wincontrols, .memu-tb-resize").count(), 0)
        self.assertEqual(self.page.evaluate("getComputedStyle(document.querySelector('.topbar')).paddingRight"), "14px")
        self.clear()
        self.press("#grow")
        self.assertEqual(self.calls("titlebar_drag"), [])

    def test_overlay_moves_buttons_to_caption_band(self):
        self.install()
        self.page.evaluate("document.getElementById('overlay').style.display = 'flex'")
        self.page.wait_for_function("document.documentElement.dataset.titlebarOverlay === 'true'", timeout=2000)
        self.page.wait_for_timeout(50)
        box = self.page.locator("#memu-wincontrols").bounding_box()
        self.assertEqual((round(box["y"]), round(box["x"] + box["width"]), round(box["height"])), (0, 1200, 32))
        # the overlay keeps the band free and the band drags the window
        self.assertEqual(self.page.evaluate("getComputedStyle(document.getElementById('overlay')).paddingTop"), "32px")
        self.clear()
        self.page.mouse.move(400, 16); self.page.mouse.down(); self.page.mouse.up()
        self.assertEqual(self.calls("titlebar_drag"), [["titlebar_drag", "caption"]])
        self.page.wait_for_timeout(600)
        self.clear()
        self.page.mouse.move(400, 120); self.page.mouse.down(); self.page.mouse.up()
        self.assertEqual(self.calls("titlebar_drag"), [])
        self.page.evaluate("document.getElementById('overlay').style.display = 'none'")
        self.page.wait_for_function("document.documentElement.dataset.titlebarOverlay === 'false'", timeout=2000)
        self.page.wait_for_timeout(50)
        box = self.page.locator("#memu-wincontrols").bounding_box()
        self.assertGreater(round(box["y"]), 0)

    def test_english_labels(self):
        self.page.evaluate("window.PG_LANG = 'en'")
        self.page.add_script_tag(content="var PG_LANG = 'en';")
        self.install()
        labels = self.page.eval_on_selector_all("[data-wc]", "els => els.map(e => e.title)")
        self.assertEqual(labels, ["Minimize", "Maximize", "Close"])


if __name__ == "__main__":
    unittest.main()
