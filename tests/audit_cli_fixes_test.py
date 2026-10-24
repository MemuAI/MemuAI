"""Regression tests for the October 2026 audit of MemuCode (app/cli and the Code backend).

Each test pins one fixed bug; they run offline and never touch the real data folder.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from app import code_sandbox  # noqa: E402
from app import cli_integrations  # noqa: E402
from app.cli import compact, paths, tokens  # noqa: E402
from app.cli import session as session_module  # noqa: E402
from app.cli.llm import ChatRequest, OpenAIBackend  # noqa: E402
from app.cli.session import Session  # noqa: E402
from app.cli.tools import mcp as mcp_tools  # noqa: E402
from app.code_sandbox import NewlineJoiner  # noqa: E402


class _TempSessions(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.project = base / "project"
        self.project.mkdir()
        store = base / "sessions"
        store.mkdir()
        self.patches = [patch.object(paths, "project_sessions", lambda root=None: store / "p"),
                        patch.object(paths, "sessions_root", lambda: store)]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for item in self.patches:
            item.stop()
        self.tmp.cleanup()


class SessionPersistence(_TempSessions):
    def test_a_line_cut_by_a_crash_does_not_swallow_the_next_event(self):
        session = Session(self.project)
        session.append({"role": "user", "kind": "prompt", "content": "first"})
        with session.path.open("ab") as handle:            # a crash in the middle of a line
            handle.write('{"t": "msg", "m": {"role": "assistant", "content": "обор'.encode("utf-8")[:-1])
        session.append({"role": "user", "kind": "prompt", "content": "second"})
        loaded = Session.load(self.project, session.id)
        self.assertEqual([m["content"] for m in loaded.messages], ["first", "second"])

    def test_cleanup_keeps_the_open_session(self):
        session = Session(self.project)
        session.append({"role": "user", "kind": "prompt", "content": "old work"})
        old = time.time() - 100 * 86400
        os.utime(session.path, (old, old))
        self.assertEqual(session_module.cleanup(30, keep={session.id}), 0)
        self.assertTrue(session.path.exists())
        self.assertEqual(session_module.cleanup(30), 1)


class ContextAccounting(unittest.TestCase):
    def test_an_elided_result_costs_only_its_stub(self):
        body = {"role": "tool", "content": "x" * 40_000}
        full = tokens.estimate_message(body)
        stub = tokens.estimate_message({**body, "elided": True})
        # Round 31: the Round 30 calibrated estimate counts a run of one repeated
        # character at ~12 chars/token (≈3.3k here), as real BPE tokenizers do.
        self.assertGreater(full, 2000)
        self.assertLess(stub, 50)

    def test_one_long_autonomous_turn_can_still_be_compacted(self):
        messages = [{"id": "u", "role": "user", "kind": "prompt", "content": "do it"}]
        for index in range(40):
            messages.append({"id": f"a{index}", "role": "assistant", "content": "",
                             "tool_calls": [{"id": f"c{index}", "name": "Read", "args": {}}]})
            messages.append({"id": f"t{index}", "role": "tool", "tool_call_id": f"c{index}", "content": "x"})
        cut = compact._cut_index(messages, 8)
        self.assertGreater(cut, 0)
        self.assertEqual(messages[cut]["role"], "assistant")      # never between a call and its result

    def test_the_tail_kept_by_an_earlier_summary_is_not_dropped(self):
        messages = [{"id": "s", "role": "user", "kind": "summary", "content": "earlier"},
                    {"id": "u", "role": "user", "kind": "prompt", "content": "next"},
                    {"id": "a", "role": "assistant", "content": "ok"}]
        cut = compact._cut_index(messages, 8)
        self.assertNotEqual(cut, 1)                               # covering only the old summary


class ShellOutput(unittest.TestCase):
    def test_crlf_split_between_chunks(self):
        lines = NewlineJoiner()
        text = lines.feed("one\r") + lines.feed("\ntwo\r\n") + lines.feed("", final=True)
        self.assertEqual(text, "one\ntwo\n")

    def test_progress_carriage_return_survives_the_end(self):
        lines = NewlineJoiner()
        self.assertEqual(lines.feed("50%\r") + lines.feed("", final=True), "50%\r")


@unittest.skipIf(sys.platform == "win32", "POSIX process groups")
class ProcessTrees(unittest.TestCase):
    def test_run_bounded_does_not_wait_for_a_grandchild_holding_the_pipe(self):
        started = time.monotonic()
        code, _out, _err, timed_out = code_sandbox.run_bounded(["/bin/sh", "-c", "sleep 30 & sleep 30"],
                                                               timeout=0.5)
        self.assertTrue(timed_out)
        self.assertIsNone(code)
        self.assertLess(time.monotonic() - started, 10)

    def test_kill_tree_reaches_children_of_a_finished_shell(self):
        import subprocess
        marker = tempfile.NamedTemporaryFile(delete=False)
        marker.close()
        process = subprocess.Popen(["/bin/sh", "-c", f"(sleep 1; echo alive > {marker.name}) &"],
                                   start_new_session=True)
        process.wait(5)
        code_sandbox.kill_tree(process)
        time.sleep(1.5)
        try:
            self.assertEqual(Path(marker.name).read_text(), "")
        finally:
            os.unlink(marker.name)


class EncodingOfConfigFiles(unittest.TestCase):
    def test_project_mcp_json_with_a_bom_is_read(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, ".mcp.json").write_bytes(
                b"\xef\xbb\xbf" + json.dumps({"mcpServers": {"x": {"command": "node"}}}).encode())
            self.assertIn("x", mcp_tools.load_project_servers(Path(folder)))

    def test_claude_settings_with_a_bom_are_valid_json(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "settings.json")
            path.write_bytes(b"\xef\xbb\xbf{\"theme\": \"dark\"}")
            data, existed = cli_integrations._read_json_file(str(path))
            self.assertTrue(existed)
            self.assertEqual(data, {"theme": "dark"})


class OpenAIToolCalls(unittest.TestCase):
    def test_parallel_calls_that_all_say_index_0_stay_separate(self):
        chunks = [
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "a", "function": {"name": "Read", "arguments": "{\"file_path\": \"a.py\"}"}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "b", "function": {"name": "Read", "arguments": "{\"file_path\":"}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": " \"b.py\"}"}}]}, "finish_reason": "tool_calls"}]},
        ]
        body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"

        def handler(_request):
            return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

        backend = OpenAIBackend({"base_url": "http://test.invalid/v1"})
        backend._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        async def collect():
            calls = []
            async for event in backend.stream(ChatRequest(model="m", system="", messages=[])):
                if event.kind == "tool_call":
                    calls.append(event.call)
            await backend._client.aclose()
            return calls

        calls = asyncio.run(collect())
        self.assertEqual([(c["id"], c["args"]) for c in calls],
                         [("a", {"file_path": "a.py"}), ("b", {"file_path": "b.py"})])


if __name__ == "__main__":
    unittest.main()
