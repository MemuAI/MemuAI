# -*- coding: utf-8 -*-
"""Round 73, item 11: slash commands in the «Characters» module.

(a) /hide and /unhide accept a range (/hide 3-50), a single index, a reversed
range (50-3 == 3-50) and clamp out-of-range ends; invalid arguments produce a
visible error toast instead of silence, and a successful application toasts
the number of messages actually hidden/shown. Indices stay 0-based, as in
S.chat.messages. (b) /help is a structured, grouped list (all 13 commands and
their 5 aliases, plus the keyboard footnote), in RU and EN. (c) Typing "/"
at the start of a line offers an autocomplete popup: prefix filtering,
keyboard/mouse insertion of the command plus a trailing space, Esc to close,
no popup in the middle of text. (g) One narrow-width fix: at <=760px the
sidebar overlay hides itself when a chat opens and the header toggle brings
it back.

Static checks below pin the i18n keys, the command table and the wiring in
the module sources. Full browser behaviour (ranges on a real chat,
persistence across reload, autocomplete keyboard flow, screenshots) is
proven by the Playground probe at ~/workspace/verify73/slash/probe_slash.js
(45 checks, all passing); the browser test here re-checks /help in EN and
the no-chat guard through the Round 29 harness when Chromium is available.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "app" / "module_builtins" / "characters"

MAIN = (MODULE / "main.js").read_text(encoding="utf-8")
CSS = (MODULE / "style.css").read_text(encoding="utf-8")
I18N = json.loads((MODULE / "i18n.json").read_text(encoding="utf-8"))

# Round 74: /sys and /narrator no longer create narrator messages; /char is
# now primary (with /sendas as alias), plus /del, /edit, /getvar, /stop.
PRIMARY_COMMANDS = [
    "char", "send", "del", "edit", "continue", "regen", "swipe", "imp",
    "stop", "hide", "unhide", "newchat", "note", "setvar", "getvar", "help",
]
ALIASES = ["sendas", "cont", "regenerate", "impersonate"]

NEW_KEYS = [
    "slash_hide_invalid", "slash_hide_no_messages",
    "slash_hidden_count", "slash_shown_count",
    "slash_group_messages", "slash_group_generation",
    "slash_group_visibility", "slash_group_chat",
    "slash_args_text", "slash_args_range", "slash_args_setvar",
    "slash_cmd_send", "slash_cmd_sendas", "slash_cmd_del", "slash_cmd_edit",
    "slash_cmd_getvar", "slash_cmd_stop", "slash_args_edit", "slash_args_varname",
    "slash_cmd_continue", "slash_cmd_regen", "slash_cmd_swipe",
    "slash_cmd_imp", "slash_cmd_newchat", "slash_cmd_note",
    "slash_cmd_setvar", "slash_cmd_hide", "slash_cmd_unhide",
    "slash_cmd_help", "slash_help_note",
]

# Description fragments taken verbatim from the old slash_help text, so the
# structured /help cannot silently drop or rewrite a command description.
OLD_DESCRIPTIONS = {
    "slash_cmd_send": ("сообщение от вас без ответа", "Your message without a reply"),
    "slash_cmd_sendas": ("сообщение от персонажа", "A message from the character"),
    "slash_cmd_continue": ("продолжить последнее сообщение", "Continue the last message"),
    "slash_cmd_regen": ("перегенерировать", "Regenerate"),
    "slash_cmd_swipe": ("новый вариант ответа", "A new answer variant"),
    "slash_cmd_imp": ("ответить за вас", "Write your reply for you"),
    "slash_cmd_newchat": ("новый чат", "New chat"),
    "slash_cmd_note": ("заметка автора", "Author's Note"),
    "slash_cmd_setvar": ("переменная чата", "Chat variable"),
    "slash_cmd_hide": ("скрыть сообщение из промпта", "Hide a message from the prompt"),
}


class I18nTest(unittest.TestCase):
    def test_new_keys_present_in_both_languages(self):
        self.assertEqual(set(I18N["ru"]), set(I18N["en"]))
        for key in NEW_KEYS:
            self.assertIn(key, I18N["ru"], key)
            self.assertIn(key, I18N["en"], key)

    def test_count_toasts_use_plural_forms(self):
        for lang, forms in (("ru", {"one", "few", "many", "other"}),
                            ("en", {"one", "other"})):
            for key in ("slash_hidden_count", "slash_shown_count"):
                value = I18N[lang][key]
                self.assertIsInstance(value, dict, (lang, key))
                self.assertEqual(set(value), forms, (lang, key))
                for text in value.values():
                    self.assertIn("{count}", text)

    def test_descriptions_preserve_old_help_text(self):
        for key, (ru_frag, en_frag) in OLD_DESCRIPTIONS.items():
            self.assertIn(ru_frag.lower(), I18N["ru"][key].lower(), key)
            self.assertEqual(I18N["en"][key], en_frag, key)

    def test_english_texts_have_no_cyrillic(self):
        for key in NEW_KEYS:
            self.assertNotRegex(json.dumps(I18N["en"][key], ensure_ascii=False),
                                "[А-Яа-яЁё]", key)

    def test_footnote_preserved(self):
        self.assertIn("Alt+", I18N["ru"]["slash_help_note"])
        self.assertIn("листать варианты ответа", I18N["ru"]["slash_help_note"])
        self.assertIn("browse answer variants", I18N["en"]["slash_help_note"])


class SourceTest(unittest.TestCase):
    def test_help_table_covers_every_command_and_alias(self):
        block = MAIN[MAIN.index("var SLASH_HELP_GROUPS"):MAIN.index("function runSlash")]
        names = re.findall(r'name: "(/[^"]+)"', block)
        aliases = re.findall(r'aliases: \[([^\]]*)\]', block)
        alias_names = re.findall(r'"(/[^"]+)"', " ".join(aliases))
        self.assertEqual(names, ["/" + c for c in PRIMARY_COMMANDS])
        self.assertEqual(sorted(alias_names), sorted("/" + a for a in ALIASES))

    def test_runslash_handles_every_command(self):
        for cmd in PRIMARY_COMMANDS + ALIASES:
            self.assertIn('"%s"' % cmd, MAIN, cmd)

    def test_hide_range_logic(self):
        self.assertIn(r'/^(\d+)(?:\s*-\s*(\d+))?$/', MAIN)
        self.assertIn("clamp(lo, 0, total - 1)", MAIN)
        self.assertIn("clamp(hi, 0, total - 1)", MAIN)
        self.assertIn('toast(t("slash_hide_invalid"), "error")', MAIN)
        self.assertIn('toast(t(hiding ? "slash_hidden_count" : "slash_shown_count"',
                      MAIN)
        self.assertNotIn("var idx = parseInt(arg, 10); var msg = S.chat.messages[idx];",
                         MAIN)

    def test_help_is_structured_not_plain_pre(self):
        self.assertNotIn('h("pre", { class: "ch-pre", text: t("slash_help") })', MAIN)
        self.assertIn("content: slashHelpContent()", MAIN)
        self.assertIn("ch-slash-help", MAIN)
        self.assertIn("ch-slash-group", MAIN)
        self.assertIn("ch-slash-row", MAIN)
        self.assertIn('t("slash_help_note")', MAIN)

    def test_autocomplete_wiring(self):
        self.assertIn("slashContext()", MAIN)
        self.assertIn(r'/^([ \t]*)\/(\w*)$/', MAIN)
        self.assertIn("slashInsert(", MAIN)
        self.assertIn("slashClose()", MAIN)
        self.assertIn('id: "ch-slash-pop"', MAIN)
        self.assertIn('role: "listbox"', MAIN)
        self.assertIn("aria-expanded", MAIN)
        for key in ("ArrowDown", "ArrowUp", "Escape"):
            self.assertIn('e.key === "%s"' % key, MAIN)
        self.assertIn('(e.key === "Enter" || e.key === "Tab")', MAIN)
        self.assertIn("S.chat.meta.draft = area.value; saveChat(); updateDraftTokens(); slashUpdate();",
                      MAIN)

    def test_help_and_autocomplete_styles_use_module_variables(self):
        for cls in ("ch-slash-help", "ch-slash-group", "ch-slash-row",
                    "ch-slash-name", "ch-slash-alias", "ch-slash-args",
                    "ch-slash-desc", "ch-slash-note", "ch-slash-pop",
                    "ch-slash-pop-item", "ch-slash-pop-name", "ch-slash-pop-desc"):
            self.assertIn("." + cls, CSS, cls)
        self.assertIn(".ch-composer{position:relative;", CSS)

    def test_runslash_exposed_for_tests(self):
        self.assertIn("runSlash: runSlash", MAIN)

    def test_narrow_sidebar_fix(self):
        self.assertIn("function syncNarrowSidebar(chat)", MAIN)
        self.assertIn("syncNarrowSidebar(true)", MAIN)
        self.assertIn("syncNarrowSidebar(false)", MAIN)
        self.assertIn("ch-side-hidden", MAIN)
        self.assertIn(".ch-app.ch-side-hidden .ch-side", CSS)


try:
    from playwright.sync_api import sync_playwright
    from tests.round29_characters_harness import chromium, frame_of, open_page
    _HARNESS_OK = bool(chromium())
except Exception:  # pragma: no cover - harness/playwright unavailable here
    sync_playwright = None
    _HARNESS_OK = False


@unittest.skipUnless(sync_playwright and _HARNESS_OK, "playwright + chromium required")
class CharactersSlashBrowserTest(unittest.TestCase):
    """EN /help and the no-chat guard, through the Round 29 harness."""

    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser, cls.page, cls.errors = open_page(cls.pw, lang="en")
        cls.page.wait_for_timeout(1500)
        cls.frame = frame_of(cls.page)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def test_help_modal_in_english(self):
        self.frame.evaluate("() => window.MemuCharacters.runSlash('/help')")
        self.frame.wait_for_selector(".ch-slash-row")
        rows = self.frame.evaluate(
            "() => [...document.querySelectorAll('.ch-slash-row')].map(e => e.textContent)")
        self.assertEqual(len(rows), 16)
        text = " ".join(rows)
        self.assertNotIn("Narrator message", text)
        self.assertIn("A message from the character", text)
        self.assertIn("Include a message in the prompt", text)
        self.assertNotRegex(text, "[А-Яа-яЁё]")

    def test_hide_without_chat_shows_error_instead_of_throwing(self):
        toast = self.frame.evaluate(
            "() => { window.MemuCharacters.runSlash('/hide abc'); "
            "return [...document.querySelectorAll('.ch-toast')].map(e => e.textContent); }")
        self.assertTrue(any("Specify a message number" in t for t in toast), toast)
