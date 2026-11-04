"""Companion bubble actions through the real desktop UI and local thread API."""

import os
import re
import socket
import threading
import time
import unittest
from unittest.mock import patch

from tests.modules_window_states_browser_test import _chromium


@unittest.skipUnless(_chromium(), "Chromium is not installed")
class WorkspaceMessageActionsTest(unittest.TestCase):
    def test_react_delete_undo_and_reload(self):
        try:
            from playwright.sync_api import expect, sync_playwright
            import uvicorn
        except ImportError as exc:
            raise unittest.SkipTest(str(exc)) from exc
        from app import pgdata, space_context, spaces
        from app.playground_api import create_app

        pgdata.save_preferences({"profile_onboarded": True, "profile_name": "Test", "language": "en"})
        workspace = spaces.create_space({"type": "agent", "activate": True})
        chat_id = workspace["global_chat_id"]
        first = {"role": "user", "content": "Hello", "tid": "u1", "seq": 0}
        answer = {"role": "assistant", "content": "Keep the first\n|||\nDiscardPhrase\n|||\n```text\n@@memu-bubble@@\n|||\n```",
                  "tid": "a1", "seq": 1}
        fillers = [{"role": "user", "content": f"Filler message {i}", "tid": f"f{i}", "seq": i} for i in range(2, 22)]
        pgdata.save_chats([{"id": chat_id, "mode": "chat", "space_role": "global", "ingestedSeq": 21,
                            "messages": [first, answer, *fillers], "updated": 1}])
        index = space_context.thread_for()
        index.ingest(chat_id, [{"id": m["tid"], "seq": m["seq"], "role": m["role"], "text": m["content"]}
                               for m in (first, answer, *fillers)])
        index.add_episode(chat_id, 0, 1, 0, 0, "DiscardPhrase summary")

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        with patch.dict(os.environ, {"MEMUAI_PROXY_URL": "http://127.0.0.1:1", "MEMUAI_TOKEN": ""}):
            app = create_app()
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        worker = threading.Thread(target=server.run, daemon=True)
        worker.start()
        self.addCleanup(worker.join, 10)
        self.addCleanup(setattr, server, "should_exit", True)
        deadline = time.monotonic() + 20
        while not server.started and time.monotonic() < deadline:
            time.sleep(.05)
        self.assertTrue(server.started)

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 960, "height": 730})
            page.add_init_script("if (top === window) localStorage.setItem('memu_lang', 'en');")
            page.route(re.compile(r"^(?!" + re.escape(url + "/") + r")https?://"), lambda route: route.abort())
            page.set_default_timeout(6500)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(url, wait_until="domcontentloaded")
            bubbles = page.locator("#messages .sp-b")
            expect(page.locator("body")).to_have_class(re.compile(r"\bboot-ready\b"), timeout=15000)
            expect(bubbles).to_have_count(3)
            self.assertEqual(page.locator("#chat").evaluate("el => getComputedStyle(el).scrollbarWidth"), "thin")
            self.assertTrue(page.locator("#chat").evaluate("el => el.scrollHeight > el.clientHeight"))
            expect(bubbles.nth(2)).to_contain_text("@@memu-bubble@@")
            expect(bubbles.nth(2)).to_contain_text("|||")
            menu = page.locator("#context-menu")
            picker = page.locator(".rx-pop .rx-quick")

            def open_menu(bubble):
                body = bubble.locator(".sp-bubble")
                body.scroll_into_view_if_needed()
                body.evaluate("el => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
                body.click(button="right")

            open_menu(bubbles.nth(1))
            self.assertEqual(errors, [])
            expect(menu).to_be_visible()
            expect(menu).to_contain_text("React")
            menu.get_by_role("menuitem", name="React", exact=True).click()
            picker.locator('[data-rx-pick="🔥"]').click()
            expect(bubbles.nth(1).locator('[data-rx-chip="🔥"]')).to_be_visible()
            bubbles.nth(2).locator("[data-rx-add]").click()
            picker.locator('[data-rx-pick="👍"]').click()
            expect(bubbles.nth(2).locator('[data-rx-chip="👍"]')).to_be_visible()

            def delete_middle():
                open_menu(bubbles.nth(1))
                menu.get_by_role("menuitem", name="Delete message", exact=True).click()
                expect(bubbles).to_have_count(2)
                expect(bubbles.nth(1).locator('[data-rx-chip="👍"]')).to_be_visible()
                expect(page.locator('[data-rx-chip="🔥"]')).to_have_count(0)

            delete_middle()
            page.get_by_role("button", name="Undo", exact=True).click()
            expect(bubbles).to_have_count(3)
            expect(bubbles.nth(1).locator('[data-rx-chip="🔥"]')).to_be_visible()
            with page.expect_response(lambda response: response.url.endswith("/api/space/thread/ingest"), timeout=10000):
                delete_middle()
            self.assertEqual(index.search(chat_id, "DiscardPhrase"), [])
            self.assertEqual(index.covered_through(chat_id), -1)

            page.reload(wait_until="domcontentloaded")
            expect(bubbles).to_have_count(2)
            expect(bubbles.nth(0)).to_contain_text("Keep the first")
            expect(bubbles.nth(1)).to_contain_text("|||")
            expect(bubbles.nth(1).locator('[data-rx-chip="👍"]')).to_be_visible()
            browser.close()
