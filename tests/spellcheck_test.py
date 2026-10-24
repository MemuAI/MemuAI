# -*- coding: utf-8 -*-
"""Контракт модуля проверки орфографии (Round 4, пункт 3).

Сам движок — Windows Spell Checking API через COM, поэтому на других ОС он обязан
аккуратно сообщать о недоступности, а не падать.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import spellcheck  # noqa: E402


class SpellcheckModuleTest(unittest.TestCase):
    def test_public_api(self):
        for name in ("spell_status", "check_text", "add_word", "ignore_word"):
            self.assertTrue(callable(getattr(spellcheck, name)), name)
        self.assertTrue(issubclass(spellcheck.SpellcheckUnavailable, Exception))
        self.assertGreaterEqual(spellcheck.MAX_TEXT, 1000)
        self.assertGreaterEqual(spellcheck.MAX_SUGGESTIONS, 3)

    def test_status_shape(self):
        status = spellcheck.spell_status()
        self.assertIn("available", status)
        self.assertIsInstance(status["available"], bool)
        self.assertIsInstance(status.get("languages", []), list)
        if not status["available"]:
            self.assertTrue(str(status.get("reason") or "").strip())

    def test_non_windows_is_explicit(self):
        if spellcheck.IS_WINDOWS:
            self.skipTest("На Windows проверка должна работать через COM")
        status = spellcheck.spell_status()
        self.assertFalse(status["available"])
        self.assertIn("Windows", str(status.get("reason") or ""))
        try:
            result = spellcheck.check_text("привеd мир")
        except spellcheck.SpellcheckUnavailable:
            return
        self.assertFalse(result.get("available", False))
        self.assertEqual(result.get("errors", []), [])

    def test_long_text_is_bounded(self):
        try:
            result = spellcheck.check_text("слово " * (spellcheck.MAX_TEXT))
        except spellcheck.SpellcheckUnavailable:
            self.skipTest("Движок недоступен на этой ОС")
        self.assertIsInstance(result.get("errors", []), list)


class SpellcheckWiringTest(unittest.TestCase):
    def source(self, rel):
        with open(os.path.join(ROOT, rel), encoding="utf-8") as handle:
            return handle.read()

    def test_endpoint_registered(self):
        api = self.source("app/playground_api.py")
        self.assertIn('"/api/pg/spellcheck"', api)
        self.assertIn("asyncio.to_thread", api)

    def test_frontend_has_toggle_and_menu(self):
        js = self.source("app/pgstatic/app.js")
        for marker in ("st-spellcheck", "spell-layer", "runSpellcheck", "inputContextMenu", "spellWordAction"):
            self.assertIn(marker, js, marker)


if __name__ == "__main__":
    unittest.main()
