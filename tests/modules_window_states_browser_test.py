"""Round 21: module window states respect the MemuAI chrome (headless Chromium).

Runs the real playground app (app/pgstatic + app/playground_api) on a free local
port with the isolated test data dir, opens the built-in Translator module (no
network) and checks, for all five designs, light/dark and the custom title bar
on/off (the titlebar_browser_test mock of window.pywebview):

- the expanded ("развернуть") window is a sheet inside #main-content, never under
  the top bar and never overlapping the Windows caption buttons;
- floating windows cannot be dragged under the reserved title band;
- Esc on the host and an unhandled Esc inside the module collapse the sheet;
  an Esc the module handled itself (preventDefault) does not.

Skipped when Playwright or a Chromium build is not available
(MEMU_TEST_CHROMIUM may point at a Chromium binary).
"""
from __future__ import annotations

import os
import shutil
import socket
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _chromium() -> str:
    for candidate in (os.environ.get("MEMU_TEST_CHROMIUM", ""), "/usr/bin/chromium", "/workspace/work/chromium/chrome",
                      shutil.which("chromium") or "", shutil.which("chromium-browser") or ""):
        if candidate and Path(candidate).exists():
            return candidate
    return ""


MOCK = """() => {
  window.__state = { ok: true, supported: true, mode: "custom", installed: true, pending: false,
    maximized: false, fullscreen: false, active: true, os: "win11", doubleClickMs: 500, forced: false };
  const rec = () => () => Promise.resolve(true);
  window.pywebview = { api: { titlebar_state: () => Promise.resolve(Object.assign({}, window.__state)),
    titlebar_set_mode: () => Promise.resolve(Object.assign({}, window.__state)),
    titlebar_drag: rec(), titlebar_command: rec(), titlebar_system_menu: rec() } };
  window.dispatchEvent(new Event("pywebviewready"));
}"""

RECTS = """() => {
  const r = (node) => { if (!node) return null; const b = node.getBoundingClientRect(); return { l: b.left, t: b.top, r: b.right, b: b.bottom, w: b.width, h: b.height }; };
  const win = document.querySelector('.module-window[data-module="translator"]');
  return { win: r(win), full: win && win.dataset.full === "1", state: win && win.dataset.state,
    content: r(document.getElementById("main-content")), bar: r(document.querySelector(".topbar.appbar")),
    controls: r(document.getElementById("memu-wincontrols")), layer: r(document.getElementById("module-layer")),
    tools: r(win && win.querySelector(".module-window-tools")), overlay: document.documentElement.dataset.titlebarOverlay || "" };
}"""


def _wait(page, expression: str, timeout: float = 10.0) -> None:
    """Poll from Python: Playwright's own wait_for_function re-evaluates string
    predicates with eval, which the app's CSP (no 'unsafe-eval') forbids as
    soon as the first check is falsy."""
    deadline = time.time() + timeout
    while True:
        if page.evaluate(f"() => !!({expression})"):
            return
        if time.time() > deadline:
            raise AssertionError(f"timed out waiting for: {expression}")
        page.wait_for_timeout(50)


@unittest.skipUnless(_chromium(), "Chromium is not installed")
class ModuleWindowStatesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
            import uvicorn
        except Exception as exc:  # pragma: no cover
            raise unittest.SkipTest(f"playwright/uvicorn unavailable: {exc}")
        from app import modules_host
        from app.playground_api import create_app
        root = modules_host.default_root()
        root.mkdir(parents=True, exist_ok=True)
        modules_host.configure_module(root, "translator", enabled=True)
        # A fresh profile greets the user with the name dialog; it is a real
        # full-window overlay over the top bar (titlebar.js then correctly moves
        # the window buttons to the overlay corner, data-titlebar-overlay=true,
        # and the first Esc closes that dialog). This test is about the module
        # sheet, so start from a profile that already finished onboarding.
        from app import pgdata
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

    def open_page(self, design="classic", theme="dark", titlebar=True, size=(1440, 900)):
        page = self.browser.new_page(viewport={"width": size[0], "height": size[1]})
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(f"http://127.0.0.1:{self.port}/", wait_until="networkidle")
        _wait(page, "window.MemuModules && document.querySelector('.topbar.appbar') && document.body.classList.contains('boot-ready')", 20)
        page.wait_for_timeout(400)  # the onboarding check runs 250 ms after boot
        self.assertFalse(page.evaluate("!!document.getElementById('onboarding-name')"), "onboarding dialog left open")
        page.evaluate("([d, t]) => { const r = document.documentElement; r.dataset.design = d; r.dataset.theme = t; }", [design, theme])
        if titlebar:
            page.evaluate(MOCK)
            _wait(page, "document.documentElement.dataset.titlebar === 'custom'", 5)
        page.evaluate("() => { MemuModules.closeAll(); return MemuModules.refresh().then(() => MemuModules.open('translator')); }")
        _wait(page, "document.querySelector('.module-window[data-module=translator]') && !document.querySelector('.module-window[data-module=translator]').hidden", 10)
        page.wait_for_timeout(400)
        self.addCleanup(page.close)
        self.addCleanup(lambda: self.assertEqual(errors, []))
        return page

    def expand(self, page):
        page.evaluate("() => { const w = document.querySelector('.module-window[data-module=translator]'); if (w.dataset.full !== '1') MemuModules.snap('translator', 'max'); }")
        page.wait_for_timeout(450)  # FLIP transition (240 ms) + layout
        return page.evaluate(RECTS)

    def assert_sheet(self, rects, label):
        win, content, bar = rects["win"], rects["content"], rects["bar"]
        self.assertTrue(rects["full"], label)
        self.assertEqual(rects["state"], "expanded", label)
        # A sheet inside the content area (with margins), not the whole window.
        self.assertGreaterEqual(win["l"], content["l"] - 0.5, label)
        self.assertLessEqual(win["r"], content["r"] + 0.5, label)
        self.assertGreaterEqual(win["t"], bar["b"] - 0.5, label)
        self.assertLessEqual(win["b"], content["b"] + 0.5, label)
        self.assertLess(win["w"] * win["h"], 1440 * 900 * 0.9, label)
        controls = rects["controls"]
        if controls:
            overlap_x = min(win["r"], controls["r"]) - max(win["l"], controls["l"])
            overlap_y = min(win["b"], controls["b"]) - max(win["t"], controls["t"])
            self.assertFalse(overlap_x > 0 and overlap_y > 0, f"{label}: sheet under the window buttons {rects}")
            self.assertNotEqual(rects["overlay"], "true", f"{label}: buttons pushed into the overlay corner")

    def test_expanded_sheet_respects_chrome_in_every_design(self):
        for titlebar in (True, False):
            page = self.open_page("classic", "dark", titlebar)
            for design in ("classic", "aurora", "focus", "grid", "glass"):
                for theme in ("dark", "light"):
                    with self.subTest(design=design, theme=theme, titlebar=titlebar):
                        page.evaluate("([d, t]) => { const r = document.documentElement; r.dataset.design = d; r.dataset.theme = t; }", [design, theme])
                        page.wait_for_timeout(350)
                        self.assert_sheet(self.expand(page), f"{design}/{theme}/{titlebar}")

    def test_floating_window_cannot_go_under_the_title_band(self):
        page = self.open_page("glass", "dark", True)
        # A sheet left expanded by the previous test reopens expanded (Round 30: without its own title row).
        if page.evaluate(RECTS)["full"]:
            page.click("#module-headbar [data-mhb=esc]")
            page.wait_for_timeout(450)
        rects = page.evaluate(RECTS)
        self.assertGreaterEqual(rects["layer"]["t"], rects["bar"]["b"] - 0.5)
        bar = page.locator(".module-window[data-module=translator] .module-window-bar")
        box = bar.bounding_box()
        page.mouse.move(box["x"] + 200, box["y"] + box["height"] / 2)
        page.mouse.down()
        page.mouse.move(box["x"] + 600, 40, steps=8)   # into the reserved band, away from the snap edge
        page.mouse.move(box["x"] + 620, 30, steps=2)
        page.mouse.up()
        page.wait_for_timeout(450)
        rects = page.evaluate(RECTS)
        self.assertGreaterEqual(rects["win"]["t"], rects["bar"]["b"] - 0.5, rects)
        if rects["controls"]:
            tools, controls = rects["tools"], rects["controls"]
            self.assertFalse(min(tools["b"], controls["b"]) - max(tools["t"], controls["t"]) > 0
                             and min(tools["r"], controls["r"]) - max(tools["l"], controls["l"]) > 0, rects)

    def test_escape_collapses_and_the_restore_chip_is_shown(self):
        page = self.open_page("classic", "dark", True)
        rects = self.expand(page)
        self.assertTrue(rects["full"])
        # Round 30: one header — the sheet's own title row is gone, its name, Esc chip, ⋯ and × sit in the top bar.
        self.assertTrue(page.evaluate("!document.getElementById('module-headbar').hidden"))
        self.assertTrue(page.evaluate("!document.querySelector('#module-headbar [data-mhb=esc]').hidden"))
        self.assertEqual(page.evaluate("getComputedStyle(document.querySelector('.module-window[data-module=translator] .module-window-bar')).display"), "none")
        bar = page.evaluate("document.querySelector('.topbar.appbar').getBoundingClientRect().height")
        self.assertLess(bar, 90, "the top bar must stay one row with the module head")
        page.evaluate("() => document.body.focus()")
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        self.assertFalse(page.evaluate(RECTS)["full"])
        # Unhandled Esc inside the module (forwarded by the bridge) collapses too.
        self.expand(page)
        frame = next(f for f in page.frames if "/modules/translator/document" in f.url)
        frame.evaluate("() => { document.activeElement && document.activeElement.blur(); document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); }")
        page.wait_for_timeout(400)
        self.assertFalse(page.evaluate(RECTS)["full"])
        # An Esc the module handled itself does not.
        self.expand(page)
        frame.evaluate("() => { document.addEventListener('keydown', (e) => e.preventDefault(), { once: true }); document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })); }")
        page.wait_for_timeout(400)
        self.assertTrue(page.evaluate(RECTS)["full"])


if __name__ == "__main__":
    unittest.main()
