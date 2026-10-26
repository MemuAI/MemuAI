"""Round 40 (r40-mcp): the MCP hub of the whole MemuAI — app/mcp_client.py (pool, transports, resources, secrets
in logs), app/mcp_hub.py (catalogue, protected secrets, permissions, scopes, @mcp: mentions, Quick Ask tool),
app/mcp_hub_api.py (routes), the «mcp_call» Planning step (app/flow_engine.py) and the window hooks.
No network: a stdio echo server (tests/fixtures/mcp_echo_server.py) and an in-process Streamable HTTP server.
Run: python -m pytest -q tests/round40_mcp_test.py
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="memu-r40-mcp-"))
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")

from app import flow_engine as fe  # noqa: E402
from app import mcp_client, mcp_hub  # noqa: E402

FIXTURE = str(ROOT / "tests" / "fixtures" / "mcp_echo_server.py")
TOKEN = "tok_super_secret_123456"

@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_client, "CONFIG_PATH", tmp_path / "mcp.json")
    monkeypatch.setattr(mcp_hub, "TOOLS_CACHE", tmp_path / "mcp-tools-cache.json")
    monkeypatch.setattr(mcp_hub, "SECRET_DIR", tmp_path / "secrets")
    monkeypatch.setattr(mcp_hub, "_STORE", None)
    monkeypatch.setattr(mcp_client, "registry", mcp_client.MCPRegistry())
    yield tmp_path

def run(coro):
    return asyncio.run(coro)

def add_echo(name="echo", **extra):
    servers = mcp_hub._raw_servers()
    servers[name] = {"command": sys.executable, "args": ["-u", FIXTURE], "env": {}, **extra}
    mcp_hub._write_servers(servers)
    if extra.get("env") is None:
        mcp_hub.update(name, {"env": {"ECHO_TOKEN": TOKEN}})

def test_config_keeps_transport_and_hub_fields_and_old_panel_does_not_wipe_them(sandbox):
    mcp_hub._write_servers({"remote": {"type": "sse", "url": "http://127.0.0.1:1/sse", "headers": {"X": "1"},
                                       "read_only": True, "chat": True, "agents": ["meme-1"], "confirm": "always"},
                            "local": {"command": "uvx", "args": ["mcp-server-time"]}})
    cfg = mcp_client.load_config()["servers"]
    assert cfg["remote"]["type"] == "sse" and cfg["remote"]["read_only"] and cfg["remote"]["agents"] == ["meme-1"]
    assert cfg["local"]["type"] == "stdio" and cfg["local"]["confirm"] == "write" and not cfg["local"]["chat"]

    mcp_client.save_config({"servers": {"remote": {"command": "", "args": [], "enabled": False, "description": ""},
                                        "local": {"command": "uvx", "args": ["mcp-server-time"], "enabled": True}}})
    cfg = mcp_client.load_config()["servers"]
    assert cfg["remote"]["url"] and cfg["remote"]["read_only"] and cfg["remote"]["confirm"] == "always"
    assert cfg["remote"]["enabled"] is False

def test_catalog_install_keeps_secrets_out_of_mcp_json(sandbox):
    name = mcp_hub.install("postgres", {"DATABASE_URI": "postgresql://u:pa55word@db:5432/x"}, read_only=True)
    raw = (sandbox / "mcp.json").read_text("utf-8")
    assert "pa55word" not in raw and "${secret:mcp/postgres/DATABASE_URI}" in raw
    spec = mcp_client.load_config()["servers"][name]
    assert spec["args"] == ["postgres-mcp", "--access-mode=restricted"] and spec["read_only"]
    assert mcp_client.resolve_secrets(spec["env"])["DATABASE_URI"].endswith("@db:5432/x")

    other = mcp_hub.install("postgres", {"DATABASE_URI": "postgresql://a@b/c"}, read_only=False)
    assert other == "postgres-2" and "--access-mode=unrestricted" in mcp_client.load_config()["servers"][other]["args"]
    ha = mcp_hub.install("homeassistant", {"HA_URL": "http://ha.local:8123/", "HA_TOKEN": "eyJhbGciOiJIUzI1NiJ9.x"})
    spec = mcp_client.load_config()["servers"][ha]
    assert spec["type"] == "http" and spec["url"] == "http://ha.local:8123/api/mcp"
    assert "eyJ" not in (sandbox / "mcp.json").read_text("utf-8")
    assert mcp_client.resolve_secrets(spec["headers"])["Authorization"] == "Bearer eyJhbGciOiJIUzI1NiJ9.x"
    with pytest.raises(mcp_hub.HubError):
        mcp_hub.install("jira", {"JIRA_URL": "https://x.atlassian.net"})
    rows = mcp_hub.public_rows()
    assert all(not e["value"] for r in rows for e in r["env"] if e["secret"])
    assert "pa55word" not in json.dumps(rows) and "eyJ" not in json.dumps(rows)
    mcp_hub.remove(name)
    assert mcp_hub.secret_value("mcp/postgres/DATABASE_URI") == ""

def test_catalog_entries_are_complete():
    ids = {c["id"] for c in mcp_hub.CATALOG}
    for wanted in ("postgres", "mysql", "clickhouse", "jira", "linear", "gitlab", "obsidian", "logseq", "homeassistant"):
        assert wanted in ids
    for c in mcp_hub.CATALOG:
        assert c["ru"] and c["en"] and c["docs"].startswith("https://") and c["cat"] in mcp_hub.CATS
        assert (c["type"] == "stdio" and c["command"] in ("npx", "uvx")) or c["type"] in ("http", "sse")
        for f in c.get("fields", []):
            assert f["ru"] and f["en"] and f["target"] in ("env", "arg", "header", "url")

def test_plain_tokens_from_the_old_panel_are_migrated(sandbox):
    mcp_hub._write_servers({"gh": {"command": "npx", "args": ["x"], "env": {"GITHUB_TOKEN": "ghp_plain_value_1", "LEVEL": "3"}}})
    assert mcp_hub.migrate_plain_secrets() == 1
    raw = (sandbox / "mcp.json").read_text("utf-8")
    assert "ghp_plain_value_1" not in raw and '"LEVEL": "3"' in raw
    assert mcp_client.resolve_secrets(mcp_client.load_config()["servers"]["gh"]["env"])["GITHUB_TOKEN"] == "ghp_plain_value_1"

def test_stdio_server_tools_resources_permissions_logs_restart(sandbox):
    add_echo()

    async def scenario():
        reg = mcp_client.registry
        checked = await mcp_hub.check("echo")
        assert checked["ok"], checked
        names = {t["name"]: t for t in checked["tools"]}
        assert names["echo"]["read_only"] and names["echo"]["policy"] == "allow"
        assert names["add_note"]["policy"] == "confirm" and not names["add_note"]["read_only"]
        assert {r["uri"] for r in checked["resources"]} == {"memo://readme", "memo://notes"}

        out = await mcp_hub.call("echo", "whoami", {}, confirmed=True)
        assert out["content"] == f"token length {len(TOKEN)}"

        await asyncio.sleep(0.2)
        logs = "\n".join(reg.live("echo").logs)
        assert "echo server started" in logs and TOKEN not in logs and "•••" in logs
        assert (await mcp_hub.call("echo", "echo", {"text": "привет"}))["content"] == "echo: привет"
        pending = await mcp_hub.call("echo", "add_note", {"text": "x"})
        assert pending["needs_confirm"] and pending["tool"] == "add_note"
        done = await mcp_hub.call("echo", "add_note", {"text": "первая"}, confirmed=True)
        assert done["content"] == "saved (1)"
        assert (await reg.read_resource("echo", "memo://notes"))["text"] == "первая"

        assert (await mcp_hub.call("echo", "add_note", {"text": "2"}, full_access=True))["content"] == "saved (2)"

        mcp_hub.update("echo", {"tool_rules": {"echo": "deny"}})
        assert "error" in await mcp_hub.call("echo", "echo", {"text": "a"})
        mcp_hub.update("echo", {"tool_rules": {"echo": None}, "read_only": True})
        assert "только чтение" in (await mcp_hub.call("echo", "add_note", {"text": "b"}, confirmed=True))["error"]
        mcp_hub.update("echo", {"read_only": False, "confirm": "always"})
        assert (await mcp_hub.call("echo", "echo", {"text": "c"}))["needs_confirm"]
        mcp_hub.update("echo", {"confirm": "write"})

        crashed = await mcp_hub.call("echo", "crash", {}, confirmed=True)
        assert "error" in crashed
        again = await mcp_hub.call("echo", "echo", {"text": "снова"})
        assert again["content"] == "echo: снова", again
        await reg.stop_all()

    run(scenario())

def test_scopes_chat_agent_quick_and_cached_tools(sandbox):
    add_echo(agents=["meme-db"])

    async def scenario():
        assert (await mcp_hub.check("echo"))["ok"]
        await mcp_client.registry.stop_all()
        assert mcp_hub.known("echo")["tools"] and not mcp_hub.known("echo")["live"]
        assert mcp_hub.scoped("chat") == []
        rows = mcp_hub.scoped("chat", "meme-db")
        assert rows and {t["name"] for t in rows[0]["tools"]} >= {"echo", "add_note"}
        assert mcp_client.registry.live("echo") is None
        refused = await mcp_hub.call("echo", "echo", {"text": "x"}, scope="chat")
        assert "не подключён" in refused["error"]
        ok = await mcp_hub.call("echo", "echo", {"text": "x"}, scope="chat", agent="meme-db")
        assert ok["content"] == "echo: x"
        assert mcp_hub.quick_description() == ""
        mcp_hub.update("echo", {"quick": True})
        line = mcp_hub.quick_description()
        assert "echo.echo" in line and "add_note" not in line
        res = await mcp_hub.quick_handler({"server": "echo", "tool": "add_note", "args": {}}, {"lang": "en"})
        assert "approval" in res["error"]
        assert mcp_hub.split_tool_name("mcp_echo_add_note") == ("echo", "add_note")
        await mcp_client.registry.stop_all()

    run(scenario())

def test_mcp_mentions_read_resources_from_a_worker_thread(sandbox):
    add_echo()

    async def scenario():
        mcp_client.registry.loop = asyncio.get_running_loop()
        assert (await mcp_hub.check("echo"))["ok"]
        rows = await asyncio.to_thread(mcp_hub.mention_search, "echo/read", {"lang": "ru"})
        assert rows and rows[0]["ref"] == {"server": "echo", "uri": "memo://readme"}
        res = await asyncio.to_thread(mcp_hub.mention_resolve, "echo/memo://readme", {})
        assert "склад открыт" in res["text"] and res["source"] == "MCP · echo"
        from app import mentions
        mcp_hub.register_mentions()
        blocks = await mentions.resolve_many([{"provider": "mcp", "ref": {"server": "echo", "uri": "memo://readme"}, "title": "readme"}], {})
        assert "склад открыт" in blocks[0]["text"], blocks
        await mcp_client.registry.stop_all()

    run(scenario())

def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

def test_streamable_http_server_with_secret_header(sandbox):
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    import uvicorn
    seen = {}

    async def mcp(request):
        msg = await request.json()
        seen["auth"] = request.headers.get("authorization")
        if "id" not in msg:
            return JSONResponse({}, 202)
        method = msg["method"]
        result = {"initialize": {"capabilities": {"tools": {}}, "serverInfo": {"name": "remote"}},
                  "tools/list": {"tools": [{"name": "list_issues", "inputSchema": {}}]},
                  "tools/call": {"content": [{"type": "text", "text": "3 issues"}]}}.get(method, {})
        return JSONResponse({"jsonrpc": "2.0", "id": msg["id"], "result": result}, headers={"Mcp-Session-Id": "s1"})

    app = Starlette(routes=[Route("/mcp", mcp, methods=["POST", "DELETE"])])

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    try:
        name = mcp_hub.add_custom({"name": "linear-test", "type": "http", "url": f"http://127.0.0.1:{port}/mcp", "token": "lin_api_secret99"})
        assert "lin_api_secret99" not in (sandbox / "mcp.json").read_text("utf-8")

        async def scenario():
            checked = await mcp_hub.check(name)
            assert checked["ok"], checked
            assert checked["tools"][0]["read_only"]
            out = await mcp_hub.call(name, "list_issues", {})
            assert out["content"] == "3 issues"
            await mcp_client.registry.stop_all()

        run(scenario())
        assert seen["auth"] == "Bearer lin_api_secret99"
    finally:
        server.should_exit = True
        thread.join(5)

def test_flow_step_mcp_call_normalize_validate_run_and_dry_run(sandbox):
    add_echo()
    steps = fe.normalize_steps([{"type": "mcp_call", "server": "echo", "tool": "echo", "args": {"text": "{{vars.who}}"}},
                                {"type": "mcp_call", "server": "echo", "tool": "add_note", "args": {"text": "n"}}])
    assert steps[0]["approved"] is False and steps[0]["timeout"] == 120
    assert not fe.validate_flow(steps, "ru")
    bad = fe.normalize_steps([{"type": "mcp_call", "server": "", "tool": ""}])
    texts = " ".join(p["text"] for p in fe.validate_flow(bad, "ru"))
    assert "сервер MCP" in texts and "Инструмент" in texts
    assert "echo.add_note" in fe.describe(steps[1], "ru")
    assert any(row["type"] == "mcp_call" for row in fe.step_catalog("en"))

    class Sv(fe.Services):
        def prefs(self):
            return {}

        async def record_result(self, *a, **k):
            pass

    async def scenario(task, dry):
        mcp_client.registry.loop = asyncio.get_running_loop()
        await mcp_hub.check("echo")
        out = await fe.execute_run(task, fe.new_run(task, dry_run=dry), Sv(lang="ru"))
        await mcp_client.registry.stop_all()
        return out

    task = {"id": "m1", "name": "MCP", "kind": "flow", "vars": {"who": "мир"},
            "steps": fe.normalize_steps([{"type": "set", "var": "who", "value": "мир"}, steps[0]])}
    result = run(scenario(task, False))
    assert result["ok"], result.get("error")
    assert "echo: мир" in json.dumps(result, ensure_ascii=False)

    task2 = {"id": "m2", "name": "MCP2", "kind": "flow", "steps": fe.normalize_steps([steps[1]])}
    result = run(scenario(task2, False))
    assert not result["ok"] and "Разрешить без подтверждения" in json.dumps(result, ensure_ascii=False)
    approved = fe.normalize_steps([{**steps[1], "approved": True}])
    result = run(scenario({**task2, "steps": approved}, False))
    assert result["ok"] and "saved (1)" in json.dumps(result, ensure_ascii=False)

    result = run(scenario({**task2, "steps": approved}, True))
    assert result["ok"] and "saved" not in json.dumps(result, ensure_ascii=False)

def test_window_hooks_and_bilingual_ui():
    app_js = (ROOT / "app/pgstatic/app.js").read_text("utf-8")
    hub_js = (ROOT / "app/pgstatic/mcp-hub.js").read_text("utf-8")
    index = (ROOT / "app/pgstatic/index.html").read_text("utf-8")
    assert "MemuMcpHub.run(call" in app_js and "MemuMcpHub.promptBlock(chatState)" in app_js
    assert "MemuMcpHub.listHtml()" in app_js and "MemuMcpHub.subHtml()" in app_js and "MemuMcpHub.mount()" in app_js
    assert "/mcp-hub.js?v=" in index and "/mcp-hub.css?v=" in index
    assert "setInterval" not in hub_js and "MutationObserver" not in hub_js

    import re
    pairs = re.findall(r'L\("([^"]*)", "([^"]*)"\)', hub_js)
    assert len(pairs) > 40 and all(ru and en for ru, en in pairs)
    flows = (ROOT / "app/pgstatic/flows.js").read_text("utf-8")
    assert 'case "mcp_call"' in flows and "mcp_call:" in flows

def test_routes_register(sandbox):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app import mcp_hub_api
    app = FastAPI()
    mcp_hub_api.register(app)
    client = TestClient(app)
    data = client.get("/api/mcp/hub?lang=en").json()
    assert len(data["catalog"]) >= 15 and data["servers"] == []
    r = client.post("/api/mcp/install", json={"catalog_id": "time", "values": {}, "lang": "en"}).json()
    assert r["name"] == "time"
    assert client.post("/api/mcp/install", json={"catalog_id": "jira", "values": {}, "lang": "en"}).json()["error"].startswith("Fill in")
    assert client.post("/api/mcp/server/time", json={"chat": True}).json()["ok"]
    assert mcp_client.load_config()["servers"]["time"]["chat"] is True
    assert client.get("/api/mcp/scope?scope=chat").json()["servers"][0]["name"] == "time"
    assert client.delete("/api/mcp/server/time").json()["ok"]
