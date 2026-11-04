"""Hints of items inside open menus are drawn above the menu, never under it (headless Chromium).

A long menu item (like «Системная рамка окна (после переоткрытия)» in the ⋯ menu of a module window) used to
get its hint painted below the menu, because menus sit on a higher z-index than the hint. The hint now lives in
the top layer (popover) and is placed beside the open menu, or above / below the whole menu when there is no room.

    python -m pytest tests/tooltip_menu_layer_test.py -q
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tooltip-ui-")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from round27_ui_lang_test import _chromium, _has_playwright  # noqa: E402

STATE = """() => {
  const t = document.getElementById('memu-tooltip');
  const m = document.querySelector('#context-menu.visible');
  if (!t || t.hidden) return { shown: false };
  const r = t.getBoundingClientRect(), mr = m.getBoundingClientRect();
  const x = (r.left + r.right) / 2, y = (r.top + r.bottom) / 2;
  // the hint ignores the pointer; let hit testing see it for a moment
  const before = t.style.pointerEvents;
  t.style.pointerEvents = 'auto';
  const hit = document.elementFromPoint(x, y);
  t.style.pointerEvents = before;
  return {
    shown: true,
    open: t.matches(':popover-open'),
    text: t.innerText,
    side: t.dataset.side || '',
    overlaps: !(r.right <= mr.left + 0.5 || r.left >= mr.right - 0.5 || r.bottom <= mr.top + 0.5 || r.top >= mr.bottom - 0.5),
    onTop: !!(hit && hit.closest('#memu-tooltip')),
    inside: r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight,
  };
}"""

OPEN_MENU = """([x, y]) => {
  showContextMenu([
    { label: 'Вернуть в MemuAI', icon: 'back', run() {} },
    { label: 'Системная рамка окна (после переоткрытия)', icon: 'layout', run() {} },
    { label: 'Свернуть', icon: 'minus', run() {} },
    { label: 'Закрыть', icon: 'close', hint: 'Ctrl+W', run() {} },
  ], x, y);
  const item = document.querySelectorAll('#context-menu [data-context-action]')[1];
  item.dataset.tip = 'Системная рамка окна (после переоткрытия) — окно модуля получит рамку и заголовок системы';
  return !!item;
}"""


@unittest.skipUnless(_has_playwright() and _chromium(), "Playwright / Chromium not installed")
class MenuHintLayer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import httpx
        from playwright.sync_api import sync_playwright
        from ui_lang_scan import Servers
        cls.servers = Servers().start()
        with httpx.Client(timeout=30, trust_env=False) as http:
            http.post(cls.servers.pg + "/api/pg/preferences", json={
                "language": "ru", "updates_enabled": False, "ui_motion": "off",
                "profile_name": "Тест", "profile_onboarded": True, "onboarding_stage": "done", "onboarding_v5_done": True})
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()
        cls.servers.stop()

    def hint_for_menu(self, width: int, height: int) -> dict:
        ctx = self.browser.new_context(viewport={"width": width, "height": height})
        self.addCleanup(ctx.close)
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(self.servers.pg + "/")
        page.wait_for_selector("body.boot-ready", timeout=90000)
        page.wait_for_timeout(500)
        # the menu opens from the top right corner, like the ⋯ button of a module window
        self.assertTrue(page.evaluate(OPEN_MENU, [width - 24, 20]))
        page.wait_for_selector("#context-menu.visible")
        page.hover("#context-menu [data-context-action='1']")
        state = {"shown": False}
        for _ in range(25):  # the page forbids eval, so poll instead of wait_for_function
            page.wait_for_timeout(200)
            state = page.evaluate(STATE)
            if state.get("shown"):
                break
        page.wait_for_timeout(250)
        state = page.evaluate(STATE)
        self.assertEqual(errors, [])
        return state

    def check(self, state: dict):
        self.assertTrue(state["shown"], state)
        self.assertIn("Системная рамка окна", state["text"])
        self.assertTrue(state["onTop"], f"something is painted over the hint: {state}")
        self.assertFalse(state["overlaps"], f"the hint covers the menu or hides under it: {state}")
        self.assertTrue(state["inside"], f"the hint leaves the window: {state}")
        self.assertTrue(state["open"], "the hint is in the top layer")

    def test_wide_window_hint_beside_the_menu(self):
        state = self.hint_for_menu(1100, 760)
        self.check(state)
        self.assertIn(state["side"], ("left", "right"))

    def test_narrow_window_hint_above_or_below_the_menu(self):
        self.check(self.hint_for_menu(520, 700))

    def test_static_rules(self):
        js = (ROOT / "app" / "pgstatic" / "tooltip.js").read_text(encoding="utf-8")
        css = (ROOT / "app" / "pgstatic" / "appearance.css").read_text(encoding="utf-8")
        self.assertIn('popover', js)
        self.assertIn("showPopover", js)
        self.assertIn(".context-menu", js)
        self.assertIn(".memu-tooltip:popover-open", css)


if __name__ == "__main__":
    unittest.main()
