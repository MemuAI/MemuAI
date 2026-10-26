"""Parallel work in MemuCode: several agents at once, background agents and commands, results that
reach the model, and edits that never overwrite each other. Offline: the model is a small script."""
from __future__ import annotations

import asyncio
import json
import re
import time
import unittest

from app.cli.agent import Agent
from app.cli.llm import Backend, StreamEvent, Usage
from app.cli.tools.base import ToolCall, ToolContext
from app.cli.tools.files import EditTool, ReadTool
from tests.memucode_cli_test import make_runtime, run, setUpModule, tearDownModule  # noqa: F401  (module fixtures)

class RuleBackend(Backend):
    """Answers from a function of the request, so concurrent agents may call it in any order."""

    name = "rules"
    native_tools = False

    def __init__(self, handler):
        self.handler = handler
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        text = await self.handler(request)
        yield StreamEvent("text", text=text)
        yield StreamEvent("usage", usage=Usage(input=100, output=10, estimated=False, requests=1))
        yield StreamEvent("done", finish="stop")

def last_text(request) -> str:
    content = request.messages[-1]["content"]
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)

def block(*calls) -> str:
    return "Working.\n```memuai-tool\n" + json.dumps([{"tool": name, "args": args} for name, args in calls]) + "\n```"

def is_worker(request) -> bool:
    return "Your role:" in request.system

class ParallelAgents(unittest.TestCase):
    def test_two_agents_in_one_round_really_run_at_the_same_time(self):
        started = []

        async def scenario():
            runtime, _ = make_runtime()
            both = asyncio.Event()

            async def handler(request):
                if is_worker(request):
                    tag = re.search(r"WORKER-(\d)", request.messages[0]["content"] if isinstance(
                        request.messages[0]["content"], str) else json.dumps(request.messages[0]["content"])).group(1)
                    started.append(tag)
                    if len(started) == 2:
                        both.set()
                    await asyncio.wait_for(both.wait(), 5)
                    return f"report {tag}"
                if "<tool_results" not in last_text(request):
                    return block(("Task", {"description": "one", "prompt": "WORKER-1"}),
                                 ("Task", {"description": "two", "prompt": "WORKER-2"}))
                return "both reports received"

            backend = RuleBackend(handler)
            runtime.router.resolve = lambda model: (backend, model)
            return await Agent(runtime).run_turn("do both with two agents in parallel"), backend

        result, backend = run(scenario())
        self.assertEqual(result.text, "both reports received")
        self.assertEqual(sorted(started), ["1", "2"])
        final = last_text(backend.requests[-1])
        self.assertIn("report 1", final)
        self.assertIn("report 2", final)

    def test_the_agents_model_from_settings_is_used_by_workers_only(self):
        async def scenario():
            runtime, _ = make_runtime(settings={"worker_model": "cheap-worker"})

            async def handler(request):
                if is_worker(request):
                    return "worker done"
                if "<tool_results" not in last_text(request):
                    return block(("Task", {"description": "x", "prompt": "do x"}))
                return "finished"

            backend = RuleBackend(handler)
            runtime.router.resolve = lambda model: (backend, model)
            await Agent(runtime).run_turn("go, use an agent")
            return backend

        backend = run(scenario())
        models = {("worker" if is_worker(request) else "main"): request.model for request in backend.requests}
        self.assertEqual(models["worker"], "cheap-worker")
        self.assertEqual(models["main"], "test-model")

    def test_a_background_agent_lets_the_main_agent_carry_on_and_its_report_arrives_later(self):
        async def scenario():
            runtime, _ = make_runtime()
            release = asyncio.Event()

            async def handler(request):
                if is_worker(request):
                    await asyncio.wait_for(release.wait(), 5)
                    return "the documentation is written"
                text = last_text(request)
                if "<tool_results" not in text and "[Background task" not in text:
                    return block(("Task", {"description": "docs", "prompt": "write the docs",
                                           "run_in_background": True}))
                if "Task #1 is running" in text:
                    release.set()
                    return block(("Bash", {"command": "python -c \"import time; time.sleep(0.5)\""}))
                return "all done"

            backend = RuleBackend(handler)
            runtime.router.resolve = lambda model: (backend, model)
            started = time.monotonic()
            result = await Agent(runtime).run_turn("build it and let an agent document it in the background")
            return runtime, backend, result, time.monotonic() - started

        runtime, backend, result, seconds = run(scenario())
        self.assertEqual(result.text, "all done")
        joined = "\n".join(last_text(request) for request in backend.requests if not is_worker(request))
        self.assertIn("[Background task #1 finished]", joined)
        self.assertIn("the documentation is written", joined)
        self.assertEqual(runtime.notes, [])

    def test_collecting_a_report_with_taskoutput_removes_the_pending_copy(self):
        async def scenario():
            runtime, _ = make_runtime()
            release = asyncio.Event()

            async def handler(request):
                if is_worker(request):
                    await asyncio.wait_for(release.wait(), 5)
                    return "report body"
                text = last_text(request)
                if "<tool_results" not in text:
                    return block(("Task", {"description": "x", "prompt": "do x", "run_in_background": True}))
                if "running in the background" in text:
                    release.set()
                    return block(("TaskOutput", {"task_id": 1, "wait_seconds": 5}))
                return "used the report"

            backend = RuleBackend(handler)
            runtime.router.resolve = lambda model: (backend, model)
            result = await Agent(runtime).run_turn("go, delegate it to an agent")
            return runtime, result

        runtime, result = run(scenario())
        self.assertEqual(result.text, "used the report")
        self.assertEqual(runtime.notes, [])

    def test_only_the_models_own_jobs_wake_it_up(self):
        async def scenario():
            runtime, _ = make_runtime()

            async def handler(request):
                return "worker report"

            backend = RuleBackend(handler)
            runtime.router.resolve = lambda model: (backend, model)
            mine = runtime.jobs.start("by the model", origin="model")
            theirs = runtime.jobs.start("by the user", origin="user")
            await asyncio.gather(mine["task"], theirs["task"])
            return runtime

        runtime = run(scenario())
        self.assertEqual(len(runtime.notes), 2)
        self.assertEqual(sorted(note["wake"] for note in runtime.notes), [False, True])
        self.assertTrue(runtime.wake_requested())
        self.assertEqual(len(runtime.drain_notes()), 2)
        self.assertFalse(runtime.wake_requested())

class AgentsOnlyOnRequest(unittest.TestCase):
    """Round 13: a sub-agent starts only when the user asked for one."""

    def scenario(self, prompt, settings=None):
        async def run_it():
            runtime, _ = make_runtime(settings=settings)

            async def handler(request):
                if is_worker(request):
                    return "worker report"
                text = last_text(request)
                if "<tool_results" not in text:
                    return block(("Task", {"description": "x", "prompt": "do x"}))
                return "REFUSED" if "only when the user asks" in text else "DELEGATED"

            backend = RuleBackend(handler)
            runtime.router.resolve = lambda model: (backend, model)
            result = await Agent(runtime).run_turn(prompt)
            return result.text, sum(1 for request in backend.requests if is_worker(request)), backend.requests[0].system

        return run(run_it())

    def test_an_agent_the_user_did_not_ask_for_is_refused_and_the_model_is_told_why(self):
        text, workers, system = self.scenario("fix the failing test in parser.py")
        self.assertEqual(text, "REFUSED")
        self.assertEqual(workers, 0)
        self.assertIn("run ONLY when the user asks for them", system)

    def test_asking_in_either_language_allows_it(self):
        for prompt in ("refactor this, use sub-agents", "do the docs and the tests in parallel",
                       "сделай это с помощью агентов", "запусти проверку в фоне, а сам чини баг",
                       "делегируй документацию"):
            text, workers, _system = self.scenario(prompt)
            self.assertEqual((text, workers), ("DELEGATED", 1), prompt)

    def test_a_follow_up_to_such_a_request_still_counts_and_an_old_one_does_not(self):
        from app.cli.prompts import user_asked_for_agents

        async def run_it():
            runtime, _ = make_runtime()
            session = runtime.session
            session.append({"role": "user", "kind": "prompt", "content": "split this between agents"})
            session.append({"role": "user", "kind": "prompt", "content": "continue"})
            recent = user_asked_for_agents(runtime)
            for text in ("now rename the function", "and add a test", "thanks, also update the readme"):
                session.append({"role": "user", "kind": "prompt", "content": text})
            return recent, user_asked_for_agents(runtime)

        self.assertEqual(run(run_it()), (True, False))

    def test_the_setting_can_hand_the_decision_back_to_the_model_or_switch_agents_off(self):
        text, workers, system = self.scenario("fix the failing test", {"subagents": "auto"})
        self.assertEqual((text, workers), ("DELEGATED", 1))
        self.assertIn("Use Task only when delegation helps", system)
        text, workers, system = self.scenario("use agents for this", {"subagents": "off"})
        self.assertEqual(workers, 0)
        self.assertIn("Sub-agents are switched off", system)
        self.assertNotIn("# Sub-agents (Task tool)", system)

class BackgroundCommands(unittest.TestCase):
    def test_a_finished_background_command_is_announced_once_with_its_output(self):
        async def scenario():
            runtime, _ = make_runtime()
            row = runtime.shells.start(runtime, "python -c \"print('built ok')\"", runtime.root, "build")
            await asyncio.to_thread(row["output_done"].wait, 10)
            first = runtime.poll_background()
            second = runtime.poll_background()
            return runtime, first, second

        runtime, first, second = run(scenario())
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        self.assertTrue(runtime.wake_requested())
        text = runtime.drain_notes()[0]
        self.assertIn("finished with exit code 0", text)
        self.assertIn("built ok", text)

    def test_reading_the_result_yourself_suppresses_the_reminder(self):
        async def scenario():
            runtime, _ = make_runtime()
            row = runtime.shells.start(runtime, "python -c \"print('x')\"", runtime.root, "")
            await runtime.shells.wait(row["id"], 10)
            runtime.shells.read(row["id"])
            return runtime, runtime.poll_background()

        runtime, lines = run(scenario())
        self.assertEqual(lines, [])
        self.assertFalse(runtime.notes)

class ConcurrentEdits(unittest.TestCase):
    def test_edits_of_one_file_from_two_agents_are_both_kept(self):
        async def scenario():
            runtime, _ = make_runtime({"f.txt": "alpha\nbeta\ngamma\n"})
            agents = []
            for _ in range(2):
                agent = Agent(runtime)
                agents.append(agent)
            path = runtime.root / "f.txt"

            def context(agent):
                call = ToolCall(name="Edit", args={})
                return ToolContext(runtime=runtime, call=call, turn="t")

            reader, editor = ReadTool(), EditTool()
            ctx = context(agents[0])
            await reader.run(ctx, {"file_path": "f.txt"}, await reader.prepare(ctx, {"file_path": "f.txt"}))
            first_args = {"file_path": "f.txt", "old_string": "alpha", "new_string": "ALPHA"}
            second_args = {"file_path": "f.txt", "old_string": "gamma", "new_string": "GAMMA"}
            first = await editor.prepare(ctx, first_args)
            second = await editor.prepare(ctx, second_args)
            await asyncio.gather(editor.run(ctx, first_args, first), editor.run(ctx, second_args, second))
            return path.read_text(encoding="utf-8")

        self.assertEqual(run(scenario()), "ALPHA\nbeta\nGAMMA\n")

    def test_an_edit_whose_text_disappeared_meanwhile_fails_loudly(self):
        async def scenario():
            runtime, _ = make_runtime({"f.txt": "one\ntwo\n"})
            ctx = ToolContext(runtime=runtime, call=ToolCall(name="Edit", args={}), turn="t")
            reader, editor = ReadTool(), EditTool()
            await reader.run(ctx, {"file_path": "f.txt"}, await reader.prepare(ctx, {"file_path": "f.txt"}))
            args = {"file_path": "f.txt", "old_string": "two", "new_string": "TWO"}
            prepared = await editor.prepare(ctx, args)
            (runtime.root / "f.txt").write_text("one\nsomething else\n", encoding="utf-8")
            with self.assertRaises(Exception) as caught:
                await editor.run(ctx, args, prepared)
            return str(caught.exception), (runtime.root / "f.txt").read_text(encoding="utf-8")

        message, text = run(scenario())
        self.assertIn("changed while this edit was waiting", message)
        self.assertEqual(text, "one\nsomething else\n")

if __name__ == "__main__":
    unittest.main()
