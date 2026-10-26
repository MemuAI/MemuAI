"""MemuCode answers in the language the user writes in, whatever language its interface uses."""
from __future__ import annotations

import unittest

from app.cli import lang, prompts
from tests.memucode_cli_test import make_runtime, setUpModule, tearDownModule  # noqa: F401  (module fixtures)

class Detection(unittest.TestCase):
    def test_scripts_map_to_languages(self):
        cases = {
            "Привет! Почини баг в app.py": "Russian",
            "Виправ помилку, будь ласка, і покажи результат": "Ukrainian",
            "请修复这个错误": "Chinese",
            "こんにちは、バグを直して": "Japanese",
            "버그를 고쳐줘": "Korean",
            "أصلح الخطأ من فضلك": "Arabic",
            "Σβήσε το λάθος": "Greek",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(lang.detect(text), expected)

    def test_latin_text_gets_no_wrong_guess(self):
        for text in ("fix the failing test", "Hola, arregla el error", "12345 ???", ""):
            with self.subTest(text=text):
                self.assertEqual(lang.detect(text), "")

    def test_code_paths_and_attachments_do_not_count(self):
        self.assertEqual(lang.detect("Исправь `foo_bar()` в src/app/main.py пожалуйста"), "Russian")
        attached = "what does this do?\n<attached_files><file path=\"a.py\">print('привет мир')</file></attached_files>"
        self.assertEqual(lang.detect(attached), "")
        self.assertEqual(lang.detect("```python\nprint('привет')\n```\nexplain the snippet"), "")

    def test_technical_english_inside_a_russian_sentence_is_still_russian(self):
        self.assertEqual(lang.detect("use React hooks для формы"), "Russian")
        self.assertEqual(lang.detect("please explain the word привет in Russian"), "")

class Reminder(unittest.TestCase):
    def test_reminder_names_the_language_or_falls_back_to_the_general_rule(self):
        russian = lang.reminder(["", "Сделай рефакторинг"])
        self.assertIn("Russian", russian)
        self.assertIn("unless they ask for another language", russian)
        self.assertNotIn("Russian", lang.reminder(["fix it"]))
        self.assertIn("same language", lang.reminder(["fix it"]))

    def test_the_latest_message_with_letters_decides(self):
        self.assertIn("Russian", lang.reminder(["12345", "Почини"]))
        self.assertIn("Chinese", lang.reminder(["请看一下", "Привет"]))

    def test_a_short_latin_reply_keeps_the_conversation_language(self):
        self.assertIn("Russian", lang.reminder(["ok", "go on", "Почини тесты, пожалуйста"]))
        self.assertNotIn("Russian", lang.reminder(["please switch to English from now on", "Почини тесты"]))

    def test_every_turn_carries_the_rule_for_the_users_language(self):
        runtime, _ = make_runtime()
        runtime.session.append({"role": "user", "kind": "prompt", "content": "Найди ошибку в тестах"})
        reminder = prompts.reminders(runtime)
        self.assertIn("<system-reminder>", reminder)
        self.assertIn("Russian", reminder)
        runtime.session.append({"role": "user", "kind": "prompt", "content": "now in English please: fix it"})
        self.assertNotIn("Russian", prompts.reminders(runtime))

    def test_the_system_prompt_no_longer_advertises_an_english_interface(self):
        runtime, _ = make_runtime()
        system = runtime.system_prompt(runtime.tools.available(runtime), native=False)
        self.assertNotIn("Interface language", system)
        self.assertIn("language of the user's latest message", system)

    def test_workers_report_in_the_language_of_their_task(self):
        runtime, _ = make_runtime()
        agent = runtime.agents()["general-purpose"]
        system = prompts.build_system_prompt(runtime, [], native=True, agent=agent)
        self.assertIn("language of the task", system)

if __name__ == "__main__":
    unittest.main()
