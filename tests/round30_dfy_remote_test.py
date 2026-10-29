"""Round 30 — DFY on a remote machine (item 1a): MemuAI Remote Agent, transports, the machine loop.

A real agent process (``app/dfy_agent.py serve``) is started on a free local port inside a
temporary directory; MemuAI talks to it over HTTP with HMAC signatures. SSH is checked with a
stand-in ``ssh`` binary that runs the remote command locally. No network beyond 127.0.0.1.
"""
import asyncio
import json
import os
import platform
import re
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager, ExitStack
from pathlib import Path
from unittest.mock import patch

from app import dfy_agent as A
from app.dfy_remote import (HttpTransport, LocalTransport, MachineStore, RemoteMachines, SshTransport,
                            action_params, summarize)
from app.dfy_vault import SecretVault

ROOT = Path(__file__).resolve().parents[1]

def python_command(root: Path, source: str, *, confirmation=False):
    """Only a uniquely named helper owned by this temporary fixture is executed."""
    script = root / ("helper-%d.py" % time.monotonic_ns())
    script.write_text(source, encoding="utf-8")
    # A venv inside MemuAI is itself protected from destructive shell commands;
    # these standard-library-only helpers use its external base interpreter.
    args = [getattr(sys, "_base_executable", sys.executable), "-u", str(script)]
    if confirmation:
        # This inert argv label exercises the real command confirmation gate.
        args.append("rmdir /s")
    return subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)


def cleanup_command(root: Path, target: Path):
    assert target.parent.resolve() == root.resolve() and target != root
    return python_command(root, "from pathlib import Path\nimport shutil\n"
                          f"root = Path({str(root)!r}).resolve()\n"
                          f"target = Path({str(target)!r}).resolve()\n"
                          "assert target.parent == root and target != root\n"
                          "shutil.rmtree(target)\n", confirmation=True)


@contextmanager
def no_mutation():
    """A failed negative assertion must never reach a filesystem/process mutator."""
    with ExitStack() as stack:
        for target in ("pathlib.Path.open", "pathlib.Path.mkdir", "pathlib.Path.unlink",
                       "pathlib.Path.rmdir", "shutil.rmtree", "shutil.move",
                       "subprocess.Popen", "subprocess.run", "os.kill"):
            stack.enter_context(patch(target, side_effect=AssertionError("mutation reached: " + target)))
        yield

def start_agent(tmp: Path, *extra: str):
    """Real agent on 127.0.0.1:<free port>; returns (process, pairing code)."""
    env = dict(os.environ, MEMUAI_AGENT_HOME=str(tmp / "agent-home"), PYTHONUNBUFFERED="1")
    script = tmp / "memuai_remote_agent.py"
    script.write_text((ROOT / "app" / "dfy_agent.py").read_text("utf-8"), "utf-8")
    process = subprocess.Popen([sys.executable, str(script), "serve", "--bind", "127.0.0.1", "--port", "0",
                                "--advertise", "127.0.0.1", *extra], stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, env=env, cwd=str(tmp))
    code = ""
    deadline = time.time() + 15
    while time.time() < deadline and not code:
        line = process.stdout.readline()
        if not line:
            break
        match = re.search(r"(MEMU1-[A-Za-z0-9_-]+)", line)
        if match:
            code = match.group(1)
    if not code:
        process.kill()
        raise RuntimeError("agent did not print a pairing code")
    return process, code, script

class GuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.app = self.root / "MemuAI"
        (self.app / "data").mkdir(parents=True)
        (self.app / "data" / "chats.json").write_text("{}")
        self.guard = A.Guard(protect=[str(self.app)])
        self.agent = A.Agent(self.guard, state=self.root / "state")

    def tearDown(self):
        self.tmp.cleanup()

    def test_command_levels(self):
        levels = {cmd: self.guard.classify(cmd)["level"] for cmd in [
            "ls -la", "cat /etc/os-release", "rm -rf /", "sudo rm -rf --no-preserve-root /", ":(){ :|:& };:",
            "rm -rf /tmp/build", "shutdown -h now", "apt-get remove -y nginx", "mkfs.ext4 /dev/sdb1",
            "dd if=/dev/zero of=/dev/sda", f"rm -rf {self.app}", f"echo x > {self.app}/data/chats.json",
            "pkill -f memuai", "systemctl stop memuai-agent", "git reset --hard"]}
        self.assertEqual(levels["ls -la"], "safe")
        self.assertEqual(levels["cat /etc/os-release"], "safe")
        for fatal in ("rm -rf /", "sudo rm -rf --no-preserve-root /", ":(){ :|:& };:", "mkfs.ext4 /dev/sdb1",
                      "dd if=/dev/zero of=/dev/sda", f"rm -rf {self.app}", f"echo x > {self.app}/data/chats.json",
                      "pkill -f memuai", "systemctl stop memuai-agent"):
            self.assertEqual(levels[fatal], "blocked", fatal)
        for risky in ("rm -rf /tmp/build", "shutdown -h now", "apt-get remove -y nginx", "git reset --hard"):
            self.assertEqual(levels[risky], "confirm", risky)

    def test_self_protection_resolves_symlinks_and_parents(self):
        system = os.environ.get("WINDIR", r"C:\Windows") if os.name == "nt" else "/etc"
        for op, params in [("fs_delete", {"path": str(self.root), "recursive": True, "confirmed": True}),
                           ("fs_write", {"path": str(self.app / "data" / "chats.json"), "text": "", "confirmed": True}),
                           ("fs_move", {"source": str(self.app), "target": str(self.root / "x"), "confirmed": True}),
                           ("fs_write", {"path": A.__file__, "text": "#", "confirmed": True}),
                           ("fs_delete", {"path": str(Path(system).anchor), "recursive": True, "confirmed": True}),
                           ("fs_delete", {"path": system, "recursive": True, "confirmed": True}),
                           ("exec", {"command": f"rm -rf {self.app}/", "confirmed": True}),
                           ("proc_kill", {"pid": os.getpid(), "confirmed": True})]:
            with no_mutation(), self.assertRaises(A.Refused, msg=op) as caught:
                self.agent.call(op, params)
            self.assertNotIsInstance(caught.exception, A.NeedsConfirm, op)
        self.assertTrue((self.app / "data" / "chats.json").exists())

    def test_self_protection_resolves_symlink(self):
        link = self.root / "innocent"
        try:
            link.symlink_to(self.app / "data", target_is_directory=True)
        except OSError as exc:
            if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
                self.skipTest("Windows account lacks symlink privilege")
            raise
        for op, params in [("fs_delete", {"path": str(link / "chats.json"), "confirmed": True}),
                           ("fs_write", {"path": str(link / "chats.json"), "text": "", "confirmed": True}),
                           ("exec", {"command": f"rm -rf {link}/", "confirmed": True})]:
            with no_mutation(), self.assertRaises(A.Refused):
                self.agent.call(op, params)

    def test_confirmation_and_ops(self):
        work = self.root / "work"
        result = self.agent.call("fs_write", {"path": str(work / "a.txt"), "text": "one\n"})
        self.assertTrue(result["created"])
        with self.assertRaises(A.NeedsConfirm):
            self.agent.call("fs_write", {"path": str(work / "a.txt"), "text": "two"})
        self.agent.call("fs_write", {"path": str(work / "a.txt"), "text": "two\n", "append": True})
        self.assertEqual(self.agent.call("fs_read", {"path": str(work / "a.txt")})["text"], "one\ntwo\n")
        self.agent.call("fs_move", {"source": str(work / "a.txt"), "target": str(work / "b.txt")})
        names = [e["name"] for e in self.agent.call("fs_list", {"path": str(work)})["entries"]]
        self.assertEqual(names, ["b.txt"])
        with self.assertRaises(A.NeedsConfirm):
            self.agent.call("fs_delete", {"path": str(work / "b.txt")})
        self.agent.call("fs_delete", {"path": str(work / "b.txt"), "confirmed": True})
        self.assertFalse((work / "b.txt").exists())
        command = cleanup_command(self.root, work)
        with no_mutation(), self.assertRaises(A.NeedsConfirm):
            self.agent.call("exec", {"command": command, "cwd": str(self.root)})
        self.assertEqual(self.agent.call("exec", {"command": command, "cwd": str(self.root), "confirmed": True})["exit_code"], 0)
        self.assertFalse(work.exists())

    def test_roots_whitelist_only_narrows(self):
        allowed = self.root / "allowed"
        allowed.mkdir()
        agent = A.Agent(A.Guard(roots=[str(allowed)]), state=self.root / "state")
        agent.call("fs_write", {"path": str(allowed / "x"), "text": "1"})
        with self.assertRaises(A.Refused):
            agent.call("fs_read", {"path": "/etc/hostname"})
        with self.assertRaises(A.Refused):
            agent.call("fs_list", {"path": "/etc", "roots": ["/etc"]})
        self.assertEqual(agent.call("exec", {"command": "cd" if os.name == "nt" else "pwd", "roots": [str(allowed)]})["output"].strip(),
                         str(allowed.resolve()))

    def test_exec_timeout_truncation_and_cancel(self):
        command = python_command(self.root, "import time\nprint('start', flush=True)\ntime.sleep(5)\nprint('never')\n")
        result = self.agent.call("exec", {"command": command, "timeout": 1})
        self.assertEqual(result["status"], "timeout")
        self.assertIn("start", result["output"])
        self.assertLess(result["duration"], 4)
        small = A.Agent(self.guard, max_output=100, state=self.root / "state")
        big = small.call("exec", {"command": python_command(self.root, "print('line\\n' * 1000)\n")})
        self.assertTrue(big["truncated"])
        self.assertLessEqual(len(big["output"]), 100)
        import threading
        holder = {}
        slow = python_command(self.root, "import time\ntime.sleep(20)\n")
        thread = threading.Thread(target=lambda: holder.update(r=self.agent.call(
            "exec", {"command": slow, "job": "j1", "timeout": 30})))
        thread.start()
        time.sleep(0.5)
        self.assertTrue(self.agent.call("exec_cancel", {"job": "j1"})["ok"])
        thread.join(10)
        self.assertEqual(holder["r"]["status"], "cancelled")

    def test_signature_and_pairing_code(self):
        body = b'{"op":"ping"}'
        header = A.sign("tok", body)
        seen = set()
        self.assertTrue(A.verify("tok", body, header, seen))
        self.assertFalse(A.verify("tok", body, header, seen), "replay must fail")
        self.assertFalse(A.verify("other", body, A.sign("tok", body)))
        self.assertFalse(A.verify("tok", b"{}", A.sign("tok", body)))
        self.assertFalse(A.verify("tok", body, A.sign("tok", body, timestamp=int(time.time()) - 3600)))
        code = A.pairing_code("10.0.0.5", 8765, "secret-token", name="prod")
        parsed = A.parse_pairing_code(code)
        self.assertEqual((parsed["host"], parsed["port"], parsed["token"], parsed["name"]),
                         ("10.0.0.5", 8765, "secret-token", "prod"))
        with self.assertRaises(ValueError):
            A.parse_pairing_code("hello")

class LiveAgentTests(unittest.IsolatedAsyncioTestCase):
    """A real agent process over HTTP: streaming, files, confirmations, self-protection, bad tokens."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.process, self.code, self.script = start_agent(self.root)
        self.vault = SecretVault(self.root / "memu", use_dpapi=False)
        self.machines = RemoteMachines(self.root / "memu", self.vault)

    def tearDown(self):
        self.process.terminate()
        try:
            self.process.wait(5)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.process.stdout.close()
        self.tmp.cleanup()

    async def test_remote_machine_end_to_end(self):
        row = self.machines.store.add({"code": self.code, "name": "test-box"})
        stored = json.loads((self.root / "memu" / "dfy_machines.json").read_text())
        token = A.parse_pairing_code(self.code)["token"]
        self.assertNotIn(token, json.dumps(stored))
        self.assertNotIn("secret", json.dumps(self.machines.store.list()))
        info = await self.machines.probe(row["id"])
        self.assertEqual(info["agent_version"], A.AGENT_VERSION)
        self.assertTrue(self.machines.store.list()[0]["online"])

        chunks = []
        command = python_command(self.root, "import sys\nsys.stdout.buffer.write(b'line1\\nline2\\nline3\\n')\n")
        result = await self.machines.call(row["id"], "exec", {"command": command},
                                          on_chunk=chunks.append)
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual("".join(chunks), "line1\nline2\nline3\n")

        work = self.root / "srv"
        await self.machines.call(row["id"], "fs_write", {"path": str(work / "app.conf"), "text": "port=80\n"})
        read = await self.machines.call(row["id"], "fs_read", {"path": str(work / "app.conf")})
        self.assertEqual(read["text"], "port=80\n")
        with self.assertRaises(A.NeedsConfirm):
            await self.machines.call(row["id"], "fs_delete", {"path": str(work), "recursive": True})
        with self.assertRaises(A.NeedsConfirm):
            await self.machines.call(row["id"], "exec", {"command": cleanup_command(self.root, work)})

        # Source/process command classification is checked without executing a
        # destructive fallback if the guard regresses.
        guard = A.Guard(protect=[str(self.script)])
        for command in (f"rm -f {self.script}", "pkill -f memuai_remote_agent"):
            self.assertEqual(guard.classify(command)["level"], "blocked")
        for op, params in [("fs_delete", {"path": str(self.script), "confirmed": True}),
                           ("fs_delete", {"path": str(self.root / "agent-home"), "recursive": True, "confirmed": True})]:
            with self.assertRaises(A.Refused, msg=op):
                await self.machines.call(row["id"], op, params)
        self.assertTrue(self.script.exists())
        await self.machines.call(row["id"], "fs_delete", {"path": str(work), "recursive": True, "confirmed": True})
        self.assertFalse(work.exists())
        self.assertIsNone(self.process.poll())

    async def test_bad_token_is_rejected(self):
        parsed = A.parse_pairing_code(self.code)
        transport = HttpTransport({"host": "127.0.0.1", "port": parsed["port"]}, "wrong-token")
        with self.assertRaises(A.Refused):
            await transport.call("exec", {"command": "id"})

    async def test_cancel_running_command(self):
        row = self.machines.store.add({"code": self.code})
        command = python_command(self.root, "import time\ntime.sleep(30)\n")
        job = asyncio.create_task(self.machines.call(row["id"], "exec", {"command": command, "job": "slow"},
                                                     on_chunk=lambda _t: None))
        await asyncio.sleep(0.8)
        cancelled = await self.machines.call(row["id"], "exec_cancel", {"job": "slow"})
        self.assertTrue(cancelled["ok"])
        result = await asyncio.wait_for(job, 10)
        self.assertEqual(result["status"], "cancelled")

    async def test_remote_symlink_protection(self):
        try:
            (self.root / "alias").symlink_to(self.script)
        except OSError as exc:
            if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
                self.skipTest("Windows account lacks symlink privilege")
            raise
        row = self.machines.store.add({"code": self.code})
        with self.assertRaises(A.Refused):
            await self.machines.call(row["id"], "fs_delete", {"path": str(self.root / "alias"), "confirmed": True})

class SshTransportTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipIf(os.name == "nt", "POSIX SSH stand-in requires a local executable /bin/sh fixture")
    async def test_ssh_runs_agent_without_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = root / "ssh"

            fake.write_text("#!/bin/sh\nwhile [ $# -gt 1 ]; do shift; done\nexec sh -c \"$1\"\n")
            fake.chmod(0o755)
            os.environ["MEMUAI_SSH_BINARY"] = str(fake)
            os.environ["HOME_BACKUP"] = os.environ.get("HOME", "")
            os.environ["HOME"] = str(root)
            try:
                transport = SshTransport({"host": "example.org", "user": "deploy", "port": 22})
                command = transport.command()
                self.assertIn("deploy@example.org", command)
                self.assertIn("BatchMode=yes", command)
                chunks = []
                result = await transport.call("exec", {"command": "echo via-ssh"}, on_chunk=chunks.append)
                self.assertEqual(result["output"].strip(), "via-ssh")
                self.assertEqual("".join(chunks).strip(), "via-ssh")
                with self.assertRaises(A.NeedsConfirm):
                    await transport.call("exec", {"command": "rm -rf /tmp/whatever-r30"})
                with self.assertRaises(A.Refused):
                    await transport.call("exec", {"command": "rm -rf /", "confirmed": True})
                info = await transport.call("sysinfo", {})
                self.assertEqual(info["agent_version"], A.AGENT_VERSION)

                with_password = SshTransport({"host": "example.org", "user": "deploy"}, "pa ss'w0rd")
                self.assertNotIn("BatchMode=yes", with_password.command())
                env = with_password.askpass_env()
                self.assertEqual(env["SSH_ASKPASS_REQUIRE"], "force")
                self.assertNotIn("pa ss", Path(env["SSH_ASKPASS"]).read_text())
                printed = subprocess.run([env["SSH_ASKPASS"], "Password:"], env=env, capture_output=True, text=True).stdout
                self.assertEqual(printed, "pa ss'w0rd\n")
                self.assertEqual((await with_password.call("exec", {"command": "echo pw-ok"}))["output"].strip(), "pw-ok")
            finally:
                os.environ["HOME"] = os.environ.pop("HOME_BACKUP")
                os.environ.pop("MEMUAI_SSH_BINARY", None)

class ScriptedModel:
    def __init__(self, script):
        self.script = list(script)
        self.prompts = []

    async def __call__(self, model, messages):
        self.prompts.append(json.dumps(messages, ensure_ascii=False))
        item = self.script.pop(0) if self.script else {"action": "done", "result": "end"}
        if callable(item):
            item = item(messages)
        return json.dumps(item, ensure_ascii=False)

class MachineLoopTests(unittest.IsolatedAsyncioTestCase):
    """DFYManager with a target: shell/file actions, the danger confirmation, live output, refusal."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.prefs = {"dfy_enabled": True, "dfy_model": "any/model", "dfy_max_steps": 20, "dfy_local_system": True}

    def tearDown(self):
        self.tmp.cleanup()

    def manager(self, model):
        from app.dfy import DFYManager
        return DFYManager(model, lambda: dict(self.prefs), data_root=self.root / "memu",
                          vision_lookup=lambda _m: True, browser_factory=lambda _v: None,
                          vault=SecretVault(self.root / "memu", use_dpapi=False))

    async def wait(self, task, statuses, timeout=15.0):
        deadline = time.monotonic() + timeout
        while task.status not in statuses:
            if time.monotonic() > deadline:
                self.fail(f"status {task.status} never became {statuses}; steps={task.steps}")
            await asyncio.sleep(0.02)

    async def test_local_machine_task_with_confirmation(self):
        work = self.root / "proj"
        create = python_command(self.root, "from pathlib import Path\n"
                                f"p = Path({str(work)!r})\np.mkdir()\n"
                                "(p / 'a.txt').write_text('hello\\n')\nprint('a.txt')\n")
        cleanup = cleanup_command(self.root, work)
        model = ScriptedModel([
            {"action": "shell", "command": create},
            {"action": "file_read", "path": str(work / "a.txt")},
            {"action": "shell", "command": cleanup, "reason": "clean up"},
            {"action": "shell", "command": "rm -rf /"},
            {"action": "done", "result": "cleaned"},
        ])
        # The deliberately forbidden literal below is never allowed to reach
        # an OS shell, even if command classification itself regresses.
        original_popen = subprocess.Popen
        def safe_popen(args, *positional, **kwargs):
            if args == "rm -rf /":
                raise AssertionError("forbidden literal reached Popen")
            return original_popen(args, *positional, **kwargs)
        self.enterContext(patch.object(A.subprocess, "Popen", side_effect=safe_popen))
        mgr = self.manager(model)
        task = await mgr.create("сделай и убери", target="local")
        await self.wait(task, ("waiting_user",))
        self.assertEqual(task.question["kind"], "confirm")
        self.assertIn(cleanup, task.question["prompt"])
        self.assertTrue((work / "a.txt").exists())
        await mgr.answer(task.id, {"approve": True})
        await self.wait(task, ("done", "failed"))
        self.assertEqual(task.status, "done", task.error)
        self.assertFalse(work.exists())
        actions = [s["action"] for s in task.steps]
        self.assertEqual(actions[0], "sysinfo")
        first = task.steps[1]
        self.assertTrue(first["ok"], first)
        self.assertIn("a.txt", first["output"])
        refused = [s for s in task.steps if s["action"] == "shell" and "rm -rf /" == s["detail"][-8:].strip()] or \
            [s for s in task.steps if s["ok"] is False]
        self.assertTrue(refused, task.steps)
        self.assertIn("REFUSED", "".join(task.history))
        prompt = model.prompts[2]
        self.assertIn("hello", prompt)
        public = task.public()
        self.assertEqual(public["target"]["id"], "local")

    async def test_declined_danger_is_not_run_and_local_needs_setting(self):
        keep = self.root / "keep"
        keep.mkdir()
        model = ScriptedModel([{"action": "shell", "command": cleanup_command(self.root, keep)},
                               {"action": "done", "result": "ok"}])
        mgr = self.manager(model)
        task = await mgr.create("удали", target="local")
        await self.wait(task, ("waiting_user",))
        await mgr.answer(task.id, {"approve": False})
        await self.wait(task, ("done", "failed"))
        self.assertTrue(keep.exists())
        self.assertIn("declined", " ".join(task.answers))
        self.prefs["dfy_local_system"] = False
        with self.assertRaises(PermissionError):
            await mgr.create("x", target="local")
        with self.assertRaises(ValueError):
            await mgr.create("x", target="no-such-machine")

    async def test_cancel_stops_remote_command(self):
        command = python_command(self.root, "import time\nfrom pathlib import Path\ntime.sleep(30)\n"
                                 f"Path({str(self.root / 'late')!r}).write_text('late')\n")
        model = ScriptedModel([{"action": "shell", "command": command}])
        mgr = self.manager(model)
        task = await mgr.create("долго", target="local")
        deadline = time.monotonic() + 10
        while not any(s["action"] == "shell" for s in task.steps) and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.3)
        await mgr.cancel(task.id)
        self.assertEqual(task.status, "cancelled")
        await asyncio.sleep(0.5)
        self.assertFalse((self.root / "late").exists())

    async def test_remote_target_through_real_agent(self):
        process, code, _script = start_agent(self.root)
        try:
            mgr = self.manager(ScriptedModel([
                {"action": "sysinfo"},
                {"action": "file_write", "path": str(self.root / "remote.txt"), "text": "from dfy"},
                {"action": "done", "result": "written"}]))
            machine = mgr.machines.store.add({"code": code, "name": "box"})
            task = await mgr.create("запиши файл", target="box")
            await self.wait(task, ("done", "failed"))
            self.assertEqual(task.status, "done", task.error)
            self.assertEqual((self.root / "remote.txt").read_text(), "from dfy")
            self.assertEqual(task.public()["target"]["id"], machine["id"])
        finally:
            process.terminate()
            process.wait(5)
            process.stdout.close()

class HttpApiTests(unittest.TestCase):
    def test_machines_api(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.dfy import DFYManager
        from app.dfy_api import build_router
        from app.dfy_remote_api import build_router as build_remote
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            process, code, _ = start_agent(root)
            try:
                mgr = DFYManager(ScriptedModel([]), lambda: {"dfy_model": "m"}, data_root=root / "memu",
                                 vision_lookup=lambda _m: True, browser_factory=lambda _v: None,
                                 vault=SecretVault(root / "memu", use_dpapi=False))
                app = FastAPI()
                app.include_router(build_router(mgr))
                app.include_router(build_remote(mgr))
                with TestClient(app) as client:
                    self.assertEqual(client.post("/api/dfy/remote/machines", json={"code": "bad"}).status_code, 400)
                    added = client.post("/api/dfy/remote/machines", json={"code": code, "name": "prod"}).json()
                    machine = added["machine"]
                    self.assertTrue(machine["online"])
                    self.assertEqual(machine["os"], platform.system().lower())
                    listing = client.get("/api/dfy/remote/machines").json()
                    token = A.parse_pairing_code(code)["token"]
                    self.assertNotIn(token, json.dumps(listing))
                    self.assertEqual(listing["targets"][0]["id"], "local")
                    self.assertEqual(listing["targets"][1]["name"], "prod")
                    check = client.post(f"/api/dfy/remote/machines/{machine['id']}/check").json()
                    self.assertTrue(check["ok"])
                    status = client.get("/api/dfy/status").json()
                    self.assertEqual(status["machines"][0]["name"], "prod")
                    renamed = client.put(f"/api/dfy/remote/machines/{machine['id']}", json={"name": "web"}).json()
                    self.assertEqual(renamed["machine"]["name"], "web")
                    source = client.get("/api/dfy/remote/agent.py")
                    self.assertIn("MemuAI Remote Agent", source.text)
                    self.assertEqual(client.delete(f"/api/dfy/remote/machines/{machine['id']}").status_code, 200)
                    self.assertEqual(client.delete(f"/api/dfy/remote/machines/{machine['id']}").status_code, 404)
            finally:
                process.terminate()
                process.wait(5)
                process.stdout.close()

class SameHostProtectionTests(unittest.IsolatedAsyncioTestCase):
    """Round 30 incident: an agent started from a COPY outside MemuAI on the same host deleted MemuAI's app/
    after a user go-ahead. Now the agent recognises a MemuAI folder by itself and MemuAI sends its own paths."""

    async def test_copy_of_agent_never_deletes_memuai_on_its_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = root / "Apps" / "MemuAI"
            (fake / "app").mkdir(parents=True)
            (fake / "app" / "dfy_agent.py").write_text("# agent")
            (fake / "app" / "playground_api.py").write_text("# ui")
            (root / "junk").mkdir()
            (root / "agent").mkdir()
            process, code, _script = start_agent(root / "agent")
            try:
                agent = A.Agent(A.Guard(), state=root / "state")
                for path in (fake / "app", fake, root / "Apps"):
                    with self.assertRaises(A.Refused, msg=str(path)):
                        agent.call("fs_delete", {"path": str(path), "recursive": True, "confirmed": True})
                self.assertEqual(agent.guard.classify(f"rm -rf {fake}/app")["level"], "blocked")
                agent.call("fs_delete", {"path": str(root / "junk"), "recursive": True, "confirmed": True})
                self.assertFalse((root / "junk").exists())

                machines = RemoteMachines(root / "memu", SecretVault(root / "memu", use_dpapi=False),
                                          protect=[str(root / "keep")])
                (root / "keep").mkdir()
                row = machines.store.add({"code": code, "name": "same-host"})
                with self.assertRaises(A.Refused):
                    await machines.call(row["id"], "fs_delete", {"path": str(root / "keep"), "recursive": True,
                                                                 "confirmed": True})
                with self.assertRaises(A.Refused):
                    await machines.call(row["id"], "fs_delete", {"path": str(fake / "app"),
                                                                "recursive": True, "confirmed": True})
                self.assertTrue((root / "keep").exists() and (ROOT / "app" / "dfy.py").exists() and (fake / "app").exists())
            finally:
                process.terminate()
                process.wait(5)
                process.stdout.close()

    def test_install_command_and_proxy_env(self):
        from types import SimpleNamespace
        from app.dfy_remote import install_command, proxy_env
        cmd = install_command()
        self.assertTrue(cmd["paste"].startswith("echo H4sI"))
        self.assertIn("| base64 -d | gunzip > ~/memuai_remote_agent.py && python3 ~/memuai_remote_agent.py serve", cmd["paste"])
        self.assertIn("systemctl enable --now memuai-agent", cmd["systemd"])
        env = proxy_env(SimpleNamespace(scheme="socks5", host="px.example", port=1080, username="u ser", password="p@ss"))
        self.assertEqual(env["ALL_PROXY"], "socks5h://u%20ser:p%40ss@px.example:1080")
        self.assertIn("127.0.0.1", env["NO_PROXY"])
        self.assertEqual(proxy_env(None), {})

class HelpersTests(unittest.TestCase):
    def test_action_params_filter_and_summaries(self):
        params = action_params("shell", {"command": "ls", "cwd": "/srv", "evil": 1, "confirmed": True})
        self.assertEqual(params, {"command": "ls", "cwd": "/srv"})
        self.assertEqual(action_params("package", {"pkg_action": "install", "name": "htop"}),
                         {"name": "htop", "action": "install"})
        line, model = summarize("shell", {"command": "ls", "exit_code": 0, "status": "ok", "output": "a\nb\n"})
        self.assertIn("ls", line)
        self.assertIn("exit_code=0", model)

    def test_local_transport_protects_memuai(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            machines = RemoteMachines(root, SecretVault(root, use_dpapi=False))
            self.assertIsInstance(machines.local, LocalTransport)
            verdict = machines.local.agent.guard.classify(f"rm -rf {ROOT}/app")
            self.assertEqual(verdict["level"], "blocked")
            with no_mutation(), self.assertRaises(A.Refused):
                machines.local.agent.call("fs_delete", {"path": str(ROOT / "app" / "dfy.py"), "confirmed": True})

    def test_store_never_exposes_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = SecretVault(Path(tmp), use_dpapi=False)
            store = MachineStore(Path(tmp), vault)
            row = store.add({"kind": "ssh", "host": "srv.example", "user": "root", "password": "hunter2-long"})
            self.assertNotIn("hunter2", json.dumps(store.list()))
            self.assertNotIn("hunter2", (Path(tmp) / "dfy_machines.json").read_text())
            self.assertEqual(store.token(row["id"]), "hunter2-long")
            self.assertEqual(vault.list(), [])

if __name__ == "__main__":
    unittest.main()
