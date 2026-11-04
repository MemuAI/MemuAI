"""System prompt budget: lazy module details, the brief canvas block, short skill cues, model-facing module notes.

An ordinary chat message must not carry the full command list and notes of every enabled module, the full canvas
manual or the long skill triggers: they join the prompt only when the request is about them.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PG = ROOT / "app" / "pgstatic"
NODE = shutil.which("node")
MODULES = ("music", "meet", "characters", "api-models", "benchmarks", "learn", "translator", "memudb")

class ModuleCommandsBudgetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from app import modules_host as mh
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        for module in MODULES:
            mh.configure_module(cls.root, module, enabled=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_compact_block_is_a_fraction_of_the_full_one(self) -> None:
        from app import module_integration as mi
        full = mi.skill_prompt(self.root, lang="en")
        compact = mi.skill_prompt(self.root, lang="en", details=False)
        self.assertTrue(compact.startswith("<module_commands>") and compact.endswith("</module_commands>"))
        self.assertIn("module_command", compact)
        self.assertIn('"help"', compact)
        self.assertIn("(music): play,", compact)
        self.assertLess(len(compact), len(full) / 4, (len(compact), len(full)))
        self.assertLess(len(compact), 2200, len(compact))
        self.assertNotIn("local music library", compact)
        self.assertIn("local music library", full)

    def test_details_carry_keys_and_the_full_section(self) -> None:
        from app import module_integration as mi
        rows = {row["module"]: row for row in mi.skill_details(self.root, lang="en")}
        self.assertEqual(set(rows), set(MODULES))
        self.assertIn("музык", rows["music"]["keys"])
        self.assertIn("встреч", rows["meet"]["keys"])
        self.assertIn("music.play {", rows["music"]["text"])
        self.assertIn("local music library", rows["music"]["text"])

    def test_help_command_returns_the_module_section_without_a_card(self) -> None:
        from app import module_integration as mi
        out = asyncio.run(mi.run_command("music", "help", {}, root=self.root, lang="en"))
        self.assertTrue(out["ok"])
        self.assertIn("music.play {", out["text"])
        self.assertNotIn("card", out)
        self.assertNotIn("client", out)

    def test_module_notes_are_model_facing(self) -> None:
        for path in (ROOT / "app" / "module_builtins").glob("*/integration/skill.md"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("Model selector:", text, path.parent.parent.name)
            self.assertNotIn("app/pgstatic/", text, path.parent.parent.name)
            self.assertLess(len(text), 1800, path.parent.parent.name)

@unittest.skipUnless(NODE, "node is required")
class ModuleSystemBlockTest(unittest.TestCase):
    def _run(self, text: str) -> str:
        src = (PG / "module-integration.js").read_text(encoding="utf-8")
        a = src.index("  function detailsFor(text)")
        b = src.index("  async function client(c)", a)
        script = ("const S = { loaded: true, prompt: '<module_commands>\\nHEAD\\n- MemuMusic (music): play\\n</module_commands>',"
                  " details: [{ module: 'music', keys: ['музык', 'music'], text: 'MemuMusic (module music):\\n- music.play {query?: string}' }] };"
                  "function load() {}\n" + src[a:b] + f"\nconsole.log(JSON.stringify(systemBlock('chat', {json.dumps(text)})));")
        out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_details_join_only_a_matching_request(self) -> None:
        plain = self._run("Как приготовить борщ?")
        self.assertNotIn("music.play {", plain)
        self.assertTrue(plain.endswith("</module_commands>"))
        music = self._run("Включи музыку для работы")
        self.assertIn("music.play {", music)
        self.assertTrue(music.endswith("</module_commands>"))
        self.assertEqual(music.count("</module_commands>"), 1)

    def test_app_passes_the_request_text(self) -> None:
        app = (PG / "app.js").read_text(encoding="utf-8")
        self.assertIn("MemuModInt.systemBlock(sourceMode, [scheduleText, ...recentAsk].join(\"\\n\"))", app)

class CanvasBriefTest(unittest.TestCase):
    def test_brief_block_for_an_ordinary_message(self) -> None:
        from app import canvas as C
        self.assertEqual(C.context_block("", text="Привет! Как дела?"), C.PROMPT_BRIEF)
        self.assertEqual(C.context_block("", text="Напиши статью про котов"), C.PROMPT)
        self.assertEqual(C.context_block("", text="make a landing page"), C.PROMPT)
        self.assertEqual(C.context_block(""), C.PROMPT, "no request text keeps the full manual")
        self.assertTrue(C.PROMPT_BRIEF.startswith("<canvas>") and C.PROMPT_BRIEF.endswith("</canvas>"))
        self.assertIn("canvas_create", C.PROMPT_BRIEF)
        self.assertLess(len(C.PROMPT_BRIEF), len(C.PROMPT) / 3)

    def test_client_sends_the_request(self) -> None:
        js = (PG / "canvas.js").read_text(encoding="utf-8")
        self.assertIn("function requestText(messages)", js)
        self.assertIn("if (text) body.text = text;", js)

class SkillCuesTest(unittest.TestCase):
    @unittest.skipUnless(NODE, "node is required")
    def test_every_builtin_skill_has_a_short_cue(self) -> None:
        script = ("global.window = {}; require(" + json.dumps(str(PG / "skills.js")) + ");"
                  "console.log(JSON.stringify(window.MEMUAI_SKILL_LIBRARY.skills.map(s => [s.id, s.cue || '', s.when || ''])));")
        out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        rows = json.loads(out.stdout)
        self.assertGreaterEqual(len(rows), 20)
        for sid, cue, when in rows:
            self.assertTrue(cue, sid)
            self.assertLess(len(cue), 125, sid)
            self.assertLessEqual(len(cue), len(when), sid)
            self.assertFalse(re.search(r"[А-Яа-яЁё]", cue), sid)

    def test_catalogue_uses_the_cue_for_builtin_skills_only(self) -> None:
        app = (PG / "app.js").read_text(encoding="utf-8")
        self.assertIn("when = oneLine((!s.custom && !s.customized && s.cue) || s.when)", app)

class BasePromptBudgetTest(unittest.TestCase):
    def test_base_prompt_and_web_tools_shrank(self) -> None:
        from app import pgdata
        self.assertLess(len(pgdata.DEFAULT_PROMPT), 7600)
        app = (PG / "app.js").read_text(encoding="utf-8")
        web = app[app.index("const WEB_TOOLS_PROMPT = `"):app.index("</web_tools>`;")]
        self.assertLess(len(web), 1800)
        dfy = (PG / "dfy-client.js").read_text(encoding="utf-8")
        block = dfy[dfy.index('"\\n\\n<dfy_tool>'):dfy.index('machinesBlock() + "</dfy_tool>"')]
        self.assertLess(len(block), 1100)
        self.assertIn("memuai-dfy", block)

if __name__ == "__main__":
    unittest.main()
