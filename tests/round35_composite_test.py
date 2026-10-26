"""Round 35 (composite): composite models of MemuAI Proxy — app/composite_models.py, app/composite_models_api.py,
the hooks in chat.py / openai_api.py / tool_protocol.py / admin_api.py / model_catalog.py and the «Составные модели» UI.

No network by default: real models are faked by patching the concrete catalogue and ``ChatService.stream_completion``.
Live test (real accounts through the Proxy): ``MEMU_LIVE_COMPOSITE=1`` + ``REFORM_BASE``/``REFORM_KEY`` in env.
Run: NO_PROXY=127.0.0.1,localhost python -m pytest -q tests/round35_composite_test.py
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-r35-composite-")

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import composite_models as cm  # noqa: E402
from app.chat import CompletionError  # noqa: E402

STATIC = ROOT / "app" / "static"

FAKE = {
    "fast-text": dict(provider="custom", vision="no", tools=True, context=32000, price=(0.1, 0.2)),
    "big-ctx": dict(provider="custom", vision="no", tools=True, context=1000000, price=(1.0, 4.0)),
    "eyes": dict(provider="custom", vision="yes", tools=True, context=128000, price=(0.5, 1.5)),
    "free-one": dict(provider="custom", vision="unknown", tools=True, context=64000, price=(0.0, 0.0), free=True),
    "kiro-x": dict(provider="kiro", vision="yes", tools=False, context=200000, price=None),
}

def _caps(model_id, concrete=None):
    key = str(model_id).lower()
    if "/" in key and cm.store.get(key) is not None:
        return {"composite": True, "available": True, "vision": "unknown", "tools": True, "context": None,
                "price": None, "free": False}
    row = FAKE.get(key)
    if row is None:
        return {"available": False, "vision": "unknown", "tools": False, "context": None, "price": None, "free": False}
    return {"available": True, "free": row.get("free", False), "json": True, "name": key, **row}

@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    store = cm._Store(str(tmp_path / "composite_models.json"))
    stats = cm._Stats(str(tmp_path / "composite_stats.json"))
    monkeypatch.setattr(cm, "store", store)
    monkeypatch.setattr(cm, "stats", stats)
    monkeypatch.setattr(cm, "observations", cm._Observations())
    monkeypatch.setattr(cm, "model_caps", _caps)
    monkeypatch.setattr(cm, "_concrete_ids", lambda: {k: SimpleNamespace(id=k, prefix="x", provider=v["provider"], model=k)
                                                      for k, v in FAKE.items()})
    monkeypatch.setattr(cm, "_metric_hours", lambda model, minutes: {"total": 0.0, "ok": 0.0, "ms": 0.0, "tokens": 0.0})
    cm._rr_counters.clear()
    cm._sticky.clear()
    yield

def model(**kw):
    base = {"prefix": "memu", "name": "smart", "default": {"candidates": ["fast-text", "big-ctx"]}}
    base.update(kw)
    return cm.normalize(base)

def facts(messages=None, params=None, **override):
    f = cm.facts_from_request(messages or [{"role": "user", "content": "hello"}], params or {})
    f.update(override)
    return f

def chunk(text="", **delta):
    d = dict(delta)
    if text:
        d["content"] = text
    return {"object": "chat.completion.chunk", "choices": [{"index": 0, "delta": d, "finish_reason": None}]}

class FakeService:
    """Stands in for ChatService: each candidate id maps to a behaviour."""

    def __init__(self, behaviours):
        self.behaviours = behaviours
        self.calls = []

    def stream_completion(self, model_id, messages, params=None, trace=None):
        self.calls.append(model_id)
        if cm.is_composite(model_id):
            return cm.run(self, model_id, messages, params or {}, trace)
        behaviour = self.behaviours.get(model_id, "ok")

        async def gen():
            if callable(behaviour):
                async for item in behaviour():
                    yield item
                return
            if behaviour == "ok":
                yield chunk("Hi from " + model_id)
                return
            if behaviour == "empty":
                yield chunk(" ")
                return
            if behaviour == "slow":
                await asyncio.sleep(5)
                yield chunk("late")
                return
            if behaviour == "midstream":
                yield chunk("partial")
                raise CompletionError("broke", status=502, err_type="upstream_error")
            status, err_type, message = behaviour
            raise CompletionError(message, status=status, err_type=err_type, message_en=message)
        return gen()

def run_composite(svc, model_id="memu/smart", messages=None, params=None):
    async def go():
        out = []
        async for item in svc.stream_completion(model_id, messages or [{"role": "user", "content": "hi"}],
                                                params if params is not None else {"_stream": True}):
            out.append(item)
        return out
    return asyncio.run(go())

def text_of(chunks):
    return "".join((c["choices"][0]["delta"].get("content") or "") for c in chunks if c.get("choices"))

def test_normalize_clamps_and_drops_unknown():
    m = cm.normalize({"id": "Memu/Smart", "junk": 1, "rules": [{"kind": "nope", "conditions": [{"type": "x"}]}],
                      "fallback": {"retries": 99, "max_attempts": 0, "on": ["429", "bogus"], "first_token_timeout": -5}})
    assert m["id"] == "memu/smart" and m["prefix"] == "memu" and m["name"] == "smart"
    assert "junk" not in m
    assert m["rules"][0]["kind"] == "route" and m["rules"][0]["conditions"][0]["type"] == "has_image"
    assert m["fallback"]["retries"] == 5 and m["fallback"]["max_attempts"] == 1
    assert m["fallback"]["on"] == ["429"] and m["fallback"]["first_token_timeout"] == 0

def test_validate_reports_errors_and_warnings():
    bad = cm.normalize({"prefix": "Мой", "name": "x y", "default": {"candidates": []}})
    paths = {i["path"] for i in cm.validate(bad, others=[]) if i["level"] == "error"}
    assert "prefix" in paths and "name" in paths and "default.candidates" in paths
    m = model(default={"candidates": ["fast-text", "ghost-model"]},
              rules=[{"kind": "route", "conditions": [{"type": "last_user", "op": "regex", "value": "(unclosed"}],
                      "candidates": ["eyes"]}])
    issues = cm.validate(m, others=[])
    assert any(i["path"] == "default.candidates[1]" and i["level"] == "warning" for i in issues)
    assert any(i["path"] == "rules[0].conditions[0]" and i["level"] == "error" for i in issues)
    for issue in issues:
        assert issue["message"] and issue["message_en"] and not re.search("[А-Яа-яЁё]", issue["message_en"])

def test_validate_rejects_regular_model_name_and_self_reference():
    m = model(prefix="fast", name="text")
    with patch.object(cm, "_concrete_ids", lambda: {"fast/text": SimpleNamespace(provider="custom")}):
        assert any("regular" in i["message_en"] for i in cm.validate(m, others=[]))
    selfref = model(default={"candidates": ["memu/smart"]})
    assert any("itself" in i["message_en"] for i in cm.validate(selfref, others=[]))

def test_cycle_detection_between_composites():
    a = model(name="a", default={"candidates": ["memu/b"]})
    b = model(name="b", default={"candidates": ["memu/a"]})
    issues = cm.validate(a, others=[b])
    assert any("loop" in i["message_en"] and i["level"] == "error" for i in issues)
    assert cm.find_cycle([a, b], "memu/a") == ["memu/a", "memu/b", "memu/a"]

def test_rule_ids_are_unique_after_normalize():
    m = model(rules=[{"id": "r1", "kind": "filter"}, {"id": "r1", "kind": "filter"}])
    assert len({r["id"] for r in m["rules"]}) == 2

def test_facts_from_request():
    messages = [{"role": "system", "content": "You are a coder"},
                {"role": "user", "content": [{"type": "text", "text": "Привет, посмотри картинку"},
                                             {"type": "image_url", "image_url": {"url": "data:,"}},
                                             {"type": "input_audio", "input_audio": {}},
                                             {"type": "file", "file": {}}]}]
    f = cm.facts_from_request(messages, {"tools": [{"type": "function"}], "tool_choice": "required",
                                         "response_format": {"type": "json_schema"}, "effort": "high",
                                         "_stream": False, "max_tokens": 900, "temperature": 0.3})
    assert f["has_image"] and f["has_audio"] and f["has_file"] and f["needs_tools"]
    assert f["tool_choice"] == "required" and f["json_mode"] and f["reasoning"] and f["effort"] == "high"
    assert f["stream"] is False and f["max_tokens"] == 900 and f["temperature"] == 0.3
    assert f["language"] == "ru" and f["system"] == "You are a coder" and f["tokens"] >= 1000
    assert f["client"] == "internal" and f["endpoint"] == "internal"
    assert cm.facts_from_request([{"role": "user", "content": "Explain the theory of relativity"}], {})["language"] == "en"

def test_client_from_asgi_scope():
    scope = {"path": "/v1/messages", "headers": [(b"user-agent", b"claude-cli/2.0"), (b"x-api-key", b"sk-secret")],
             "state": {}}
    got = cm._client_from_ctx({"scope": scope})
    assert got["endpoint"] == "anthropic" and got["client"] == "api" and got["user_agent"].startswith("claude-cli")
    assert got["api_key"].startswith("key:") and "secret" not in got["api_key"]
    scope["state"] = {"public_key": "k123"}
    assert cm._client_from_ctx({"scope": scope})["client"] == "public"
    assert cm._client_from_ctx({"scope": {"path": "/api/admin/playground/chat", "headers": []}})["client"] == "memuai"

@pytest.mark.parametrize("cond,f,expected", [
    ({"type": "tokens", "op": "gt", "value": 1000}, {"tokens": 5000}, True),
    ({"type": "tokens", "op": "lte", "value": 1000}, {"tokens": 5000}, False),
    ({"type": "max_tokens", "op": "gt", "value": 10}, {"max_tokens": None}, False),
    ({"type": "last_user", "op": "contains", "value": "PYTHON"}, {"last_user": "write python code"}, True),
    ({"type": "last_user", "op": "regex", "value": r"\bsql\b"}, {"last_user": "fix my SQL query"}, True),
    ({"type": "last_user", "op": "not_regex", "value": r"\bsql\b"}, {"last_user": "fix my SQL query"}, False),
    ({"type": "system", "op": "starts_with", "value": "you are"}, {"system": "  You are a bot"}, True),
    ({"type": "client", "op": "in", "value": ["api", "public"]}, {"client": "public"}, True),
    ({"type": "client", "op": "not_in", "value": ["api"]}, {"client": "api"}, False),
    ({"type": "weekday", "op": "in", "value": ["6", "7"]}, {"weekday": 7}, True),
    ({"type": "time", "op": "between", "value": "22:00-06:00"}, {"time": 23 * 60}, True),
    ({"type": "time", "op": "between", "value": "22:00-06:00"}, {"time": 12 * 60}, False),
    ({"type": "time", "op": "not_between", "value": "09:00-18:00"}, {"time": 20 * 60}, True),
    ({"type": "has_image", "op": "is", "value": False}, {"has_image": False}, True),
])
def test_conditions(cond, f, expected):
    c = cm._condition(cond)
    assert cm.check_condition(c, f)[0] is expected

def test_random_condition_is_seedable():
    c = cm._condition({"type": "random", "op": "chance", "value": 50})
    hits = sum(cm.check_condition(c, {}, random.Random(i))[0] for i in range(200))
    assert 60 < hits < 140
    assert not cm.check_condition(cm._condition({"type": "random", "value": 0}), {}, random.Random(1))[0]

def test_plan_first_matching_route_and_fallthrough():
    m = model(rules=[
        {"id": "img", "kind": "route", "conditions": [{"type": "has_image", "value": True}], "candidates": ["eyes"],
         "fallthrough": False},
        {"id": "long", "kind": "route", "conditions": [{"type": "tokens", "op": "gt", "value": 100000}],
         "candidates": ["big-ctx"]},
    ])
    p = cm.plan(m, facts(has_image=True))
    assert p["route"]["id"] == "img" and p["order"] == ["eyes"]
    assert p["rules"][1]["skipped"] == "after_route"
    p = cm.plan(m, facts(tokens=150000))
    assert p["route"]["id"] == "long" and p["order"][0] == "big-ctx"
    assert "fast-text" not in p["order"]
    p = cm.plan(m, facts())
    assert p["route"]["id"] == "default" and p["order"] == ["fast-text", "big-ctx"]

def test_auto_filter_and_capability_filters():
    m = model(default={"candidates": ["fast-text", "eyes", "kiro-x", "free-one"]})
    p = cm.plan(m, facts(has_image=True, needs_tools=True))
    assert p["order"] == ["eyes", "free-one"]
    reasons = {c["model"]: c["excluded"]["en"] for c in p["candidates"] if c["excluded"]}
    assert "vision" in reasons["fast-text"] and "tool" in reasons["kiro-x"]
    m = model(default={"candidates": ["fast-text", "eyes", "free-one"], "filters": {"vision": True, "vision_strict": True}})
    assert cm.plan(m, facts())["order"] == ["eyes"]
    m = model(default={"candidates": ["fast-text", "eyes", "free-one"], "filters": {"free_only": True}})
    assert cm.plan(m, facts())["order"] == ["free-one"]
    m = model(default={"candidates": ["fast-text", "big-ctx"], "filters": {"min_context": 100000}})
    assert cm.plan(m, facts())["order"] == ["big-ctx"]
    m = model(auto_filter=False, default={"candidates": ["fast-text", "eyes", "ghost"]})
    assert cm.plan(m, facts(has_image=True))["order"] == ["fast-text", "eyes"]

def test_modifier_cards_prefer_exclude_filter_settings():
    m = model(default={"candidates": ["fast-text", "big-ctx", "eyes"]}, rules=[
        {"id": "night", "kind": "prefer", "conditions": [{"type": "time", "op": "between", "value": "00:00-23:59"}],
         "models": ["eyes"]},
        {"id": "nobig", "kind": "exclude", "models": ["big-ctx"]},
        {"id": "only-tools", "kind": "filter", "conditions": [{"type": "needs_tools", "value": True}],
         "filters": {"tools": True}},
        {"id": "quick", "kind": "settings", "conditions": [{"type": "stream", "value": True}],
         "fallback": {"first_token_timeout": 15}},
    ])
    p = cm.plan(m, facts(time=600))
    assert p["order"] == ["eyes", "fast-text"]
    assert p["fallback"]["first_token_timeout"] == 15 and p["fallback"]["max_attempts"] == 6
    assert [x["id"] for x in p["modifiers"]] == ["night", "nobig", "quick"]

def test_strategies_cheapest_fastest_success():
    m = model(default={"candidates": ["big-ctx", "eyes", "fast-text", "kiro-x"], "strategy": {"type": "cheapest"}})
    assert cm.plan(m, facts())["order"] == ["fast-text", "eyes", "big-ctx", "kiro-x"]
    m["default"]["strategy"] = {"type": "cheapest", "unknown": "skip"}
    assert "kiro-x" not in cm.plan(m, facts())["order"]
    now = time.time()
    cm.observations.add("big-ctx", True, 300, 900, now)
    cm.observations.add("eyes", True, 1200, 2000, now)
    cm.observations.add("fast-text", True, 50, 4000, now - 7200)
    m = model(default={"candidates": ["fast-text", "eyes", "big-ctx"], "strategy": {"type": "fastest", "metric": "ttft", "explore": 0}})
    order = cm.plan(m, facts())["order"]
    assert order[:2] == ["big-ctx", "eyes"] and order[2] == "fast-text"
    cm.observations.add("eyes", False, 0, 100, now)
    cm.observations.add("eyes", False, 0, 100, now)
    m = model(default={"candidates": ["eyes", "big-ctx"], "strategy": {"type": "success", "explore": 0}})
    assert cm.plan(m, facts())["order"][0] == "big-ctx"

def test_exploration_tries_unmeasured_models_sometimes():
    cm.observations.add("big-ctx", True, 300, 900)
    m = model(default={"candidates": ["big-ctx", "eyes"], "strategy": {"type": "fastest", "explore": 20}})
    firsts = [cm.plan(m, facts(), rng=random.Random(i))["order"][0] for i in range(200)]
    assert 15 < firsts.count("eyes") < 70
    assert all(cm.plan(m, facts(), rng=random.Random(i), peek=True)["order"][0] == "big-ctx" for i in range(30))
    assert cm.normalize({"default": {"strategy": {"type": "fastest"}}})["default"]["strategy"]["explore"] == 10

def test_strategies_round_robin_weighted_sticky_least_used(monkeypatch):
    m = model(default={"candidates": ["fast-text", "eyes", "big-ctx"], "strategy": {"type": "round_robin"}})
    firsts = [cm.plan(m, facts())["order"][0] for _ in range(3)]
    assert firsts == ["fast-text", "eyes", "big-ctx"]
    assert cm.plan(m, facts(), peek=True)["order"][0] == cm.plan(m, facts(), peek=True)["order"][0]
    m = model(default={"candidates": [{"model": "fast-text", "weight": 9}, {"model": "eyes", "weight": 1},
                                      {"model": "big-ctx", "weight": 0}], "strategy": {"type": "weighted"}})
    wins = sum(cm.plan(m, facts(), rng=random.Random(i))["order"][0] == "fast-text" for i in range(200))
    assert wins > 150 and all(cm.plan(m, facts(), rng=random.Random(i))["order"][-1] == "big-ctx" for i in range(20))
    m = model(default={"candidates": ["fast-text", "eyes"], "strategy": {"type": "sticky"}})
    f = facts()
    cm.remember_sticky(cm.plan(m, f)["route_key"], f, "eyes")
    assert cm.plan(m, f)["order"][0] == "eyes"
    assert cm.plan(m, facts([{"role": "user", "content": "another chat"}]))["order"][0] == "fast-text"
    monkeypatch.setattr(cm, "quota_score", lambda mid: {"fast-text": 0.1, "eyes": 0.9}.get(mid))
    m = model(default={"candidates": ["fast-text", "eyes", "big-ctx"], "strategy": {"type": "least_used"}})
    assert cm.plan(m, facts())["order"] == ["eyes", "big-ctx", "fast-text"]

def test_simulate_from_sample():
    m = model(rules=[{"id": "img", "kind": "route", "conditions": [{"type": "has_image", "value": True}],
                      "candidates": ["eyes"], "fallthrough": False},
                     {"id": "night", "kind": "route", "conditions": [{"type": "time", "op": "between", "value": "22:00-06:00"}],
                      "candidates": ["big-ctx"]}])
    cm.store.upsert(m)
    r = cm.simulate(m, {"text": "look", "has_image": True})
    assert r["order"] == ["eyes"] and r["facts"]["has_image"] and r["rules"][0]["matched"]
    r = cm.simulate(m, {"text": "hi", "time": "23:30"})
    assert r["route"]["id"] == "night" and r["facts"]["time"] == "23:30"
    r = cm.simulate(m, {"text": "hi", "tokens": 50000, "client": "public", "weekday": 6})
    assert r["facts"]["tokens"] == 50000 and r["facts"]["client"] == "public" and r["facts"]["weekday"] == 6
    assert "fast-text" not in r["order"]

def test_run_falls_back_on_error_and_records_stats():
    cm.store.upsert(model(default={"candidates": ["fast-text", "eyes"]}))
    svc = FakeService({"fast-text": (404, "model_not_found", "no such model")})
    out = run_composite(svc)
    assert text_of(out) == "Hi from eyes" and svc.calls == ["memu/smart", "fast-text", "eyes"]
    st = cm.stats.get("memu/smart")
    assert st["requests"] == 1 and st["ok"] == 1 and st["fallbacks"] == 1

    assert st["recent"][0]["chosen"] == "eyes" and st["recent"][0]["attempts"][0]["trigger"] == "unavailable"

def test_run_trigger_not_selected_surfaces_error():
    cm.store.upsert(model(default={"candidates": ["fast-text", "eyes"]}, fallback={"on": ["429"]}))
    svc = FakeService({"fast-text": (502, "upstream_error", "boom")})
    with pytest.raises(CompletionError) as err:
        run_composite(svc)
    assert err.value.status == 502 and svc.calls == ["memu/smart", "fast-text"]

def test_run_retries_429_with_backoff_then_next():
    cm.store.upsert(model(default={"candidates": ["fast-text", "eyes"]},
                          fallback={"retries": 2, "backoff_ms": 0, "on": ["429"]}))
    svc = FakeService({"fast-text": (429, "rate_limit_error", "slow down")})
    out = run_composite(svc)
    assert svc.calls.count("fast-text") == 3 and text_of(out) == "Hi from eyes"

def test_run_max_attempts_and_exhausted_message():
    cm.store.upsert(model(default={"candidates": ["fast-text", "eyes", "big-ctx"]}, fallback={"max_attempts": 2}))
    svc = FakeService({"fast-text": (500, "upstream_error", "a"), "eyes": (500, "upstream_error", "b")})
    with pytest.raises(CompletionError) as err:
        run_composite(svc)
    assert "big-ctx" not in svc.calls
    assert "every model failed" in err.value.message_en and "fast-text" in err.value.message_en

def test_run_first_token_timeout_moves_on():
    cm.store.upsert(model(default={"candidates": ["fast-text", "eyes"]}, fallback={"first_token_timeout": 1}))
    svc = FakeService({"fast-text": "slow"})
    started = time.time()
    out = run_composite(svc)
    assert text_of(out) == "Hi from eyes" and time.time() - started < 4
    assert cm.stats.get("memu/smart")["recent"][0]["attempts"][0]["trigger"] == "timeout"

def test_run_empty_answer_moves_on_and_after_commit_error_surfaces():
    cm.store.upsert(model(default={"candidates": ["fast-text", "eyes"]}))
    assert text_of(run_composite(FakeService({"fast-text": "empty"}), params={"_stream": False})) == "Hi from eyes"

    cm.store.upsert(model(default={"candidates": ["fast-text", "eyes"]}, fallback={"stream_recovery": False}))
    svc = FakeService({"fast-text": "midstream"})
    got = []

    async def go():
        async for item in svc.stream_completion("memu/smart", [{"role": "user", "content": "hi"}], {"_stream": True}):
            got.append(item)
    with pytest.raises(CompletionError):
        asyncio.run(go())
    assert text_of(got) == "partial" and "eyes" not in svc.calls

    svc = FakeService({"fast-text": "midstream"})
    assert text_of(run_composite(svc, params={"_stream": False})) == "Hi from eyes"

def test_run_context_length_goes_to_bigger_window():
    cm.store.upsert(model(default={"candidates": ["fast-text", "eyes", "free-one", "big-ctx"]}, auto_filter=False))
    svc = FakeService({"fast-text": (400, "upstream_error", "This model's maximum context length is 32000 tokens")})
    run_composite(svc)
    assert svc.calls[:3] == ["memu/smart", "fast-text", "free-one"]

def test_nested_composite_and_runtime_loop_guard():
    cm.store.upsert(model(name="inner", default={"candidates": ["fast-text", "eyes"]}))
    cm.store.upsert(model(name="outer", default={"candidates": ["memu/inner", "big-ctx"]}))
    svc = FakeService({"fast-text": (500, "upstream_error", "x")})
    assert text_of(run_composite(svc, "memu/outer")) == "Hi from eyes"

    cm.store.save_all([model(name="a", default={"candidates": ["memu/b"]}), model(name="b", default={"candidates": ["memu/a"]})])
    with pytest.raises(CompletionError) as err:
        run_composite(FakeService({}), "memu/a")
    assert "loop" in err.value.message_en

def test_classify():
    assert cm.classify(CompletionError("x", status=429, err_type="rate_limit_error")) == "429"
    assert cm.classify(CompletionError("prompt is too long: 300000 tokens", status=400, err_type="upstream_error")) == "context_length"
    assert cm.classify(CompletionError("x", status=400, err_type="content_filter")) == "content_filter"
    assert cm.classify(CompletionError("провайдер завершил поток без текста", status=502, err_type="upstream_error")) == "empty"
    assert cm.classify(CompletionError("x", status=503, err_type="service_unavailable")) == "5xx"
    assert cm.classify(cm._Timeout("connect", 5)) == "timeout"
    assert cm.classify(CompletionError("bad request", status=400, err_type="invalid_request_error")) == "error"

def test_header_value_ascii_and_nested_chain():
    sink = {"routes": {0: {"composite": "memu/smart", "rule": "Картинка", "chosen": "memu/inner",
                           "attempts": [{"model": "a", "ok": False, "trigger": "429"}, {"model": "memu/inner", "ok": True}]},
                       1: {"chosen": "eyes"}}}
    value = cm._header_value(sink)
    value.encode("latin-1")
    assert "chosen=memu/inner>eyes" in value and "tried=a:429,memu/inner:ok" in value

def test_listing_hooks_and_tools_support():
    cm.store.upsert(model(title="Умная", description="d", default={"candidates": ["eyes", "kiro-x"]}))
    cm.store.upsert(model(name="off", enabled=False))
    rows = cm.listed()
    assert [r["id"] for r in rows] == ["memu/smart"] and rows[0]["owned_by"] == "memuai-composite"
    pg = cm.pg_rows()[0]
    assert pg["caps"]["vision_state"] == "yes" and pg["display_name"] == "Умная" and pg["composite"]
    from app import model_catalog
    payload = {"models": [dict(pg)]}
    model_catalog.enrich(payload)
    assert payload["models"][0]["name"] == "Умная" and payload["models"][0]["kind"] == "chat"
    assert payload["models"][0]["caps"]["vision_state"] == "yes"
    assert cm.tools_supported("memu/smart") is True
    from app.tool_protocol import tool_provider_supported
    assert tool_provider_supported("memu/smart") is True
    from app import openai_api
    assert "memu/smart" in [r["id"] for r in openai_api.listed_models()]

def test_presets_are_valid_models():
    available = [{"id": k, "name": k, "account": "x", **{kk: vv for kk, vv in v.items() if kk != "price"},
                  "price": list(v["price"]) if v.get("price") else None} for k, v in FAKE.items()]
    presets = cm.presets(available)
    keys = {p["key"] for p in presets}
    assert {"reliable", "vision", "long", "day_night"} <= keys
    vision = next(p for p in presets if p["key"] == "vision")["model"]
    assert vision["rules"][0]["candidates"][0]["model"] in ("eyes", "kiro-x")
    for p in presets:
        assert not [i for i in cm.validate(p["model"], others=[]) if i["level"] == "error"], p["key"]
        assert p["title"] and p["title_en"] and not re.search("[А-Яа-яЁё]", p["title_en"] + p["description_en"])

def test_import_export_roundtrip():
    cm.store.upsert(model(title="A"))
    data = cm.export_json()
    assert data["memuai"] == "composite-models" and data["models"][0]["id"] == "memu/smart"
    cm.store.delete("memu/smart")
    assert cm.import_json(data)["saved"] == ["memu/smart"] and cm.get("memu/smart")["title"] == "A"
    res = cm.import_json({"models": [{"prefix": "memu", "name": "bad", "default": {"candidates": ["memu/bad"]}}]})
    assert res["errors"] and cm.get("memu/bad") is None
    assert cm.import_json("nonsense")["errors"]

@pytest.fixture()
def bare_client(monkeypatch):
    """The app without its lifespan (no Proxy background loops)."""
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.chat import ChatService
    from app.config import config_store
    original = ChatService.stream_completion

    def fake_stream(self, model_id, messages, params=None, trace=None):
        if cm.is_composite(model_id):
            return original(self, model_id, messages, params, trace)
        return FakeService({"fast-text": (500, "upstream_error", "down")}).stream_completion(model_id, messages, params, trace)

    monkeypatch.setattr(ChatService, "stream_completion", fake_stream)
    monkeypatch.setattr(config_store, "authorize_api_request", lambda *a, **k: True)
    from app import admin_api
    monkeypatch.setattr(admin_api, "_auth", lambda request: None)
    return TestClient(create_app())

def test_admin_api_save_validate_simulate_delete(bare_client):
    c = bare_client
    r = c.post("/api/admin/composite/save", json={"model": {"prefix": "memu", "name": "x", "default": {"candidates": []}}})
    assert r.status_code == 400 and r.json()["issues"]
    r = c.post("/api/admin/composite/save", json={"model": {"prefix": "memu", "name": "inner",
                                                             "default": {"candidates": ["fast-text", "eyes"]}}})
    assert r.status_code == 200, r.text
    r = c.post("/api/admin/composite/save", json={"model": {"prefix": "memu", "name": "outer",
                                                             "default": {"candidates": ["memu/inner"]}}})
    assert r.status_code == 200
    r = c.post("/api/admin/composite/simulate", json={"model": cm.get("memu/inner"), "sample": {"text": "hi"}})
    assert r.json()["order"] == ["fast-text", "eyes"]
    r = c.post("/api/admin/composite/delete", json={"id": "memu/inner"})
    assert r.status_code == 409 and r.json()["used_by"] == ["memu/outer"]

    cm.stats.record("memu/outer", {"ok": True, "attempts": []})
    r = c.post("/api/admin/composite/save", json={"model": dict(cm.get("memu/outer"), name="renamed"), "original_id": "memu/outer"})
    assert r.status_code == 200 and cm.get("memu/outer") is None and cm.stats.get("memu/renamed")["requests"] == 1
    r = c.get("/api/admin/composite")
    body = r.json()
    assert {m["id"] for m in body["models"]} == {"memu/inner", "memu/renamed"}
    assert body["schema"]["conditions"]["tokens"]["ops"] and body["presets"]
    r = c.get("/api/admin/composite/export?id=memu/inner")
    assert r.json()["models"][0]["id"] == "memu/inner" and "attachment" in r.headers["content-disposition"]
    assert c.post("/api/admin/composite/toggle", json={"id": "memu/inner", "enabled": False}).json()["model"]["enabled"] is False

def test_v1_endpoints_with_route_header(bare_client):
    c = bare_client
    cm.store.upsert(model(default={"candidates": ["fast-text", "eyes"]}))
    ids = [m["id"] for m in c.get("/v1/models").json()["data"]]
    assert "memu/smart" in ids
    assert c.get("/v1/models/memu/smart").json()["id"] == "memu/smart"
    r = c.post("/v1/chat/completions", json={"model": "memu/smart", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200, r.text
    assert r.json()["choices"][0]["message"]["content"] == "Hi from eyes"
    route = r.headers["x-memu-route"]
    assert "model=memu/smart" in route and "chosen=eyes" in route and "fast-text:5xx" in route
    with c.stream("POST", "/v1/chat/completions", json={"model": "memu/smart", "stream": True,
                                                         "messages": [{"role": "user", "content": "hi"}]}) as s:
        body = "".join(s.iter_text())
        assert "chosen=eyes" in s.headers["x-memu-route"] and "eyes:ok" in s.headers["x-memu-route"]
    assert "Hi from eyes" in body and "[DONE]" in body and '"error"' not in body
    r = c.post("/v1/messages", json={"model": "memu/smart", "max_tokens": 50, "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200 and r.json()["content"][0]["text"] == "Hi from eyes" and "x-memu-route" in r.headers
    with c.stream("POST", "/v1/messages", json={"model": "memu/smart", "max_tokens": 50, "stream": True,
                                                "messages": [{"role": "user", "content": "hi"}]}) as s:
        body = "".join(s.iter_text())
    assert "Hi from eyes" in body and "event: error" not in body

    r = c.post("/v1/chat/completions", json={"model": "memu/smart", "messages": [{"role": "user", "content": "hi"}],
                                             "tools": [{"type": "function", "function": {"name": "f", "parameters": {}}}]})
    assert r.status_code == 200, r.text

def test_v1_regular_requests_have_no_route_header(bare_client):
    r = bare_client.get("/v1/models")
    assert "x-memu-route" not in r.headers

def test_custom_403_for_one_model_is_not_a_rejected_key():
    from app.providers.custom import CustomProvider
    from app.providers.base import ProviderAuthError, ProviderModelUnavailableError
    provider = CustomProvider.__new__(CustomProvider)
    head = json.dumps({"error": {"message": "Для API-ключа нет доступа к этому запросу. Проверьте права"}})
    with pytest.raises(ProviderModelUnavailableError):
        asyncio.run(provider._raise_for_status(403, head))
    with pytest.raises(ProviderModelUnavailableError):
        asyncio.run(provider._raise_for_status(403, '{"error":{"message":"Project does not have access to model gpt-5"}}'))
    with pytest.raises(ProviderAuthError):
        asyncio.run(provider._raise_for_status(403, '{"error":{"message":"Invalid API key provided"}}'))
    with pytest.raises(ProviderAuthError):
        asyncio.run(provider._raise_for_status(401, head))

def test_ui_files_hooked_and_parse():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'data-tab="composite"' in html and 'id="tab-composite"' in html and 'id="composite-root"' in html
    assert "/composite.js" in html and "/composite.css" in html and "/composite-i18n.js" in html
    assert html.index("/composite-i18n.js") < html.index("/app.js")
    node = shutil.which("node")
    if node:
        for name in ("composite.js", "composite-i18n.js"):
            assert subprocess.run([node, "--check", str(STATIC / name)], capture_output=True).returncode == 0, name

def test_ui_english_strings_have_no_cyrillic():
    src = (STATIC / "composite.js").read_text(encoding="utf-8")

    pairs = re.findall(r'L\(\s*"((?:[^"\\]|\\.)*)"\s*,\s*"((?:[^"\\]|\\.)*)"\s*\)', src)
    pairs += re.findall(r'\[\s*"((?:[^"\\]|\\.)*)"\s*,\s*"((?:[^"\\]|\\.)*)"\s*\]', src)
    pairs += re.findall(r'L\(\s*`((?:[^`\\]|\\.)*)`\s*,\s*`((?:[^`\\]|\\.)*)`\s*\)', src)
    assert len(pairs) > 150
    bad = [en for _ru, en in pairs if re.search("[А-Яа-яЁё]", en)]
    assert not bad, bad[:5]
    i18n = (STATIC / "composite-i18n.js").read_text(encoding="utf-8")
    en_part = i18n[i18n.index("I18N.en"):]
    assert not re.search("[А-Яа-яЁё]", en_part)

def test_build_and_specs_list_the_modules():
    build = (ROOT / "build.py").read_text(encoding="utf-8")
    assert build.count('"app.composite_models"') >= 3
    for spec in ("MemuAI.spec", "MemuAIProxy.spec"):
        assert "'app.composite_models'" in (ROOT / spec).read_text(encoding="utf-8")

@pytest.mark.skipif(not (os.environ.get("MEMU_LIVE_COMPOSITE") and os.environ.get("REFORM_KEY")),
                    reason="live: MEMU_LIVE_COMPOSITE=1 and REFORM_BASE/REFORM_KEY")
def test_live_reform_failover_and_vision(monkeypatch, tmp_path):
    """Real reformboss account through the Proxy app: a model the key cannot use → fallback; a picture → Qwen-VL."""
    import base64
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.config import config_store
    from app.model_capabilities import solid_png
    monkeypatch.undo()
    monkeypatch.setattr(config_store, "authorize_api_request", lambda *a, **k: True)
    from app import admin_api
    monkeypatch.setattr(admin_api, "_auth", lambda request: None)
    cm.store.reset_cache()
    c = TestClient(create_app())
    name = "r35live"
    r = c.post("/api/admin/accounts", json={"provider": "custom", "fields": {
        "name": name, "base_url": os.environ.get("REFORM_BASE", "https://api.reformboss.com/v1"),
        "api_key": os.environ["REFORM_KEY"], "models": "qwen3.8-flash,qwen3-vl-plus,broken-no-such-model"}})
    assert r.status_code == 200 or "уже существует" in r.text
    composite = {"prefix": "r35live", "name": "smart", "default": {"candidates": [f"{name}-broken-no-such-model",
                                                                                     f"{name}-qwen3.8-flash"]},
                 "rules": [{"kind": "route", "conditions": [{"type": "has_image", "value": True}],
                            "candidates": [f"{name}-qwen3-vl-plus"], "fallthrough": False}]}
    assert c.post("/api/admin/composite/save", json={"model": composite}).status_code == 200
    r = c.post("/v1/chat/completions", json={"model": "r35live/smart", "max_tokens": 50,
                                             "messages": [{"role": "user", "content": "Say OK"}]})
    assert r.status_code == 200, r.text
    assert f"chosen={name}-qwen3.8-flash" in r.headers["x-memu-route"]
    img = base64.b64encode(solid_png((220, 30, 30), 64)).decode()
    r = c.post("/v1/chat/completions", json={"model": "r35live/smart", "max_tokens": 50, "messages": [{"role": "user", "content": [
        {"type": "text", "text": "What colour is this picture? One word."},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + img}}]}]})
    assert r.status_code == 200, r.text
    assert f"chosen={name}-qwen3-vl-plus" in r.headers["x-memu-route"]
    assert "red" in r.json()["choices"][0]["message"]["content"].lower()
