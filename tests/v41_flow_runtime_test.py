"""Offline lifecycle regressions for persisted automation flows."""
import asyncio
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import flow_engine as fe, pgdata, schedule_runtime
from app import storage


class FlowLifecycle(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.patcher = patch.object(pgdata, "MEMU_DIR", self.root)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def runner(self, steps, **fields):
        task = {"id": "lifecycle", "name": "Lifecycle", "steps": fe.normalize_steps(steps)}
        run = fe.new_run(task)
        run.update(fields)
        return fe.FlowRunner(task, run, fe.Services(lang="en"))

    async def test_replayed_terminal_error_does_not_execute_later_steps(self):
        runner = self.runner([{"type": "set", "var": "first", "value": 1},
                              {"type": "set", "var": "later", "value": 2}])
        runner.done[runner.steps[0]["id"]] = {"status": "error", "error": "previous failure"}
        result = await runner.execute()
        self.assertEqual(result["status"], "failed")
        self.assertIn("previous failure", result["error"])
        self.assertNotIn("later", runner.ctx.vars)

    async def test_replayed_allowed_error_can_continue(self):
        runner = self.runner([{"type": "set", "var": "first", "value": 1, "continue_on_error": True},
                              {"type": "set", "var": "later", "value": 2}])
        runner.done[runner.steps[0]["id"]] = {"status": "error", "error": "allowed"}
        self.assertEqual((await runner.execute())["status"], "done")
        self.assertEqual(runner.ctx.vars["later"], "2")

    async def test_cancellation_does_not_mark_the_active_step_successful(self):
        runner = self.runner([{"type": "wait", "seconds": 10}])
        started = asyncio.Event()
        async def interrupted(*_args):
            started.set()
            await asyncio.Future()
        with patch.object(runner, "_exec", interrupted):
            job = asyncio.create_task(runner.execute())
            await asyncio.wait_for(started.wait(), 2)
            job.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await job
        self.assertEqual(runner.run["status"], "cancelled")
        self.assertEqual(runner.run["log"][-1]["status"], "cancelled")

    async def test_stop_failure_is_visible_in_the_step_log(self):
        runner = self.runner([{"type": "stop", "status": "fail", "message": "requested failure"}])
        result = await runner.execute()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["log"][-1]["status"], "error")

    async def test_stop_message_is_not_interpreted_as_status(self):
        runner = self.runner([{"type": "stop", "status": "ok", "message": "fail"}])
        result = await runner.execute()
        self.assertEqual(result["status"], "done")
        self.assertTrue(result["ok"])

    async def test_early_resumed_wait_preserves_original_deadline(self):
        runner = self.runner([{"type": "wait", "seconds": 120}])
        key = runner.steps[0]["id"]
        runner.run["waits"] = {key: 1050}
        with patch.object(fe.time, "time", return_value=1000):
            result = await runner.execute()
        self.assertEqual(result["status"], "waiting")
        self.assertEqual(result["wake_at"], 1050)

    async def test_stale_progress_save_preserves_cancel_request(self):
        runner = self.runner([{"type": "set", "var": "later", "value": 2}])
        fe.save_run(runner.run)
        stale = copy.deepcopy(runner.run)
        self.assertTrue(fe.cancel_run(runner.run["id"]))
        fe.save_run(stale)
        self.assertTrue(fe.load_run(runner.run["id"])["control"]["cancel"])
        resumed = fe.FlowRunner(runner.task, stale, fe.Services(lang="en"))
        with self.assertRaises(asyncio.CancelledError):
            await resumed.execute()
        self.assertNotIn("later", resumed.ctx.vars)

    async def test_stale_progress_save_preserves_continue_request(self):
        runner = self.runner([{"type": "wait", "seconds": 1}], status="paused")
        fe.save_run(runner.run)
        stale = copy.deepcopy(runner.run)
        self.assertTrue(fe.continue_run(runner.run["id"], all_steps=True))
        fe.save_run(stale)
        control = fe.load_run(runner.run["id"])["control"]
        self.assertEqual(control["continue_seq"], 1)
        self.assertTrue(control["all"])

    async def test_cancel_from_api_worker_thread_interrupts_the_active_job(self):
        runner = self.runner([{"type": "wait", "seconds": 10}])
        started = asyncio.Event()
        async def waiting(*_args):
            started.set()
            await asyncio.Future()
        with patch.object(runner, "_exec", waiting):
            job = asyncio.create_task(runner.execute())
            fe._ACTIVE[runner.run["id"]] = job
            try:
                await asyncio.wait_for(started.wait(), 2)
                self.assertTrue(await asyncio.to_thread(fe.cancel_run, runner.run["id"]))
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(job, 2)
            finally:
                fe._ACTIVE.pop(runner.run["id"], None)
                if not job.done():
                    job.cancel()

    async def test_cancelling_an_active_question_closes_its_pending_form(self):
        runner = self.runner([{"type": "wait", "seconds": 1}], asks={"step": "question"})
        with patch.object(runner.sv, "form_state", return_value={"status": "pending"}), \
                patch.object(runner.sv, "settle_form") as settle, \
                patch.object(runner.sv, "drop_telegram_form") as drop, \
                patch.object(runner, "_exec", side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await runner.execute()
        settle.assert_called_once_with("question", "cancelled")
        drop.assert_called_once_with("question")

    async def test_cross_process_cancel_during_final_step_cannot_finish_successfully(self):
        runner = self.runner([{"type": "set", "var": "value", "value": "ok"}])
        async def last_step(*_args):
            fe.cancel_run(runner.run["id"])
            return {"result": "ok"}
        with patch.object(runner, "_exec", last_step):
            with self.assertRaises(asyncio.CancelledError):
                await runner.execute()
        self.assertEqual(runner.run["status"], "cancelled")

    async def test_cancel_control_write_cannot_restore_an_older_running_snapshot(self):
        runner = self.runner([{"type": "set", "var": "value", "value": "ok"}], status="running", boot=fe.PROCESS_BOOT)
        fe.save_run(runner.run)
        mutate = storage.mutate_json
        intercepted = []
        def race(path, default, mutator, **kwargs):
            if Path(path) == fe._run_path(runner.run["id"]) and not intercepted:
                intercepted.append(True)
                terminal = copy.deepcopy(runner.run)
                terminal.update(status="cancelled", finished=1234, log=[{"status": "cancelled", "key": "last"}])
                mutate(path, {}, lambda raw: (True, terminal), backup=False)
            return mutate(path, default, mutator, **kwargs)
        with patch.object(storage, "mutate_json", side_effect=race):
            self.assertTrue(fe.cancel_run(runner.run["id"]))
        saved = fe.load_run(runner.run["id"])
        self.assertEqual(saved["status"], "cancelled")
        self.assertEqual(saved["log"], [{"status": "cancelled", "key": "last"}])
        self.assertTrue(saved["control"]["cancel"])
        self.assertEqual(fe.list_runs()[0]["status"], "cancelled")

    async def test_concurrent_continue_requests_each_increment_the_sequence(self):
        runner = self.runner([{"type": "wait", "seconds": 1}], status="paused")
        fe.save_run(runner.run)
        results = await asyncio.gather(*(asyncio.to_thread(fe.continue_run, runner.run["id"]) for _ in range(8)))
        self.assertEqual(results, [True] * 8)
        self.assertEqual(fe.load_run(runner.run["id"])["control"]["continue_seq"], 8)

    async def test_invalid_run_ids_never_alias_an_existing_run(self):
        runner = self.runner([{"type": "wait", "seconds": 1}], status="paused")
        fe.save_run(runner.run)
        for identity in (runner.run["id"] + "/", "../" + runner.run["id"], runner.run["id"] + "\n", "a" * 41, "", None):
            with self.subTest(identity=identity):
                self.assertIsNone(fe.load_run(identity))
                self.assertFalse(fe.cancel_run(identity))
                self.assertFalse(fe.continue_run(identity))
        saved = fe.load_run(runner.run["id"])
        self.assertEqual(saved["status"], "paused")
        self.assertNotIn("control", saved)

    async def test_mismatching_stored_id_cannot_be_cancelled_or_resumed(self):
        runner = self.runner([{"type": "wait", "seconds": 1}], status="paused")
        fe.save_run(runner.run)
        storage.mutate_json(fe._run_path(runner.run["id"]), {}, lambda raw: (True, {**raw, "id": "different"}), backup=False)
        self.assertIsNone(fe.load_run(runner.run["id"]))
        self.assertFalse(fe.cancel_run(runner.run["id"]))
        self.assertFalse(fe.continue_run(runner.run["id"]))


class SchedulerWake(unittest.TestCase):
    def test_disabled_strings_and_nonfinite_deadlines_do_not_cause_polling(self):
        tasks = [{"enabled": "false", "next_run": 1},
                 {"enabled": True, "next_run": float("nan")},
                 {"enabled": True, "next_run": float("-inf")},
                 {"enabled": True, "next_run": True},
                 {"enabled": True, "next_run": 1000}]
        with patch.object(pgdata, "load_schedules", return_value=tasks):
            self.assertEqual(schedule_runtime._earliest_due(), 1000)


if __name__ == "__main__":
    unittest.main()
