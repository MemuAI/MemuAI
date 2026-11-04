"""Settings start page (search in place), every section always visible, and the MemuCode page:
static contracts and preference defaults."""
from __future__ import annotations

import os
import tempfile

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

import re  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "pgstatic"


class SettingsHomeStaticTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.js = (STATIC / "settings-home.js").read_text(encoding="utf-8")
        cls.css = (STATIC / "settings-home.css").read_text(encoding="utf-8")
        cls.kit = (STATIC / "settings-kit.js").read_text(encoding="utf-8")
        cls.app = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.html = (STATIC / "index.html").read_text(encoding="utf-8")

    def test_assets_are_loaded_after_the_settings_kit(self) -> None:
        self.assertLess(self.html.index("/settings.css"), self.html.index("/settings-home.css"))
        self.assertLess(self.html.index("/settings-kit.js"), self.html.index("/settings-home.js"))

    def test_pages_and_aliases_are_registered(self) -> None:
        for snippet in ('"home"', '"memucode"', "pane-home", "pane-memucode"):
            self.assertIn(snippet, self.app)
        self.assertRegex(self.app, r"memucode_cli:\s*\"memucode\"")

    def test_every_section_is_always_visible(self) -> None:
        # The simple/all switch is gone: nothing in settings hides behind «Показать всё».
        for gone in ("settingsView(", "setSettingsView(", "sx-view-toggle"):
            self.assertNotIn(gone, self.app)
        self.assertNotRegex(self.js, r"\bSIMPLE\s*=")
        for gone in ('[data-view="simple"]', "sx-show-all"):
            self.assertNotIn(gone, self.css)
        self.assertIn("prefers-reduced-motion", self.css)

    def test_home_search_works_in_place(self) -> None:
        self.assertIn("data-sx-search-here", self.js)
        self.assertIn("kit.find(", self.js)
        self.assertRegex(self.kit, r"function find\(query, limit")
        self.assertRegex(self.kit, r"\bfind, showAll\b")

    def test_memucode_page_is_independent_of_the_cli(self) -> None:
        for gone in ("/api/code/cli/", "code_engine", "bypassPermissions", "Кто выполняет задачи", "sxm-engine"):
            self.assertNotIn(gone, self.js)
            self.assertNotIn(gone, self.css)
        for section in ("settings-memucode-access", "settings-memucode-agent", "settings-memucode-editor",
                        "settings-memucode-terminal", "settings-memucode-tools"):
            self.assertIn(section, self.js)
        for control in ('name="sxm-access"', "sxm-model", "sxm-plan", "sxm-verify", "sxm-rules", "sxm-rounds",
                        "sxm-font", "sxm-wrap", "sxm-diff", "sxm-follow", "sxm-preview", "sxm-history-clear"):
            self.assertIn(control, self.js)
        for key in ("code_access_mode", "code_model", "code_plan_mode", "code_auto_verify", "code_project_rules",
                    "code_tool_rounds", "code_font_size", "code_wrap", "code_diff_view", "code_follow_edits"):
            self.assertIn(key, self.js)
        # Full access is confirmed before it is stored.
        self.assertIn("setCodeAccessLevel", self.js)

    def test_memucode_page_adapts_to_narrow_widths(self) -> None:
        self.assertIn("container: sxm / inline-size", self.css)
        self.assertRegex(self.css, r"@container sxm \(max-width: 740px\)")
        self.assertRegex(self.css, r"@container sxm \(max-width: 480px\)")

    def test_no_emoji_in_the_interface_text(self) -> None:
        self.assertNotRegex(self.js, re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]"))


class SettingsPreferenceTest(unittest.TestCase):
    def test_removed_preferences_are_dropped(self) -> None:
        from app import pgdata
        for key in ("settings_view", "code_engine"):
            self.assertNotIn(key, pgdata.DEFAULT_PREFERENCES)
            self.assertNotIn(key, pgdata._normalize_preferences({key: "all"}))

    def test_memucode_defaults(self) -> None:
        from app import pgdata
        prefs = pgdata._normalize_preferences({})
        expected = {"code_access_mode": "ask", "code_plan_mode": False, "code_model": "", "code_auto_verify": True,
                    "code_project_rules": True, "code_tool_rounds": 20, "code_font_size": 13, "code_wrap": False,
                    "code_diff_view": "unified", "code_follow_edits": True}
        self.assertEqual({key: prefs[key] for key in expected}, expected)

    def test_memucode_access_inherits_work_until_stored(self) -> None:
        from app import pgdata
        normalize = pgdata._normalize_preferences
        self.assertEqual(normalize({"work_access_mode": "full"})["code_access_mode"], "full")
        self.assertEqual(normalize({"work_access_mode": "ask"})["code_access_mode"], "ask")
        self.assertEqual(normalize({"work_access_mode": "full", "code_access_mode": "edits"})["code_access_mode"], "edits")
        self.assertEqual(normalize({"code_access_mode": "rocket"})["code_access_mode"], "ask")
        self.assertTrue(normalize({"work_plan_mode": True})["code_plan_mode"])
        self.assertFalse(normalize({"work_plan_mode": True, "code_plan_mode": False})["code_plan_mode"])

    def test_memucode_values_are_clamped(self) -> None:
        from app import pgdata
        normalize = pgdata._normalize_preferences
        self.assertEqual(normalize({"code_tool_rounds": 2})["code_tool_rounds"], 6)
        self.assertEqual(normalize({"code_tool_rounds": 999})["code_tool_rounds"], 50)
        self.assertEqual(normalize({"code_tool_rounds": "abc"})["code_tool_rounds"], 20)
        self.assertEqual(normalize({"code_font_size": 30})["code_font_size"], 18)
        self.assertEqual(normalize({"code_font_size": 5})["code_font_size"], 11)
        self.assertEqual(normalize({"code_diff_view": "weird"})["code_diff_view"], "unified")
        self.assertEqual(normalize({"code_diff_view": "split"})["code_diff_view"], "split")
        self.assertEqual(len(normalize({"code_model": "m" * 400})["code_model"]), 300)
        self.assertIs(normalize({"code_wrap": "false"})["code_wrap"], False)

    def test_memucode_preferences_round_trip(self) -> None:
        from app import pgdata
        pgdata.save_preferences({"code_access_mode": "edits", "code_tool_rounds": 35, "code_diff_view": "split",
                                 "code_model": "  some-model  "})
        stored = pgdata.load_preferences()
        self.assertEqual((stored["code_access_mode"], stored["code_tool_rounds"], stored["code_diff_view"],
                          stored["code_model"]), ("edits", 35, "split", "some-model"))
        pgdata.save_preferences({"code_access_mode": "nonsense"})
        self.assertEqual(pgdata.load_preferences()["code_access_mode"], "ask")


if __name__ == "__main__":
    unittest.main()
