"""Round 43 cross-service glue against the real desktop ASGI application.

All network, account and native automation boundaries are replaced. No live
account, browser window, Telegram message or user's data is touched.
"""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
import importlib.util
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi.testclient import TestClient

from app import browser_extension as chrome
from app import integrations, pgdata, telegram_use
from app import telegram_use_config as tgconfig

EXTENSION = "c" * 32

class DesktopGlueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="round43-glue-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(pgdata, "MEMU_DIR", self.root / "data"))
        self.stack.enter_context(patch.object(integrations, "MEMU_DIR", self.root / "data"))
        self.stack.enter_context(patch.object(tgconfig, "path", return_value=self.root / "data/telegram_use.json"))
        self.stack.enter_context(patch.object(chrome, "_path", return_value=self.root / "extension.json"))
        self.bridge = chrome.Bridge()
        self.stack.enter_context(patch.object(chrome, "bridge", self.bridge))
        self.token = self.bridge.pair(self.bridge.begin_pair()["code"], EXTENSION)["token"]
        self.headers = {"Origin": "chrome-extension://" + EXTENSION,
                        "X-MemuAI-Extension": EXTENSION, "Authorization": "Bearer " + self.token,
                        "Sec-Fetch-Site": "cross-site"}
        self.proxy_calls = []
        self.http_clients = []
        original_client = httpx.AsyncClient
        def proxy(request):
            self.proxy_calls.append(request)
            return httpx.Response(200, json={"models": [{"id": "integration-model"}], "ok": True})
        def client(**kwargs):
            out = original_client(transport=httpx.MockTransport(proxy), **kwargs)
            self.http_clients.append(out)
            return out
        self.stack.enter_context(patch("app.playground_api.httpx.AsyncClient", client))
        self.browser = types.SimpleNamespace(shutdown=AsyncMock())
        self.computer = types.SimpleNamespace(stop=AsyncMock())
        self.dfy = types.SimpleNamespace(stop=AsyncMock())
        self.stack.enter_context(patch("app.automation_runtime.EdgeAutomation", return_value=self.browser))
        self.stack.enter_context(patch("app.automation_runtime.ComputerAutomation", return_value=self.computer))
        self.stack.enter_context(patch("app.voice_runtime.VoiceRuntime", return_value=MagicMock()))
        def install_dfy(app, *_args): app.state.dfy_manager = self.dfy
        self.stack.enter_context(patch("app.dfy_api.install", side_effect=install_dfy))
        async def panels(): await asyncio.Future()
        self.panels = self.stack.enter_context(patch("app.panels.panels_loop", side_effect=panels))
        self.mcp_stop = self.stack.enter_context(patch("app.mcp_client.registry.stop_all", new_callable=AsyncMock))
        self.sandbox_close = self.stack.enter_context(patch("app.code_sandbox.manager.close_all"))
        self.tg_start = self.stack.enter_context(patch.object(telegram_use, "start", return_value=MagicMock()))
        self.tg_stop = self.stack.enter_context(patch.object(telegram_use, "stop"))
        from app.playground_api import create_app
        self.app = create_app()

    def client(self):
        return TestClient(self.app, base_url="http://127.0.0.1:8765")

    def test_paired_chrome_can_only_access_bridge_routes_across_origin(self):
        with self.client() as client:
            models = client.get(chrome.PREFIX + "/bridge/models", headers=self.headers)
            self.assertEqual(models.status_code, 200, models.text)
            self.assertEqual(models.json()["models"][0]["id"], "integration-model")
            self.assertEqual(models.headers["access-control-allow-origin"], self.headers["Origin"])
            calls = len(self.proxy_calls)
            for path in ("/api/pg/state", "/api/pg/integrations", "/api/pg/integrations/telegram-use",
                         chrome.PREFIX + "/status", chrome.PREFIX + "/settings"):
                with self.subTest(path=path):
                    result = client.get(path, headers=self.headers)
                    self.assertEqual(result.status_code, 403, result.text)
                    self.assertNotIn("access-control-allow-origin", result.headers)
            blocked = client.post("/api/pg/tools", headers=self.headers,
                                  json={"tool": "telegram_use_send", "confirmed": True, "args": {"chat": "7", "text": "no"}})
            self.assertEqual(blocked.status_code, 403)
            self.assertEqual(len(self.proxy_calls), calls, "blocked extension requests must not reach the Proxy")

    def test_wrong_token_wrong_extension_origin_and_dns_rebinding_are_denied(self):
        with self.client() as client:
            for changes in ({"Authorization": "Bearer bad"}, {"X-MemuAI-Extension": "d" * 32},
                            {"Origin": "https://evil.example"}, {"Host": "evil.example"}):
                with self.subTest(changes=changes):
                    response = client.get(chrome.PREFIX + "/bridge/models", headers={**self.headers, **changes})
                    self.assertEqual(response.status_code, 403)
            self.assertEqual(self.proxy_calls, [])

    def test_preflight_is_narrow_and_does_not_grant_desktop_credentials(self):
        with self.client() as client:
            headers = {"Origin": self.headers["Origin"], "Access-Control-Request-Method": "POST",
                       "Access-Control-Request-Headers": "Authorization,Content-Type,X-MemuAI-Extension"}
            response = client.options(chrome.PREFIX + "/bridge/chat", headers=headers)
            self.assertEqual(response.status_code, 204)
            self.assertEqual(response.headers["access-control-allow-origin"], self.headers["Origin"])
            self.assertNotIn("access-control-allow-credentials", response.headers)
            for path in ("/api/pg/tools", "/api/pg/integrations", chrome.PREFIX + "/settings"):
                self.assertEqual(client.options(path, headers=headers).status_code, 403)

    def test_telegram_write_requires_confirmation_before_connector_is_called(self):
        execute = MagicMock(return_value={"message_ids": [99]})
        with patch.object(telegram_use, "run_tool", execute), self.client() as client:
            payload = {"tool": "telegram_use_send", "args": {"chat": "7", "text": "mock only"}, "access": "full"}
            for confirmation in (None, False, "true", 1):
                data = {**payload, "confirmed": confirmation}
                result = client.post("/api/pg/tools", json=data)
                self.assertEqual(result.status_code, 200)
                self.assertIn("error", result.json())
                execute.assert_not_called()
            result = client.post("/api/pg/tools", json={**payload, "confirmed": True})
            self.assertEqual(result.json()["value"]["message_ids"], [99])
            execute.assert_called_once_with("telegram_use_send", payload["args"], scheduled=False)

    def test_status_advertises_all_connector_tools_without_account_secrets(self):
        with self.client() as client:
            result = client.get("/api/pg/integrations")
            self.assertEqual(result.status_code, 200, result.text)
            rows = {r["service"]: r for r in result.json()["services"]}
            self.assertEqual(rows["telegram_use"]["tools"], telegram_use.TOOLS)
            self.assertEqual(rows["chrome_extension"]["tools"], chrome.TOOLS)
            self.assertFalse(rows["telegram_use"]["connected"])
            self.assertTrue(set(telegram_use.WRITE) <= set(result.json()["write_tools"]))
            self.assertNotIn("token_hash", rows["chrome_extension"])
            self.assertNotIn(self.token, result.text)
            self.assertNotIn("api_hash", rows["telegram_use"])

    def test_lifespan_starts_telegram_once_and_closes_all_resources(self):
        with self.client() as client:
            self.tg_start.assert_called_once()
            self.tg_stop.assert_not_called()
            self.assertEqual(client.get(chrome.PREFIX + "/status").status_code, 200)
        self.tg_stop.assert_called_once()
        self.panels.assert_awaited_once()
        self.browser.shutdown.assert_awaited_once()
        self.computer.stop.assert_awaited_once()
        self.dfy.stop.assert_awaited_once()
        self.mcp_stop.assert_awaited_once()
        self.sandbox_close.assert_called_once()
        self.assertTrue(all(client.is_closed for client in self.http_clients))

    def test_chrome_shutdown_failure_does_not_skip_telegram_or_other_cleanup(self):
        with patch.object(self.bridge, "stop", AsyncMock(side_effect=RuntimeError("simulated Chrome close failure"))):
            with self.client(): pass
        self.tg_stop.assert_called_once()
        self.browser.shutdown.assert_awaited_once()
        self.mcp_stop.assert_awaited_once()
        self.assertTrue(all(client.is_closed for client in self.http_clients))

    def test_failed_connector_startup_still_cleans_up_desktop_resources(self):
        self.tg_start.side_effect = RuntimeError("simulated startup failure")
        with self.assertRaisesRegex(RuntimeError, "simulated startup failure"):
            with self.client(): pass
        self.tg_start.assert_called_once()
        self.tg_stop.assert_called_once()
        self.browser.shutdown.assert_awaited_once()
        self.mcp_stop.assert_awaited_once()
        self.assertTrue(all(client.is_closed for client in self.http_clients))

    def test_sidepanel_chrome_tool_approval_and_scope_are_enforced(self):
        rpc = AsyncMock(return_value={"tab_id": 9})
        tg = MagicMock()
        with patch.object(self.bridge, "rpc", rpc), patch.object(telegram_use, "run_tool", tg), self.client() as client:
            payload = {"tool": "chrome_open", "args": {"url": "https://example.com"}}
            result = client.post(chrome.PREFIX + "/bridge/tool", json=payload, headers=self.headers)
            self.assertTrue(result.json()["needs_confirmation"])
            rpc.assert_not_awaited()
            result = client.post(chrome.PREFIX + "/bridge/tool", json={**payload, "confirmed": True}, headers=self.headers)
            self.assertEqual(result.json()["tab_id"], 9)
            rpc.assert_awaited_once()
            result = client.post(chrome.PREFIX + "/bridge/tool", json={"tool": "telegram_use_send", "args": {}, "confirmed": True}, headers=self.headers)
            self.assertEqual(result.status_code, 400)
            tg.assert_not_called()

    def test_full_application_websocket_checks_token_and_extension_binding(self):
        from starlette.websockets import WebSocketDisconnect
        with self.client() as client:
            for token, extension in (("wrong", EXTENSION), (self.token, "d" * 32)):
                with self.subTest(extension=extension), self.assertRaises(WebSocketDisconnect):
                    with client.websocket_connect(chrome.PREFIX + "/socket", headers={"Origin": "chrome-extension://" + extension, "Host": "127.0.0.1:8765"}) as socket:
                        socket.send_json({"token": token})
                        socket.receive_json()
            with client.websocket_connect(chrome.PREFIX + "/socket", headers={"Origin": self.headers["Origin"], "Host": "127.0.0.1:8765"}) as socket:
                socket.send_json({"token": self.token})
                self.assertEqual(socket.receive_json()["op"], "ready")
                socket.send_json({"op": "ping"})
                self.assertEqual(socket.receive_json()["op"], "pong")

class QuickBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_quick_access_prefers_extension_without_http_or_native_reads(self):
        from app.quick_ask_api import read_page_content
        extension = AsyncMock(return_value={"text": "Private logged-in page content", "url": "https://example.com/private", "title": "Private"})
        fetch = AsyncMock(side_effect=AssertionError("must not fetch"))
        with patch.object(chrome, "run_tool", extension):
            result = await read_page_content({"extension_tab_id": 7, "url": "https://example.com/private", "title": "Private"},
                                             run_tool=AsyncMock(side_effect=AssertionError("must not run fallback tool")), fetch_html=fetch)
        self.assertEqual(result["source"], "extension")
        self.assertIn("logged-in", result["text"])
        extension.assert_awaited_once_with("chrome_read", {"tab_id": 7, "limit": 60000})
        fetch.assert_not_awaited()

    async def test_extension_unavailable_keeps_existing_http_fallback(self):
        from app.quick_ask_api import read_page_content
        from app.quick_page_read import cache_clear
        cache_clear()
        text = "This is the original readable page. " * 70
        fetch = AsyncMock(return_value={"text": "<html><head><title>Document</title></head><body><article><h1>Document</h1><p>" + text + "</p></article></body></html>", "url": "https://example.com/fallback"})
        with patch.object(chrome, "run_tool", AsyncMock(side_effect=RuntimeError("offline"))):
            result = await read_page_content({"extension_tab_id": 7, "url": "https://example.com/fallback", "title": "Document"},
                                             run_tool=AsyncMock(), fetch_html=fetch)
        self.assertEqual(result["source"], "fetch", result)
        self.assertIn("original readable page", result["text"])
        fetch.assert_awaited_once()
        cache_clear()

    async def test_detection_uses_extension_only_for_chrome_and_preserves_other_browser(self):
        from app.quick_ask_browser import detect
        reader = types.SimpleNamespace(address=MagicMock(return_value="https://example.com/fallback"))
        hint = {"extension_tab_id": 7, "url": "https://example.com/private", "title": "Document", "source": "extension"}
        with patch.object(chrome.bridge, "page_hint", return_value=hint) as page_hint:
            chrome_result = detect(123, info={"exe": "chrome.exe", "title": "Document - Google Chrome"}, reader=reader)
            self.assertEqual(chrome_result["extension_tab_id"], 7)
            reader.address.assert_not_called()
            page_hint.reset_mock()
            edge_result = detect(123, info={"exe": "msedge.exe", "title": "Document - Microsoft Edge"}, reader=reader)
            self.assertEqual(edge_result["source"], "uia")
            self.assertNotIn("extension_tab_id", edge_result)
            page_hint.assert_not_called()

class FreezeContractTests(unittest.TestCase):
    def test_memuai_freeze_collects_dynamic_telegram_and_qr_dependencies(self):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location("round43_build_contract", root / "build.py")
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)
        target = build.TARGETS["memuai"]
        self.assertTrue({"telethon", "python_socks", "qrcode"} <= set(target["collect"]))
        command = build.pyinstaller_cmd(target, False)
        collected = {command[i+1] for i, arg in enumerate(command[:-1]) if arg == "--collect-all"}
        self.assertTrue({"telethon", "python_socks", "qrcode"} <= collected)
        for name in ("telethon.network.connection.tcpmtproxy", "telethon.tl.functions.messages",
                     "python_socks.async_.asyncio", "qrcode.image.pil", "PIL.ImageOps", "av"):
            with self.subTest(module=name): self.assertIsNotNone(importlib.util.find_spec(name))
        inputs = {p.resolve() for p in build.target_inputs(target)}
        for name in ("telegram_use.py", "telegram_use_api.py", "telegram_use_config.py", "telegram_use_ai.py",
                     "browser_extension.py", "browser_extension_chat.py", "connected_services.py"):
            with self.subTest(module=name): self.assertIn((root / "app" / name).resolve(), inputs)
        self.assertIn((root / "app/pgstatic/browser-extension/worker.js").resolve(), inputs)

if __name__ == "__main__": unittest.main()
