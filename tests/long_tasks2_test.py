"""Long-task stability (2/2): Work with many tasks, DFY resume/idempotency,
and the scheduled-messages batch cap.

Simulations with stub models/machines only: hundreds of Work files,
dozens of finished agents, an interrupted DFY run that resumes without
re-executing its idempotent step, and a DFY file write retried with the
same idempotency key. No network, no real data.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-long-tasks2-")

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import dfy as dfy_mod
from app.dfy import DFYManager, DFYTask
from app import dfy_machine
from app.dfy_agent import Agent as MachineAgent, Guard
from app.scheduled_messages_runner import pick_due
from app.work_agents import AgentPool
from app.work_tools import run as work_run, tool_search, tool_tree


class WorkPaginationTest(unittest.TestCase):
    def test_tree_pages_cover_every_file_exactly_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index in range(120):
                (root / f"file{index:03d}.py").write_text("VALUE = %d\n" % index, encoding="utf-8")
            seen: list = []
            offset = 0
            pages = 0
            while True:
                page = tool_tree(root, {"limit": 50, "offset": offset})
                self.assertEqual(page["offset"], offset)
                seen.extend(row["path"] for row in page["files"])
                pages += 1
                if page["next_offset"] is None:
                    break
                offset = page["next_offset"]
                self.assertLess(pages, 10)
            self.assertEqual(len(seen), 120)
            self.assertEqual(len(set(seen)), 120)

    def test_search_pages_match_the_full_search(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index in range(12):
                (root / f"mod{index}.py").write_text(
                    "".join("needle line %d\n" % line for line in range(5)), encoding="utf-8")
            full = tool_search(root, {"query": "needle", "limit": 200})
            self.assertEqual(len(full["matches"]), 60)
            page = tool_search(root, {"query": "needle", "limit": 10, "offset": 10})
            self.assertEqual(page["next_offset"], 20)
            wanted = [(m["path"], m["line"]) for m in full["matches"][10:20]]
            got = [(m["path"], m["line"]) for m in page["matches"]]
            self.assertEqual(got, wanted)

    def test_work_run_reports_tool_timing_without_changing_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "a.py").write_text("x = 1\n", encoding="utf-8")
            result = work_run("work_read", root, {"path": "a.py"})
            self.assertEqual(result["text"], "x = 1\n")
            self.assertIn("ms", result)


class AgentPoolPruneTest(unittest.IsolatedAsyncioTestCase):
    async def _pool(self) -> AgentPool:
        async def complete(model, messages):
            return "report: all done"

        async def run_tool(name, args, job):
            return {"tool": name, "ok": True}

        return AgentPool(complete, run_tool)

    async def test_many_finished_agents_do_not_block_new_ones(self) -> None:
        pool = await self._pool()
        for index in range(30):
            job = pool.spawn("chat1", title=f"agent {index}", task="do the thing", model="m")
            await asyncio.wait_for(job.handle, timeout=5)
            self.assertEqual(job.status, "done")
        self.assertLessEqual(len(pool.jobs["chat1"]), 24)
        job = pool.spawn("chat1", title="one more", task="do the thing", model="m")
        await asyncio.wait_for(job.handle, timeout=5)
        self.assertEqual(job.status, "done")

    async def test_cancel_stops_a_running_agent_gracefully(self) -> None:
        async def slow_complete(model, messages):
            await asyncio.sleep(30)
            return "never"

        async def run_tool(name, args, job):
            return {"tool": name}

        pool = AgentPool(slow_complete, run_tool)
        job = pool.spawn("chat1", title="slow", task="wait", model="m")
        await asyncio.sleep(0.05)
        stopped = pool.cancel("chat1", job.id)
        self.assertEqual(stopped, 1)
        await asyncio.gather(job.handle, return_exceptions=True)
        self.assertEqual(job.status, "cancelled")
        self.assertEqual(pool.running("chat1"), [])


class DfyStepKeyTest(unittest.TestCase):
    def test_key_ignores_volatile_fields_only(self) -> None:
        first = dfy_machine.step_key("file_read", {"path": "/a.txt", "timeout": 10, "reason": "x"})
        second = dfy_machine.step_key("file_read", {"path": "/a.txt", "timeout": 99, "reason": "y"})
        other = dfy_machine.step_key("file_read", {"path": "/b.txt"})
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)

    def test_only_reads_and_same_content_writes_are_idempotent(self) -> None:
        self.assertTrue(dfy_machine.is_idempotent("file_read"))
        self.assertTrue(dfy_machine.is_idempotent("file_write"))
        self.assertFalse(dfy_machine.is_idempotent("shell"))
        self.assertFalse(dfy_machine.is_idempotent("file_delete"))
        self.assertFalse(dfy_machine.is_idempotent("download"))


class _FakeMachines:
    def __init__(self) -> None:
        self.calls: list = []

    async def probe(self, machine_id):
        return {"distro": "linux", "os": "linux", "arch": "x86_64", "user": "tester",
                "shell": "bash", "package_managers": [], "memory": {}, "disk": {},
                "cwd": "/tmp"}

    async def call(self, machine_id, op, params, on_chunk=None, timeout=None):
        self.calls.append((op, dict(params)))
        if op == "fs_read":
            return {"path": params.get("path"), "size": 5, "text": "hello", "truncated": False}
        if op == "fs_write":
            return {"path": params.get("path"), "bytes": 5, "size": 5}
        return {"ok": True}


class _FakeVault:
    def redactor(self):
        return lambda value: str(value)

    def public_for_model(self):
        return []


class _FakeManager:
    """Only the surface dfy_machine.run() touches."""

    def __init__(self, machines, actions, settings):
        self.machines = machines
        self.vault = _FakeVault()
        self.settings_provider = lambda: settings
        self._actions = list(actions)
        self.steps: list = []
        self.proxy = None

    def _lang(self):
        return "ru"

    async def _add_step(self, task, action, detail, redact, shot="", ok=True):
        step = {"n": len(task.steps) + 1, "action": action, "detail": detail, "ok": ok}
        task.steps.append(step)
        return step

    async def _touch(self, task):
        pass

    async def _next_action(self, task, messages):
        if not self._actions:
            return {"action": "done", "result": "finished", "reason": "ok"}
        action = self._actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return action


class DfyMachineReplayTest(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_read_runs_once_and_replays_from_checkpoint(self) -> None:
        from app.stuck_detector import PREF_KEY
        machines = _FakeMachines()
        action = {"action": "file_read", "path": "/tmp/x.txt", "reason": "read it"}
        manager = _FakeManager(machines, [action, dict(action), {"action": "done", "result": "ok", "reason": "ok"}],
                               {PREF_KEY: "off"})
        task = DFYTask("read the file", [], "chat", "c1", "m", 5, "irreversible")
        task.target = {"id": "local", "name": "local", "kind": "local"}
        await dfy_machine.run(manager, task)
        self.assertEqual(task.status, "done")
        self.assertEqual(len(machines.calls), 1)
        self.assertEqual(task.machine_steps, 1)
        self.assertIn("replayed from checkpoint", " ".join(task.history))
        self.assertEqual(len(task.completed_keys), 1)


class DfyResumeTest(unittest.IsolatedAsyncioTestCase):
    def _manager(self, machines, actions, temp):
        from app.stuck_detector import PREF_KEY
        queue = list(actions)

        async def completion(model, messages):
            if not queue:
                return '{"action": "done", "result": "ok", "reason": "ok"}'
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        settings = {PREF_KEY: "off", "language": "ru", "dfy_enabled": True, "dfy_model": "m",
                    "dfy_max_steps": 5, "dfy_confirm_mode": "irreversible",
                    "dfy_browser_visible": False, "dfy_local_system": True}
        manager = DFYManager(completion, lambda: settings, data_root=Path(temp))
        manager._machines = machines
        return manager, queue

    async def test_interrupted_run_resumes_without_reexecuting_its_write(self) -> None:
        machines = _FakeMachines()
        write = '{"action": "file_write", "path": "/tmp/note.txt", "text": "hello", "reason": "write"}'
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        manager, queue = self._manager(machines, [write, RuntimeError("model boom"),
                                              RuntimeError("model boom again")], tmp.name)
        task = DFYTask("write the note", [], "chat", "c1", "m", 5, "irreversible")
        task.target = {"id": "local", "name": "local", "kind": "local"}
        manager.tasks[task.id] = task
        await manager._run(task, visible=False)
        self.assertEqual(task.status, "failed")
        self.assertEqual(len(machines.calls), 1)
        self.assertEqual(len(task.completed_keys), 1)
        queue.append(write)
        resumed = await manager.resume(task.id)
        self.assertIs(resumed, task)
        await asyncio.wait_for(task.job, timeout=10)
        self.assertEqual(task.status, "done")
        self.assertEqual(len(machines.calls), 1)
        self.assertIn("replayed from checkpoint", " ".join(task.history))

    async def test_model_timeout_fails_the_run_cleanly(self) -> None:
        async def hanging(model, messages):
            await asyncio.sleep(30)
            return "{}"

        settings = {"language": "ru", "dfy_enabled": True, "dfy_model": "m",
                    "dfy_max_steps": 5, "dfy_confirm_mode": "irreversible",
                    "dfy_browser_visible": False}
        with tempfile.TemporaryDirectory() as temp:
            manager = DFYManager(hanging, lambda: settings, data_root=Path(temp))
            task = DFYTask("goal", [], "chat", "c1", "m", 5, "irreversible")
            with mock.patch.object(dfy_mod, "NEXT_ACTION_TIMEOUT", 0.05):
                with self.assertRaises(RuntimeError):
                    await manager._next_action(task, [{"role": "user", "content": "go"}])


class DfyAgentWriteTest(unittest.TestCase):
    def _agent(self, root: Path) -> MachineAgent:
        return MachineAgent(guard=Guard(roots=[str(root)]))

    def test_identical_overwrite_is_a_noop_and_leaves_no_temp_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            agent = self._agent(root)
            target = root / "note.txt"
            first = agent.fs_write({"path": str(root / "note.txt"), "text": "hello", "confirmed": True})
            self.assertTrue(first["created"])
            before = target.stat().st_mtime_ns
            time.sleep(0.01)
            second = agent.fs_write({"path": str(root / "note.txt"), "text": "hello", "confirmed": True})
            self.assertTrue(second.get("unchanged"))
            self.assertEqual(target.stat().st_mtime_ns, before)
            self.assertEqual(target.read_text(encoding="utf-8"), "hello")
            self.assertEqual([p.name for p in root.iterdir() if p.suffix == ".tmp"], [])

    def test_append_with_the_same_idempotency_key_writes_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            agent = self._agent(root)
            params = {"path": str(root / "log.txt"), "text": "line\n", "append": True, "idempotency_key": "k-1"}
            first = agent.fs_write(params)
            second = agent.fs_write(params)
            self.assertTrue(second.get("replayed"))
            self.assertEqual((root / "log.txt").read_text(encoding="utf-8"), "line\n")
            self.assertEqual(first["size"], second["size"])

    def test_overwrite_with_the_same_key_replays_and_new_key_rewrites(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            agent = self._agent(root)
            params = {"path": str(root / "a.txt"), "text": "one", "confirmed": True, "idempotency_key": "k-2"}
            agent.fs_write(params)
            replayed = agent.fs_write(params)
            self.assertTrue(replayed.get("replayed"))
            changed = agent.fs_write({"path": str(root / "a.txt"), "text": "two", "confirmed": True,
                                      "idempotency_key": "k-3"})
            self.assertFalse(changed.get("replayed", False))
            self.assertEqual((root / "a.txt").read_text(encoding="utf-8"), "two")

    def test_fs_list_paginates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index in range(5):
                (root / f"f{index}.txt").write_text("x", encoding="utf-8")
            agent = self._agent(root)
            page = agent.fs_list({"path": str(root), "limit": 2, "offset": 2})
            self.assertEqual(page["total"], 5)
            self.assertEqual(page["shown"], 2)
            self.assertEqual(page["next_offset"], 4)
            last = agent.fs_list({"path": str(root), "limit": 2, "offset": 4})
            self.assertEqual(last["shown"], 1)
            self.assertIsNone(last["next_offset"])


class PickDueTest(unittest.TestCase):
    def test_batch_cap_keeps_the_oldest_due_first(self) -> None:
        now = 1_700_000_000_000
        rows = [{"id": f"m{i}", "status": "scheduled", "trigger": "time", "due_at": now - (25 - i) * 1000}
                for i in range(25)]
        rows.append({"id": "future", "status": "scheduled", "trigger": "time",
                     "due_at": now + 60_000})
        picked = pick_due(rows, now)
        self.assertEqual(len(picked), 10)
        self.assertEqual(picked[0]["id"], "m0")
        self.assertNotIn("future", [row["id"] for row in picked])
        self.assertEqual(len(pick_due(rows, now, limit=3)), 3)
        self.assertEqual(pick_due([], now), [])


if __name__ == "__main__":
    unittest.main()
