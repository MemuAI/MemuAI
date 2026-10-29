"""Remote output and cancellation using only temporary Python child processes."""
import ctypes
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from app import dfy_agent as A


class BrowserArgumentsTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "native Windows command-line parser")
    def test_windows_quoted_values_and_backslashes_round_trip(self):
        args = ['--host-resolver-rules=MAP site.test.example 127.0.0.1:8000',
                r'--profile=C:\Browser\User', '', '--name=say "hello"',
                '--path=C:\\folder with spaces\\\\']
        self.assertEqual(A._browser_args(subprocess.list2cmdline(args)), args)

    def test_posix_shlex_keeps_quoted_values(self):
        with patch.object(A, "WINDOWS", False):
            self.assertEqual(A._browser_args("--one 'two three' ''"), ['--one', 'two three', ''])


class RemoteFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.agent = A.Agent(A.Guard(roots=[str(self.root)]), state=self.root / "state")

    def command(self, source):
        script = self.root / "command.py"
        script.write_text(source, encoding="utf-8")
        args = [sys.executable, "-u", str(script)]
        return subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)

    def run_bytes(self, raw, agent=None):
        chunks = []
        result = (agent or self.agent).exec({
            "command": self.command("import sys\nsys.stdout.buffer.write(" + repr(raw) + ")\n"),
            "cwd": str(self.root), "confirmed": True,
        }, on_chunk=lambda job, text: chunks.append(text))
        self.assertEqual(result["exit_code"], 0, result)
        self.assertEqual(result["output"], "".join(chunks))
        return result


class RemoteOutputTests(RemoteFixture):
    def test_windows_oem_output_and_utf8_can_share_the_stream(self):
        expected = "Русская строка\r\nСовременный UTF-8: 世界\n"
        raw = "Русская строка\r\n".encode("cp866") + "Современный UTF-8: 世界\n".encode("utf-8")
        with patch.object(A, "_output_codepages", return_value=["cp866"], create=True):
            result = self.run_bytes(raw)
        self.assertEqual(result["output"], expected)
        self.assertFalse(result["truncated"])

    def test_utf8_is_preferred_to_the_legacy_console_page(self):
        expected = "Привет, 世界\r\n"
        with patch.object(A, "_output_codepages", return_value=["cp866"], create=True):
            result = self.run_bytes(expected.encode("utf-8"))
        self.assertEqual(result["output"], expected)

    def test_single_oversized_line_sets_truncated_and_bounds_streamed_text(self):
        small = A.Agent(self.agent.guard, max_output=5, state=self.root / "state")
        result = self.run_bytes(b"123456789", small)
        self.assertEqual(result["output"], "12345")
        self.assertTrue(result["truncated"])

    def test_exact_character_limit_is_not_truncated(self):
        small = A.Agent(self.agent.guard, max_output=5, state=self.root / "state")
        result = self.run_bytes("Приве".encode("utf-8"), small)
        self.assertEqual(result["output"], "Приве")
        self.assertFalse(result["truncated"])

    def bounded_output(self, text, encoding, limit):
        reads = []
        original = A._command_process

        class OutputSpy:
            def __init__(self, stream):
                self.stream = stream

            def readline(self, size=-1):
                if size <= 0 or size > max(4 * limit + 4, 64 * 1024):
                    raise AssertionError(f"unbounded process output read: {size}")
                raw = self.stream.readline(size)
                reads.append((size, len(raw)))
                return raw

            def close(self):
                return self.stream.close()

        def launch(*args, **kwargs):
            process = original(*args, **kwargs)
            process.stdout = OutputSpy(process.stdout)
            return process

        small = A.Agent(self.agent.guard, max_output=limit, state=self.root / "state")
        with patch.object(A, "_command_process", side_effect=launch), \
             patch.object(A, "_output_codepages", return_value=["cp866"]):
            result = self.run_bytes(text.encode(encoding), small)
        self.assertEqual(result["output"], text[:limit])
        self.assertEqual(result["truncated"], len(text) > limit)
        self.assertTrue(reads)
        return reads

    def test_bounded_unicode_prefix_does_not_mistake_partial_utf8_for_oem(self):
        text = "😀界Привет" * 1000
        limit = 66
        with self.assertRaises(UnicodeDecodeError):
            text.encode("utf-8")[:4 * limit + 4].decode("utf-8")
        reads = self.bounded_output(text, "utf-8", limit)
        self.assertEqual(reads[0], (4 * limit + 4, 4 * limit + 4))
        self.assertTrue(any(size == 64 * 1024 for size, _ in reads[1:]))

    def test_bounded_oem_output_preserves_requested_prefix(self):
        self.bounded_output("Русская строка " * 1000, "cp866", 66)

    def test_exact_unicode_budget_is_not_truncated_with_bounded_reads(self):
        text = ("😀界Привет" * 10)[:66]
        self.bounded_output(text, "utf-8", len(text))

    def test_zero_output_budget_drains_without_decoding_or_unbounded_reads(self):
        self.bounded_output("Русская строка " * 100, "utf-8", 0)


class RemoteJobIdentityTests(RemoteFixture):
    def test_active_job_id_cannot_be_replaced_and_remains_cancellable(self):
        ready = threading.Event()
        result = {}
        command = self.command("import time\nprint('ready', flush=True)\ntime.sleep(2)\n")

        def execute():
            try:
                result["value"] = self.agent.exec({
                    "command": command, "cwd": str(self.root), "confirmed": True,
                    "job": "shared-id", "timeout": 5,
                }, on_chunk=lambda job, text: ready.set())
            except BaseException as exc:
                result["error"] = exc

        thread = threading.Thread(target=execute, daemon=True)
        thread.start()
        owned = None
        try:
            self.assertTrue(ready.wait(5), result)
            with self.agent._lock:
                owned = self.agent.jobs["shared-id"]
            replacement = self.command("print('replacement')\n")
            with self.assertRaises(A.Refused):
                self.agent.exec({"command": replacement, "cwd": str(self.root),
                                 "job": "shared-id", "confirmed": True})
            with self.agent._lock:
                self.assertIs(self.agent.jobs.get("shared-id"), owned)
            self.assertTrue(self.agent.exec_cancel({"job": "shared-id"})["ok"])
            thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertNotIn("error", result, result)
            self.assertEqual(result["value"]["status"], "cancelled")
            self.assertFalse(self.agent.jobs)
        finally:
            if owned is not None:
                self.agent._terminate(owned)
            thread.join(5)

    def test_completed_job_id_can_be_used_again(self):
        command = self.command("print('done')\n")
        for _ in range(2):
            result = self.agent.exec({"command": command, "cwd": str(self.root),
                                      "job": "reused-id", "confirmed": True})
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["output"].strip(), "done")
            self.assertFalse(self.agent.jobs)


@unittest.skipUnless(os.name == "nt", "native Windows process-tree regression")
class RemoteWindowsTreeTests(RemoteFixture):
    def setUp(self):
        super().setUp()
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
        self.kernel.OpenProcess.restype = ctypes.c_void_p
        self.kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        self.kernel.WaitForSingleObject.restype = ctypes.c_uint
        self.kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        self.handles = []
        self.addCleanup(self.cleanup_owned_children)

    def cleanup_owned_children(self):
        # A live handle proves this is the exact process created by the fixture,
        # even if a failed pre-fix run already killed its shell parent.
        for pid, handle in self.handles:
            if self.kernel.WaitForSingleObject(handle, 0) == 258:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
            self.kernel.CloseHandle(handle)
        self.handles.clear()

    def check_tree_stop(self, cancel):
        pids_file = self.root / "owned-pids.json"
        command = self.command(
            "import json, os, subprocess, sys, time\n"
            "from pathlib import Path\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(15)'])\n"
            "Path(os.environ['MEMUAI_TEST_PIDS']).write_text(json.dumps([os.getpid(), child.pid]))\n"
            "print('owned child ready', flush=True)\n"
            "time.sleep(15)\n")
        ready = threading.Event()
        holder = {}
        def execute():
            try:
                holder["result"] = self.agent.exec({
                    "command": command, "cwd": str(self.root), "confirmed": True,
                    "job": "owned-tree", "timeout": 12 if cancel else 2,
                    "env": {"MEMUAI_TEST_PIDS": str(pids_file)},
                }, on_chunk=lambda job, text: ready.set() if "ready" in text else None)
            except BaseException as exc:
                holder["error"] = exc
        thread = threading.Thread(target=execute, daemon=True)
        thread.start()
        try:
            self.assertTrue(ready.wait(5), holder)
            for pid in json.loads(pids_file.read_text()):
                handle = self.kernel.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE
                self.assertTrue(handle, ctypes.get_last_error())
                self.handles.append((pid, handle))
            started = time.monotonic()
            if cancel:
                self.assertTrue(self.agent.exec_cancel({"job": "owned-tree"})["ok"])
            thread.join(6)
            self.assertFalse(thread.is_alive(), "a descendant kept stdout open after termination")
            self.assertLess(time.monotonic() - started, 6)
            self.assertNotIn("error", holder, holder)
            result = holder["result"]
            self.assertEqual(result["status"], "cancelled" if cancel else "timeout")
            self.assertIn("owned child ready", result["output"])
            self.assertFalse(self.agent.jobs)
            for pid, handle in self.handles:
                self.assertEqual(self.kernel.WaitForSingleObject(handle, 1000), 0,
                                 f"owned descendant {pid} survived")
        finally:
            self.cleanup_owned_children()
            thread.join(5)
            if thread.is_alive():
                self.agent.exec_cancel({"job": "owned-tree"})
                thread.join(5)

    def test_cancel_kills_owned_windows_children_and_closes_the_pipe(self):
        self.check_tree_stop(cancel=True)

    def test_timeout_kills_owned_windows_children_and_closes_the_pipe(self):
        self.check_tree_stop(cancel=False)

    def check_exited_parent_stop(self, cancel, detached_output=False):
        pids_file = self.root / "exited-parent.json"
        child_stdout = ", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL" if detached_output else ""
        command = self.command(
            "import json, os, subprocess, sys\nfrom pathlib import Path\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(15)']"
            + child_stdout + ")\n"
            "Path(os.environ['MEMUAI_TEST_PIDS']).write_text(json.dumps(child.pid))\n"
            "print('exited parent ready', flush=True)\n")
        ready = threading.Event()
        holder = {}

        def observe(job, text):
            if "ready" in text:
                pid = json.loads(pids_file.read_text())
                handle = self.kernel.OpenProcess(0x100000, False, pid)
                if not handle:
                    raise ctypes.WinError(ctypes.get_last_error())
                self.handles.append((pid, handle))
                with self.agent._lock:
                    holder["shell"] = self.agent.jobs[job]
                ready.set()

        def execute():
            try:
                holder["result"] = self.agent.exec({
                    "command": command, "cwd": str(self.root), "confirmed": True,
                    "job": "exited-parent", "timeout": 12 if cancel or detached_output else 2,
                    "env": {"MEMUAI_TEST_PIDS": str(pids_file)},
                }, on_chunk=observe)
            except BaseException as exc:
                holder["error"] = exc

        thread = threading.Thread(target=execute, daemon=True)
        thread.start()
        try:
            self.assertTrue(ready.wait(5), holder)
            deadline = time.monotonic() + 3
            while holder["shell"].poll() is None and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(holder["shell"].poll(), 0, holder)
            if cancel and not detached_output:
                self.assertTrue(self.agent.exec_cancel({"job": "exited-parent"})["ok"])
            thread.join(5)
            self.assertFalse(thread.is_alive(), "descendant retained stdout after its shell exited")
            self.assertNotIn("error", holder, holder)
            self.assertEqual(holder["result"]["status"], "ok" if detached_output else "cancelled" if cancel else "timeout")
            self.assertIn("exited parent ready", holder["result"]["output"])
            self.assertFalse(self.agent.jobs)
            for pid, handle in self.handles:
                if detached_output:
                    self.assertEqual(self.kernel.WaitForSingleObject(handle, 0), 258,
                                     "normal completion killed an intentionally detached child")
                else:
                    self.assertEqual(self.kernel.WaitForSingleObject(handle, 1000), 0,
                                     f"owned detached descendant {pid} survived cancellation")
        finally:
            self.cleanup_owned_children()
            thread.join(5)

    def test_timeout_stops_child_even_when_shell_and_immediate_parent_have_exited(self):
        self.check_exited_parent_stop(cancel=False)

    def test_cancel_stops_child_even_when_shell_and_immediate_parent_have_exited(self):
        self.check_exited_parent_stop(cancel=True)

    def test_normal_completion_preserves_intentionally_detached_child(self):
        self.check_exited_parent_stop(cancel=False, detached_output=True)

    def test_launch_failure_stops_suspended_process_and_closes_all_resources(self):
        marker = self.root / "must-not-run.txt"
        command = self.command("from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('ran')\n")
        original_popen = A.subprocess.Popen
        for assigned in (False, True):
            with self.subTest(assigned=assigned):
                captured = []

                def launch(*args, **kwargs):
                    process = original_popen(*args, **kwargs)
                    captured.append(process)
                    return process

                def fail(job, process):
                    if assigned:
                        self.assertTrue(job._kernel.AssignProcessToJobObject(job._handle, int(process._handle)))
                    raise OSError("injected binding/resume failure")

                with patch.object(A.subprocess, "Popen", side_effect=launch), \
                     patch.object(A._WindowsJob, "bind_and_resume", fail):
                    with self.assertRaisesRegex(OSError, "injected"):
                        self.agent.exec({"command": command, "cwd": str(self.root),
                                         "confirmed": True, "job": "failed-launch"})
                self.assertEqual(len(captured), 1)
                process = captured[0]
                self.assertIsNotNone(process.poll())
                self.assertTrue(process.stdout.closed)
                self.assertIsNone(process._memu_windows_job._handle)
                self.assertFalse(marker.exists())
                self.assertFalse(self.agent.jobs)


if __name__ == "__main__":
    unittest.main()
