# -*- coding: utf-8 -*-
"""Контракт раздела «Мемесы» (Round 4, пункт 10).

Функция целиком живёт в интерфейсе, поэтому проверяем контракт исходников:
раздел в боковой панели, перетаскивание разделов, мастер из 4 окон,
создание через ИИ и приоритет инструкций в системном промпте.
"""
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def source(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as handle:
        return handle.read()

class MemeSidebarTest(unittest.TestCase):
    def setUp(self):
        self.html = source("app/pgstatic/index.html")
        self.js = source("app/pgstatic/app.js")
        self.css = source("app/pgstatic/appearance.css")

    def test_section_exists_above_chats(self):
        self.assertIn('id="group-memes"', self.html)
        self.assertIn('id="memes-list"', self.html)
        self.assertIn('id="btn-meme-new"', self.html)
        self.assertLess(self.html.index('id="group-memes"'), self.html.index('id="chats-list"'))

    def test_all_three_sections_are_draggable(self):
        for section in ("folders", "memes", "chats"):
            self.assertIn('data-section="%s"' % section, self.html, section)
            self.assertIn('data-section-handle="%s"' % section, self.html, section)
        self.assertIn("memu_section_order", self.js)
        self.assertIn("applySectionOrder", self.js)
        self.assertIn(".section-drop", self.css)

    def test_storage_and_chat_link(self):
        for marker in ("memu_memes", "memu_chat_memes", "memeIdForChat", "setMemeForChat", "startMemeChat"):
            self.assertIn(marker, self.js, marker)

        self.assertIn(".filter((c) => !memeIdForChat(c.id))", self.js)
        self.assertIn("meme-chat", self.css)

    def test_manual_wizard_has_four_windows(self):
        self.assertIn("renderMemeWizard", self.js)
        self.assertIn("memeWizardHead", self.js)
        for marker in ("meme-instructions", "meme-model", "data-meme-cap", "meme-review"):
            self.assertIn(marker, self.js, marker)
        self.assertIn('id="meme-save"', self.js)

    def test_capabilities_include_planning_and_telegram(self):
        for key in ("planning", "telegram", "tools", "integrations", "files", "memory", "skills"):
            self.assertIn('key: "%s"' % key, self.js, key)
        self.assertIn("silent_telegram", self.js)

    def test_ai_creation_path(self):
        self.assertIn("generateMemeWithAi", self.js)
        self.assertIn("openMemeAiPanel", self.js)
        self.assertIn("/api/pg/chat", self.js)
        self.assertIn("You design custom agents", self.js)

    def test_instructions_have_highest_priority(self):
        self.assertIn("memePromptBlock", self.js)
        self.assertIn("agent_profile", self.js)
        self.assertIn("highest", self.js)

        self.assertIn('sys = memeBlock + (sys ? "\\n\\n" + sys : "")', self.js)

if __name__ == "__main__":
    unittest.main()
