"""Round 23: the settings shell (settings-kit.js + settings.css + renderSettingsPanel).

Static contracts run everywhere; the browser part loads the REAL UI
(tests/appearance_fixture.py, mocked API) in headless Chromium and checks the
navigation, sub-pages with breadcrumbs, the fuzzy ru/en search that jumps to a
setting, `when` dependencies, reset-to-default and the «Сохранено» indicator.
The browser part is skipped when Playwright or a Chromium build is missing.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PG = ROOT / "app" / "pgstatic"
sys.path.insert(0, str(ROOT))


def _chromium() -> str:
    for candidate in (os.environ.get("MEMU_TEST_CHROMIUM", ""), "/usr/bin/chromium",
                      shutil.which("chromium") or "", shutil.which("chromium-browser") or "",
                      "/workspace/work/chromium/chrome"):
        if candidate and os.path.exists(candidate):
            return candidate
    return ""


try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover - optional dependency
    sync_playwright = None

OLD_TABS = ("general", "apps", "models", "search", "integrations", "remote", "appearance", "work", "voice",
            "context", "prompts", "profile", "transfer", "panels", "modules", "docs", "about", "quick")


class SettingsShellContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = (PG / "app.js").read_text(encoding="utf-8")
        cls.kit = (PG / "settings-kit.js").read_text(encoding="utf-8")
        cls.css = (PG / "settings.css").read_text(encoding="utf-8")
        cls.html = (PG / "index.html").read_text(encoding="utf-8")

    def test_assets_are_linked_in_the_right_order(self):
        self.assertIn("/settings-kit.js", self.html)
        self.assertIn("/settings.css", self.html)
        # The kit is available to every script that renders a settings section.
        for later in ("/quick-settings.js", "/telegram-settings.js", "/panels.js", "/app.js", "/companion/settings.js"):
            self.assertLess(self.html.index("/settings-kit.js"), self.html.index(later), later)
        # Kit styles win over the designs and the section stylesheets.
        for earlier in ("/designs/glass.css", "/motion.css", "/quick-settings.css", "/telegram-settings.css", "/panels.css"):
            self.assertLess(self.html.index(earlier), self.html.index("/settings.css"), earlier)

    def test_every_old_tab_id_still_opens_a_page(self):
        pages = set(re.findall(r'P\("([a-z]+)"', self.js))
        aliases = re.search(r"const SETTINGS_PAGE_ALIASES = \{([^}]*)\}", self.js).group(1)
        alias_keys = set(re.findall(r"([a-z_]+):", aliases))
        for tab in OLD_TABS:
            self.assertTrue(tab in pages or tab in alias_keys, tab)
        # Panes other scripts mount into keep their ids.
        for pane in ("pane-quick", "pane-panels", "pane-remote", "pane-modules", "pane-docs", "pane-appearance", "pane-apps", "pane-transfer"):
            self.assertIn(f'id="{pane}"', self.js, pane)

    def test_kit_api_is_published(self):
        for name in ("row", "section", "toggle", "select", "segmented", "slider", "input", "secret", "field", "more",
                     "link", "sub", "note", "badge", "danger", "registerSearch", "openSub", "saved", "refreshDeps"):
            self.assertRegex(self.kit, rf"\b{name}\b", name)
        self.assertIn("window.MemuSettingsKit = api", self.kit)

    def test_motion_and_designs_are_respected(self):
        self.assertIn("prefers-reduced-motion: reduce", self.css)
        self.assertIn(':root[data-motion="off"]', self.css)
        for design in ("aurora", "focus", "grid", "glass"):
            self.assertIn(f':root[data-design="{design}"]', self.css, design)
        self.assertIn("@media (max-width: 860px)", self.css)


@unittest.skipUnless(sync_playwright is not None and _chromium(), "Playwright/Chromium is not installed")
class SettingsShellBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.appearance_fixture import build_fixture
        cls._tmp = tempfile.mkdtemp(prefix="memu-settings-")
        fixture = build_fixture(Path(cls._tmp))
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])
        cls.page = cls.browser.new_page(viewport={"width": 1360, "height": 860})
        cls.errors = []
        cls.page.on("pageerror", lambda e: cls.errors.append(str(e)))
        cls.page.goto(fixture.as_uri(), wait_until="domcontentloaded")
        deadline = time.time() + 90
        while time.time() < deadline:
            if cls.page.evaluate("document.body.classList.contains('boot-ready') && typeof showSettings === 'function' && !!window.MemuSettingsKit"):
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("MemuAI UI did not boot")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
        finally:
            cls._pw.stop()
            shutil.rmtree(cls._tmp, ignore_errors=True)

    def wait(self, expression, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.page.evaluate(expression):
                return
            time.sleep(0.1)
        self.fail("timed out: " + expression)

    def open(self, tab="general"):
        self.page.evaluate(f"async () => {{ if (document.querySelector('#panel-back.visible')) closePanel(); currentSettingsTab = '{tab}'; await showSettings(true); }}")
        self.wait("!!document.querySelector('#panel .sx-shell .settings-tab-pane.active')")

    def test_navigation_old_ids_and_breadcrumbs(self):
        self.open("integrations")      # the old Notifications tab id is an alias now
        self.assertEqual(self.page.evaluate("document.querySelector('.settings-tab-pane.active').id"), "pane-apps")
        self.page.click('.sx-nav [data-set-tab="voice"]')
        self.wait("document.querySelector('#pane-voice').classList.contains('active')")
        self.assertIn("Голос и Live", self.page.inner_text(".sx-crumbs"))
        # Keyboard: ArrowDown on the nav moves to the next page.
        self.page.focus('.sx-nav [data-set-tab="voice"]')
        self.page.keyboard.press("ArrowDown")
        self.wait("document.querySelector('#pane-work').classList.contains('active')")
        self.assertEqual(self.errors, [])

    def test_sub_page_open_back_and_escape(self):
        self.open("voice")
        self.page.click('[data-sx-open="tts"]')
        self.wait("!document.querySelector('[data-sx-sub=\"tts\"]').hidden")
        crumbs = self.page.inner_text(".sx-crumbs")
        self.assertIn("Озвучивание ответов", crumbs)
        self.assertTrue(self.page.evaluate("document.querySelector('#settings-voice-buttons').closest('.sx-sub-hidden') !== null"
                                           " || document.querySelector('#settings-voice-buttons').classList.contains('sx-sub-hidden')"))
        self.page.keyboard.press("Escape")       # first Escape leaves the sub-page, not the dialog
        self.wait("document.querySelector('[data-sx-sub=\"tts\"]').hidden")
        self.assertTrue(self.page.evaluate("document.querySelector('#panel-back').classList.contains('visible')"))

    def test_search_is_fuzzy_bilingual_and_jumps(self):
        self.open("general")
        kit = "window.MemuSettingsKit"
        # Russian, a typo, the wrong keyboard layout and English keywords all find the setting.
        self.assertEqual(self.page.evaluate(f"{kit}.search('орфография')[0].page"), "general")
        self.assertEqual(self.page.evaluate(f"{kit}.search('уведомленя')[0].page"), "notifications")
        self.assertIn(self.page.evaluate(f"{kit}.search('ntvf')[0].page"), ("appearance",))   # «тема» typed in EN layout
        self.assertEqual(self.page.evaluate(f"{kit}.search('spell check')[0].page"), "general")
        self.page.fill("#sx-search", "кэширование")
        self.wait("!document.querySelector('.sx-results').hidden && document.querySelectorAll('.sx-result').length > 0")
        self.page.keyboard.press("Enter")
        self.wait("document.querySelector('#pane-models').classList.contains('active')")
        self.wait("document.querySelector('#models-more').open === true")
        self.wait("!!document.querySelector('.sx-flash')")
        self.assertEqual(self.page.evaluate("document.querySelector('#sx-search').value"), "")
        # A row inside a sub-page: the jump opens the sub-page first.
        self.page.fill("#sx-search", "токен github")
        self.wait("document.querySelectorAll('.sx-result').length > 0")
        self.page.keyboard.press("Enter")
        self.wait("!document.querySelector('[data-sx-sub=\"int-github\"]').hidden")
        self.assertIn("GitHub", self.page.inner_text(".sx-crumbs"))
        # Nothing found is an honest empty state.
        self.page.fill("#sx-search", "zzqqxx")
        self.wait("!!document.querySelector('.sx-results-empty')")
        self.page.keyboard.press("Escape")
        self.wait("document.querySelector('.sx-results').hidden")

    def test_final_pass_details(self):
        """Round 23 finish: sub-page rows are found by their sub-page title, signed sliders, honest buttons."""
        self.open("general")
        kit = "window.MemuSettingsKit"
        hit = self.page.evaluate(f"{kit}.search('токен github').find((h) => h.sub === 'int-github')")
        self.assertIsNotNone(hit)
        self.assertIn("+3", self.page.evaluate(f"{kit}.slider('x-rate', {{ min: -10, max: 10, value: 3, signed: true }})"))
        self.assertIn("−2", self.page.evaluate(f"{kit}.slider('x-rate', {{ min: -10, max: 10, value: -2, signed: true }})"))
        self.open("apps")
        # nothing is connected in the fixture: there is nothing to disconnect
        self.wait("[...document.querySelectorAll('[data-int-off]')].every((b) => b.hidden)")

    def test_dependencies_reset_and_saved_indicator(self):
        self.open("notifications")
        hidden = "document.querySelector('.sound-settings-card').hidden"
        if self.page.evaluate("document.querySelector('#st-notify-sound').checked"):
            self.page.click("label:has(#st-notify-sound)")
        self.wait(hidden)
        self.page.click("label:has(#st-notify-sound)")            # sound on → its variants appear
        self.wait("!" + hidden)
        self.wait("document.querySelector('[data-sx-key=\"notify_sound\"]').classList.contains('sx-modified')")
        self.wait("document.querySelector('.sx-saved').classList.contains('show')")
        self.wait("(JSON.parse(localStorage.getItem('qa_prefs') || '{}').notify_sound) === true")
        self.page.hover('[data-sx-key="notify_sound"]')
        self.page.click('[data-sx-key="notify_sound"] [data-sx-reset]')
        self.wait("document.querySelector('#st-notify-sound').checked === false")
        self.wait(hidden)
        self.wait("(JSON.parse(localStorage.getItem('qa_prefs') || '{}').notify_sound) === false")

    def test_narrow_window_shows_one_column(self):
        page = self.browser.new_page(viewport={"width": 600, "height": 820})
        try:
            page.goto(self.page.url, wait_until="domcontentloaded")
            deadline = time.time() + 60
            while time.time() < deadline and not page.evaluate("document.body.classList.contains('boot-ready') && !!window.MemuSettingsKit"):
                time.sleep(0.2)
            page.evaluate("async () => { currentSettingsTab = 'models'; await showSettings(true); }")
            page.wait_for_selector("#panel .sx-shell .settings-tab-pane.active")
            self.assertFalse(page.evaluate("document.querySelector('.sx-side').checkVisibility({ visibilityProperty: true })"))
            page.click(".sx-nav-toggle")
            page.wait_for_function("document.querySelector('.sx-shell').classList.contains('sx-nav-open')")
            page.click('.sx-nav [data-set-tab="chat"]')
            page.wait_for_function("document.querySelector('#pane-chat').classList.contains('active') && !document.querySelector('.sx-shell').classList.contains('sx-nav-open')")
            overflow = page.evaluate("(() => { const b = document.querySelector('.sx-shell .settings-body'); return b.scrollWidth - b.clientWidth; })()")
            self.assertLessEqual(overflow, 1)
        finally:
            page.close()


if __name__ == "__main__":
    unittest.main()
