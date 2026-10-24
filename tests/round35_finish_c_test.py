"""Round 35 (finish-c): finishing the «triggers» zone.

- the chat ```schedule protocol (schedule_rules.CHAT_PROTOCOL = plan.js PROTOCOL) knows watch triggers and flows;
- chat blocks with steps are normalized by /api/plan/preview; app.js accepts kind «flow»;
- {{trigger.type}} = the actual source type (gmail.new_email …), the «по событию» badge keeps trigger="event";
- web page «появился текст» — whole words and negation-aware («В наличии» ⊄ «Нет в наличии»).

Run: NO_PROXY=127.0.0.1,localhost python -m pytest -q tests/round35_finish_c_test.py
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="memu-r35-finishc-"))
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")

from app import schedule_rules as rules  # noqa: E402
from app import schedule_triggers_ext as ext  # noqa: E402

PLAN_JS = (ROOT / "app/pgstatic/plan.js").read_text(encoding="utf-8")
APP_JS = (ROOT / "app/pgstatic/app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    from app import pgdata
    monkeypatch.setattr(pgdata, "MEMU_DIR", tmp_path / "memuai")
    (tmp_path / "memuai").mkdir(parents=True, exist_ok=True)
    ext._INFLIGHT.clear()
    monkeypatch.setattr(ext, "service_status", lambda: {"gmail": True, "github": True})
    yield tmp_path
    ext._INFLIGHT.clear()


# --------------------------------------------------------------------------- chat protocol

def _plan_protocol() -> str:
    m = re.search(r'  const PROTOCOL = (".*?");\n', PLAN_JS)
    assert m, "plan.js PROTOCOL literal"
    return json.loads(m.group(1))


def test_chat_protocol_copies_are_equal_and_compact():
    assert _plan_protocol() == rules.CHAT_PROTOCOL
    text = rules.CHAT_PROTOCOL
    for marker in ('"type":"watch"', "gmail", "calendar_before", "github_actions", "web_page", "panel", "folder_file",
                   '"kind":"flow"', '"steps"', "{{step1.result}}", "{{trigger.title}}", "notify_telegram"):
        assert marker in text, marker
    assert len(text) < 3700, len(text)          # ~800 tokens (r37: +calendar/sun/weather/Wi-Fi/groups), only for scheduling-like messages
    assert "\n" not in text                       # one stable line (prompt-cache friendly)


def test_every_watch_source_named_in_protocol_exists():
    named = set(re.findall(r"\b([a-z]+_[a-z_]+|gmail|rss|panel|system|webhook)\b(?= \+|\||\(| \.|$)", rules.CHAT_PROTOCOL))
    for source in ("gmail", "calendar_before", "calendar_created", "github_issue", "github_pr", "github_actions",
                   "github_release", "sheets_row", "drive_file", "notion_db", "rss", "web_page", "panel",
                   "folder_file", "system", "webhook"):
        assert source in ext.SOURCES and source in rules.CHAT_PROTOCOL, source
    del named


def test_protocol_examples_normalize():
    # the shapes the protocol shows must pass the real validators
    rule = rules.normalize_rule({"when": [{"type": "watch", "source": "gmail", "query": "from:bank"}]})
    assert rule["when"][0]["source"] == "gmail"
    for raw in ({"source": "calendar_before", "minutes": 10}, {"source": "github_actions", "repo": "octo/x"},
                {"source": "web_page", "url": "https://shop.example/p", "mode": "contains", "text": "В наличии"},
                {"source": "system", "metric": "disk_free_pct", "op": "lt", "value": 10}):
        ext.normalize({"type": "watch", **raw})
    from app.flow_engine import normalize_steps, validate_flow
    steps = normalize_steps([{"type": "web_search", "query": "python junior"},
                             {"type": "model", "prompt": "Отбери {{step1.result}}", "json": True},
                             {"type": "only_new", "items": "{{step2.result}}", "key": "url"},
                             {"type": "if", "cond": {"mode": "compare", "left": "{{step3.result}}", "op": "not_empty"},
                              "then": [{"type": "telegram", "text": "{{step3.result | list}}"}]}])
    assert not [p for p in validate_flow(steps) if p["level"] == "error"]


def test_preview_normalizes_chat_steps():
    from app import schedule_rules_api as api
    task = {"name": "Вакансии", "kind": "flow", "rule": {"when": [{"type": "daily", "times": ["09:00"]}]},
            "steps": [{"type": "web_search", "query": "python junior"},
                      {"type": "telegram", "text": "{{step1.result | list}}"}]}
    out = api.preview_payload(task, [], "ru")
    assert [s["type"] for s in out["steps"]] == ["web_search", "telegram"] and all(s.get("id") for s in out["steps"])
    assert len(out["steps_text"]) == 2 and out["summary"]
    with pytest.raises(rules.RuleError):
        api.preview_payload(dict(task, steps=[{"type": "no_such_step"}]), [], "ru")
    # without steps the payload is unchanged
    assert "steps" not in api.preview_payload({"rule": task["rule"]}, [], "ru")


def test_app_js_accepts_flow_blocks_and_enrich_keeps_steps():
    body = APP_JS[APP_JS.index("async function extractSchedules("):APP_JS.index("function scheduleTiming(")]
    assert 'rawTask.kind === "flow"' in body and "task.steps = rawTask.steps" in body
    assert "(!prompt && !isFlow)" in body and 'type: "daily", times: ["09:00"]' in body
    enrich = PLAN_JS[PLAN_JS.index("async function enrich("):]
    assert 'kind: "flow", steps: pv.steps' in enrich[:900]


@pytest.mark.skipif(not NODE, reason="node is required")
def test_schedule_intent_regex_covers_event_requests():
    a = APP_JS.index("const SCHEDULE_INTENT_RE")
    b = APP_JS.index("/* Среда Work/MemuCode", a)

    def block(text):
        script = ("globalThis.MemuPlan = { protocol: 'P' };\n" + APP_JS[a:b] +
                  f"\nconsole.log(JSON.stringify(scheduleRulesPromptBlock({json.dumps(text)})));")
        out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
        assert out.returncode == 0, out.stderr
        return json.loads(out.stdout)

    for text in ("когда придёт письмо от банка — кратко перескажи его в Telegram",
                 "если упадёт Actions в репозитории octo/x — разберись почему",
                 "за 10 минут до встречи напомни", "как только появится новый релиз — сообщи",
                 "если курс доллара станет больше 100 — напиши", "when an email from the bank arrives, summarize it",
                 "remind me 10 minutes before each meeting"):
        assert block(text) == "<schedule_rules>P</schedule_rules>", text
    for text in ("Объясни, что такое рекурсия", "Какая погода в Праге?", "Когда придёт весна в Москве?",
                 "When was the email protocol invented?", "Что станет с ценами на нефть?"):
        assert block(text) == "", text


# --------------------------------------------------------------------------- {{trigger.type}}

def test_take_and_decide_give_the_actual_type(monkeypatch):
    import round35_triggers_test as T
    from app import scheduler, schedule_runtime
    g = T.FakeGoogle()
    monkeypatch.setattr(ext, "google", g)
    g.messages = [("m1", "Старое", "bank")]
    task = {"id": "tt", "name": "Банк", "kind": "chat", "prompt": "Тип: {{trigger.type}} — {{trigger.title}}", "enabled": True,
            "rule": {"when": [{"type": "watch", "source": "gmail", "query": "from:bank"}]}}
    T._save_tasks([task])
    started = []
    monkeypatch.setattr(scheduler, "_start_claimed_task", lambda t, tid, ts: started.append(t))
    monkeypatch.setattr(schedule_runtime.SAMPLER, "get", lambda needs, now=None: (None, {}))
    now = time.time()
    asyncio.run(scheduler._scheduler_tick(now=now))
    g.messages.append(("m2", "Выписка", "Банк"))
    key = next(iter(ext.load_state()["watches"]))
    ext.mutate_state(lambda w: w[key].update(next_check=0))
    for step in (5, 10, 15):
        if not started:
            asyncio.run(scheduler._scheduler_tick(now=now + step))
    assert len(started) == 1
    meta = started[0]["_run_meta"]
    assert meta["trigger"] == "event"                    # the «по событию» badge
    assert meta["source"] == "gmail.new_email" and meta["payload"]["type"] == "gmail.new_email"
    assert ext.apply_trigger(started[0])["prompt"] == "Тип: gmail.new_email — Выписка"
    # flows: {{trigger.type}}
    from app import flow_engine as fe
    run = fe.new_run({"id": "f", "steps": fe.normalize_steps([{"type": "wait", "seconds": 1}])}, trigger=meta)
    assert run["trigger_payload"]["type"] == "gmail.new_email" and run["trigger_payload"]["title"] == "Выписка"
    assert run["trigger_payload"]["payload"]["from"] == "Банк"


def test_type_fallbacks():
    from app import flow_engine as fe
    steps = fe.normalize_steps([{"type": "wait", "seconds": 1}])
    assert fe.new_run({"id": "a", "steps": steps})["trigger_payload"]["type"] == "manual"
    assert fe.new_run({"id": "a", "steps": steps}, trigger={"trigger": "task_done"})["trigger_payload"]["type"] == "task_done"
    assert fe.new_run({"id": "a", "steps": steps}, trigger={"trigger": "event", "source": "process_start"})["trigger_payload"]["type"] == "process_start"
    assert ext.type_id("github_actions") == "github.actions_failed" and ext.type_id("x") == "x"
    assert set(ext.TYPE_IDS) == set(ext.SOURCES)


def test_classic_event_source_in_decide():
    now = time.time()
    task = {"id": "c1", "kind": "notify", "prompt": "x", "enabled": True,
            "rule": rules.normalize_rule({"when": [{"type": "event", "event": "process_start", "name": "dota2.exe"}]})}
    task["rule_state"] = {"event_at": now - 1, "event_deadline": now + 100, "event_name": "process_start"}
    out = rules.decide(task, now, {}, None)
    assert out["run"] and out["trigger"] == "event" and out["source"] == "process_start"


# --------------------------------------------------------------------------- web page «появился текст»

@pytest.mark.parametrize("hay,needle,hit", [
    ("Нет в наличии", "В наличии", False), ("Товар: В наличии!", "в наличии", True),
    ("Out of stock", "In stock", False), ("In stock now", "in stock", True), ("Not available", "available", False),
    ("Unavailable", "available", False), ("Нет в наличии", "нет в наличии", True), ("Недоступно", "доступно", False),
    ("Размер M: в наличии; размер S: нет в наличии", "в наличии", True), ("ёлка  в   НАЛИЧИИ", "Елка в наличии", True),
    ("Цена 100 ₽", "100", True), ("Цена 1000 ₽", "100", False), ("not in stock", "In stock", False),
])
def test_text_found_exact(hay, needle, hit):
    assert ext.text_found(hay, needle) is hit


def test_text_found_substring_mode():
    assert ext.text_found("Нет в наличии", "В наличии", exact=False) is True
    assert ext.text_found("anything", "", exact=True) is False


def test_web_page_contains_does_not_fire_on_negation(monkeypatch):
    page = {"html": "<ul class='stock'><li>Нет в наличии</li></ul>"}
    monkeypatch.setattr(ext, "http_get", lambda url, **kw: (page["html"], url))
    trig = ext.normalize({"type": "watch", "source": "web_page", "url": "https://shop.example/p",
                          "selector": "ul.stock", "mode": "contains", "text": "В наличии"})
    assert "exact" not in trig                       # default, not stored → old watch keys unchanged
    r1 = ext.poll_source(trig, None)
    assert r1.events == []                           # «Нет в наличии» is not «В наличии»
    page["html"] = "<ul class='stock'><li>В наличии: 3 шт.</li></ul>"
    r2 = ext.poll_source(trig, r1.cursor)
    assert r2.events and "В наличии" in r2.events[0]["title"]
    assert ext.poll_source(trig, r2.cursor).events == []
    page["html"] = "<ul class='stock'><li>Нет в наличии</li></ul>"
    r3 = ext.poll_source(trig, r2.cursor)
    assert r3.events == []
    page["html"] = "<ul class='stock'><li>В наличии</li></ul>"
    assert ext.poll_source(trig, r3.cursor).events    # re-armed after it vanished
    # «пропал текст» with exact matching: «Нет в наличии» means «В наличии» is gone
    gone = ext.normalize({"type": "watch", "source": "web_page", "url": "https://shop.example/p",
                          "mode": "not_contains", "text": "В наличии", "initial": True})
    page["html"] = "<p>В наличии</p>"
    g1 = ext.poll_source(gone, None)
    assert g1.events == []
    page["html"] = "<p>Нет в наличии</p>"
    assert ext.poll_source(gone, g1.cursor).events
    # substring mode (old behaviour) when «точное совпадение» is off
    loose = ext.normalize({"type": "watch", "source": "web_page", "url": "https://shop.example/p",
                           "mode": "contains", "text": "В наличии", "exact": False})
    assert loose["exact"] is False
    assert ext.poll_source(loose, None).events      # substring of «Нет в наличии»


def test_web_page_exact_checkbox_in_ui():
    js = (ROOT / "app/pgstatic/plan-triggers.js").read_text(encoding="utf-8")
    assert "${p}.exact" in js and "точное совпадение" in js and "exact match" in js
