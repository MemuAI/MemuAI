"""Focused contracts for durable, non-overlapping scheduled task execution."""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import pgdata, scheduler


class SchedulerContracts(unittest.TestCase):
    def tearDown(self) -> None:
        scheduler._RUNNING_TASK_IDS.clear()
        scheduler._ACTIVE_TASKS.clear()

    def test_malformed_persisted_rows_do_not_block_valid_tasks(self) -> None:
        async def scenario(root: Path) -> None:
            (root / "schedules.json").write_text(json.dumps([
                "not-a-task",
                None,
                {
                    "id": "bad-next", "enabled": "true", "interval_minutes": "broken",
                    "next_run": "NaN", "last_run": "also-broken", "custom": {"keep": True},
                },
                {"name": "disabled", "enabled": "false", "daily_at": "99:99", "next_run": "bad"},
                {
                    "id": "due", "name": "Due", "prompt": "run", "model": "test",
                    "enabled": True, "interval_minutes": 60, "next_run": 10,
                },
            ]), encoding="utf-8")
            calls = []

            async def fake_run(task):
                calls.append(task["id"])

            with patch.object(scheduler, "_run_task", fake_run):
                self.assertEqual(await scheduler._scheduler_tick(now=100), 1)
                await asyncio.gather(*list(scheduler._ACTIVE_TASKS))

            saved = json.loads((root / "schedules.json").read_text(encoding="utf-8"))
            self.assertEqual(calls, ["due"])
            self.assertEqual(len(saved), 3)
            self.assertTrue(all(isinstance(task, dict) for task in saved))
            bad = next(task for task in saved if task["id"] == "bad-next")
            self.assertEqual(bad["next_run"], 3700)
            self.assertNotIn("last_run", bad)
            self.assertEqual(bad["custom"], {"keep": True})
            disabled = next(task for task in saved if task.get("name") == "disabled")
            self.assertIs(disabled["enabled"], False)
            self.assertTrue(disabled["id"])
            self.assertNotIn("next_run", disabled)
            due = next(task for task in saved if task["id"] == "due")
            self.assertEqual(due["last_run"], 100)
            self.assertEqual(due["next_run"], 3700)

            (root / "schedules.json").write_text('{"not":"a list"}', encoding="utf-8")
            self.assertEqual(await scheduler._scheduler_tick(now=200), 0)
            self.assertEqual(pgdata.load_schedules(), [])
            self.assertEqual(json.loads((root / "schedules.json").read_text(encoding="utf-8")), [])

        with tempfile.TemporaryDirectory() as temp, patch.object(pgdata, "MEMU_DIR", Path(temp)):
            asyncio.run(scenario(Path(temp)))

    def test_running_task_is_not_started_twice_and_stale_save_keeps_runtime_fields(self) -> None:
        async def scenario() -> None:
            pgdata.save_schedules([{
                "id": "slow", "name": "Original", "prompt": "wait", "model": "test",
                "enabled": True, "interval_minutes": 1, "next_run": 1,
            }])
            stale = pgdata.load_schedules()
            entered = asyncio.Event()
            release = asyncio.Event()
            calls = 0

            async def slow_run(_task):
                nonlocal calls
                calls += 1
                entered.set()
                await release.wait()

            with patch.object(scheduler, "_run_task", slow_run):
                self.assertEqual(await scheduler._scheduler_tick(now=100), 1)
                await asyncio.wait_for(entered.wait(), timeout=1)

                stale[0]["name"] = "Edited while running"
                stale[0].pop("last_run", None)
                stale[0]["next_run"] = 1
                pgdata.save_schedules(stale)
                merged = pgdata.load_schedules()[0]
                self.assertEqual(merged["name"], "Edited while running")
                self.assertEqual(merged["last_run"], 100)
                self.assertEqual(merged["next_run"], 160)

                self.assertEqual(await scheduler._scheduler_tick(now=200), 0)
                self.assertEqual(calls, 1)
                release.set()
                await asyncio.gather(*list(scheduler._ACTIVE_TASKS))

            final = pgdata.load_schedules()[0]
            self.assertEqual(final["name"], "Edited while running")
            self.assertEqual(final["last_run"], 100)
            self.assertNotIn("slow", scheduler._RUNNING_TASK_IDS)

        with tempfile.TemporaryDirectory() as temp, patch.object(pgdata, "MEMU_DIR", Path(temp)):
            asyncio.run(scenario())

    def test_cancelled_one_shot_is_due_again_and_not_recorded_as_run(self) -> None:
        async def scenario() -> None:
            pgdata.save_schedules([{
                "id": "once", "name": "One shot", "prompt": "run", "model": "test",
                "enabled": True, "run_at": 50, "next_run": 50,
            }])
            entered = asyncio.Event()
            calls = 0

            async def blocked_run(_task):
                nonlocal calls
                calls += 1
                entered.set()
                await asyncio.Event().wait()

            with patch.object(scheduler, "_run_task", blocked_run):
                self.assertEqual(await scheduler._scheduler_tick(now=100), 1)
                job = next(iter(scheduler._ACTIVE_TASKS))
                await asyncio.wait_for(entered.wait(), timeout=1)
                job.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await job

                cancelled = pgdata.load_schedules()[0]
                self.assertTrue(cancelled["enabled"])
                self.assertEqual(cancelled["next_run"], 100)
                self.assertNotIn("last_run", cancelled)

                self.assertEqual(await scheduler._scheduler_tick(now=100), 1)
                retry = next(iter(scheduler._ACTIVE_TASKS))
                await asyncio.sleep(0)
                retry.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await retry
            self.assertEqual(calls, 2)

        with tempfile.TemporaryDirectory() as temp, patch.object(pgdata, "MEMU_DIR", Path(temp)):
            asyncio.run(scenario())

    def test_parallel_schedule_results_are_not_lost(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.object(pgdata, "MEMU_DIR", Path(temp)):
            with ThreadPoolExecutor(max_workers=12) as pool:
                list(pool.map(
                    lambda number: pgdata.append_schedule_result(str(number), True, f"result-{number}"),
                    range(40),
                ))
            results = pgdata.load_schedule_results()
            self.assertEqual(len(results), 40)
            self.assertEqual({row["task_id"] for row in results}, {str(number) for number in range(40)})

    def test_notification_failure_does_not_create_a_false_failure_result(self) -> None:
        from app import desktop_notifications
        from app.chat import chat_service

        records = []

        def stream_completion(_model, _messages):
            async def chunks():
                yield {"choices": [{"delta": {"content": "done"}}]}
            return chunks()

        async def scenario() -> None:
            with patch.object(chat_service, "stream_completion", stream_completion), \
                 patch.object(scheduler, "append_schedule_result",
                              lambda task_id, ok, text: records.append((task_id, ok, text))), \
                 patch.object(scheduler, "load_preferences",
                              return_value={"notify_windows": True, "notify_sound": False}), \
                 patch.object(desktop_notifications, "show_completion",
                              side_effect=RuntimeError("notification unavailable")), \
                 patch.object(scheduler.log, "warning") as warning, \
                 patch("app.telegram_bot.send_notification", new=AsyncMock()):
                await scheduler._run_task({"id": "notify", "name": "Task", "model": "test"})
                warning.assert_called_once()

        asyncio.run(scenario())
        self.assertEqual(records, [("notify", True, "done")])


if __name__ == "__main__":
    unittest.main()
