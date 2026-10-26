"""Round 27 (r27-hunt-ui-3): MemuCode's right panel in a narrow window (≤980 px).

At ≤980 px the «Changes» panel is a drawer over the greeting and the input. It used to read the wide
window's saved ``memu_code_dock_hidden`` (shown by default), so a narrow window opened MemuCode with the
drawer already covering the chat. Now the drawer starts closed every time and its open/closed state is
kept apart: toggling it never rewrites the wide column's saved choice.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-r27-dock-")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from round27_ui_lang_test import _chromium, _has_playwright  # noqa: E402

STATE_JS = """() => ({
  dock: !document.getElementById('code-dock').hidden,
  backdrop: !document.getElementById('code-dock-backdrop').hidden,
  cls: document.body.classList.contains('code-dock-hidden'),
  saved: localStorage.getItem('memu_code_dock_hidden'),
  pressed: document.getElementById('code-dock-toggle').getAttribute('aria-pressed') })"""

@unittest.skipUnless(_chromium() and _has_playwright(), "headless Chromium / playwright is not available")
class NarrowDockTest(unittest.TestCase):
    def test_narrow_drawer_starts_closed_and_keeps_wide_choice(self) -> None:
        import httpx
        from ui_lang_scan import Servers
        from playwright.sync_api import sync_playwright
        servers = Servers().start()
        try:
            httpx.post(servers.pg + "/api/pg/preferences", json={"profile_onboarded": True, "profile_name": "Alex",
                                                                  "language": "ru", "ui_motion": "reduced"},
                       trust_env=False, timeout=20)
            with sync_playwright() as play:
                browser = play.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])
                page = browser.new_page(viewport={"width": 1360, "height": 820})
                page.add_init_script("try { if (!sessionStorage.getItem('r27dock')) {"
                                     " localStorage.setItem('memu_code_dock_hidden', '0');"
                                     " sessionStorage.setItem('r27dock', '1'); } } catch (e) {}")
                page.goto(servers.pg + "/")
                page.wait_for_selector("body.boot-ready", timeout=90000)
                page.click("#nav-code")
                page.wait_for_timeout(900)
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)

                wide = page.evaluate(STATE_JS)
                self.assertTrue(wide["dock"], wide)
                self.assertEqual(wide["saved"], "0")

                page.set_viewport_size({"width": 900, "height": 820})
                page.wait_for_timeout(300)
                narrow = page.evaluate(STATE_JS)
                self.assertFalse(narrow["dock"], narrow)
                self.assertFalse(narrow["backdrop"], narrow)
                self.assertTrue(narrow["cls"], narrow)
                self.assertEqual(narrow["pressed"], "false")

                page.click("#code-dock-toggle")
                page.wait_for_timeout(200)
                opened = page.evaluate(STATE_JS)
                self.assertTrue(opened["dock"] and opened["backdrop"], opened)
                page.click("#code-dock-backdrop", position={"x": 20, "y": 400})
                page.wait_for_timeout(200)
                closed = page.evaluate(STATE_JS)
                self.assertFalse(closed["dock"], closed)
                self.assertEqual(closed["saved"], "0", "closing the drawer must not hide the wide column")

                page.set_viewport_size({"width": 1360, "height": 820})
                page.wait_for_timeout(300)
                self.assertTrue(page.evaluate(STATE_JS)["dock"])

                page.set_viewport_size({"width": 900, "height": 820})
                page.reload()
                page.wait_for_selector("body.boot-ready", timeout=90000)
                page.wait_for_timeout(600)
                if not page.evaluate("document.body.classList.contains('code-mode')"):
                    page.click("#nav-code")
                    page.wait_for_timeout(900)
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(300)
                fresh = page.evaluate(STATE_JS)
                self.assertFalse(fresh["dock"], fresh)
                self.assertEqual(fresh["saved"], "0")
                composer = page.evaluate("""() => { const el = document.querySelector('#input, textarea');
                  const r = el.getBoundingClientRect(); const top = document.elementFromPoint(r.left + 20, r.top + r.height / 2);
                  return !!top && (el === top || el.contains(top) || top.contains(el)); }""")
                self.assertTrue(composer, "the input is reachable, not covered by the drawer")
                browser.close()
        finally:
            servers.stop()

@unittest.skipUnless(_chromium() and _has_playwright(), "headless Chromium / playwright is not available")
class GreetingScrollTest(unittest.TestCase):
    """From a long chat scrolled to its end, MemuCode / a new chat showed the greeting with the old scroll
    offset: at 720 px the «MemuCode works in the repository» heading sat above the top edge."""

    def test_greeting_starts_at_the_top(self) -> None:
        import httpx
        from ui_lang_scan import Servers
        from round27_ui_look_shots import seed_chat
        from playwright.sync_api import sync_playwright
        servers = Servers().start()
        try:
            seed_chat("ru")
            httpx.post(servers.pg + "/api/pg/preferences", json={"profile_onboarded": True, "profile_name": "Alex",
                                                                  "language": "ru", "ui_motion": "reduced"},
                       trust_env=False, timeout=20)
            with sync_playwright() as play:
                browser = play.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])
                page = browser.new_page(viewport={"width": 720, "height": 820})
                page.goto(servers.pg + "/")
                page.wait_for_selector("body.boot-ready", timeout=90000)
                for leave in ("document.getElementById('nav-code').click()", "newChat()"):
                    page.evaluate("openChat('c_look1')")
                    page.wait_for_timeout(700)
                    page.evaluate("const c = document.getElementById('chat'); c.scrollTop = c.scrollHeight")
                    self.assertGreater(page.evaluate("document.getElementById('chat').scrollTop"), 0)
                    page.evaluate(leave)
                    page.wait_for_timeout(900)
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(300)
                    state = page.evaluate("""() => { const chat = document.getElementById('chat');
                      const head = document.querySelector('#greeting .greet-head');
                      return {scroll: chat.scrollTop, chatTop: chat.getBoundingClientRect().top,
                              headTop: head.getBoundingClientRect().top}; }""")
                    self.assertEqual(state["scroll"], 0, (leave, state))
                    self.assertGreaterEqual(state["headTop"] + 1, state["chatTop"], (leave, state))
                browser.close()
        finally:
            servers.stop()

if __name__ == "__main__":
    unittest.main()
