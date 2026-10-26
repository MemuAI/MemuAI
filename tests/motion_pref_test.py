"""Round20 motion layer: the ui_motion preference, its migration from the old
reduce_motion switch, and the wiring of motion.css / motion.js."""
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import pgdata

ROOT = Path(__file__).resolve().parents[1]
PG = ROOT / "app" / "pgstatic"

class MotionPreferenceTest(unittest.TestCase):
    def test_default_and_validation(self):
        prefs = pgdata._normalize_preferences({})
        self.assertEqual(prefs["ui_motion"], "full")
        self.assertFalse(prefs["reduce_motion"])
        for value in ("full", "reduced", "off"):
            self.assertEqual(pgdata._normalize_preferences({"ui_motion": value})["ui_motion"], value)
        self.assertEqual(pgdata._normalize_preferences({"ui_motion": "turbo"})["ui_motion"], "full")
        self.assertEqual(pgdata._normalize_preferences({"ui_motion": None})["ui_motion"], "full")

    def test_migration_from_reduce_motion(self):
        old = pgdata._normalize_preferences({"reduce_motion": True})
        self.assertEqual(old["ui_motion"], "off")
        self.assertTrue(old["reduce_motion"])
        self.assertEqual(pgdata._normalize_preferences({"reduce_motion": "yes"})["ui_motion"], "off")
        self.assertEqual(pgdata._normalize_preferences({"reduce_motion": False})["ui_motion"], "full")

    def test_reduce_motion_mirrors_the_level(self):
        self.assertTrue(pgdata._normalize_preferences({"ui_motion": "off", "reduce_motion": False})["reduce_motion"])
        self.assertFalse(pgdata._normalize_preferences({"ui_motion": "reduced", "reduce_motion": True})["reduce_motion"])
        self.assertFalse(pgdata._normalize_preferences({"ui_motion": "full", "reduce_motion": True})["reduce_motion"])

    def test_save_roundtrip_and_legacy_writer(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(pgdata, "MEMU_DIR", Path(temp)):
            saved = pgdata.save_preferences({"ui_motion": "reduced", "reduce_motion": False})
            self.assertEqual(saved["ui_motion"], "reduced")
            self.assertEqual(json.loads((Path(temp) / "preferences.json").read_text("utf-8"))["ui_motion"], "reduced")

            saved = pgdata.save_preferences({"reduce_motion": True})
            self.assertEqual(saved["ui_motion"], "off")
            self.assertTrue(saved["reduce_motion"])

            saved = pgdata.save_preferences({"reduce_motion": False})
            self.assertEqual(saved["ui_motion"], "full")

            pgdata.save_preferences({"ui_motion": "reduced"})
            saved = pgdata.save_preferences({"reduce_motion": False})
            self.assertEqual(saved["ui_motion"], "reduced")
            self.assertEqual(pgdata.load_preferences()["ui_motion"], "reduced")

class MotionWiringTest(unittest.TestCase):
    def test_files_are_linked_after_the_designs(self):
        html = (PG / "index.html").read_text(encoding="utf-8")
        self.assertIn("/motion.css", html)
        self.assertIn("/motion.js", html)
        self.assertLess(html.index("/designs/glass.css"), html.index("/motion.css"))
        self.assertLess(html.index("/motion.js"), html.index("/app.js"))

    def test_settings_control_and_application(self):
        js = (PG / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="st-motion"', js)
        for label in ("Анимации", "Animations", "Полные", "Сокращённые", "Выкл", "Full", "Reduced", "Off"):
            self.assertIn(label, js)
        self.assertIn("dataset.motion = motionLevel", js)
        self.assertIn("savePreferences({ ui_motion: level, reduce_motion: level === \"off\" })", js)

        self.assertNotIn('id="st-reduce-motion"', js)
        self.assertIn('classList?.toggle("reduce-motion"', js)

    def test_motion_css_respects_every_off_switch(self):
        css = (PG / "motion.css").read_text(encoding="utf-8")
        self.assertIn("@media (prefers-reduced-motion: no-preference)", css)
        self.assertIn(':root[data-motion="reduced"]', css)
        self.assertIn(':root[data-motion="off"] *', css)
        self.assertIn("body:not(.reduce-motion)", css)

        for body in re.findall(r"@keyframes m-[\w-]+\s*\{(.*?)\}\s*(?=@|/\*|:root|\Z)", css, flags=re.S):
            for prop in re.findall(r"([a-z-]+)\s*:", body):
                self.assertIn(prop, {"opacity", "translate", "scale", "transform", "stroke-dashoffset",
                                     "outline-offset", "outline-color", "clip-path"}, prop)

    def test_motion_js_contract(self):
        js = (PG / "motion.js").read_text(encoding="utf-8")
        for needle in ("startViewTransition", "prefers-reduced-motion", "reduce-motion", "visibilitychange",
                       "passive: true", "requestAnimationFrame", "window.MemuMotion", "m-paused"):
            self.assertIn(needle, js)
        appearance = (PG / "appearance.js").read_text(encoding="utf-8")
        self.assertIn("MemuMotion.swap", appearance)

if __name__ == "__main__":
    unittest.main()
