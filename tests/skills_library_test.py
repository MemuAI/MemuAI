"""AI skills: rebuilt library and the progressive disclosure protocol."""
import pathlib
import re
import unittest

PG = pathlib.Path(__file__).resolve().parents[1] / "app" / "pgstatic"
REQUIRED_FIELDS = (
    "name:", "name_ru:", "category:", "category_ru:",
    "desc:", "desc_ru:", "when:", "when_ru:", "prompt:",
)


class SkillsLibraryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.library = (PG / "skills.js").read_text(encoding="utf-8")
        cls.app = (PG / "app.js").read_text(encoding="utf-8")
        cls.html = (PG / "index.html").read_text(encoding="utf-8")
        cls.blocks = re.split(r'\n  \{\n    id: "', cls.library)[1:]

    def test_library_is_exposed_with_a_version(self):
        self.assertIn("window.MEMUAI_SKILL_LIBRARY", self.library)
        self.assertRegex(self.library, r"version: [45]\b")  # Round 32: v5 keeps the on/off state of built-ins

    def test_library_is_loaded_before_the_app(self):
        self.assertLess(self.html.index("/skills.js"), self.html.index("/app.js"))

    def test_every_skill_is_complete_and_substantial(self):
        self.assertGreaterEqual(len(self.blocks), 18)
        ids = []
        for block in self.blocks:
            skill_id = block.split('"', 1)[0]
            ids.append(skill_id)
            for field in REQUIRED_FIELDS:
                self.assertIn(field, block, skill_id)
            playbook = block.split("prompt:", 1)[1]
            self.assertGreater(len(playbook), 900, skill_id)
        self.assertEqual(len(ids), len(set(ids)))

    def test_old_micro_skills_are_gone(self):
        for retired in ('id: "web_dev"', 'id: "copywriter"', 'id: "office"', "BUILTIN_SKILL_RU"):
            self.assertNotIn(retired, self.app)
            self.assertNotIn(retired, self.library)

    def test_app_uses_the_catalogue_protocol(self):
        self.assertNotIn("<active_skills>", self.app)
        self.assertIn("<skills_protocol>", self.app)
        self.assertIn("memuai-skill", self.app)
        self.assertIn("function deliverSkillInstructions", self.app)
        self.assertIn("skill_instructions", self.app)
        self.assertIn("SKILL_LOAD_LIMIT", self.app)
        self.assertIn('assistant.skillRequest?.status === "pending"', self.app)

    def test_skill_cards_stay_editable(self):
        self.assertIn('data-skill-edit="${esc(s.id)}"', self.app)
        self.assertIn("eskill-restore", self.app)


if __name__ == "__main__":
    unittest.main()
