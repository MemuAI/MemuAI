"""First-run onboarding, workspaces hooks, the «?» help and hint layering — static checks.

* index.html hooks onboarding.css / onboarding.js / help-tips.css / help-tips.js with cache versions, in order;
  the old interface tour (tour.js / tour.css) is gone;
* onboarding.js and help-tips.js: no emoji glyphs, every L()/L2() pair has an English half without Cyrillic,
  no «\\b» next to Cyrillic in regexes, the public API is exported;
* app.js renders setup cards and keeps local-only messages out of the model context;
* pgdata keeps the onboarding preferences tidy (stage, answers, the user's own questions with hints);
* hints never hide under menus: the tooltip uses the top layer (popover) and the highest z-index,
  and an item of an open menu gets its hint beside the menu.

    python -m pytest tests/first_run_onboarding_test.py -q
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-onboarding-")

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "pgstatic"
sys.path.insert(0, str(ROOT))

CYR = re.compile(r"[А-Яа-яЁё]")
EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u26FF]")
PAIR = re.compile(r'\bL2?\(\s*"((?:[^"\\]|\\.)*)"\s*,\s*"((?:[^"\\]|\\.)*)"')
PAIR_TPL = re.compile(r"\bL2?\(\s*`((?:[^`\\]|\\.)*)`\s*,\s*`((?:[^`\\]|\\.)*)`")


def read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


class IndexHooks(unittest.TestCase):
    def test_files_are_hooked_in_order(self):
        html = read("index.html")
        for ref in ("/onboarding.css?v=", "/onboarding.js?v=", "/help-tips.css?v=", "/help-tips.js?v=", "/spaces.js?v=", "/spaces.css?v="):
            self.assertEqual(html.count(ref), 1, ref)
        self.assertLess(html.index("/spaces.css?v="), html.index("/onboarding.css?v="))
        self.assertLess(html.index("/spaces.js?v="), html.index("/onboarding.js?v="))
        self.assertLess(html.index("/onboarding.js?v="), html.index("/help-tips.js?v="))
        self.assertLess(html.index("/app.js?v="), html.index("/onboarding.js?v="))
        self.assertNotIn("/tour.js", html)
        self.assertNotIn("/tour.css", html)
        self.assertFalse((STATIC / "tour.js").exists())
        self.assertFalse((STATIC / "tour.css").exists())


class OnboardingScript(unittest.TestCase):
    src = read("onboarding.js")

    def test_no_emoji_and_language_pairs(self):
        self.assertFalse(EMOJI.search(self.src), "emoji in onboarding.js (no emoji font in WebView2)")
        pairs = PAIR.findall(self.src) + PAIR_TPL.findall(self.src)
        self.assertGreater(len(pairs), 150)
        for ru, en in pairs:
            self.assertFalse(CYR.search(en), f"Cyrillic in the English string: {en[:90]}")

    def test_no_word_boundary_next_to_cyrillic(self):
        self.assertFalse(re.search(r"\\b[А-Яа-яЁё]|[А-Яа-яЁё]\\b", self.src))

    def test_public_api(self):
        for name in ("start", "resume", "restart", "active", "cardHtml", "openSurveyEditor", "survey", "DEFAULT_SURVEY"):
            self.assertRegex(self.src, r"window\.MemuOnboarding = Object\.freeze\(\{[^}]*\b" + name + r"\b")
        self.assertIn('const REPORT_TAG = "memuai-setup_report";', self.src)

    def test_default_survey_covers_the_requested_topics(self):
        block = self.src[self.src.index("const DEFAULT_SURVEY"):]
        block = block[:block.index("];") + 2]
        for word in ("Программирование", "Автоматизация", "изображений", "сервер"):
            self.assertIn(word, block)

    def test_standard_space_is_addressed_formally(self):
        self.assertIn("Address the user formally, with «вы» (never «ты»)", self.src)
        self.assertIn("Use feature and settings names exactly as written in the facts below", self.src)

    def test_automation_does_not_start_the_wizard(self):
        self.assertIn('localStorage.getItem("memu_onb_force") !== "1"', self.src)


class AppHooks(unittest.TestCase):
    app = read("app.js")

    def test_setup_cards_and_local_messages(self):
        self.assertIn('${m.setupCard ? " msg-setup" : ""}', self.app)
        self.assertIn('${m.localOnly ? " msg-local" : ""}', self.app)
        self.assertIn("MemuOnboarding.cardHtml(m, i)", self.app)
        self.assertIn("globalThis.MemuOnboarding?.start?.()", self.app)
        self.assertIn("globalThis.MemuOnboarding?.resume?.()", self.app)
        self.assertIn('["Знакомство с MemuAI", "", "onboarding"]', self.app)
        self.assertIn('settingsSlot("help", () => MemuHelp.settingsHtml({ K, L2 }))', self.app)
        self.assertNotIn("MemuTour", self.app)

    def test_setup_turns_get_no_kits(self):
        self.assertIn("memuai-setup_report", read("spaces.js"))


class HelpScript(unittest.TestCase):
    src = read("help-tips.js")

    def test_help_api_and_strings(self):
        self.assertIn("window.MemuHelp = Object.freeze({", self.src)
        for name in ("hint", "menu", "reset", "setEnabled", "settingsHtml", "bindSettings", "state"):
            self.assertRegex(self.src, r"window\.MemuHelp = Object\.freeze\(\{[^}]*\b" + name + r"\b")
        self.assertFalse(EMOJI.search(self.src))
        for ru, en in PAIR.findall(self.src):
            self.assertFalse(CYR.search(en), en)
        for hook in ('"st-tips-reset"', "st-intro-start", 'data-help="${act}"', "MemuOnboarding.restart()"):
            self.assertIn(hook, self.src)
        # First-run questions are answered in the introduction, not edited in Settings.
        self.assertNotIn("st-intro-questions", self.src)
        self.assertNotIn("MemuTour", self.src)


class TooltipLayer(unittest.TestCase):
    def test_tooltip_is_above_every_layer(self):
        js = read("tooltip.js")
        css = read("appearance.css")
        self.assertIn('tip.setAttribute("popover", "manual")', js)
        self.assertIn("tip.showPopover()", js)
        self.assertIn("tip.hidePopover()", js)
        self.assertIn(".context-menu", js[js.index("const MENU"):js.index("function element")])
        rule = css[css.index(".memu-tooltip {"):]
        rule = rule[:rule.index("}")]
        self.assertIn("z-index: 2147483647", rule)
        self.assertIn("inset: auto", rule)
        # no layer may beat the tooltip
        for path in STATIC.rglob("*.css"):
            if "vendor" in path.parts:
                continue
            for value in re.findall(r"z-index:\s*(\d+)", path.read_text(encoding="utf-8", errors="ignore")):
                self.assertLessEqual(int(value), 2147483647, path.name)

    def test_tooltip_measures_without_transforms(self):
        js = read("tooltip.js")
        self.assertIn("const own = { width: tip.offsetWidth, height: tip.offsetHeight };", js)
        self.assertIn('tip.style.left = "0px";', js)


class Preferences(unittest.TestCase):
    def test_onboarding_preferences_are_normalized(self):
        from app import pgdata
        out = pgdata._normalize_preferences({
            "onboarding_stage": "weird", "onboarding_chat": "x" * 200, "onboarding_v5_done": "yes",
            "onboarding_answers": {"uses": ["Код", "Музыка", 5], "server": "Нет", "_tags": ["code"], "bad": {"a": 1}},
            "onboarding_survey_custom": [
                {"q": "  Что слушаете?  ", "hint": "h" * 400, "type": "multi", "options": ["Рок", " ", "Джаз"], "other": 1},
                {"q": "", "type": "single", "options": ["a", "b"]},
                {"q": "Без вариантов", "type": "single", "options": []},
                "junk",
            ]})
        self.assertEqual(out["onboarding_stage"], "")
        self.assertEqual(len(out["onboarding_chat"]), 80)
        self.assertIs(out["onboarding_v5_done"], True)
        self.assertEqual(out["onboarding_answers"]["uses"], ["Код", "Музыка", "5"])
        self.assertEqual(out["onboarding_answers"]["_tags"], ["code"])
        self.assertNotIn("bad", out["onboarding_answers"])
        custom = out["onboarding_survey_custom"]
        self.assertEqual(len(custom), 2)
        self.assertEqual(custom[0]["q"], "Что слушаете?")
        self.assertEqual(len(custom[0]["hint"]), 200)
        self.assertEqual(custom[0]["options"], ["Рок", "Джаз"])
        self.assertTrue(custom[0]["other"])
        self.assertEqual(custom[0]["id"], "q1")
        self.assertEqual(custom[1]["type"], "text")
        self.assertEqual(custom[1]["options"], [])
        for stage in ("", "setup", "done"):
            self.assertEqual(pgdata._normalize_preferences({"onboarding_stage": stage})["onboarding_stage"], stage)


if __name__ == "__main__":
    unittest.main()
