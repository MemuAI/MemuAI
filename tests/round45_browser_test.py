"""Profile storage, shared preferences, protocol handoff and real tool turn regressions."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import inspect
import json
import secrets
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from app import browser_extension as ext, browser_extension_connect as handoff
from app import browser_extension_store as store, browser_extension_chat as chat_api, pgdata
from tests import round44_extension_test as fixtures


class BrowserRound45(unittest.TestCase):
    setUp = fixtures.ExtensionWorkspaceTests.setUp
    post = fixtures.ExtensionWorkspaceTests.post
    get = fixtures.ExtensionWorkspaceTests.get
    events = fixtures.ExtensionWorkspaceTests.events
    upload = fixtures.ExtensionWorkspaceTests.upload

    def test_new_chats_are_private_and_existing_shared_conversations_stay_shared(self):
        first = self.post("chat", {"text": "Browser-only", "model": "fake"})
        private_id = next(event["id"] for event in self.events(first) if event["event"] == "chat")
        self.assertEqual(pgdata.load_chats(), [])
        self.assertEqual(len(store.browser_chats()), 1)
        self.assertTrue(self.get("chats?id=" + private_id).json()["chat"]["browser_only"])
        self.assertEqual(self.get("chats?scope=main").json()["chats"], [])
        self.assertEqual(self.post("settings", {"save_to_main": True}).status_code, 200)
        second = self.post("chat", {"text": "Shared", "model": "fake"})
        shared_id = next(event["id"] for event in self.events(second) if event["event"] == "chat")
        self.assertEqual(len(pgdata.load_chats()), 1)
        self.assertEqual(len(self.get("chats?scope=all").json()["chats"]), 2)
        self.post("settings", {"save_to_main": False})
        self.post("chat", {"text": "Continue shared", "model": "fake", "chat_id": shared_id})
        self.assertEqual(len(pgdata.load_chats()[0]["messages"]), 4)
        self.assertEqual(len(store.browser_chats()[0]["messages"]), 2)

    def test_private_chat_profile_isolation_and_profile_deletion(self):
        store.upsert_chat({"id": "browser-work", "messages": [], "updated": 1}, "work")
        self.assertEqual(self.get("chats?scope=browser").json()["chats"], [])
        self.assertEqual(self.get("chats?profile_id=work").status_code, 409)
        self.post("chat", {"text": "Private default", "model": "fake"})
        self.assertTrue((self.root / "browser-chats.json").is_file())
        pgdata.delete_profile("default")
        self.assertFalse((self.root / "browser-chats.json").exists())
        self.assertEqual(store.browser_chats("work")[0]["id"], "browser-work")

    def test_shared_default_and_pins_preserve_desktop_edits_and_unavailable_models(self):
        self.proxy.return_value = (200, {"models": [{"id": "live", "enabled": True, "name": "Live"}],
                                        "unavailable_models": [{"id": "future", "name": "Future", "enabled": True,
                                                                "availability": {"available": False, "reason": "Not in this client"}}]})
        pgdata.save_preferences({"default_model": "live", "pinned_models": ["desktop-image", "live"]})
        value = self.get("models").json()
        self.assertEqual(value["default_model"], "live")
        self.assertEqual(value["pinned_models"], ["desktop-image", "live"])
        self.assertEqual(next(m for m in value["models"] if m["id"] == "future")["availability"]["available"], False)
        self.post("model-preferences", {"pin_model": "new", "pinned": True})
        self.assertEqual(pgdata.load_preferences()["pinned_models"], ["desktop-image", "live", "new"])
        self.assertEqual(self.post("model-preferences", {"default_model": "unknown"}).status_code, 400)
        self.proxy.return_value = (200, {"models": [{"id": "future", "availability": {"available": False}}]})
        self.assertEqual(self.post("model-preferences", {"default_model": "future"}).status_code, 400)
        self.assertEqual(pgdata.load_preferences()["default_model"], "live")
        self.assertEqual(self.post("model-preferences", {"pin_model": "new", "pinned": False, "profile_id": "work"}).status_code, 409)

    def test_concurrent_pin_deltas_do_not_lose_other_models(self):
        def pin(index):
            return self.post("model-preferences", {"pin_model": "m" + str(index), "pinned": True}).status_code
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(list(pool.map(pin, range(12))), [200] * 12)
        self.assertEqual(set(pgdata.load_preferences()["pinned_models"]), {"m" + str(index) for index in range(12)})

    def test_synced_settings_are_typed_and_do_not_expose_the_bearer(self):
        body = {"read_pages": False, "control": "deny", "dfy": True, "schedules": True, "save_to_main": True}
        response = self.post("settings", body)
        self.assertEqual(response.json()["settings"], body)
        self.assertNotIn("token_hash", response.text)
        self.assertNotIn(self.headers["authorization"].removeprefix("Bearer "), response.text)
        self.assertEqual(self.client.get(ext.PREFIX + "/status").json()["control"], "deny")
        self.assertEqual(self.post("settings", {"control": "surprise"}).status_code, 400)
        self.assertEqual(self.post("settings", {"read_pages": "true"}).status_code, 400)
        self.assertEqual(self.post("revoke", {}).status_code, 200)
        self.assertEqual(self.get("settings").status_code, 403)

    def test_protocol_handoff_is_one_time_bound_to_extension_and_never_contains_a_bearer(self):
        from app import deeplinks
        challenge = secrets.token_hex(16)
        link = "memuai://browser-extension/connect?challenge=" + challenge + "&extension_id=" + "a" * 32
        parsed = deeplinks.parse(link)
        self.assertIn("browser-extension/connect", str(parsed))
        headers = {key: value for key, value in self.headers.items() if key != "authorization"}
        def claim(extra=None):
            return self.client.get(ext.PREFIX + "/bridge/connect?challenge=" + challenge, headers=extra or headers)
        self.assertFalse(claim().json()["ready"])
        granted = handoff.approve(challenge, "a" * 32)
        self.assertNotIn("token", json.dumps(granted))
        other = {"origin": "chrome-extension://" + "b" * 32, "x-memuai-extension": "b" * 32}
        self.assertFalse(claim(other).json()["ready"])
        ticket = claim().json()
        self.assertTrue(ticket["ready"])
        self.assertNotIn("token", ticket)
        self.assertFalse(claim().json()["ready"])
        result = self.client.post(ext.PREFIX + "/bridge/pair", json={"code": ticket["code"]}, headers=headers)
        self.assertEqual(result.status_code, 200)
        self.assertIn("token", result.json())
        self.assertEqual(self.client.post(ext.PREFIX + "/bridge/pair", json={"code": ticket["code"]}, headers=headers).status_code, 403)
        with self.assertRaises(ValueError):
            handoff.approve("x" * 32, "a" * 32)

    def test_page_context_activity_is_streamed_and_persisted(self):
        with patch.object(chat_api, "run_tool", AsyncMock(return_value={"text": "page text", "title": "Actual page"})):
            response = self.post("chat", {"text": "Summarize", "model": "fake", "page_context": True})
        activity = [e for e in self.events(response) if e["event"] == "tool"]
        self.assertEqual([e["phase"] for e in activity], ["running", "done"])
        self.assertEqual(activity[-1]["detail"], "Actual page")
        self.assertEqual(store.browser_chats()[0]["messages"][-1]["tool_activity"][0]["phase"], "done")
        with patch.object(chat_api, "run_tool", AsyncMock(side_effect=ValueError("Site permission denied"))):
            response = self.post("chat", {"text": "Summarize", "model": "fake", "page_context": True})
        self.assertEqual([e["phase"] for e in self.events(response) if e["event"] == "tool"], ["running", "error"])

    def test_old_chat_cannot_use_a_newly_paired_browser_after_model_completion(self):
        async def complete(model, messages):
            self.bridge.pair(self.bridge.begin_pair()["code"], "b" * 32)
            return '```memuai-tool\n[{"tool":"chrome_read","args":{"tab_id":1}}]\n```'
        self.complete.side_effect = complete
        rpc = AsyncMock(return_value={"text": "Other Chrome profile's page"})
        with patch.object(self.bridge, "rpc", rpc):
            response = self.post("chat", {"text": "Read my page", "model": "fake"})
        rpc.assert_not_awaited()
        self.assertEqual(self.complete.await_count, 1)
        events = self.events(response)
        self.assertEqual(events[-1]["event"], "error")
        self.assertIn("Подключение Chrome изменилось", events[-1]["text"])
        self.assertEqual([m["role"] for m in store.browser_chats()[0]["messages"]], ["user"])

    def test_stream_stops_publishing_old_turn_tokens_when_pairing_changes(self):
        from fastapi import FastAPI
        async def lines(path, payload):
            yield 'data: {"choices":[{"delta":{"content":"Before pairing changed"}}]}'
            self.bridge.pair(self.bridge.begin_pair()["code"], "b" * 32)
            yield 'data: {"choices":[{"delta":{"content":"Private result after pairing changed"}}]}'
        app = FastAPI()
        ext.register(app, completion=self.complete, proxy_json=self.proxy, stream_lines=lines)
        async def scenario():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8600") as client:
                return await client.post(ext.PREFIX + "/bridge/chat", headers=self.headers,
                                         json={"text": "Explain", "model": "fake"})
        response = asyncio.run(scenario())
        self.assertNotIn("Private result after pairing changed", response.text)
        self.assertEqual(self.events(response)[-1]["event"], "error")

    def test_pending_approval_cannot_close_tabs_after_pairing_to_another_profile(self):
        self.complete.return_value = '```memuai-tool\n[{"tool":"chrome_close","args":{"tab_id":1}}]\n```'
        tabs = [{"tab_id": 1, "title": "Original tab", "url": "https://github.com/original"}]
        rpc = AsyncMock(return_value={"tabs": tabs})
        answer = next(r for r in self.app.routes if getattr(r, "path", "") == ext.PREFIX + "/bridge/answer")
        approvals = inspect.getclosurevars(answer.endpoint).nonlocals["approvals"]
        async def scenario():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(self.app), base_url="http://127.0.0.1:8600") as client:
                task = asyncio.create_task(client.post(ext.PREFIX + "/bridge/chat", headers=self.headers,
                                          json={"text": "Close the original tab", "model": "fake"}))
                try:
                    for _ in range(100):
                        if approvals or task.done(): break
                        await asyncio.sleep(.01)
                    self.assertEqual(len(approvals), 1)
                    token = self.bridge.pair(self.bridge.begin_pair()["code"], "b" * 32)["token"]
                    new_headers = {"origin": "chrome-extension://" + "b" * 32, "x-memuai-extension": "b" * 32,
                                   "authorization": "Bearer " + token}
                    response = await client.post(ext.PREFIX + "/bridge/answer", headers=new_headers,
                                                 json={"id": next(iter(approvals)), "approve": True})
                    self.assertEqual(response.status_code, 200)
                    return await asyncio.wait_for(task, 3)
                finally:
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
        with patch.object(self.bridge, "rpc", rpc):
            response = asyncio.run(scenario())
        self.assertEqual([call.args[0] for call in rpc.await_args_list], ["tabs"])
        self.assertEqual(self.events(response)[-1]["event"], "error")
        self.assertEqual(approvals, {})

    def test_all_close_calls_use_one_named_approval_and_one_mutation(self):
        self.complete.side_effect = ['```memuai-tool\n[{"tool":"chrome_close","args":{"tab_id":1}},{"tool":"chrome_close","args":{"tab_id":2}}]\n```', "Closed."]
        tabs = [{"tab_id": 1, "title": "GitHub project", "url": "https://github.com/a"},
                {"tab_id": 2, "title": "GitHub issues", "url": "https://github.com/b/issues"}]
        async def rpc(op, args):
            return {"tabs": tabs} if op == "tabs" else {"ok": True, "closed": args["tab_ids"]}
        mock = AsyncMock(side_effect=rpc)
        answer = next(r for r in self.app.routes if getattr(r, "path", "") == ext.PREFIX + "/bridge/answer")
        approvals = inspect.getclosurevars(answer.endpoint).nonlocals["approvals"]
        async def scenario():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(self.app), base_url="http://127.0.0.1:8600") as client:
                task = asyncio.create_task(client.post(ext.PREFIX + "/bridge/chat", headers=self.headers,
                                          json={"text": "Close all GitHub tabs", "model": "fake"}))
                try:
                    for _ in range(100):
                        if approvals or task.done(): break
                        await asyncio.sleep(.01)
                    self.assertEqual(len(approvals), 1)
                    self.assertFalse(any(call.args[0] == "close_tabs" for call in mock.await_args_list))
                    reply = await client.post(ext.PREFIX + "/bridge/answer", headers=self.headers,
                                              json={"id": next(iter(approvals)), "approve": True})
                    self.assertEqual(reply.status_code, 200)
                    return await asyncio.wait_for(task, 3)
                finally:
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
        with patch.object(self.bridge, "rpc", mock):
            response = asyncio.run(scenario())
        events = self.events(response)
        confirms = [e for e in events if e["event"] == "confirm"]
        self.assertEqual(len(confirms), 1)
        self.assertEqual(confirms[0]["tabs"], tabs)
        self.assertIn("GitHub project", confirms[0]["description"])
        closes = [call for call in mock.await_args_list if call.args[0] == "close_tabs"]
        self.assertEqual(len(closes), 1)
        self.assertEqual(closes[0].args[1]["tab_ids"], [1, 2])
        self.assertEqual(events[-1]["event"], "done", events)


class BridgeEpochTest(unittest.IsolatedAsyncioTestCase):
    async def test_queued_rpc_never_moves_to_a_replacement_socket(self):
        bridge = ext.Bridge()
        old = SimpleNamespace(send_json=AsyncMock())
        replacement = SimpleNamespace(send_json=AsyncMock())
        bridge.socket = old
        bridge._send_lock = asyncio.Lock()
        await bridge._send_lock.acquire()
        task = asyncio.create_task(bridge.rpc("close_tabs", {"tab_ids": [1]}, timeout=1))
        try:
            await asyncio.sleep(0)
            self.assertEqual(len(bridge.pending), 1)
            bridge.disconnected()
            bridge.socket = replacement
            bridge._send_lock.release()
            with self.assertRaisesRegex(RuntimeError, "Подключение Chrome изменилось"):
                await task
        finally:
            if bridge._send_lock.locked(): bridge._send_lock.release()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        old.send_json.assert_not_awaited()
        replacement.send_json.assert_not_awaited()
        self.assertEqual(bridge.pending, {})

    async def test_queued_rpc_cleans_pending_future_if_socket_was_replaced_without_disconnect(self):
        bridge = ext.Bridge()
        bridge.socket = SimpleNamespace(send_json=AsyncMock())
        bridge._send_lock = asyncio.Lock()
        await bridge._send_lock.acquire()
        task = asyncio.create_task(bridge.rpc("read", {"tab_id": 1}, timeout=1))
        try:
            await asyncio.sleep(0)
            pending, = bridge.pending.values()
            replacement = SimpleNamespace(send_json=AsyncMock())
            bridge.socket = replacement
            bridge._send_lock.release()
            with self.assertRaises(RuntimeError):
                await task
            replacement.send_json.assert_not_awaited()
            self.assertTrue(pending.cancelled())
            self.assertEqual(bridge.pending, {})
        finally:
            if bridge._send_lock.locked(): bridge._send_lock.release()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
