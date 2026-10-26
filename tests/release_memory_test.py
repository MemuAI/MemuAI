"""Memory imports cannot silently read outside a selected project."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from app.cli import paths, project, prompts
from tests import memucode_cli_test as fixtures

setUpModule = fixtures.setUpModule
tearDownModule = fixtures.tearDownModule

class MemoryBoundaryRegressionTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)
        self.root = self.folder / "project"
        self.root.mkdir()
        self.user = self.folder / "user-memory.md"
        for name, target in (("user_memory_file", self.user),
                             ("user_settings_file", self.folder / "settings.json")):
            patcher = patch.object(paths, name, return_value=target)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_project_imports_include_local_rules_and_reject_sibling_secrets(self):
        (self.root / "AGENTS.md").write_text("@./rules.md\n@../secrets.json", encoding="utf-8")
        (self.root / "rules.md").write_text("LOCAL_RULE", encoding="utf-8")
        (self.folder / "secrets.json").write_text("OUTSIDE_SECRET", encoding="utf-8")
        rows = project.memory_files(self.root, ["AGENTS.md"])
        self.assertEqual(len(rows), 1)
        self.assertIn("LOCAL_RULE", rows[0].text)
        self.assertNotIn("OUTSIDE_SECRET", rows[0].text)
        self.assertEqual(rows[0].imports, [str(self.root / "rules.md")])

    def test_user_owned_imports_keep_explicit_outside_references(self):
        self.user.write_text("@./personal-rules.md", encoding="utf-8")
        (self.folder / "personal-rules.md").write_text("PERSONAL_RULE", encoding="utf-8")
        rows = project.memory_files(self.root, ["AGENTS.md"], trusted=False)
        self.assertEqual([row.scope for row in rows], ["user"])
        self.assertIn("PERSONAL_RULE", rows[0].text)

    def test_instruction_names_cannot_escape_ancestor_search(self):
        (self.folder / "secret.json").write_text("PRIVATE_SENTINEL", encoding="utf-8")
        (self.root / "AGENTS.md").write_text("LOCAL_RULE", encoding="utf-8")
        rows = project.memory_files(self.root, ["AGENTS.md", "secret.json", "../secret.json", str(self.folder / "secret.json")])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].text, "LOCAL_RULE")

    def test_instruction_filename_change_refreshes_cached_system_prompt(self):
        runtime, _ = fixtures.make_runtime({"AGENTS.md": "FIRST_RULE", "rules.md": "SECOND_RULE"},
                                           settings={"permission_sync": False})
        runtime.settings.trust()
        self.assertIn("FIRST_RULE", runtime.system_prompt([], native=False))
        runtime.settings.set("memory_files", ["rules.md"], "user")
        after = runtime.system_prompt([], native=False)
        self.assertIn("SECOND_RULE", after)
        self.assertNotIn("FIRST_RULE", after)

    def test_memory_cache_refreshes_when_project_becomes_trusted(self):
        self.user.write_text("PERSONAL_RULE", encoding="utf-8")
        runtime, _ = fixtures.make_runtime({"AGENTS.md": "PROJECT_RULE\n@../secret.json"},
                                           settings={"permission_sync": False})
        (runtime.root.parent / "secret.json").write_text("PRIVATE_SENTINEL", encoding="utf-8")
        self.assertFalse(runtime.trusted())
        before = prompts.memory_block(runtime)
        first_prompt = runtime.system_prompt([], native=False)
        self.assertIn("PERSONAL_RULE", before)
        self.assertNotIn("PROJECT_RULE", before)
        self.assertNotIn("PRIVATE_SENTINEL", before)
        self.assertNotIn("PROJECT_RULE", first_prompt)
        runtime.settings.trust()
        after = prompts.memory_block(runtime)
        self.assertIn("PROJECT_RULE", after)
        self.assertNotIn("PRIVATE_SENTINEL", after)
        self.assertIn("PROJECT_RULE", runtime.system_prompt([], native=False))

    def test_junction_memory_and_imports_do_not_read_outside_project(self):
        outside = self.folder / "outside"
        outside.mkdir()
        (outside / "rules.md").write_text("PRIVATE_SENTINEL", encoding="utf-8")
        link = self.root / "linked"
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                           check=True, capture_output=True)
            self.addCleanup(link.rmdir)
        else:
            link.symlink_to(outside, target_is_directory=True)
            self.addCleanup(link.unlink)
        (self.root / "AGENTS.md").write_text("LOCAL_RULE\n@./linked/rules.md", encoding="utf-8")
        rows = project.memory_files(self.root, ["AGENTS.md", "linked/rules.md"])
        self.assertEqual(len(rows), 1)
        self.assertNotIn("PRIVATE_SENTINEL", rows[0].text)
        self.assertEqual(rows[0].imports, [])

    def test_large_import_graph_is_bounded_and_keeps_initial_instructions(self):
        (self.root / "AGENTS.md").write_text("FIRST_RULE\n" + "\n".join(f"@./rules{n}.md" for n in range(8)), encoding="utf-8")
        for n in range(8):
            (self.root / f"rules{n}.md").write_text(str(n) * project.MAX_MEMORY_FILE * 3, encoding="utf-8")
        rows = project.memory_files(self.root, ["AGENTS.md"])
        self.assertTrue(rows)
        self.assertIn("FIRST_RULE", rows[0].text)
        self.assertLessEqual(sum(len(row.text) for row in rows), project.MAX_MEMORY_TOTAL)
        self.assertLessEqual(len(rows[0].imports), 3)

if __name__ == "__main__":
    unittest.main()
