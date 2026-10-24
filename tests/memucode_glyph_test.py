"""MemuCode's terminal text must not contain characters that Windows Terminal draws as coloured emoji.

U+2733 (✳) is the reason: it is an "emoji" character, Windows Terminal renders it as a green tile, and it used
to flash in the middle of the working animation. Symbols that have a plain text form (✻, ●, ⎿, ⚠) are fine.
"""
from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1] / "app" / "cli"
# Characters with an emoji presentation that are drawn as coloured tiles or wide glyphs.
BANNED = set("✳✴❇✅✔✨❌❎☑⭐➕➖➗✖❗❓❔❕⚡⏩⏪⏫⏬⏭⏮⏯⏰⏱⏲⏳")
SPINNER_FRAMES = "·✢*✶✻✽"


class GlyphSafety(unittest.TestCase):
    def test_no_emoji_tiles_in_the_interface_sources(self):
        problems = []
        for path in ROOT.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r"[^\x00-⿿]|[✳✴❇✅✔✨❌❎☑⭐➕➖➗✖❗❓❔❕⚡⏩⏪⏫⏬⏭⏮⏯⏰⏱⏲⏳]", text):
                char = match.group()
                if char in BANNED or ord(char) >= 0x1F000:
                    problems.append(f"{path.name}: U+{ord(char):04X} {char}")
        self.assertEqual(problems, [], "emoji-style characters in MemuCode sources")

    def test_spinner_frames_are_plain_symbols(self):
        from app.cli.tui.app import SPINNER
        for frame in SPINNER:
            self.assertIn(frame, SPINNER_FRAMES)
            self.assertNotIn(frame, BANNED)


if __name__ == "__main__":
    unittest.main()
