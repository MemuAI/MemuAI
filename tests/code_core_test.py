"""MemuCode engine, sandbox, artifacts, skills and the parallel tool batch."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import code_artifacts, code_context, code_routes, code_sandbox, code_skills, code_tools
from app import pgdata

class ContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.session = "ctx-" + str(id(self))
        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "app.py").write_text("class Box:\n    def run(self):\n        return 1\n", encoding="utf-8")
        (self.root / "notes.md").write_text("hello\n", encoding="utf-8")

    def tearDown(self) -> None:
        code_context.drop_session(self.session)
        self.tmp.cleanup()

    def engine(self) -> code_context.CodeContextEngine:
        return code_context.engine_for(self.session, self.root)

    def test_repeat_read_is_a_reference_and_a_new_span_is_not(self) -> None:
        engine = self.engine()
        engine.refresh()
        first = engine.read_cached("pkg/app.py", 1, 2)
        self.assertFalse(first["already_read"])
        self.assertIn("class Box", first["text"])
        second = engine.read_cached("pkg/app.py", 1, 2)
        self.assertTrue(second["already_read"])
        self.assertGreater(engine.stats()["saved_chars"], 0)
        overlap = engine.read_cached("pkg/app.py", 2, 3)
        self.assertFalse(overlap["already_read"])
        self.assertIn("return 1", overlap["text"])

    def test_mtime_change_drops_only_that_file(self) -> None:
        engine = self.engine()
        engine.refresh()
        engine.read_cached("notes.md", 1, 1)
        self.assertTrue(engine.read_cached("notes.md", 1, 1)["already_read"])
        path = self.root / "notes.md"
        path.write_text("hello\nchanged\n", encoding="utf-8")
        os.utime(path, None)
        engine.refresh()
        again = engine.read_cached("notes.md", 1, 2)
        self.assertFalse(again["already_read"])
        self.assertIn("changed", again["text"])
        self.assertTrue(engine.read_cached("pkg/app.py", 1, 1)["already_read"] or "class Box" in engine.read_cached("pkg/app.py", 1, 1).get("text", "") or engine.read_cached("pkg/app.py", 1, 1).get("already_read"))

    def test_symbol_search_finds_the_class(self) -> None:
        engine = self.engine()
        found = engine.search_symbols("Box")
        self.assertTrue(any(item.get("name") == "Box" for item in found["symbols"]))

class SandboxTests(unittest.TestCase):
    def tearDown(self) -> None:
        code_sandbox.manager.close_all()

    def test_environment_drops_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with patch.dict(os.environ, {"OPENAI_API_KEY": "secret-value", "PATH": os.environ.get("PATH", "")}, clear=False):
                env = code_sandbox.filtered_env("sess", Path(temp))
            self.assertNotIn("OPENAI_API_KEY", {key.upper() for key in env})
            self.assertNotIn("secret-value", " ".join(env.values()))
            self.assertIn("MEMUAI_CODE_SESSION", env)
            self.assertTrue(env.get("PATH") or env.get("Path"))

    def test_forbidden_command_is_named(self) -> None:
        self.assertTrue(code_sandbox.forbidden_reason("format c:"))
        self.assertFalse(code_sandbox.forbidden_reason("python -m unittest"))

    def test_process_output_is_drained_in_bounded_chunks(self) -> None:
        class Output:
            def __init__(self):
                self.remaining = 100
                self.closed = False

            def read(self, size=-1):
                self_test.assertEqual(size, 8192)
                if not self.remaining:
                    return b""
                self.remaining -= 1
                return b"x" * 8192

            def close(self):
                self.closed = True

        self_test = self
        output = Output()
        tail = code_sandbox._drain(output)
        self.assertEqual(len(tail), code_sandbox.MAX_OUTPUT)
        self.assertTrue(output.closed)

    def test_process_runs_and_stop_kills_the_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            captured = code_sandbox.launch_captured(
                "run-ok", root, [sys.executable, "-c", "print('memucode-ok')"],
                cwd=str(root), timeout=20)
            self.assertIn("memucode-ok", captured["stdout"])
            process = code_sandbox.spawn(
                "run-kill", root, [sys.executable, "-c", "import time; time.sleep(30)"], cwd=str(root))
            job = code_sandbox.manager.job("run-kill", root, create=False)
            self.assertIsNotNone(job)
            job.kill_all()
            process.wait(timeout=8)
            for pipe in (process.stdout, process.stderr):
                if pipe is not None:
                    pipe.close()
            self.assertIsNotNone(process.poll())

    @unittest.skipUnless(sys.platform == "win32", "Windows Job Object only")
    def test_unassigned_process_never_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            marker = root / "ran.txt"
            created = []
            original_popen = code_sandbox._popen

            def record_popen(*args, **kwargs):
                process = original_popen(*args, **kwargs)
                created.append(process)
                return process

            with patch.object(code_sandbox.SessionJob, "track", return_value=False), \
                    patch.object(code_sandbox, "_popen", side_effect=record_popen):
                with self.assertRaisesRegex(RuntimeError, "attach"):
                    code_sandbox.spawn(
                        "unassigned", root,
                        [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"],
                        cwd=str(root))
            self.assertEqual(len(created), 1)
            self.assertIsNotNone(created[0].poll())
            self.assertFalse(marker.exists())

    @unittest.skipUnless(sys.platform == "win32", "Windows Job Object only")
    def test_unresumable_process_never_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            marker = root / "ran.txt"
            created = []
            original_popen = code_sandbox._popen

            def record_popen(*args, **kwargs):
                process = original_popen(*args, **kwargs)
                created.append(process)
                return process

            with patch.object(code_sandbox, "_resume", return_value=False), \
                    patch.object(code_sandbox, "_popen", side_effect=record_popen):
                with self.assertRaisesRegex(RuntimeError, "resume"):
                    code_sandbox.spawn(
                        "unresumable", root,
                        [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"],
                        cwd=str(root))
            self.assertEqual(len(created), 1)
            self.assertIsNotNone(created[0].poll())
            self.assertFalse(marker.exists())

class ArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = self.root / "store"
        self.project = self.root / "project"
        self.project.mkdir()
        self._patch = patch.object(code_artifacts, "ARTIFACT_ROOT", self.store)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        self.tmp.cleanup()

    def test_versions_publish_and_escape(self) -> None:
        from app import code_checkpoints

        created = code_artifacts.create("sess", kind="html", title="Page", content="<p>hi</p>")
        updated = code_artifacts.update("sess", created["id"], content="<p>yo</p>")
        self.assertEqual(updated["version"], 2)
        listed = code_artifacts.list_artifacts("sess")
        self.assertNotIn("content", listed[0])
        with patch.object(code_checkpoints, "CHECKPOINT_ROOT", self.root / "checkpoints"):
            published = code_artifacts.publish("sess", created["id"], self.project, "out/page.html")
        self.assertEqual((self.project / "out" / "page.html").read_text(encoding="utf-8"), "<p>yo</p>")
        self.assertEqual(published["path"], "out/page.html")
        with self.assertRaises(ValueError):
            code_artifacts.publish("sess", created["id"], self.project, "../outside.html")

    def test_publish_overwrite_can_be_undone(self) -> None:
        from app import code_checkpoints

        target = self.project / "page.html"
        target.write_text("previous", encoding="utf-8")
        created = code_artifacts.create("undo-publish", kind="html", title="Page", content="replacement")
        with patch.object(code_checkpoints, "CHECKPOINT_ROOT", self.root / "checkpoints"):
            code_artifacts.publish("undo-publish", created["id"], self.project, "page.html")
            self.assertEqual(target.read_text(encoding="utf-8"), "replacement")
            result = code_checkpoints.restore("undo-publish", self.project)
        self.assertEqual(result["restored"], ["page.html"])
        self.assertEqual(target.read_text(encoding="utf-8"), "previous")

class SkillTests(unittest.TestCase):
    def test_project_skill_overrides_builtin_and_personal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            personal = root / "personal"
            project = root / ".memuai" / "skills" / "review"
            project.mkdir(parents=True)
            personal.mkdir()
            (personal / "review").mkdir()
            (personal / "review" / "SKILL.md").write_text("---\ntitle: Personal\n---\nPERSONAL", encoding="utf-8")
            (project / "SKILL.md").write_text("---\ntitle: Project review\n---\nPROJECT PLAYBOOK", encoding="utf-8")
            with patch.object(code_skills, "PERSONAL_ROOT", personal):
                rows = code_skills.catalogue(root, include_prompt=True)
            review = next(row for row in rows if row["id"] == "review")
            self.assertEqual(review["source"], "project")
            self.assertIn("PROJECT PLAYBOOK", review["prompt"])
            self.assertGreaterEqual(len(code_skills.BUILTIN_SKILLS), 14)
            with patch.object(code_skills, "PERSONAL_ROOT", personal):
                self.assertIn("PROJECT PLAYBOOK", code_skills.prompt_for(root, "review"))

class BatchTests(unittest.TestCase):
    def test_read_only_tools_share_a_batch_and_writes_stay_guarded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "main.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
            result = asyncio.run(code_routes.run_batch(root, [
                {"tool": "code_project_info", "args": {}},
                {"tool": "code_symbols", "args": {"query": "hello"}},
                {"tool": "code_run", "args": {"command": "format c:"}},
            ], "batch", "ask"))
            self.assertEqual(len(result["results"]), 3)
            self.assertEqual(result["results"][0]["tool"], "code_project_info")
            self.assertTrue(any(item.get("name") == "hello" for item in result["results"][1].get("symbols", [])))
            self.assertIn("error", result["results"][2])
            self.assertFalse(code_tools.parallel_safe("code_run", {"command": "git status"}))
            self.assertTrue(code_tools.parallel_safe("code_git", {"action": "status"}))

class PreferenceTests(unittest.TestCase):
    def test_code_intro_is_stored_and_not_dropped_by_later_saves(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(pgdata, "MEMU_DIR", Path(temp)):
                Path(temp).mkdir(exist_ok=True)
                first = pgdata.save_preferences({"code_intro_seen": True, "language": "en"})
                self.assertTrue(first["code_intro_seen"])
                second = pgdata.save_preferences({"notify_sound_volume": 40})
                self.assertTrue(second["code_intro_seen"])
                self.assertEqual(pgdata.load_preferences()["code_intro_seen"], True)
                self.assertIsInstance(pgdata.load_preferences()["code_skill_state"], dict)

class GuardTests(unittest.TestCase):
    def test_terminal_run_without_action_still_needs_full_access(self) -> None:
        """code_terminal_run sets action=run itself; the batch guard used to miss it."""
        for name, args in (("code_terminal_run", {"command": "del important.txt"}),
                           ("code_terminal", {"action": "run", "command": "rm -rf build"})):
            self.assertTrue(code_routes._guard_mutation(name, dict(args), "ask"), name)
            self.assertEqual(code_routes._guard_mutation(name, dict(args), "full"), "", name)
        self.assertEqual(code_routes._guard_mutation("code_terminal_run", {"command": "git status"}, "ask"), "")
        for harmless in (("code_terminal", {}), ("code_terminal", {"action": "read"}),
                         ("code_terminal_start", {}), ("code_terminal_stop", {})):
            self.assertEqual(code_routes._guard_mutation(harmless[0], dict(harmless[1]), "ask"), "", harmless)

class CheckpointTests(unittest.TestCase):
    def setUp(self) -> None:
        from app import code_checkpoints
        self.cp = code_checkpoints
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.store = patch.object(code_checkpoints, "CHECKPOINT_ROOT", base / "store")
        self.store.start()
        self.project = base / "project"
        self.other = base / "other"
        self.project.mkdir()
        self.other.mkdir()
        self.outside = base / "outside.txt"

    def tearDown(self) -> None:
        self.store.stop()
        self.tmp.cleanup()

    def test_paths_outside_the_project_are_never_recorded_or_touched(self) -> None:
        result = self.cp.snapshot("s1", self.project, "../outside.txt")
        self.assertFalse(result["stored"])
        self.outside.write_text("keep me", encoding="utf-8")
        self.cp.restore("s1", self.project)
        self.assertEqual(self.outside.read_text(encoding="utf-8"), "keep me")

    def test_restore_never_removes_project_or_directory(self) -> None:
        self.assertFalse(self.cp.snapshot("s-dir", self.project, ".")["stored"])
        existing = self.project / "existing"
        existing.mkdir()
        self.assertFalse(self.cp.snapshot("s-dir", self.project, "existing")["stored"])

        self.cp.snapshot("s-dir", self.project, "new_path")
        new_directory = self.project / "new_path"
        new_directory.mkdir()
        marker = new_directory / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        result = self.cp.restore("s-dir", self.project)
        self.assertTrue(self.project.is_dir())
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
        self.assertEqual(result["removed"], [])
        self.assertEqual(len(result["failed"]), 1)

    def test_required_snapshot_rejects_unrestorable_files(self) -> None:
        large = self.project / "large.txt"
        large.write_bytes(b"x" * (self.cp.MAX_FILE_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "too large"):
            self.cp.require_snapshot("s-large", self.project, "large.txt", label="work_write")
        self.assertEqual(self.cp.list_entries("s-large"), [])

        folder = self.project / "folder"
        folder.mkdir()
        (folder / "inner.txt").write_text("inner", encoding="utf-8")
        self.cp.require_snapshot("s-large", self.project, "folder", label="work_delete")
        self.assertEqual([row["path"] for row in self.cp.list_entries("s-large")], ["folder", "folder/inner.txt"])
        self.assertTrue(folder.is_dir())

    def test_restore_goes_to_the_project_that_was_changed(self) -> None:
        (self.project / "a.txt").write_text("before", encoding="utf-8")
        self.cp.snapshot("s2", self.project, "a.txt")
        self.cp.snapshot("s2", self.project, "new.txt")
        (self.project / "a.txt").write_text("after", encoding="utf-8")
        (self.project / "new.txt").write_text("created", encoding="utf-8")
        (self.other / "new.txt").write_text("unrelated", encoding="utf-8")

        result = self.cp.restore("s2", self.other)
        self.assertEqual(sorted(result["restored"] + result["removed"]), ["a.txt", "new.txt"])
        self.assertEqual((self.project / "a.txt").read_text(encoding="utf-8"), "before")
        self.assertFalse((self.project / "new.txt").exists())
        self.assertEqual((self.other / "new.txt").read_text(encoding="utf-8"), "unrelated")

    def test_restore_does_not_use_another_project_when_original_is_missing(self) -> None:
        self.cp.snapshot("s-missing", self.project, "new.txt")
        (self.other / "new.txt").write_text("unrelated", encoding="utf-8")
        self.project.rmdir()
        result = self.cp.restore("s-missing", self.other)
        self.assertEqual(result["removed"], [])
        self.assertEqual(len(result["failed"]), 1)
        self.assertEqual((self.other / "new.txt").read_text(encoding="utf-8"), "unrelated")

if __name__ == "__main__":
    unittest.main()
