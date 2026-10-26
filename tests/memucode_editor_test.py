"""The MemuCode built-in editor: loading and saving files by hand, safely."""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import code_checkpoints, code_routes, pgdata
from app.playground_api import create_app
from app.work_tools import WorkError

class EditorFunctions(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.checkpoints = patch.object(code_checkpoints, "CHECKPOINT_ROOT", Path(self.temp.name) / "checkpoints")
        self.checkpoints.start()

    def tearDown(self) -> None:
        self.checkpoints.stop()
        self.temp.cleanup()

    def test_crlf_and_bom_survive_an_edit(self) -> None:
        path = self.root / "win.txt"
        path.write_bytes(b"\xef\xbb\xbfone\r\ntwo\r\n")
        source = code_routes.edit_source(self.root, "win.txt")
        self.assertEqual(source["text"], "one\ntwo\n")
        self.assertEqual((source["eol"], source["encoding"]), ("crlf", "utf-8-sig"))
        result = code_routes.save_source(self.root, "win.txt", "one\nTWO\nthree\n", expected_hash=source["hash"],
                                         eol=source["eol"], encoding=source["encoding"])
        self.assertTrue(result["ok"])
        self.assertEqual(path.read_bytes(), b"\xef\xbb\xbfone\r\nTWO\r\nthree\r\n")
        self.assertEqual(result["hash"], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_cp1251_is_kept_and_upgraded_only_when_needed(self) -> None:
        path = self.root / "legacy.txt"
        path.write_bytes("привет".encode("cp1251"))
        source = code_routes.edit_source(self.root, "legacy.txt")
        self.assertEqual((source["text"], source["encoding"]), ("привет", "cp1251"))
        code_routes.save_source(self.root, "legacy.txt", "пока", expected_hash=source["hash"], encoding="cp1251")
        self.assertEqual(path.read_bytes(), "пока".encode("cp1251"))
        again = code_routes.edit_source(self.root, "legacy.txt")
        result = code_routes.save_source(self.root, "legacy.txt", "emoji 😀", expected_hash=again["hash"], encoding="cp1251")
        self.assertEqual(result["encoding"], "utf-8")
        self.assertEqual(path.read_text(encoding="utf-8"), "emoji 😀")

    def test_a_newer_file_on_disk_is_never_overwritten_silently(self) -> None:
        path = self.root / "app.js"
        path.write_text("let a = 1;\n", encoding="utf-8")
        source = code_routes.edit_source(self.root, "app.js")
        path.write_text("let a = 2; // the agent\n", encoding="utf-8")
        conflict = code_routes.save_source(self.root, "app.js", "let a = 3;\n", expected_hash=source["hash"])
        self.assertTrue(conflict["conflict"])
        self.assertEqual(path.read_text(encoding="utf-8"), "let a = 2; // the agent\n")
        forced = code_routes.save_source(self.root, "app.js", "let a = 3;\n", expected_hash=source["hash"], force=True)
        self.assertTrue(forced["ok"])
        self.assertEqual(path.read_text(encoding="utf-8"), "let a = 3;\n")

    def test_a_hand_edit_is_captured_for_the_undo_tab(self) -> None:
        path = self.root / "notes.md"
        path.write_text("before\n", encoding="utf-8")
        source = code_routes.edit_source(self.root, "notes.md")
        code_routes.save_source(self.root, "notes.md", "after\n", expected_hash=source["hash"], session_id="s1")
        entries = code_checkpoints.list_entries("s1")
        self.assertEqual([(row["path"], row["label"]) for row in entries], [("notes.md", "manual_edit")])
        code_checkpoints.restore("s1", self.root)
        self.assertEqual(path.read_text(encoding="utf-8"), "before\n")

    def test_hand_edit_refuses_when_undo_cannot_capture_file(self) -> None:
        path = self.root / "notes.md"
        path.write_text("before\n", encoding="utf-8")
        source = code_routes.edit_source(self.root, "notes.md")
        with patch.object(code_checkpoints, "MAX_FILE_BYTES", 1):
            with self.assertRaises(WorkError):
                code_routes.save_source(self.root, "notes.md", "after\n",
                                        expected_hash=source["hash"], session_id="s1")
        self.assertEqual(path.read_text(encoding="utf-8"), "before\n")

    def test_refusals(self) -> None:
        (self.root / "blob.bin").write_bytes(b"\x00\x01\x02" * 10)
        with self.assertRaises(WorkError):
            code_routes.edit_source(self.root, "blob.bin")
        with self.assertRaises(WorkError):
            code_routes.edit_source(self.root, "../outside.txt")
        with self.assertRaises(WorkError):
            code_routes.save_source(self.root, "../outside.txt", "x", expected_hash="")
        big = self.root / "big.txt"
        big.write_bytes(b"a" * (code_routes.EDIT_LIMIT + 1))
        with self.assertRaises(WorkError):
            code_routes.edit_source(self.root, "big.txt")
        self.assertEqual(list(self.root.glob(".memucode-*.tmp")), [])

class EditorRoutes(unittest.TestCase):
    def test_routes_load_save_and_report_conflicts_as_409(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp) / "data"
            project = Path(temp) / "project"
            data.mkdir()
            project.mkdir()
            (project / "index.html").write_text("<h1>Hi</h1>\n", encoding="utf-8")
            with patch.object(pgdata, "MEMU_DIR", data), \
                    patch.object(code_checkpoints, "CHECKPOINT_ROOT", Path(temp) / "checkpoints"):
                pgdata.save_workspaces({"active_id": "p", "items": [{"id": "p", "name": "P", "path": str(project), "added": 1}]})
                with TestClient(create_app(), base_url="http://127.0.0.1") as client:
                    source = client.post("/api/code/source", json={"project_id": "p", "path": "index.html"}).json()
                    self.assertEqual(source["text"], "<h1>Hi</h1>\n")
                    saved = client.post("/api/code/save", json={"project_id": "p", "path": "index.html",
                                                                "text": "<h1>Hello</h1>\n", "hash": source["hash"]})
                    self.assertEqual(saved.status_code, 200, saved.text)
                    stale = client.post("/api/code/save", json={"project_id": "p", "path": "index.html",
                                                                "text": "<h1>Old</h1>\n", "hash": source["hash"]})
                    self.assertEqual(stale.status_code, 409)
                    self.assertTrue(stale.json()["conflict"])
                    self.assertEqual((project / "index.html").read_text(encoding="utf-8"), "<h1>Hello</h1>\n")
                    outside = client.post("/api/code/source", json={"project_id": "p", "path": "../x"})
                    self.assertEqual(outside.status_code, 400)

if __name__ == "__main__":
    unittest.main()
