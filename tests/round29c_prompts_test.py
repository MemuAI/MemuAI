"""Round 29, phase 2, wave C (r29c-prompts): token economy, schedule-rule block on demand, skills selection.

Offline checks only; the live A/B numbers are in .phase2/r29c-prompts.md (tests/prompt_eval/*).
Запуск: python3 -m pytest -q tests/round29c_prompts_test.py
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
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="r29c-prompts-test-"))

APP_JS = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")

def _mem_prompt() -> str:
    start = APP_JS.index("const MEM_PROMPT = `") + len("const MEM_PROMPT = `")
    end = APP_JS.index("</internal_protocols>`;", start)
    return APP_JS[start:end].replace("\\`", "`").replace('\\"', '"') + "</internal_protocols>"

class BasePromptTest(unittest.TestCase):
    def test_app_js_fallback_is_an_exact_mirror(self) -> None:
        from app import pgdata
        self.assertEqual(_mem_prompt(), pgdata.DEFAULT_PROMPT)

    def test_base_prompt_stays_compact(self) -> None:
        from app import pgdata

        self.assertLess(len(pgdata.DEFAULT_PROMPT), 7800)

    def test_rule_is_named_but_its_format_comes_on_demand(self) -> None:
        from app import pgdata
        self.assertIn('a "rule" object', pgdata.DEFAULT_PROMPT)
        self.assertIn("<schedule_rules>", pgdata.DEFAULT_PROMPT)
        self.assertNotIn('"only_if"', pgdata.DEFAULT_PROMPT)

    def test_memory_protocol_compact(self) -> None:
        from app import memory_v2
        text = memory_v2.protocol_prompt("chat", doc=memory_v2.load())
        self.assertLess(len(text), 2100)
        self.assertIn("however long the answer is", text)

@unittest.skipUnless(NODE, "node is required")
class ScheduleRulesBlockTest(unittest.TestCase):
    def _block(self, text: str) -> str:
        a = APP_JS.index("const SCHEDULE_INTENT_RE")
        b = APP_JS.index("function workRuntimePrompt(", a)
        script = ("globalThis.MemuPlan = { protocol: 'PROTO' };\n" + APP_JS[a:b] +
                  f"\nconsole.log(JSON.stringify(scheduleRulesPromptBlock({json.dumps(text)})));")
        out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_scheduling_requests_get_the_rule_format(self) -> None:
        for text in ("Напоминай мне каждый день в 16:00 пить воду", "каждые 30 минут с 6 до 10 проверяй почту",
                     "Когда я запущу Dota 2, напомни размяться", "по будням в 9 присылай сводку новостей",
                     "Remind me every Monday to send the report", "schedule a daily digest at 8am",
                     "запланируй проверку сайта на завтра"):
            self.assertEqual(self._block(text), "<schedule_rules>PROTO</schedule_rules>", text)

    def test_ordinary_messages_do_not(self) -> None:
        for text in ("Объясни, что такое рекурсия", "Какая погода в Праге?", "Напиши функцию сортировки",
                     "What is the capital of Australia?", "Переведи: каждый охотник желает знать"):
            self.assertEqual(self._block(text), "", text)

    def test_generate_adds_it_after_the_integrations(self) -> None:
        body = APP_JS[APP_JS.index("async function generate("):]
        self.assertIn("scheduleRulesPromptBlock(scheduleText)", body[:20000])

class SkillsSelectionTest(unittest.TestCase):
    def test_compact_catalogue_and_explicit_protocol(self) -> None:
        self.assertIn("if (!s.custom && !s.customized && when) return `- ${s.id}: ${when}`;", APP_JS)
        protocol = APP_JS[APP_JS.index("<skills_protocol>"):APP_JS.index("</skills_protocol>")]
        self.assertIn("Answer directly, without loading", protocol)
        self.assertIn("memuai-skill", protocol)

    @unittest.skipUnless(NODE, "node is required")
    def test_every_builtin_trigger_is_short_and_specific(self) -> None:
        script = ("global.window = {}; require(" + json.dumps(str(ROOT / "app/pgstatic/skills.js")) + ");"
                  "console.log(JSON.stringify(window.MEMUAI_SKILL_LIBRARY.skills.map(s => [s.id, s.when, s.when_ru])));")
        out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        rows = json.loads(out.stdout)
        self.assertGreaterEqual(len(rows), 20)
        for sid, when, when_ru in rows:
            self.assertTrue(when and when_ru, sid)
            self.assertLess(len(when), 200, sid)
            self.assertFalse(re.search(r"[А-Яа-яЁё]", when), sid)
        not_for = [sid for sid, when, _ in rows if "Not for" in when]
        self.assertGreaterEqual(len(not_for), 8, "triggers say what a skill is NOT for")

    def test_cli_skill_rule(self) -> None:
        from app.cli import prompts
        import inspect
        src = inspect.getsource(prompts.skills_block)
        self.assertIn("more than a quick question or a one-line change", src)

class OperatorPromptTest(unittest.TestCase):
    def test_computer_prompt_has_no_raw_newline_inside_a_sentence(self) -> None:
        from app import automation_runtime
        self.assertNotIn('("\n"', automation_runtime.COMPUTER_SYSTEM_PROMPT)
        self.assertIn('a "\\n" in the text presses Enter', automation_runtime.COMPUTER_SYSTEM_PROMPT)

if __name__ == "__main__":
    unittest.main()
