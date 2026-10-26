# -*- coding: utf-8 -*-
"""Phase-2 wave D regressions for the «Characters» module in Chromium (mock host from round29_characters_harness).

* «Universal»: effective temperature 1.0 with a visible note; «Use 2.0 from the preset» keeps 2.0 as the user's edit;
* the settings drawer overlays the chat (scrim, click closes) at ~1060px instead of squeezing it; ≤760px the chat is visible;
* macros the person types are evaluated once at send and stored;
* a leaked control token ends the answer;
* the untouched starter persona follows the UI language;
* the import preview resolves {{char}}/{{user}}; the English screens carry no Cyrillic.
"""
from __future__ import annotations

import json
import unittest

from tests import round29_characters_harness as harness
from tests.round29_characters_harness import chromium, frame_of, open_page

try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover
    sync_playwright = None

@unittest.skipUnless(sync_playwright and chromium(), "playwright + chromium required")
class CharactersPhase2DBrowserTest(unittest.TestCase):
    def setUp(self):
        self.pw = sync_playwright().start()
        self.browser = None

    def tearDown(self):
        if self.browser:
            self.browser.close()
        self.pw.stop()

    def boot(self, size=(1360, 860), lang="en", store=None):
        original = harness.PARENT
        if store:
            harness.PARENT = original.replace("window.__store = {};", "window.__store = " + json.dumps(store, ensure_ascii=False) + ";", 1)
        try:
            self.browser, self.page, self.errors = open_page(self.pw, lang=lang, size=size)
        finally:
            harness.PARENT = original
        self.page.wait_for_timeout(1500)
        self.frame = frame_of(self.page)
        return self.frame

    def wait(self, js, timeout=8000):
        self.frame.wait_for_function(js, timeout=timeout)

    def make_char(self):
        f = self.frame
        f.click("text=New character")
        f.wait_for_selector(".ch-editor")
        fields = f.query_selector_all(".ch-editor .mu-input, .ch-editor .mu-textarea")
        fields[0].fill("Alice")
        fields[2].fill("Welcome, {{user}}!")
        f.click(".ch-modal-foot >> text=Create")
        f.wait_for_selector("#composer-text", timeout=8000)

    def send(self, text):
        self.frame.fill("#composer-text", text)
        self.frame.press("#composer-text", "Enter")
        self.page.wait_for_timeout(300)
        self.wait("() => !window.MemuCharacters.S.busy")

    def test_universal_temperature_note(self):
        f = self.boot()
        self.make_char()
        S = "window.MemuCharacters.S"
        self.assertEqual(f.evaluate(f"() => {S}.preset.temperature"), 1)
        f.evaluate("() => window.MemuCharacters.openDrawer('preset')")
        f.wait_for_selector("#drawer .ch-temp-note")
        note = f.inner_text("#drawer .ch-temp-note")
        self.assertIn("2.0", note)
        self.assertIn("1.0", note)
        self.send("hi")
        self.assertEqual(self.page.evaluate("() => window.__sent[window.__sent.length - 1].params.temperature"), 1)
        f.click("#drawer .ch-temp-note button")
        self.page.wait_for_timeout(800)
        self.assertEqual(f.evaluate(f"() => {S}.preset.temperature"), 2)
        self.assertEqual(f.input_value("#drawer .ch-slider input.ch-num >> nth=2"), "2")

        self.assertEqual(self.page.evaluate("() => window.__store['presets/universal'].temperature"), 2)
        self.assertTrue(f.is_visible("#drawer .ch-temp-note.warn"))
        f.evaluate("() => window.MemuCharacters.render()")
        self.assertEqual(self.errors, [])

    def test_drawer_overlays_at_narrow_width(self):
        f = self.boot(size=(1060, 760))
        self.make_char()
        before = f.evaluate("() => document.getElementById('main').getBoundingClientRect().width")
        f.evaluate("() => window.MemuCharacters.openDrawer('preset')")
        self.page.wait_for_timeout(500)
        pos = f.evaluate("() => getComputedStyle(document.getElementById('drawer')).position")
        after = f.evaluate("() => document.getElementById('main').getBoundingClientRect().width")
        self.assertEqual(pos, "absolute")
        self.assertGreater(after, before - 20)
        self.page.mouse.click(120, 400)
        self.page.wait_for_timeout(300)
        self.assertFalse(f.evaluate("() => window.MemuCharacters.S.drawerOpen"))
        self.assertEqual(self.errors, [])

    def test_drawer_docks_when_wide(self):
        f = self.boot(size=(1500, 860))
        self.make_char()
        f.evaluate("() => window.MemuCharacters.openDrawer('preset')")
        self.page.wait_for_timeout(500)
        self.assertNotEqual(f.evaluate("() => getComputedStyle(document.getElementById('drawer')).position"), "absolute")

    def test_chat_visible_at_mobile_width(self):
        f = self.boot(size=(740, 700))
        self.make_char()
        width = f.evaluate("() => document.getElementById('main').getBoundingClientRect().width")
        self.assertGreater(width, 600)

    def test_user_macros_evaluated_once(self):
        f = self.boot()
        self.make_char()
        self.send("I roll {{roll:1d1}} for {{char}} and {{user}}")
        mes = f.evaluate("() => window.MemuCharacters.S.chat.messages[1].mes")
        self.assertEqual(mes, "I roll 1 for Alice and User")
        sent = self.page.evaluate("() => window.__sent[window.__sent.length - 1].messages")
        self.assertTrue(any(m["content"] == "I roll 1 for Alice and User" for m in sent))

    def test_leaked_token_ends_answer(self):
        f = self.boot()
        self.page.evaluate("() => { window.__reply = 'Sure thing.<｜begin▁of▁sentence｜># Unrelated article text that goes on and on'; }")
        self.make_char()
        self.send("hi")
        mes = f.evaluate("() => window.MemuCharacters.S.chat.messages[2].mes")
        self.assertEqual(mes, "Sure thing.")

    def test_english_screens_have_no_cyrillic(self):
        """The module's own English UI, screen by screen: gallery, chat, every drawer tab, import, inspector, editors."""
        f = self.boot(size=(1500, 900))
        collect = """() => { const out = []; const seen = new Set();
          const shown = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden'; };
          document.querySelectorAll('body *').forEach((el) => {
            if (el.closest('script,style,.ch-text,.ch-msg-body,textarea') || !shown(el)) return;
            for (const n of el.childNodes) if (n.nodeType === 3 && /[А-Яа-яЁё]/.test(n.textContent)) out.push(n.textContent.trim().slice(0, 80));
            for (const a of ['title', 'placeholder', 'aria-label']) { const v = el.getAttribute(a); if (v && /[А-Яа-яЁё]/.test(v)) out.push(a + ': ' + v.slice(0, 80)); }
          });
          return out.filter((x) => !seen.has(x) && seen.add(x)); }"""
        found = {}
        found["home"] = f.evaluate(collect)
        self.make_char()
        self.send("hi")
        found["chat"] = f.evaluate(collect)
        for tab in ("preset", "connection", "worlds", "personas", "appearance", "extras"):
            f.evaluate(f"() => window.MemuCharacters.openDrawer('{tab}')")
            self.page.wait_for_timeout(300)
            found["drawer:" + tab] = f.evaluate(collect)
        f.evaluate("() => { window.MemuCharacters.S.drawerOpen = false; window.MemuCharacters.render(); }")
        f.evaluate("() => window.MemuCharacters.openInspector()")
        self.page.wait_for_timeout(400)
        found["inspector"] = f.evaluate(collect)
        self.page.keyboard.press("Escape")
        f.evaluate("() => window.MemuCharacters.importFiles([])")
        self.page.wait_for_timeout(400)
        found["import"] = f.evaluate(collect)
        bad = {k: v for k, v in found.items() if v}
        self.assertEqual(bad, {})

    def test_import_preview_resolves_macros(self):
        """The import preview shows the card like the chat will: {{char}}/{{user}} filled in, nothing raw."""
        f = self.boot()
        card = {"spec": "chara_card_v2", "spec_version": "2.0", "data": {
            "name": "Mira", "description": "{{char}} runs the inn and greets {{user}} by name.",
            "first_mes": "*{{char}} wipes the counter.* Welcome, {{user}}!", "personality": "", "scenario": "",
            "mes_example": "", "creator_notes": "", "tags": ["inn"]}}
        f.evaluate("""(card) => window.MemuCharacters.importFiles([new File([JSON.stringify(card)], 'mira.json', {type: 'application/json'})])""", card)
        f.wait_for_selector(".ch-imp-card.kind-character .ch-imp-first", timeout=8000)
        first = f.inner_text(".ch-imp-card.kind-character .ch-imp-first")
        blurb = f.inner_text(".ch-imp-card.kind-character .ch-imp-blurb")
        self.assertIn("Mira wipes the counter.", first)
        self.assertIn("Welcome, User!", first)
        self.assertIn("Mira runs the inn and greets User by name.", blurb)
        self.assertNotIn("{{", first + blurb)

        f.click(".ch-modal-foot .mu-btn.primary")
        self.wait("() => Object.values(window.MemuCharacters.S.chars).some((c) => c.data && c.data.name === 'Mira')")
        stored = f.evaluate("() => Object.values(window.MemuCharacters.S.chars).find((c) => c.data.name === 'Mira').data.first_mes")
        self.assertEqual(stored, "*{{char}} wipes the counter.* Welcome, {{user}}!")
        self.assertEqual(self.errors, [])

    def test_starter_persona_follows_language(self):
        store = {"personas": [{"id": "p1", "name": "Пользователь", "description": "", "avatar": "", "position": 0, "depth": 2, "role": 0}],
                 "settings": {"version": 1, "personaId": "p1"}}
        f = self.boot(lang="en", store=store)
        self.assertEqual(f.evaluate("() => window.MemuCharacters.S.personas[0].name"), "User")

if __name__ == "__main__":
    unittest.main()
