"""Round 46: browser selection, account catalogue and Telegram author attribution.

No real browser, remote machine, Telegram account or model request is used.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
import httpx

from app import browser_extension as extension
from app import dfy, dfy_api, flow_engine, schedule_rules_api
from app import telegram_use as use, telegram_use_ai as ai, telegram_use_config as config
from app.providers import antigravity_models as names
from tests.round24_dfy_test import FakeBrowser
from tests.round43_telegram_use_test import Client, message

class BrowserSelection(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="memu-dfy46-")
        self.prefs = {"dfy_model": "vision", "language": "en"}
        self.cfg = {"dfy": True, "schedules": True}
        self.settings_patch = patch.object(extension, "settings", side_effect=lambda: dict(self.cfg))
        self.settings_patch.start()
        self.socket_patch = patch.object(extension.bridge, "socket", object())
        self.socket_patch.start()
        self.manager = dfy.DFYManager(AsyncMock(return_value='{"action":"done"}'), lambda: self.prefs,
            data_root=Path(self.tmp.name), browser_factory=lambda _: FakeBrowser())
        self.manager._run = AsyncMock()

    async def asyncTearDown(self):
        for task in self.manager.tasks.values():
            if task.job: await task.job
        await self.manager.shutdown()
        self.socket_patch.stop(); self.settings_patch.stop(); self.tmp.cleanup()

    async def test_opt_in_requires_explicit_choice_without_creating_or_starting_any_task(self):
        for source in ("chat", "quick", "link", "cli"):
            with self.subTest(source=source), self.assertRaises(dfy.BrowserChoiceRequired) as caught:
                await self.manager.create("Do a task", source=source)
            self.assertEqual({row["id"] for row in caught.exception.choices}, {"isolated", "chrome"})
        self.assertEqual(self.manager.tasks, {})
        self.manager._run.assert_not_awaited()

    async def test_explicit_choice_is_stored_per_task(self):
        first = await self.manager.create("Use Chrome", browser="chrome")
        second = await self.manager.create("Use isolated", browser="isolated")
        self.assertEqual((first.public()["browser"], second.public()["browser"]), ("chrome", "isolated"))
        self.assertTrue(self.cfg["dfy"])

    async def test_opt_out_keeps_isolated_default_and_refuses_chrome(self):
        self.cfg["dfy"] = False
        task = await self.manager.create("Use default")
        self.assertEqual(task.browser_mode, "isolated")
        with self.assertRaises(PermissionError): await self.manager.create("No capability", browser="chrome")

    async def test_disconnected_chrome_does_not_block_isolated_browser(self):
        extension.bridge.socket = None
        state = self.manager.status()
        self.assertTrue(state["ready"])
        self.assertFalse(state["browser_choices"][1]["available"])
        await self.manager.create("Use separate browser", browser="isolated")
        with self.assertRaisesRegex(PermissionError, "Chrome"): await self.manager.create("Offline Chrome", browser="chrome")

    async def test_missing_edge_does_not_block_connected_chrome(self):
        self.manager._real_browser = True
        with patch("app.automation_runtime._edge_executable", side_effect=FileNotFoundError("No Edge")):
            self.assertTrue(self.manager.status()["ready"])
            await self.manager.create("Chrome available", browser="chrome")
            with self.assertRaisesRegex(PermissionError, "Edge"):
                await self.manager.create("No separate browser", browser="isolated")

    async def test_schedule_default_and_override_never_require_a_choice(self):
        self.prefs["dfy_schedule_browser"] = "chrome"
        chrome = await self.manager.create("Scheduled Chrome", source="schedule")
        separate = await self.manager.create("Scheduled separate", source="schedule", browser="isolated")
        self.assertEqual((chrome.browser_mode, separate.browser_mode), ("chrome", "isolated"))
        self.assertTrue(chrome.unattended)
        self.assertFalse(separate.unattended)

    async def test_schedule_chrome_requires_both_integration_permissions(self):
        self.cfg["schedules"] = False
        with self.assertRaisesRegex(PermissionError, "scheduled"):
            await self.manager.create("No schedule permission", source="schedule", browser="chrome")
        self.assertEqual(self.manager.tasks, {})
        self.prefs["dfy_schedule_browser"] = "chrome"; self.cfg["dfy"] = False
        with self.assertRaises(PermissionError): await self.manager.create("No Chrome optin", source="schedule")

    async def test_invalid_browser_or_target_has_no_side_effects(self):
        for value in ("firefox", [], "Chrome", "remote"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                await self.manager.create("Invalid", browser=value)
        with self.assertRaises(ValueError): await self.manager.create("Unknown machine", target="unknown-machine")
        self.assertFalse(self.manager.tasks)

    async def test_remote_machine_does_not_ask_for_a_local_browser(self):
        with patch.object(self.manager, "resolve_target", return_value={"id":"remote", "name":"Server"}):
            task = await self.manager.create("Remote task", target="remote")
        self.assertEqual(task.browser_mode, "remote")

    async def test_http_choice_contract_prevents_direct_implicit_run(self):
        app = FastAPI(); app.include_router(dfy_api.build_router(self.manager))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/dfy/tasks", json={"goal":"Do a task"})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["code"], "browser_choice_required")
            self.assertFalse(self.manager.tasks)
            response = await client.post("/api/dfy/tasks", json={"goal":"Do a task", "browser":"isolated"})
            self.assertEqual(response.status_code, 200)

    async def test_runner_uses_captured_mode_after_settings_change(self):
        task = dfy.DFYTask("task", [], "chat", "", "vision", 5, "irreversible")
        task.browser_mode = "isolated"
        browser = FakeBrowser()
        with patch.object(self.manager, "_ensure_browser", AsyncMock(return_value=browser)) as ensure, \
             patch.object(self.manager, "_loop", AsyncMock()):
            await dfy.DFYManager._run(self.manager, task, visible=False)
        ensure.assert_awaited_once_with(False, "isolated")

    async def test_clean_schedule_and_flow_preserve_override(self):
        task = schedule_rules_api._clean_task({"name":"test", "kind":"dfy", "prompt":"goal", "dfy_browser":"chrome"})
        self.assertEqual(task["dfy_browser"], "chrome")
        flow = flow_engine.task_to_flow(task)
        self.assertEqual(flow[0]["dfy_browser"], "chrome")
        with self.assertRaises(flow_engine.FlowError):
            flow_engine.normalize_step({"type":"dfy", "goal":"task", "dfy_browser":"unknown"})
        manager = types.SimpleNamespace(create=AsyncMock(return_value=types.SimpleNamespace(id="dfy", unattended=True)),
            wait_change=AsyncMock(return_value={"version":1, "status":"done", "result":"ok"}))
        executor = schedule_rules_api.Executor(types.SimpleNamespace(state=types.SimpleNamespace(dfy_manager=manager)))
        with patch("app.schedule_runtime._handoff_mutate", return_value=True):
            await executor.run_dfy({"id":"handoff", "task":task})
        manager.create.assert_awaited_once_with("goal", [], "schedule", "", browser="chrome")

class Catalogue(unittest.TestCase):
    def tearDown(self): names.reset_learned()
    def data(self):
        return {"models": {"gemini-3.8-flash-tiered": {}, "gemini-3.7-flash-tiered": {},
                           "claude-sonnet-4-6": {"displayName":"Claude Sonnet 4.6 (Thinking)"}},
                "agentModelSorts":[{"groups":[{"modelIds":["claude-sonnet-4-6"]}]}]}
    def test_current_server_tiered_flash_survives_a_stale_ide_picker(self):
        rows = {row.public:row for row in names.from_server(self.data())}
        self.assertIn("gemini-3.8-flash", rows)
        self.assertTrue(rows["gemini-3.8-flash"].available)
        self.assertNotIn("gemini-3.7-flash", rows)
        self.assertEqual(rows["gemini-3.8-flash"].upstream, "gemini-3.8-flash-tiered")
        self.assertTrue(all(f"gemini-3.8-flash-{level}" in rows for level in names.LEVELS))
    def test_absent_or_deprecated_flash_is_never_invented(self):
        for variant in ("absent", "deprecated", "internal"):
            data = self.data()
            if variant == "absent": del data["models"]["gemini-3.8-flash-tiered"]
            elif variant == "deprecated": data["deprecatedModelIds"] = {"gemini-3.8-flash-tiered":{"newModelId":"new-server-id"}}
            else: data["models"]["gemini-3.8-flash-tiered"]["isInternal"] = True
            with self.subTest(variant=variant):
                self.assertFalse(any(row.public.startswith("gemini-3.8") for row in names.from_server(data)))
    def test_disabled_flash_stays_visible_but_unavailable(self):
        data = self.data(); data["models"]["gemini-3.8-flash-tiered"]["disabled"] = True
        rows = [row for row in names.from_server(data) if row.public.startswith("gemini-3.8")]
        self.assertEqual(len(rows), 4)
        self.assertFalse(any(row.available for row in rows))

class TelegramIdentity(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="memu-tg46-")
        self.path_patch = patch.object(config, "path", return_value=Path(self.tmp.name)/"telegram_use.json")
        self.path_patch.start()
        config.save({"enabled":True, "api_id":123, "commands_enabled":True, "model":"fake"})
        config.secrets().set("api_hash", "b"*32)
        self.client = Client(); self.runtime = use.Runtime(lambda:self.client)
        await self.runtime._connect()
    async def asyncTearDown(self):
        await self.runtime._action("close", {})
        self.path_patch.stop(); config._stores.clear(); self.tmp.cleanup()
    def rows(self):
        owner = message(3, "I will pay", sender=42, outgoing=True)
        other = message(2, "I will deliver", sender=7)
        other.sender = types.SimpleNamespace(id=7, first_name="Ivan", last_name="Ivanovich", username="ivan")
        third = message(1, "I will check", sender=8)
        third.sender = types.SimpleNamespace(id=8, first_name="Anna", last_name="", username="anna")
        return [owner, other, third]
    async def test_tool_history_declares_account_and_distinct_group_authors(self):
        self.client.rows = self.rows()
        result = await self.runtime.tool("telegram_use_history", {"chat":7})
        self.assertEqual(result["account"]["id"], 42)
        owner, ivan, anna = result["messages"]
        self.assertEqual((owner["is_self"], owner["author_role"], owner["sender"]["name"]), (True,"account_owner","Me"))
        self.assertFalse(ivan["is_self"]); self.assertEqual(ivan["sender"]["name"], "Ivan Ivanovich")
        self.assertEqual(anna["sender"]["name"], "Anna")
    async def test_command_summary_passes_identity_metadata_and_preserves_chronology(self):
        self.client.rows = self.rows()
        command = message(10, "!//summary 3", sender=42, outgoing=True)
        self.client.rows.append(command)
        event = types.SimpleNamespace(message=command, sender_id=42, chat_id=7, is_private=False)
        with patch.object(ai,"complete",AsyncMock(return_value="summary")) as complete:
            await self.runtime._on_event(event)
            await asyncio.gather(*self.runtime._tasks)
        system, content = complete.call_args.args[1]
        self.assertIn('"id": 42', system["content"])
        self.assertIn("account_owner is the user", system["content"])
        lines = [json.loads(line) for line in content["content"].splitlines() if line.startswith('{"id":')]
        self.assertEqual([row["id"] for row in lines], [1,2,3])
        self.assertEqual([row["is_self"] for row in lines], [False,False,True])
    async def test_outgoing_channel_post_keeps_channel_identity_and_forward_source(self):
        row = message(1, "Forwarded words", sender=-100123, outgoing=True)
        row.post = True; row.sender = types.SimpleNamespace(id=123,title="News Channel",username="news")
        row.fwd_from = types.SimpleNamespace(from_id=types.SimpleNamespace(user_id=9),from_name="Source Author")
        public = use.public_message(row,self.runtime.me)
        self.assertFalse(public["is_self"])
        self.assertEqual(public["author_role"],"channel")
        self.assertEqual(public["sender"]["name"],"News Channel")
        self.assertEqual(public["forwarded_from"],{"sender_id":9,"name":"Source Author"})
    async def test_owner_identity_uses_sender_id_even_if_outgoing_flag_missing(self):
        row = use.public_message(message(sender=42,outgoing=False),self.runtime.me)
        self.assertTrue(row["is_self"])
        self.assertFalse(row["outgoing"])
    async def test_summary_budget_keeps_complete_json_and_labels_truncation(self):
        self.client.rows = [message(1, '\\"\n'*10000, sender=42,outgoing=True)]
        command = message(10,"!//summary 1",sender=42,outgoing=True); self.client.rows.append(command)
        event = types.SimpleNamespace(message=command,sender_id=42,chat_id=7,is_private=True)
        config.save({"context_chars":4000})
        with patch.object(ai,"complete",AsyncMock(return_value="summary")) as complete:
            await self.runtime._on_event(event); await asyncio.gather(*self.runtime._tasks)
        content = complete.call_args.args[1][1]["content"]
        line = next(line for line in content.splitlines() if line.startswith('{"id":'))
        self.assertEqual(json.loads(line)["author_role"],"account_owner")
        self.assertLessEqual(len(line),config.load()["context_chars"])
        self.assertIn("excerpt truncated",content)

if __name__ == "__main__": unittest.main()
