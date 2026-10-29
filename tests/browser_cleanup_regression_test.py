"""Browser cancellation and child cleanup use owned process identities only."""
from __future__ import annotations

import asyncio
import subprocess
from unittest.mock import patch

from app import automation_runtime as runtime


def test_browser_extra_arguments_preserve_quoted_values(tmp_path, monkeypatch):
    argv = ["--host-resolver-rules=MAP example.test 127.0.0.1", "--disable-gpu"]
    value = subprocess.list2cmdline(argv) if runtime.os.name == "nt" else "'--host-resolver-rules=MAP example.test 127.0.0.1' --disable-gpu"
    monkeypatch.setenv("MEMUAI_BROWSER_ARGS", value)
    captured = []
    def stop_launch(command, **_kwargs):
        captured.extend(command)
        raise RuntimeError("test stopped before browser launch")
    async def check():
        edge = runtime.EdgeAutomation()
        with patch.object(edge, "_new_profile", return_value=tmp_path), \
                patch.object(runtime, "_edge_executable", return_value="test-edge"), \
                patch.object(runtime.subprocess, "Popen", side_effect=stop_launch):
            try:
                await edge.start()
            except RuntimeError as exc:
                assert str(exc) == "test stopped before browser launch"
            else:
                raise AssertionError("the mocked launch was not called")
        assert all(arg in captured for arg in argv), captured
        assert "example.test" not in captured
    asyncio.run(check())


def test_cancelled_receiver_still_cleans_profile_and_pending_commands(tmp_path):
    class Socket:
        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.Event().wait()

        async def close(self):
            self.closed = True

    async def check():
        edge = runtime.EdgeAutomation()
        socket = Socket()
        profile = tmp_path / "session-test"
        profile.mkdir()
        edge.profile, edge.socket = profile, socket
        future = asyncio.get_running_loop().create_future()
        edge._pending[1] = future
        receiver = asyncio.create_task(edge._receive(socket))
        edge._receiver = receiver
        await asyncio.sleep(0)
        receiver.cancel()
        await asyncio.gather(receiver, return_exceptions=True)
        assert edge._cleanup_task is not None
        await edge.close_browser()
        assert edge.socket is None and socket.closed and not profile.exists()
        assert not edge._pending
        assert "closed" in str(future.exception()).lower() or "закрыто" in str(future.exception()).lower()

    asyncio.run(check())


def test_descendant_identities_are_captured_before_graceful_parent_exit():
    events = []
    owned_children = [object()]

    class Process:
        pid = 123
        alive = True

        def poll(self):
            return None if self.alive else 0

    class Socket:
        async def close(self):
            events.append("socket closed")

    async def check():
        edge = runtime.EdgeAutomation()
        process = Process()
        edge.process, edge.socket = process, Socket()

        async def command(*_args, **_kwargs):
            events.append("browser close")
            process.alive = False
            return {}

        edge.command = command
        with patch.object(runtime, "_browser_children", side_effect=lambda captured: events.append("captured") or owned_children), \
                patch.object(runtime, "_reap_browser_children", side_effect=lambda children: events.append(("reaped", children))):
            await edge.close_browser()
        assert events.index("captured") < events.index("browser close")
        assert ("reaped", owned_children) in events
        assert edge.process is None

    asyncio.run(check())
