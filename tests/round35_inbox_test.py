"""Round 35 (inbox): «Входящие» — the store (app/inbox.py), routes (app/inbox_api.py), producers and
button handlers (app/inbox_hooks.py) and the UI wiring (app/pgstatic/inbox.js / inbox.css).

Run: python -m pytest -q tests/round35_inbox_test.py
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="memu-r35-inbox-"))
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")

from app import inbox, inbox_hooks  # noqa: E402


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMUAI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MEMUCODE_CONFIG_DIR", str(tmp_path / "memucode"))
    from app import pgdata
    monkeypatch.setattr(pgdata, "MEMU_DIR", tmp_path / "memuai")
    (tmp_path / "memuai").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(inbox_hooks, "_lang", lambda: "ru")
    inbox._reset_for_tests()
    inbox_hooks._mc_seen.clear()
    inbox_hooks._acct_flags.clear()
    yield tmp_path
    inbox._reset_for_tests()


# --------------------------------------------------------------------------- store
def test_post_dedupe_resolve_and_groups(fresh):
    a = inbox.post("approval", "Разрешить оплату?", "1 290 ₽", source="dfy", ref={"task_id": "t"},
                   actions=[{"id": "approve", "label": "Да"}, "deny"], dedupe_key="dfy:t:q", severity="ask")
    assert a.startswith("ib_")
    b = inbox.post("approval", "Разрешить оплату? (2)", source="dfy", dedupe_key="dfy:t:q", severity="ask")
    assert a == b, "an open item with the same dedupe_key is updated, not duplicated"
    it = inbox.get("dfy:t:q")
    assert it["count"] == 2 and it["title"].endswith("(2)") and it["group"] == "confirm"
    assert inbox.post("question", "Q?", source="dfy", severity="ask") and inbox.post("notice", "N", source="models")
    c = inbox.counts()
    assert (c["confirm"], c["ask"], c["notify"], c["unread"], c["open"]) == (1, 1, 1, 3, 3)
    assert inbox.resolve("dfy:t:q", result="approve") is True
    assert inbox.resolve("dfy:t:q") is False, "resolving twice is a no-op"
    assert inbox.get(a)["status"] == "resolved" and inbox.get(a)["group"] == "done"
    # a new post after it was resolved creates a new item
    assert inbox.post("approval", "again", source="dfy", dedupe_key="dfy:t:q") != a
    assert inbox.counts()["done"] == 1


def test_list_filters_search_read_snooze(fresh):
    inbox.post("notice", "Новые модели API", "qwen3.8-coder", source="models")
    x = inbox.post("error", "Задача не выполнена: Сводка", "ConnectError", source="schedule", severity="warn")
    assert [i["source"] for i in inbox.list(source="schedule")] == ["schedule"]
    assert [i["id"] for i in inbox.list(q="connecterror")] == [x]
    assert inbox.list(group="notify") and not inbox.list(group="ask")
    assert inbox.mark_read([x]) == 1 and inbox.mark_read([x]) == 0
    assert inbox.counts()["unread"] == 1
    assert inbox.mark_read(all_items=True) == 1
    assert inbox.snooze(x, 3600) is True
    snoozed = inbox.get(x)
    assert snoozed["snoozed"] and snoozed["snooze_until"] > time.time()
    assert inbox.counts()["open"] == 1, "a snoozed item leaves the badge"
    assert x not in [i["id"] for i in inbox.list(include_snoozed=False)]
    assert inbox.snooze(x, 0) and not inbox.get(x)["snoozed"]


def test_persistence_cross_process_and_atomic_file(fresh):
    x = inbox.post("notice", "persist me", source="ui")
    path = fresh / "inbox" / "inbox.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["items"][0]["id"] == x
    # another process (the Proxy) writes the same file: this process sees it
    code = ("import os,sys; sys.path.insert(0, %r); os.environ['MEMUAI_DATA_DIR']=%r;"
            "from app import inbox; inbox.post('error','from proxy',source='proxy',dedupe_key='proxy:quota:a');"
            "inbox.resolve(%r, result='seen')") % (str(ROOT), str(fresh), x)
    subprocess.run([sys.executable, "-c", code], check=True, timeout=60)
    assert inbox.get("proxy:quota:a")["title"] == "from proxy"
    assert inbox.get(x)["status"] == "resolved"
    # a broken file does not break the hub
    path.write_text("{broken", encoding="utf-8")
    inbox._reset_for_tests()
    assert isinstance(inbox.list(), list)
    assert inbox.post("notice", "after corruption", source="ui")


def test_thread_safety(fresh):
    def worker(n):
        for i in range(15):
            inbox.post("notice", f"w{n}-{i}", source="ui", dedupe_key=f"k{n}-{i}")
    threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(inbox.list(limit=600)) == 90
    keys = [i["dedupe_key"] for i in inbox.list(limit=600)]
    assert len(set(keys)) == 90


def test_retention_caps_and_ephemeral(fresh, monkeypatch):
    monkeypatch.setattr(inbox, "MAX_ITEMS", 20)
    monkeypatch.setattr(inbox, "MAX_OPEN_PER_SOURCE", 1000)
    for i in range(10):
        inbox.resolve(inbox.post("notice", f"old{i}", source="ui"))
    for i in range(25):
        inbox.post("question", f"q{i}", source="dfy", severity="ask")
    rows = inbox.list(limit=600)
    assert len(rows) == 20
    assert all(r["status"] == "open" for r in rows), "closed items go first when the cap is reached"
    # resolved items older than RESOLVED_TTL_DAYS are pruned on the next write
    inbox._reset_for_tests()
    y = inbox.post("notice", "ancient", source="ui")
    inbox.resolve(y)
    path = fresh / "inbox" / "inbox.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for it in data["items"]:
        it["updated"] = time.time() - 40 * 86400
    path.write_text(json.dumps(data), encoding="utf-8")
    inbox.post("notice", "fresh", source="ui")
    titles = [r["title"] for r in inbox.list(limit=600)]
    assert "ancient" not in titles and "fresh" in titles
    assert "q24" in titles, "open questions are never pruned by age"
    # ephemeral items (computer-control steps) vanish when resolved
    e = inbox.post("approval", "step", source="computer", dedupe_key="computer:t:1", ephemeral=True)
    inbox.resolve(e)
    assert inbox.get(e) is None


def test_subscribe_and_version(fresh):
    seen = []
    unsub = inbox.subscribe(lambda ev, it: seen.append(ev))
    v0 = inbox.version()
    x = inbox.post("notice", "n", source="ui")
    inbox.mark_read(x)
    inbox.resolve(x)
    unsub()
    inbox.post("notice", "after", source="ui")
    assert seen == ["post", "read", "resolve"]
    assert inbox.version() != v0


# --------------------------------------------------------------------------- actions
def test_run_action_routes_to_handler(fresh):
    calls = []

    def handler(item, action, payload):
        calls.append((item["ref"], action, payload))
        return {"resolve": True}
    inbox.register_handler("demo", handler)
    x = inbox.post("approval", "ok?", source="demo", ref={"k": 1},
                   actions=[{"id": "yes", "label": "Да"}, {"id": "open", "label": "Открыть", "kind": "ui"}])
    assert inbox.run_action(x, "open") == {"ok": True, "open": "", "resolved": False}
    assert calls == [] and inbox.get(x)["read"] is True
    res = inbox.run_action(x, "yes", {"text": "t"})
    assert res["ok"] and res["resolved"] and calls == [({"k": 1}, "yes", {"text": "t"})]
    assert inbox.get(x)["result"] == "yes"
    assert inbox.run_action(x, "yes")["error"] == "already_resolved"
    assert inbox.run_action("nope", "yes")["error"] == "not_found"

    async def ahandler(item, action, payload):
        raise ValueError("boom")
    inbox.register_handler("demo2", ahandler)
    y = inbox.post("question", "q", source="demo2", actions=["go"])
    res = asyncio.run(inbox.arun_action(y, "go"))
    assert res == {"ok": False, "error": "boom"} and inbox.get(y)["status"] == "open"
    # no handler → the button just closes the item; «dismiss» / «ack» are built in
    z = inbox.post("notice", "n", source="nobody", actions=["ok"])
    assert inbox.run_action(z, "ok")["resolved"]
    w = inbox.post("notice", "n2", source="demo")
    assert inbox.run_action(w, "dismiss")["resolved"] and inbox.get(w)["status"] == "dismissed"


def test_api_routes(fresh):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app import inbox_api
    app = FastAPI()
    inbox_api.register(app)
    started = []

    async def start():
        started.append(1)
    inbox_api.set_startup(start)
    c = TestClient(app)
    assert c.get("/api/pg/inbox/counts").json()["open"] == 0
    assert c.get("/api/pg/inbox/counts").status_code == 200 and started == [1], "startup runs once"
    r = c.post("/api/pg/inbox/post", json={"title": "Из интерфейса", "source": "ui", "kind": "notice",
                                          "actions": [{"id": "open", "label": "Открыть", "url": "https://x"}]})
    iid = r.json()["id"]
    item = c.get(f"/api/pg/inbox/item/{iid}").json()
    assert item["actions"][0]["kind"] == "ui", "UI-posted buttons never reach a server handler"
    assert c.post("/api/pg/inbox/post", json={}).status_code == 400
    data = c.get("/api/pg/inbox", params={"status": "open", "q": "интерфейс"}).json()
    assert [i["id"] for i in data["items"]] == [iid] and data["counts"]["unread"] == 1
    assert c.post("/api/pg/inbox/read", json={"ids": [iid]}).json()["changed"] == 1
    assert c.post(f"/api/pg/inbox/item/{iid}/snooze", json={"preset": "tomorrow"}).json()["ok"]
    assert 0 < inbox.get(iid)["snooze_until"] - time.time() <= 86400 * 1.5
    assert c.post(f"/api/pg/inbox/item/{iid}/action", json={"action": "open"}).json()["open"] == "https://x"
    assert c.post(f"/api/pg/inbox/item/{iid}/action", json={}).status_code == 400
    assert c.post("/api/pg/inbox/item/nope/action", json={"action": "x"}).status_code == 404
    assert c.post(f"/api/pg/inbox/item/{iid}/dismiss").json()["ok"]
    assert c.post("/api/pg/inbox/clear-done").json()["removed"] == 1
    assert c.get("/api/pg/inbox/item/" + iid).status_code == 404


# --------------------------------------------------------------------------- producers
class FakeManager:
    def __init__(self, task):
        self.task, self.answers = task, []

    def get(self, task_id):
        return self.task if task_id == self.task.id else None

    async def answer(self, task_id, payload):
        if str(payload.get("question_id")) != str(self.task.question.get("id")):
            raise RuntimeError("stale")
        self.answers.append(payload)
        inbox_hooks.dfy_question_closed(self.task, self.task.question)   # DFYManager._ask finally
        self.task.question = None
        return self.task


def test_dfy_question_roundtrip(fresh):
    q = {"id": "q1", "kind": "confirm", "prompt": "DFY хочет нажать «Оплатить». Разрешить?", "amount": "1 290 ₽"}
    task = NS(id="t1", goal="Купить корм", chat_id="c1", source="chat", question=q)
    inbox_hooks.dfy_question(task, q)
    it = inbox.get("dfy:t1:q1")
    assert it["group"] == "confirm" and [a["id"] for a in it["actions"]] == ["approve", "deny", "open"]
    assert "1 290" in it["body"] and it["ref"]["chat_id"] == "c1"
    mgr = FakeManager(task)
    inbox.register_handler("dfy", inbox_hooks._dfy_handler(mgr))
    res = asyncio.run(inbox.arun_action(it["id"], "approve"))
    assert res["ok"] and res["resolved"]
    assert mgr.answers == [{"question_id": "q1", "approve": True}]
    # choice question: the button sends the option text; answering elsewhere closes the item here too
    q2 = {"id": "q2", "kind": "choice", "prompt": "Какое время?", "choices": ["Вт 10:00", "Ср 15:30"]}
    task.question = q2
    inbox_hooks.dfy_question(task, q2)
    it2 = inbox.get("dfy:t1:q2")
    assert it2["group"] == "ask" and it2["actions"][1]["label"] == "Ср 15:30"
    assert asyncio.run(inbox.arun_action(it2["id"], "choice:1"))["resolved"]
    assert mgr.answers[-1] == {"question_id": "q2", "answer": "Ср 15:30"}
    # a stale item (the task moved on) is closed with an explanation instead of answering the next question
    q3 = {"id": "q3", "kind": "text", "prompt": "Как зовут?"}
    task.question = q3
    inbox_hooks.dfy_question(task, q3)
    task.question = {"id": "q4", "kind": "confirm", "prompt": "next"}
    res = asyncio.run(inbox.arun_action("dfy:t1:q3", "reply", {"text": "Юра"}))
    assert res["ok"] is False and res["resolved"] and mgr.answers[-1]["question_id"] == "q2"
    # secrets are never answered from the inbox: only «open the form» / «decline»
    q5 = {"id": "q5", "kind": "secret", "label": "карта", "prompt": "Нужна карта"}
    inbox_hooks.dfy_question(task, q5)
    acts = inbox.get("dfy:t1:q5")["actions"]
    assert acts[0]["kind"] == "ui" and not any(a.get("form") for a in acts)


def test_real_dfy_manager_ask_roundtrip(fresh):
    """The real DFYManager._ask posts the question; the inbox button answers it through DFYManager.answer;
    an answer given in the chat card closes the inbox item."""
    from app.dfy import DFYManager, DFYTask
    mgr = DFYManager(None, lambda: {}, data_root=fresh / "memuai")
    inbox.register_handler("dfy", inbox_hooks._dfy_handler(mgr))

    async def scenario():
        task = DFYTask("Купить корм", [], "chat", "chat-1", "m", 10, "irreversible")
        mgr.tasks[task.id] = task
        ask = asyncio.create_task(mgr._ask(task, {"kind": "confirm", "prompt": "Оплатить 1 290 ₽?"}, timeout=10))
        await asyncio.sleep(0.05)
        qid = task.question["id"]
        it = inbox.get(f"dfy:{task.id}:{qid}")
        assert it and it["status"] == "open" and it["ref"]["chat_id"] == "chat-1"
        res = await inbox.arun_action(it["id"], "deny")
        assert res["ok"] and res["resolved"]
        assert await ask == {"approve": False}
        assert inbox.get(it["id"])["result"] == "deny"
        # answered in the chat card instead: the inbox item closes by itself
        ask2 = asyncio.create_task(mgr._ask(task, {"kind": "text", "prompt": "Какой адрес?"}, timeout=10))
        await asyncio.sleep(0.05)
        key = f"dfy:{task.id}:{task.question['id']}"
        assert inbox.is_open(key)
        await mgr.answer(task.id, {"answer": "Шяуляй", "question_id": task.question["id"]})
        assert await ask2 == {"answer": "Шяуляй"}
        assert inbox.get(key)["status"] == "resolved" and inbox.get(key)["result"] == "answered"
    asyncio.run(scenario())


def test_dfy_ask_hook_in_manager_source():
    src = (ROOT / "app" / "dfy.py").read_text(encoding="utf-8")
    assert '_inbox_hook("dfy_question", task, asked)' in src
    assert '_inbox_hook("dfy_question_closed", task, asked)' in src


def test_memory_review_sync_both_ways(fresh):
    from app import memory_v2 as mem
    mem.propose([{"op": "add", "fact": "Пользователь живёт в Шяуляе", "confidence": 0.5},
                 {"op": "add", "fact": "Любит тёмную тему", "confidence": 0.5}], service="chat")
    pend = [i for i in mem.load()["items"] if i["status"] == "pending"]
    if not pend:   # «auto» mode saved them at once: switch to «ask» and propose again
        mem.mutate(lambda doc: doc.setdefault("settings", {}).update({"mode": "ask"}))
        mem.propose([{"op": "add", "fact": "Пользователь живёт в Шяуляе"}, {"op": "add", "fact": "Любит тёмную тему"}], service="chat")
        pend = [i for i in mem.load()["items"] if i["status"] == "pending"]
    assert len(pend) == 2
    rows = inbox.list(status="open", source="memory")
    assert len(rows) == 2 and all(r["group"] == "ask" for r in rows)
    inbox.register_handler("memory", inbox_hooks._memory_handler())
    a = next(r for r in rows if "Шяуля" in r["title"])
    res = inbox.run_action(a["id"], "edit", {"text": "Живёт в Шяуляе, Литва"})
    assert res["resolved"] and inbox.get(a["id"])["result"] == "edit"
    assert any(i["text"] == "Живёт в Шяуляе, Литва" and i["status"] == "active" for i in mem.load()["items"])
    # rejecting in the Memory window closes the inbox item too
    b = next(r for r in rows if "тёмную" in r["title"])
    mem.reject(b["ref"]["item_id"])
    assert inbox.get(b["id"])["status"] == "resolved"
    assert inbox.counts()["open"] == 0


def test_schedule_results(fresh):
    from app import pgdata, scheduler
    pgdata.save_schedules([{"id": "s1", "name": "Сводка новостей", "prompt": "x", "enabled": True}]) \
        if hasattr(pgdata, "save_schedules") else None
    scheduler._record_result("s1", False, "ConnectError")
    fail = inbox.get("schedule:fail:s1")
    assert fail and fail["severity"] == "warn" and "ConnectError" in fail["body"]
    scheduler._record_result("s1", False, "ConnectError again")
    assert inbox.get("schedule:fail:s1")["count"] == 2, "repeated failures update one item"
    scheduler._record_result("s1", True, "Готовая сводка")
    assert inbox.get("schedule:fail:s1")["status"] == "resolved"
    assert inbox.get("schedule:result:s1")["body"] == "Готовая сводка"
    scheduler._record_result("s1", True, "draft", {"mode": "work", "work_status": "pending", "project_id": "p",
                                                   "pending_work": [{"path": "a.txt", "content": "1"}]})
    rid = pgdata.load_schedule_results()[0]["id"]
    work = inbox.get(f"schedule:work:{rid}")
    assert work and work["group"] == "confirm" and "a.txt" in work["body"]
    assert pgdata.mark_schedule_result_applied(rid)
    assert inbox.get(f"schedule:work:{rid}")["status"] == "resolved"
    # test runs never reach the inbox
    scheduler._record_result("s1", False, "test", meta={"test": True})
    assert inbox.get("schedule:fail:s1")["status"] == "resolved"


def test_module_event_and_models(fresh):
    inbox_hooks.module_event("api-models", "Новые модели API", "OpenRouter: +3")
    inbox_hooks.module_event("weather", "Погода", "Дождь")
    rows = {r["source"]: r for r in inbox.list()}
    assert rows["models"]["ref"] == {"module_id": "api-models"} and rows["models"]["actions"][0]["kind"] == "ui"
    assert rows["modules"]["title"] == "Погода"
    src = (ROOT / "app" / "module_monitor.py").read_text(encoding="utf-8")
    assert "module_event(module_id, title, text)" in src


def test_proxy_account_hooks(fresh):
    from app.providers.base import ProviderAuthError, ProviderQuotaError, ProviderNetworkError
    acc = NS(id="acc1", label="Tooken", provider="custom")
    inbox_hooks.proxy_account_failed(acc, ProviderNetworkError("net"), "network")
    inbox_hooks.proxy_account_failed(acc, ProviderQuotaError("quota"), "429 quota exceeded")
    inbox_hooks.proxy_account_failed(acc, ProviderQuotaError("quota"), "429 again")   # throttled
    for _ in range(100):
        if inbox.get("proxy:quota:acc1"):
            break
        time.sleep(0.05)
    it = inbox.get("proxy:quota:acc1")
    assert it and it["severity"] == "warn" and "Tooken" in it["title"] and it["count"] == 1
    assert inbox.get("proxy:auth:acc1") is None and len(inbox.list()) == 1
    inbox_hooks.proxy_account_ok(acc)
    for _ in range(100):
        if inbox.get("proxy:quota:acc1")["status"] != "open":
            break
        time.sleep(0.05)
    assert inbox.get("proxy:quota:acc1")["result"] == "recovered"
    inbox_hooks.proxy_account_failed(acc, ProviderAuthError("bad key"), "401")
    for _ in range(100):
        if inbox.get("proxy:auth:acc1"):
            break
        time.sleep(0.05)
    assert inbox.get("proxy:auth:acc1")


def test_memucode_terminal_questions(fresh):
    from app.cli import remote
    rc = "4242-deadbeef"
    d = remote.remote_dir()
    dialog = {"id": 3, "title": "Разрешить bash?", "kind": "permission", "multi": False, "danger": False, "body": "",
              "options": [{"index": 0, "label": "Да", "value": "yes", "cancel": False, "danger": False, "text_input": False},
                          {"index": 1, "label": "Всегда", "value": "always", "cancel": False, "danger": False, "text_input": False},
                          {"index": 2, "label": "Нет, иначе", "value": "feedback", "cancel": False, "danger": False, "text_input": True},
                          {"index": 3, "label": "Отмена", "value": None, "cancel": True, "danger": False, "text_input": False}],
              "detail": {"tool": "bash", "preview": "$ npm test", "danger": False}}
    state = {"v": 1, "rc": rc, "heartbeat": time.time(), "started": time.time(), "root": "/p/memu", "dialog": dialog}
    (d / f"{rc}.json").write_text(json.dumps(state), encoding="utf-8")
    inbox_hooks.memucode_scan()
    it = inbox.get(f"memucode:{rc}:3")
    assert it and it["group"] == "confirm" and "bash" in it["title"]
    acts = {a["id"]: a for a in it["actions"]}
    assert acts["choice:0"]["style"] == "primary" and acts["choice:1"]["confirm"] is True
    assert acts["choice:2"]["form"] == "text" and acts["choice:3"]["style"] == "ghost"
    inbox.register_handler("memucode", inbox_hooks._memucode_handler())
    res = inbox.run_action(it["id"], "choice:2", {"text": "сначала линтер"})
    assert res["resolved"]
    cmds = [json.loads(p.read_text()) for p in (d / f"{rc}.inbox").glob("*.json")]
    assert cmds and cmds[0]["type"] == "answer" and cmds[0]["choice"] == 2 and cmds[0]["text"] == "сначала линтер"
    # the dialog closes in the terminal → the next scan does not re-post it
    state["dialog"] = None
    (d / f"{rc}.json").write_text(json.dumps(state), encoding="utf-8")
    inbox_hooks.memucode_scan()
    assert inbox.counts()["open"] == 0


def test_computer_approval_hook(fresh):
    class RT:
        task_id = "cu1"

        def __init__(self):
            self.calls = []

        def approve(self, allowed, *, note="", all_steps=False, step=None):
            self.calls.append((allowed, all_steps, step))
            return True
    rt = RT()
    inbox_hooks.computer_approval(rt, {"step": 4, "description": "click at (930, 560)"})
    it = inbox.get("computer:cu1:4")
    assert it and it["ephemeral"] and it["body"] == "click at (930, 560)"
    inbox.register_handler("computer", inbox_hooks._computer_handler(rt))
    assert inbox.run_action(it["id"], "approve_all")["resolved"]
    assert rt.calls == [(True, True, 4)]
    assert inbox.get(it["id"]) is None, "computer steps do not pile up in «Готово»"
    src = (ROOT / "app" / "automation_runtime.py").read_text(encoding="utf-8")
    assert "inbox_hooks.computer_approval(self, action)" in src


def test_install_playground_registers_routes_and_handlers(fresh):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    inbox_hooks.install_playground(app, dfy_manager=FakeManager(NS(id="x", question=None)), computer_runtime=NS())
    for src in ("dfy", "computer", "memory", "memucode"):
        assert inbox.handler_for(src) is not None
    stale = inbox.post("question", "old DFY question", source="dfy", ref={"task_id": "gone"}, dedupe_key="dfy:gone:q")
    with TestClient(app) as c:
        assert c.get("/api/pg/inbox/counts").status_code == 200
    assert inbox.get(stale)["result"] == "expired", "questions of a previous run are closed on start"


# --------------------------------------------------------------------------- UI / packaging
def test_ui_wiring_and_language():
    html = (ROOT / "app" / "pgstatic" / "index.html").read_text(encoding="utf-8")
    assert '/inbox.js?v=' in html and '/inbox.css?v=' in html
    assert html.index("/plan.js") < html.index("/inbox.js"), "inbox.js after plan.js (it uses MemuPlan)"
    js = (ROOT / "app" / "pgstatic" / "inbox.js").read_text(encoding="utf-8")
    try:
        subprocess.run(["node", "--check", str(ROOT / "app" / "pgstatic" / "inbox.js")], check=True, timeout=60)
    except FileNotFoundError:
        pass
    pairs = re.findall(r'T\(\s*"((?:[^"\\]|\\.)*)"\s*,\s*"((?:[^"\\]|\\.)*)"\s*\)', js)
    pairs += re.findall(r"T\(\s*`((?:[^`\\]|\\.)*)`\s*,\s*`((?:[^`\\]|\\.)*)`\s*\)", js)
    assert len(pairs) > 60
    cyr = re.compile("[А-Яа-яЁё]")
    bad = [eng for _ru, eng in pairs if cyr.search(eng)]
    assert not bad, f"EN strings with Cyrillic: {bad[:5]}"
    for needle in ("/api/pg/inbox/stream", "nav-inbox", "ArrowDown", "snooze", "read-all", "data-dfy-task"):
        assert needle in js
    css = (ROOT / "app" / "pgstatic" / "inbox.css").read_text(encoding="utf-8")
    assert "#nav-inbox" in css and "@media (max-width: 720px)" in css
    hooks = (ROOT / "app" / "inbox_hooks.py").read_text(encoding="utf-8")
    pairs = re.findall(r'_t\(\s*"((?:[^"\\]|\\.)*)"\s*,\s*"((?:[^"\\]|\\.)*)"\s*\)', hooks)
    assert len(pairs) > 40 and not [e for _r, e in pairs if cyr.search(e)]


def test_build_targets_and_specs():
    import build
    for key in ("memuai", "proxy", "proxy_only"):
        hidden = build.TARGETS[key]["hidden"]
        assert {"app.inbox", "app.inbox_hooks", "app.inbox_api"} <= set(hidden), key
    for spec in ("MemuAI.spec", "MemuAIProxy.spec"):
        text = (ROOT / spec).read_text(encoding="utf-8")
        assert "'app.inbox'" in text and "'app.inbox_hooks'" in text and "'app.inbox_api'" in text
