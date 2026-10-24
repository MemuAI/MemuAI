"""Round 29, phase 2 (r29b-prompts): system prompts — tool use, persistence, protocol formats, dead internal prompts.

Offline checks only. The empirical A/B eval on real models lives in tests/prompt_eval/ (not run here).
Запуск: python3 -m pytest -q tests/round29_prompts_test.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="r29prompts-test-"))

APP_JS = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _mem_prompt() -> str:
    start = APP_JS.index("const MEM_PROMPT = `") + len("const MEM_PROMPT = `")
    end = APP_JS.index("</internal_protocols>`;", start)
    return APP_JS[start:end].replace("\\`", "`") + "</internal_protocols>"


class DefaultPromptTest(unittest.TestCase):
    def test_tool_use_rules_in_both_copies(self) -> None:
        from app import pgdata
        for text in (pgdata.DEFAULT_PROMPT, _mem_prompt()):
            self.assertIn("<tool_use>", text)
            self.assertIn("call it in this same reply", text)
            self.assertIn("answer directly", text)
            # the exact hidden block a weak model has to copy, not a description of it
            self.assertIn('```memuai-tool\n[{"tool":"disk_space","args":{}}]\n```', text)
            self.assertIn("search with web tools first", text)
            self.assertIn("Before you finish any answer, check both", text)

    def test_protocol_formats_kept(self) -> None:
        from app import pgdata
        for text in (pgdata.DEFAULT_PROMPT, _mem_prompt()):
            for marker in ("```schedule", "[MEM:", "<memory_protocol>", "@!web-search", "<local_tools>"):
                self.assertIn(marker, text)

    def test_web_tools_search_first(self) -> None:
        block = APP_JS[APP_JS.index("const WEB_TOOLS_PROMPT = `"):APP_JS.index("</web_tools>`;")]
        self.assertIn("Search FIRST", block)
        self.assertIn("```memuai-tool", block.replace("\\`", "`"))


class InternalPromptsWiredTest(unittest.TestCase):
    """Internal prompts shown in Settings → System prompts must actually reach the model."""

    def test_formerly_dead_prompts_are_read(self) -> None:
        for prompt_id in ("work-execution", "temporary-chat", "attachment-text-bridge"):
            self.assertIn(f'internalPromptText("{prompt_id}")', APP_JS, prompt_id)

    def test_work_execution_fallback_matches_builtin(self) -> None:
        from app import pgdata
        match = re.search(r'const WORK_EXECUTION_FALLBACK = "([^"]+)";', APP_JS)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), pgdata.internal_prompt("work-execution"))
        self.assertIn("Carry the request through to the end", match.group(1))

    def test_work_goal_and_plan_keep_their_blocks(self) -> None:
        from app import pgdata
        self.assertIn("memuai-goal", pgdata.internal_prompt("work-goal"))
        self.assertIn("complete only with evidence", pgdata.internal_prompt("work-goal"))
        plan = pgdata.internal_prompt("work-plan-execution")
        self.assertIn('{"step":1,"status":"complete|blocked","evidence":"..."}', plan)


@unittest.skipUnless(NODE, "node is required")
class BareToolCallsTest(unittest.TestCase):
    def _calls(self, text: str):
        a = APP_JS.index("function bareToolCalls(")
        b = APP_JS.index("/* Переводит «чужие» вызовы поиска", a)
        script = APP_JS[a:b] + f"\nconsole.log(JSON.stringify(bareToolCalls({json.dumps(text)})));"
        out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_accepts_a_whole_reply_call(self) -> None:
        self.assertEqual(self._calls('[{"tool":"disk_space","args":{}}]'), [{"tool": "disk_space", "args": {}}])
        self.assertEqual(self._calls('```json\n{"tool": "open_apps"}\n```'), [{"tool": "open_apps", "args": {}}])
        two = self._calls('[{"tool":"web_search","args":{"query":"x"}},{"tool":"Work_Read","args":{"path":"a"}}]')
        self.assertEqual([c["tool"] for c in two], ["web_search", "work_read"])

    def test_rejects_examples_and_prose(self) -> None:
        for text in ('Вот пример: [{"tool":"disk_space","args":{}}]',
                     '[{"tool":"disk_space","args":{},"note":"x"}]',
                     '[{"name":"disk_space"}]', '{"query":"weather"}', '[1, 2]', '[]',
                     '[{"tool":"disk space","args":{}}]', '```python\n[{"tool":"x1","args":{}}]\n```'):
            self.assertEqual(self._calls(text), [], text)


class GenderNeutralMemoryTest(unittest.TestCase):
    """A tester saw facts like «У него есть кот»: every memory-writing prompt asks for gender-neutral wording."""

    def test_memory_protocol(self) -> None:
        from app import memory_v2
        text = memory_v2.protocol_prompt("chat", doc=memory_v2.load())
        self.assertIn("gender-neutrally unless the user stated their gender", text)
        self.assertIn("«Есть кот»", text)

    def test_legacy_marker_and_import(self) -> None:
        from app import pgdata
        for text in (pgdata.DEFAULT_PROMPT, _mem_prompt()):
            self.assertIn("worded gender-neutrally unless the user stated their gender", text)
        self.assertIn("Do not assume the user's gender", APP_JS)
        self.assertIn("Не угадывай пол", APP_JS)


class CliPromptTest(unittest.TestCase):
    def test_persistence_and_tool_judgement(self) -> None:
        from app.cli import prompts
        self.assertIn("6. Finish the job.", prompts.WORKFLOW)
        self.assertIn("answer general programming questions from your own knowledge without tools", prompts.TOOL_RULES)
        self.assertIn("After two failures of the same approach, change approach", prompts.TOOL_RULES)

    def test_work_agent_prompt(self) -> None:
        from app import work_agents
        self.assertIn("Carry the whole assignment through", work_agents.SYSTEM_PROMPT)
        self.assertIn("```memuai-tool", work_agents.SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
