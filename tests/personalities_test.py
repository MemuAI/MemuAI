"""MemuAI personalities: one catalogue (app/personalities.py) for every surface."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "pgstatic"


class CatalogueTest(unittest.TestCase):
    def test_catalogue_has_the_requested_personalities(self):
        from app import personalities as p
        ids = set(p.IDS)
        for wanted in ("friendly", "pragmatic", "creative", "teacher", "kawaii", "catgirl", "uwu", "shakespeare",
                       "philosopher", "helpful", "concise", "technical", "pirate", "surfer", "noir", "hype"):
            self.assertIn(wanted, ids)
        self.assertGreaterEqual(len(ids), 30)
        self.assertEqual(len(ids), len(p.PERSONALITIES), "duplicate ids")
        cats = {c["id"] for c in p.CATEGORIES}
        for row in p.PERSONALITIES:
            self.assertIn(row["category"], cats, row["id"])
            self.assertTrue(row["name"] and row["name_ru"] and row["description"] and row["description_ru"], row["id"])
            self.assertLess(len(row["text"]), 600, row["id"])
            self.assertIsNone(re.search(r"[А-Яа-яЁё]", row["text"]), row["id"])

    def test_old_ids_and_texts_survive(self):
        from app import personalities as p
        self.assertEqual(p.normalize("pragmatic"), "pragmatic")
        self.assertEqual(p.normalize("unknown"), "friendly")
        self.assertEqual(p.normalize(None), "friendly")
        self.assertTrue(p.default_text("friendly").startswith("Be warm, attentive and encouraging"))
        self.assertTrue(p.default_text("pragmatic").startswith("Be direct, pragmatic, concise and precise"))

    def test_block_uses_the_edited_text_and_the_shared_rule(self):
        from app import personalities as p
        block = p.block({"ai_personality": "pirate"}, lookup=lambda pid, fallback: "Talk like a pirate." if pid == "personality-pirate" else fallback)
        self.assertTrue(block.startswith("<personality>Talk like a pirate."))
        self.assertIn("never facts", block)
        plain = p.block({"ai_personality": "friendly"}, lookup=lambda pid, fallback: fallback)
        self.assertNotIn("never facts", plain)

    def test_internal_prompts_expose_every_personality(self):
        from app import pgdata, personalities as p
        ids = {row["id"] for row in pgdata.INTERNAL_PROMPTS}
        for pid in p.IDS:
            self.assertIn("personality-" + pid, ids)
        row = next(r for r in pgdata.INTERNAL_PROMPTS if r["id"] == "personality-catgirl")
        self.assertEqual(row.get("group"), "personality")

    def test_preferences_keep_any_known_personality(self):
        from app import pgdata
        self.assertEqual(pgdata._normalize_preferences({"ai_personality": "shakespeare"})["ai_personality"], "shakespeare")
        self.assertEqual(pgdata._normalize_preferences({"ai_personality": "pragmatic"})["ai_personality"], "pragmatic")
        self.assertEqual(pgdata._normalize_preferences({"ai_personality": "<script>"})["ai_personality"], "friendly")
        self.assertEqual(pgdata._normalize_preferences({})["ai_personality"], "friendly")

    def test_mini_app_enum_lists_every_personality(self):
        from app import personalities as p, tgapp_api
        self.assertEqual(list(tgapp_api.SETTINGS["ai_personality"]["options"]), list(p.IDS))
        source = (ROOT / "app" / "tgapp_api.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count('"personalities": personalities.catalog('), 2)
        cat = p.catalog("en")
        self.assertEqual(cat["default"], "friendly")
        self.assertEqual(len(cat["items"]), len(p.IDS))
        self.assertTrue(all(item["name"] and item["description"] for item in cat["items"]))

    def test_shared_rule_is_mirrored_in_the_browser(self):
        from app import personalities as p
        js = (STATIC / "personalities.js").read_text(encoding="utf-8")
        self.assertIn(json.dumps(p.SHARED_RULE, ensure_ascii=False), js)

    def test_no_surface_hardcodes_two_personalities(self):
        for name in ("telegram_bot.py", "telegram_use_ai.py", "tgapp_chat.py", "browser_extension_chat.py"):
            text = (ROOT / "app" / name).read_text(encoding="utf-8")
            self.assertNotIn('== "pragmatic" else "friendly"', text, name)
            self.assertNotIn('"pragmatic" if prefs.get("ai_personality")', text, name)


class FrontendTest(unittest.TestCase):
    def test_gallery_is_loaded_and_wired(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertIn("/personalities.js?v=", html)
        self.assertIn("/personalities.css?v=", html)
        app = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn("st-personality-gallery", app)
        self.assertIn("MemuPersonalities", app)
        self.assertNotIn('pref("ai_personality", "friendly") === "pragmatic" ? "pragmatic" : "friendly"', app)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_scripts_parse(self):
        for name in ("personalities.js", "app.js"):
            proc = subprocess.run(["node", "--check", str(STATIC / name)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_mini_app_uses_server_catalogue(self):
        js = (ROOT / "app" / "tgapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn("S.personalities", js)


if __name__ == "__main__":
    unittest.main()
