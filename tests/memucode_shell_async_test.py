"""Background shell waits use real temporary child processes, never user data."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.cli.tools.base import ToolError
from app.cli.tools.shell import BackgroundShells, BashOutputTool, BashTool


class BackgroundShellWaitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="memucode-shell-wait-")
        self.root = Path(self.temp.name)
        self.shells = BackgroundShells()
        self.runtime = SimpleNamespace(shells=self.shells, settings={})
        self.ctx = SimpleNamespace(runtime=self.runtime)
        self.tool = BashOutputTool()

    def tearDown(self):
        for row in self.shells.rows.values():
            process = row["process"]
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
            self.assertTrue(row["output_done"].wait(5))
        self.temp.cleanup()

    def start_process(self):
        source = self.root / "worker.py"
        source.write_text(
            "import pathlib, time\n"
            "print('ready', flush=True)\n"
            "while not pathlib.Path('release').exists(): time.sleep(.01)\n"
            "print('final output', flush=True)\n", encoding="utf-8")
        def spawn(*_args):
            return subprocess.Popen(
                [sys.executable, "-u", str(source)], cwd=self.root,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        with patch("app.cli.tools.shell._spawn", side_effect=spawn), \
                patch("app.cli.tools.shell.argv_for", return_value=[]), \
                patch("app.cli.tools.shell.command_env", return_value={}):
            return self.shells.start(self.runtime, "test worker", self.root)

    async def test_wait_yields_to_other_work_and_includes_final_output(self):
        row = self.start_process()
        waiter = asyncio.create_task(self.tool.run(self.ctx, {
            "bash_id": row["id"], "wait_seconds": 5}, None))
        ticks = 0
        for _ in range(5):
            await asyncio.sleep(.02)
            ticks += 1
            self.assertFalse(waiter.done())
        (self.root / "release").touch()
        result = await asyncio.wait_for(waiter, 5)
        self.assertEqual(ticks, 5)
        self.assertIn("final output", result.output)
        self.assertEqual(result.meta["exit_code"], 0)
        self.assertFalse(result.meta["running"])
        self.assertEqual(self.shells.read(row["id"])["new_output"], "")

    async def test_timeout_and_default_poll_leave_process_running(self):
        row = self.start_process()
        result = await self.tool.run(self.ctx, {"bash_id": row["id"]}, None)
        self.assertTrue(result.meta["running"])
        started = time.monotonic()
        result = await self.tool.run(self.ctx, {"bash_id": row["id"], "wait_seconds": .08}, None)
        self.assertLess(time.monotonic() - started, 1)
        self.assertTrue(result.meta["running"])
        self.assertIsNone(row["process"].poll())

    async def test_cancel_wait_does_not_kill_or_consume_output(self):
        row = self.start_process()
        waiter = asyncio.create_task(self.shells.wait(row["id"], 60))
        await asyncio.sleep(.08)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertIsNone(row["process"].poll())
        self.assertEqual(row["read"], 0)
        (self.root / "release").touch()
        result = await self.shells.wait(row["id"], 5)
        self.assertIn("ready", result["new_output"])
        self.assertIn("final output", result["new_output"])

    async def test_invalid_waits_and_missing_job_are_actionable_errors(self):
        for value in (-1, 61, float("inf"), float("nan"), "invalid", None):
            with self.subTest(value=value), self.assertRaises(ToolError):
                await self.tool.run(self.ctx, {"bash_id": "missing", "wait_seconds": value}, None)
        with self.assertRaisesRegex(ToolError, "No background command"):
            await self.tool.run(self.ctx, {"bash_id": "missing", "wait_seconds": 1}, None)
        with self.assertRaisesRegex(ToolError, "bash_id is required"):
            await self.tool.run(self.ctx, {"wait_seconds": 1}, None)

    async def test_background_dispatch_has_no_fixed_delay(self):
        row = self.start_process()
        with patch.object(self.shells, "start", return_value=row):
            result = await asyncio.wait_for(
                BashTool()._background(self.ctx, "test worker", self.root, {}), .5)
        self.assertEqual(result.meta["background_id"], row["id"])
        self.assertIsNone(row["process"].poll())


if __name__ == "__main__":
    unittest.main()
