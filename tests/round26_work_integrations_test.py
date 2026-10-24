"""Round 26 regressions: Work, MCP, integrations, scheduler, Deep Research (r26-work-integrations)."""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import mcp_client

DYING_SERVER = '''import json, sys
for line in sys.stdin:
    msg = json.loads(line)
    method, ident = msg.get("method"), msg.get("id")
    if method == "initialize":
        result = {"capabilities": {}}
    elif method == "tools/list":
        result = {"tools": [{"name": "echo"}, {"name": "die"}]}
    elif method == "tools/call":
        if msg["params"]["name"] == "die":
            sys.exit(1)
        result = {"content": [{"type": "text", "text": "ok"}]}
    else:
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": ident, "result": result}), flush=True)
'''


class MCPRegressions(unittest.TestCase):
    def setUp(self):
        self.sandbox = tempfile.TemporaryDirectory(prefix="memuai-r26-mcp-")
        self.addCleanup(self.sandbox.cleanup)
        patcher = patch.object(mcp_client, "CONFIG_PATH", Path(self.sandbox.name) / "mcp.json")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_crashed_server_restarts_on_next_call(self):
        """The pipe closes before asyncio sets returncode: the dead server looked alive and every
        later call failed with «сервер закрыл поток» until MemuAI restarted."""
        mcp_client.save_config({"servers": {"fake": {"command": sys.executable, "args": ["-u", "-c", DYING_SERVER]}}})

        async def scenario():
            registry = mcp_client.MCPRegistry()
            try:
                with self.assertRaises(RuntimeError):
                    await registry.call("fake", "die", {})
                return await registry.call("fake", "echo", {})
            finally:
                await registry.stop_all()

        self.assertEqual(asyncio.run(scenario())["content"], "ok")

    def test_panel_save_keeps_env_and_cwd(self):
        """The MCP panel never sees ``env`` (tokens) and posts the whole list: a toggle wiped every token."""
        mcp_client.save_config({"servers": {
            "gh": {"command": "npx", "args": ["x"], "env": {"GITHUB_TOKEN": "t"}, "cwd": "/tmp"},
            "other": {"command": "y", "env": {"A": "1"}}}})
        mcp_client.save_config({"servers": {"gh": {"command": "npx", "args": ["x"], "enabled": False},
                                            "other": {"command": "y", "args": []}}})
        servers = mcp_client.load_config()["servers"]
        self.assertEqual(servers["gh"]["env"], {"GITHUB_TOKEN": "t"})
        self.assertEqual(servers["gh"]["cwd"], "/tmp")
        self.assertFalse(servers["gh"]["enabled"])
        self.assertEqual(servers["other"]["env"], {"A": "1"})
        # An explicit env still replaces the saved one (CLI «mcp add -e»).
        mcp_client.save_config({"servers": {"gh": {"command": "npx", "env": {}}}})
        self.assertEqual(mcp_client.load_config()["servers"]["gh"]["env"], {})


class WorkDeleteEndpoint(unittest.TestCase):
    def test_deleting_a_link_keeps_its_target(self):
        """«memuai-delete» of the link «lib → src» went through ``/api/work/delete``, which resolved the
        link and ran ``rmtree`` on ``src`` (the work_delete tool was fixed in Round 25, the endpoint was not)."""
        from fastapi.testclient import TestClient
        from app import code_checkpoints, pgdata
        from app.playground_api import create_app
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = base / "project"
            (project / "src").mkdir(parents=True)
            (project / "src" / "main.py").write_text("print(1)", encoding="utf-8")
            outside = base / "outside"
            outside.mkdir()
            (outside / "keep.txt").write_text("keep", encoding="utf-8")
            try:
                (project / "lib").symlink_to(project / "src", target_is_directory=True)
                (project / "out").symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are not available")
            with patch.object(pgdata, "MEMU_DIR", base / "data"), \
                    patch.object(code_checkpoints, "CHECKPOINT_ROOT", base / "checkpoints"):
                pgdata.save_workspaces({"active_id": "p", "items": [
                    {"id": "p", "name": "P", "path": str(project), "added": 1}]})
                with TestClient(create_app(), base_url="http://127.0.0.1") as client:
                    for name in ("lib", "out"):
                        response = client.post("/api/work/delete", json={"project_id": "p", "session": "s", "path": name})
                        self.assertEqual(response.status_code, 200, response.text)
                    escape = client.post("/api/work/delete", json={"project_id": "p", "path": "../outside"})
                    self.assertEqual(escape.status_code, 400)
                    root = client.post("/api/work/delete", json={"project_id": "p", "path": "."})
                    self.assertEqual(root.status_code, 400)
            self.assertFalse((project / "lib").is_symlink())
            self.assertFalse((project / "out").is_symlink())
            self.assertTrue((project / "src" / "main.py").is_file())
            self.assertTrue((outside / "keep.txt").is_file())


class DecisionJson(unittest.TestCase):
    def test_braces_around_the_answer_do_not_lose_it(self):
        """«model» decisions: a reasoning model's ``<ds_safety>{…}</ds_safety>`` after the JSON (or ``{x}``
        before it) made the whole answer unreadable — DecisionsError and no judgement at all."""
        from app.decisions import _extract_json
        self.assertEqual(_extract_json('{"answers": {"a": 1}}\n<ds_safety>{"risk": "none"}</ds_safety>'),
                         {"answers": {"a": 1}})
        self.assertEqual(_extract_json('<ds_safety>{x}</ds_safety> {"answers": {"a": 1},}'), {"answers": {"a": 1}})
        self.assertIsNone(_extract_json("no json {here"))


class ScheduledReasoning(unittest.TestCase):
    def test_think_block_is_not_delivered_or_applied(self):
        """A provider in «think tags» mode streams reasoning as content: it went to Telegram/email and a
        ``memuai-work`` draft inside the reasoning was written into the project."""
        from unittest.mock import AsyncMock
        from app import desktop_notifications, scheduler
        from app.chat import chat_service
        answer = ("<think>\nDraft:\n```memuai-work\n{\"path\": \"draft.txt\"}\nwrong\n```\n</think>\n\n"
                  "```memuai-work\n{\"path\": \"final.txt\"}\nright\n```\nDone.")

        def stream_completion(_model, _messages):
            async def chunks():
                for start in range(0, len(answer), 7):
                    yield {"choices": [{"delta": {"content": answer[start:start + 7]}}]}
            return chunks()

        records, sent = [], AsyncMock()
        with tempfile.TemporaryDirectory() as temp:
            project = {"id": "p", "name": "P", "path": temp}

            async def scenario():
                with patch.object(chat_service, "stream_completion", stream_completion), \
                        patch.object(scheduler, "_work_project", return_value=project), \
                        patch.object(scheduler, "append_schedule_result",
                                     lambda task_id, ok, text, extra=None: records.append((ok, text, extra))), \
                        patch.object(scheduler, "load_preferences", return_value={"notify_windows": False}), \
                        patch.object(desktop_notifications, "show_completion"), \
                        patch("app.telegram_bot.send_notification", new=sent):
                    await scheduler._run_task({"id": "t", "name": "T", "model": "m", "mode": "work",
                                               "project_id": "p", "access_mode": "full"})

            asyncio.run(scenario())
            self.assertFalse((Path(temp) / "draft.txt").exists())
            self.assertEqual((Path(temp) / "final.txt").read_text(encoding="utf-8"), "right\n")
        ok, text, extra = records[0]
        self.assertTrue(ok)
        self.assertNotIn("Draft", text)
        self.assertEqual(extra["work_files"], ["final.txt"])
        self.assertNotIn("<think>", sent.await_args.args[1])


class PausedSchedule(unittest.TestCase):
    def test_reenabled_daily_task_waits_for_its_next_slot(self):
        """A daily 09:00 task paused on Monday and switched on again on Friday afternoon ran immediately
        (the stale Monday slot was kept while it was off)."""
        from app import pgdata, scheduler
        with tempfile.TemporaryDirectory() as temp, patch.object(pgdata, "MEMU_DIR", Path(temp)):
            monday = 1_790_000_000.0
            pgdata.save_schedules([{"id": "d", "name": "D", "prompt": "p", "model": "m", "daily_at": "09:00",
                                    "enabled": True}])
            asyncio.run(scheduler._scheduler_tick(now=monday))             # schedules the next 09:00
            tasks = pgdata.load_schedules()
            tasks[0]["enabled"] = False
            pgdata.save_schedules(tasks)
            asyncio.run(scheduler._scheduler_tick(now=monday + 3600))
            tasks = pgdata.load_schedules()
            tasks[0]["enabled"] = True
            pgdata.save_schedules(tasks)
            friday = monday + 4 * 86400 + 3 * 3600
            with patch.object(scheduler, "_run_task") as run:
                started = asyncio.run(scheduler._scheduler_tick(now=friday))
            self.assertEqual(started, 0)
            run.assert_not_called()
            self.assertGreater(pgdata.load_schedules()[0]["next_run"], friday)


if __name__ == "__main__":
    unittest.main()
