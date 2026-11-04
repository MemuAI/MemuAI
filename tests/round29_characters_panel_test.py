# -*- coding: utf-8 -*-
"""The Characters library stays on the left and the optional character card opens on the right.

The layout itself is asserted on the real stylesheet (jsdom has no layout
engine, so the declared grid is the source of truth), the rendered panel
is covered by the jsdom smoke test in
tests/round29_characters_panel_right_test.cjs, which runs the real
renderChatSidebar() from the assembled module JS.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MODULE = ROOT / "app" / "module_builtins" / "characters"

# Keys renderChatSidebar() reads through t().
PANEL_KEYS = (
    "character_panel", "character_panel_width", "library_width", "edit_character_full",
    "unnamed", "catalog_by", "catalog_tokens_short", "new_chat", "chats", "manage_chats",
    "untitled_chat", "copy_avatar", "download_avatar", "avatar_copied", "avatar_unavailable",
)


class PanelRightTest(unittest.TestCase):
    def test_library_left_and_character_card_right_in_css(self):
        css = (MODULE / "style.css").read_text(encoding="utf-8")
        self.assertIn("grid-template-columns:var(--ch-library-track) minmax(0,1fr) var(--ch-card-track)", css)
        self.assertIn(".ch-main{grid-column:2;", css)
        self.assertRegex(css, r"\.ch-side\{[^}]*grid-column:1;")
        self.assertIn(".ch-character-panel{grid-column:3;", css)
        self.assertIn(".ch-character-panel[hidden]{display:none}", css)
        self.assertIn(".ch-app.ch-side-hidden .ch-side{transform:translateX(-105%)", css)

    def test_redesigned_card_classes_exist(self):
        css = (MODULE / "style.css").read_text(encoding="utf-8")
        for cls in ("ch-inspector-identity", "ch-side-info", "ch-side-av",
                    "ch-inspector-actions", "ch-inspector-section", "ch-chat-menu"):
            self.assertIn("." + cls, css)
        # The active chat is explicit: accent bar, not just a tint.
        self.assertIn(".ch-chat-menu .ch-menu-item.active", css)
        self.assertIn(".ch-split-right{grid-column:3;", css)

    def test_panel_i18n_keys(self):
        i18n = json.loads((MODULE / "i18n.json").read_text(encoding="utf-8"))
        self.assertEqual(set(i18n["ru"]), set(i18n["en"]))
        for key in PANEL_KEYS:
            self.assertIn(key, i18n["ru"])
            self.assertIn(key, i18n["en"])

    def test_panel_ui_smoke(self):
        """The real renderChatSidebar() in jsdom (see the .cjs file).

        Exit code 2 from the script means jsdom is not installed in this
        checkout — the smoke is skipped then, like the Playwright tests.
        """
        done = subprocess.run(
            ["node", str(ROOT / "tests" / "round29_characters_panel_right_test.cjs")],
            capture_output=True, text=True, timeout=180)
        if done.returncode == 2 and "jsdom not available" in done.stdout:
            self.skipTest("jsdom is not installed in this checkout")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertIn("characters panel-right smoke OK", done.stdout)

    def test_panel_is_in_built_module(self):
        from app import modules_host
        row = modules_host.load_package(ROOT, "characters")
        self.assertIn("renderChatSidebar", row["js"])
        self.assertIn("ch-inspector-identity", row["js"])
        self.assertIn("ch-side-info", row["js"])
        with tempfile.TemporaryDirectory() as temp:
            built = Path(temp) / "built.js"
            built.write_text(row["js"], encoding="utf-8")
            done = subprocess.run(["node", "--check", str(built)],
                                  capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)


if __name__ == "__main__":
    unittest.main()
