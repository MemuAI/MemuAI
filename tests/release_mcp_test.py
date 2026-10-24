"""MCP startup race/cancellation regressions, entirely in a temporary profile."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from app import mcp_client
from app.cli.tools.mcp import CliMCP, HttpMCPServer

SERVER = '''import json, sys, time
for line in sys.stdin:
    msg = json.loads(line)
    method, ident = msg.get("method"), msg.get("id")
    if method == "initialize":
        time.sleep(float(sys.argv[1]))
        result = {"capabilities": {}}
    elif method == "tools/list":
        result = {"tools": [{"name": "echo", "inputSchema": {}}]}
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": "ok"}]}
    else:
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": ident, "result": result}), flush=True)
'''


class MCPStartupSecurity(unittest.TestCase):
    def setUp(self):
        self.sandbox = tempfile.TemporaryDirectory(prefix="memuai-mcp-test-")
        self.addCleanup(self.sandbox.cleanup)

    def spec(self, delay="0.1"):
        return {"command": sys.executable, "args": ["-u", "-c", SERVER, delay],
                "env": {}, "enabled": True, "cwd": self.sandbox.name}

    async def process_started(self, registry):
        for _ in range(200):
            await asyncio.sleep(0.01)
            rows = registry._servers if hasattr(registry, "_servers") else registry.servers
            if rows.get("test") is not None and rows["test"].process is not None:
                return rows["test"], rows["test"].process
        self.fail("test process did not start")

    def test_concurrent_registry_and_cli_share_one_start(self):
        async def scenario(cli):
            registry = CliMCP(types.SimpleNamespace()) if cli else mcp_client.MCPRegistry()
            if cli:
                registry.config = lambda: {"test": self.spec()}
            created = []
            original = asyncio.create_subprocess_exec
            async def spawn(*args, **kwargs):
                await asyncio.sleep(0.03)  # expose the pre-process startup window
                process = await original(*args, **kwargs)
                created.append(process)
                return process
            with patch.object(mcp_client, "load_config", return_value={"servers": {"test": self.spec()}}), \
                    patch.object(asyncio, "create_subprocess_exec", side_effect=spawn):
                try:
                    servers = await asyncio.gather(*(registry.server("test") for _ in range(8)))
                    self.assertTrue(all(server is servers[0] for server in servers))
                    self.assertEqual(len(created), 1)
                    self.assertTrue(servers[0].ready.is_set())
                    self.assertEqual((await servers[0].call("echo", {}))["content"], "ok")
                finally:
                    await registry.stop_all()
            self.assertIsNotNone(created[0].returncode)
        for cli in (False, True):
            asyncio.run(scenario(cli))

    def test_cancel_last_caller_stops_process_and_allows_retry(self):
        async def scenario():
            registry = mcp_client.MCPRegistry()
            with patch.object(mcp_client, "load_config", return_value={"servers": {"test": self.spec("0.3")}}):
                try:
                    waiter = asyncio.create_task(registry.server("test"))
                    server, process = await self.process_started(registry)
                    waiter.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await waiter
                    self.assertFalse(server.running)
                    self.assertIsNotNone(process.returncode)
                    self.assertTrue(server.ready.is_set())
                    retried = await registry.server("test")
                    self.assertIsNot(retried, server)
                    self.assertTrue(retried.running)
                finally:
                    await registry.stop_all()
        asyncio.run(scenario())

    def test_cancelling_one_waiter_preserves_other_waiter(self):
        async def scenario():
            registry = mcp_client.MCPRegistry()
            with patch.object(mcp_client, "load_config", return_value={"servers": {"test": self.spec("0.3")}}):
                try:
                    first = asyncio.create_task(registry.server("test"))
                    second = asyncio.create_task(registry.server("test"))
                    server, process = await self.process_started(registry)
                    first.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await first
                    self.assertTrue(server.running)
                    self.assertIs(await second, server)
                    self.assertEqual([tool["name"] for tool in server.tools], ["echo"])
                finally:
                    await registry.stop_all()
            self.assertIsNotNone(process.returncode)
        asyncio.run(scenario())

    def test_stop_all_during_startup_stops_process(self):
        async def scenario():
            registry = mcp_client.MCPRegistry()
            with patch.object(mcp_client, "load_config", return_value={"servers": {"test": self.spec("30")}}):
                waiter = asyncio.create_task(registry.server("test"))
                server, process = await self.process_started(registry)
                await registry.stop_all()
                with self.assertRaises(asyncio.CancelledError):
                    await waiter
                self.assertFalse(server.running)
                self.assertIsNotNone(process.returncode)
        asyncio.run(scenario())

    def test_http_start_is_shared(self):
        async def scenario():
            server = HttpMCPServer("test", {"url": "https://example.invalid/mcp"})
            calls = []
            async def request(method, params):
                calls.append(method)
                await asyncio.sleep(0.03)
                return {"tools": [{"name": "echo"}]} if method == "tools/list" else {}
            async def post(*args, **kwargs):
                return {}
            server._request, server._post = request, post
            try:
                await asyncio.gather(*(server.start() for _ in range(6)))
                self.assertEqual(calls, ["initialize", "tools/list"])
                self.assertTrue(server.running)
            finally:
                await server.stop()
        asyncio.run(scenario())

    def test_http_cancel_closes_client_and_releases_session(self):
        async def scenario():
            server = HttpMCPServer("test", {"url": "https://example.invalid/mcp"})
            entered = asyncio.Event()
            state = {"closed": False, "deleted": False}
            class Client:
                async def delete(self, *args, **kwargs):
                    state["deleted"] = True
                async def aclose(self):
                    state["closed"] = True
            server._client = Client()
            async def request(method, params):
                server.session_id = "owned-session"
                entered.set()
                await asyncio.Event().wait()
            server._request = request
            waiter = asyncio.create_task(server.start())
            await entered.wait()
            waiter.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiter
            self.assertEqual(state, {"closed": True, "deleted": True})
            self.assertIsNone(server._client)
            self.assertEqual(server.session_id, "")
            self.assertFalse(server.running)
            self.assertTrue(server.ready.is_set())
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
