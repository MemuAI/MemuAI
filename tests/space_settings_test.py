"""Рабочие области: у каждой свои настройки (характер, вид, модели, боковая панель) и панели."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import pgdata, spaces


class _Data(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._patch = patch.object(pgdata, "MEMU_DIR", self.root)
        self._patch.start()
        spaces._CACHE.clear()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()


class SpaceSettingsTest(_Data):
    def test_new_space_starts_as_copy_and_then_lives_apart(self):
        pgdata.save_preferences({"ai_personality": "friendly", "accent_color": "blue", "web_search": True})
        item = spaces.create_space({"type": "agent", "name": "Мира", "activate": True})
        own = self.root / "spaces" / item["id"] / "preferences.json"
        self.assertTrue(own.is_file())
        prefs = pgdata.load_preferences()
        self.assertEqual(prefs["ai_personality"], "friendly")
        self.assertEqual(prefs["accent_color"], "blue")
        pgdata.save_preferences({"ai_personality": "concise", "accent_color": "violet", "web_search": False})
        self.assertEqual(pgdata.load_preferences()["ai_personality"], "concise")
        base = json.loads((self.root / "preferences.json").read_text(encoding="utf-8"))
        self.assertEqual(base["ai_personality"], "friendly")
        self.assertFalse(base["web_search"], "shared settings stay shared")
        spaces.activate_space("standard")
        prefs = pgdata.load_preferences()
        self.assertEqual(prefs["ai_personality"], "friendly")
        self.assertEqual(prefs["accent_color"], "blue")
        self.assertFalse(prefs["web_search"])

    def test_space_created_before_gets_its_copy_on_first_visit(self):
        item = spaces.create_space({"type": "agent"})
        own = self.root / "spaces" / item["id"] / "preferences.json"
        own.unlink()
        pgdata.save_preferences({"chat_folders": [{"id": "f1", "name": "Учёба", "chats": []}]})
        spaces.activate_space(item["id"])
        prefs = pgdata.load_preferences()
        self.assertTrue(own.is_file())
        self.assertEqual([f.get("name") for f in prefs["chat_folders"]], ["Учёба"])
        spaces.activate_space("standard")
        pgdata.save_preferences({"chat_folders": []})
        spaces.activate_space(item["id"])
        self.assertEqual([f.get("name") for f in pgdata.load_preferences()["chat_folders"]], ["Учёба"])

    def test_revision_follows_own_settings(self):
        item = spaces.create_space({"type": "agent", "activate": True})
        before = pgdata.preferences_revision()
        pgdata.save_preferences({"ui_design": "focus"})
        self.assertNotEqual(before, pgdata.preferences_revision())
        self.assertEqual(spaces.read_overlay(spaces.preferences_overlay_path())["ui_design"], pgdata.load_preferences()["ui_design"])
        self.assertIsNotNone(item)

    def test_panels_are_per_space_seeded_from_standard(self):
        from app import panels
        std = panels.load()
        self.assertTrue(std["panels"], "the built-in weather panel is seeded")
        item = spaces.create_space({"type": "agent", "activate": True})
        self.assertEqual(Path(pgdata._profile_data_name("panels.json")), Path("spaces") / item["id"] / "panels.json")
        agent = panels.load()
        self.assertEqual([p["id"] for p in agent["panels"]], [p["id"] for p in std["panels"]])
        panels.mutate(lambda d: d["panels"].clear())
        self.assertEqual(panels.load()["panels"], [])
        spaces.activate_space("standard")
        self.assertEqual(len(panels.load()["panels"]), len(std["panels"]))

    def test_copy_settings_between_spaces(self):
        a = spaces.create_space({"type": "agent", "activate": True})
        pgdata.save_preferences({"accent_color": "violet"})
        b = spaces.create_space({"type": "standard"})
        spaces.activate_space("standard")
        pgdata.save_preferences({"accent_color": "blue"})
        spaces.copy_settings(b["id"], "standard")
        spaces.activate_space(b["id"])
        self.assertEqual(pgdata.load_preferences()["accent_color"], "blue")
        spaces.copy_settings("standard", a["id"])
        spaces.activate_space("standard")
        self.assertEqual(pgdata.load_preferences()["accent_color"], "violet")
        with self.assertRaises(ValueError):
            spaces.copy_settings("standard", "standard")

    def test_readers_outside_pgdata_see_the_workspace(self):
        pgdata.save_preferences({"default_model": "base-model"})
        spaces.create_space({"type": "agent", "activate": True})
        pgdata.save_preferences({"default_model": "agent-model"})
        raw = json.loads((self.root / "preferences.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["default_model"], "base-model")
        self.assertEqual(spaces.overlay_preferences(raw)["default_model"], "agent-model")
        from app import memu_cli
        self.assertEqual(memu_cli._space_own_prefs(self.root)["default_model"], "agent-model")
        meta = spaces.space_meta()
        self.assertEqual(meta["type"], "agent")
        self.assertIn("ai_personality", meta["own_keys"])
        self.assertNotIn("web_search", meta["own_keys"])


if __name__ == "__main__":
    unittest.main()
