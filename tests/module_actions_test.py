"""Real module actions for Planning (app/module_actions.py) and their per-chat / delta triggers."""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import module_actions as ma  # noqa: E402
from app import schedule_rules as rules  # noqa: E402
from app.characters_api import store_get, store_set  # noqa: E402


class FakeServices:
    def __init__(self, answer: str = "## Ключевые события\n- Встретились у реки") -> None:
        self.answer = answer
        self.calls = []

    def default_model(self) -> str:
        return "test-model"

    async def complete(self, model, messages):
        self.calls.append((model, messages))
        return self.answer


def run(coro):
    return asyncio.run(coro)


class ModuleActionsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "modules"
        self.out = Path(self.tmp.name) / "out"
        self._env = os.environ.get(ma.OUTPUT_ROOT_ENV)
        os.environ[ma.OUTPUT_ROOT_ENV] = str(self.out)

    def tearDown(self) -> None:
        if self._env is None:
            os.environ.pop(ma.OUTPUT_ROOT_ENV, None)
        else:
            os.environ[ma.OUTPUT_ROOT_ENV] = self._env
        self.tmp.cleanup()

    def _chat(self, char_id: str, chat_id: str, count: int) -> None:
        messages = [{"mes": f"реплика {i}", "is_user": i % 2 == 0, "name": "Вы" if i % 2 == 0 else "Аня"} for i in range(count)]
        store_set(self.root, "characters", "index", [{"id": char_id, "name": "Аня"}])
        store_set(self.root, "characters", f"chatlist/{char_id}", [{"id": chat_id, "title": "У реки", "count": count, "tokens": count * 10}])
        store_set(self.root, "characters", f"chats/{char_id}/{chat_id}", {"id": chat_id, "messages": messages})

    def test_registry_and_manifests_use_known_handlers(self) -> None:
        self.assertGreaterEqual(len(ma.HANDLERS), 30)
        from app.module_integration import planning_actions
        rows = planning_actions()
        modules = {row["module"] for row in rows}
        self.assertTrue({"characters", "meet", "learn", "memudb", "music", "translator", "studio", "design",
                         "api-models", "benchmarks", "catalog", "utils"} <= modules)
        for row in rows:
            real = [a for a in row["actions"] if a["action"]["kind"] == "module"]
            self.assertTrue(real, row["module"])
            for action in real:
                self.assertTrue(ma.known(action["action"]["handler"], row["module"]), action["id"])
                self.assertIn("handler_info", action)
                self.assertFalse(action["enabled"])
            for action in row["actions"]:
                self.assertNotEqual(action.get("action", {}).get("text_en"), row["module"])

    def test_wrong_module_is_refused(self) -> None:
        result = run(ma.run_module_action("music", "characters.backup", root=self.root))
        self.assertFalse(result["ok"])
        self.assertFalse(run(ma.run_module_action("characters", "characters.nope", root=self.root))["ok"])

    def test_long_chat_gets_ai_memory_and_markdown(self) -> None:
        self._chat("c1", "chat1", 40)
        services = FakeServices()
        result = run(ma.run_module_action("characters", "characters.save_long_chats", root=self.root, services=services,
                                          settings={"threshold": 30}, pending=["c1/chat1"]))
        self.assertTrue(result["ok"], result)
        memory = store_get(self.root, "characters", "memory/c1/chat1")
        self.assertIn("Ключевые события", memory["text"])
        self.assertEqual(memory["messages"], 40)
        self.assertEqual(memory["covered"], 40)
        self.assertTrue(any(f.endswith(".md") for f in result["files"]))
        md = Path(result["files"][0]).read_text(encoding="utf-8")
        self.assertIn("реплика 39", md)
        self.assertIn("<transcript>", services.calls[0][1][1]["content"])
        # a manual run does nothing more: the chat did not grow by another 30 messages
        again = run(ma.run_module_action("characters", "characters.save_long_chats", root=self.root, services=services,
                                         settings={"threshold": 30}))
        self.assertTrue(again["ok"])
        self.assertEqual(len(services.calls), 1)

    def test_dry_run_writes_nothing(self) -> None:
        self._chat("c1", "chat1", 40)
        services = FakeServices()
        result = run(ma.run_module_action("characters", "characters.save_long_chats", root=self.root, services=services,
                                          settings={"threshold": 30}, dry=True))
        self.assertTrue(result["ok"])
        self.assertEqual(services.calls, [])
        self.assertIsNone(store_get(self.root, "characters", "memory/c1/chat1"))
        self.assertFalse(self.out.exists())

    def test_backup_and_export_zip(self) -> None:
        self._chat("c1", "chat1", 4)
        backup = run(ma.run_module_action("characters", "characters.backup", root=self.root))
        export = run(ma.run_module_action("characters", "characters.export_chats", root=self.root))
        self.assertTrue(backup["ok"] and export["ok"])
        import zipfile
        with zipfile.ZipFile(export["files"][0]) as archive:
            self.assertTrue(any(name.endswith(".md") for name in archive.namelist()))
        with zipfile.ZipFile(backup["files"][0]) as archive:
            self.assertIn("index.json", archive.namelist())

    def test_meeting_summary_is_stored_and_never_overwritten(self) -> None:
        store_set(self.root, "meet", "meetings/meet-aaaaaaaaaaaaaaaaaaaa", {"id": "meet-aaaaaaaaaaaaaaaaaaaa", "title": "Планёрка", "created_at": int(__import__("time").time() * 1000),
                                                      "transcript": "Решили выпустить версию в пятницу. Иван пишет тесты."})
        store_set(self.root, "meet", "meetings/meet-bbbbbbbbbbbbbbbbbbbb", {"id": "meet-bbbbbbbbbbbbbbbbbbbb", "title": "Готово", "transcript": "x", "summary": "своё"})
        services = FakeServices("## Конспект\nРелиз\n## Решения\n- Релиз в пятницу\n## Задачи\n- Иван: тесты")
        result = run(ma.run_module_action("meet", "meet.summarize_pending", root=self.root, services=services))
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(services.calls), 1)
        self.assertIn("Иван", store_get(self.root, "meet", "meetings/meet-aaaaaaaaaaaaaaaaaaaa")["summary"])
        self.assertEqual(store_get(self.root, "meet", "meetings/meet-bbbbbbbbbbbbbbbbbbbb")["summary"], "своё")
        tasks = run(ma.run_module_action("meet", "meet.tasks_digest", root=self.root, settings={"days": 7}))
        self.assertIn("Иван: тесты", Path(tasks["files"][0]).read_text(encoding="utf-8"))

    def test_translator_and_design_use_persisted_fields(self) -> None:
        persist = self.root / "persist"
        persist.mkdir(parents=True)
        (persist / "translator.json").write_text(json.dumps({"glossary": "cat = кот\ndog — собака", "history": [
            {"from": "en", "to": "ru", "source": "cat", "result": "кот", "at": 1}]}), encoding="utf-8")
        (persist / "design.json").write_text(json.dumps({"versions": [{"code": "<h1>A</h1>", "at": 1}, {"code": "<h1>B</h1>", "at": 2}]}),
                                             encoding="utf-8")
        glossary = run(ma.run_module_action("translator", "translator.glossary_backup", root=self.root))
        self.assertTrue(glossary["ok"], glossary)
        self.assertEqual(glossary["data"]["pairs"], 2)
        csv_file = next(f for f in glossary["files"] if f.endswith(".csv"))
        self.assertIn("собака", Path(csv_file).read_text(encoding="utf-8-sig"))
        versions = run(ma.run_module_action("design", "design.save_versions", root=self.root))
        self.assertEqual(versions["data"]["versions"], 2)
        self.assertTrue(any(f.endswith("index.html") for f in versions["files"]))

    def test_memudb_backup_copies_recent_sqlite(self) -> None:
        from app import pgdata
        db = Path(self.tmp.name) / "notes.sqlite"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE t (a)")
        con.execute("INSERT INTO t VALUES (1)")
        con.commit()
        con.close()
        old = pgdata.MEMU_DIR
        pgdata.MEMU_DIR = Path(self.tmp.name) / "memu"
        try:
            ma.remember_database(db)
            ma.remember_database(Path(self.tmp.name) / "evil.txt")  # wrong suffix: ignored
            self.assertEqual([r["name"] for r in ma.recent_databases()], ["notes.sqlite"])
            result = run(ma.run_module_action("memudb", "memudb.backup_databases", root=self.root))
            self.assertTrue(result["ok"], result)
            copy = sqlite3.connect(result["files"][0])
            self.assertEqual(copy.execute("SELECT a FROM t").fetchone()[0], 1)
            copy.close()
            health = run(ma.run_module_action("memudb", "memudb.health_report", root=self.root))
            self.assertIn("ok", health["text"])
        finally:
            pgdata.MEMU_DIR = old


class TriggerTest(unittest.TestCase):
    def test_per_chat_fires_for_each_chat_on_its_own(self) -> None:
        task = {"module_id": "characters", "module_trigger": {"kind": "threshold", "metric": "messages", "threshold": 30,
                                                              "scope": "per_chat"}}
        stats = {"characters": {"chats": {"a/1": {"messages": 10}, "b/2": {"messages": 31}}}}
        fire, changed = rules.check_module_trigger(task, stats, 1.0)
        self.assertTrue(fire and changed)
        self.assertEqual(task["module_trigger_state"]["pending"], ["b/2"])
        fire, _ = rules.check_module_trigger(task, stats, 2.0)
        self.assertFalse(fire)  # handled; it did not grow by another 30
        stats["characters"]["chats"]["a/1"]["messages"] = 35
        fire, _ = rules.check_module_trigger(task, stats, 3.0)
        self.assertTrue(fire)
        self.assertEqual(task["module_trigger_state"]["pending"], ["a/1"])
        stats["characters"]["chats"]["b/2"]["messages"] = 61
        fire, _ = rules.check_module_trigger(task, stats, 4.0)
        self.assertTrue(fire)
        self.assertEqual(task["module_trigger_state"]["pending"], ["b/2"])

    def test_delta_mode_fires_on_growth(self) -> None:
        task = {"module_id": "benchmarks", "module_trigger": rules.normalize_module_trigger(
            {"kind": "threshold", "metric": "changes", "threshold": 2, "mode": "delta"})}
        self.assertEqual(task["module_trigger"]["mode"], "delta")
        self.assertEqual(rules.check_module_trigger(task, {"benchmarks": {"changes": 5}}, 1.0), (False, True))
        self.assertFalse(rules.check_module_trigger(task, {"benchmarks": {"changes": 6}}, 2.0)[0])
        self.assertTrue(rules.check_module_trigger(task, {"benchmarks": {"changes": 7}}, 3.0)[0])
        self.assertFalse(rules.check_module_trigger(task, {"benchmarks": {"changes": 7}}, 4.0)[0])

    def test_runtime_fields_keep_trigger_state(self) -> None:
        previous = {"id": "t", "rule": {"a": 1}, "module_trigger_state": {"done": {"a/1": 30}}}
        stale = {"id": "t", "rule": {"a": 1}, "module_trigger_state": {}}
        rules.keep_runtime_fields(stale, previous)
        self.assertEqual(stale["module_trigger_state"], {"done": {"a/1": 30}})


class ScenarioModuleStepTest(unittest.TestCase):
    def test_resolver_accepts_handler_names_and_manifest_ids(self) -> None:
        self.assertEqual(ma.resolve_action("music", "music.backup_playlists"), ("music.backup_playlists", {}))
        handler, settings = ma.resolve_action("characters", "save-chat-messages")
        self.assertEqual(handler, "characters.save_long_chats")
        self.assertEqual(settings.get("metric"), "messages")
        self.assertEqual(settings.get("threshold"), 30)
        self.assertEqual(ma.resolve_action("music", "characters.backup"), ("", {}))
        self.assertEqual(ma.resolve_action("nope", "x"), ("", {}))

    def test_scenario_module_step_runs_the_handler(self) -> None:
        from app import plan_scenarios as ps
        calls = []

        async def fake(ctx):
            calls.append((ctx.module, dict(ctx.settings)))
            return ma._result(ctx, True, "готово", files=["/tmp/x.md"])

        saved = dict(ma.HANDLERS["learn.due_report"])
        ma.HANDLERS["learn.due_report"]["fn"] = fake
        try:
            with tempfile.TemporaryDirectory() as tmp:
                res = run(ps.execute({"steps": [{"type": "module", "module": "learn", "action": "learn.due_report"},
                                                {"type": "module", "module": "learn", "action": "no-such"}]},
                                     base_dir=tmp))
        finally:
            ma.HANDLERS["learn.due_report"].update(saved)
        self.assertEqual(calls[0][0], "learn")
        self.assertFalse(res["ok"])
        self.assertIn("готово", res["text"])
        self.assertIn("no-such", json.dumps(res["log"], ensure_ascii=False))


class ApiCleanTest(unittest.TestCase):
    def test_declared_action_takes_the_handler_from_the_manifest(self) -> None:
        from app import schedule_rules_api as api
        task = api._clean_task({"id": "x", "name": "n", "kind": "module", "module_handler": "characters.backup",
                                "module_action": {"module": "characters", "action_id": "save-chat-messages"}})
        self.assertEqual(task["module_handler"], "characters.save_long_chats")
        self.assertEqual(task["module_options"].get("metric"), "messages")
        with self.assertRaises(rules.RuleError):
            api._clean_task({"id": "x", "name": "n", "kind": "module", "module_id": "music", "module_handler": "characters.backup"})


if __name__ == "__main__":
    unittest.main()
