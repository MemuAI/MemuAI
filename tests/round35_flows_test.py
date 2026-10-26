"""Round 35 (flows): multi-step tasks in «Планирование» — app/flow_engine.py, app/flow_adapters.py,
app/flow_api.py, the scheduler / NL-parser hooks and the constructor UI (pgstatic/flows.js).

Run: python -m pytest -q tests/round35_flows_test.py
Live (skipped without keys): SOL_BASE/SOL_KEY (gpt-6.1-sol) and REFORM_BASE/REFORM_KEY (qwen) — the user's
vacancy sentence → a multi-step flow.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="memu-r35-flows-"))
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")

from app import flow_engine as fe  # noqa: E402
from app import schedule_rules as rules  # noqa: E402

VACANCY = ("веб-поиск «вакансии Python junior удалённо»; модель отбирает подходящие в JSON; условие через модель "
           "решений: «есть новые?»; если да, строки добавляются в MemuBase и уходят в Telegram списком; иначе ничего не происходит")

@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMUAI_DATA_DIR", str(tmp_path))
    from app import pgdata
    monkeypatch.setattr(pgdata, "MEMU_DIR", tmp_path / "memuai")
    (tmp_path / "memuai").mkdir(parents=True, exist_ok=True)
    try:
        from app import inbox
        inbox._reset_for_tests()
    except Exception:
        pass
    yield

def run(coro):
    return asyncio.run(coro)

class FakeServices(fe.Services):
    """Records every side effect; the model answers from a script."""

    def __init__(self, *, search=None, answers=None, lang="ru"):
        super().__init__(lang=lang)
        self.search_rows = search or []
        self.answers = list(answers or [])
        self.sent = []
        self.notified = []
        self.prompts = []
        self.results = []
        self.handoffs = []
        self.forms = {}

    def prefs(self):
        return {"decision_model": "decider", "default_model": "main"}

    async def complete(self, model, messages):
        self.prompts.append((model, messages))
        answer = self.answers.pop(0) if self.answers else "{}"
        return answer(messages) if callable(answer) else answer

    async def web_search(self, query, limit):
        self.query = query
        return self.search_rows[:limit]

    async def telegram(self, text, *, silent=False):
        self.sent.append(text)
        return "telegram:1"

    async def notify(self, title, text, *, windows, inbox, task):
        self.notified.append((title, text))
        return ["inbox"]

    async def handoff(self, kind, payload):
        self.handoffs.append((kind, payload))
        return True, f"{kind} ok", {}

    async def record_result(self, task, ok, text, extra, meta):
        self.results.append((ok, text, extra, meta))

    async def deliver(self, task, ok, text):
        pass

    def open_form(self, form, *, ref, title):
        fid = f"f{len(self.forms) + 1}"
        self.forms[fid] = {"id": fid, "form": form, "status": "pending", "answers": None, "ref": ref}
        return fid

    def form_state(self, fid):
        return self.forms.get(fid)

    def settle_form(self, fid, status):
        if fid in self.forms:
            self.forms[fid]["status"] = status

    async def telegram_form(self, fid, form, title):
        return True

VACANCY_STEPS = [
    {"type": "web_search", "query": "вакансии Python junior удалённо", "limit": 8},
    {"type": "model", "prompt": "Отбери подходящие вакансии для Python junior на удалёнке:\n{{шаг1.результат}}", "json": True,
     "schema": {"type": "array", "items": {"type": "object", "required": ["title", "url"],
                                           "properties": {"title": {"type": "string"}, "url": {"type": "string"}}}}},
    {"type": "only_new", "items": "{{step2.result}}", "key": "url"},
    {"type": "if", "cond": {"mode": "decide", "question": "Есть новые вакансии?", "context": "{{step3.result}}"},
     "then": [{"type": "memubase_insert", "table": "Вакансии", "rows": "{{step3.result}}", "unique": "url"},
              {"type": "telegram", "text": "Новые вакансии ({{step3.result | count}}):\n{{step3.result | list}}"}],
     "else": []},
]
ROWS = [{"title": "Junior Python (remote)", "url": "https://hh.example/1", "snippet": "remote"},
        {"title": "Senior Java", "url": "https://hh.example/2", "snippet": "office"},
        {"title": "Python intern remote", "url": "https://hh.example/3", "snippet": "remote"}]
PICKED = json.dumps([{"title": "Junior Python Developer", "company": "Acme", "url": "https://hh.example/1"},
                     {"title": "Python Intern", "company": "Beta", "url": "https://hh.example/3"}], ensure_ascii=False)

def vacancy_task():
    return {"id": "vac1", "name": "Вакансии Python", "kind": "flow", "steps": fe.normalize_steps(VACANCY_STEPS)}

def decider(messages):
    data = messages[-1]["content"]
    return '{"answer": true, "reason": "есть"}' if "hh.example" in data else '{"answer": false, "reason": "пусто"}'

def test_templates_ru_en_aliases_filters_and_raw_values():
    steps = fe.normalize_steps(VACANCY_STEPS)
    ctx = fe.Ctx(trigger={"type": "gmail", "payload": {"subject": "Hi"}, "subject": "Hi"})
    ctx.numbers = {v: k for k, v in fe.numbering(steps).items()}
    ctx.steps[steps[0]["id"]] = {"result": ROWS, "text": "t"}
    assert ctx.render("{{step1.result}}") == ROWS
    assert ctx.render("{{шаг1.результат | count}}") == 3
    assert ctx.render("{{step1.result[0].title | upper}}") == "JUNIOR PYTHON (REMOTE)"
    assert ctx.render("{{step1.result | pluck(\"url\") | join(\"; \")}}").count("https://") == 3
    assert ctx.text("{{trigger.subject}}/{{триггер.payload.subject}}/{{trigger.type}}") == "Hi/Hi/gmail"
    assert ctx.render('{{nothing | default("—")}}') == "—"
    assert ctx.text("x {{step9.result}} y") == "x  y"
    assert "•" in ctx.render("{{step1.result | list}}")

    assert ctx.render("{{step1.__class__}}") is None
    assert ctx.text("{{ ().__class__.__bases__ }}") == ""
    ctx.locals.append({"as": "вакансия", "item": ROWS[2], "loop": {"index": 3}})
    assert ctx.text("{{вакансия.title}} #{{loop.index}} {{item.url}}") == "Python intern remote #3 https://hh.example/3"

def test_conditions():
    ctx = fe.Ctx()
    ctx.vars["n"] = 5
    ctx.vars["l"] = []
    assert fe.check_cond({"op": "gt", "left": "{{vars.n}}", "right": "3"}, ctx)
    assert fe.check_cond({"op": "empty", "left": "{{vars.l}}"}, ctx)
    assert not fe.check_cond({"op": "not_empty", "left": "{{vars.l}}"}, ctx)
    assert fe.check_cond({"op": "contains", "left": "Python junior", "right": "junior"}, ctx)
    assert fe.check_cond({"op": "matches", "left": "abc123", "right": r"\d+"}, ctx)
    assert fe.check_cond({"op": "is_false", "left": "нет"}, ctx)
    assert fe.check_cond({"op": "eq", "left": "1.0", "right": "1"}, ctx)

def test_normalize_aliases_limits_and_numbering():
    steps = fe.normalize_steps([{"type": "search", "q": "x"}, {"type": "loop", "list": "{{step1.result}}",
                                                                "body": [{"type": "llm", "text": "{{item}}"}]}])
    assert [s["type"] for s in fe.walk(steps)] == ["web_search", "foreach", "model"]
    nums = fe.numbering(steps)
    assert list(nums.values()) == ["1", "2", "2.1"]
    with pytest.raises(fe.FlowError):
        fe.normalize_steps([{"type": "rm -rf"}])
    with pytest.raises(fe.FlowError):
        fe.normalize_steps([{"type": "wait"}] * (fe.MAX_STEPS + 1))
    deep = {"type": "if", "then": [{"type": "if", "then": [{"type": "if", "then": [{"type": "if", "then": [{"type": "if"}]}]}]}]}
    with pytest.raises(fe.FlowError):
        fe.normalize_steps([deep])
    s = fe.normalize_steps([{"type": "model", "prompt": "p", "retries": 99, "timeout": 1}])[0]
    assert s["retries"] == 5 and s["timeout"] == 5

def test_validate_flow_reports_missing_fields_and_forward_refs():
    steps = fe.normalize_steps([{"type": "telegram", "text": "{{step2.result}}"}, {"type": "web_search", "query": ""},
                                {"type": "model", "prompt": "{{step7.result}}"}])
    problems = fe.validate_flow(steps, "ru")
    texts = " | ".join(p["text"] for p in problems)
    assert "позже" in texts and "нет шага 7" in texts and "заполните" in texts
    en = fe.validate_flow(steps, "en")
    assert not any(re.search("[А-Яа-я]", p["text"]) for p in en)

def test_lazy_migration_of_old_tasks():
    assert fe.task_to_flow({"kind": "chat", "prompt": "news", "model": "m"})[0]["type"] == "model"
    steps = fe.task_to_flow({"kind": "chat", "prompt": "news", "notify_telegram": True})
    assert [s["type"] for s in steps] == ["model", "telegram"] and steps[1]["text"] == "{{step1.text}}"
    assert fe.task_to_flow({"kind": "notify", "prompt": "break"})[0]["type"] == "notify"
    step = fe.task_to_flow({"kind": "command", "command": "Утро"})[0]
    assert step["type"] == "qa_command" and step["command"] == "Утро"
    assert fe.task_to_flow({"kind": "dfy", "prompt": "check"})[0]["type"] == "dfy"
    assert fe.task_to_flow({"kind": "memucode", "prompt": "t", "project_path": "C:/p"})[0]["type"] == "memucode"
    assert not fe.legacy_convertible({"kind": "chat", "mode": "work"})

def test_vacancy_flow_end_to_end_then_only_new_silences_second_run():
    sv = FakeServices(search=ROWS, answers=[PICKED, decider])
    task = vacancy_task()
    result = run(fe.execute_run(task, fe.new_run(task, lang="ru"), sv))
    assert result["status"] == "done" and result["ok"], result.get("error")
    assert sv.query == "вакансии Python junior удалённо"
    assert "hh.example/1" in sv.prompts[0][1][-1]["content"]
    assert sv.prompts[1][0] == "decider"
    assert len(sv.sent) == 1 and "Новые вакансии (2)" in sv.sent[0] and "• Junior Python Developer — Acme" in sv.sent[0]
    rows = fe.memubase_query("Вакансии", {}, limit=10)
    assert {r["url"] for r in rows} == {"https://hh.example/1", "https://hh.example/3"}
    ok, text, extra, meta = sv.results[-1]
    assert ok and extra["kind"] == "flow" and "quiet" not in extra
    statuses = [(row["n"], row["status"]) for row in result["log"]]
    assert ("1", "ok") in statuses and ("4", "ok") in statuses and ("4.2", "ok") in statuses

    sv2 = FakeServices(search=ROWS, answers=[PICKED, decider])
    result2 = run(fe.execute_run(task, fe.new_run(task), sv2))
    assert result2["ok"] and sv2.sent == []
    assert len(fe.memubase_query("Вакансии", {}, limit=10)) == 2
    assert sv2.results[-1][2].get("quiet") is True
    assert any(r["n"] == "3" and "новых: 0" in r.get("short", "") for r in result2["log"])

def test_dry_run_simulates_side_effects_and_keeps_only_new_state():
    sv = FakeServices(search=ROWS, answers=[PICKED, decider])
    task = vacancy_task()
    result = run(fe.execute_run(task, fe.new_run(task, dry_run=True), sv))
    assert result["ok"] and sv.sent == [] and sv.results == []
    assert fe.memubase_query("Вакансии", {}, limit=10) == []
    tg = next(r for r in result["log"] if r["type"] == "telegram")
    assert tg["status"] == "simulated" and "Junior Python Developer" in tg["sent"]

    assert fe.seen_keys("vac1", task["steps"][2]["id"]) == set()

def test_invalid_json_is_repaired_once_then_fails_clearly():
    task = {"id": "t", "name": "j", "steps": fe.normalize_steps([{"type": "model", "prompt": "p", "json": True,
                                                                 "schema": {"type": "array"}}])}
    sv = FakeServices(answers=["not json at all", '```json\n[{"a": 1}]\n```'])
    result = run(fe.execute_run(task, fe.new_run(task), sv))
    assert result["ok"] and result["log"][0]["output"].startswith("[")
    assert "not valid" in sv.prompts[1][1][-1]["content"]
    sv = FakeServices(answers=["nope", '{"a": 1}'])
    result = run(fe.execute_run(task, fe.new_run(task), sv))
    assert result["status"] == "failed" and "JSON" in result["error"]

def test_retries_timeouts_and_continue_on_error(monkeypatch):
    calls = {"n": 0}

    class Flaky(FakeServices):
        async def web_search(self, query, limit):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("503")
            return ROWS

    task = {"id": "r", "steps": fe.normalize_steps([{"type": "web_search", "query": "q", "retries": 2, "retry_delay": 1}])}
    monkeypatch.setattr(fe.asyncio, "sleep", _fast_sleep)
    result = run(fe.execute_run(task, fe.new_run(task), Flaky()))
    assert result["ok"] and result["log"][0]["attempts"] == 3

    class Slow(FakeServices):
        async def web_search(self, query, limit):
            await _real_sleep(10)

    task = {"id": "s", "steps": fe.normalize_steps([{"type": "web_search", "query": "q", "timeout": 5, "retries": 0, "continue_on_error": True},
                                                     {"type": "set", "var": "x", "value": "{{step1.status}}"}])}
    task["steps"][0]["timeout"] = 0.2
    result = run(fe.execute_run(task, fe.new_run(task), Slow()))
    assert result["ok"] and result["log"][0]["status"] == "error" and "time" in result["log"][0]["error"].lower() or "врем" in result["log"][0]["error"]
    assert result["log"][1]["status"] == "ok"

_real_sleep = asyncio.sleep

async def _fast_sleep(seconds, *a, **k):
    await _real_sleep(0)

def test_foreach_collects_and_nested_if_branches():
    steps = fe.normalize_steps([
        {"type": "set", "var": "nums", "value": "[1, 5, 10]", "transform": "json"},
        {"type": "foreach", "items": "{{vars.nums}}", "as": "n", "body": [
            {"type": "if", "cond": {"left": "{{n}}", "op": "gt", "right": "4"},
             "then": [{"type": "set", "var": "last", "value": "big {{n}} #{{loop.index}}"}],
             "else": [{"type": "set", "var": "last", "value": "small {{n}}"}]},
            {"type": "set", "var": "seen", "value": "{{vars.last}}"}]},
        {"type": "telegram", "text": "{{step2.result | join(\", \")}}"}])
    sv = FakeServices()
    result = run(fe.execute_run({"id": "f", "steps": steps}, fe.new_run({"id": "f", "steps": steps}), sv))
    assert result["ok"], result.get("error")
    assert sv.sent == ["small 1, big 5 #2, big 10 #3"]
    iters = [r for r in result["log"] if r["n"] == "2.1"]
    assert len(iters) == 3 and [r.get("iter") for r in iters] == [1, 2, 3]

def test_stop_step_and_failure_record():
    steps = fe.normalize_steps([{"type": "stop", "message": "done early"}, {"type": "telegram", "text": "never"}])
    sv = FakeServices()
    result = run(fe.execute_run({"id": "x", "steps": steps}, fe.new_run({"id": "x", "steps": steps}), sv))
    assert result["ok"] and sv.sent == [] and result["stopped"] == "done early"
    steps = fe.normalize_steps([{"type": "telegram", "text": ""}])
    result = run(fe.execute_run({"id": "y", "steps": steps}, fe.new_run({"id": "y", "steps": steps}), sv))
    assert result["status"] == "failed" and sv.results[-1][0] is False

def test_ask_user_pauses_persists_and_resumes_with_answer():
    steps = fe.normalize_steps([
        {"type": "ask_user", "question": "Отправлять?", "fields": [{"id": "go", "type": "choice", "label": "Отправлять?", "options": ["Да", "Нет"]}]},
        {"type": "if", "cond": {"left": "{{step1.result}}", "op": "eq", "right": "Да"},
         "then": [{"type": "telegram", "text": "ok {{step1.answers.go}}"}]}])
    task = {"id": "ask", "name": "Спросить", "steps": steps}
    sv = FakeServices()
    result = run(fe.execute_run(task, fe.new_run(task), sv))
    assert result["status"] == "waiting" and result["wait"]["kind"] == "ask"
    stored = fe.load_run(result["id"])
    assert stored["status"] == "waiting" and stored["form_id"] == "f1"
    assert sv.results == []
    assert not fe._ready_to_resume(stored, sv)
    sv.forms["f1"].update(status="answered", answers={"go": "Да"})
    assert fe._ready_to_resume(stored, sv)
    result = run(fe.execute_run(task, stored, sv))
    assert result["ok"] and sv.sent == ["ok Да"] and sv.results[-1][0] is True
    assert sum(1 for r in result["log"] if r["n"] == "1") == 1

def test_ask_user_timeout_uses_default():
    steps = fe.normalize_steps([{"type": "ask_user", "question": "Сколько?", "timeout_hours": 1,
                                 "fields": [{"id": "n", "type": "number", "label": "Сколько?", "default": 3}]},
                                {"type": "set", "var": "v", "value": "{{step1.result}}"}])
    task = {"id": "t2", "steps": steps}
    sv = FakeServices()
    first = run(fe.execute_run(task, fe.new_run(task), sv))
    first["asked_at"] = {k: time.time() - 7200 for k in first["asked_at"]}
    first["wait"]["deadline"] = time.time() - 1
    assert fe._ready_to_resume(first, sv)
    result = run(fe.execute_run(task, first, sv))
    assert result["ok"] and result["log"][-1]["output"].strip() == "3"

def test_real_ask_user_store_and_inbox(tmp_path):
    """The default Services: the form goes to ask_user.STORE (surface flow) and to «Входящие»."""
    from app import ask_user as au
    sv = fe.Services(lang="ru")
    fid = sv.open_form({"question": "Публиковать?", "fields": [{"id": "ok", "type": "bool", "label": "Публиковать?"}]},
                       ref={"run_id": "r1"}, title="Задача")
    record = au.STORE.get(fid)
    assert record["surface"] == "flow" and record["status"] == "pending"
    try:
        from app import inbox
        items = inbox.list(status="open")
        assert any((it.get("ref") or {}).get("form_id") == fid for it in items)
    except ImportError:
        pass
    au.STORE.settle(fid, "answered", {"ok": True})
    assert sv.form_state(fid)["answers"] == {"ok": True}

def test_resume_after_restart_replays_finished_steps():
    steps = fe.normalize_steps([{"type": "web_search", "query": "q"}, {"type": "telegram", "text": "{{step1.result | count}}"}])
    task = {"id": "rs", "steps": steps}
    run_rec = fe.new_run(task)
    key = steps[0]["id"]
    run_rec["done"][key] = {"status": "ok", "result": ROWS, "text": ""}
    run_rec["status"] = "running"
    run_rec["boot"] = "another-process"
    fe.save_run(run_rec)
    sv = FakeServices(search=[])
    assert fe._ready_to_resume(fe.load_run(run_rec["id"]), sv)
    result = run(fe.execute_run(task, fe.load_run(run_rec["id"]), sv))
    assert result["ok"] and sv.sent == ["3"] and not hasattr(sv, "query")

def test_resume_pending_picks_waiting_runs(monkeypatch):
    steps = fe.normalize_steps([{"type": "wait", "seconds": 120}, {"type": "telegram", "text": "after"}])
    task = {"id": "w", "name": "w", "steps": steps}
    from app import pgdata
    pgdata.save_schedules([dict(task, rule={"when": [{"type": "daily", "times": ["09:00"]}]})])
    sv = FakeServices()
    first = run(fe.execute_run(task, fe.new_run(task), sv))
    assert first["status"] == "waiting" and first["wait"]["kind"] == "wait"
    first["wait"]["wake_at"] = time.time() - 1
    first["waits"] = {k: time.time() - 1 for k in first["waits"]}
    fe.save_run(first)

    async def go():
        n = await fe.resume_pending(lambda: sv)
        await asyncio.gather(*list(fe._ACTIVE.values()))
        return n
    assert run(go()) == 1
    assert sv.sent == ["after"] and fe.load_run(first["id"])["status"] == "done"

def test_run_index_and_pruning(monkeypatch):
    monkeypatch.setattr(fe, "MAX_RUNS", 3)
    ids = []
    for i in range(5):
        r = fe.new_run({"id": "p", "steps": [{"type": "wait"}]})
        fe.save_run(r)
        ids.append(r["id"])
    rows = fe.list_runs("p", 10)
    assert [r["id"] for r in rows] == ids[::-1][:3]
    assert fe.load_run(ids[0]) is None and fe.load_run(ids[-1]) is not None

def test_cancel_and_stepwise_controls():
    steps = fe.normalize_steps([{"type": "set", "var": "a", "value": "1"}, {"type": "set", "var": "b", "value": "2"}])
    task = {"id": "sw", "steps": steps}
    sv = FakeServices()

    async def go():
        r = fe.new_run(task, dry_run=True, stepwise=True)
        fe.save_run(r)
        job = asyncio.ensure_future(fe.execute_run(task, r, sv, record=False))
        for _ in range(50):
            await asyncio.sleep(0.1)
            if (fe.load_run(r["id"]) or {}).get("status") == "paused":
                break
        assert fe.load_run(r["id"])["status"] == "paused"
        assert fe.continue_run(r["id"], all_steps=True)
        return await asyncio.wait_for(job, 10)
    result = run(go())
    assert result["ok"] and [x["status"] for x in result["log"]] == ["ok", "ok"]

def test_http_step_refuses_private_addresses():
    with pytest.raises(fe.StepFailed):
        fe._public_url("http://127.0.0.1:8317/api/admin")
    with pytest.raises(fe.StepFailed):
        fe._public_url("file:///etc/passwd")
    with pytest.raises(fe.StepFailed):
        fe._public_url("http://user:pw@example.com/")

def test_handoff_steps_and_dfy_and_dry_run_never_hand_off():
    steps = fe.normalize_steps([{"type": "qa_command", "command": "Утро"}, {"type": "qa_macro", "action_id": "a1"},
                                {"type": "tool", "action": "volume.set", "params": {"level": "{{vars.x | default(30)}}"}}])
    task = {"id": "h", "steps": steps}
    sv = FakeServices()
    result = run(fe.execute_run(task, fe.new_run(task), sv))
    assert result["ok"] and [k for k, _ in sv.handoffs] == ["command", "macro", "action"]
    assert json.loads(sv.handoffs[2][1]["prompt"]) == {"level": 30}
    sv2 = FakeServices()
    result = run(fe.execute_run(task, fe.new_run(task, dry_run=True), sv2))
    assert result["ok"] and sv2.handoffs == [] and all(r["status"] == "simulated" for r in result["log"])

def test_trigger_payload_contract():
    steps = fe.normalize_steps([{"type": "telegram", "text": "{{trigger.type}}: {{trigger.subject}} / {{триггер.payload.from}}"}])
    task = {"id": "tp", "steps": steps}
    sv = FakeServices()
    run(fe.execute_run(task, fe.new_run(task, trigger={"trigger": "gmail", "payload": {"subject": "Счёт", "from": "a@b"}}), sv))
    assert sv.sent == ["gmail: Счёт / a@b"]

def test_scheduler_runs_flow_task(monkeypatch):
    from app import scheduler
    seen = {}

    async def fake_flow(task, meta=None, services=None):
        seen["task"], seen["meta"] = task, meta
        return {}
    monkeypatch.setattr(fe, "run_task_flow", fake_flow)
    run(scheduler._run_task({"id": "z", "kind": "flow", "steps": [{"type": "wait"}],
                             "_run_meta": {"trigger": "gmail", "payload": {"a": 1}}}))
    assert seen["meta"]["payload"] == {"a": 1} and "_run_meta" not in seen["task"]

def test_parse_model_steps_to_flow_draft():
    data = {"name": "Вакансии", "kind": "flow", "steps": VACANCY_STEPS}
    draft = rules.draft_from_model(data, "ru")
    assert draft["kind"] == "flow" and len(list(fe.walk(draft["steps"]))) == 6
    assert draft["rule"]["when"][0]["type"] == "daily"
    assert "steps" in rules.PARSE_PROMPT and "only_new" in rules.PARSE_PROMPT
    single = rules.draft_from_model({"name": "n", "kind": "notify", "prompt": "p", "rule": {"when": [{"type": "daily", "times": ["10:00"]}]}})
    assert single["kind"] == "notify" and "steps" not in single

def test_save_task_with_steps_and_back_to_classic():
    from app import schedule_rules_api as api
    from app import pgdata
    task_id = api.save_task({"name": "V", "kind": "chat", "steps": VACANCY_STEPS,
                             "rule": {"when": [{"type": "daily", "times": ["09:00"]}]}}, "ru")
    row = next(t for t in pgdata.load_schedules() if t["id"] == task_id)
    assert row["kind"] == "flow" and len(row["steps"]) == 4
    with pytest.raises(rules.RuleError):
        api.save_task({"name": "bad", "steps": [{"type": "web_search", "query": ""}],
                       "rule": {"when": [{"type": "daily", "times": ["09:00"]}]}}, "ru")
    api.save_task({"id": task_id, "name": "V", "kind": "chat", "prompt": "news", "steps": [],
                   "rule": {"when": [{"type": "daily", "times": ["09:00"]}]}}, "ru")
    row = next(t for t in pgdata.load_schedules() if t["id"] == task_id)
    assert row["kind"] == "chat" and not row.get("steps")

def test_flow_api_routes():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app import flow_api
    app = FastAPI()

    async def completion(model, messages, prompt_caching=False):
        return '{"answer": true, "reason": "ok"}'
    flow_api.register(app, completion=completion)
    c = TestClient(app)
    cat = c.get("/api/plan/flow/catalog?lang=en").json()
    assert {s["type"] for s in cat["steps"]} >= {"web_search", "ask_user", "foreach", "if", "memubase_insert"}
    assert not any(re.search("[А-Яа-я]", s["title"]) for s in cat["steps"])
    v = c.post("/api/plan/flow/validate", json={"steps": VACANCY_STEPS, "lang": "ru"}).json()
    assert v["problems"] == [] and list(v["numbers"].values())[-1] == "4.2"
    of = c.post("/api/plan/flow/of", json={"task": {"kind": "chat", "prompt": "x"}}).json()
    assert of["legacy"] and of["steps"][0]["type"] == "model"
    vars_ = c.post("/api/plan/flow/vars", json={"steps": v["steps"], "before": v["steps"][3]["then"][1]["id"]}).json()["vars"]
    assert any(x["expr"] == "{{step3.result | list}}" for x in vars_)
    t = c.post("/api/plan/flow/test", json={"task": {"id": "tt", "name": "T", "steps": [
        {"type": "decide", "question": "ok?", "context": "x", "model": "m"},
        {"type": "telegram", "text": "{{step1.result}}"}]}}).json()
    rid = t["id"]
    for _ in range(50):
        r = c.get(f"/api/plan/flow/run?id={rid}").json()
        if r["status"] in ("done", "failed"):
            break
        time.sleep(0.1)
    assert r["status"] == "done" and r["log"][1]["status"] == "simulated" and r["log"][1]["sent"] == "true"
    assert c.get("/api/plan/flow/runs?task_id=tt").json()["runs"][0]["id"] == rid

def test_adapters_round_trip_scenarios_and_macros():
    from app import flow_adapters as fa
    command = {"id": "cmd-1", "name": "Утро", "triggers": ["доброе утро"], "steps": [
        {"type": "action", "action": "volume.set", "params": {"level": 30}}, {"type": "wait", "seconds": 2},
        {"type": "ask_ai", "prompt": "погода"}, {"type": "say", "text": "привет"}]}
    flow = fa.scenario_to_flow(command, "ru")
    assert [s["type"] for s in flow["steps"]] == ["tool", "wait", "model", "notify"]
    assert fa.flow_to_scenario_steps(flow) == command["steps"]
    action = {"id": "a1", "label": "Скрин", "steps": [{"type": "system", "op": "screenshot"},
                                                    {"type": "command", "action": "volume.mute", "params": {}}]}
    mflow = fa.macro_to_flow(action, "ru")
    assert mflow["source"] == "macro" and fa.flow_to_macro_steps(mflow) == action["steps"]
    draft = fa.as_task(flow, "ru")
    assert draft["steps"][0] == {"type": "qa_command", "command": "Утро"}
    assert fa.as_task(mflow)["steps"][0]["type"] == "qa_macro"

    assert fe.normalize_steps(draft["steps"])[0]["type"] == "qa_command"

def test_ui_files_wired_and_parse():
    index = (ROOT / "app/pgstatic/index.html").read_text("utf-8")
    assert "flows.js" in index and "flows.css" in index
    plan = (ROOT / "app/pgstatic/plan.js").read_text("utf-8")
    assert "MemuFlows" in plan
    for path in ("app/pgstatic/flows.js", "app/pgstatic/plan.js", "app/tgapp/plan.js"):
        out = subprocess.run(["node", "--check", str(ROOT / path)], capture_output=True, text=True)
        assert out.returncode == 0, out.stderr

def test_ui_english_strings_have_no_cyrillic():
    src = (ROOT / "app/pgstatic/flows.js").read_text("utf-8")
    pairs = re.findall(r'T\("((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)"\)', src)
    assert len(pairs) > 60
    bad = [en for _, en in pairs if re.search("[А-Яа-яЁё]", en)]
    assert not bad, bad[:5]

def test_build_and_spec_include_flow_modules():
    build = (ROOT / "build.py").read_text("utf-8")
    spec = (ROOT / "MemuAI.spec").read_text("utf-8")
    proxy = (ROOT / "MemuAIProxy.spec").read_text("utf-8")
    for mod in ("app.flow_engine", "app.flow_adapters", "app.flow_api"):
        assert mod in build and mod in spec
    assert "app.flow_engine" in proxy

LIVE = {"sol": ("SOL_BASE", "SOL_KEY", "gpt-6.1-sol"), "qwen": ("REFORM_BASE", "REFORM_KEY", os.environ.get("REFORM_MODEL", "qwen3.8-max"))}

@pytest.mark.parametrize("name", list(LIVE))
def test_live_vacancy_sentence_becomes_multi_step_flow(name):
    import httpx
    from app import schedule_rules_api as api
    base_env, key_env, model = LIVE[name]
    base, key = os.environ.get(base_env, ""), os.environ.get(key_env, "")
    if not (base and key):
        pytest.skip(f"no {name} key")

    async def completion(model_id, messages, prompt_caching=False):
        async with httpx.AsyncClient(timeout=180) as client:
            resp = await client.post(base.rstrip("/") + "/chat/completions", headers={"Authorization": f"Bearer {key}"},
                                     json={"model": model_id, "messages": messages, "temperature": 0.2})
            if resp.status_code in (402, 429, 503):
                pytest.skip(f"{name}: provider unavailable / out of quota (HTTP {resp.status_code})")
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

    sentence = "Каждое утро: " + VACANCY
    draft = run(api.parse_text(sentence, model, completion, lang="ru", tasks=[], commands=[]))
    types = [s["type"] for s in fe.walk(draft["steps"])]
    print(name, json.dumps(draft["steps"], ensure_ascii=False)[:3000])
    assert draft["kind"] == "flow"
    assert types[0] == "web_search" and "model" in types
    assert any(t in types for t in ("if", "decide"))
    assert "memubase_insert" in types and "telegram" in types
    assert fe.validate_flow(draft["steps"], "ru") == [] or all(p["level"] == "warn" for p in fe.validate_flow(draft["steps"], "ru"))

    sv = FakeServices(search=ROWS, answers=[PICKED, decider, decider, decider])
    task = {"id": "live", "name": draft["name"], "steps": draft["steps"]}
    result = run(fe.execute_run(task, fe.new_run(task), sv))
    assert result["ok"], result.get("error")
    assert len(sv.sent) == 1 and "hh.example" in sv.sent[0]

def test_autolink_feeds_earlier_data_to_model_steps():
    """qwen wrote «По результатам поиска отбери…» without {{step1.result}}: the model never saw the results."""
    steps = fe.normalize_steps([{"type": "web_search", "query": "q"}, {"type": "model", "prompt": "По результатам поиска отбери", "json": True},
                                {"type": "only_new", "items": "{{step2.result}}"},
                                {"type": "if", "cond": {"mode": "decide", "question": "есть новые?"}, "then": [{"type": "telegram", "text": "x"}]}])
    fe.autolink(steps)
    assert steps[1]["prompt"].endswith("{{step1.result}}")
    assert steps[3]["cond"]["context"] == "{{step3.result}}"
    draft = rules.draft_from_model({"name": "v", "steps": [{"type": "web_search", "query": "q"}, {"type": "model", "prompt": "отбери"}]})
    assert "{{step1.result}}" in draft["steps"][1]["prompt"]

def test_notify_title_is_not_the_step_label():
    step = fe.normalize_steps([{"type": "notify", "title": "Новые ({{step1.result | count}})", "text": "x"}])[0]
    assert "label" not in step and step["title"].startswith("Новые")
    assert fe.normalize_steps([{"type": "model", "title": "Отбор", "prompt": "p"}])[0]["label"] == "Отбор"

def test_network_steps_retry_once_by_default():
    steps = fe.normalize_steps([{"type": "model", "prompt": "p"}, {"type": "telegram", "text": "t"}])
    assert steps[0]["retries"] == 1 and steps[1]["retries"] == 0

def test_quiet_run_does_not_post_done_to_inbox():
    from app import inbox, inbox_hooks
    inbox_hooks.schedule_result("tq", True, "nothing", {"quiet": True}, {})
    assert not any(it.get("dedupe_key") == "schedule:result:tq" for it in inbox.list(status="all"))
    inbox_hooks.schedule_result("tq", True, "sent", {}, {})
    assert any(it.get("dedupe_key") == "schedule:result:tq" for it in inbox.list(status="all"))

def test_miniapp_rows_carry_read_only_steps():
    from app import pgdata, tgapp_extras
    pgdata.save_schedules([{"id": "m1", "name": "V", "kind": "flow", "steps": VACANCY_STEPS,
                            "rule": {"when": [{"type": "daily", "times": ["09:00"]}]}}])
    row = next(r for r in tgapp_extras.schedule_rows("ru") if r["id"] == "m1")
    assert [s["n"] for s in row["steps"]] == ["1", "2", "3", "4", "4.1", "4.2"]
    assert row["steps"][4]["depth"] == 1 and "MemuBase" in row["steps"][4]["text"]

def _quick_command_env():
    from app import assistant_commands as ac
    eng = ac.Engine()
    ac.set_engine(eng)
    cmd = ac.validate_command({"name": "Утро", "triggers": ["доброе утро"], "steps": [{"type": "say", "text": "привет"}]})
    eng.store.update(lambda data: data["commands"].append(cmd))
    return ac, eng, cmd

async def _with_executor(coro):
    """Runs ``coro`` while a real window-side Executor pumps ``schedule_handoff.json``."""
    from app import schedule_rules_api as sra
    ex = sra.Executor(None)

    async def pump():
        while True:
            await ex.pump()
            await asyncio.sleep(0.2)
    job = asyncio.create_task(pump())
    try:
        return await asyncio.wait_for(coro, 30)
    finally:
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)

def test_scenario_step_and_old_command_task_reach_executor():
    ac, eng, cmd = _quick_command_env()
    try:
        class Real(FakeServices):
            handoff = fe.Services.handoff

        task = {"id": "qa1", "name": "Утренний сценарий", "kind": "flow",
                "steps": fe.normalize_steps([{"type": "qa_command", "command": "Утро"}])}
        sv = Real()
        result = run(_with_executor(fe.execute_run(task, fe.new_run(task), sv)))
        assert result["ok"], result["log"]
        row = result["log"][0]
        assert row["status"] == "ok" and "не хватает" not in json.dumps(row, ensure_ascii=False)
        assert "привет" in json.dumps(row["output"], ensure_ascii=False)

        from app import schedule_runtime as srt
        for ref in ({"command": "Утро"}, {"command": cmd["id"]}, {"command_id": cmd["id"]}):
            ok, text, extra = run(_with_executor(srt.run_action(dict({"id": "old", "name": "Старая", "kind": "command",
                                                                         "lang": "ru"}, **ref))))
            assert ok and "привет" in text and extra.get("command") == "Утро", text
    finally:
        ac.set_engine(None)

def test_command_run_by_id_only_fills_name():
    """A hotkey of a custom command calls command.run with the id only (the catalog requires name)."""
    ac, eng, cmd = _quick_command_env()
    try:
        card = run(eng.run("command.run", {"id": cmd["id"]}, source="hotkey", lang="ru", emit=False))
        assert card["status"] == "done", card
    finally:
        ac.set_engine(None)

def test_add_menu_columns_and_design_popovers():
    """r35-finish-b: the add-step menu lays whole groups into ≤3 columns; glass/soft/brutal restyle it."""
    js = (ROOT / "app" / "pgstatic" / "flows.js").read_text(encoding="utf-8")
    css = (ROOT / "app" / "pgstatic" / "flows.css").read_text(encoding="utf-8")
    assert 'class="flw-menu-col"' in js and "ncol" in js and "sc.scrollTop" in js
    assert ".flw-menu-col" in css and "grid-template-columns: repeat(var(--cols" in css
    for design in ("brutal", "glass", "soft"):
        assert f':root[data-design="{design}"] :is(.flw-menu, .flw-ac)' in css
    assert ':root[data-design="brutal"] .flw-card' in css
