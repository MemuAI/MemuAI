"""Round 35 — ask_user form tool (app/ask_user.py, pgstatic/askuser.js, Quick Access, Telegram, Mini App, DFY).

Offline: schema normalization (aliases, ≤3 fields, sanitizing), answer validation, tool results, the text-protocol
extractor, the cross-process pending store (futures + polling, timeout, cancel), Inbox post/resolve, HTTP routes,
the Telegram keyboard, static wiring of the chat / Quick Access / Mini App / DFY clients.
Live (skips without keys): a real model through MemuAI Proxy decides to call ask_user for an ambiguous request.
Run: MEMUAI_DATA_DIR=$(mktemp -d) python -m pytest -q tests/round35_askuser_test.py
"""
import asyncio
import json
import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="memu-askuser-"))
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")

from app import ask_user as au  # noqa: E402

def test_normalize_aliases_and_limits():
    form = au.normalize_form({"title": "Письмо", "fields": [
        {"type": "chips", "label": "Тон", "options": ["Формальный", "Дружеский", "Другое"], "default": "дружеский",
         "allow_other": True},
        {"type": "checkbox", "label": "Добавить", "options": "Подпись, Ссылку"},
        {"type": "int", "label": "Длина", "min": 500, "max": 50, "unit": "слов", "default": 10},
        {"type": "date", "label": "лишнее поле"}]})
    assert form["question"] == "Письмо"
    assert len(form["fields"]) == au.MAX_FIELDS == 3
    assert form["dropped_fields"] == 1
    tone, add, length = form["fields"]
    assert tone["type"] == "choice" and tone["options"] == ["Формальный", "Дружеский"]
    assert tone["default"] == "Дружеский" and tone["allow_other"] is True
    assert add["type"] == "multi" and add["options"] == ["Подпись", "Ссылку"]
    assert length["type"] == "number" and (length["min"], length["max"]) == (50, 500) and length["default"] == 50
    assert len({f["id"] for f in form["fields"]}) == 3

def test_normalize_degrades_and_sanitizes():

    form = au.normalize_form({"fields": [
        {"type": "choice", "label": "<b>Город</b>\u202e"},
        {"type": "choice", "label": "", "options": ["Согласны?"]},
        "Комментарий"]})
    kinds = [f["type"] for f in form["fields"]]
    assert kinds == ["text", "bool", "text"]
    assert form["fields"][0]["label"] == "Город"
    long = au.normalize_form({"fields": [{"type": "text", "label": "x" * 500}]})
    assert len(long["fields"][0]["label"]) <= au.MAX_LABEL
    many = au.normalize_form({"fields": [{"type": "choice", "label": "a", "options": [str(i) for i in range(30)]}]})
    assert len(many["fields"][0]["options"]) == au.MAX_OPTIONS

    assert au.normalize_form({"question": "Как назвать?"})["fields"][0]["type"] == "text"
    with pytest.raises(au.FormError):
        au.normalize_form({"fields": [{"type": "text"}]})
    with pytest.raises(au.FormError):
        au.normalize_form(42)

    form = au.normalize_form(json.dumps({"fields": {"size": {"type": "select", "label": "Размер", "options": ["S", "M"]}}}))
    assert form["fields"][0]["id"] == "size" and form["fields"][0]["type"] == "choice"

def test_validate_answers():
    form = au.normalize_form({"fields": [
        {"id": "tone", "type": "choice", "label": "Тон", "options": ["A", "B"], "allow_other": True, "required": True},
        {"id": "extras", "type": "multi", "label": "Ещё", "options": ["X", "Y"]},
        {"id": "n", "type": "number", "label": "Число", "min": 1, "max": 10},
    ]})
    clean, errors = au.validate_answers(form, {"tone": "b", "extras": ["Y", "X", "Y"], "n": "3,5"})
    assert errors == [] and clean == {"tone": "B", "extras": ["Y", "X"], "n": 3.5}
    clean, errors = au.validate_answers(form, {"tone": {"value": "", "other": "Своё"}, "n": 11})
    assert clean["tone"] == "Своё" and errors == [{"field": "n", "error": "above_max"}]
    _clean, errors = au.validate_answers(form, {"extras": ["Z"]})
    assert {"field": "tone", "error": "required"} in errors and {"field": "extras", "error": "not_an_option"} in errors
    date_form = au.normalize_form({"fields": [{"id": "d", "type": "date", "label": "Когда", "min": "2026-01-01"},
                                              {"id": "ok", "type": "bool", "label": "OK"}]})
    assert au.validate_answers(date_form, {"d": "12.10.2026", "ok": "да"}) == ({"d": "2026-10-12", "ok": True}, [])
    assert au.validate_answers(date_form, {"d": "2025-05-05"})[1] == [{"field": "d", "error": "below_min"}]
    assert au.validate_answers(date_form, {"d": "31.02.2026"})[1] == [{"field": "d", "error": "not_a_date"}]

    assert au.validate_answers(date_form, {"_reply": "как хочешь"})[0] == {"_reply": "как хочешь"}

def test_tool_result_and_summary():
    form = au.normalize_form({"question": "Q", "fields": [{"id": "a", "type": "bool", "label": "Да?"},
                                                         {"id": "b", "type": "number", "label": "Сколько", "unit": "шт"}]})
    res = au.tool_result(form, {"a": True}, "answered")
    assert res["status"] == "answered" and res["answers"] == {"a": True} and "Сколько" in res["note"]
    assert "skipped" in au.tool_result(form, None, "skipped")["status"]
    assert "defaults" in au.tool_result(form, None, "skipped")["note"]
    assert au.summary(form, {"a": False, "b": 3}) == "Да?: Нет · Сколько: 3 шт"
    assert au.summary(form, {}, "skipped", "en").startswith("Skipped")

def test_extract_call_text_protocol():
    text = 'Уточню.\n```memuai-tool\n[{"tool":"ask_user","args":{"fields":[{"type":"bool","label":"Да?"}]}}]\n```'
    rest, args = au.extract_call(text)
    assert rest == "Уточню." and args["fields"][0]["label"] == "Да?"
    assert au.extract_call('<tool_call>{"name":"ask_user","arguments":{"question":"Q"}}</tool_call>')[1] == {"question": "Q"}
    assert au.extract_call('[{"tool":"ask_user","args":{"question":"Q"}}]')[1] == {"question": "Q"}

    assert au.extract_call('```memuai-tool\n[{"tool":"web_search","args":{"query":"x"}}]\n```')[1] is None
    assert au.extract_call('Пример: {"a": 1}')[1] is None
    assert au.strip_calls('a\n```memuai-tool\n[{"tool":"x","args":{}}]\n```\nb') == "a\n\nb"

def test_swapped_closers_are_repaired_everywhere():
    """Live finding (finish-a, nemotron :free): the model closed «]}]}]» instead of «]}}]» seven times in a row —
    every parser (Python text protocol, Quick Access, main chat JS) rebalances the closers once."""
    broken = ('```memuai-tool\n[{"tool":"ask_user","args":{"question":"Q","fields":[{"id":"a","type":"choice",'
              '"label":"L ]}","options":["x","y"]}]}]}]\n```')
    assert au.rebalance_closers('[{"a":[1}]') == '[{"a":[1]}]'
    assert au.rebalance_closers('{"s":"]}"}}') == '{"s":"]}"}'
    rest, args = au.extract_call("Уточню.\n" + broken)
    assert rest == "Уточню." and args["fields"][0]["label"] == "L ]}"
    from app import quick_ask_core
    visible, calls, errors = quick_ask_core.parse_tool_calls("hi\n" + broken)
    assert visible == "hi" and not errors and calls[0]["tool"] == "ask_user"
    import shutil
    import subprocess
    if not shutil.which("node"):
        pytest.skip("node not installed")
    src = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
    start, end = src.index("function parseToolBlocks("), src.index("const TOOL_ROUND_LIMIT")
    js = src[start:end] + "\nconst r=parseToolBlocks(process.argv[1]);console.log(JSON.stringify(r));"
    out = subprocess.run(["node", "-e", js, "hi\n" + broken], capture_output=True, text=True, timeout=30)
    data = json.loads(out.stdout)
    assert not data["errors"] and data["calls"][0]["tool"] == "ask_user", out.stdout + out.stderr
    assert data["text"].strip() == "hi"

def test_prompt_rules():
    text = au.prompt_section()
    assert "at most 3" in text.lower() or "At most 3" in text
    assert "ask_user" in text and "only what changes the result" in text.lower()
    js = (ROOT / "app/pgstatic/askuser.js").read_text(encoding="utf-8")
    block = re.search(r"const PROMPT = `([\s\S]*?)`;", js).group(1).replace("\\`", "`")
    assert block.strip() == au._PROMPT.strip(), "askuser.js PROMPT must equal app/ask_user.py::_PROMPT"
    tool = au.openai_tool()
    assert tool["function"]["name"] == "ask_user"
    assert tool["function"]["parameters"]["properties"]["fields"]["maxItems"] == 3

def test_store_wait_answer_timeout_cancel(tmp_path):
    st = au.PendingStore(str(tmp_path / "ask.json"))
    form = au.normalize_form({"fields": [{"id": "x", "type": "choice", "label": "X", "options": ["a", "b"]}]})

    async def scenario():
        rec = st.open(form, surface="telegram", ref={"chat_id": "42"}, inbox_post=False)
        assert st.pending("telegram", "chat_id", "42")[0]["id"] == rec["id"]
        task = asyncio.create_task(st.wait(rec["id"], timeout=5))
        await asyncio.sleep(0.1)
        record, errors = st.settle(rec["id"], "answered", {"x": "zzz"})
        assert errors and record["status"] == "pending"
        st.settle(rec["id"], "answered", {"x": "B"})
        done = await asyncio.wait_for(task, 2)
        assert done["status"] == "answered" and done["answers"] == {"x": "b"}

        again, _ = st.settle(rec["id"], "skipped")
        assert again["status"] == "answered"

        rec2 = st.open(form, inbox_post=False)
        assert (await st.wait(rec2["id"], timeout=1, poll=0.2))["status"] == "timeout"

        rec3 = st.open(form, inbox_post=False)
        stop = asyncio.Event()
        task3 = asyncio.create_task(st.wait(rec3["id"], timeout=10, stop=stop, poll=0.1))
        await asyncio.sleep(0.15)
        stop.set()
        assert (await asyncio.wait_for(task3, 2))["status"] == "cancelled"

    asyncio.run(scenario())

def test_store_cross_process_answer(tmp_path):
    """An answer written by another process (another store on the same file) reaches the waiter by polling."""
    path = str(tmp_path / "ask.json")
    waiter_store, other_store = au.PendingStore(path), au.PendingStore(path)
    form = au.normalize_form({"fields": [{"id": "ok", "type": "bool", "label": "OK"}]})
    rec = waiter_store.open(form, inbox_post=False)
    threading.Timer(0.3, lambda: other_store.settle(rec["id"], "answered", {"ok": "yes"})).start()
    done = asyncio.run(waiter_store.wait(rec["id"], timeout=5, poll=0.1))
    assert done["status"] == "answered" and done["answers"] == {"ok": True}

def test_inbox_post_and_resolve(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMUAI_DATA_DIR", str(tmp_path))
    try:
        from app import inbox
    except ImportError:
        pytest.skip("inbox module not present")
    if hasattr(inbox, "_reset_for_tests"):
        inbox._reset_for_tests()
    st = au.PendingStore(str(tmp_path / "ask.json"))
    rec = st.open(au.normalize_form({"question": "Какой бюджет?", "fields": [{"type": "number", "label": "Бюджет"}]}),
                  surface="chat", ref={"chat_id": "c1"}, title="Ноутбук")
    item = inbox.get("ask_user:" + rec["id"])
    assert item and item["status"] == "open" and item["source"] == "ask_user"
    assert "ждёт ответа" in item["body"].lower() and item["ref"]["form_id"] == rec["id"]
    assert {a["id"] for a in item["actions"]} >= {"open", "skip"}

    monkeypatch.setattr(au, "STORE", st)
    result = au._inbox_handler(item, "skip", {})
    assert result.get("resolve") and st.get(rec["id"])["status"] == "skipped"
    assert not inbox.is_open("ask_user:" + rec["id"])

def test_http_routes(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    st = au.PendingStore(str(tmp_path / "ask.json"))
    au.register(app, store=st)
    client = TestClient(app)
    bad = client.post("/api/pg/ask_user/open", json={"form": {"fields": []}})
    assert bad.status_code == 400 and "ask_user" in bad.json()["result"]["tool"]
    rec = client.post("/api/pg/ask_user/open", json={
        "form": {"question": "Q", "fields": [{"type": "choice", "label": "Тон", "options": ["A", "B"]}] * 5},
        "surface": "chat", "ref": {"chat_id": "c9", "evil": {"x": 1}}, "lang": "ru"}).json()
    assert len(rec["form"]["fields"]) == 3 and rec["ref"] == {"chat_id": "c9"}
    assert client.get("/api/pg/ask_user/pending", params={"chat_id": "c9"}).json()["forms"][0]["id"] == rec["id"]
    fid = rec["form"]["fields"][0]["id"]
    invalid = client.post(f"/api/pg/ask_user/{rec['id']}/answer", json={"answers": {fid: "C"}})
    assert invalid.status_code == 422 and invalid.json()["errors"][0]["error"] == "not_an_option"
    ok = client.post(f"/api/pg/ask_user/{rec['id']}/answer", json={"answers": {fid: "A"}}).json()
    assert ok["result"]["status"] == "answered" and ok["result"]["answers"] == {fid: "A"}
    assert client.get(f"/api/pg/ask_user/{rec['id']}").json()["status"] == "answered"
    assert client.post("/api/pg/ask_user/nope/skip").status_code == 404

def test_telegram_keyboard_flow():
    from app import ask_user_telegram as tg
    form = au.normalize_form({"question": "Поездка", "fields": [
        {"id": "kind", "type": "choice", "label": "Куда", "options": ["Море", "Горы"], "allow_other": True},
        {"id": "with", "type": "multi", "label": "С кем", "options": ["Дети", "Собака"]},
        {"id": "budget", "type": "number", "label": "Бюджет", "unit": "₽"}]})
    state = tg.new_state("au-1", form, "ru")
    text, kb = tg.render(state)
    flat = [b for row in kb["inline_keyboard"] for b in row]
    assert all(len(b["callback_data"].encode()) <= 64 for b in flat)
    assert any("Море" in b["text"] for b in flat) and any("Готово" in b["text"] for b in flat)
    assert any("Пропустить" in b["text"] for b in flat)

    assert tg.on_callback(state, "c:0:1")["changed"]
    assert state["values"]["kind"] == "Горы"
    tg.on_callback(state, "m:1:0"); tg.on_callback(state, "m:1:1"); tg.on_callback(state, "m:1:0")
    assert state["values"]["with"] == ["Собака"]
    prompt = tg.on_callback(state, "i:2")
    assert prompt["ask_text"] and state["awaiting"] == "budget"
    assert tg.on_text(state, "15 000")["changed"] and state["values"]["budget"] == "15 000"
    done = tg.on_callback(state, "ok")
    assert done["submit"] == {"kind": "Горы", "with": ["Собака"], "budget": "15 000"}
    clean, errors = au.validate_answers(form, done["submit"])
    assert errors == [] and clean["budget"] == 15000
    assert tg.on_callback(state, "skip")["skip"]

    other = tg.on_callback(state, "o:0")
    assert other["ask_text"] and state["awaiting"] == "kind__other"
    tg.on_text(state, "Озеро")
    assert tg.collect(state)["kind"] == "Озеро"

def test_telegram_wiring_static():
    bot = (ROOT / "app/telegram_bot.py").read_text(encoding="utf-8")
    assert "ask_user_telegram" in bot
    src = (ROOT / "app/ask_user_telegram.py").read_text(encoding="utf-8")
    assert "callback_data" in src and "au:" in src

def test_chat_client_wiring():
    app_js = (ROOT / "app/pgstatic/app.js").read_text(encoding="utf-8")
    index = (ROOT / "app/pgstatic/index.html").read_text(encoding="utf-8")
    assert '/askuser.js' in index and '/askuser.css' in index
    assert 'name === "ask_user"' in app_js and "MemuAskUser.ask(" in app_js
    assert "MemuAskUser.html(m, i)" in app_js and "MemuAskUser.promptBlock()" in app_js
    assert "MemuAskUser?.interceptSend(raw)" in app_js and "!m.awaitingUser" in app_js
    js = (ROOT / "app/pgstatic/askuser.js").read_text(encoding="utf-8")
    for needle in ("/api/pg/ask_user/open", "/skip", "/cancel", "resumeAfterAutomation", "AbortError",
                   'role="radiogroup"', 'role="checkbox"', "Ctrl+Enter", "Пропустить — решай сам", "Skip — you decide"):
        assert needle in js, needle

def test_quick_client_wiring():
    quick = (ROOT / "app/pgstatic/quick/quick.js").read_text(encoding="utf-8")
    index = (ROOT / "app/pgstatic/quick/index.html").read_text(encoding="utf-8")
    assert '/askuser.js' in index and 'case "ask_user"' in quick and 'case "ask_user_done"' in quick
    api = (ROOT / "app/quick_ask_api.py").read_text(encoding="utf-8")
    assert '"event": "ask_user"' in api and "_ask_user.STORE.wait" in api

def test_spec_and_build_list_modules():
    build = (ROOT / "build.py").read_text(encoding="utf-8")
    for spec in ("MemuAI.spec", "MemuAIProxy.spec"):
        text = (ROOT / spec).read_text(encoding="utf-8")
        assert "'app.ask_user'" in text and "'app.ask_user_telegram'" in text, spec
    assert '"app.ask_user"' in build and '"app.ask_user_telegram"' in build

def _live_models():
    rows = []
    if os.environ.get("SOL_KEY"):
        rows.append(("SOL_BASE", "SOL_KEY", "gpt-6.1-sol"))
    if os.environ.get("REFORM_KEY"):
        rows.append(("REFORM_BASE", "REFORM_KEY", "qwen3.8-max"))
    if os.environ.get("OPENROUTER_KEY"):
        rows.append(("OPENROUTER_BASE", "OPENROUTER_KEY", os.environ.get("ASKUSER_OR_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")))
    return rows

@pytest.mark.parametrize("row", _live_models() or [None])
def test_live_model_calls_ask_user(row):
    """Direct OpenAI-compatible call with the ask_user prompt: an ambiguous request → a valid form."""
    if row is None:
        pytest.skip("no live keys in env")
    import httpx
    base, key, model = os.environ[row[0]].rstrip("/"), os.environ[row[1]], row[2]
    system = ("You are a helpful assistant.\n\n" + au.prompt_section())
    body = {"model": model, "messages": [{"role": "system", "content": system},
                                         {"role": "user", "content": "Подбери мне ноутбук"}], "temperature": 0.2}
    try:
        resp = httpx.post(base + "/chat/completions", json=body, headers={"Authorization": "Bearer " + key}, timeout=120)
    except httpx.HTTPError as exc:
        pytest.skip(f"provider unreachable: {exc}")
    if resp.status_code in (401, 402, 403, 429) or resp.status_code >= 500:
        pytest.skip(f"provider refused: {resp.status_code}")
    text = resp.json()["choices"][0]["message"].get("content") or ""
    _rest, args = au.extract_call(text)
    assert args is not None, text[:4000]
    form = au.normalize_form(args)
    assert 1 <= len(form["fields"]) <= 3

def test_dfy_form_question(tmp_path):
    sys.path.insert(0, str(ROOT / "tests"))
    from round24_dfy_test import FakeBrowser, ScriptedModel
    from app.dfy import DFYManager
    from app.dfy_vault import SecretVault

    async def scenario():
        model = ScriptedModel([
            {"action": "ask_user", "question": "Бронь", "fields": [
                {"id": "date", "type": "date", "label": "Заезд"},
                {"id": "room", "type": "choice", "label": "Номер", "options": ["Стандарт", "Люкс"]}]},
            {"action": "ask_user", "question": "Ещё", "fields": [{"id": "ok", "type": "bool", "label": "Завтрак?"}]},
            {"action": "done", "result": "ok"}])
        browser = FakeBrowser()
        mgr = DFYManager(model, lambda: {"dfy_enabled": True, "dfy_model": "vision/model", "dfy_max_steps": 20},
                         data_root=tmp_path, vision_lookup=lambda _m: True, browser_factory=lambda _v: browser,
                         vault=SecretVault(tmp_path, use_dpapi=False))
        task = await mgr.create("Забронируй отель")
        for _ in range(500):
            if task.status == "waiting_user":
                break
            await asyncio.sleep(0.01)
        assert task.question["kind"] == "form" and len(task.question["form"]["fields"]) == 2
        with pytest.raises(ValueError):
            await mgr.answer(task.id, {"question_id": task.question["id"], "answers": {"room": "Пентхаус"}})
        await mgr.answer(task.id, {"question_id": task.question["id"], "answers": {"date": "2026-12-01", "room": "люкс"}})
        for _ in range(500):
            if task.status == "waiting_user" and task.question and task.question.get("prompt") == "Ещё":
                break
            await asyncio.sleep(0.01)
        assert any("Номер: Люкс" in line for line in task.history)
        await mgr.answer(task.id, {"question_id": task.question["id"], "skip": True})
        for _ in range(500):
            if task.status == "done":
                break
            await asyncio.sleep(0.01)
        assert task.status == "done"
        assert any("пропустил" in line for line in task.history)

    asyncio.run(scenario())

def test_dfy_client_and_miniapp_static():
    dfy = (ROOT / "app/pgstatic/dfy-client.js").read_text(encoding="utf-8")
    assert 'q.kind === "form"' in dfy and "MemuAskForm.mount" in dfy
    tg_js = (ROOT / "app/tgapp/app.js").read_text(encoding="utf-8")
    tg_html = (ROOT / "app/tgapp/index.html").read_text(encoding="utf-8")
    assert 'case "ask_user"' in tg_js and "jobs.answer" in tg_js and "shared/askuser.js" in tg_html
    api = (ROOT / "app/tgapp_api.py").read_text(encoding="utf-8")
    assert '"askuser.js"' in api and '@op("jobs.answer")' in api

def test_miniapp_generate_with_form(tmp_path, monkeypatch):
    """The Mini App job: the model asks, the job emits ask_user, jobs.answer settles, the same job continues."""
    monkeypatch.setenv("MEMUAI_DATA_DIR", str(tmp_path))
    from app import tgapp_chat as tchat
    calls = []

    class FakeService:
        def stream_completion(self, model, messages, opts):
            calls.append(messages)
            async def gen():
                if len(calls) == 1:
                    text = 'X```memuai-tool\n[{"tool":"ask_user","args":{"question":"Q","fields":[{"id":"c","type":"choice","label":"Цвет","options":["Red","Blue"]}]}}]\n```'
                else:
                    text = "Синий выбран."
                for piece in (text[:5], text[5:]):
                    yield {"choices": [{"delta": {"content": piece}}]}
            return gen()

    import app.chat as chat_mod
    monkeypatch.setattr(chat_mod, "chat_service", FakeService(), raising=False)
    monkeypatch.setattr(tchat, "_persist", lambda *a, **k: None)
    monkeypatch.setattr(tchat, "remember_answer", lambda *a, **k: {})

    async def scenario():
        job = tchat.Job("chat", "u1")
        job.meta.update({"profile_id": "default", "chat_id": "c1"})
        task = asyncio.create_task(tchat._generate(job, {"id": "c1", "title": "T"}, [], {"role": "user", "content": "q"},
                                                   [{"role": "user", "content": "q"}], "m", False, False, {}, "ru",
                                                   True, ""))
        for _ in range(300):
            if job.meta.get("ask_id"):
                break
            await asyncio.sleep(0.01)
        ask = next(e for e in job.events if e["type"] == "ask_user")
        assert ask["form"]["fields"][0]["options"] == ["Red", "Blue"]
        assert "".join(e.get("text", "") for e in job.events if e["type"] == "delta") == "X"
        au.STORE.settle(ask["id"], "answered", {"c": "Blue"})
        await asyncio.wait_for(task, 5)
        message = next(e for e in job.events if e["type"] == "message")["message"]
        assert message["content"] == "Синий выбран." and message["askForms"][0]["answers"] == {"c": "Blue"}
        assert "local-tool_report" in calls[1][-1]["content"] and '"Blue"' in calls[1][-1]["content"]

    asyncio.run(scenario())

def test_telegram_answer_loop_with_form(monkeypatch, tmp_path):
    """telegram_bot._stream_answer: ask_user → a button form → the user taps → the same answer continues."""
    monkeypatch.setenv("MEMUAI_DATA_DIR", str(tmp_path))
    from app import telegram_bot as bot, ask_user_telegram as tg
    from app.telegram_stream import StreamReply

    sent = []

    class FakeApi:
        async def call(self, method, essential=False, **payload):
            sent.append((method, payload))
            return {"message_id": len(sent) + 100}

    monkeypatch.setattr(bot, "_api", lambda client, token: FakeApi())
    histories = []

    class FakeService:
        def stream_completion(self, model, messages, opts):
            histories.append(list(messages))
            async def gen():
                text = ('```memuai-tool\n[{"tool":"ask_user","args":{"question":"Поездка","fields":['
                        '{"id":"place","type":"choice","label":"Куда","options":["Море","Горы"]}]}}]\n```'
                        if len(histories) == 1 else "Едем в горы!")
                yield {"choices": [{"delta": {"content": text}}]}
            return gen()

    async def user_taps():
        for _ in range(300):
            state = tg._ACTIVE.get("777")
            if state and state.get("message_id"):
                await tg.handle_callback(FakeApi(), "777", f"au:{state['tag']}:c:0:1")
                await tg.handle_callback(FakeApi(), "777", f"au:{state['tag']}:ok")
                return
            await asyncio.sleep(0.01)

    async def scenario():
        generation = bot._Generation("777")
        stream = StreamReply(FakeApi(), "777", mode="off")
        tapper = asyncio.create_task(user_taps())
        await bot._stream_answer(object(), "T", "777", "Спланируй поездку", {"telegram_language": "ru"},
                                 FakeService(), generation, stream, None, asyncio.Event(),
                                 chat={"id": "temp-777", "title": "Telegram", "messages": []},
                                 messages=[{"role": "user", "content": "Спланируй поездку"}], temp=True, model="m",
                                 agent=None, profile_id="default", retry=False, reply_to=0, attachments=None,
                                 voice=False, edited=False)
        await tapper

    asyncio.run(scenario())
    form_msg = next(p for m, p in sent if m == "sendMessage" and "reply_markup" in p and "au:" in json.dumps(p["reply_markup"]))
    assert "Поездка" in form_msg["text"]
    assert any(m == "editMessageText" and "Горы" in p["text"] and not p["reply_markup"]["inline_keyboard"] for m, p in sent)
    assert any("Едем в горы!" in str(p.get("text")) for m, p in sent if m == "sendMessage")
    assert len(histories) == 2 and '"Горы"' in histories[1][-1]["content"]
    assert not any("memuai-tool" in str(p.get("text")) for m, p in sent)
    assert "777" not in tg._ACTIVE

def test_inbox_toast_quiet_for_visible_form():
    """finish-a: the «Входящие: новый вопрос» toast is not shown while the same ask_user form is on screen."""
    js = (ROOT / "app/pgstatic/inbox.js").read_text(encoding="utf-8")
    assert '!document.querySelector("#messages .au-pending")' in js

def test_quick_ask_forms_setting_hides_the_tool():
    api = (ROOT / "app/quick_ask_api.py").read_text(encoding="utf-8")
    assert 'settings.get("quick_ask_forms", True) is not False' in api

def test_date_field_follows_app_theme():
    css = (ROOT / "app/pgstatic/askuser.css").read_text(encoding="utf-8")
    assert '[data-theme="dark"] .au-date { color-scheme: dark; }' in css
    assert '[data-theme="light"] .au-date { color-scheme: light; }' in css
