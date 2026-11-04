"""MemuCode permission levels: dry-run previews for approval cards, the approval gate
in the window, project rules for the prompt and the step limit."""
from __future__ import annotations

import os
import tempfile

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

import re  # noqa: E402
import shutil  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "pgstatic"


def snapshot(root: Path) -> dict:
    out = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        out[rel] = path.read_bytes() if path.is_file() else b"<dir>"
    return out


class PreviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="memucode-preview-"))
        (self.root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        (self.root / "notes").mkdir()
        (self.root / "notes" / "todo.md").write_text("# TODO\n- one\n", encoding="utf-8")
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_every_write_tool_previews_without_touching_the_disk(self) -> None:
        from app import work_tools
        calls = {
            "work_write": {"path": "new/hello.py", "text": "print('hi')\n"},
            "work_patch": {"path": "calc.py", "old_text": "return a - b", "new_text": "return a + b"},
            "work_insert": {"path": "calc.py", "line": 1, "text": "# header\n"},
            "work_replace_lines": {"path": "calc.py", "start_line": 2, "end_line": 2, "text": "    return a + b\n"},
            "work_append": {"path": "notes/todo.md", "text": "- two\n"},
            "work_delete": {"path": "notes/todo.md"},
            "work_move": {"path": "notes/todo.md", "to": "notes/done.md"},
            "work_mkdir": {"path": "build/out"},
        }
        self.assertEqual(set(calls), set(work_tools.WRITE_TOOLS))
        before = snapshot(self.root)
        for name, args in calls.items():
            with self.subTest(tool=name):
                result = work_tools.preview(name, self.root, args)
                self.assertTrue(result.get("preview"))
                self.assertEqual(result.get("tool"), name)
                self.assertEqual(snapshot(self.root), before, f"{name} changed the project during a preview")
        patch = work_tools.preview("work_patch", self.root, calls["work_patch"])
        self.assertIn("+    return a + b", patch.get("diff", ""))
        self.assertIn("-    return a - b", patch.get("diff", ""))
        self.assertTrue(work_tools.preview("work_write", self.root, calls["work_write"]).get("created"))

    def test_preview_fails_like_the_real_call(self) -> None:
        from app import work_tools
        with self.assertRaises(work_tools.WorkError):
            work_tools.preview("work_patch", self.root, {"path": "calc.py", "old_text": "missing", "new_text": "x"})
        with self.assertRaises(work_tools.WorkError):
            work_tools.preview("work_read", self.root, {"path": "calc.py"})
        with self.assertRaises(Exception):
            work_tools.preview("work_write", self.root, {"path": "../outside.txt", "text": "x"})

    def test_the_real_call_still_writes_after_a_preview(self) -> None:
        from app import work_tools
        work_tools.preview("work_patch", self.root, {"path": "calc.py", "old_text": "return a - b", "new_text": "return a + b"})
        work_tools.run("work_patch", self.root, {"path": "calc.py", "old_text": "return a - b", "new_text": "return a + b"})
        self.assertIn("return a + b", (self.root / "calc.py").read_text(encoding="utf-8"))


class ProjectRulesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="memucode-rules-"))
        (self.root / "main.py").write_text("print(1)\n", encoding="utf-8")
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_agents_md_wins_and_claude_md_is_the_fallback(self) -> None:
        from app.work_context import project_rules
        self.assertEqual(project_rules(self.root), {})
        (self.root / "CLAUDE.md").write_text("Use tabs.\n", encoding="utf-8")
        self.assertEqual(project_rules(self.root)["file"], "CLAUDE.md")
        (self.root / "AGENTS.md").write_text("Run pytest -q.\n", encoding="utf-8")
        rules = project_rules(self.root)
        self.assertEqual((rules["file"], rules["text"], rules["truncated"]), ("AGENTS.md", "Run pytest -q.", False))

    def test_rules_are_clipped_and_empty_files_are_skipped(self) -> None:
        from app.work_context import project_rules
        (self.root / "AGENTS.md").write_text("   \n", encoding="utf-8")
        (self.root / "CLAUDE.md").write_text("x" * 50_000, encoding="utf-8")
        rules = project_rules(self.root, limit=1000)
        self.assertEqual(rules["file"], "CLAUDE.md")
        self.assertTrue(rules["truncated"])
        self.assertLessEqual(len(rules["text"]), 1000)

    def test_index_lists_files_without_their_text(self) -> None:
        from app.work_context import project_index
        index = project_index(self.root)
        self.assertIn("main.py", index["index"])
        self.assertNotIn("print(1)", repr(index))

    def test_context_endpoint_offers_the_index_and_rules(self) -> None:
        api = (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8")
        start = api.index('@app.post("/api/work/context")')
        block = api[start:start + 1500]
        self.assertIn('body.get("index_only") is True', block)
        self.assertIn('body.get("rules") is True', block)
        self.assertIn("project_rules", block)


class ApprovalWiringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.code = (STATIC / "memucode.js").read_text(encoding="utf-8")
        cls.api = (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8")

    def test_three_levels_and_the_gate(self) -> None:
        self.assertIn('const CODE_LEVELS = ["ask", "edits", "full"]', self.app)
        self.assertIn("async function codeApprovalGate(", self.app)
        # Every write tool and every command goes through the gate in MemuCode.
        for tool in ("work_write", "work_patch", "work_insert", "work_replace_lines", "work_append",
                     "work_delete", "work_move", "work_mkdir"):
            self.assertRegex(self.app, r"CODE_EDIT_TOOLS = new Set\(\[[^\]]*\"" + tool + r"\"")
        for command in ('name === "code_run"', 'name === "code_task" && action === "submit"',
                        'name === "code_diagnostics" && args.execute'):
            self.assertIn(command, self.app)
        # Read-only commands do not ask; a rejection carries the user's note to the model.
        self.assertIn('kind === "command" && preview?.read_only', self.app)
        self.assertIn("Follow that note.", self.app)

    def test_previews_never_execute(self) -> None:
        self.assertIn('if tool in work_tools.WRITE_TOOLS and body.get("preview") is True:', self.api)
        start = self.api.index('if tool.startswith("code_"):')
        block = self.api[start:start + 4000]
        preview_at = block.index('body.get("preview") is True')
        self.assertLess(preview_at, block.index("code_tools.run_command"))
        self.assertIn('"read_only": code_tools.is_read_only(command)', block)

    def test_cards_in_the_chat(self) -> None:
        for snippet in ("function requestApproval(", 'data-cap-act="approve"', 'data-cap-act="session"',
                        'data-cap-act="reject"', "function approvalsHtml(m, index, which", "Не дождалось решения"):
            self.assertIn(snippet, self.code)
        self.assertIn('approvalsHtml(m, i, "done")', self.app)
        self.assertIn('approvalsHtml(m, i, "pending")', self.app)
        # Typing while a card waits answers it instead of stopping the turn.
        self.assertIn("function composerAnswersWait(", self.app)

    def test_prompt_follows_the_settings(self) -> None:
        self.assertIn("<verify_policy>", self.app)
        self.assertIn('pref("code_auto_verify", true)', self.app)
        self.assertIn('pref("code_project_rules", true)', self.app)
        self.assertIn("<project_rules_note>", self.app)
        self.assertRegex(self.app, r'pref\("code_tool_rounds", 20\)')
        self.assertRegex(self.app, r"Math\.max\(6, Math\.min\(50,")

    def test_access_command_sets_memucode_levels(self) -> None:
        start = self.app.index('case "/access": {')
        block = self.app[start:start + 900]
        for level in ('setCodeAccessLevel("full")', 'setCodeAccessLevel("edits")', 'setCodeAccessLevel("ask")',
                      "openCodeAccessChooser()"):
            self.assertIn(level, block)

    def test_skills_have_russian_labels(self) -> None:
        from app import code_skill_texts, code_skills
        self.assertEqual(set(code_skill_texts.RU), {row["id"] for row in code_skill_texts.SKILLS})
        builtin = [row for row in code_skills.catalogue("") if row["source"] == "builtin"]
        self.assertTrue(builtin)
        cyrillic = re.compile("[А-Яа-яЁё]")
        for row in builtin:
            self.assertTrue(row["title_ru"].strip())
            self.assertRegex(row["description_ru"], cyrillic)
        self.assertIn("skillDescription(skill)", self.code)


if __name__ == "__main__":
    unittest.main()
