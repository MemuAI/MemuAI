"""Undo and read-only foreign database path regressions."""
from __future__ import annotations

import shutil
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import code_checkpoints as cp, work_tools
from app.cli.foreign import agy, cursor, opencode

class ForeignDatabasePathRegressionTest(unittest.TestCase):
    def test_special_characters_preserve_readonly_database_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            profile = base / "profile#name with пробелы%"
            profile.mkdir()
            path = profile / "sessions.db"
            con = sqlite3.connect(path)
            try:
                con.execute("CREATE TABLE sentinel (value TEXT)")
                con.execute("INSERT INTO sentinel VALUES ('preserved')")
                con.commit()
            finally:
                con.close()
            for module in (agy, cursor, opencode):
                with self.subTest(module=module.__name__):
                    con = module._connect(path)
                    try:
                        self.assertEqual(con.execute("SELECT value FROM sentinel").fetchone(), ("preserved",))
                        with self.assertRaises(sqlite3.OperationalError):
                            con.execute("INSERT INTO sentinel VALUES ('bad')")
                    finally:
                        con.close()
            self.assertEqual(list(base.iterdir()), [profile])

class DirectoryUndoRegressionTest(unittest.TestCase):
    def test_move_undo_restores_nested_files_and_empty_folders(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(cp, "CHECKPOINT_ROOT", Path(temp) / "checkpoints"):
            root = Path(temp) / "project"
            source = root / "source"
            (source / "empty" / "nested").mkdir(parents=True)
            (source / "pkg").mkdir()
            (source / "pkg" / "a.txt").write_text("original", encoding="utf-8")
            cp.require_move_snapshots("move", root, "source", "created/destination", turn="one")
            work_tools.tool_move(root, {"path": "source", "to": "created/destination"})
            result = cp.restore("move", root, turn="one")
            self.assertEqual(result["failed"], [])
            self.assertEqual(result["remaining"], 0)
            self.assertEqual((source / "pkg" / "a.txt").read_text(encoding="utf-8"), "original")
            self.assertTrue((source / "empty" / "nested").is_dir())
            self.assertFalse((root / "created").exists())

    def test_undo_keeps_unrelated_files_added_to_destination(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(cp, "CHECKPOINT_ROOT", Path(temp) / "checkpoints"):
            root = Path(temp) / "project"
            source = root / "source"
            source.mkdir(parents=True)
            (source / "a.txt").write_text("original", encoding="utf-8")
            cp.require_move_snapshots("move", root, "source", "dest")
            work_tools.tool_move(root, {"path": "source", "to": "dest"})
            (root / "dest" / "user-file.txt").write_text("keep", encoding="utf-8")
            result = cp.restore("move", root)
            self.assertEqual((root / "dest" / "user-file.txt").read_text(encoding="utf-8"), "keep")
            self.assertFalse((root / "dest" / "a.txt").exists())
            self.assertEqual((source / "a.txt").read_text(encoding="utf-8"), "original")
            self.assertTrue(result["failed"], "a nonempty new directory is preserved")

    def test_deleted_empty_directory_can_be_restored(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(cp, "CHECKPOINT_ROOT", Path(temp) / "checkpoints"):
            root = Path(temp) / "project"
            folder = root / "empty" / "nested"
            folder.mkdir(parents=True)
            cp.require_snapshot("delete", root, "empty")
            shutil.rmtree(root / "empty")
            self.assertEqual(cp.restore("delete", root)["failed"], [])
            self.assertTrue(folder.is_dir())

    def test_file_added_between_capture_passes_is_not_deleted(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(cp, "CHECKPOINT_ROOT", Path(temp) / "checkpoints"):
            root = Path(temp) / "project"
            source = root / "source"
            source.mkdir(parents=True)
            (source / "a.txt").write_text("original", encoding="utf-8")
            capture = cp._capture_entries

            def user_write_after_source(*args, **kwargs):
                result = capture(*args, **kwargs)
                (source / "new-user-file.txt").write_text("user content", encoding="utf-8")
                return result

            with patch.object(cp, "_capture_entries", side_effect=user_write_after_source):
                cp.require_move_snapshots("race", root, "source", "dest")
            work_tools.tool_move(root, {"path": "source", "to": "dest"})
            result = cp.restore("race", root)
            self.assertEqual((root / "dest" / "new-user-file.txt").read_text(encoding="utf-8"), "user content")
            self.assertEqual((source / "a.txt").read_text(encoding="utf-8"), "original")
            self.assertTrue(result["failed"], "a user file prevents removing the destination")

    def test_restore_rejects_internal_junction_and_retains_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(cp, "CHECKPOINT_ROOT", Path(temp) / "checkpoints"):
            root = Path(temp) / "project"
            source = root / "source"
            source.mkdir(parents=True)
            (source / "a.txt").write_text("original", encoding="utf-8")
            cp.require_move_snapshots("link", root, "source", "dest")
            work_tools.tool_move(root, {"path": "source", "to": "dest"})
            (root / "dest").rename(root / "moved")
            unrelated = root / "unrelated"
            unrelated.mkdir()
            user_file = unrelated / "a.txt"
            user_file.write_text("unrelated", encoding="utf-8")
            link = root / "dest"
            if os.name == "nt":
                subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(unrelated)],
                               check=True, capture_output=True)
            else:
                link.symlink_to(unrelated, target_is_directory=True)

            with patch.object(Path, "is_junction", None, create=True):
                result = cp.restore("link", root)
            self.assertEqual(user_file.read_text(encoding="utf-8"), "unrelated")
            self.assertEqual((root / "moved" / "a.txt").read_text(encoding="utf-8"), "original")
            self.assertEqual(result["remaining"], 2)
            self.assertEqual(len(result["failed"]), 2)
            link.rmdir() if os.name == "nt" else link.unlink()
            (root / "moved").rename(root / "dest")
            self.assertEqual(cp.restore("link", root)["remaining"], 0)

if __name__ == "__main__":
    unittest.main()
