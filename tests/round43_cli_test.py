"""Round 43: offline MemuCode context, file-cache and transport regressions."""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.cli import compact, convert, tokens
from app.cli.agent import Agent
from app.cli.hooks import HookOutcome
from app.cli.llm import OpenAIBackend
from app.cli.tools import files
from app.cli.tools.base import ToolCall, ToolContext, ToolResult
from tests.memucode_cli_test import make_runtime, run, setUpModule, tearDownModule  # noqa: F401

class ContextRecovery(unittest.TestCase):
    def test_elision_allows_rereading_the_result_in_the_same_turn(self):
        runtime, _ = make_runtime({"a.py": "print(1)"})
        agent = Agent(runtime)
        guard = {"seen": {}, "epoch": 0}
        call = ToolCall(name="Read", args={"file_path": "a.py"})
        agent._note_executed([(call, ToolResult(ok=True, output="old"))], guard, 1)
        self.assertTrue(agent._mark_duplicates([{"name": "Read", "args": call.args}], guard))
        runtime.session.context_epoch += 1
        reread = {"name": "Read", "args": call.args}
        self.assertFalse(agent._mark_duplicates([reread], guard))
        self.assertNotIn("duplicate_of", reread)

    def test_failed_read_does_not_poison_duplicate_guard(self):
        runtime, _ = make_runtime()
        agent = Agent(runtime)
        guard = {"seen": {}, "epoch": 0}
        call = ToolCall(name="Read", args={"file_path": "generated.py"})
        agent._note_executed([(call, ToolResult.fail("not found"))], guard, 1)
        self.assertFalse(agent._mark_duplicates([{"name": "Read", "args": call.args}], guard))

    def test_repeated_failed_reads_still_have_a_bound_when_stuck_detector_is_off(self):
        runtime, _ = make_runtime()
        agent = Agent(runtime)
        guard = {"seen": {}, "epoch": 0}
        call = ToolCall(name="Read", args={"file_path": "generated.py"})
        for round_no in (1, 2):
            agent._note_executed([(call, ToolResult.fail("not found"))], guard, round_no)
        self.assertTrue(agent._mark_duplicates([{"name": "Read", "args": call.args}], guard))

    def test_context_measurement_is_invalidated_by_elision(self):
        runtime, _ = make_runtime()
        runtime.session.append({"role": "user", "kind": "prompt", "content": "hello"})
        runtime.context_measured = {"model": runtime.model, "input": 90_000, "messages": 1,
                                    "epoch": runtime.session.context_epoch}
        self.assertEqual(compact.context_breakdown(runtime)["source"], "provider")
        runtime.session.context_epoch += 1
        self.assertEqual(compact.context_breakdown(runtime)["source"], "estimate")

    def test_images_returned_by_tools_are_in_the_context_budget(self):
        message = {"role": "tool", "content": "picture", "images": [{"width": 1024, "height": 1024}]}
        self.assertGreater(tokens.estimate_message(message), 1000)
        self.assertLess(tokens.estimate_message({**message, "elided": True}), 50)

    def test_native_tool_arguments_are_counted_without_a_provider_usage(self):
        short = {"role": "assistant", "tool_calls": [{"function": {"name": "Write", "arguments": "{}"}}]}
        long = {"role": "assistant", "tool_calls": [{"function": {"name": "Write",
                                "arguments": json.dumps({"content": "some different words\n" * 300})}}]}
        self.assertGreater(tokens.estimate_message(long), tokens.estimate_message(short) + 500)

class FileReadCache(unittest.TestCase):
    def test_unchanged_read_avoids_reading_and_decoding_the_file_again(self):
        runtime, _ = make_runtime({"a.py": "print(1)\nprint(2)\n"})
        tool = files.ReadTool()
        ctx = ToolContext(runtime=runtime, call=ToolCall(name="Read", args={}), turn="test")
        async def scenario():
            args = {"file_path": "a.py"}
            prepared = await tool.prepare(ctx, args)
            first = await tool.run(ctx, args, prepared)
            with patch.object(files, "read_text_meta", side_effect=AssertionError("redundant disk read")):
                second = await tool.run(ctx, args, prepared)
            runtime.session.context_epoch += 1
            third = await tool.run(ctx, args, prepared)
            return first, second, third
        first, second, third = run(scenario())
        self.assertIn("print(1)", first.output)
        self.assertTrue(second.meta.get("unchanged"))
        self.assertIn("print(1)", third.output)

    def test_file_changed_while_reading_is_not_marked_fresh_for_edit(self):
        runtime, _ = make_runtime({"a.py": "before\n"})
        path = runtime.root / "a.py"
        tool = files.ReadTool()
        ctx = ToolContext(runtime=runtime, call=ToolCall(name="Read", args={}), turn="test")
        original = files.read_text_meta
        def racing_read(*args, **kwargs):
            value = original(*args, **kwargs)
            path.write_text("after changed externally\n", encoding="utf-8")
            return value
        async def scenario():
            prepared = await tool.prepare(ctx, {"file_path": "a.py"})
            with patch.object(files, "read_text_meta", side_effect=racing_read):
                await tool.run(ctx, {"file_path": "a.py"}, prepared)
        run(scenario())
        with self.assertRaises(files.ToolError):
            files._check_fresh(ctx, path)

class ConversionBudget(unittest.TestCase):
    def test_native_parallel_results_share_the_same_budget_as_text_protocol(self):
        messages = [{"role": "assistant", "tool_calls": [
            {"id": str(i), "name": "Read", "args": {}} for i in range(8)]}]
        messages += [{"role": "tool", "tool_call_id": str(i), "name": "Read", "content":
                      f"start {i}\n" + "some code line\n" * 6000 + f"\nend {i}"} for i in range(8)]
        results = [m for m in convert.to_native(messages) if m["role"] == "tool"]
        self.assertEqual(len(results), 8)
        self.assertLessEqual(sum(len(m["content"]) for m in results), convert.ROUND_BUDGET)
        for i, result in enumerate(results):
            self.assertTrue(result["content"].startswith(f"start {i}"))
            self.assertTrue(result["content"].endswith(f"end {i}"))

    def test_text_protocol_reports_a_missing_parallel_result_after_crash(self):
        messages = [{"role": "assistant", "tool_calls": [
            {"id": "one", "name": "Read", "args": {}}, {"id": "two", "name": "Write", "args": {}}]},
            {"role": "tool", "tool_call_id": "one", "name": "Read", "content": "read completed"},
            {"role": "user", "content": "continue"}]
        converted = convert.to_text_protocol(messages)
        results = converted[1]["content"]
        self.assertIn("read completed", results)
        self.assertIn('id="two" ok="false"', results)
        self.assertIn(convert.MISSING_RESULT, results)

    def test_split_budget_never_exceeds_even_a_tiny_budget(self):
        for budget in (0, 1, 20, 100, 500):
            with self.subTest(budget=budget):
                parts = tokens.split_budget(["a" * 900, "b" * 900, "c" * 900], budget)
                self.assertLessEqual(sum(map(len, parts)), budget)

class Compaction(unittest.TestCase):
    def make(self):
        runtime, _ = make_runtime()
        for i in range(10):
            runtime.session.append({"role": "user", "kind": "prompt", "content": f"request {i}"})
            runtime.session.append({"role": "assistant", "content": "Длинный ответ. " * 200})
        return runtime

    def test_precompact_block_does_not_call_model_or_change_context(self):
        runtime = self.make()
        before = list(runtime.session.api_messages())
        runtime.hooks.run = AsyncMock(return_value=HookOutcome(decision="block", reason="wait"))
        runtime.quick_answer = AsyncMock(return_value="summary")
        result = run(compact.compact(runtime))
        self.assertFalse(result["ok"])
        runtime.quick_answer.assert_not_awaited()
        self.assertEqual(runtime.session.api_messages(), before)

    def test_small_window_summary_request_and_output_fit_together(self):
        runtime = self.make()
        runtime.effective_window = lambda: 8000
        runtime.quick_answer = AsyncMock(return_value="useful summary")
        self.assertTrue(run(compact.compact(runtime))["ok"])
        call = runtime.quick_answer.call_args
        total = tokens.estimate_text(call.args[0]) + tokens.estimate_text(call.args[1]) + call.kwargs["max_tokens"]
        self.assertLess(total, 8000)

    def test_empty_summary_never_hides_the_history(self):
        runtime = self.make()
        before = runtime.session.api_messages()
        runtime.quick_answer = AsyncMock(return_value="  ")
        self.assertFalse(run(compact.compact(runtime))["ok"])
        self.assertEqual(runtime.session.api_messages(), before)

class HeadlessLifecycle(unittest.TestCase):
    def test_round_limit_final_answer_is_in_the_turn_usage(self):
        runtime, _ = make_runtime({"a.py": "print(1)"}, native=True, settings={"max_turns": 1}, turns=[
            {"calls": [{"id": "read", "name": "Read", "args": {"file_path": "a.py"}}]}, "partial summary"])
        runtime.ui.interactive = False
        result = run(Agent(runtime).run_turn("inspect the file"))
        self.assertEqual(result.stopped, "limit")
        self.assertEqual(result.usage.requests, 2)
        self.assertEqual(result.usage.input, 2000)

    def test_shutdown_cancels_mcp_startup_before_stopping_servers(self):
        runtime, _ = make_runtime()
        async def wait_forever():
            await asyncio.Event().wait()
        runtime._start_mcp = wait_forever
        runtime.mcp.stop_all = AsyncMock()
        async def scenario():
            await runtime.startup(load_models=False)
            await asyncio.sleep(0)
            task = runtime.mcp_task
            try:
                await runtime.shutdown()
                return task.done()
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(run(scenario()))

    def test_limit_is_reported_as_incomplete_with_nonzero_exit_status(self):
        from app.cli import headless
        from app.cli.agent import TurnResult
        runtime, _ = make_runtime()
        runtime.startup = AsyncMock()
        runtime.shutdown = AsyncMock()
        runtime.goal_followup = AsyncMock(return_value="")
        with patch.object(Agent, "run_turn", new=AsyncMock(return_value=TurnResult(stopped="limit", text="partial"))):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = run(headless.run(runtime, "task", fmt="json"))
        self.assertEqual(code, 1)
        self.assertTrue(json.loads(output.getvalue())["is_error"])
        runtime.shutdown.assert_awaited_once()

    def test_startup_exception_still_closes_background_resources(self):
        from app.cli import headless
        runtime, _ = make_runtime()
        runtime.startup = AsyncMock(side_effect=RuntimeError("startup interrupted"))
        runtime.shutdown = AsyncMock()
        with self.assertRaisesRegex(RuntimeError, "startup interrupted"):
            run(headless.run(runtime, "task", fmt="json"))
        runtime.shutdown.assert_awaited_once()

class DirectModels(unittest.TestCase):
    def test_non_json_models_response_preserves_configured_models(self):
        backend = OpenAIBackend({"name": "local", "base_url": "http://test.invalid/v1", "models": ["mine"]})
        backend._client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, text="<html>reverse proxy</html>")))
        async def scenario():
            try:
                return await backend.list_models()
            finally:
                await backend.close()
        self.assertEqual([m.raw for m in run(scenario())], ["mine"])

if __name__ == "__main__":
    unittest.main()
