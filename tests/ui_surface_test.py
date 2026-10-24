"""Source contracts for the skills header, sound picker, model names, and prompt text."""
from __future__ import annotations

import unittest
from pathlib import Path

from app.pgdata import INTERNAL_PROMPTS


ROOT = Path(__file__).resolve().parents[1]


class PromptCatalogueText(unittest.TestCase):
    def test_media_router_russian_description_is_intact(self) -> None:
        # Round 29 (r29c-media): the unused "image-generation-router" was replaced by the real media router.
        self.assertFalse(any(item["id"] == "image-generation-router" for item in INTERNAL_PROMPTS))
        row = next(item for item in INTERNAL_PROMPTS if item["id"] == "media-router")
        text = row["description_ru"]
        self.assertIn("модели решений", text)
        self.assertNotIn("\ufffd", text)


class InterfaceSourceContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.css = (ROOT / "app" / "pgstatic" / "style.css").read_text(encoding="utf-8")
        self.appearance = (ROOT / "app" / "pgstatic" / "appearance.css").read_text(encoding="utf-8")
        self.js = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")

    def test_skills_actions_use_the_full_row(self) -> None:
        block = self.appearance.split(".skills-head-actions", 1)[1].split("}", 1)[0]
        self.assertIn("width: 100%", block)
        self.assertIn("minmax(0, 1fr)", block)
        self.assertIn("grid-template-columns", block)
        group = self.appearance.split(".skills-action-group", 1)[1].split("}", 1)[0]
        self.assertIn("width: 100%", group)

    def test_sound_picker_is_themed_and_does_not_clip(self) -> None:
        block = self.css.split(".sound-picker {", 1)[1].split("}", 1)[0]
        self.assertNotIn("overflow: hidden", block)
        self.assertNotIn("height:", block)
        self.assertNotIn('id="st-notify-sound-style"', self.js)
        self.assertIn("sound-choice", self.js)
        self.assertIn("st-notify-sound-preview", self.js)
        picker = self.js.split('class="sound-picker"', 1)[1].split("</div>", 1)[0]
        self.assertNotIn("<select", picker)

    def test_model_lists_show_the_full_id_without_horizontal_scroll(self) -> None:
        self.assertIn("model-id-full", self.js)
        self.assertIn("${esc(x.id)}", self.js)
        self.assertIn("modelOptionLabel", self.js)
        visibility = self.css.split(".model-visibility {", 1)[1].split("}", 1)[0]
        self.assertIn("overflow-x: hidden", visibility)
        label = self.css.split(".model-visibility span", 1)[1].split("}", 1)[0]
        self.assertIn("overflow-wrap: anywhere", label)
        self.assertNotIn("ellipsis", label)
        self.assertNotIn("nowrap", label)
        names = self.css.split(".model-id-full", 1)[1].split("}", 1)[0]
        self.assertIn("overflow-wrap: anywhere", names)
        self.assertIn(".dd-list { overflow-x: hidden;", self.css)


if __name__ == "__main__":
    unittest.main()
