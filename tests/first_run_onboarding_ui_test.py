"""First-run onboarding in a real browser (isolated servers, an offline model mock, headless Chromium).

* under automation (navigator.webdriver) the wizard stays away and the short legacy name dialog is used,
  so other UI tests are not disturbed;
* the full standard path: profile (name required, theme) → workspace type → setup chat → model card →
  the shared model picker → «Подтвердить» → a survey written via the API → answers → «Кратко» → tips;
  preferences, memory of the answers and the API conversation are checked;
* the user's own first-run questions are edited in Settings and show up when the introduction is run again;
* an agentic workspace with a companion: the wizard creates it, the setup runs in the single global chat.

    python -m pytest tests/first_run_onboarding_ui_test.py -q
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-onboarding-ui-")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from round27_ui_lang_test import _chromium, _has_playwright  # noqa: E402

REPORT = "<memuai-setup_report>"


def _text(content) -> str:
    if isinstance(content, list):
        return " ".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return str(content or "")


@unittest.skipUnless(_has_playwright() and _chromium(), "Playwright / Chromium not installed")
class FirstRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import httpx
        from playwright.sync_api import sync_playwright
        from ui_lang_scan import Servers
        from tests import mock_custom
        cls.mock = mock_custom
        cls.servers = Servers().start()
        cls.http = httpx.Client(timeout=30, trust_env=False)
        cls.http.post(cls.servers.pg + "/api/pg/preferences", json={"language": "ru", "updates_enabled": False, "ui_motion": "off"})
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()
        cls.http.close()
        cls.servers.stop()

    def prefs(self) -> dict:
        data = self.http.get(self.servers.pg + "/api/pg/preferences").json()
        return data.get("preferences", data)

    def wait_prefs(self, key: str, value, timeout=10) -> dict:
        # preferences are saved with a short debounce
        end = time.time() + timeout
        prefs = self.prefs()
        while prefs.get(key) != value and time.time() < end:
            time.sleep(0.2)
            prefs = self.prefs()
        self.assertEqual(prefs.get(key), value)
        return prefs

    def open(self, force: bool, width=1280, height=820):
        ctx = self.browser.new_context(viewport={"width": width, "height": height})
        flag = "localStorage.setItem('memu_onb_force', '1');" if force else "localStorage.removeItem('memu_onb_force');"
        ctx.add_init_script(f"try {{ localStorage.setItem('memu_lang', 'ru'); {flag} }} catch (_) {{}}")
        self.addCleanup(ctx.close)
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        self.addCleanup(lambda: self.assertEqual(errors, []))
        page.goto(self.servers.pg + "/")
        page.wait_for_selector("body.boot-ready", timeout=90000)
        return page

    def wait_idle(self, page, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            page.wait_for_timeout(300)
            if not page.evaluate("generationJobs.size || S.messages.some((m) => m.streaming)"):
                return
        self.fail("the answer did not finish")

    def last_report(self) -> str:
        body = self.mock.state.get("last_body") or {}
        messages = body.get("messages") or []
        self.assertTrue(messages, "no request reached the model")
        return _text(messages[-1].get("content"))

    def pick_model(self, page) -> str:
        model = page.evaluate("S.models.find((m) => /gpt-test/.test(m.id)).id")
        page.wait_for_selector(".onb-card-model [data-onb='model-pick']", timeout=20000)
        page.click(".onb-card-model [data-onb='model-pick']")
        page.wait_for_selector(".mmp-pop .mmp-row[data-model]", timeout=8000)
        page.click(f".mmp-pop .mmp-row[data-model='{model}']")
        page.wait_for_timeout(300)
        self.assertTrue(page.is_enabled("[data-onb='model-confirm']"))
        return model

    def test_1_automation_keeps_the_legacy_dialog(self):
        page = self.open(force=False)
        page.wait_for_selector("#onboarding-name", timeout=15000)
        self.assertEqual(page.locator(".onb-back").count(), 0)
        self.assertEqual(page.locator(".msg-setup").count(), 0)

    def test_2_standard_path(self):
        page = self.open(force=True)
        page.wait_for_selector("#memu-onb .onb-shell", timeout=20000)
        self.assertEqual(page.locator("#onboarding-name").count(), 0)
        page.click("[data-onb-w='next']")
        self.assertTrue(page.is_visible("#onb-name-err"), "the name is required")
        page.fill("#onb-name", "Алекс")
        page.click("[data-onb-theme='light']")
        page.wait_for_timeout(200)
        self.assertEqual(page.evaluate("document.documentElement.dataset.theme || S.theme"), "light")
        self.assertEqual(page.locator("[data-onb-w='photo'] .ui-icon").count(), 1, "the photo button keeps its icon")
        page.click("[data-onb-w='next']")
        page.wait_for_selector(".sp-type-card[data-sp-type='standard']")
        page.click(".sp-type-card[data-sp-type='standard']")
        page.click("[data-onb-w='next']")
        page.wait_for_selector(".onb-card-model", timeout=20000)
        self.assertEqual(page.locator(".onb-back").count(), 0)
        self.assertEqual(page.evaluate("currentChat().title"), "Знакомство с MemuAI")

        self.mock.state.update({"last_body": None})
        model = self.pick_model(page)
        page.click("[data-onb='model-confirm']")
        page.wait_for_selector(".onb-card-survey [data-onb='survey-submit']", timeout=30000)
        self.wait_idle(page)
        report = self.last_report()
        self.assertTrue(report.startswith(REPORT))
        self.assertIn("Address the user formally", report)
        self.assertIn('"Алекс"', report)
        self.assertEqual(self.prefs().get("default_model"), model)

        page.click("[data-onb='chip'][data-q='uses'][data-o='code']")
        page.click("[data-onb='chip'][data-q='uses'][data-o='automation']")
        page.click("[data-onb='chip'][data-q='media'][data-o='images']")
        page.click("[data-onb='survey-submit']")
        page.wait_for_selector(".onb-card-walk [data-onb='walk'][data-choice='brief']", timeout=30000)
        self.wait_idle(page)
        report = self.last_report()
        self.assertIn("The user's answers", report)
        self.assertIn("Программирование", report)
        sent = [_text(m.get("content")) for m in self.mock.state["last_body"]["messages"]]
        self.assertFalse(any(text.startswith("Для чего вам MemuAI?") for text in sent), "the local answer bubble stays local")

        page.click("[data-onb='walk'][data-choice='brief']")
        page.wait_for_selector(".onb-card-brief .onb-tile", timeout=10000)
        self.assertGreaterEqual(page.locator(".onb-card-brief .onb-tile").count(), 4)
        overflow = page.evaluate("""() => [...document.querySelectorAll('.onb-seg button')].filter((b) => b.scrollWidth > b.clientWidth + 1).length""")
        self.assertEqual(overflow, 0, "segment labels fit their tile")
        page.click("[data-onb='brief-done']")
        page.wait_for_selector(".onb-card-tips .onb-tip", timeout=10000)

        prefs = self.wait_prefs("onboarding_stage", "done")
        self.assertTrue(prefs.get("onboarding_v5_done"))
        self.assertTrue(prefs.get("profile_onboarded"))
        self.assertEqual(prefs.get("theme"), "light")
        self.assertIn("Программирование", prefs["onboarding_answers"]["uses"])
        self.assertIn("code", prefs["onboarding_answers"]["_tags"])
        self.assertFalse(page.evaluate("MemuOnboarding.active()"))

    def test_3_own_questions_and_restart(self):
        page = self.open(force=True)
        page.wait_for_timeout(800)
        self.assertEqual(page.locator(".onb-back").count(), 0, "the wizard runs only once")
        page.evaluate("showSettings(true)")
        page.wait_for_timeout(400)
        page.evaluate("openSettingsPage('general')")
        page.wait_for_selector("#settings-tips #st-intro-start")
        self.assertEqual(page.locator("#st-intro-questions").count(), 0,
                         "the first-run questions editor is not offered in Settings")
        page.evaluate("MemuOnboarding.openSurveyEditor()")
        page.wait_for_selector(".onb-editor-panel .onb-ed-q")
        before = page.locator(".onb-editor-panel .onb-ed-q").count()
        page.click("#onb-ed-add")
        row = page.locator(".onb-editor-panel .onb-ed-q").nth(before)
        row.locator("[data-ed='q']").fill("Какую музыку вы слушаете?")
        row.locator("[data-ed='type']").select_option("text", force=True)
        page.click("#onb-ed-save")
        end = time.time() + 10
        custom = self.prefs().get("onboarding_survey_custom") or []
        while len(custom) != before + 1 and time.time() < end:
            time.sleep(0.2)
            custom = self.prefs().get("onboarding_survey_custom") or []
        self.assertEqual(len(custom), before + 1)
        self.assertEqual(custom[-1]["q"], "Какую музыку вы слушаете?")

        page.evaluate("closePanel(); closePanel();")
        page.evaluate("MemuOnboarding.restart()")
        page.wait_for_selector(".onb-card-model [data-onb='model-confirm']", timeout=20000)
        page.click("[data-onb='model-confirm']")
        page.wait_for_selector(".onb-card-survey", timeout=30000)
        self.wait_idle(page)
        self.assertIn("Какую музыку вы слушаете?", page.inner_text(".onb-card-survey"))
        page.click("[data-onb='survey-skip']")
        page.wait_for_selector(".onb-card-walk [data-onb='walk'][data-choice='skip']", timeout=30000)
        self.wait_idle(page)
        page.click("[data-onb='walk'][data-choice='skip']")
        page.wait_for_selector(".onb-card-tips", timeout=10000)
        self.wait_prefs("onboarding_stage", "done")

    def test_4_agentic_workspace(self):
        page = self.open(force=True)
        page.evaluate("MemuOnboarding.start({ force: true, space: 'agent' })")
        page.wait_for_selector("#memu-onb .onb-shell", timeout=10000)
        page.click("[data-onb-w='next']")
        page.wait_for_selector(".sp-type-card[data-sp-type='agent']")
        page.click(".sp-type-card[data-sp-type='agent']")
        page.click("[data-onb-w='next']")
        page.wait_for_selector("#memu-onb [data-cf-photo-btn] .ui-icon", timeout=10000)
        name = page.input_value("#memu-onb [data-cf='name']")
        page.click("[data-onb-w='next']")
        page.wait_for_timeout(2500)
        page.wait_for_load_state("load")
        page.wait_for_selector("body.boot-ready", timeout=90000)
        page.wait_for_selector(".onb-card-model", timeout=30000)
        self.assertTrue(page.evaluate("MemuSpaces.isAgent()"))
        self.assertEqual(page.evaluate("S.currentId"), page.evaluate("MemuSpaces.globalId()"))
        companion = page.evaluate("MemuSpaces.companion().name")
        if name:
            self.assertEqual(companion, name)

        self.mock.state.update({"last_body": None})
        self.pick_model(page)
        page.click("[data-onb='model-confirm']")
        page.wait_for_selector(".onb-card-survey [data-onb='survey-skip']", timeout=30000)
        self.wait_idle(page)
        report = self.last_report()
        self.assertIn(REPORT, report)
        self.assertIn(f"You are {companion}", report)
        self.assertNotIn("Address the user formally", report)
        page.click("[data-onb='survey-skip']")
        page.wait_for_selector(".onb-card-walk [data-onb='walk'][data-choice='skip']", timeout=30000)
        self.wait_idle(page)
        page.click("[data-onb='walk'][data-choice='skip']")
        page.wait_for_selector(".onb-card-tips [data-onb='done-write']", timeout=10000)
        self.wait_prefs("onboarding_stage", "done")


if __name__ == "__main__":
    unittest.main()
