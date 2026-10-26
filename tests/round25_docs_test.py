"""Round 25 (docs): the custom-model guide and the pages updated for Round 24 features stay true to the code.

The guide «Свои модели персонажа» (``live2d-models``) quotes limits, file names, parameter ids and the
words that pick a Live2D expression for an emotion; when the code changes, these tests point at the page
that has to change with it.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from app import companion_avatar, docs
from app import assistant_commands  # noqa: F401  (installs the Round 25 tool phrases)
from app import assistant_intents as intents
from app import assistant_r25, dfy, dfy_vault, panels, public_api

ROOT = Path(__file__).resolve().parents[1]
STAGE = (ROOT / "app" / "pgstatic" / "companion" / "stage.js").read_text(encoding="utf-8")
QUICK = (ROOT / "app" / "pgstatic" / "quick" / "quick.js").read_text(encoding="utf-8")
VOICE_SETTINGS = (ROOT / "app" / "pgstatic" / "voice-settings.js").read_text(encoding="utf-8")
DFY_CLIENT = (ROOT / "app" / "pgstatic" / "dfy-client.js").read_text(encoding="utf-8")

def text(lang: str, page_id: str) -> str:
    return (ROOT / "app" / "docs" / lang / f"{page_id}.md").read_text(encoding="utf-8")

class Live2DGuideTests(unittest.TestCase):
    def test_page_is_registered_next_to_companion_and_linked(self) -> None:
        catalog = docs.catalog("ru")
        pages = [page["id"] if isinstance(page, dict) else page
                 for group in catalog["groups"] for page in group["pages"]]
        self.assertIn("live2d-models", pages)
        self.assertEqual(pages.index("live2d-models"), pages.index("companion") + 1)
        for lang in docs.LANGUAGES:
            self.assertIn("(doc:live2d-models", text(lang, "companion"))
            self.assertIn("(doc:live2d-models)", text(lang, "glossary"))

    def test_limits_match_the_importer(self) -> None:
        mb = 1024 * 1024
        self.assertEqual(companion_avatar.MAX_ZIP_BYTES, 200 * mb)
        self.assertEqual(companion_avatar.MAX_VRM_BYTES, 200 * mb)
        self.assertEqual(companion_avatar.MAX_UNPACKED_BYTES, 400 * mb)
        for lang, unit in (("ru", "МБ"), ("en", "MB")):
            page = text(lang, "live2d-models")
            for value in ("200 " + unit, "400 " + unit, str(companion_avatar.MAX_ZIP_FILES), str(companion_avatar.MAX_MODELS)):
                self.assertIn(value, page, f"{lang}: {value}")

    def test_cubism2_is_refused_as_the_page_says(self) -> None:
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr("old/old.model.json", "{}")
            archive.writestr("old/old.moc", b"moc")
        with self.assertRaises(companion_avatar.ModelError) as caught:
            companion_avatar.validate_live2d_zip(buf.getvalue())
        self.assertEqual(caught.exception.code, "cubism2")

    def test_parameters_and_emotion_words_match_the_stage(self) -> None:
        for param in ("ParamMouthOpenY", "ParamMouthForm", "ParamEyeLSmile"):
            self.assertIn(param, STAGE)
        words = re.search(r"const words = \{(.+?)\}\[want\]", STAGE, re.S)
        self.assertIsNotNone(words, "stage.js: the emotion → expression word table moved")
        quoted = set(re.findall(r'"([a-z0-9]+)"', words.group(1)))
        for lang in docs.LANGUAGES:
            page = text(lang, "live2d-models")
            for word in quoted:
                self.assertIn(f"`{word}`", page, f"{lang}: expression word {word}")
        self.assertIn("/tap|flick/i", STAGE)
        self.assertIn("/^idle/i", STAGE)

class Round24FeaturePagesTests(unittest.TestCase):
    def test_dfy_page_and_command(self) -> None:
        pages = [p for g in docs.catalog("en")["groups"] for p in g["pages"]]
        self.assertIn("dfy", [p["id"] if isinstance(p, dict) else p for p in pages])
        for lang in docs.LANGUAGES:
            self.assertIn("`/dfy", text(lang, "commands"))

    def test_quick_ask_shortcuts_match_the_window(self) -> None:

        self.assertIn('if (mod && e.shiftKey && e.key === "Enter") { e.preventDefault(); openInMemuAI(); return; }', QUICK)
        self.assertIn("window.QuickDFY.run()", QUICK)
        for lang in docs.LANGUAGES:
            page = text(lang, "quick-ask")
            self.assertRegex(page, r"\| \+\+Ctrl\+Enter\+\+ \| \[[^]]+\]\(doc:dfy\)")
            self.assertIn("| ++Ctrl+Shift+Enter++ |", page)

TOOL_ACTIONS = {"color.convert", "text.encode", "json.format", "hash.text", "text.case", "text.stats",
                "date.calc", "time.zone", "random.pick", "lorem.generate"}

class Round25SyncTests(unittest.TestCase):
    """«a5-docs-2»: the pages describe the final Round 25 code."""

    def test_internal_anchors_resolve(self) -> None:
        for lang in docs.LANGUAGES:
            for page_id in ("quick-ask", "quick-commands", "proxy-public", "dfy", "live2d-models", "companion"):
                page = text(lang, page_id)
                heads = {docs.anchor(h) for h in re.findall(r"^#{1,6}\s+(.+)$", page, re.M)}
                for target in re.findall(r"\]\(#([^)]+)\)", page):
                    self.assertIn(target, heads, f"{lang}/{page_id}: #{target}")

    def test_stt_presets_match_the_settings(self) -> None:
        presets = re.search(r"const STT_PRESETS = \[(.+?)\];", VOICE_SETTINGS, re.S).group(1)
        for label, model in (("Groq", "whisper-large-v3-turbo"), ("OpenAI", "gpt-4o-mini-transcribe")):
            self.assertIn(f'label: "{label}"', presets)
            self.assertIn(f'model: "{model}"', presets)
            for lang in docs.LANGUAGES:
                page = text(lang, "voice")
                self.assertIn(f"**{label}**", page, lang)
                self.assertIn(f"`{model}`", page, lang)
        for lang in docs.LANGUAGES:
            self.assertRegex(text(lang, "voice"), r"Gemini, ElevenLabs (и|and) Azure")

    def test_client_ip_rules_match_the_proxy(self) -> None:
        from types import SimpleNamespace
        from unittest import mock
        request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"),
                                  headers={"cf-connecting-ip": "9.9.9.9", "x-forwarded-for": "1.1.1.1, 2.2.2.2"})
        with mock.patch.object(public_api, "_own_tunnel_running", return_value=False):
            self.assertEqual(public_api.client_ip(request), "2.2.2.2")
        with mock.patch.object(public_api, "_own_tunnel_running", return_value=True):
            self.assertEqual(public_api.client_ip(request), "9.9.9.9")
        self.assertTrue(public_api.invalid_ips("1.2.3.4/40"))
        for lang in docs.LANGUAGES:
            page = text(lang, "proxy-public")
            self.assertIn("`CF-Connecting-IP`", page)
            self.assertIn("`X-Forwarded-For`", page)
            self.assertIn("`1.2.3.4/40`", page)
            self.assertIn("8320", page)

    def test_tool_phrases_quoted_on_the_pages_work(self) -> None:
        section = {"ru": "## Инструменты", "en": "## Tools"}
        for lang in docs.LANGUAGES:
            page = text(lang, "quick-commands")
            body = page[page.index(section[lang]):]
            body = body[:body.index("\n## ", 3)]
            rows = [line for line in body.splitlines() if line.startswith("| ") and not line.startswith("| ---")][1:]
            self.assertEqual(len(rows), 10, lang)
            quote = r"«([^»]+)»" if lang == "ru" else r'"([^"]+)"'
            phrases = [p for row in rows for p in re.findall(quote, row.rsplit(" | ", 1)[1])]
            self.assertGreater(len(phrases), 25, lang)
            for phrase in phrases:
                found = intents.match(phrase)
                self.assertIsNotNone(found, f"{lang}: {phrase}")
                self.assertIn(found["action"], TOOL_ACTIONS, f"{lang}: {phrase}")
            self.assertIn(f"{len(assistant_r25.ZONES) // 10 * 10}", body)

    def test_pin_and_step_back_are_described(self) -> None:
        self.assertIn('$("qa-pin").hidden = !NATIVE;', QUICK)
        self.assertIn('windowAction("inactive")', QUICK)
        self.assertIn("Дождитесь ответа", QUICK)
        for lang, words in (("ru", ("уходит назад", "после перезапуска вернётся", "всегда видна")),
                            ("en", ("steps back", "comes back after a restart", "always visible"))):
            page = text(lang, "quick-ask")
            for word in words:
                self.assertIn(word, page, lang)

    def test_dfy_forms_and_limits(self) -> None:
        self.assertEqual(dfy.CODE_TIMEOUT, 10 * 60)
        self.assertEqual((dfy.STUCK_HINT_AFTER, dfy.STUCK_FAIL_AFTER), (3, 6))
        phone = dfy_vault.normalize_phone("89161234567")
        self.assertEqual(phone, "+79161234567")
        self.assertEqual(dfy_vault._mask_phone(phone), "+7 •••• ••67")
        self.assertIn("Код не пришёл — отправить снова", DFY_CLIENT)
        self.assertIn("middle_name", DFY_CLIENT)
        for lang, words in (("ru", ("+7 •••• ••67", "10 минут", "отчество", "Код не пришёл — отправить снова", "Указать вручную")),
                            ("en", ("+7 •••• ••67", "10 minutes", "middle name", "No code — send again", "Set manually"))):
            page = text(lang, "dfy")
            for word in words:
                self.assertIn(word, page, lang)

    def test_manual_panels_and_companion_gaze(self) -> None:
        self.assertIn("rerun", panels.start_refresh.__code__.co_varnames)
        self.assertIn("/api/companion/cursor/stream", STAGE)
        for lang, panel_word, gaze_word in (("ru", "Только вручную", "по всему экрану"), ("en", "Manual only", "across the whole screen")):
            self.assertIn(panel_word, text(lang, "panels"))
            self.assertIn(gaze_word, text(lang, "live2d-models"))
            self.assertIn(gaze_word, text(lang, "companion"))

if __name__ == "__main__":
    unittest.main()
