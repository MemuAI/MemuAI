"""Round 13: agents leave the chat and become collaborative workers of Work and MemuCode.

Everything is offline: the model and the tools of an agent are small functions.
"""
from __future__ import annotations

import os
import tempfile

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

import asyncio  # noqa: E402
import json  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

from app import work_agents  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

def run_node(script: str) -> dict:
    done = subprocess.run([shutil.which("node"), str(ROOT / "tests" / script)],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    lines = [line for line in done.stdout.splitlines() if line.strip().startswith("{")]
    if not lines:
        raise AssertionError(done.stderr[-1500:] or "the runner printed nothing")
    out = json.loads(lines[-1])
    if "error" in out:
        raise AssertionError(out["error"])
    return out

def tool_block(*calls) -> str:
    return "Working.\n```memuai-tool\n" + json.dumps([{"tool": name, "args": args} for name, args in calls]) + "\n```"

class Scripted:
    """A model that answers from ``handler(model, messages)`` and remembers what it was asked."""

    def __init__(self, handler) -> None:
        self.handler = handler
        self.requests = []

    async def __call__(self, model, messages):
        self.requests.append((model, messages))
        return await self.handler(model, messages)

class WorkAgentsTest(unittest.TestCase):
    def pool(self, handler, tool=None):
        used = []

        async def run_tool(name, args, job):
            used.append((job.id, name, args))
            if tool is not None:
                return await tool(name, args, job)
            return {"tool": name, "text": "contents of " + str(args.get("path"))}

        model = Scripted(handler)
        return work_agents.AgentPool(model, run_tool), model, used

    def test_agents_run_at_the_same_time_and_spawning_returns_at_once(self) -> None:
        async def scenario():
            started, both = [], asyncio.Event()

            async def handler(model, messages):
                started.append(messages[1]["content"])
                if len(started) == 2:
                    both.set()
                await asyncio.wait_for(both.wait(), 5)
                return "report of " + ("docs" if "docs" in messages[1]["content"] else "tests")

            pool, _model, _used = self.pool(handler)
            common = dict(session="chat", model="main-model", access="full", code=False, web=False, project_id="p", limit=2)
            first = await pool.tool("agent_spawn", {"title": "Docs", "task": "write the docs"}, **common)
            second = await pool.tool("agent_spawn", {"title": "Tests", "task": "write the tests"}, **common)
            still_running = [row["status"] for row in pool.list("chat")]
            waited = await pool.tool("agent_wait", {"timeout": 5}, **common)
            if waited["still_running"]:
                waited["finished"] += (await pool.tool("agent_wait", {"timeout": 5}, **common))["finished"]
            return first, second, still_running, waited, pool

        first, second, still_running, waited, pool = asyncio.run(scenario())
        self.assertEqual((first["id"], first["status"], second["id"]), (1, "running", 2))
        self.assertIn("Keep doing your own part", first["note"])
        self.assertEqual(still_running, ["running", "running"])
        self.assertEqual(sorted(row["report"] for row in waited["finished"]), ["report of docs", "report of tests"])
        self.assertTrue(all(row["delivered"] for row in pool.list("chat")))

    def test_an_agent_uses_its_tools_and_its_last_message_is_the_report(self) -> None:
        async def scenario():
            async def handler(model, messages):
                if len(messages) == 2:
                    return tool_block(("work_read", {"path": "a.py"}), ("work_search", {"query": "TODO"}))
                return "Found two TODOs in a.py."

            pool, model, used = self.pool(handler)
            job = pool.spawn("chat", title="Scan", task="find the TODOs", model="worker", project_id="p")
            await job.handle
            return job, model, used

        job, model, used = asyncio.run(scenario())
        self.assertEqual((job.status, job.report), ("done", "Found two TODOs in a.py."))
        self.assertEqual([(name, args) for _id, name, args in used],
                         [("work_read", {"path": "a.py"}), ("work_search", {"query": "TODO"})])
        self.assertEqual([step["label"] for step in job.steps], ["work_read a.py", "work_search TODO"])
        system = model.requests[0][1][0]["content"]
        self.assertIn("You cannot talk to the user", system)
        self.assertIn("<assignment title=\"Scan\">", model.requests[0][1][1]["content"])
        self.assertIn("<tool_results>", model.requests[1][1][-1]["content"])
        self.assertIn("contents of a.py", model.requests[1][1][-1]["content"])
        self.assertEqual(model.requests[0][0], "worker")

    def test_without_full_access_an_agent_only_reads(self) -> None:
        async def scenario(access):
            async def handler(model, messages):
                if len(messages) == 2:
                    return tool_block(("work_patch", {"path": "a.py", "old_text": "x", "new_text": "y"}))
                return "done"

            pool, model, used = self.pool(handler)
            job = pool.spawn("chat", title="Edit", task="change a.py", model="m", access=access, code=True)
            await job.handle
            return job, model, used

        job, model, used = asyncio.run(scenario("ask"))
        self.assertEqual(used, [])
        self.assertIn("not available to an agent", model.requests[1][1][-1]["content"])
        self.assertIn("You may read and search only", model.requests[0][1][0]["content"])
        self.assertNotIn("work_patch {", model.requests[0][1][0]["content"])
        self.assertNotIn("code_run", job.tools())
        job, model, used = asyncio.run(scenario("full"))
        self.assertEqual([name for _id, name, _args in used], ["work_patch"])
        self.assertIn("code_run", job.tools())

    def test_an_agent_cannot_start_agents_or_drive_the_browser(self) -> None:
        async def scenario():
            async def handler(model, messages):
                if len(messages) == 2:
                    return tool_block(("agent_spawn", {"title": "x", "task": "y"}), ("memuai-browser", {"task": "z"}))
                return "could not delegate"

            pool, model, used = self.pool(handler)
            job = pool.spawn("chat", title="A", task="t", model="m", access="full", code=True, web=True)
            await job.handle
            return pool, job, used

        pool, job, used = asyncio.run(scenario())
        self.assertEqual(used, [])
        self.assertEqual(len(pool.list("chat")), 1)
        self.assertFalse(any(name.startswith("agent_") for name in job.tools()))

    def test_limits_a_task_is_required_and_errors_are_answers_for_the_model(self) -> None:
        async def scenario():
            gate = asyncio.Event()

            async def handler(model, messages):
                await gate.wait()
                return "ok"

            pool, _model, _used = self.pool(handler)
            common = dict(session="chat", model="m", access="ask", code=False, web=False, project_id="p", limit=2)
            empty = await pool.tool("agent_spawn", {"title": "x"}, **common)
            await pool.tool("agent_spawn", {"task": "one"}, **common)
            await pool.tool("agent_spawn", {"task": "two"}, **common)
            third = await pool.tool("agent_spawn", {"task": "three"}, **common)
            other_chat = await pool.tool("agent_spawn", {"task": "elsewhere"}, **{**common, "session": "another"})
            missing = await pool.tool("agent_result", {"id": 99}, **common)
            unknown = await pool.tool("agent_fly", {}, **common)
            gate.set()
            await asyncio.gather(*(job.handle for rows in pool.jobs.values() for job in rows))
            return empty, third, other_chat, missing, unknown, pool

        empty, third, other_chat, missing, unknown, pool = asyncio.run(scenario())
        self.assertIn("needs a task", empty["error"])
        self.assertIn("2 agents are already running", third["error"])
        self.assertEqual(other_chat["status"], "running")
        self.assertIn("No agent with that id", missing["error"])
        self.assertIn("Unknown agent tool", unknown["error"])
        self.assertEqual([row["title"] for row in pool.list("chat")], ["Agent 1", "Agent 2"])

    def test_a_failing_model_becomes_the_agents_error_and_the_others_carry_on(self) -> None:
        async def scenario():
            async def handler(model, messages):
                if "break" in messages[1]["content"]:
                    raise RuntimeError("HTTP 429: quota exhausted")
                return "fine"

            pool, _model, _used = self.pool(handler)
            bad = pool.spawn("chat", title="Bad", task="break", model="m")
            good = pool.spawn("chat", title="Good", task="work", model="m")
            await asyncio.gather(bad.handle, good.handle)
            return bad, good, pool.collect("chat")

        bad, good, collected = asyncio.run(scenario())
        self.assertEqual((bad.status, good.status), ("error", "done"))
        self.assertIn("quota exhausted", bad.error)
        self.assertEqual([row["status"] for row in collected], ["error", "done"])
        self.assertIn("quota exhausted", collected[0]["error"])

    def test_stopping_cancels_the_running_agents_and_never_wakes_the_conversation(self) -> None:
        async def scenario():
            async def handler(model, messages):
                await asyncio.sleep(30)
                return "never"

            pool, _model, _used = self.pool(handler)
            pool.spawn("chat", title="Slow", task="t", model="m")
            pool.spawn("chat", title="Slower", task="t", model="m")
            await asyncio.sleep(0.05)
            stopped = pool.cancel("chat")
            await asyncio.sleep(0.05)
            return stopped, pool.list("chat"), pool.collect("chat")

        stopped, rows, undelivered = asyncio.run(scenario())
        self.assertEqual(stopped, 2)
        self.assertEqual([row["status"] for row in rows], ["cancelled", "cancelled"])
        self.assertEqual(undelivered, [])

    def test_an_endless_tool_loop_ends_with_a_report_at_the_round_limit(self) -> None:
        async def scenario():
            async def handler(model, messages):
                if "tool limit is reached" in messages[-1]["content"]:
                    return "Stopped at the limit: half of the files were checked."
                return tool_block(("work_tree", {}))

            pool, model, used = self.pool(handler)
            job = pool.spawn("chat", title="Loop", task="t", model="m")
            await job.handle
            return job, used

        job, used = asyncio.run(scenario())
        self.assertEqual(len(used), work_agents.MAX_ROUNDS)
        self.assertEqual(job.status, "done")
        self.assertIn("Stopped at the limit", job.report)

    def test_the_file_tools_of_work_take_edits_one_at_a_time(self) -> None:
        from app import work_tools
        root = Path(tempfile.mkdtemp(prefix="memuai-agents-"))
        (root / "f.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

        async def scenario():
            first = asyncio.to_thread(work_tools.run, "work_patch", root,
                                      {"path": "f.txt", "old_text": "alpha", "new_text": "ALPHA"})
            second = asyncio.to_thread(work_tools.run, "work_patch", root,
                                       {"path": "f.txt", "old_text": "gamma", "new_text": "GAMMA"})
            await asyncio.gather(first, second)

        for _ in range(20):
            (root / "f.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
            asyncio.run(scenario())
            self.assertEqual((root / "f.txt").read_text(encoding="utf-8"), "ALPHA\nbeta\nGAMMA\n")

class AgentsInTheWindowTest(unittest.TestCase):
    """The real agents.js of the MemuAI window, run in Node."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.out = run_node("agents_runner.cjs")

    def test_agents_are_allowed_only_in_work_and_memucode_and_only_on_request(self) -> None:
        out = self.out
        self.assertFalse(out["chat"])
        self.assertFalse(out["chatSwitch"])
        self.assertTrue(out["workAsked"])
        self.assertTrue(out["codeAskedRu"])
        self.assertFalse(out["workPlain"])
        self.assertTrue(out["workSwitch"])
        self.assertTrue(out["followUp"])
        self.assertFalse(out["tooOld"])
        self.assertFalse(out["internalIgnored"])

    def test_the_prompt_describes_working_together_not_waiting(self) -> None:
        full, ask = self.out["promptFull"], self.out["promptAsk"]
        for text in ("agent_spawn {title, task}", "agent_wait", "KEEP WORKING on your own part", "the same file"):
            self.assertIn(text, full)
        self.assertIn("At most 3 agents run at once", full)
        self.assertIn("read and change the project", full)
        self.assertIn("agents can only read and search", ask)

    def test_running_agents_are_shown_live_and_nothing_interrupts_the_busy_model(self) -> None:
        out = self.out
        self.assertEqual(out["remembered"], ["Docs", "Tests"])
        self.assertEqual(out["liveLast"], ["work_read README.md", "work_search parse"])
        card = out["runningCard"]
        self.assertEqual((card["status"], card["meta"], card["open"], card["actions"]), ("running", "2 / 2", True, True))
        self.assertIn("work_read README.md", card["body"])
        self.assertEqual(out["deliveredWhileBusy"], 0)

    def test_a_report_nobody_waited_for_continues_the_conversation_once(self) -> None:
        out = self.out
        self.assertEqual(len(out["resumed"]), 1)
        resumed = out["resumed"][0]
        self.assertEqual(resumed["kind"], "agents")
        self.assertTrue(resumed["sameBubble"])
        self.assertIn("Docs written to README.md", resumed["report"])
        self.assertEqual(out["collectCalls"], 1)
        self.assertEqual(out["resumedTwice"], 1)

    def test_stop_cancels_the_agents_without_resuming_the_chat(self) -> None:
        out = self.out
        self.assertEqual(out["afterStop"], {"statuses": ["done", "cancelled"], "resumed": 1})
        self.assertEqual(out["doneCard"]["status"], "done")
        self.assertFalse(out["doneCard"]["actions"])
        self.assertIn("Docs written to README.md", out["doneCard"]["body"])
        self.assertEqual(out["noCard"], "")

class AgentsLeftTheChatTest(unittest.TestCase):
    def setUp(self) -> None:
        self.js = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        self.api = (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8")

    def test_nothing_runs_agents_before_a_chat_answer(self) -> None:
        generate = self.js[self.js.index("async function generate("):self.js.index("const PROVIDER_IMAGE_HOSTS")]
        for gone in ("agents_mode", "agentEligible", "specialist_reports", "delegation_plan", "raw.length > 280"):
            self.assertNotIn(gone, generate, gone)
        self.assertNotIn('"agents_mode"', (ROOT / "app" / "pgdata.py").read_text(encoding="utf-8").split("DEFAULT_PREFERENCES")[1][:6000])
        self.assertNotIn("agents_run", self.api)
        from app.pgdata import INTERNAL_PROMPTS
        ids = {row["id"] for row in INTERNAL_PROMPTS}
        self.assertFalse(ids & {"agent-router", "agent-specialist", "agent-synthesis"})

    def test_the_chat_refuses_agent_tools_and_project_modes_pass_the_request_flag(self) -> None:
        self.assertIn("/^(?:work_|code_|mcp_|agent_)/.test(name)", self.js)
        self.assertIn("agents_allowed: !!assistant._agentsAllowed", self.js)
        self.assertIn('if tool == "agent_spawn" and not body.get("agents_allowed")', self.api)
        self.assertIn('if mode not in ("work", "code")', self.api)

    def test_old_preferences_lose_the_auto_mode_and_keep_the_rest(self) -> None:
        from app.pgdata import _normalize_preferences
        prefs = _normalize_preferences({"agents_mode": "always", "agent_models": ["a", "b"], "agent_count": 9})
        self.assertNotIn("agents_mode", prefs)
        self.assertEqual(prefs["agent_models"], ["a", "b"])
        self.assertEqual(prefs["agent_count"], 4)

class ContextInTheWindowTest(unittest.TestCase):
    """context.js and the counter functions of app.js, run in Node on real image headers."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.out = run_node("context_runner.cjs")

    def test_text_is_counted_by_alphabet(self) -> None:

        (latin, latin_chars), (cyrillic, cyrillic_chars) = self.out["latin"], self.out["cyrillic"]
        self.assertGreater(cyrillic / cyrillic_chars, 1.3 * latin / latin_chars)
        self.assertGreater(self.out["claudeRussian"], cyrillic)
        self.assertEqual(self.out["empty"], 0)

    def test_an_image_costs_what_its_pixels_cost_not_what_its_file_weighs(self) -> None:
        out = self.out
        self.assertEqual(out["pngSize"], {"width": 1920, "height": 1080})
        self.assertEqual(out["jpegSize"], {"width": 800, "height": 600})
        self.assertEqual(out["exifSize"], {"width": 4000, "height": 3000})
        self.assertIsNone(out["notImage"])
        self.assertIsNone(out["remote"])
        self.assertEqual(out["sameBytes"], [1600, 50])
        self.assertEqual(out["byFamily"], {"claude": 1600, "openai": 1105, "gemini": 1548, "small": 258, "unknown": 1100})
        self.assertEqual(out["families"], ["claude", "claude", "gemini", "openai", "openai", "other"])

    def test_a_request_is_the_system_prompt_the_text_and_the_images(self) -> None:
        request = self.out["request"]
        self.assertEqual(request["imageCount"], 2)
        self.assertEqual(request["total"], request["system"] + request["text"] + request["images"])
        self.assertEqual(request["system"], 47)

    def test_video_is_counted_as_the_frames_that_are_really_sent(self) -> None:
        out = self.out
        self.assertEqual(out["video"]["imageCount"], 16)
        self.assertGreater(out["video"]["images"], 5000)
        self.assertGreater(out["video"]["text"], 100)
        self.assertEqual(out["videoAnalysed"]["images"], 0)
        self.assertEqual(out["described"]["images"], 0)
        self.assertEqual(out["blindModel"], 0)
        self.assertEqual(out["reasoningIgnored"], 47)

    def test_the_counter_includes_the_system_prompt_and_follows_compression(self) -> None:
        out = self.out
        self.assertEqual(out["chat"]["system"], 427)
        self.assertEqual(out["chat"]["imageCount"], 17)
        self.assertTrue(out["chat"]["sums"])
        self.assertTrue(out["withoutImages"])
        self.assertEqual(out["compressed"], {"smaller": True, "summary": 73, "count": 2, "firstImageGone": True})
        self.assertLess(out["staleReport"], 100)
        self.assertGreater(out["freshReport"], 4000)

    def test_the_providers_own_count_replaces_the_estimate(self) -> None:
        out = self.out
        self.assertEqual(out["measured"], {"flag": True, "total": 9005})
        self.assertEqual(out["measuredPlusNew"], 47)
        self.assertEqual(out["sentOnly"], {"flag": False, "total": 5052})
        self.assertFalse(out["otherModel"])
        self.assertFalse(out["movedMessage"])

    def test_the_window_records_the_request_size_and_the_tooltip_names_its_source(self) -> None:
        js = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "app" / "pgstatic" / "index.html").read_text(encoding="utf-8")
        self.assertLess(html.index("/context.js"), html.index("/app.js"))
        self.assertIn("MemuContext.payload(apiMessages, model)", js)
        self.assertIn("roundUsage.estimated === false", js)
        self.assertIn("посчитано провайдером за последний запрос", js)
        self.assertIn("Изображения и кадры видео", js)

class RealTokenCountsTest(unittest.TestCase):
    """The Proxy passes the provider's own token counts through instead of its estimate."""

    def chunks(self, events):
        from app.chat import ChatService

        class Provider:
            id = "custom"

            async def chat_stream(self, account, model, messages, params):
                for event in events:
                    yield event

        async def collect():
            service = ChatService.__new__(ChatService)
            out = []
            async for chunk in service._attempt(Provider(), type("A", (), {"label": "a", "id": "a"})(), "m", "custom-m",
                                                [{"role": "user", "content": "hello there"}], {}):
                out.append(chunk)
            return out

        return asyncio.run(collect())

    def test_a_provider_count_is_reported_and_marked_as_real(self) -> None:
        chunks = self.chunks([{"type": "text", "text": "Hi"}, {"type": "usage", "prompt_tokens": 4321, "completion_tokens": 77}])
        self.assertEqual(chunks[-1]["usage"], {"prompt_tokens": 4321, "completion_tokens": 77, "total_tokens": 4398,
                                               "estimated": False})

    def test_without_one_the_estimate_is_marked_as_an_estimate(self) -> None:
        usage = self.chunks([{"type": "text", "text": "Hi"}])[-1]["usage"]
        self.assertTrue(usage["estimated"])
        self.assertGreater(usage["prompt_tokens"], 0)

    def test_antigravity_reports_googles_count_with_the_thoughts_in_the_answer(self) -> None:
        from app.providers import antigravity

        class Stream:
            status_code = 200

            async def aiter_lines(self):
                payload = {"response": {"candidates": [{"content": {"parts": [{"text": "42"}]}}],
                                        "usageMetadata": {"promptTokenCount": 1200, "candidatesTokenCount": 3,
                                                          "thoughtsTokenCount": 40}}}
                for line in (("data: " + json.dumps(payload)).encode("utf-8"), b""):
                    yield line

        async def collect():
            out = []
            async for event in antigravity.AntigravityProvider()._iter_sse(Stream()):
                out.append(event)
            return out

        self.assertEqual(asyncio.run(collect()), [{"type": "text", "text": "42"},
                                              {"type": "usage", "prompt_tokens": 1200, "completion_tokens": 43, "reasoning_tokens": 40}])

    def test_memucode_uses_the_measured_request_size_and_adds_what_came_after(self) -> None:
        from tests.memucode_cli_test import make_runtime, setUpModule
        setUpModule()
        from app.cli.compact import context_breakdown
        runtime, _ = make_runtime()
        for text in ("first question " * 20, "an answer " * 20):
            runtime.session.append({"role": "user" if "question" in text else "assistant", "kind": "prompt", "content": text})
        estimate = context_breakdown(runtime)
        self.assertEqual(estimate["source"], "estimate")
        runtime.context_measured = {"input": 50_000, "output": 10, "model": runtime.model,
                                    "messages": len(runtime.session.api_messages())}
        measured = context_breakdown(runtime)
        self.assertEqual((measured["source"], measured["used"]), ("provider", 50_000))
        self.assertAlmostEqual(sum(measured["parts"].values()), 50_000, delta=5)
        from app.cli.tokens import estimate_text
        added = "Почему парсер падает на пустой строке? " * 25
        runtime.session.append({"role": "user", "kind": "prompt", "content": added})
        self.assertAlmostEqual(context_breakdown(runtime)["used"], 50_000 + estimate_text(added), delta=20)
        runtime.invalidate_prompt()
        self.assertEqual(context_breakdown(runtime)["source"], "estimate")

if __name__ == "__main__":
    unittest.main()
