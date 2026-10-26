"""Round 43 connected integrations: builder/NL/storage/handoff/flow regressions."""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import connected_services, flow_engine as fe, pgdata
from app import schedule_integrations as si, schedule_rules as rules, schedule_rules_api as api
from app import schedule_runtime as runtime

ROWS = [{"service": "telegram_use", "connected": False, "tools": [
    "telegram_use_history{chat,limit}", "telegram_use_send{chat,text}"]},
    {"service": "chrome_extension", "connected": False, "tools": [
        "chrome_tabs{}", "chrome_close{tab_id}"]}]

def task(**fields):
    return {"id": "integration-test", "name": "Read Telegram", "kind": "integration",
            "tool": "telegram_use_history", "args": {"chat": "123", "limit": 50},
            "rule": {"when": [{"type": "daily", "times": ["09:00"]}]}, **fields}

class Isolated(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir_patch = patch.object(pgdata, "MEMU_DIR", Path(self.tmp.name))
        self.dir_patch.start()
        self.addCleanup(self.dir_patch.stop)
        self.rows_patch = patch.object(connected_services, "status_rows", return_value=ROWS)
        self.rows_patch.start()
        self.addCleanup(self.rows_patch.stop)

class ValidationTests(Isolated):
    def test_catalog_is_available_even_when_disconnected(self):
        rows = si.catalog()
        self.assertEqual({row["name"] for row in rows}, {
            "telegram_use_history", "telegram_use_send", "chrome_tabs", "chrome_close"})
        self.assertFalse(any(row["connected"] for row in rows))
        history = next(row for row in rows if row["name"] == "telegram_use_history")
        self.assertEqual(history["parameters"]["properties"]["limit"]["type"], "integer")

    def test_only_real_integration_tools_and_json_objects_are_accepted(self):
        for name, args in (("shutdown", {}), ("chrome_unknown", {}), ("chrome_tabs", []),
                           ("telegram_use_history", {"x": float("nan")})):
            with self.subTest(name=name), self.assertRaises(rules.RuleError):
                si.validate(name, args)
        self.assertEqual(si.validate("chrome_tabs", {}), {"tool": "chrome_tabs", "args": {}})

    def test_nested_and_oversized_args_are_bounded(self):
        value = {}
        for _ in range(23):
            value = {"x": value}
        for args in (value, {"text": "x" * (si.MAX_ARGS + 1)}):
            with self.assertRaises(rules.RuleError):
                si.normalize("telegram_use_send", args)

    def test_builder_saves_integration_without_model_prompt_and_keeps_args(self):
        saved = api.save_task(task())
        stored = pgdata.load_schedules()[0]
        self.assertEqual(saved, stored["id"])
        self.assertEqual(stored["kind"], "integration")
        self.assertEqual(stored["args"], {"chat": "123", "limit": 50})
        self.assertEqual(stored["prompt"], "")
        preview = api.preview_payload(stored, [], "en")
        self.assertIn("MemuAI window", " ".join(preview["warnings"]))

    def test_bypassed_save_cannot_turn_unknown_tool_into_chat_or_execute_it(self):
        invalid = rules.sanitize_task(task(tool="shell", args={}))
        self.assertFalse(invalid["enabled"])
        self.assertEqual(invalid["rule_state"]["status"], "invalid")
        with self.assertRaises(rules.RuleError):
            api.save_task(task(tool="chrome_unknown"))
        with self.assertRaises(rules.RuleError):
            api.preview_payload(task(tool="chrome_unknown"), [], "en")

    def test_natural_language_draft_preserves_integration_action(self):
        draft = rules.draft_from_model(task(), "en")
        self.assertEqual(draft["tool"], "telegram_use_history")
        self.assertEqual(draft["args"]["limit"], 50)
        self.assertEqual(draft["kind"], "integration")
        self.assertIn('"type":"integration"', rules.CHAT_PROTOCOL)

    def test_direct_integration_can_convert_to_pipeline_without_losing_action(self):
        steps = fe.task_to_flow(task())
        self.assertEqual(steps[0]["type"], "integration")
        self.assertEqual(steps[0]["tool"], "telegram_use_history")
        self.assertEqual(steps[0]["args"]["limit"], 50)

    def test_catalog_routes_include_disconnected_tools(self):
        app = FastAPI()
        executor = api.register(app)
        app.state.plan_no_executor = True
        with TestClient(app) as client:
            metadata = client.get("/api/plan/meta").json()
            flows = client.get("/api/plan/flow/catalog").json()
            self.assertIn("integration", metadata["kinds"])
            self.assertEqual(len(metadata["integrations"]), 4)
            self.assertEqual(len(flows["integrations"]), 4)
            self.assertIsNone(executor.task)

class ExecutionTests(Isolated):
    def test_scheduler_sets_scheduled_flag_and_preserves_structured_output(self):
        async def run():
            mock = AsyncMock(return_value={"messages": [{"text": "hello"}]})
            with patch.object(connected_services, "run", mock):
                ok, text, extra = await si.run(task())
            mock.assert_awaited_once_with("telegram_use_history", {"chat": "123", "limit": 50}, scheduled=True)
            self.assertTrue(ok)
            self.assertEqual(json.loads(text), extra["integration_result"])
        asyncio.run(run())

    def test_disabled_permission_finishes_failed_without_asking_user(self):
        async def run():
            with patch.object(connected_services, "run", AsyncMock(side_effect=PermissionError("Scheduled access disabled"))):
                executor = api.Executor(SimpleNamespace(state=SimpleNamespace()))
                rows = [{"id": "handoff", "kind": "integration", "task": task(), "status": "running"}]
                with patch.object(runtime, "_handoff_mutate", side_effect=lambda fn: fn(rows)):
                    await executor.execute(rows[0])
            self.assertEqual(rows[0]["status"], "done")
            self.assertFalse(rows[0]["ok"])
            self.assertIn("Scheduled access disabled", rows[0]["text"])
        asyncio.run(run())

    def test_interactive_confirmation_result_is_an_error_not_a_wait(self):
        async def run():
            with patch.object(connected_services, "run", AsyncMock(return_value={"needs_confirmation": True})):
                ok, text, _extra = await si.run(task())
            self.assertFalse(ok)
            self.assertIn("not authorized", text)
        asyncio.run(run())

    def test_full_proxy_window_handoff_keeps_tool_arguments_and_result(self):
        async def run():
            executor = api.Executor(SimpleNamespace(state=SimpleNamespace()))
            mock = AsyncMock(return_value={"messages": [{"id": 1, "text": "hello"}]})
            with patch.object(connected_services, "run", mock):
                caller = asyncio.create_task(runtime._handoff(task()))
                for _ in range(100):
                    if runtime.handoff_items():
                        break
                    await asyncio.sleep(0.01)
                await executor.pump()
                await asyncio.gather(*list(executor.running.values()))
                ok, _text, extra = await asyncio.wait_for(caller, 5)
            self.assertTrue(ok)
            self.assertEqual(extra["integration_result"]["messages"][0]["text"], "hello")
            self.assertEqual(runtime.handoff_items(), [])
            self.assertEqual(mock.await_args.kwargs, {"scheduled": True})
        asyncio.run(run())

    def test_natural_language_parser_receives_exact_tool_catalog(self):
        async def run():
            completion = AsyncMock(return_value=json.dumps(task()))
            draft = await api.parse_text("Read last 50 Telegram messages every morning", "fake", completion,
                                         commands=[], lang="en")
            messages = completion.await_args.args[1]
            self.assertIn("telegram_use_history", messages[1]["content"])
            self.assertEqual(draft["args"]["limit"], 50)
        asyncio.run(run())

    def test_unattended_extension_dfy_never_waits_for_an_interactive_question(self):
        async def run():
            manager = SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(id="dfy-test", unattended=True)),
                wait_change=AsyncMock(return_value={"version": 1, "status": "waiting_user", "question": {"question": "Sign in first"}}),
                cancel=AsyncMock())
            executor = api.Executor(SimpleNamespace(state=SimpleNamespace(dfy_manager=manager)))
            with patch.object(runtime, "_handoff_mutate", return_value=True):
                ok, text, extra = await asyncio.wait_for(executor.run_dfy({"id": "handoff", "task": {
                    "name": "Check an order", "prompt": "Check my order status"}}), 1)
            self.assertFalse(ok)
            self.assertEqual(extra["dfy_status"], "needs_input")
            self.assertIn("Sign in first", text)
            manager.cancel.assert_awaited_once_with("dfy-test")
            self.assertNotIn("handoff", executor.dfy_ids)
        asyncio.run(run())

class FlowTests(Isolated):
    def test_read_summarize_send_pipeline_uses_structured_history_and_model_result(self):
        async def run():
            calls = []
            class Services(fe.Services):
                async def handoff(self, kind, payload):
                    self_kind = kind
                    calls.append((self_kind, payload))
                    if payload["tool"] == "telegram_use_history":
                        return True, "history", {"integration_result": {"messages": [{"text": "Meet at five"}]}}
                    return True, "sent", {"integration_result": {"message_id": 42}}

                def default_model(self):
                    return "fake"

                async def complete(self, _model, messages):
                    if "Meet at five" not in messages[-1]["content"]:
                        raise AssertionError("History did not reach model")
                    return "Meeting at 17:00."

                async def record_result(self, *_args):
                    pass

                async def deliver(self, *_args):
                    pass
            steps = fe.autolink(fe.normalize_steps([
                {"type": "integration", "tool": "telegram_use_history", "args": {"chat": "123", "limit": 50}},
                {"type": "model", "prompt": "Summarize the conversation"},
                {"type": "integration", "tool": "telegram_use_send", "args": {"chat": "me", "text": "{{step2.result}}"}}]))
            self.assertIn("{{step1.result}}", steps[1]["prompt"])
            self.assertEqual(fe.validate_flow(steps, "en"), [])
            definition = {"id": "pipeline", "name": "Daily summary", "steps": steps}
            result = await fe.execute_run(definition, fe.new_run(definition), Services(), record=False)
            self.assertTrue(result["ok"], result.get("error"))
            self.assertEqual(calls[-1][1]["args"], {"chat": "me", "text": "Meeting at 17:00."})
            self.assertTrue(all(kind == "integration" for kind, _payload in calls))
        asyncio.run(run())

    def test_dry_run_reads_but_does_not_send_or_close_tabs(self):
        async def run():
            handoff = AsyncMock(return_value=(True, "read", {"integration_result": []}))
            services = fe.Services()
            services.handoff = handoff
            definition = {"id": "dry", "steps": fe.normalize_steps([
                {"type": "integration", "tool": "chrome_tabs", "args": {}},
                {"type": "integration", "tool": "chrome_close", "args": {"tab_id": 5}},
                {"type": "integration", "tool": "telegram_use_send", "args": {"chat": "me", "text": "No send"}}])}
            with patch.object(connected_services, "write_tools", return_value={"chrome_close", "telegram_use_send"}):
                result = await fe.execute_run(definition, fe.new_run(definition, dry_run=True), services, record=False)
            self.assertTrue(result["ok"], result.get("error"))
            self.assertEqual(handoff.await_count, 1)
            self.assertEqual(handoff.await_args.args[1]["tool"], "chrome_tabs")
            self.assertEqual([row["status"] for row in result["log"]], ["ok", "simulated", "simulated"])
        asyncio.run(run())

    def test_raw_lists_in_arguments_remain_lists(self):
        step = fe.normalize_step({"type": "integration", "tool": "telegram_use_history", "args": {"ids": "{{vars.ids}}"}})
        context = fe.Ctx()
        context.vars["ids"] = [1, 2]
        self.assertEqual(context.render(step["args"])["ids"], [1, 2])

if __name__ == "__main__":
    unittest.main()
