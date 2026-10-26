"""Loopback integration tests: discovery, actual CLI permissions and image delivery."""
from __future__ import annotations

import base64
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.cli.agent import Agent
from app.cli.tools import connected
from tests.memucode_cli_test import make_runtime, run, setUpModule, tearDownModule  # noqa: F401

REAL_CLIENT = httpx.AsyncClient

class ConnectedTools(unittest.TestCase):
    def make(self, *, write=False, screenshot=False, failure=False, malformed=False):
        runtime, _ = make_runtime(settings={"permission_mode": "acceptEdits", "permission_sync": False})
        runtime.ui.interactive = False
        requests = []
        options = []
        tool = "chrome_screenshot" if screenshot else "telegram_use_send" if write else "telegram_use_history"
        signature = {"chrome_screenshot": "chrome_screenshot{tab_id}",
                     "telegram_use_send": "telegram_use_send{chat,text,reply_to,silent}",
                     "telegram_use_history": "telegram_use_history{chat,limit,before_id,search}"}[tool]
        def handler(request):
            requests.append(request)
            if request.method == "GET":
                return httpx.Response(200, json={"services": [
                    {"service": "telegram_use" if not screenshot else "chrome_extension", "connected": True,
                     "tools": {"bad": "shape"} if malformed else [signature]},
                    {"service": "google", "connected": True, "tools": ["google_gmail_send{}"]}],
                    "write_tools": [tool] if write else []})
            if failure:
                return httpx.Response(400, json={"tool": tool, "error": "disconnected"})
            value = {"data": base64.b64encode(b"test-image").decode(), "media_type": "image/jpeg"} if screenshot \
                else {"messages": [{"id": 10, "text": "hello"}]}
            return httpx.Response(200, json={"tool": tool, "value": value})
        def client(**kwargs):
            options.append(kwargs)
            return REAL_CLIENT(transport=httpx.MockTransport(handler), **kwargs)
        return runtime, requests, options, tool, client

    def test_discovery_only_exposes_connected_round43_services_with_typed_schemas(self):
        runtime, requests, options, name, client = self.make()
        async def scenario():
            with patch.object(connected, "_runtime_url", return_value="http://127.0.0.1:9999"), \
                    patch.object(connected.httpx, "AsyncClient", side_effect=client):
                await connected.refresh(runtime)
                await connected.refresh(runtime)
        run(scenario())
        tool = runtime.tools.resolve(name)
        self.assertIsNotNone(tool)
        self.assertEqual(tool.schema()["parameters"]["properties"]["limit"]["type"], "integer")
        self.assertEqual(tool.required, ("chat",))
        self.assertNotIn("google_gmail_send", runtime.tools.tools)
        self.assertEqual(len(requests), 1)
        self.assertFalse(options[0]["trust_env"])
        self.assertFalse(options[0]["follow_redirects"])

    def test_readonly_tool_reaches_http_and_returns_real_messages(self):
        runtime, requests, _, name, client = self.make()
        async def scenario():
            with patch.object(connected, "_runtime_url", return_value="http://127.0.0.1:9999"), \
                    patch.object(connected.httpx, "AsyncClient", side_effect=client):
                await connected.refresh(runtime)
                return await Agent(runtime).execute([{"name": name, "args": {"chat": "Иван", "limit": 50}}], "turn")
        results = run(scenario())
        self.assertTrue(results[0][1].ok)
        self.assertIn("hello", results[0][1].output)
        self.assertIn("untrusted", results[0][1].output)
        self.assertEqual(len(requests), 2)
        self.assertEqual(json.loads(requests[1].content)["args"], {"chat": "Иван", "limit": 50})

    def test_external_write_is_not_auto_allowed_by_accept_edits(self):
        runtime, requests, _, name, client = self.make(write=True)
        async def scenario():
            with patch.object(connected, "_runtime_url", return_value="http://127.0.0.1:9999"), \
                    patch.object(connected.httpx, "AsyncClient", side_effect=client):
                await connected.refresh(runtime)
                return await Agent(runtime).execute([{"name": name, "args": {"chat": "Иван", "text": "hello"}}], "turn")
        results = run(scenario())
        self.assertFalse(results[0][1].ok)
        self.assertTrue(results[0][1].meta.get("denied"))
        self.assertEqual([r.method for r in requests], ["GET"])

    def test_write_confirmation_precedes_the_post_and_contains_reviewed_arguments(self):
        runtime, requests, _, name, client = self.make(write=True)
        runtime.ui.interactive = True
        async def approve(request):
            self.assertEqual([r.method for r in requests], ["GET"])
            self.assertIn("hello", request.prepared.preview)
            self.assertIn("Иван", request.prepared.preview)
            return {"choice": "yes"}
        runtime.ui.ask_permission = AsyncMock(side_effect=approve)
        async def scenario():
            with patch.object(connected, "_runtime_url", return_value="http://127.0.0.1:9999"), \
                    patch.object(connected.httpx, "AsyncClient", side_effect=client):
                await connected.refresh(runtime)
                return await Agent(runtime).execute([{"name": name, "args": {"chat": "Иван", "text": "hello"}}], "turn")
        self.assertTrue(run(scenario())[0][1].ok)
        payload = json.loads(requests[1].content)
        self.assertIs(payload["confirmed"], True)
        self.assertEqual(payload["args"], {"chat": "Иван", "text": "hello"})
        runtime.ui.ask_permission.assert_awaited_once()

    def test_chrome_screenshot_is_attached_as_an_image_not_base64_text(self):
        runtime, _, _, name, client = self.make(screenshot=True)
        async def scenario():
            with patch.object(connected, "_runtime_url", return_value="http://127.0.0.1:9999"), \
                    patch.object(connected.httpx, "AsyncClient", side_effect=client):
                await connected.refresh(runtime)
                return await Agent(runtime).execute([{"name": name, "args": {"tab_id": 1}}], "turn")
        result = run(scenario())[0][1]
        self.assertTrue(result.ok)
        self.assertEqual(base64.b64decode(result.images[0]["data"]), b"test-image")
        self.assertNotIn(result.images[0]["data"], result.output)
        self.assertTrue(runtime.session.messages[-1].get("images"))

    def test_server_failure_is_visible_to_model_as_failure(self):
        runtime, _, _, name, client = self.make(failure=True)
        async def scenario():
            with patch.object(connected, "_runtime_url", return_value="http://127.0.0.1:9999"), \
                    patch.object(connected.httpx, "AsyncClient", side_effect=client):
                await connected.refresh(runtime)
                return await Agent(runtime).execute([{"name": name, "args": {"chat": "Иван"}}], "turn")
        result = run(scenario())[0][1]
        self.assertFalse(result.ok)
        self.assertIn("disconnected", result.error)

    def test_closed_memuai_does_not_start_or_contact_any_service(self):
        runtime, _, _, _, _ = self.make()
        with patch.object(connected, "_runtime_url", return_value=""), \
                patch.object(connected.httpx, "AsyncClient", side_effect=AssertionError("unexpected network")):
            run(connected.refresh(runtime))
        self.assertEqual(runtime.tools._connected_names, set())

    def test_malformed_status_does_not_break_the_agent(self):
        runtime, _, _, _, client = self.make(malformed=True)
        with patch.object(connected, "_runtime_url", return_value="http://127.0.0.1:9999"), \
                patch.object(connected.httpx, "AsyncClient", side_effect=client):
            run(connected.refresh(runtime))
        self.assertEqual(runtime.tools._connected_names, set())

if __name__ == "__main__":
    unittest.main()
