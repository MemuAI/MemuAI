"""Round 29, phase 2, wave D (r29d-prompts): Quick Ask escalation, MemuNote answer language, DFY CAPTCHA / done,
docs-AI no invented settings, media autoprompt edits, compact CLI WORKFLOW, Live relay keeps the provider's last frames.

Offline checks only; the live A/B numbers are in .phase2/r29d-prompts.md (tests/prompt_eval/others_eval.py).
Запуск: python3 -m pytest -q tests/round29d_prompts_test.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="r29d-prompts-test-"))


class QuickEscalationTest(unittest.TestCase):
    def test_escalation_is_stated_up_front_only_when_available(self) -> None:
        from app import quick_ask_core as q
        with_adv = q.system_prompt(lang="ru", style="brief", web=True, screenshot=True, vision=True, can_escalate=True)
        without = q.system_prompt(lang="ru", style="brief", web=True, screenshot=True, vision=True)
        self.assertIn("Big requests are not yours", with_adv)
        self.assertLess(with_adv.index("Big requests are not yours"), with_adv.index("<tools>"))
        self.assertIn("code with tests", with_adv)
        self.assertNotIn("escalate_to_advanced", without)
        self.assertNotIn("Big requests", without)
        self.assertLess(len(with_adv), 4200)


class MemuNoteLanguageTest(unittest.TestCase):
    def build(self, action: str, ui: str, instruction: str = "") -> list:
        from app.memunote import prompts
        body = {"action": action, "input": "Текст заметки.", "instruction": instruction,
                "context": {"kind": "text", "name": "a.txt", "text": ""}}
        return prompts.build_messages(body, {"ai_context_chars": 60000}, ui, False)["messages"]

    def test_answers_name_the_interface_language(self) -> None:
        for action in ("summarize", "explain", "titles", "todos", "outline"):
            ru = self.build(action, "ru")
            self.assertIn("in Russian (the user's interface language)", ru[0]["content"], action)
            self.assertIn("Write the answer in Russian.", ru[-1]["content"], action)
            en = self.build(action, "en")
            self.assertIn("Write the answer in English.", en[-1]["content"], action)

    def test_user_instruction_language_wins(self) -> None:
        messages = self.build("summarize", "ru", instruction="in German please")
        self.assertNotIn("Write the answer in Russian.", messages[-1]["content"])

    def test_transforms_keep_the_text_language(self) -> None:
        messages = self.build("grammar", "en")
        self.assertIn("Keep the language of the original text", messages[0]["content"])
        self.assertNotIn("Write the answer in", messages[-1]["content"])

    def test_regex_pattern_without_backticks(self) -> None:
        from app.memunote import prompts
        self.assertIn("no slashes, flags or backticks", prompts.ACTIONS["regex"]["task"])


class DfyRulesTest(unittest.TestCase):
    def test_captcha_and_visible_answer(self) -> None:
        from app import dfy
        self.assertIn("CAPTCHA", dfy.SYSTEM_PROMPT)
        self.assertIn("never try to solve or click through it", dfy.SYSTEM_PROMPT)
        self.assertIn('finish with "done" at once', dfy.SYSTEM_PROMPT)


class DocsAiAskTest(unittest.TestCase):
    def test_no_invented_paths(self) -> None:
        from app import docs_ai
        en = docs_ai.ask_messages("en", "T", "Body", "q")[0]["content"]
        ru = docs_ai.ask_messages("ru", "T", "Тело", "q")[0]["content"]
        self.assertIn("never name a menu path, setting, plugin or integration the article does not mention", en)
        self.assertIn("не называй пути в меню, настройки, плагины и интеграции, которых нет в статье", ru)
        self.assertNotIn("suggest where in MemuAI to look", en)


class MediaAutopromptTest(unittest.TestCase):
    def test_edit_of_previous_generation(self) -> None:
        from app import media_routing as mr
        text = mr.AUTOPROMPT_SYSTEM.format(kind="image", video_extra="", search_rule="")
        self.assertIn("keep that prompt and change only what the user asks", text)
        self.assertNotIn("{", text)


class CliWorkflowTest(unittest.TestCase):
    def test_compact_and_complete(self) -> None:
        from app.cli import prompts
        text = prompts.WORKFLOW
        self.assertLess(len(text), 1900, "sent with every MemuCode turn")
        for phrase in ("1. Understand first.", "2. Plan multi-step work.", "3. Change minimally.", "4. Verify.",
                       "5. Ask only when it matters", "6. Finish the job.", "Never claim something passed",
                       "shall I continue?", "TodoWrite", "old_string exactly"):
            self.assertIn(phrase, text)


class LiveRelayReaderTest(unittest.TestCase):
    def test_upstream_is_read_by_a_dedicated_task(self) -> None:
        # websockets<14 drops frames still buffered when the provider closes right after its last event;
        # the relay must take frames off the socket independently of forwarding them (round9 live-relay test).
        source = (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8")
        start = source.index("async def upstream_to_client()")
        body = source[start:start + 1600]
        self.assertIn("asyncio.Queue()", body)
        self.assertIn("async for raw in upstream:", body)
        self.assertIn("reader.cancel()", body)


if __name__ == "__main__":
    unittest.main()
