"""Long-task stability (1/2): long chats/context and long MemuCode runs.

Simulations, not live models: a history of thousands of messages, a
compaction whose model call fails and is resumed, a tool that hangs past
its timeout, and a cancelled tool call. No network, no real data: the
data dir is a temporary one, set before ``app`` is imported.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-long-tasks-")

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.cli import compact
from app.cli.session import Session
from app.cli.tokens import estimate_message, estimate_messages, estimate_text, fit_messages
from app import memory_v2
from app.code_context import CodeContextEngine
from app.work_context import build_work_context


def _messages(count: int) -> list:
    rows = [{"role": "system", "content": "You are MemuCode. Follow the instructions."}]
    for index in range(count):
        role = "user" if index % 2 == 0 else "assistant"
        rows.append({"role": role, "content": f"message number {index} " + "x" * 60,
                     "kind": "prompt" if role == "user" else None})
    return rows


class FitMessagesTest(unittest.TestCase):
    def test_thousands_of_messages_fit_the_budget_with_system_kept(self) -> None:
        rows = _messages(3000)
        fitted = fit_messages(rows, 2000, system_tokens=100, reserve_tokens=100)
        self.assertLessEqual(fitted["kept_tokens"], 1800)
        self.assertGreater(fitted["dropped"], 0)
        self.assertEqual(fitted["messages"][0]["role"], "system")
        self.assertIn("message number 2999", fitted["messages"][-1]["content"])
        ids = [id(row) for row in fitted["messages"]]
        self.assertEqual(ids, sorted(ids, key=lambda value: [id(r) for r in rows].index(value)))

    def test_healthy_history_comes_back_unchanged(self) -> None:
        rows = _messages(5)
        fitted = fit_messages(rows, 100_000)
        self.assertEqual(fitted["messages"], rows)
        self.assertEqual(fitted["dropped"], 0)
        self.assertFalse(fitted["truncated"])

    def test_system_alone_overflow_is_reported_not_silently_dropped(self) -> None:
        rows = [{"role": "system", "content": "S" * 5000},
                {"role": "user", "content": "hello"}]
        fitted = fit_messages(rows, 10)
        self.assertEqual([row["role"] for row in fitted["messages"]], ["system"])
        self.assertTrue(fitted["truncated"])
        self.assertEqual(fitted["dropped"], 1)


class RenderTranscriptTest(unittest.TestCase):
    def test_long_history_renders_bounded_with_head_and_tail(self) -> None:
        rows = _messages(2000)
        text = compact.render_transcript(rows, limit=20_000)
        self.assertLessEqual(len(text), 20_000 + 13_000)
        self.assertIn("message number 0", text)
        self.assertIn("message number 1999", text)
        self.assertIn("middle of the transcript omitted", text)
        self.assertNotIn("message number 1000", text)

    def test_short_history_renders_whole(self) -> None:
        rows = _messages(3)
        text = compact.render_transcript(rows, limit=180_000)
        self.assertNotIn("omitted", text)
        self.assertIn("message number 2", text)

    def test_transcript_rows_streams(self) -> None:
        rows = _messages(100)[1:]
        streamed = list(compact.transcript_rows(rows))
        self.assertEqual(len(streamed), len(rows))


class _Hooks:
    async def run(self, *args, **kwargs):
        return types.SimpleNamespace(decision="", stop=False, stop_reason="",
                                     reason="", context=[])


class _Runtime:
    def __init__(self, session, quick):
        self.session = session
        self.settings = {"keep_recent_messages": 8, "auto_compact": True,
                         "auto_compact_threshold": 85}
        self.hooks = _Hooks()
        self._quick = quick
        self.model = "test-model"
        self.stats = types.SimpleNamespace(compactions=0)
        self.invalidated = 0

    def effective_window(self):
        return 20_000

    async def quick_answer(self, system, user, model="", max_tokens=0):
        return await self._quick(system, user)

    def invalidate_prompt(self):
        self.invalidated += 1


def _long_session(root: Path, rounds: int = 14) -> Session:
    session = Session(root)
    for index in range(rounds):
        session.append({"role": "user", "kind": "prompt",
                        "content": f"user request {index} " + "please continue the work " * 8})
        session.append({"role": "assistant", "content": f"assistant answer {index} " + "done " * 20})
    return session


class CompactCheckpointTest(unittest.IsolatedAsyncioTestCase):
    async def test_failed_compaction_leaves_checkpoint_and_resumes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "proj"
            root.mkdir()
            session = _long_session(root)
            calls: list = []

            async def flaky(system, user):
                calls.append(user)
                if len(calls) == 1:
                    raise RuntimeError("model boom")
                return "SUMMARY: work in progress, files a.py and b.py changed."

            runtime = _Runtime(session, flaky)
            with self.assertRaises(RuntimeError):
                await compact.compact(runtime, reason="manual")
            checkpoint = session.compaction_checkpoint
            self.assertIsNotNone(checkpoint)
            self.assertTrue(checkpoint["covers_until"])
            reloaded = Session.load(root, session.id)
            self.assertEqual(reloaded.compaction_checkpoint, checkpoint)

            result = await compact.compact(runtime, reason="manual")
            self.assertTrue(result["ok"])
            summaries = [m for m in session.messages if m.get("kind") == "summary"]
            self.assertEqual(len(summaries), 1)
            self.assertIsNone(session.compaction_checkpoint)
            self.assertEqual(len(calls), 2)

    async def test_summary_already_appended_is_not_duplicated_on_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "proj"
            root.mkdir()
            session = _long_session(root)
            calls: list = []

            async def quick(system, user):
                calls.append(user)
                return "SUMMARY: done part one."

            runtime = _Runtime(session, quick)
            first = await compact.compact(runtime, reason="manual")
            self.assertTrue(first["ok"])
            summary = next(m for m in session.messages if m.get("kind") == "summary")
            # Crash between «summary appended» and «checkpoint cleared»:
            # the checkpoint is still there, the summary is already in the file.
            session.set_compaction_checkpoint({"covers_until": summary["covers_until"],
                                               "summarized": 1, "reason": "manual"})
            result = await compact.compact(runtime, reason="manual")
            self.assertTrue(result.get("resumed"))
            self.assertEqual(len(calls), 1)
            summaries = [m for m in session.messages if m.get("kind") == "summary"]
            self.assertEqual(len(summaries), 1)

    async def test_two_compactions_do_not_run_at_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "proj"
            root.mkdir()
            session = _long_session(root)
            started = asyncio.Event()

            async def slow(system, user):
                started.set()
                await asyncio.sleep(5)
                return "SUMMARY"

            runtime = _Runtime(session, slow)
            task = asyncio.create_task(compact.compact(runtime, reason="manual"))
            await started.wait()
            second = await compact.compact(runtime, reason="manual")
            self.assertFalse(second["ok"])
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task


class MemoryRankHardCapTest(unittest.TestCase):
    def _doc(self, budget: int, texts: list, pinned: bool = True):
        items = []
        for index, text in enumerate(texts):
            items.append({"id": f"f{index}", "text": text, "status": "active", "scope": [],
                          "category": "fact", "confidence": 0.9, "pinned": pinned,
                          "updated_at": 1_700_000_000_000, "last_used_at": 1_700_000_000_000,
                          "uses": 0, "tags": []})
        return {"items": items, "settings": {"budget_tokens": budget, "core": True},
                "profile_id": ""}

    def test_pinned_overflow_is_truncated_to_the_hard_budget(self) -> None:
        texts = ["fact number %d " % i + "word " * 200 for i in range(3)]
        doc = self._doc(100, texts)
        chosen = memory_v2.rank(doc, "chat", "", budget_tokens=100)
        total = sum(memory_v2.estimate_tokens(item["text"]) for item in chosen)
        self.assertLessEqual(total, 100)
        self.assertGreaterEqual(len(chosen), 1)
        self.assertTrue(any(item.get("truncated") for item in chosen))
        for item in doc["items"]:
            self.assertGreater(len(item["text"]), 500)

    def test_healthy_rank_is_unchanged(self) -> None:
        doc = self._doc(4000, ["likes tea", "works in Berlin", "prefers short answers"])
        chosen = memory_v2.rank(doc, "chat", "", budget_tokens=4000)
        self.assertEqual(len(chosen), 3)
        self.assertFalse(any(item.get("truncated") for item in chosen))


class WorkContextBudgetTest(unittest.TestCase):
    def test_chars_never_exceed_the_budget_and_drops_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index in range(30):
                (root / f"mod{index}.py").write_text(
                    "def needle_function_%d():\n    return %d\n" % (index, index) * 40, encoding="utf-8")
            result = build_work_context(root, "needle_function", max_chars=1000)
            self.assertLessEqual(result["chars"], 1000)
            self.assertEqual(result["budget"]["used_chars"], result["chars"])
            self.assertEqual(result["budget"]["max_chars"], 1000)
            self.assertGreaterEqual(result["budget"]["candidates"], len(result["files"]))
            self.assertEqual(result["budget"]["dropped"],
                             result["budget"]["candidates"] - len(result["files"]))


class CodeContextBudgetTest(unittest.TestCase):
    def test_selective_context_reports_its_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index in range(6):
                (root / f"needle_{index}.py").write_text(
                    "# needle module\n" + "x = %d\n" % index * 200, encoding="utf-8")
            engine = CodeContextEngine(root)
            engine.refresh()
            result = engine.selective_context("needle", max_chars=2000)
            self.assertLessEqual(result["budget"]["used_chars"], 2000)
            self.assertEqual(result["budget"]["max_chars"], 2000)
            self.assertIn("chars", result)


class RetryDelayTest(unittest.TestCase):
    def test_backoff_doubles_and_caps(self) -> None:
        from app.cli.agent import retry_delay
        self.assertEqual(retry_delay(1), 2.0)
        self.assertEqual(retry_delay(2), 4.0)
        self.assertEqual(retry_delay(3), 8.0)
        self.assertEqual(retry_delay(10), 30.0)
        self.assertEqual(retry_delay(1, jitter=0.5), 3.0)


class _FakeUI:
    def tool_started(self, *args, **kwargs):
        pass

    def tool_finished(self, *args, **kwargs):
        pass

    def tool_progress(self, *args, **kwargs):
        pass


class _FakeTool:
    name = "slow_tool"
    parallel = False

    def __init__(self, delay: float):
        self.delay = delay

    def display(self, args):
        return "slow"

    def enabled(self, runtime):
        return True

    def check_required(self, args):
        pass

    async def prepare(self, ctx, args):
        return None

    async def run(self, ctx, args, prepared):
        await asyncio.sleep(self.delay)
        from app.cli.tools.base import ToolResult
        return ToolResult(ok=True, output="done")


class _FakeRuntime:
    def __init__(self, tool, settings):
        from app.cli.tools.base import ToolResult  # noqa: F401
        self.tools = types.SimpleNamespace(resolve=lambda name: tool)
        self.settings = settings
        self.model = "test-model"
        self.stats = types.SimpleNamespace(tool_calls=0, tool_errors=0, lines_added=0,
                                           lines_removed=0)
        self._hooks_outcome = types.SimpleNamespace(decision="allow", messages=[],
                                                    context=[], reason="")
        self.hooks = types.SimpleNamespace(run=self._hooks_run)
        self.permissions = types.SimpleNamespace(check=self._perm_check)

    async def _hooks_run(self, *args, **kwargs):
        return self._hooks_outcome

    async def _perm_check(self, *args, **kwargs):
        return types.SimpleNamespace(allow=True, feedback="", stop=False,
                                      reason="", source="rule")

    def log_error(self, text):
        pass


class ToolTimeoutTest(unittest.IsolatedAsyncioTestCase):
    async def test_hanging_tool_ends_with_a_readable_error(self) -> None:
        from app.cli.agent import Agent
        from app.cli.tools.base import ToolCall
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "proj"
            root.mkdir()
            session = Session(root)
            runtime = _FakeRuntime(_FakeTool(delay=30), {"tool_timeout_seconds": 0.05})
            agent = Agent(runtime, session=session, ui=_FakeUI(), depth=1)
            call = ToolCall(name="slow_tool", args={}, id="t1")
            result = await agent._run_one(call, "turn1", {"slow_tool"})
            self.assertFalse(result.ok)
            self.assertIn("timeout", (result.error or "").lower())
            self.assertEqual(call.status, "error")

    async def test_cancelled_tool_call_reports_cancelled(self) -> None:
        from app.cli.agent import Agent
        from app.cli.tools.base import ToolCall
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "proj"
            root.mkdir()
            session = Session(root)
            runtime = _FakeRuntime(_FakeTool(delay=30), {"tool_timeout_seconds": 0})
            agent = Agent(runtime, session=session, ui=_FakeUI(), depth=1)
            call = ToolCall(name="slow_tool", args={}, id="t1")
            task = asyncio.create_task(agent._run_one(call, "turn1", {"slow_tool"}))
            await asyncio.sleep(0.05)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(call.status, "cancelled")


if __name__ == "__main__":
    unittest.main()
