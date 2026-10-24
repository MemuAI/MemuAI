"""Round 36 (plan): fixes in «Планирование» (шаги), «Входящие» and memuai:// links + no background polling.

Run: python -m pytest -q tests/round36_plan_test.py   (no network)
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="memu-r36-plan-"))
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")

from app import flow_engine as fe  # noqa: E402
from app import schedule_rules as rules  # noqa: E402

STATIC = ROOT / "app" / "pgstatic"


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMUAI_DATA_DIR", str(tmp_path))
    from app import pgdata
    monkeypatch.setattr(pgdata, "MEMU_DIR", tmp_path / "memuai")
    (tmp_path / "memuai").mkdir(parents=True, exist_ok=True)
    yield


class Svc(fe.Services):
    def __init__(self, rows):
        super().__init__(lang="ru")
        self.rows, self.sent, self.results = rows, [], []

    def prefs(self):
        return {"default_model": "m"}

    async def complete(self, model, messages):
        return "{}"

    async def web_search(self, query, limit):
        return self.rows[:limit]

    async def telegram(self, text, *, silent=False):
        self.sent.append(text)
        return "telegram:1"

    async def record_result(self, task, ok, text, extra, meta):
        self.results.append((ok, text, extra))

    async def deliver(self, task, ok, text):
        pass


def _ctx():
    c = fe.Ctx(trigger={"type": "manual"})
    c.steps = {"a": {"result": [{"title": "Py", "url": "u1"}, {"title": "Go", "url": "u2"}], "text": "t", "status": "ok"}}
    c.numbers = {"1": "a"}
    return c


# --------------------------------------------------------------------------- templates
def test_bare_step_reference_is_its_result():
    c = _ctx()
    assert c.render("{{шаг1}}") == c.steps["a"]["result"]
    assert c.render("{{step1}}") == c.steps["a"]["result"]
    assert c.render("{{step.1}}") == c.steps["a"]["result"]
    assert c.render("{{шаг1.статус}}") == "ok", "the record fields stay reachable"
    assert "status" not in c.render("Итог: {{шаг1}}")


def test_dotted_list_index():
    c = _ctx()
    assert c.render("{{шаг1.результат.0.title}}") == "Py"
    assert c.render("{{шаг1.результат.1.url}}") == "u2"
    assert c.render("{{шаг1.результат[0].title}}") == "Py"
    assert c.render("{{шаг1.результат.5.title}}") is None


def test_list_line_does_not_repeat_company():
    assert fe.item_line({"title": "Junior Python — Acme", "company": "Acme", "url": "u"}) == "Junior Python — Acme\n  u"
    assert fe.item_line({"title": "Junior Python", "company": "Acme"}) == "Junior Python — Acme"


# --------------------------------------------------------------------------- «только новые» without «Если»
def test_search_only_new_telegram_without_if_is_quiet_when_nothing_new():
    task = {"id": "vac", "name": "Вакансии", "kind": "flow", "steps": [
        {"type": "web_search", "query": "вакансии Python junior"},
        {"type": "only_new", "items": "{{step1.result}}", "key": "url"},
        {"type": "telegram", "text": "{{step2.result | list}}"}]}
    task["steps"] = fe.normalize_steps(task["steps"])   # saved tasks keep their step ids («только новые» is per step)
    rows = [{"title": "Junior Python", "url": "https://hh.ru/1", "snippet": ""}]
    first = Svc(rows)
    res = asyncio.run(fe.execute_run(task, fe.new_run(task), first))
    assert res["status"] == "done" and len(first.sent) == 1
    second = Svc(rows)
    res = asyncio.run(fe.execute_run(task, fe.new_run(task), second))
    assert res["status"] == "done", res.get("summary")
    assert second.sent == [] and second.results[0][0] is True
    assert second.results[0][2].get("quiet") is True, "nothing sent → no «done» noise in the inbox"


def test_empty_telegram_without_only_new_still_fails():
    task = {"id": "e", "name": "E", "kind": "flow", "steps": [{"type": "set", "var": "x", "value": ""},
                                                             {"type": "telegram", "text": "{{vars.x}}"}]}
    svc = Svc([])
    res = asyncio.run(fe.execute_run(task, fe.new_run(task), svc))
    assert res["status"] == "failed" and svc.sent == []


# --------------------------------------------------------------------------- chat protocol
def test_chat_protocol_same_in_js_and_names_new_only_searches():
    src = (STATIC / "plan.js").read_text(encoding="utf-8")
    js = json.loads(re.search(r'const PROTOCOL = (".*?");\n', src, re.S).group(1))
    assert js == rules.CHAT_PROTOCOL
    assert "only NEW finds (vacancies" in rules.CHAT_PROTOCOL


# --------------------------------------------------------------------------- memuai:// nudges through the inbox SSE
def test_deeplink_queue_version_grows_on_dispatch():
    from app import deeplinks
    deeplinks.reset_for_tests()
    before = deeplinks.queue_version()
    res = deeplinks.dispatch("memuai://new", "os", notify=False)
    if res.get("ok"):
        assert deeplinks.queue_version() == before + 1
    bad = deeplinks.dispatch("memuai://definitely-not-a-route", "os", notify=False)
    assert bad.get("ok") is False and deeplinks.queue_version() == (before + 1 if res.get("ok") else before)
    deeplinks.reset_for_tests()


def test_inbox_stream_sends_links_event():
    src = (ROOT / "app" / "inbox_api.py").read_text(encoding="utf-8")
    assert "event: links" in src and "_links_version()" in src


# --------------------------------------------------------------------------- no background polling in the window
def test_no_idle_timers_in_plan_inbox_links():
    links = (STATIC / "deeplinks.js").read_text(encoding="utf-8")
    assert "setInterval" not in links, "memuai:// queue is read on events, not every 4 s"
    assert '"memu:links"' in links
    inbox = (STATIC / "inbox.js").read_text(encoding="utf-8")
    assert "visibilitychange" in inbox and "disconnect" in inbox
    assert 'new CustomEvent("memu:links")' in inbox
    for m in re.finditer(r"setInterval\(", inbox):
        body = inbox[m.start():m.start() + 200]
        assert "visibilityState" in body, "the fallback poll must sleep while the window is hidden"
    plan = (STATIC / "plan.js").read_text(encoding="utf-8")
    i = plan.index("async function refreshQuiet()")
    assert 'document.visibilityState === "hidden"' in plan[i:i + 400]


# --------------------------------------------------------------------------- «реагировать только в это время» + ждать
def _hours_task(wait):
    hours = {"from": "09:00", "to": "18:00", "days": [1, 2, 3, 4, 5]}
    if wait:
        hours["wait"] = True
    return {"id": "w" + str(int(wait)), "name": "RSS", "kind": "chat", "prompt": "p", "enabled": True,
            "rule": rules.normalize_rule({"when": [{"type": "watch", "source": "rss", "url": "https://example.com/feed",
                                                    "hours": hours}]})}


@pytest.mark.parametrize("wait", [False, True])
def test_trigger_hours_skip_or_wait(monkeypatch, wait):
    from app import schedule_triggers_ext as ext
    ext._INFLIGHT.clear()
    task = _hours_task(wait)
    trig = task["rule"]["when"][0]
    assert bool(trig["hours"].get("wait")) is wait
    assert ("ждут" in ext.describe(trig, "ru")) is wait
    polls = iter([[], [{"source": "rss", "title": "Новая запись", "id": "n1"}], [], []])
    monkeypatch.setattr(ext, "_run_poll", lambda t, cur, lang, now: {"ok": True, "events": next(polls), "cursor": {"seen": []},
                                                                    "info": {}, "next_check": None, "at": now})
    inside = {"v": False}
    monkeypatch.setattr(ext, "_in_hours", lambda t, now: inside["v"])
    t0 = 1_800_000_000.0
    assert asyncio.run(ext.tick([task], t0, "ru")) == {}                     # baseline
    out = asyncio.run(ext.tick([task], t0 + 3600, "ru"))                    # event at night
    assert out == {}, "outside the hours nothing is handed to the task"
    inside["v"] = True
    out = asyncio.run(ext.tick([task], t0 + 3 * 24 * 3600, "ru"))           # Monday morning, ~3 days later
    if wait:
        rows = out[task["id"]][ext.watch_key(task["id"], trig)]
        assert rows[0]["payload"]["title"] == "Новая запись"
    else:
        assert out == {}
    ext._INFLIGHT.clear()


def test_plan_run_link_is_task_run_with_confirmation():
    from app import deeplinks
    deeplinks.reset_for_tests()
    res = deeplinks.dispatch("memuai://plan/run?id=abc123", "os", notify=False)
    assert res["ok"] and res["route"] == "task/run" and res["danger"] == "confirm"
    deeplinks.reset_for_tests()
