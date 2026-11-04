"""Плагины провайдеров: управление из админки и MCP-сервер Proxy.

Админка: установка/удаление/вкл-выкл плагинов, галерея шаблонов, открытие
папки. MCP: POST /mcp (initialize, tools/list, tools/call, resources,
уведомления, ошибки JSON-RPC) и GET /mcp (SSE-поток).
"""
from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")

GOOD2_PLUGIN = '''
PLUGIN_API = 1
PLUGIN_VERSION = "1.2"
PLUGIN_AUTHOR = "Tester"
from app.providers.base import Field, Provider

class GoodTwoProvider(Provider):
    id = "good-two"
    title = "Good Two"
    icon = "T"
    description = "Second test plugin"

    def account_fields(self):
        return [Field("api_key", "API-ключ", "password")]

    def seed_models(self):
        return [("model-a", "Model A")]

    async def check(self, account):
        return True, "OK"

    async def chat_stream(self, account, model, messages, params):
        yield {"type": "text", "text": "hi"}
'''

GOOD_PLUGIN = '''
PLUGIN_API = 1
from app.providers.base import Field, Provider

class GoodProvider(Provider):
    id = "good-test"
    title = "Good Test"
    icon = "T"
    description = "Test plugin"

    def account_fields(self):
        return [Field("api_key", "API-ключ", "password")]

    def seed_models(self):
        return [("model-a", "Model A")]

    async def check(self, account):
        return True, "OK"

    async def chat_stream(self, account, model, messages, params):
        yield {"type": "text", "text": "hi"}
'''


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "good.py").write_text(GOOD_PLUGIN, encoding="utf-8")
        self.env = patch.dict(os.environ, {"MEMUAI_PROVIDERS_DIR": str(self.dir)})
        self.env.start()
        from app.config import config_store
        self.host_patch = patch.object(config_store, "_runtime_host", "127.0.0.1")
        self.port_patch = patch.object(config_store, "_runtime_port", 8317)
        self.host_patch.start()
        self.port_patch.start()
        from fastapi.testclient import TestClient
        from app.main import create_app
        self.client = TestClient(create_app(), base_url="http://127.0.0.1:8317")
        self.account_id = ""

    def tearDown(self) -> None:
        if self.account_id:
            from app.state import state
            state.remove_account(self.account_id)
            self.account_id = ""
        self.client.close()
        self.port_patch.stop()
        self.host_patch.stop()
        self.env.stop()
        self.tmp.cleanup()
        from app.providers import init_registry
        init_registry()

    def _add_good_account(self) -> None:
        response = self.client.post("/api/admin/accounts", json={
            "provider": "good-test", "fields": {"api_key": "x"}})
        self.assertEqual(response.status_code, 200, response.text)
        self.account_id = response.json()["account"]["id"]

    def _mcp(self, payload, headers=None):
        return self.client.post("/mcp", json=payload, headers=headers or {})

    def _mcp_call(self, name, arguments):
        response = self._mcp({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": name, "arguments": arguments}})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertNotIn("error", body, body)
        return body["result"]


class PluginsAdminUiTest(_Base):
    """Новые админ-эндпоинты управления плагинами."""

    def test_plugins_endpoint_returns_dir_and_templates(self):
        response = self.client.get("/api/admin/providers/plugins")
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["dir"], str(self.dir))
        self.assertEqual([t["id"] for t in data["templates"]],
                         ["openai", "kimi", "web", "empty"])
        row = [r for r in data["plugins"] if r["file"] == "good.py"]
        self.assertEqual(len(row), 1)
        self.assertEqual(row[0]["status"], "loaded")

    def test_install_valid_and_rejects_bad(self):
        content = base64.b64encode(GOOD2_PLUGIN.encode("utf-8")).decode("ascii")
        response = self.client.post("/api/admin/providers/plugins/install",
                                    json={"name": "second.py", "content_base64": content})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["file"], "second.py")
        ids = [p["id"] for p in response.json()["providers"]]
        self.assertIn("good-two", ids)
        rows = response.json()["plugins"]
        row = [r for r in rows if r["file"] == "second.py"][0]
        self.assertEqual(row["status"], "loaded")
        self.assertEqual(row["version"], "1.2")
        self.assertEqual(row["author"], "Tester")

        # Повторная установка того же имени — 400, файл не перезаписывается.
        response = self.client.post("/api/admin/providers/plugins/install",
                                    json={"name": "second.py", "content_base64": content})
        self.assertEqual(response.status_code, 400)
        # Путь в имени — 400.
        response = self.client.post("/api/admin/providers/plugins/install",
                                    json={"name": "../evil.py", "content_base64": content})
        self.assertEqual(response.status_code, 400)
        # Неподходящее расширение — 400.
        response = self.client.post("/api/admin/providers/plugins/install",
                                    json={"name": "x.txt", "content_base64": content})
        self.assertEqual(response.status_code, 400)
        # Больше 512 КБ — 400.
        big = base64.b64encode(b"x" * (512 * 1024 + 1)).decode("ascii")
        response = self.client.post("/api/admin/providers/plugins/install",
                                    json={"name": "big.py", "content_base64": big})
        self.assertEqual(response.status_code, 400)
        # Не base64 — 400.
        response = self.client.post("/api/admin/providers/plugins/install",
                                    json={"name": "bad.py", "content_base64": "!!!not-base64"})
        self.assertEqual(response.status_code, 400)

    def test_toggle_disable_enable(self):
        response = self.client.post("/api/admin/providers/plugins/toggle",
                                    json={"file": "good.py", "disabled": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["changed"])
        ids = [p["id"] for p in response.json()["providers"]]
        self.assertNotIn("good-test", ids)
        row = [r for r in response.json()["plugins"] if r["file"] == "good.py"][0]
        self.assertEqual(row["status"], "disabled")

        response = self.client.post("/api/admin/providers/plugins/toggle",
                                    json={"file": "good.py", "disabled": False})
        self.assertEqual(response.status_code, 200, response.text)
        ids = [p["id"] for p in response.json()["providers"]]
        self.assertIn("good-test", ids)

        # Несуществующий файл — 400.
        response = self.client.post("/api/admin/providers/plugins/toggle",
                                    json={"file": "nope.py", "disabled": True})
        self.assertEqual(response.status_code, 400)

    def test_template_openai(self):
        response = self.client.post("/api/admin/providers/plugins/template",
                                    json={"template": "openai", "id": "my-api"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["file"], "my-api.yaml")
        spec = (self.dir / "my-api.yaml").read_text(encoding="utf-8")
        self.assertIn("id: my-api", spec)
        # В этой VM публичные имена резолвятся в CGNAT 198.18.x.x, поэтому
        # base_url из шаблона (api.example.com) отклоняется валидацией —
        # тот же quirk, что и в protocol_contract_test. На обычной машине
        # шаблон грузится как есть; здесь подменяем адрес на loopback
        # и проверяем, что провайдер после этого появляется.
        spec = spec.replace("https://api.example.com/v1", "http://127.0.0.1:1/v1")
        (self.dir / "my-api.yaml").write_text(spec, encoding="utf-8")
        response = self.client.post("/api/admin/providers/reload", json={})
        self.assertEqual(response.status_code, 200, response.text)
        ids = [p["id"] for p in response.json()["providers"]]
        self.assertIn("my-api", ids)

        # Плохой id — 400.
        response = self.client.post("/api/admin/providers/plugins/template",
                                    json={"template": "openai", "id": "BAD ID"})
        self.assertEqual(response.status_code, 400)
        # Неизвестный шаблон — 400.
        response = self.client.post("/api/admin/providers/plugins/template",
                                    json={"template": "nope"})
        self.assertEqual(response.status_code, 400)

    def test_template_kimi_and_delete(self):
        response = self.client.post("/api/admin/providers/plugins/template",
                                    json={"template": "kimi"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["file"], "kimi2api.py")
        self.assertTrue((self.dir / "kimi2api.py").is_file())

        response = self.client.delete("/api/admin/providers/plugins/kimi2api.py")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse((self.dir / "kimi2api.py").exists())
        files = [r["file"] for r in response.json()["plugins"]]
        self.assertNotIn("kimi2api.py", files)

        # Повторное удаление — 400.
        response = self.client.delete("/api/admin/providers/plugins/kimi2api.py")
        self.assertEqual(response.status_code, 400)

    def test_open_folder(self):
        with patch("subprocess.Popen") as popen:
            response = self.client.post("/api/admin/providers/plugins/open-folder", json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["ok"])
        popen.assert_called_once()
        args = popen.call_args[0][0]
        self.assertEqual(args[-1], str(self.dir))


class McpServerTest(_Base):
    """MCP-сервер Proxy: POST /mcp и GET /mcp."""

    def test_initialize(self):
        response = self._mcp({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                              "params": {"protocolVersion": "2025-06-18",
                                         "capabilities": {},
                                         "clientInfo": {"name": "test", "version": "1"}}})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(body["result"]["serverInfo"]["name"], "MemuAI Proxy")
        self.assertIn("tools", body["result"]["capabilities"])
        self.assertIn("resources", body["result"]["capabilities"])
        self.assertTrue(response.headers.get("mcp-session-id"))

    def test_initialize_old_protocol_gets_latest(self):
        response = self._mcp({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                              "params": {"protocolVersion": "1999-01-01",
                                         "capabilities": {},
                                         "clientInfo": {"name": "test", "version": "1"}}})
        self.assertEqual(response.json()["result"]["protocolVersion"], "2025-06-18")

    def test_tools_list(self):
        response = self._mcp({"jsonrpc": "2.0", "id": 1, "method": "tools/list",
                              "params": {}})
        self.assertEqual(response.status_code, 200, response.text)
        names = [t["name"] for t in response.json()["result"]["tools"]]
        self.assertEqual(names, ["list_providers", "list_models", "list_plugins",
                                 "reload_plugins", "chat"])

    def test_tools_call_list_providers(self):
        result = self._mcp_call("list_providers", {})
        providers = result["structuredContent"]["providers"]
        good = [p for p in providers if p["id"] == "good-test"]
        self.assertEqual(len(good), 1)
        self.assertTrue(good[0]["plugin"])
        self.assertFalse(good[0]["has_account"])

    def test_tools_call_list_models_and_chat(self):
        self._add_good_account()
        result = self._mcp_call("list_models", {"provider": "good-test"})
        ids = [m["id"] for m in result["structuredContent"]["models"]]
        self.assertIn("good-test-model-a", ids)

        result = self._mcp_call("chat", {"model": "good-test-model-a",
                                         "messages": [{"role": "user", "content": "hi"}]})
        self.assertNotIn("isError", result)
        self.assertEqual(result["structuredContent"]["content"], "hi")
        self.assertEqual(result["structuredContent"]["model"], "good-test-model-a")
        self.assertEqual(result["structuredContent"]["finish_reason"], "stop")

    def test_tools_call_unknown_tool_and_bad_args(self):
        result = self._mcp_call("nope", {})
        self.assertTrue(result.get("isError"))
        result = self._mcp_call("chat", {"messages": [{"role": "user", "content": "hi"}]})
        self.assertTrue(result.get("isError"))
        result = self._mcp_call("chat", {"model": "good-test-model-a", "messages": []})
        self.assertTrue(result.get("isError"))

    def test_list_plugins_and_reload(self):
        result = self._mcp_call("list_plugins", {})
        files = [p["file"] for p in result["structuredContent"]["plugins"]]
        self.assertIn("good.py", files)
        result = self._mcp_call("reload_plugins", {})
        self.assertTrue(result["structuredContent"]["ok"])

    def test_unknown_method_and_parse_error(self):
        response = self._mcp({"jsonrpc": "2.0", "id": 7, "method": "bogus/method",
                              "params": {}})
        self.assertEqual(response.json()["error"]["code"], -32601)
        response = self.client.post("/mcp", content="{not json",
                                    headers={"Content-Type": "application/json"})
        self.assertEqual(response.json()["error"]["code"], -32700)

    def test_notification_returns_202_without_body(self):
        response = self._mcp({"jsonrpc": "2.0", "method": "notifications/initialized",
                              "params": {}})
        self.assertEqual(response.status_code, 202)

    def test_resources(self):
        response = self._mcp({"jsonrpc": "2.0", "id": 1, "method": "resources/list",
                              "params": {}})
        uris = [r["uri"] for r in response.json()["result"]["resources"]]
        self.assertEqual(uris, ["memuai://providers", "memuai://models", "memuai://plugins"])
        response = self._mcp({"jsonrpc": "2.0", "id": 2, "method": "resources/read",
                              "params": {"uri": "memuai://plugins"}})
        text = response.json()["result"]["contents"][0]["text"]
        self.assertIn("good.py", text)
        response = self._mcp({"jsonrpc": "2.0", "id": 3, "method": "resources/read",
                              "params": {"uri": "memuai://nope"}})
        self.assertEqual(response.json()["error"]["code"], -32602)

    def test_ping_and_batch(self):
        response = self._mcp({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}})
        self.assertEqual(response.json()["result"], {})
        response = self._mcp([{"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}},
                              {"jsonrpc": "2.0", "method": "notifications/initialized"},
                              {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}])
        self.assertEqual(len(response.json()), 2)

    def test_get_sse_stream(self):
        init = self._mcp({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                          "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                     "clientInfo": {"name": "test", "version": "1"}}})
        session = init.headers.get("mcp-session-id")
        self.assertTrue(session)
        with self.client.stream("GET", "/mcp", headers={"Mcp-Session-Id": session}) as resp:
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(resp.headers["content-type"].startswith("text/event-stream"))
            first = next(resp.iter_text())
            self.assertIn("ping", first)
        # Без сессии — 400.
        response = self.client.get("/mcp")
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
