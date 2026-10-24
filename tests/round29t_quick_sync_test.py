"""Round 29 phase 2 (r29t-quick-sync): regressions found by deep testing of Quick Access (Explorer change sets,
extras), the screen layer and Sync / safe mode. Real files in temp folders; Win32 is not needed."""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

from app import quick_apps, quick_explorer as qe, quick_extras  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def tree(folder: Path) -> dict:
    return {p.relative_to(folder).as_posix(): (p.read_bytes() if p.is_file() else None)
            for p in sorted(folder.rglob("*"))}


class ChangeSetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="qx-")) / "Загрузки"
        self.folder.mkdir()
        quick_apps.save_settings({"access": "ask"})

    def ctx(self):
        return quick_apps.register_context({"kind": "explorer", "folder": str(self.folder)})

    def put(self, rel: str, data: str) -> None:
        path = self.folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding="utf-8")

    def cycle(self, ops):
        before = tree(self.folder)
        change = qe.plan_changes(self.ctx(), ops)
        result = qe.apply_set(change["id"])
        after = tree(self.folder)
        undo = qe.undo(result["set"]["undo"]) if result["set"]["undo"] else None
        self.assertEqual(tree(self.folder), before, "undo must restore the folder exactly")
        return change, result, after, undo

    def test_shifted_numbering_chain(self):
        self.put("1.txt", "one"); self.put("2.txt", "two"); self.put("3.txt", "three")
        change, result, after, undo = self.cycle([{"op": "move", "path": f"{i}.txt", "to": f"{i + 1}.txt"} for i in (1, 2, 3)])
        self.assertEqual([op["status"] for op in change["ops"]], ["applied"] * 3)
        self.assertEqual({k: v for k, v in after.items()}, {"2.txt": b"one", "3.txt": b"two", "4.txt": b"three"})
        self.assertEqual(undo["problems"], [])

    def test_swap_names_and_undo(self):
        self.put("a.txt", "A"); self.put("b.txt", "B")
        _c, result, after, undo = self.cycle([{"op": "move", "path": "a.txt", "to": "b.txt"},
                                              {"op": "move", "path": "b.txt", "to": "a.txt"}])
        self.assertEqual(after, {"a.txt": b"B", "b.txt": b"A"})
        self.assertEqual((result["applied"], undo["undone"], undo["problems"]), (2, 2, []))

    def test_existing_name_still_refused(self):
        self.put("a.txt", "A"); self.put("b.txt", "B")
        with self.assertRaises(qe.FolderError):
            qe.plan_changes(self.ctx(), [{"op": "move", "path": "a.txt", "to": "b.txt"}])

    def test_target_appearing_after_preview_is_not_overwritten(self):
        self.put("a.txt", "A"); self.put("c.txt", "C")
        change = qe.plan_changes(self.ctx(), [{"op": "move", "path": "a.txt", "to": "b.txt"},
                                              {"op": "copy", "path": "c.txt", "to": "d.txt"}])
        self.put("b.txt", "USER"); self.put("d.txt", "USER2")
        result = qe.apply_set(change["id"])
        self.assertEqual(result["applied"], 0)
        self.assertEqual((self.folder / "b.txt").read_text(), "USER")
        self.assertEqual((self.folder / "d.txt").read_text(), "USER2")
        self.assertEqual((self.folder / "a.txt").read_text(), "A")

    def test_folder_move_then_children_by_old_and_new_path(self):
        self.put("Docs/a.txt", "hello"); self.put("Docs/b.txt", "b")
        change, result, after, _undo = self.cycle([
            {"op": "move", "path": "Docs/b.txt", "to": "Docs/b2.txt"},       # mtime of «Docs» changes …
            {"op": "move", "path": "Docs", "to": "Documents"},               # … but that is this set itself
            {"op": "edit", "path": "Documents/a.txt", "old_text": "hello", "new_text": "bye"},
            {"op": "move", "path": "Docs/a.txt", "to": "x.txt"}])            # the old path → the moved file
        self.assertEqual([op["status"] for op in change["ops"]], ["applied"] * 4, change["ops"])
        self.assertEqual(after, {"Documents": None, "Documents/b2.txt": b"b", "x.txt": b"bye"})

    def test_children_of_deleted_folder_rejected_at_preview(self):
        self.put("old/x.txt", "x")
        change = qe.plan_changes(self.ctx(), [{"op": "delete", "path": "old"},
                                              {"op": "edit", "path": "old/x.txt", "old_text": "x", "new_text": "y"}])
        self.assertEqual([op["status"] for op in change["ops"]], ["pending", "invalid"])

    def test_two_changes_of_one_file_build_on_each_other(self):
        (self.folder / "cp.txt").write_bytes("Привет мир\r\nвторая\r\n".encode("cp1251"))
        self.cycle([{"op": "edit", "path": "cp.txt", "old_text": "мир", "new_text": "дом"},
                    {"op": "append", "path": "cp.txt", "text": "третья"}])
        change = qe.plan_changes(self.ctx(), [{"op": "edit", "path": "cp.txt", "old_text": "мир", "new_text": "дом"},
                                              {"op": "append", "path": "cp.txt", "text": "третья"}])
        qe.apply_set(change["id"])
        self.assertEqual((self.folder / "cp.txt").read_bytes(), "Привет дом\r\nвторая\r\nтретья".encode("cp1251"))

    def test_change_after_overwrite_waits_for_the_click_in_full_mode(self):
        self.put("a.txt", "old")
        quick_apps.save_settings({"access": "full"})
        try:
            out = qe._change_tool(self.ctx(), {"ops": [{"op": "write", "path": "a.txt", "text": "NEW"},
                                                       {"op": "append", "path": "a.txt", "text": "tail"}]}, "ru")
        finally:
            quick_apps.save_settings({"access": "ask"})
        self.assertEqual(out["applied"], 0)
        self.assertEqual((self.folder / "a.txt").read_text(), "old")
        self.assertIn("«Применить»", out["note"])

    def test_undo_removes_folders_it_created(self):
        self.put("a.txt", "a")
        self.cycle([{"op": "write", "path": "new/sub/n.txt", "text": "hi"},
                    {"op": "copy", "path": "a.txt", "to": "bk/2026/a.txt"},
                    {"op": "move", "path": "a.txt", "to": "moved/deep/a.txt"}])

    def test_big_folder_delete_refused_at_preview(self):
        self.put("big/a.bin", "x")
        old = qe.UNDO_MAX_BYTES
        qe.UNDO_MAX_BYTES = 0
        try:
            # Round 30: with the Recycle Bin on a big delete goes there; the refusal holds when it is off
            with mock.patch.object(qe, "_trash_on", return_value=False), self.assertRaises(qe.FolderError):
                qe.plan_changes(self.ctx(), [{"op": "delete", "path": "big"}])
        finally:
            qe.UNDO_MAX_BYTES = old

    def test_read_line_range_beyond_the_first_chunk(self):
        self.put("big.log", "\n".join(f"line {i} " + "x" * 40 for i in range(1, 20001)))
        out = qe.tool_read(self.ctx(), {"path": "big.log", "start_line": 15000, "end_line": 15001})
        self.assertTrue(out["text"].startswith("15000: line 15000"))
        self.assertEqual(out["total_lines"], 20000)
        self.assertIn("note", qe.tool_read(self.ctx(), {"path": "big.log"}))

    def test_context_block_asks_for_russian_folder_names(self):
        self.assertIn("Name new folders in Russian", qe.context_block(self.ctx(), "ru"))
        self.assertNotIn("in Russian", qe.context_block(self.ctx(), "en"))


class NotesTests(unittest.TestCase):
    def test_crlf_notes_file_keeps_crlf_and_one_day_heading(self):
        folder = Path(tempfile.mkdtemp())
        path = folder / "Быстрые заметки.md"
        path.write_bytes("\ufeff# Быстрые заметки\r\n## 2026-10-10\r\n- 10:00 a\r\n".encode())
        quick_extras.add_note("b\nc", settings={"notes_dir": str(folder)}, now=time.mktime((2026, 10, 10, 12, 0, 0, 0, 0, -1)))
        self.assertEqual(path.read_bytes().decode(), "\ufeff# Быстрые заметки\r\n## 2026-10-10\r\n- 10:00 a\r\n- 12:00 b\r\n  c\r\n")

    def test_language_switch_keeps_one_notes_file(self):
        folder = Path(tempfile.mkdtemp())
        quick_extras.add_note("ru", settings={"notes_dir": str(folder)}, lang="ru")
        out = quick_extras.add_note("en", settings={"notes_dir": str(folder)}, lang="en")
        self.assertEqual(out["name"], "Быстрые заметки.md")
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["Быстрые заметки.md"])


class CurrencyParseTests(unittest.TestCase):
    def test_more_phrasings(self):
        script = ("const {parseCurrency:p}=require(process.argv[1]);console.log(JSON.stringify(["
                  "p('сколько будет 100 usd в rub'),p('50 евро на рубли'),p('100 тг'),p('100 рублей на человека'),"
                  "p('how much is 20 eur in usd')]))")
        try:
            out = subprocess.run(["node", "-e", script, str(ROOT / "app/pgstatic/quick/plugins/extras.js")],
                                 capture_output=True, text=True, timeout=30)
        except FileNotFoundError:
            self.skipTest("node is not installed")
        import json
        rows = json.loads(out.stdout)
        self.assertEqual(rows[0], {"amount": 100, "from": "USD", "to": "RUB"})
        self.assertEqual(rows[1], {"amount": 50, "from": "EUR", "to": "RUB"})
        self.assertEqual(rows[2], {"amount": 100, "from": "KZT", "to": None})
        self.assertIsNone(rows[3])
        self.assertEqual(rows[4], {"amount": 20, "from": "EUR", "to": "USD"})


if __name__ == "__main__":
    unittest.main()
