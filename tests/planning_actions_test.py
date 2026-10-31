"""Module planning actions are visible even when modules are disabled.

Built-in modules start disabled, so planning_actions() must not filter
them out: Planning would otherwise show no module actions at all in
the default state. Actions stay off and consent-gated.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.module_integration import planning_action, planning_actions, validate_contributes  # noqa: E402

MANIFESTS = sorted((ROOT / "app" / "module_builtins").glob("*/manifest.json"))


class PlanningActionsVisibleTest(unittest.TestCase):
    def test_fresh_root_exposes_all_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = planning_actions(Path(tmp))
        self.assertEqual(len(rows), 11)
        self.assertEqual({r["module"] for r in rows},
                         {"translator", "studio", "design", "benchmarks", "catalog",
                          "api-models", "characters", "music", "learn", "memudb", "meet"})
        total = sum(len(r["actions"]) for r in rows)
        self.assertGreaterEqual(total, 22)
        for row in rows:
            self.assertGreaterEqual(len(row["actions"]), 2)
            self.assertLessEqual(len(row["actions"]), 6)
            for action in row["actions"]:
                self.assertIs(action["enabled"], False)
                self.assertIs(action["requires_consent"], True)

    def test_lookup_works_for_disabled_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            found = planning_action("characters", "save-chat-messages", root=Path(tmp))
        self.assertIsNotNone(found)
        self.assertEqual(found["action"]["id"], "save-chat-messages")

    def test_manifests_validate_without_errors(self):
        for manifest_path in MANIFESTS:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            errors = validate_contributes(manifest.get("contributes"), builtin=True,
                                          folder=manifest_path.parent)
            self.assertEqual(errors, [], manifest_path.name)

    def test_action_ids_unique_per_module(self):
        for manifest_path in MANIFESTS:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            ids = [a["id"] for a in manifest.get("contributes", {}).get("planning", [])]
            self.assertEqual(len(ids), len(set(ids)), manifest_path.name)


if __name__ == "__main__":
    unittest.main()
