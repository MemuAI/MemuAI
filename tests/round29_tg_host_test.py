"""Round 29: MemuCode «remote host» — the full interactive MemuCode driven only from the Mini App.

The real TerminalUI runs with a pipe as the keyboard and a dummy screen; the phone's commands go through
the remote-control inbox exactly like for an open terminal: slash commands work (``/help`` used to be sent
to the model as plain text by the headless turn), notices are published as ``notes``, the command catalogue
is written for auto-complete, and ``close`` ends a host (but never a real terminal)."""
from __future__ import annotations

import asyncio
import json
import time
import unittest

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from tests.memucode_cli_test import make_runtime, setUpModule, tearDownModule  # noqa: F401  (module fixtures)
from app.cli import paths, remote

def _enable_tgapp():
    target = paths.memu_dir() / "tgapp.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"enabled": True, "code_control": True}), encoding="utf-8")

def drive_host(runtime, drive, *, host=True):
    from app.cli.tui.app import TerminalUI

    async def main():
        runtime.settings.overrides["onboarded"] = True
        ui = TerminalUI(runtime)
        if host:
            ui._remote_host = {"nonce": "n-test", "idle_minutes": 30, "last": time.monotonic()}
        with create_pipe_input() as keys:
            async def player():
                for _ in range(100):
                    await asyncio.sleep(0.05)
                    if getattr(ui, "remote_control", None) and ui.remote_control.registered:
                        break
                try:
                    return await drive(ui, ui.remote_control)
                finally:
                    ui.exit()

            async def after_start(terminal):
                terminal.remote_control = remote.attach(terminal)

            task = asyncio.ensure_future(player())
            await ui.run(input=keys, output=DummyOutput(), after_start=after_start)
            outcome = await task
        await remote.detach(ui.remote_control)
        await runtime.shutdown()
        return ui, outcome

    return asyncio.run(main())

async def wait_for(check, seconds=6.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        await asyncio.sleep(0.1)
    return check()

class RemoteHost(unittest.TestCase):
    def setUp(self):
        _enable_tgapp()

    def test_slash_commands_run_as_commands_and_their_output_reaches_the_phone(self):
        runtime, backend = make_runtime(turns=["Hello from the model."])

        async def drive(ui, control):
            remote.send_command(control.id, {"type": "prompt", "text": "/version"})
            state = await wait_for(lambda: (lambda s: s if s and any("MemuCode" in n["text"] for n in s.get("notes") or []) else None)(
                remote.terminal_state(control.id)))

            remote.send_command(control.id, {"type": "prompt", "text": "/help"})
            helped = await wait_for(lambda: (lambda s: s if s and (s.get("dialog") or {}).get("title") else None)(
                remote.terminal_state(control.id)))
            self.assertIn("help", helped["dialog"]["title"].lower())
            catalog = await wait_for(lambda: (control.folder / f"{control.id}.commands.json").is_file())
            data = json.loads((control.folder / f"{control.id}.commands.json").read_text(encoding="utf-8")) if catalog else {}
            return state, data

        ui, (state, catalog) = drive_host(runtime, drive)
        self.assertIsNotNone(state, "the /version output was not published")
        self.assertTrue(state.get("host"))
        self.assertEqual(state.get("host_nonce"), "n-test")
        self.assertEqual(backend.requests, [], "slash commands must never be sent to the model")
        names = {row["name"] for row in catalog.get("commands") or []}
        self.assertTrue({"help", "model", "compact", "clear"} <= names)
        self.assertIn("test-model", catalog.get("models") or [])
        seqs = [n["seq"] for n in state["notes"]]
        self.assertEqual(seqs, sorted(seqs))

    def test_close_ends_a_host(self):
        runtime, _backend = make_runtime()

        async def drive(ui, control):
            remote.send_command(control.id, {"type": "close"})
            return await wait_for(lambda: not ui.app.is_running, 5)

        _ui, closed = drive_host(runtime, drive)
        self.assertTrue(closed)

    def test_close_is_refused_for_a_real_terminal(self):
        runtime, _backend = make_runtime()

        async def drive(ui, control):
            command_id = remote.send_command(control.id, {"type": "close"})
            state = await wait_for(lambda: (lambda s: s if s and any(a["id"] == command_id for a in s.get("acks") or []) else None)(
                remote.terminal_state(control.id)))
            return state, command_id

        ui, (state, command_id) = drive_host(runtime, drive, host=False)
        ack = next(a for a in state["acks"] if a["id"] == command_id)
        self.assertFalse(ack["ok"])
        self.assertFalse(state.get("host"))

if __name__ == "__main__":
    unittest.main()
