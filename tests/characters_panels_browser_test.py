"""Exercise the real Characters document and its bridge in an isolated browser."""

import base64
import re
import unittest
from unittest.mock import patch

from tests import round29_characters_harness as harness

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:
    sync_playwright = None


@unittest.skipUnless(sync_playwright and harness.chromium(), "playwright + chromium required")
class CharacterPanelsBrowserTest(unittest.TestCase):
    def test_resize_chat_menu_and_avatar_actions(self):
        parent = harness.PARENT.replace('case "clipboard": return reply(d.id, { ok: true });',
            'case "clipboard": window.__copy = p; return reply(d.id, { ok: true });')
        with sync_playwright() as pw, patch.object(harness, "PARENT", parent):
            browser, page, errors = harness.open_page(pw, size=(1300, 850))
            frame = harness.frame_of(page)
            frame.get_by_text("New character", exact=True).last.click()
            frame.locator(".ch-editor .mu-input").first.fill("Traveler")
            avatar = frame.locator(".ch-ed-avatar-pick")
            with page.expect_file_chooser() as chooser:
                avatar.click()
            chooser.value.set_files({"name": "avatar.png", "mimeType": "image/png", "buffer": base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==")})
            expect(avatar.locator("img")).to_have_attribute("src", re.compile(r"^data:image/png;base64,"))
            avatar.click(button="right")
            frame.get_by_role("menuitem", name="Copy avatar").click()
            page.wait_for_function("window.__copy?.image.startsWith('data:image/png;base64,')")
            avatar.click(button="right")
            frame.get_by_role("menuitem", name="Download avatar").click()
            page.wait_for_function("window.__downloads.some(d => d.name === 'Traveler.png' && d.url.startsWith('data:image/png;base64,'))")
            frame.locator(".ch-modal-foot").get_by_text("Create", exact=True).click()
            expect(frame.locator("#composer-text")).to_be_visible()
            pane = frame.locator("#character-panel")
            chat = frame.locator("#main")
            left = frame.locator("#side")
            expect(pane).to_be_hidden()
            before = chat.bounding_box()["width"]
            frame.locator('.ch-chat-head button[title="Character card"]').click()
            expect(pane).to_be_visible()
            self.assertAlmostEqual(chat.bounding_box()["width"] + pane.bounding_box()["width"], before, delta=1)
            self.assertEqual(pane.locator(".ch-side-switch, .ch-side-chats, input[type=range]").count(), 0)

            def drag(selector, delta):
                rect = frame.locator(selector).bounding_box()
                x, y = rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2
                page.mouse.move(x, y)
                page.mouse.down()
                page.mouse.move(x + delta, y, steps=6)
                page.mouse.up()

            drag("#library-resize", 46)
            drag("#character-resize", -54)
            page.wait_for_function("window.__store.settings?.ui?.libraryWidth === 322 && window.__store.settings.ui.characterPanelWidth === 394")
            self.assertEqual(left.bounding_box()["width"], 322)
            self.assertEqual(pane.bounding_box()["width"], 394)
            pane.locator('button[title="Chats"]').click()
            expect(frame.locator(".ch-chat-menu [role=menuitem].active")).to_have_count(1)
            frame.get_by_role("menuitem", name="Manage chats").click()
            expect(frame.locator(".ch-chatlist")).to_be_visible()
            frame.locator("body").press("Escape")
            pane.locator('button[title="New chat"]').click()
            pane.locator('button[title="Chats"]').click()
            expect(frame.locator(".ch-chat-menu [role=menuitem]")).to_have_count(3)

            with page.expect_event("framenavigated", predicate=lambda item: item == frame):
                page.locator("#f").evaluate("el => { el.src = '/module'; }")
            expect(frame.locator("#composer-text")).to_be_visible()
            expect(pane).to_be_hidden()
            self.assertEqual(left.bounding_box()["width"], 322)
            frame.locator('.ch-chat-head button[title="Character card"]').click()
            self.assertEqual(pane.bounding_box()["width"], 394)
            frame.locator("#character-resize").press("ArrowLeft")
            self.assertEqual(pane.bounding_box()["width"], 410)
            page.set_viewport_size({"width": 540, "height": 710})
            self.assertLess(chat.bounding_box()["width"], 540)
            self.assertGreaterEqual(chat.bounding_box()["width"], 200)
            self.assertLessEqual(pane.bounding_box()["x"] + pane.bounding_box()["width"], 540)
            self.assertEqual(errors, [])
            browser.close()
