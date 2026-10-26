"""Round 35 (protocol): memuai:// links and the memu command line.

Run: /workspace/work/venv/bin/python -m pytest -q tests/round35_protocol_test.py
Windows-only parts (registry, single-instance forwarding) are tested with mocks.
"""
from __future__ import annotations

import base64
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_TMP = tempfile.mkdtemp(prefix="memu-r35-protocol-")
os.environ["MEMUAI_DATA_DIR"] = _TMP
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")

import pytest  # noqa: E402

from app import deeplinks as dl  # noqa: E402
from app import deeplinks_win  # noqa: E402
from app import memu_cli  # noqa: E402

@pytest.fixture(autouse=True)
def _clean():
    dl.reset_for_tests()
    dl.save_settings(dict(dl.DEFAULT_SETTINGS))
    yield
    dl.reset_for_tests()

def _b64(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj, ensure_ascii=False).encode()).decode().rstrip("=")

@pytest.mark.parametrize("url,route,expect", [
    ("memuai://chat/new?q=%D0%9F%D1%80%D0%B8%D0%B2%D0%B5%D1%82", "chat/new", {"q": "Привет", "send": False}),
    ("memuai://chat/new?q=a+b&model=gpt-6.1-sol&send=0", "chat/new", {"q": "a b", "model": "gpt-6.1-sol"}),
    ("MEMUAI://Chat/New/", "chat/new", {}),
    ("memuai:chat/new", "chat/new", {}),
    ("memuai://new?q=x", "chat/new", {"q": "x"}),
    ("memuai://chat/chat-1a2b3c", "chat", {"id": "chat-1a2b3c"}),
    ("memuai://chat/open?id=chat-1", "chat/open", {"id": "chat-1"}),
    ("memuai://open/plan", "open", {"tab": "plan"}),
    ("memuai://open/inbox", "open", {"tab": "inbox"}),
    ("memuai://settings/appearance", "settings", {"section": "appearance"}),
    ("memuai://settings", "settings", {"section": "general"}),
    ("memuai://module/studio?q=hi", "module", {"id": "studio", "q": "hi"}),
    ("memuai://quick?q=hello", "quick", {"q": "hello"}),
    ("memuai://search?q=%D0%BE%D1%82%D0%BF%D1%83%D1%81%D0%BA", "search", {"q": "отпуск"}),
    ("memuai://proxy/composite", "proxy", {"page": "composite"}),
    ("memuai://task/run?id=abc123", "task/run", {"id": "abc123"}),
    ("memuai://task/abc123", "task", {"id": "abc123"}),
    ("memuai://note?path=C:%5CUsers%5Cme%5Ca.md", "note", {"path": "C:\\Users\\me\\a.md"}),
    ("memuai://files?path=D:/Work", "files", {"path": "D:/Work"}),
    ("memuai://inbox/item-1", "inbox", {"id": "item-1"}),
    ("memuai://dfy/start?task=Find%20tickets", "dfy/start", {"task": "Find tickets"}),
    ("memuai://skill/install?url=https://github.com/a/b/tree/main/x", "skill/install", {"url": "https://github.com/a/b/tree/main/x"}),
    ("memuai://provider/add?name=myapi&base_url=https://api.example.com/v1", "provider/add", {"name": "myapi"}),
    ("memuai://chat/new#fragment", "chat/new", {}),
])
def test_parse_valid(url, route, expect):
    link = dl.parse(url)
    assert link.route == route
    for key, value in expect.items():
        assert link.params[key] == value

@pytest.mark.parametrize("url,code", [
    ("file:///C:/Windows/System32/calc.exe", "bad_scheme"),
    ("javascript:alert(1)", "bad_scheme"),
    ("http://memuai/chat", "bad_scheme"),
    ("memuai2://chat/new", "bad_scheme"),
    ("memuai://", "no_route"),
    ("memuai://exec?cmd=calc", "blocked"),
    ("memuai://shell", "blocked"),
    ("memuai://run?x=1", "blocked"),
    ("memuai://settings/reset", "blocked"),
    ("memuai://nope/thing", "unknown_route"),
    ("memuai://chat/new?q=1&q=2", "duplicate_param"),
    ("memuai://chat/new?evil=1", "unknown_param"),
    ("memuai://chat/new?api_key=sk-x", "secret_param"),
    ("memuai://chat/new?token=abc", "secret_param"),
    ("memuai://chat/new?Password=abc", "secret_param"),
    ("memuai://chat/new?q=sk-proj-abcdefghijklmnopqrstuvwxyz123", "secret_param"),
    ("memuai://chat/new?q=ghp_abcdefghijklmnopqrstuvwxyz0123456789", "secret_param"),
    ("memuai://chat/new?q=AIzaSyA1234567890abcdefghijklmnopqrstuv", "secret_param"),
    ("memuai://chat/new?q=123456789:AAabcdefghijklmnopqrstuvwxyz0123456789", "secret_param"),
    ("memuai://search?q=%E2%80%AEtxt.exe", "bidi"),
    ("memuai://search?q=a%0Ab", "control_chars"),
    ("memuai://chat/new?q=a%00b", "control_chars"),
    ("memuai://chat/new?q=hello world", "control_chars"),
    ("memuai://chat/new?q=" + "a" * 9000, "too_long"),
    ("memuai://search?q=" + "a" * 400, "too_long"),
    ("memuai://note?path=%5C%5Cevil.example.com%5Cshare%5Cx", "unc_path"),
    ("memuai://note?path=//evil/share", "unc_path"),
    ("memuai://note?path=C:%5CWindows%5C..%5Cx", "bad_path"),
    ("memuai://note?path=%25APPDATA%25%5Cx", "bad_path"),
    ("memuai://note?path=relative.txt", "bad_path"),
    ("memuai://note?path=C:%5Ccon", "bad_path"),
    ("memuai://note?path=file:///C:/x", "bad_path"),
    ("memuai://note", "missing_param"),
    ("memuai://open/evil", "bad_value"),
    ("memuai://chat/../../x", "bad_path"),
    ("memuai://chat/a%2Fb", "bad_path"),
    ("memuai://chat/new/extra/parts", "bad_path"),
    ("memuai://skill/install?url=https://evil.com/skill", "bad_host"),
    ("memuai://skill/install?url=http://github.com/a/b", "bad_url"),
    ("memuai://provider/add?name=x&base_url=https://127.0.0.1/v1", "bad_url"),
    ("memuai://provider/add?name=x&base_url=https://user:pw@api.x.com", "bad_url"),
    ("memuai://provider/add?name=x&base_url=https://10.0.0.5/", "bad_url"),
    ("memuai://provider/add?name=x&base_url=https://api.local/", "bad_url"),
    ("memuai://composite/import?data=notbase64!!", "bad_value"),
    ("memuai://composite/import?data=" + _b64({"id": "memu/x", "api_key": "abc"}), "secret_param"),
    ("memuai://composite/import?data=" + _b64("just a string"), "bad_value"),
    ("memuai://chat/new?q=javascript:alert(1)", "bad_value"),
    ("memuai://chat/new?" + "&".join(f"a{i}=1" for i in range(20)), "too_many_params"),
    ("memuai://ch\u202eat/new", "bidi"),
])
def test_parse_rejects(url, code):
    with pytest.raises(dl.LinkError) as info:
        dl.parse(url)
    assert info.value.code == code, (url, info.value.code)
    assert info.value.ru and info.value.en
    assert not re.search("[А-Яа-яЁё]", info.value.en), info.value.en

def test_parser_fuzz_never_crashes():
    rng = random.Random(35)
    seeds = ["memuai://chat/new?q=hi&send=1", "memuai://note?path=C:%5Ca.md", "memuai://open/plan",
             "memuai://composite/import?data=" + _b64({"id": "memu/x", "candidates": ["a"]}), "memuai://task/run?id=x"]
    alphabet = "memuai:/?&=%#\\.-_~ abcXYZ019\u202e\u0000\n\t'\"<>[]@:;+АЯ"
    accepted = 0
    for i in range(4000):
        if i % 3 == 0:
            text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 80)))
            if rng.random() < .5:
                text = "memuai://" + text
        else:
            text = list(rng.choice(seeds))
            for _ in range(rng.randint(1, 6)):
                op = rng.random()
                pos = rng.randint(0, len(text))
                if op < .4:
                    text.insert(pos, rng.choice(alphabet))
                elif op < .7 and text:
                    del text[min(pos, len(text) - 1)]
                elif text:
                    text[min(pos, len(text) - 1)] = rng.choice(alphabet)
            text = "".join(text)
        try:
            link = dl.parse(text)
        except dl.LinkError:
            continue
        accepted += 1
        route = dl.get_route(link.route)
        assert route is not None and route.danger != "block"
        for key, value in link.params.items():
            assert key in route.params
            if isinstance(value, str):
                assert not re.search("[\x00-\x08\x0b\x0c\x0e-\x1f\u202a-\u202e\u2066-\u2069]", value)
    assert accepted > 20

def test_build_roundtrip():
    for route, params in [("chat/new", {"q": "Привет & пока = 100%", "send": True}), ("chat", {"id": "chat-1"}),
                          ("settings", {"section": "links"}), ("note", {"path": "C:\\Users\\Имя\\a b.md"}),
                          ("composite/import", {"data": {"id": "memu/x", "candidates": ["m"]}})]:
        link = dl.build(route, params)
        assert link.startswith("memuai://") and " " not in link
        parsed = dl.parse(link)
        assert parsed.route == route
        for key, value in params.items():
            assert parsed.params[key] == value
    assert dl.build("chat", {"id": "chat-1"}) == "memuai://chat/chat-1"

def test_other_zone_route_contract():
    calls = []
    dl.register_route("testzone/open", lambda p, ctx: calls.append(p) or {"ok": True, "ui": {"do": "testzone.open", "id": p["id"]}},
                      "safe", params={"id": dl.P("id", required=True)}, positional=("id",), title=("Т", "T"), aliases=("tz",))
    try:
        assert dl.parse("memuai://testzone/open/x1").params == {"id": "x1"}
        assert dl.parse("memuai://tz/x1").route == "testzone/open"
        with pytest.raises(ValueError):
            dl.register_route("Bad Name", None)
        with pytest.raises(ValueError):
            dl.register_route("x/y", None, "maybe")
        with pytest.raises(ValueError):
            dl.register_route("x/y", None, params={"api_key": dl.P()})
        with pytest.raises(ValueError):
            dl.register_route("x/y", None, params={}, positional=("id",))
    finally:
        dl.unregister_route("testzone/open")

def _run(item_id, confirmed=False, inputs=None, services=None):
    import asyncio
    return asyncio.run(dl.run(item_id, dl.Ctx(inputs=inputs or {}, services=services or {}), confirmed=confirmed))

def test_safe_link_runs_once():
    res = dl.dispatch("memuai://open/plan", "os")
    assert res["ok"] and res["danger"] == "safe"
    assert dl.pending()[0]["route"] == "open"
    out = _run(res["id"])
    assert out["ok"] and out["ui"] == {"do": "tab.open", "tab": "plan"}
    assert _run(res["id"])["error"] == "expired"

def test_confirm_requires_window_and_time():
    res = dl.dispatch("memuai://chat/new?q=hi&send=1", "os")
    assert res["danger"] == "confirm"
    assert _run(res["id"])["error"] == "needs_confirm"
    assert _run(res["id"], confirmed=True)["error"] == "too_fast"
    items = dl.pending(mark_shown=True)
    assert items[0]["lines"] and any("СРАЗУ" in line for line in items[0]["lines"])
    assert _run(res["id"], confirmed=True)["error"] == "too_fast"
    time.sleep(dl.CONFIRM_MIN_SHOWN + .05)
    out = _run(res["id"], confirmed=True)
    assert out["ok"] and out["ui"]["send"] is True

def test_module_with_text_asks_first():
    assert dl.dispatch("memuai://module/studio", "os")["danger"] == "safe"
    assert dl.dispatch("memuai://module/studio?q=draw", "os")["danger"] == "confirm"

def test_cancel_and_ttl(monkeypatch):
    res = dl.dispatch("memuai://open/plan", "os")
    assert dl.reject(res["id"]) and not dl.pending()
    res = dl.dispatch("memuai://open/plan", "os")
    monkeypatch.setattr(dl, "PENDING_TTL", -1)
    assert not dl.pending()
    assert _run(res["id"])["error"] == "expired"

def test_queue_is_bounded():
    for _ in range(dl.PENDING_MAX + 3):
        dl.BURST_LIMIT.reset()
        dl.dispatch("memuai://open/plan", "os")
    assert len(dl.pending()) == dl.PENDING_MAX

def test_settings_disable_and_always_ask():
    dl.save_settings({"enabled": False})
    assert dl.dispatch("memuai://open/plan", "os")["error"] == "disabled"
    dl.save_settings({"enabled": True, "always_ask": True})
    assert dl.dispatch("memuai://open/plan", "os")["danger"] == "confirm"
    dl.save_settings({"always_ask": False, "cli": False, "chat_links": False})
    assert dl.dispatch("memuai://open/plan", "cli")["error"] == "cli_disabled"
    assert dl.dispatch("memuai://open/plan", "chat")["error"] == "chat_disabled"
    assert dl.dispatch("memuai://open/plan", "os")["ok"]

def test_burst_limit():
    results = [dl.dispatch("memuai://open/plan", "os") for _ in range(8)]
    assert [r["ok"] for r in results].count(True) == 6
    assert results[-1]["error"] == "rate_limited"

def test_journal_and_notifier():
    seen = []
    dl.set_notifier(lambda item: seen.append(item.route.name))
    try:
        dl.dispatch("memuai://open/plan", "cli")
        dl.dispatch("memuai://exec?x=1", "os")
    finally:
        dl.set_notifier(None)
    assert seen == ["open"]
    events = [(r["event"], r["source"]) for r in dl.journal()]
    assert ("rejected", "os") in events and ("queued", "cli") in events

class FakeProxy:
    def __init__(self):
        self.calls = []

    async def __call__(self, method, path, body=None):
        data = json.loads(body.decode()) if body else None
        self.calls.append((method, path, data))
        if path == "/api/admin/accounts":
            return 200, {"ok": True, "account": {"id": "acc1"}}
        if path == "/api/admin/composite/validate":
            return 200, {"model": data["model"], "issues": [{"level": "warning", "message": "мало кандидатов", "message_en": "few candidates"}]}
        if path == "/api/admin/composite/import":
            return 200, {"imported": [m["id"] for m in ([data["data"]] if isinstance(data["data"], dict) else data["data"])], "errors": []}
        if path == "/api/admin/show_window":
            return 200, {"ok": True}
        return 404, {}

@pytest.fixture()
def api():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app import deeplinks_api
    app = FastAPI()
    ran = []

    @app.post("/api/plan/run")
    async def plan_run(body: dict):
        ran.append(body)
        return {"ok": True}

    proxy = FakeProxy()
    deeplinks_api.register(app, proxy_json=proxy)
    client = TestClient(app)
    client.proxy, client.ran = proxy, ran
    yield client
    dl.set_notifier(None)

def _confirm(client, item_id, **body):
    client.get("/api/pg/deeplink/pending?mark=1")
    time.sleep(dl.CONFIRM_MIN_SHOWN + .05)
    return client.post(f"/api/pg/deeplink/item/{item_id}/run", json={"confirm": True, **body})

def test_api_dispatch_pending_run(api):
    r = api.post("/api/pg/deeplink/dispatch", json={"url": "memuai://settings/links", "source": "os"})
    assert r.status_code == 200 and r.json()["danger"] == "safe"
    items = api.get("/api/pg/deeplink/pending?mark=1&lang=en").json()["items"]
    assert items[0]["source_label"].startswith("A link from another program")
    out = api.post(f"/api/pg/deeplink/item/{items[0]['id']}/run", json={}).json()
    assert out["ui"] == {"do": "settings.open", "section": "links"}
    bad = api.post("/api/pg/deeplink/dispatch", json={"url": "memuai://exec?c=calc"})
    assert bad.status_code == 400 and bad.json()["error"] == "blocked"
    assert api.post("/api/pg/deeplink/dispatch", content=b"x" * 70000).status_code == 400

def test_api_provider_key_only_from_dialog(api):
    r = api.post("/api/pg/deeplink/dispatch", json={"url": "memuai://provider/add?name=myapi&base_url=https://api.example.com/v1&models=a,b"}).json()
    item = api.get("/api/pg/deeplink/pending?mark=1").json()["items"][0]
    assert item["danger"] == "confirm" and item["inputs"][0]["type"] == "password"
    secret = "sk-test-NOT-A-REAL-KEY-1234567890"
    out = _confirm(api, r["id"], inputs={"api_key": secret, "evil": "x"}).json()
    assert out["ok"], out
    method, path, body = api.proxy.calls[-1]
    assert path == "/api/admin/accounts" and body["fields"]["api_key"] == secret and "evil" not in body["fields"]
    journal = (Path(_TMP) / "memuai" / "deeplinks_log.json").read_text(encoding="utf-8")
    assert secret not in journal

def test_api_composite_full_preview_then_import(api):
    model = {"id": "memu/smart", "name": "Smart", "candidates": [{"model": "a"}, {"model": "b"}]}
    r = api.post("/api/pg/deeplink/dispatch", json={"url": "memuai://composite/import?data=" + _b64(model)}).json()
    assert r["danger"] == "confirm"
    prev = api.post(f"/api/pg/deeplink/item/{r['id']}/preview", json={"lang": "en"}).json()
    assert prev["ok"] and "memu/smart" in " ".join(prev["lines"]) and '"candidates"' in prev["code"]
    assert any("few candidates" in line for line in prev["lines"])
    out = _confirm(api, r["id"]).json()
    assert out["ok"] and api.proxy.calls[-1][1] == "/api/admin/composite/import"

def test_api_skill_installs_what_was_previewed(api, monkeypatch):
    from app import skills_catalog
    monkeypatch.setattr(skills_catalog, "resolve_url", lambda url: {"repo": "owner/repo", "skills": [{"path": "s1", "name": "s1"}]})
    monkeypatch.setattr(skills_catalog, "skill_detail", lambda repo, path: {"name": "Skill One", "description": "Does one thing", "body": "# Steps\nDo it."})
    r = api.post("/api/pg/deeplink/dispatch", json={"url": "memuai://skill/install?url=https://github.com/owner/repo"}).json()
    early = _confirm(api, r["id"]).json()
    assert early["error"] == "no_preview"
    r = api.post("/api/pg/deeplink/dispatch", json={"url": "memuai://skill/install?url=https://github.com/owner/repo"}).json()
    prev = api.post(f"/api/pg/deeplink/item/{r['id']}/preview", json={}).json()
    assert "Skill One" in prev["code"] and "Do it." in prev["code"]
    monkeypatch.setattr(skills_catalog, "skill_detail", lambda repo, path: {"name": "Evil", "body": "changed"})
    out = _confirm(api, r["id"]).json()
    assert out["ui"]["do"] == "skills.add" and out["ui"]["items"][0]["name"] == "Skill One"

def test_api_task_run(api, monkeypatch):
    from app import pgdata
    monkeypatch.setattr(pgdata, "load_schedules", lambda: [{"id": "t1", "name": "Утренние новости", "prompt": "Собери новости"}])
    r = api.post("/api/pg/deeplink/dispatch", json={"url": "memuai://task/run?id=t1"}).json()
    item = api.get("/api/pg/deeplink/pending?mark=1").json()["items"][0]
    assert any("Утренние новости" in line for line in item["lines"])
    out = _confirm(api, r["id"]).json()
    assert out["ok"] and api.ran == [{"id": "t1", "lang": "ru"}]
    r = api.post("/api/pg/deeplink/dispatch", json={"url": "memuai://task/run?id=nope"}).json()
    assert _confirm(api, r["id"]).json()["error"] == "not_found"

def test_api_proxy_page_and_note(api, monkeypatch, tmp_path):
    r = api.post("/api/pg/deeplink/dispatch", json={"url": "memuai://proxy/composite"}).json()
    assert api.post(f"/api/pg/deeplink/item/{r['id']}/run", json={}).json()["ok"]
    assert api.proxy.calls[-1] == ("POST", "/api/admin/show_window", {"tab": "composite"})
    from app import memunote_manager
    opened = []
    monkeypatch.setattr(memunote_manager, "open_app", lambda files: opened.append(files) or {"ok": True})
    f = tmp_path / "n.md"
    f.write_text("x")
    r = api.post("/api/pg/deeplink/dispatch", json={"url": dl.build("note", {"path": str(f)})}).json()
    assert r["danger"] == "confirm"
    assert _confirm(api, r["id"]).json()["ok"] and opened == [[str(f)]]

def test_api_settings_build_journal(api):
    s = api.get("/api/pg/deeplink/settings").json()
    assert s["settings"]["enabled"] and any(r["name"] == "chat/new" for r in s["routes"])
    assert not any(r["danger"] == "block" for r in s["routes"])
    api.post("/api/pg/deeplink/settings", json={"always_ask": True, "bogus": 1})
    assert dl.load_settings()["always_ask"] is True and "bogus" not in dl.load_settings()
    b = api.post("/api/pg/deeplink/build", json={"route": "chat", "params": {"id": "chat-9"}}).json()
    assert b["link"] == "memuai://chat/chat-9"
    assert api.post("/api/pg/deeplink/build", json={"route": "chat/new", "params": {"q": "sk-proj-abcdefghijklmnopqrstuvwxyz1"}}).status_code == 400
    assert api.post("/api/pg/deeplink/journal/clear", json={}).json()["ok"] and dl.journal() == []

class FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 1
    KEY_WRITE = 2
    REG_SZ = 1

    def __init__(self):
        self.keys = {}

    class _H:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def OpenKey(self, hive, path, reserved=0, access=0):
        if path not in self.keys:
            raise FileNotFoundError(path)
        return self._H(path)

    def CreateKeyEx(self, hive, path, reserved=0, access=0):
        parts = path.split("\\")
        for i in range(1, len(parts) + 1):
            self.keys.setdefault("\\".join(parts[:i]), {})
        return self._H(path)

    def QueryValueEx(self, handle, name):
        if name not in self.keys[handle.path]:
            raise FileNotFoundError(name)
        return self.keys[handle.path][name], 1

    def SetValueEx(self, handle, name, reserved, kind, value):
        self.keys[handle.path][name] = value

    def DeleteKey(self, hive, path):
        if any(k.startswith(path + "\\") for k in self.keys):
            raise OSError("has subkeys")
        if path not in self.keys:
            raise FileNotFoundError(path)
        del self.keys[path]

def test_registry_register_status_unregister(monkeypatch):
    fake = FakeWinreg()
    monkeypatch.setattr(deeplinks_win, "_reg", fake)
    exe = r"C:\Users\me\AppData\Local\Programs\MemuAI\MemuAI\MemuAI.exe"
    monkeypatch.setattr(deeplinks_win, "exe_path", lambda: exe)
    assert deeplinks_win.status()["registered"] is False
    st = deeplinks_win.register()
    assert st["ok"] and st["current"]
    root = fake.keys[r"Software\Classes\memuai"]
    assert root[""] == "URL:MemuAI" and root["URL Protocol"] == ""
    assert fake.keys[r"Software\Classes\memuai\shell\open\command"][""] == f'"{exe}" --deeplink "%1"'
    deeplinks_win.unregister()
    assert not any(k.startswith(r"Software\Classes\memuai") for k in fake.keys)

def test_registry_self_heal(monkeypatch):
    fake = FakeWinreg()
    monkeypatch.setattr(deeplinks_win, "_reg", fake)
    monkeypatch.setattr(deeplinks_win.sys, "platform", "win32")
    exe = r"D:\Moved\MemuAI\MemuAI.exe"
    monkeypatch.setattr(deeplinks_win, "exe_path", lambda: exe)
    fake.CreateKeyEx(0, r"Software\Classes\memuai\shell\open\command")
    fake.keys[r"Software\Classes\memuai\shell\open\command"][""] = '"C:\\Old\\MemuAI.exe" --deeplink "%1"'
    res = deeplinks_win.self_heal()
    assert res["repaired"] and deeplinks_win.status()["current"]
    assert deeplinks_win.self_heal()["repaired"] is False
    dl.save_settings({"enabled": False})
    deeplinks_win.unregister()
    assert deeplinks_win.self_heal().get("skipped") and not deeplinks_win.status()["registered"]
    monkeypatch.setattr(deeplinks_win, "exe_path", lambda: None)
    dl.save_settings({"enabled": True})
    assert deeplinks_win.self_heal().get("skipped")

def test_link_from_argv():
    f = deeplinks_win.link_from_argv
    assert f(["--deeplink", "memuai://open/plan"]) == "memuai://open/plan"
    assert f(["--deeplink=memuai://x"]) == "memuai://x"
    assert f(["MEMUAI://chat/new"]) == "MEMUAI://chat/new"
    assert f(["--new-chat"]) == ""

class FakeServer:
    """MemuAI (/api/pg/*) + Proxy (/health, /v1/*) on one loopback port for the CLI and forwarding tests."""

    def __init__(self):
        self.requests = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype="application/json"):
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                outer.requests.append(("GET", self.path, None, dict(self.headers)))
                p = self.path
                if p == "/api/pg/ping":
                    return self._send(200, {"ok": True})
                if p == "/health":
                    return self._send(200, {"service": "memuai-proxy"})
                if p == "/v1/models":
                    return self._send(200, {"data": [{"id": "test-model"}, {"id": "other"}]})
                if p.startswith("/api/plan/tasks"):
                    return self._send(200, {"tasks": [{"id": "t1", "name": "Новости", "enabled": True}]})
                if p.startswith("/api/pg/inbox"):
                    return self._send(200, {"items": [{"title": "Вопрос", "source": "dfy", "read": False}], "counts": {"open": 1}})
                if p.startswith("/api/fsindex/search"):
                    return self._send(200, {"results": [{"path": "C:\\a.pdf"}]})
                if p.startswith("/api/pg/deeplink/settings"):
                    return self._send(200, {"settings": {"enabled": True, "always_ask": False}})
                return self._send(404, {"error": "nope"})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                outer.requests.append(("POST", self.path, body, dict(self.headers)))
                p = self.path
                if p == "/api/pg/deeplink/dispatch":
                    try:
                        link = dl.parse(body["url"])
                    except dl.LinkError as exc:
                        return self._send(400, exc.as_dict())
                    route = dl.get_route(link.route)
                    danger = "confirm" if route.danger == "confirm" or (route.confirm_if and route.confirm_if(link.params)) else "safe"
                    return self._send(200, {"ok": True, "id": "x", "danger": danger, "title": route.title[0]})
                if p == "/api/plan/run":
                    return self._send(200, {"ok": True})
                if p == "/v1/chat/completions":
                    if body.get("stream"):
                        chunks = b"".join(b"data: " + json.dumps({"choices": [{"delta": {"content": c}}]}).encode() + b"\n\n" for c in ("При", "вет"))
                        return self._send(200, chunks + b"data: [DONE]\n\n", "text/event-stream")
                    text = body["messages"][-1]["content"]
                    return self._send(200, {"model": body["model"], "choices": [{"message": {"content": "echo:" + text}}]})
                return self._send(404, {"error": "nope"})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()

@pytest.fixture()
def server(monkeypatch):
    srv = FakeServer()
    memu_dir = Path(_TMP) / "memuai"
    memu_dir.mkdir(parents=True, exist_ok=True)
    (memu_dir / "memuai.json").write_text(json.dumps({"url": srv.url}), encoding="utf-8")
    (memu_dir / "proxy.json").write_text(json.dumps({"url": srv.url}), encoding="utf-8")
    monkeypatch.delenv("MEMUAI_PROXY_URL", raising=False)
    monkeypatch.delenv("MEMUAI_API_KEY", raising=False)
    monkeypatch.delenv("MEMUAI_TOKEN", raising=False)
    yield srv
    srv.close()
    for name in ("memuai.json", "proxy.json"):
        (memu_dir / name).unlink(missing_ok=True)

def test_forward_to_running_instance(server):
    import memuai
    assert memuai._forward_deeplink("memuai://open/plan", attempts=2) is True
    method, path, body, _ = server.requests[-1]
    assert path == "/api/pg/deeplink/dispatch" and body == {"url": "memuai://open/plan", "source": "os"}

def test_forward_gives_up_when_nothing_runs(monkeypatch):
    import memuai
    monkeypatch.setattr(memuai.time, "sleep", lambda s: None)
    assert memuai._forward_deeplink("memuai://open/plan", attempts=3) is False

class Stdin(io.StringIO):
    def __init__(self, text="", tty=False):
        super().__init__(text)
        self._tty = tty

    def isatty(self):
        return self._tty

def cli(monkeypatch, capsys, *args, stdin="", tty=True):
    monkeypatch.setattr(sys, "stdin", Stdin(stdin, tty))
    code = memu_cli.main(list(args))
    out = capsys.readouterr()
    return code, out.out, out.err

def test_cli_ask_plain_json_stream_and_pipe(server, monkeypatch, capsys):
    code, out, _ = cli(monkeypatch, capsys, "ask", "-m", "test-model", "Сколько?")
    assert code == 0 and out.strip() == "echo:Сколько?"
    code, out, _ = cli(monkeypatch, capsys, "--json", "ask", "-m", "test-model", "hi")
    assert code == 0 and json.loads(out)["content"] == "echo:hi"
    code, out, _ = cli(monkeypatch, capsys, "ask", "-m", "test-model", "--stream", "x")
    assert code == 0 and out.strip() == "Привет"
    code, out, _ = cli(monkeypatch, capsys, "ask", "-m", "test-model", "Опиши", stdin="diff --git a b", tty=False)
    assert out.strip() == "echo:Опиши\n\ndiff --git a b"
    code, out, _ = cli(monkeypatch, capsys, "ask", "default?")
    sent = [r for r in server.requests if r[1] == "/v1/chat/completions"][-1][2]
    assert sent["model"] == "test-model"
    code, _, err = cli(monkeypatch, capsys, "ask")
    assert code == 2 and "memu:" in err

def test_cli_token_header(server, monkeypatch, capsys):
    cli(monkeypatch, capsys, "--token", "secret-token-1", "models")
    headers = [r for r in server.requests if r[1] == "/v1/models"][-1][3]
    assert headers.get("Authorization") == "Bearer secret-token-1"

def test_cli_models_status_inbox_search_tasks(server, monkeypatch, capsys):
    code, out, _ = cli(monkeypatch, capsys, "models")
    assert code == 0 and out.split() == ["test-model", "other"]
    code, out, _ = cli(monkeypatch, capsys, "--lang", "en", "status")
    assert code == 0 and "running" in out and "memuai links:  on" in out
    code, out, _ = cli(monkeypatch, capsys, "--json", "inbox")
    assert json.loads(out)["items"][0]["title"] == "Вопрос"
    code, out, _ = cli(monkeypatch, capsys, "search-files", "*.pdf")
    assert out.strip() == "C:\\a.pdf"
    code, out, _ = cli(monkeypatch, capsys, "task", "list")
    assert "t1" in out and "Новости" in out

def test_cli_links_go_through_the_window(server, monkeypatch, capsys):
    code, out, _ = cli(monkeypatch, capsys, "--lang", "ru", "chat", "new", "Идея", "--send")
    assert code == 0 and "подтвердите" in out.lower()
    body = [r for r in server.requests if r[1] == "/api/pg/deeplink/dispatch"][-1][2]
    assert body["source"] == "cli" and dl.parse(body["url"]).params == {"q": "Идея", "send": True, "temp": False}
    code, out, _ = cli(monkeypatch, capsys, "--lang", "ru", "open", "memuai://open/plan")
    assert code == 0 and "Открыто" in out
    code, out, _ = cli(monkeypatch, capsys, "open", "memuai://exec?x=1")
    assert code == 4
    code, out, _ = cli(monkeypatch, capsys, "task", "run", "t1")
    assert code == 0 and dl.parse([r for r in server.requests if r[1] == "/api/pg/deeplink/dispatch"][-1][2]["url"]).route == "task/run"
    before = len([r for r in server.requests if r[1] == "/api/plan/run"])
    code, out, _ = cli(monkeypatch, capsys, "task", "run", "t1", "--yes")
    assert code == 0 and len([r for r in server.requests if r[1] == "/api/plan/run"]) == before + 1

def test_cli_link_help_completion_errors(monkeypatch, capsys):
    code, out, _ = cli(monkeypatch, capsys, "link", "chat/new", "q=Привет")
    assert code == 0 and dl.parse(out.strip()).params["q"] == "Привет"
    code, out, _ = cli(monkeypatch, capsys, "link", "chat/new", "api_key=x")
    assert code == 4
    code, out, _ = cli(monkeypatch, capsys, "link", "nope")
    assert code == 2
    code, out, _ = cli(monkeypatch, capsys, "--lang", "en", "--help")
    assert code == 0 and "Usage" in out and not re.search("[А-Яа-я]", out)
    code, out, _ = cli(monkeypatch, capsys, "--lang", "ru", "help")
    assert "Использование" in out
    for shell in ("bash", "zsh", "powershell"):
        code, out, _ = cli(monkeypatch, capsys, "completion", shell)
        assert code == 0 and "memu" in out and "ask" in out
    code, _, _ = cli(monkeypatch, capsys, "frobnicate")
    assert code == 2
    code, out, _ = cli(monkeypatch, capsys, "--json", "link", "--list")
    assert any(r["name"] == "chat/new" for r in json.loads(out)["routes"])

def test_cli_unreachable(monkeypatch, capsys):
    monkeypatch.setenv("MEMUAI_PROXY_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(memu_cli, "launch_memuai", lambda link="": False)
    code, _, err = cli(monkeypatch, capsys, "models")
    assert code == 3
    code, _, _ = cli(monkeypatch, capsys, "inbox")
    assert code == 3
    code, _, _ = cli(monkeypatch, capsys, "status")
    assert code == 3
    code, _, _ = cli(monkeypatch, capsys, "open", "memuai://open/plan")
    assert code == 3

def test_memu_entry_point_runs():
    env = dict(os.environ, MEMU_LANG="en")
    res = subprocess.run([sys.executable, str(ROOT / "memu.py"), "--help"], capture_output=True, text=True, env=env, timeout=60)
    assert res.returncode == 0 and "memu" in res.stdout

def test_cli_stays_light():
    """memu.exe must not pull the server stack in (small, fast)."""
    code = ("import sys, app.memu_cli, app.deeplinks; app.deeplinks.routes(); "
            "bad=[m for m in ('fastapi','httpx','uvicorn','app.pgdata','app.config') if m in sys.modules]; print(bad)")
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(ROOT), timeout=60,
                         env=dict(os.environ, MEMUAI_DATA_DIR=_TMP))
    assert res.stdout.strip() == "[]", res.stdout + res.stderr

def test_installer_registers_and_removes_protocol(tmp_path):
    from installer import engine, products

    class P(engine.Platform):
        def __init__(self):
            self.registry = {}

        def reg_get(self, root, key, name):
            return self.registry.get((root, key), {}).get(name)

        def reg_set(self, root, key, name, value, kind="sz"):
            self.registry.setdefault((root, key), {})[name] = value

        def reg_delete_key(self, root, key):
            if any(k[1].startswith(key + "\\") for k in self.registry):
                raise OSError("subkeys")
            self.registry.pop((root, key), None)

    plat = P()
    eng = engine.Engine(products.SUITE, plat)
    app_dir = tmp_path / "MemuAI"
    eng._apply_protocols(app_dir, ["proxy", "memuai"])
    cmd = plat.registry[("HKCU", r"Software\Classes\memuai\shell\open\command")][""]
    assert "--deeplink" in cmd and str(app_dir) in cmd and cmd.endswith('"%1"')
    other = engine.Engine(products.SUITE, plat)
    other._apply_protocols(tmp_path / "Elsewhere", [], removing=True)
    assert ("HKCU", r"Software\Classes\memuai\shell\open\command") in plat.registry
    eng._apply_protocols(app_dir, [], removing=True)
    assert not any(k[1].startswith(r"Software\Classes\memuai") for k in plat.registry)
    assert ("memuai", r"MemuAI\MemuCLI") in products.SUITE.path_entries
    assert ("memucode", "MemuCode") in products.SUITE.path_entries
    src = Path(engine.__file__).read_text(encoding="utf-8")
    assert "self._apply_protocols(app_dir, components)" in src and "self._apply_protocols(app_dir, [], removing=True)" in src

def test_build_targets_and_specs():
    import ast
    import build
    memu = build.TARGETS["memu"]
    assert memu["console"] and memu["entry"] == "memu.py" and memu["name"] == "memu"
    assert {"app.memu_cli", "app.deeplinks", "app.deeplinks_routes"} <= set(memu["hidden"])
    assert {"app.deeplinks", "app.deeplinks_routes", "app.deeplinks_api", "app.deeplinks_win"} <= set(build.TARGETS["memuai"]["hidden"])
    for spec, names in (("memu.spec", {"app.memu_cli", "app.deeplinks", "app.deeplinks_routes"}),
                        ("MemuAI.spec", {"app.deeplinks", "app.deeplinks_api", "app.deeplinks_win", "app.deeplinks_routes"})):
        tree = ast.parse((ROOT / spec).read_text(encoding="utf-8"))

        values = [n.value for n in ast.walk(tree) if isinstance(n, ast.Assign)
                  and any(getattr(t, "id", "") == "hiddenimports" for t in n.targets)]
        if not values:
            values = [kw.value for n in ast.walk(tree) if isinstance(n, ast.Call)
                      and getattr(n.func, "id", "") == "Analysis"
                      for kw in n.keywords if kw.arg == "hiddenimports"]
        assert len(values) == 1, spec
        hidden = ast.literal_eval(values[0])
        assert names <= set(hidden), spec
    assert "place_memu_cli()" in (ROOT / "build.py").read_text(encoding="utf-8")

def test_ui_wiring_and_languages():
    pg = ROOT / "app" / "pgstatic"
    index = (pg / "index.html").read_text(encoding="utf-8")
    assert "/deeplinks.js" in index and "/deeplinks.css" in index
    app_js = (pg / "app.js").read_text(encoding="utf-8")
    assert "MemuLinks.chatMenuItems(c)" in app_js and 'P("links", "access"' in app_js
    assert 'page === "links" && window.MemuLinksSettings' in app_js and 'id="pane-links"' in app_js
    js = (pg / "deeplinks.js").read_text(encoding="utf-8")
    pairs = re.findall(r'L\(\s*"((?:[^"\\]|\\.)*)"\s*,\s*"((?:[^"\\]|\\.)*)"\s*\)', js)
    assert len(pairs) > 40
    for ru, en in pairs:
        assert not re.search("[А-Яа-яЁё]", en), en
    if shutil.which("node"):
        assert subprocess.run(["node", "--check", str(pg / "deeplinks.js")], capture_output=True).returncode == 0
    for route in dl.routes():
        for lang in ("en",):
            assert not re.search("[А-Яа-яЁё]", route.title[1]), route.name

def test_describe_lines_bilingual():
    samples = {"chat/new": {"q": "hi", "send": True, "model": "m", "temp": True}, "note": {"path": "C:\\a.md"},
               "dfy/start": {"task": "x"}, "provider/add": {"name": "a", "base_url": "https://x.com", "models": "m"},
               "skill/install": {"url": "https://github.com/a/b"}, "composite/import": {"data": {"id": "memu/x"}},
               "open": {"tab": "plan"}, "proxy": {"page": "logs"}, "quick": {"q": "x"}, "search": {"q": "x"}}
    for name, params in samples.items():
        route = dl.get_route(name)
        en = route.describe(params, "en")
        ru = route.describe(params, "ru")
        assert en and ru
        assert not any(re.search("[А-Яа-яЁё]", line) for line in en), (name, en)
