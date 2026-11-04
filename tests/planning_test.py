"""Planning contributions, module triggers and old-rule compatibility (Round 42, item 8)."""
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import fastapi  # noqa: F401
except ImportError:
    _fa = types.ModuleType("fastapi")
    _fa.Request = object
    sys.modules["fastapi"] = _fa
    _fr = types.ModuleType("fastapi.responses")
    _fr.JSONResponse = object
    sys.modules["fastapi.responses"] = _fr

from app import schedule_rules as R  # noqa: E402
from app.module_integration import normalize_contributes, validate_contributes  # noqa: E402

MANIFESTS = sorted((ROOT / "app" / "module_builtins").glob("*/manifest.json"))
EXPECTED = {"translator", "studio", "design", "benchmarks", "catalog", "api-models",
            "characters", "music", "learn", "memudb", "meet", "utils"}


class ContributionsTest(unittest.TestCase):
    def test_every_builtin_module_declares_planning(self):
        found = {}
        for path in MANIFESTS:
            data = json.loads(path.read_text(encoding="utf-8"))
            found[data["id"]] = data
            errors = validate_contributes(data.get("contributes"), builtin=True)
            self.assertEqual(errors, [], f"{data['id']}: {errors}")
        self.assertEqual(set(found), EXPECTED)
        for module_id, data in found.items():
            clean = normalize_contributes(data.get("contributes"), builtin=True)
            actions = clean.get("planning", [])
            self.assertGreaterEqual(len(actions), 5, module_id)
            self.assertLessEqual(len(actions), 12, module_id)
            ids = [a["id"] for a in actions]
            self.assertEqual(len(set(ids)), len(ids), module_id)
            for action in actions:
                # Off by default: a manifest can never arm a planning action.
                self.assertIs(action["enabled"], False, module_id)
                self.assertIn(action["trigger"]["kind"], ("threshold", "periodic"))
                self.assertIn(action["action"]["kind"], ("notify", "chat", "scenario", "module"))
                if action["trigger"]["kind"] == "threshold":
                    self.assertGreaterEqual(action["trigger"]["threshold"], 1)
                settings = {s["key"]: s for s in action["settings"]}
                if "threshold" in settings:
                    self.assertEqual(settings["threshold"]["default"], action["trigger"]["threshold"],
                                     f"{module_id}/{action['id']}: setting default must be the trigger threshold")

    def test_characters_thresholds_match_the_request(self):
        data = json.loads((ROOT / "app" / "module_builtins" / "characters" / "manifest.json").read_text(encoding="utf-8"))
        clean = normalize_contributes(data["contributes"], builtin=True)
        by_id = {a["id"]: a for a in clean["planning"]}
        messages = by_id["save-chat-messages"]
        self.assertEqual(messages["trigger"], {"kind": "threshold", "metric": "messages",
                                               "threshold": 30, "scope": "per_chat"})
        tokens = by_id["save-chat-tokens"]
        self.assertEqual(tokens["trigger"]["metric"], "tokens")
        self.assertEqual(tokens["trigger"]["threshold"], 500000)
        self.assertEqual(tokens["trigger"]["scope"], "per_chat")

    def test_bad_planning_is_dropped_with_errors(self):
        errors = []
        clean = normalize_contributes({"planning": [
            {"id": "bad-metric", "trigger": {"metric": "nope", "threshold": 1},
             "action": {"kind": "notify", "text": "x"}},
            {"id": "bad-metric", "trigger": {"metric": "items", "threshold": 1},
             "action": {"kind": "notify", "text": "x"}},
            {"id": "enabled", "trigger": {"metric": "items", "threshold": 1},
             "action": {"kind": "notify", "text": "x"}, "enabled": True},
        ]}, errors=errors)
        self.assertEqual(len(clean.get("planning", [])), 2, "the bad-metric item is dropped, the rest stay")
        self.assertTrue(errors, "bad items must be reported")
        self.assertTrue(all(a["enabled"] is False for a in clean["planning"]))

    def test_meet_has_no_planning_before_the_mechanism(self):
        # The meet module (created earlier in this round) declares planning now;
        # the schema must still accept a manifest without any planning block.
        clean = normalize_contributes({"panels": []})
        self.assertNotIn("planning", clean)


class ModuleTriggerTest(unittest.TestCase):
    def test_normalize(self):
        trigger = R.normalize_module_trigger({"kind": "threshold", "metric": "due", "threshold": 5,
                                              "settings": {"threshold": 7}})
        self.assertEqual(trigger["threshold"], 7, "the editable setting wins over the manifest threshold")
        self.assertEqual(R.normalize_module_trigger({"kind": "periodic", "every_minutes": 60})["every_minutes"], 60)
        with self.assertRaises(R.RuleError):
            R.normalize_module_trigger({"kind": "threshold", "metric": "due", "threshold": 0})
        with self.assertRaises(R.RuleError):
            R.normalize_module_trigger({"kind": "threshold", "metric": "unknown", "threshold": 1})

    def test_edge_triggered_once_then_reset(self):
        task = {"module_id": "characters",
                "module_trigger": R.normalize_module_trigger(
                    {"kind": "threshold", "metric": "messages", "threshold": 30, "scope": "per_chat"})}
        stats = {"characters": {"messages": 40, "max_messages": 40}}
        self.assertEqual(R.check_module_trigger(task, stats, 100.0), (True, True))
        self.assertEqual(R.check_module_trigger(task, stats, 200.0), (False, False),
                         "staying above the threshold must not refire")
        stats["characters"]["max_messages"] = 10
        self.assertEqual(R.check_module_trigger(task, stats, 300.0), (False, True),
                         "falling below resets the watermark")
        stats["characters"]["max_messages"] = 35
        self.assertEqual(R.check_module_trigger(task, stats, 400.0), (True, True))

    def test_unknown_stat_never_fires(self):
        task = {"module_id": "translator",
                "module_trigger": R.normalize_module_trigger({"metric": "items", "threshold": 30})}
        self.assertEqual(R.check_module_trigger(task, {}, 100.0), (False, False))

    def test_periodic_always_fires(self):
        task = {"module_id": "meet",
                "module_trigger": R.normalize_module_trigger({"kind": "periodic", "every_minutes": 60})}
        self.assertEqual(R.check_module_trigger(task, {}, 100.0), (True, False))

    def test_stats_snapshot_never_raises(self):
        stats = R.module_stats_snapshot()
        self.assertIsInstance(stats, dict)


class OldRulesTest(unittest.TestCase):
    def test_legacy_and_rule_tasks_unchanged(self):
        rule = R.normalize_rule({"when": [{"type": "daily", "times": ["09:00"]}]})
        self.assertEqual(rule["when"][0]["type"], "daily")
        legacy = R.legacy_rule({"run_at": "2030-01-02T09:00"})
        self.assertEqual(legacy["when"][0]["type"], "once")
        task = {"kind": "chat", "prompt": "hello", "rule": {"when": [{"type": "daily", "times": ["09:00"]}]}}
        R.sanitize_task(task)
        self.assertEqual(task["kind"], "chat")
        self.assertEqual(task["prompt"], "hello")

    def test_module_action_starts_disabled_without_consent(self):
        from app import schedule_rules_api as api
        store = []

        def fake_mutate(fn):
            rows = [dict(r) for r in store]
            changed = fn(rows)
            if changed:
                store[:] = rows
            return changed

        fake_pgdata = types.ModuleType("app.pgdata")
        fake_pgdata._mutate_schedules = fake_mutate
        with patch.object(api, "_load_tasks", lambda: [dict(r) for r in store]):
            with patch.dict(sys.modules, {"app.pgdata": fake_pgdata}):
                task_id = api.save_task({
                    "name": "Save chat", "kind": "notify", "prompt": "save it",
                    "enabled": True, "module_id": "characters",
                    "module_action": {"module": "characters", "action_id": "save-chat-messages"},
                    "module_trigger": {"kind": "threshold", "metric": "messages", "threshold": 30,
                                       "scope": "per_chat"},
                    "rule": {"when": [{"type": "interval", "every": 60}]},
                }, "ru")
        saved = next(r for r in store if r["id"] == task_id)
        self.assertIs(saved["enabled"], False)
        self.assertTrue(saved.get("consent_pending"))
        self.assertEqual(saved["module_trigger"]["threshold"], 30)

    def test_scenario_preview_describes_steps(self):
        from app import schedule_rules_api as api
        payload = api.preview_payload({
            "kind": "scenario", "name": "S",
            "scenario": [{"type": "set", "name": "n", "value": "1"},
                         {"type": "notify", "text": "hi ${n}"}],
            "rule": {"when": [{"type": "daily", "times": ["09:00"]}]},
        }, [], "ru")
        self.assertEqual(len(payload["steps_text"]), 2)
        with self.assertRaises(R.RuleError):
            api.preview_payload({
                "kind": "scenario", "name": "S",
                "scenario": [{"type": "explode"}],
                "rule": {"when": [{"type": "daily", "times": ["09:00"]}]},
            }, [], "ru")


if __name__ == "__main__":
    unittest.main()
