"""Round 29 (r29-moddocs-keys): the single hotkey registry (app/pgstatic/hotkeys.js).

* the registry loads in Node, ids are unique, every entry has a group from GROUPS and a scope from SCOPES;
* English descriptions have no Cyrillic, Russian ones are present;
* the default bindings have no conflicts; customizable entries have exactly one default;
* «Send later» (schedule_send, Ctrl+Shift+Enter) is in the registry, so it is listed and rebindable;
* app/docs/{ru,en}/shortcuts.md are in sync with the registry (tests/round29_hotkeys_docs.cjs --check);
* hotkeys.js / hotkeys.css are wired into index.html before app.js.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "pgstatic"
CYR = re.compile(r"[А-Яа-яЁё]")

DUMP = """
const H = require(process.argv[1]);
const rows = H.all();
process.stdout.write(JSON.stringify({
  groups: H.GROUPS.map((g) => g.id), scopes: Object.keys(H.SCOPES), rows,
  conflicts: H.conflicts({}),
}));
"""

@unittest.skipUnless(shutil.which("node"), "node is not installed")
class HotkeyRegistryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        result = subprocess.run(["node", "-e", DUMP, str(STATIC / "hotkeys.js")], capture_output=True, text=True,
                                encoding="utf-8")
        if result.returncode:
            raise AssertionError(result.stderr)
        cls.data = json.loads(result.stdout)

    def test_ids_groups_scopes(self) -> None:
        ids = [row["id"] for row in self.data["rows"]]
        self.assertEqual(len(ids), len(set(ids)), [i for i in ids if ids.count(i) > 1])
        for row in self.data["rows"]:
            self.assertIn(row["group"], self.data["groups"], row["id"])
            self.assertIn(row["scope"], self.data["scopes"], row["id"])
            self.assertTrue(row["ru"], row["id"])
            self.assertFalse(CYR.search(row["en"]), (row["id"], row["en"]))
            if row["custom"]:
                self.assertEqual(len(row["keys"]), 1, row["id"])

    def test_no_default_conflicts(self) -> None:
        self.assertEqual(self.data["conflicts"], [])

    def test_schedule_send_is_registered(self) -> None:
        row = next((r for r in self.data["rows"] if r["id"] == "schedule_send"), None)
        self.assertIsNotNone(row)
        self.assertEqual(row["keys"], ["Ctrl+Shift+Enter"])
        self.assertTrue(row["custom"])
        self.assertEqual(row["group"], "chat")

    def test_docs_in_sync(self) -> None:
        result = subprocess.run(["node", str(ROOT / "tests" / "round29_hotkeys_docs.cjs"), "--check"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

class HotkeyWiringTest(unittest.TestCase):
    def test_index_tags(self) -> None:
        index = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertIn("/hotkeys.css?v=", index)
        self.assertIn("/hotkeys.js?v=", index)
        self.assertLess(index.index("/hotkeys.js?v="), index.index("/app.js?v="))

    def test_app_reads_the_registry(self) -> None:
        app = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn("MemuHotkeys.match(id, e", app)
        self.assertIn("MemuHotkeys.handleMain(e)", app)
        schedsend = (STATIC / "schedsend.js").read_text(encoding="utf-8")
        self.assertIn('MemuHotkeys.match("schedule_send"', schedsend)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_node_check(self) -> None:
        result = subprocess.run(["node", "--check", str(STATIC / "hotkeys.js")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

if __name__ == "__main__":
    unittest.main()
