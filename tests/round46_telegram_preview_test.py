"""Telegram confirmation preview uses the actual send formatter, without login."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.telegram_use_api import register
from app.telegram_format import markdown_to_telegram_html, split_telegram_html
from app.telegram_use import parse_html, caption_text


class Preview(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        register(app)
        self.client = TestClient(app, base_url="http://127.0.0.1")

    def preview(self, text, **extra):
        return self.client.post("/api/pg/integrations/telegram-use/preview", json={"text": text, **extra})

    def test_formatting_and_newlines_match_the_send_path_without_starting_telegram(self):
        text = "**Привет**\n\n*Курсив* и `literal \\n`\n> Цитата\n[Ссылка](https://example.test/a)\n```py\nprint('hi')\n```"
        with patch("app.telegram_use.start", side_effect=AssertionError("Must not log in or send")):
            response = self.preview(text)
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["parts"], split_telegram_html(markdown_to_telegram_html(text)))
        self.assertEqual(result["plain"], [parse_html(part)[0] for part in result["parts"]])
        self.assertIn("<b>Привет</b>", result["parts"][0])
        self.assertIn("\n\n", result["parts"][0])
        self.assertIn("literal \\n", result["plain"][0])
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_long_text_shows_all_real_message_parts(self):
        result = self.preview("**Длинный текст**\n" + "абвгд " * 1800).json()
        self.assertGreater(len(result["parts"]), 1)
        self.assertEqual(len(result["parts"]), len(result["plain"]))

    def test_html_and_unsafe_links_are_not_active_markup(self):
        result = self.preview('<img src="https://attacker.test/pixel"><script>alert(1)</script> [bad](javascript:alert(1))').json()
        self.assertNotIn("<img", result["parts"][0])
        self.assertNotIn("<script", result["parts"][0])
        self.assertNotIn('href="javascript:', result["parts"][0])

    def test_caption_preview_is_identical_to_the_actual_shortening(self):
        text = "🧠" * 700
        result = self.preview(text, kind="caption").json()
        self.assertTrue(result["truncated"])
        self.assertEqual(result["plain"], [parse_html(markdown_to_telegram_html(caption_text(text)))[0]])

    def test_invalid_text_and_cross_origin_are_rejected(self):
        for value in (None, {}, "", "x" * 100001):
            self.assertEqual(self.preview(value).status_code, 400)
        response = self.client.post("/api/pg/integrations/telegram-use/preview", json={"text": "Hello"}, headers={"Origin": "https://example.test"})
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
