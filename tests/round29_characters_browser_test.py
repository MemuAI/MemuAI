# -*- coding: utf-8 -*-
"""Round 29: the «Characters» module end-to-end in Chromium against a mocked host bridge.

Covers: boot with the default preset, creating a character, a streamed reply with thoughts, the exact
message array sent (SillyTavern order), swipes, continue, editing, PNG card export -> import round trip,
the drawer panels and the English UI having no Cyrillic. Skips when Chromium/Playwright are missing.
"""
from __future__ import annotations

import base64
import json
import re
import unittest

from tests.round29_characters_harness import chromium, frame_of, open_page

try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover
    sync_playwright = None


@unittest.skipUnless(sync_playwright and chromium(), "playwright + chromium required")
class CharactersBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser, cls.page, cls.errors = open_page(cls.pw)
        cls.page.wait_for_timeout(1200)
        cls.frame = frame_of(cls.page)
        cls.frame.evaluate("() => { window.M = window.MemuCharacters; }")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def ev(self, script, arg=None):
        return self.frame.evaluate(script, arg)

    def wait(self, script, timeout=8000):
        step = 100
        for _ in range(timeout // step):
            if self.frame.evaluate(script):
                return True
            self.page.wait_for_timeout(step)
        self.fail("timeout waiting for: " + script)

    def test_1_boot_and_preset(self):
        self.assertTrue(self.ev("() => !!document.querySelector('.ch-hero')"))
        self.assertEqual(self.ev("() => M.S.settings.presetId"), "universal")
        self.assertGreater(self.ev("() => M.S.preset.prompts.length"), 20)
        self.assertEqual(self.ev("() => M.S.settings.model"), "test/model-a")

    def test_2_create_character_and_chat(self):
        self.frame.click("text=New character")
        self.frame.wait_for_selector(".ch-editor")
        fields = self.frame.query_selector_all(".ch-editor .mu-input, .ch-editor .mu-textarea")
        fields[0].fill("Alice")
        fields[1].fill("{{char}} is a kind witch who lives in a tower. {{user}} is her apprentice.")
        fields[2].fill("Welcome, {{user}}!")
        self.frame.click(".ch-modal-foot >> text=Create")
        self.wait("() => M.S.view === 'chat' && M.S.char && M.S.char.data.name === 'Alice'")
        self.wait("() => document.querySelectorAll('.ch-msg').length === 1")
        greeting = self.ev("() => document.querySelector('.ch-msg .ch-text').textContent")
        self.assertIn("Welcome, User!", greeting)  # phase 2: starter persona «User», not the pronoun «You»
        # send a message
        self.frame.fill("#composer-text", "Hi Alice, teach me a spell.")
        self.frame.press("#composer-text", "Enter")
        self.wait("() => M.S.chat.messages.length === 3 && !M.S.busy")
        last = self.ev("() => M.S.chat.messages[2]")
        self.assertEqual(last["mes"], 'Hello there! *waves* "Nice to meet you."')
        self.assertEqual(last["reasoning"], "Let me think.")
        sent = self.page.evaluate("() => window.__sent[window.__sent.length - 1]")
        self.assertEqual(sent["model"], "test/model-a")
        roles = [m["role"] for m in sent["messages"]]
        self.assertEqual(roles[-2:], ["assistant", "user"])
        system = sent["messages"][0]["content"]
        self.assertIn("Alice is a kind witch", system)              # charDescription marker filled, macros resolved
        self.assertIn("User is her apprentice", system)
        self.assertNotIn("{{", system)
        self.assertEqual(sent["params"]["temperature"], 1)            # universal file says 2; index.json recommends 1.0 (effective)
        self.assertEqual(sent["params"]["effort"], "max")
        html = self.ev("() => document.querySelectorAll('.ch-msg')[2].querySelector('.ch-text').innerHTML")
        self.assertIn("<em", html)
        self.assertIn('class="ch-q"', html)

    def test_3_swipe_continue_edit(self):
        self.page.evaluate("() => { window.__reply = 'Second variant.'; }")
        self.ev("() => M.generate('swipe')")
        self.wait("() => !M.S.busy && M.S.chat.messages[2].swipes.length === 2")
        self.assertEqual(self.ev("() => M.S.chat.messages[2].mes"), "Second variant.")
        self.ev("() => M.swipe(M.S.chat.messages[2], -1)")
        self.assertIn("Hello there", self.ev("() => M.S.chat.messages[2].mes"))
        self.page.evaluate("() => { window.__reply = ' And more.'; }")
        self.ev("() => M.generate('continue')")
        self.wait("() => !M.S.busy && M.S.chat.messages[2].mes.endsWith('And more.')")
        sent = self.page.evaluate("() => window.__sent[window.__sent.length - 1]")
        # the universal preset continues as a prefill: the message being continued goes last, as the assistant
        self.assertEqual(sent["messages"][-1]["role"], "assistant")
        self.assertIn("Hello there", sent["messages"][-1]["content"])
        self.ev("() => M.startEdit(M.S.chat.messages[1])")
        self.frame.fill(".ch-edit-area", "Edited message")
        self.frame.click(".ch-edit >> text=Save")
        self.assertEqual(self.ev("() => M.S.chat.messages[1].mes"), "Edited message")

    def test_4_png_round_trip(self):
        self.ev("() => M.exportCharacter(M.S.char.id, 'png')")
        self.page.wait_for_timeout(800)
        downloads = self.page.evaluate("() => window.__downloads")
        png = [d for d in downloads if d["name"].endswith(".png")][-1]
        raw = base64.b64decode(png["url"].split(",", 1)[1])
        self.assertEqual(raw[:8], b"\x89PNG\r\n\x1a\n")
        self.assertIn(b"tEXtchara", raw)
        self.assertIn(b"tEXtccv3", raw)
        chunk = raw[raw.index(b"tEXtchara") + 10:]
        b64 = chunk[:chunk.index(b"\x00\x00") if b"\x00\x00" in chunk else len(chunk)]
        card = self.ev("(url) => M.E.readCardFromPng(M.E.base64ToBytes(url.split(',')[1]))", png["url"])
        self.assertEqual(card["spec"], "chara_card_v3")
        self.assertEqual(card["data"]["name"], "Alice")
        self.assertTrue(b64)

    def test_5_drawer_and_english(self):
        for tab in ("preset", "connection", "worlds", "personas", "appearance", "extras"):
            self.ev("(t) => M.openDrawer(t)", tab)
            self.page.wait_for_timeout(150)
            text = self.ev("() => document.getElementById('drawer').innerText")
            self.assertFalse(re.search("[А-Яа-яЁё]", text), f"Cyrillic in the English {tab} tab")
        self.ev("() => M.openInspector()")
        self.page.wait_for_timeout(200)
        self.assertTrue(self.ev("() => !!document.querySelector('.ch-insp')"))
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
