"""Round 43 Chrome bridge: real ASGI routes/WebSockets, no browser automation."""
from __future__ import annotations

import asyncio
import io
import inspect
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
import zipfile

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import browser_extension as ext

EXTENSION = "a" * 32
OTHER = "b" * 32

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="memu-extension-tests-")
        self.addCleanup(self.temp.cleanup)
        self.file = Path(self.temp.name) / "extension.json"
        self.patch = patch.object(ext, "_path", return_value=self.file)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.bridge = ext.Bridge()
        self.bp = patch.object(ext, "bridge", self.bridge)
        self.bp.start()
        self.addCleanup(self.bp.stop)
        code = self.bridge.begin_pair()["code"]
        self.token = self.bridge.pair(code, EXTENSION)["token"]
        self.headers = {"origin": "chrome-extension://" + EXTENSION, "x-memuai-extension": EXTENSION,
                        "authorization": "Bearer " + self.token}

    def app(self, completion=None, stream_lines=None):
        app = FastAPI()
        @app.exception_handler(PermissionError)
        async def denied(_request, exc):
            return JSONResponse({"error": str(exc)}, 403)
        ext.register(app, completion=completion or AsyncMock(return_value="Hello"),
                     proxy_json=AsyncMock(return_value=(200, {"models": [{"id": "fake"}]})),
                     stream_lines=stream_lines, run_chat_tool=AsyncMock(return_value={"value": "tool completed"}))
        return app

    def cfg(self, **changes):
        current = ext.settings()
        current.update(changes)
        from app.storage import save_json
        save_json(self.file, current, backup=False)

    def request(self, path="/bridge/models", headers=None, method="GET"):
        return Request({"type": "http", "method": method, "scheme": "http", "path": ext.PREFIX + path,
                        "query_string": b"", "server": ("127.0.0.1", 8600), "client": ("127.0.0.1", 1),
                        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or self.headers).items()]})

    def test_token_is_hashed_on_disk_and_never_in_public_status(self):
        self.assertNotIn(self.token, self.file.read_text(encoding="utf-8"))
        self.assertNotIn("token_hash", self.bridge.public())
        self.assertNotIn(self.token, json.dumps(self.bridge.public()))
        self.assertTrue(ext.valid_token(self.token, EXTENSION))
        self.assertFalse(ext.valid_token(self.token, OTHER))

    def test_pair_code_is_one_time_expiring_and_rate_limited(self):
        code = self.bridge.begin_pair()["code"]
        self.bridge.expires = 0
        with self.assertRaises(PermissionError):
            self.bridge.pair(code, EXTENSION)
        code = self.bridge.begin_pair()["code"]
        self.bridge.pair(code, EXTENSION)
        with self.assertRaises(PermissionError):
            self.bridge.pair(code, EXTENSION)
        self.bridge.begin_pair()
        for _ in range(10):
            with self.assertRaises(PermissionError):
                self.bridge.pair("wrong", EXTENSION)
        with self.assertRaisesRegex(PermissionError, "минут"):
            self.bridge.pair(self.bridge.code, EXTENSION)

    def test_pairing_exception_is_narrow_and_origin_token_bound(self):
        self.assertTrue(ext.cross_origin_allowed(self.request()))
        self.assertFalse(ext.cross_origin_allowed(self.request(headers={**self.headers, "origin": "https://evil.invalid"})))
        self.assertFalse(ext.cross_origin_allowed(self.request(headers={**self.headers, "authorization": "Bearer bad"})))
        self.assertFalse(ext.cross_origin_allowed(self.request("/settings")))
        self.assertFalse(ext.cross_origin_allowed(self.request(headers={**self.headers, "x-memuai-extension": OTHER})))

    def test_navigation_refuses_credentials_and_nonweb_schemes(self):
        for url in ("javascript:alert(1)", "file:///C:/secret", "data:text/html,x", "https://a:b@example.com"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                ext.safe_url(url)
        self.assertEqual(ext.safe_url("https://example.com/a"), "https://example.com/a")

    def test_permission_modes_and_unattended_opt_in_are_enforced_before_rpc(self):
        rpc = AsyncMock(return_value={"ok": True})
        async def scenario():
            with patch.object(self.bridge, "rpc", rpc):
                result = await ext.run_tool("chrome_open", {"url": "https://example.com"})
                self.assertTrue(result.get("needs_confirmation"))
                rpc.assert_not_awaited()
                await ext.run_tool("chrome_open", {"url": "https://example.com"}, confirmed=True)
                self.assertEqual(rpc.await_count, 1)
                with self.assertRaises(PermissionError):
                    await ext.run_tool("chrome_open", {"url": "https://example.com"}, scheduled=True)
                self.cfg(schedules=True)
                await ext.run_tool("chrome_open", {"url": "https://example.com"}, scheduled=True)
                self.cfg(control="deny")
                with self.assertRaises(PermissionError):
                    await ext.run_tool("chrome_close", {"tab_id": 1}, confirmed=True, scheduled=True)
        asyncio.run(scenario())

    def test_page_read_opt_out_blocks_read_and_screenshot(self):
        self.cfg(read_pages=False)
        for tool in ("chrome_read", "chrome_screenshot"):
            with self.subTest(tool=tool), self.assertRaises(PermissionError):
                asyncio.run(ext.run_tool(tool, {}))

    def test_http_models_chats_and_unpacked_zip_routes(self):
        with TestClient(self.app(), base_url="http://127.0.0.1:8600") as client:
            models = client.get(ext.PREFIX + "/bridge/models", headers=self.headers)
            self.assertEqual(models.json()["models"][0]["id"], "fake")
            self.assertEqual(client.get(ext.PREFIX + "/bridge/models", headers={**self.headers,
                             "authorization": "Bearer bad"}).status_code, 403)
            downloaded = client.get(ext.PREFIX + "/download")
            self.assertEqual(downloaded.status_code, 200)
            with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                self.assertEqual(manifest["manifest_version"], 3)
                self.assertIn("worker.js", archive.namelist())

    def test_websocket_auth_rpc_metadata_and_disconnect(self):
        with TestClient(self.app(), base_url="http://127.0.0.1:8600") as client:
            with client.websocket_connect(ext.PREFIX + "/socket", headers={"origin": self.headers["origin"], "host": "127.0.0.1:8600"}) as socket:
                socket.send_json({"token": self.token})
                self.assertEqual(socket.receive_json()["op"], "ready")
                socket.send_json({"op": "active_tab", "value": {"tab_id": 7, "title": "Document", "url": "https://example.com"}})
                socket.send_json({"op": "ping"})
                self.assertEqual(socket.receive_json()["op"], "pong")
                self.assertEqual(self.bridge.page_hint("Document")["extension_tab_id"], 7)
                self.assertIsNone(self.bridge.page_hint("Other document"))
                future = asyncio.run_coroutine_threadsafe(self.bridge.rpc("tabs"), self.bridge.loop)
                command = socket.receive_json()
                socket.send_json({"id": command["id"], "value": {"tabs": [{"tab_id": 7}]}})
                self.assertEqual(future.result(2)["tabs"][0]["tab_id"], 7)
            self.assertIsNone(self.bridge.socket)
            self.assertEqual(self.bridge.pending, {})

    def test_bad_websocket_token_cannot_claim_connection(self):
        from starlette.websockets import WebSocketDisconnect
        with TestClient(self.app(), base_url="http://127.0.0.1:8600") as client:
            with client.websocket_connect(ext.PREFIX + "/socket", headers={"origin": self.headers["origin"], "host": "127.0.0.1:8600"}) as socket:
                socket.send_json({"token": "wrong"})
                with self.assertRaises(WebSocketDisconnect):
                    socket.receive_json()
        self.assertIsNone(self.bridge.socket)

    def test_reconnect_does_not_clear_the_new_socket(self):
        with TestClient(self.app(), base_url="http://127.0.0.1:8600") as client:
            headers = {"origin": self.headers["origin"], "host": "127.0.0.1:8600"}
            with client.websocket_connect(ext.PREFIX + "/socket", headers=headers) as first:
                first.send_json({"token": self.token})
                self.assertEqual(first.receive_json()["op"], "ready")
                with client.websocket_connect(ext.PREFIX + "/socket", headers=headers) as second:
                    second.send_json({"token": self.token})
                    self.assertEqual(second.receive_json()["op"], "ready")
                    second.send_json({"op": "ping"})
                    self.assertEqual(second.receive_json()["op"], "pong")
                    self.assertIsNotNone(self.bridge.socket)
            self.assertIsNone(self.bridge.socket)

    def test_streaming_tool_round_preserves_screenshot_and_hides_tool_json(self):
        from app import pgdata, integrations
        from PIL import Image
        import base64
        screenshot = io.BytesIO()
        Image.new("RGB", (2, 2), (20, 30, 40)).save(screenshot, "PNG")
        screenshot_data = base64.b64encode(screenshot.getvalue()).decode()
        requests = []
        async def stream(_path, body):
            request = json.loads(body)
            requests.append(request)
            text = ('```memuai-tool\n[{"tool":"chrome_screenshot","args":{"tab_id":7}}]\n```'
                    if len(requests) == 1 else "The screenshot shows a document.")
            for position in range(0, len(text), 3):
                yield "data: " + json.dumps({"choices": [{"delta": {"content": text[position:position+3]}}]})
            yield "data: [DONE]"
        with patch.object(pgdata, "load_profiles", return_value={"active_id": "default"}), \
                patch.object(pgdata, "load_chats", return_value=[]), \
                patch.object(pgdata, "upsert_chat"), patch.object(pgdata, "patch_chat"), \
                patch.object(pgdata, "load_prompts", return_value={"prompts": []}), \
                patch.object(integrations, "status", return_value={"services": []}), \
                patch.object(self.bridge, "rpc", new=AsyncMock(return_value={"data": screenshot_data, "media_type": "image/png"})), \
                TestClient(self.app(stream_lines=stream), base_url="http://127.0.0.1:8600") as client:
            response = client.post(ext.PREFIX + "/bridge/chat", headers=self.headers,
                                   json={"text": "What is in tab 7?", "model": "fake"})
        frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        self.assertEqual(frames[-1]["event"], "done")
        visible = "".join(row.get("text", "") for row in frames if row["event"] == "token")
        self.assertNotIn("memuai-tool", visible)
        self.assertEqual(len(requests), 2)
        image_parts = requests[1]["messages"][-1]["content"]
        self.assertIsInstance(image_parts, list)
        self.assertTrue(any(p.get("type") == "image_url" for p in image_parts))

    def test_expired_approval_cannot_confirm_another_action(self):
        with TestClient(self.app(), base_url="http://127.0.0.1:8600") as client:
            response = client.post(ext.PREFIX + "/bridge/answer", headers=self.headers,
                                   json={"id": "unknown", "approve": True})
        self.assertEqual(response.status_code, 409)

    def test_chat_deleted_before_stream_start_is_not_resurrected(self):
        from app import pgdata
        complete = AsyncMock(return_value="must not run")
        with patch.object(pgdata, "load_profiles", return_value={"active_id": "default"}), \
                patch.object(pgdata, "load_chats", side_effect=[[{"id": "removed", "messages": []}], []]), \
                patch.object(pgdata, "upsert_chat") as upsert, patch.object(pgdata, "patch_chat"), \
                TestClient(self.app(completion=complete), base_url="http://127.0.0.1:8600") as client:
            response = client.post(ext.PREFIX + "/bridge/chat", headers=self.headers,
                                   json={"chat_id": "removed", "text": "Continue", "model": "fake"})
        self.assertIn('"event": "error"', response.text)
        upsert.assert_not_called()
        complete.assert_not_awaited()

    def test_sidepanel_mutation_waits_for_the_actual_answer_route(self):
        from app import pgdata, integrations
        for approve in (False, True):
            with self.subTest(approve=approve):
                complete = AsyncMock(side_effect=['```memuai-tool\n[{"tool":"chrome_open","args":{"url":"https://example.com"}}]\n```', "Finished."])
                app = self.app(completion=complete)
                answer_route = next(r for r in app.routes if getattr(r, "path", "") == ext.PREFIX + "/bridge/answer")
                approvals = inspect.getclosurevars(answer_route.endpoint).nonlocals["approvals"]
                rpc = AsyncMock(return_value={"tab_id": 9})
                async def scenario():
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8600") as client:
                        task = asyncio.create_task(client.post(ext.PREFIX + "/bridge/chat", headers=self.headers,
                                  json={"text": "Open example", "model": "fake"}))
                        try:
                            for _ in range(100):
                                if approvals or task.done():
                                    break
                                await asyncio.sleep(0.01)
                            self.assertTrue(approvals, "the mutation did not request approval")
                            rpc.assert_not_awaited()
                            approval_id = next(iter(approvals))
                            response = await client.post(ext.PREFIX + "/bridge/answer", headers=self.headers,
                                                          json={"id": approval_id, "approve": approve})
                            self.assertEqual(response.status_code, 200)
                            result = await asyncio.wait_for(task, 2)
                            self.assertIn('"event": "done"', result.text)
                        finally:
                            if not task.done():
                                task.cancel()
                                await asyncio.gather(task, return_exceptions=True)
                with patch.object(pgdata, "load_profiles", return_value={"active_id": "default"}), \
                        patch.object(pgdata, "load_chats", return_value=[]), \
                        patch.object(pgdata, "upsert_chat"), patch.object(pgdata, "patch_chat"), \
                        patch.object(pgdata, "load_prompts", return_value={"prompts": []}), \
                        patch.object(integrations, "status", return_value={"services": []}), \
                        patch.object(self.bridge, "rpc", rpc):
                    asyncio.run(scenario())
                self.assertEqual(rpc.await_count, 1 if approve else 0)

    def test_plain_sidepanel_chat_persists_both_messages_and_sse(self):
        from app import pgdata, integrations
        store = [{"id": "existing", "title": "Existing", "messages": []}]
        def upsert(row, _profile=None):
            store[:] = [r for r in store if r["id"] != row["id"]] + [row]
        def update(chat_id, function, _profile=None):
            row = next((r for r in store if r["id"] == chat_id), None)
            if row is not None:
                function(row)
            return row
        complete = AsyncMock(return_value="**Hello**")
        with patch.object(pgdata, "load_profiles", return_value={"active_id": "default"}), \
                patch.object(pgdata, "load_chats", side_effect=lambda *_: json.loads(json.dumps(store))), \
                patch.object(pgdata, "upsert_chat", side_effect=upsert), \
                patch.object(pgdata, "patch_chat", side_effect=update), \
                patch.object(pgdata, "load_prompts", return_value={"prompts": [], "default_id": ""}), \
                patch.object(integrations, "status", return_value={"services": []}), \
                TestClient(self.app(completion=complete), base_url="http://127.0.0.1:8600") as client:
            response = client.post(ext.PREFIX + "/bridge/chat", headers=self.headers,
                                   json={"chat_id": "existing", "text": "Hi", "model": "fake", "page_context": False})
        self.assertEqual(response.status_code, 200)
        frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        self.assertEqual(frames[-1]["event"], "done")
        self.assertIn("Hello", next(v["text"] for v in frames if v["event"] == "replace"))
        self.assertEqual([m["role"] for m in store[0]["messages"]], ["user", "assistant"])
        messages = complete.call_args.args[1]
        self.assertIn("Chrome side panel", messages[0]["content"])
        self.assertNotIn("<untrusted_page>", json.dumps(messages))

class DFYLifetime(unittest.TestCase):
    def test_owned_tab_initializes_secret_masks_and_close_only_its_tab(self):
        from app import browser_extension_dfy as module
        connection = object()
        rpc = AsyncMock(side_effect=lambda op, *args, **kwargs: {"tab_id": 55} if op == "dfy_start" else {})
        mock = type("FakeBridge", (), {"socket": connection, "rpc": rpc})()
        async def scenario():
            with patch.object(module, "bridge", mock), patch.object(module, "settings", return_value={"dfy": True}):
                browser = module.ExtensionDFYBrowser()
                await browser.start()
                self.assertTrue(browser.running)
                await browser.close()
                self.assertFalse(browser.running)
        asyncio.run(scenario())
        commands = [c.args for c in rpc.call_args_list]
        self.assertTrue(any(a[0] == "cdp" and a[1]["method"] == "Page.addScriptToEvaluateOnNewDocument" for a in commands))
        self.assertTrue(any(a[0] == "cdp" and a[1]["method"] == "Runtime.evaluate" for a in commands))
        self.assertEqual(commands[-1], ("dfy_stop", {"tab_id": 55}))

class QuickAccessPreference(unittest.TestCase):
    def manager(self):
        from app.quick_ask import QuickAskManager
        manager = object.__new__(QuickAskManager)
        manager._page_token = 0
        manager.page = None
        manager.native_ok = True
        manager.api = object()
        manager.hwnd = 999
        manager._push = Mock()
        return manager

    def test_public_page_keeps_valid_extension_tab_id_and_hides_native_handle(self):
        from app.quick_ask_core import public_page
        value = public_page({"id": "page", "hwnd": 123, "extension_tab_id": 7, "source": "extension"})
        self.assertEqual(value["extension_tab_id"], 7)
        self.assertNotIn("hwnd", value)
        for invalid in (-1, "7", None):
            self.assertNotIn("extension_tab_id", public_page({"extension_tab_id": invalid}))

    def test_browser_detection_prefers_extension_and_other_browsers_use_existing_reader(self):
        from app import quick_ask_browser as qb
        reader = SimpleNamespace(address=Mock(return_value="https://fallback.example.com"))
        hint = {"extension_tab_id": 7, "source": "extension", "title": "Document", "url": "https://example.com"}
        with patch.object(ext.bridge, "page_hint", return_value=hint) as extension:
            value = qb.detect(123, info={"exe": "chrome.exe", "title": "Document - Google Chrome"}, reader=reader)
            self.assertEqual(value["source"], "extension")
            reader.address.assert_not_called()
            extension.reset_mock()
            value = qb.detect(123, info={"exe": "firefox.exe", "title": "Document - Mozilla Firefox"}, reader=reader)
            self.assertEqual(value["source"], "uia")
            extension.assert_not_called()
            reader.address.assert_called_once()

    def test_hotkey_page_creation_uses_extension_without_spawning_uia_thread(self):
        from app import quick_ask, quick_ask_browser as qb
        manager = self.manager()
        hint = {"extension_tab_id": 7, "source": "extension", "title": "Document", "url": "https://example.com"}
        with patch.object(qb, "foreground_info", return_value={"exe": "chrome.exe", "title": "Document - Google Chrome"}), \
                patch.object(ext.bridge, "page_hint", return_value=hint), \
                patch.object(quick_ask.threading, "Thread", side_effect=AssertionError("UIA thread must not start")):
            page = manager._begin_page(123, {"quick_ask_browser": True})
        self.assertEqual(page["extension_tab_id"], 7)
        self.assertEqual(page["source"], "extension")
        self.assertFalse(page["pending"])

    def test_hotkey_page_creation_preserves_uia_fallback_without_extension(self):
        from app import quick_ask, quick_ask_browser as qb
        manager = self.manager()
        reader = SimpleNamespace(address=Mock(return_value="https://fallback.example.com"))
        def launch(*, target, **kwargs):
            def start():
                manager.page["_shown"].set()
                target()
            return SimpleNamespace(start=start)
        with patch.object(qb, "foreground_info", return_value={"exe": "chrome.exe", "title": "Document - Google Chrome"}), \
                patch.object(ext.bridge, "page_hint", return_value=None), \
                patch.object(qb.UiaReader, "shared", return_value=reader), \
                patch.object(quick_ask.threading, "Thread", side_effect=launch):
            page = manager._begin_page(123, {"quick_ask_browser": True})
        self.assertEqual(page["url"], "https://fallback.example.com")
        self.assertEqual(page["source"], "uia")
        reader.address.assert_called_once_with(123)

    def test_current_page_enriches_existing_page_with_extension_metadata(self):
        from app import quick_ask_browser as qb
        manager = self.manager()
        manager.page = {"hwnd": 123, "id": "unchanged", "url": "https://example.com", "title": "Document", "source": "uia", "t": 0}
        fresh = {"hwnd": 123, "url": "https://example.com", "title": "Document", "source": "extension", "extension_tab_id": 7}
        with patch.object(qb, "top_level_windows", return_value=[{"hwnd": 123, "exe": "chrome.exe", "title": "Document"}]), \
                patch.object(qb, "pick_top_browser", return_value=123), patch.object(qb, "detect", return_value=fresh):
            page = manager.current_page()
        self.assertEqual(page["id"], "unchanged")
        self.assertEqual(page["source"], "extension")
        self.assertEqual(page["extension_tab_id"], 7)

    def test_page_read_prefers_authenticated_extension_and_falls_back_cleanly(self):
        from app import quick_ask_api
        page = {"id": "page", "extension_tab_id": 7, "url": "https://example.com", "title": "Document"}
        manager = SimpleNamespace(page_text=Mock(return_value="Visible fallback paragraph. " * 20))
        generic = AsyncMock(side_effect=AssertionError("No external HTTP in the test"))
        reader = AsyncMock(return_value={"text": "Authenticated extension paragraph. " * 20, "url": page["url"]})
        async def scenario():
            with patch.object(ext, "run_tool", reader):
                preferred = await quick_ask_api.read_page_content(page, run_tool=generic, manager=manager, allow_fetch=False)
            manager.page_text.assert_not_called()
            reader.side_effect = RuntimeError("extension disconnected")
            with patch.object(ext, "run_tool", reader):
                fallback = await quick_ask_api.read_page_content(page, run_tool=generic, manager=manager, allow_fetch=False)
            return preferred, fallback
        preferred, fallback = asyncio.run(scenario())
        self.assertEqual(preferred["source"], "extension")
        self.assertIn("Authenticated", preferred["text"])
        self.assertEqual(fallback["source"], "screen")
        self.assertIn("fallback", fallback["text"])
        generic.assert_not_awaited()

if __name__ == "__main__":
    unittest.main()
