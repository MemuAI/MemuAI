"""Round 40 (r40-proxy): Smart Stream Recovery, the «difficulty» condition, prompt caching + «Сэкономлено кэшем»,
web models («<model>:web») of MemuAI Proxy. No network: providers, search and pages are faked.

Run: NO_PROXY=127.0.0.1,localhost python -m pytest -q tests/round40_proxy_test.py
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
from pathlib import Path
from types import SimpleNamespace

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-r40-proxy-")

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import composite_models as cm  # noqa: E402
from app import difficulty, prompt_cache, stream_recovery, web_models  # noqa: E402
from app.chat import CompletionError  # noqa: E402

STATIC = ROOT / "app" / "static"

@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "store", cm._Store(str(tmp_path / "composite_models.json")))
    monkeypatch.setattr(cm, "stats", cm._Stats(str(tmp_path / "composite_stats.json")))
    monkeypatch.setattr(cm, "observations", cm._Observations())
    monkeypatch.setattr(cm, "model_caps", lambda mid, concrete=None: {
        "available": True, "vision": "unknown", "tools": True, "context": 128000, "price": None, "free": False})
    monkeypatch.setattr(cm, "_concrete_ids", lambda: {k: SimpleNamespace(id=k, prefix="x", provider="custom", model=k)
                                                      for k in ("a", "b", "c", "fast", "flagship")})
    monkeypatch.setattr(cm, "_metric_hours", lambda model, minutes: {"total": 0.0, "ok": 0.0, "ms": 0.0, "tokens": 0.0})
    monkeypatch.setattr(web_models, "store", web_models._Store(str(tmp_path / "web_models.json")))
    yield

def chunk(text="", finish=None, **delta):
    d = dict(delta)
    if text:
        d["content"] = text
    return {"id": "x", "object": "chat.completion.chunk", "model": "m",
            "choices": [{"index": 0, "delta": d, "finish_reason": finish}]}

def call_delta(index, id_=None, name=None, args=None):
    part = {"index": index}
    if id_:
        part["id"], part["type"] = id_, "function"
    fn = {}
    if name:
        fn["name"] = name
    if args is not None:
        fn["arguments"] = args
    part["function"] = fn
    return chunk(tool_calls=[part])

class FakeService:
    """Each model id → a list of scripted items; an Exception item is raised at that point."""

    def __init__(self, scripts):
        self.scripts = scripts
        self.calls = []

    def stream_completion(self, model_id, messages, params=None, trace=None):
        self.calls.append((model_id, [dict(m) for m in messages], dict(params or {})))
        if cm.is_composite(model_id):
            return cm.run(self, model_id, messages, params or {}, trace)
        if web_models.is_web(model_id):
            return web_models.run(self, model_id, messages, params or {}, trace)
        script = self.scripts[model_id]
        if callable(script):
            script = script(messages, params or {})

        async def gen():
            for item in script:
                if isinstance(item, BaseException):
                    raise item
                yield item
        return gen()

def collect(svc, model_id, messages=None, params=None):
    async def go():
        out = []
        async for item in svc.stream_completion(model_id, messages or [{"role": "user", "content": "hi"}],
                                                params if params is not None else {"_stream": True}):
            out.append(item)
        return out
    return asyncio.run(go())

def text_of(items):
    return "".join((c["choices"][0]["delta"].get("content") or "") for c in items
                   if c.get("object") == "chat.completion.chunk" and c.get("choices"))

def calls_of(items):
    out = []
    for c in items:
        if c.get("object") == "chat.completion.chunk" and c.get("choices"):
            out += c["choices"][0]["delta"].get("tool_calls") or []
    return out

def broke():
    return CompletionError("The upstream stream broke off", status=502, err_type="upstream_error")

def composite(**kw):
    base = {"prefix": "memu", "name": "r", "default": {"candidates": ["a", "b", "c"]}}
    base.update(kw)
    cm.store.upsert(cm.normalize(base))

def test_overlap_cuts_repeated_joint():
    assert stream_recovery.overlap("Hello wor", "world and more") == 3
    assert stream_recovery.overlap("Первое. Второе предложение обо", "Второе предложение обо всём") == len("Второе предложение обо")
    assert stream_recovery.overlap("abc", "xyz") == 0
    assert stream_recovery.overlap("The answer is", " 42.") == 0

def test_break_mid_answer_is_continued_by_next_candidate():
    composite()
    svc = FakeService({
        "a": [chunk("Однажды в студёную зимнюю пору я из лесу вы"), broke()],
        "b": [chunk("я из лесу вышел; был сильный мороз."), chunk("", finish="stop")],
    })
    got = collect(svc, "memu/r", [{"role": "user", "content": "Прочитай Некрасова"}])
    assert text_of(got) == "Однажды в студёную зимнюю пору я из лесу вышел; был сильный мороз."

    model_b, messages_b, _ = svc.calls[-1]
    assert model_b == "b"
    assert messages_b[-2] == {"role": "assistant", "content": "Однажды в студёную зимнюю пору я из лесу вы"}
    assert messages_b[-1]["role"] == "user" and "оборвался" in messages_b[-1]["content"]
    assert any(i.get("object") == "memuai.event" and i.get("event") == "composite_recovery" for i in got)
    recent = cm.stats.get("memu/r")["recent"][0]
    assert recent["ok"] and recent["recoveries"] == 1 and recent["chosen"] == "b"
    assert cm.stats.get("memu/r")["recoveries"] == 1
    header = cm._header_value({"routes": {0: {**recent, "composite": "memu/r"}}})
    assert "recovered=1" in header

def test_recovery_limit_and_switch_off():
    composite(fallback={"recovery_attempts": 1})
    svc = FakeService({"a": [chunk("one "), broke()], "b": [chunk("two "), broke()], "c": [chunk("three")]})
    with pytest.raises(CompletionError):
        collect(svc, "memu/r")
    composite(fallback={"stream_recovery": False})
    svc = FakeService({"a": [chunk("one "), broke()], "b": [chunk("two")]})
    with pytest.raises(CompletionError):
        collect(svc, "memu/r")
    assert [c[0] for c in svc.calls] == ["memu/r", "a"]

def test_content_filter_is_not_recovered():
    composite()
    svc = FakeService({"a": [chunk("text "), CompletionError("blocked", status=400, err_type="content_filter")],
                       "b": [chunk("other")]})
    with pytest.raises(CompletionError):
        collect(svc, "memu/r")

def test_break_inside_tool_call_after_complete_calls_ends_turn_with_tool_calls():
    composite()
    svc = FakeService({"a": [call_delta(0, "c1", "get_weather", '{"city": '), call_delta(0, args='"Riga"}'),
                             call_delta(1, "c2", "get_time", '{"tz'), broke()],
                       "b": [chunk("never")]})
    got = collect(svc, "memu/r", params={"_stream": True, "tools": [{"type": "function", "function": {"name": "x"}}]})
    calls = calls_of(got)
    assert len(calls) == 1 and calls[0]["id"] == "c1"
    assert json.loads(calls[0]["function"]["arguments"]) == {"city": "Riga"}
    finishes = [c["choices"][0]["finish_reason"] for c in got if c.get("choices") and c["choices"][0].get("finish_reason")]
    assert finishes == ["tool_calls"]
    assert "b" not in [c[0] for c in svc.calls]

def test_break_inside_first_tool_call_is_reissued_by_next_candidate():
    composite()
    svc = FakeService({"a": [chunk("Checking. "), call_delta(0, "c1", "get_weather", '{"ci'), broke()],
                       "b": [call_delta(0, "z9", "get_weather", '{"city": "Oslo"}'), chunk("", finish="tool_calls")]})
    got = collect(svc, "memu/r", params={"_stream": True, "tools": [{"type": "function", "function": {"name": "x"}}]})
    calls = calls_of(got)
    assert text_of(got) == "Checking. "
    assert len(calls) == 1 and calls[0]["id"] == "z9" and json.loads(calls[0]["function"]["arguments"]) == {"city": "Oslo"}

def test_empty_continuation_is_fine():
    composite()
    svc = FakeService({"a": [chunk("Almost done."), broke()], "b": [chunk("", finish="stop")]})
    assert text_of(collect(svc, "memu/r")) == "Almost done."

@pytest.mark.parametrize("text,level", [
    ("привет!", "chat"),
    ("Переведи на английский: «Доброе утро, коллеги»", "translation"),
    ("Кратко перескажи эту статью: ...", "summary"),
    ("Сколько будет 2+2?", "simple"),
    ("Найди race condition и deadlock в этом многопоточном коде:\n```python\nimport threading\n```", "complex_code"),
    ("Докажи теорему: интеграл от f по замкнутому контуру равен нулю", "deep_math"),
    ("Спроектируй архитектуру высоконагруженного сервиса с шардированием и микросервисами", "architecture"),
    ("Design a scalable distributed architecture for a chat system, discuss trade-offs", "architecture"),
])
def test_difficulty_heuristics(text, level):
    assert difficulty.classify_text(text)[0] == level

def test_difficulty_condition_routes_and_model_refines_with_cache():
    composite(default={"candidates": ["fast"]},
              rules=[{"kind": "route", "conditions": [{"type": "difficulty", "op": "in",
                                                       "value": ["complex_code", "deep_math", "architecture"]}],
                      "candidates": ["flagship"], "fallthrough": False}])
    svc = FakeService({"fast": [chunk("fast")], "flagship": [chunk("flag")]})
    assert text_of(collect(svc, "memu/r", [{"role": "user", "content": "Докажи лемму о вложенных отрезках, теорема Коши"}])) == "flag"
    assert text_of(collect(svc, "memu/r", [{"role": "user", "content": "привет"}])) == "fast"

    composite(default={"candidates": ["fast"]}, difficulty_model="cls",
              rules=[{"kind": "route", "conditions": [{"type": "difficulty", "op": "in", "value": ["architecture"]}],
                      "candidates": ["flagship"], "fallthrough": False}])
    svc = FakeService({"fast": [chunk("fast")], "flagship": [chunk("flag")], "cls": [chunk("architecture")]})
    msg = [{"role": "user", "content": "Как лучше разложить наш бэкенд по сервисам на следующий год, с учётом роста команды?"}]
    assert text_of(collect(svc, "memu/r", msg)) == "flag"
    assert text_of(collect(svc, "memu/r", msg)) == "flag"
    assert [c[0] for c in svc.calls].count("cls") == 1
    assert cm.stats.get("memu/r")["recent"][0]["difficulty"] == "architecture"

def test_difficulty_in_schema_and_ui():
    assert "difficulty" in cm.SCHEMA["conditions"]
    assert "complex_code" in cm.SCHEMA["conditions"]["difficulty"]["options"]
    assert cm.normalize({"id": "memu/x", "fallback": {"recovery_attempts": 99}})["fallback"]["recovery_attempts"] == 5
    assert any(p["key"] == "difficulty" for p in cm.presets([]))
    src = (STATIC / "composite.js").read_text(encoding="utf-8")
    assert 'difficulty: ["Сложность задачи", "Task difficulty"]' in src
    assert "stream_recovery" in src and "recovery_attempts" in src and "difficulty_model" in src

def _long_chat(turns=6):
    messages = [{"role": "system", "content": "You are a coding agent. " * 200}]
    for i in range(turns):
        messages += [{"role": "user", "content": f"question {i} " * 80}, {"role": "assistant", "content": f"answer {i} " * 80}]
    return messages + [{"role": "user", "content": "the newest question"}]

def _markers(body):
    return [i for i, m in enumerate(body["messages"]) if isinstance(m.get("content"), list)
            and any(p.get("cache_control") for p in m["content"])]

def test_claude_gets_at_most_four_breakpoints_on_stable_prefix():
    body = {"model": "anthropic/claude-sonnet-4.6", "messages": _long_chat()}
    cached, used = prompt_cache.apply(body, "anthropic/claude-sonnet-4.6", base="https://openrouter.ai/api/v1")
    marks = _markers(cached)
    assert used and 1 <= len(marks) <= 4
    assert marks[0] == 0
    assert len(body["messages"]) - 2 in marks
    assert len(body["messages"]) - 1 in marks
    assert body["messages"][0]["content"].startswith("You are")

def test_gemini_openrouter_one_breakpoint_deepseek_implicit_client_markers_kept():
    body = {"model": "google/gemini-2.5-pro", "messages": _long_chat()}
    cached, used = prompt_cache.apply(body, "google/gemini-2.5-pro", base="https://openrouter.ai/api/v1")
    assert used and len(_markers(cached)) == 1
    same, used = prompt_cache.apply(body, "gemini-2.5-pro", base="https://generativelanguage.googleapis.com/v1beta")
    assert not used and same is body
    same, used = prompt_cache.apply(body, "deepseek-chat", base="https://api.deepseek.com/v1")
    assert not used and same is body
    own = {"messages": [{"role": "system", "content": [{"type": "text", "text": "x", "cache_control": {"type": "ephemeral"}}]},
                        {"role": "user", "content": "hi"}]}
    same, used = prompt_cache.apply(own, "claude-opus-4", base="")
    assert not used and same is own
    gpt, used = prompt_cache.apply(body, "gpt-5.6", base="")
    assert used and gpt["prompt_cache_key"].startswith("memuai-")

def test_metrics_count_cache_savings(tmp_path):
    from app import metrics as metrics_mod, pricing
    m = metrics_mod.Metrics(str(tmp_path / "metrics.json"))
    m.record(provider="custom", model="deepseek-chat", ok=True, ms=100, prompt_tokens=1_000_000,
             completion_tokens=1000, cache_read_tokens=800_000)
    snap = m.snapshot("day")
    assert snap["cache"]["tokens"] == 800_000 and snap["cache"]["share"] == 80.0
    price = pricing.lookup("deepseek-chat", "custom")
    if price is not None:
        rate = price.cache_read if price.cache_read is not None else price.input * 0.1
        assert abs(snap["cache"]["saved_usd"] - 0.8 * (price.input - rate)) < 1e-6
    ui = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "renderMetricsCache" in ui and 'id="metrics-cache"' in (STATIC / "index.html").read_text(encoding="utf-8")
    i18n = (STATIC / "i18n.js").read_text(encoding="utf-8")
    assert i18n.count('"metrics.cache.saved"') == 2

@pytest.fixture
def web(monkeypatch):
    monkeypatch.setattr(web_models, "base_exists", lambda base: True)
    monkeypatch.setattr(web_models, "native_tools", lambda base: base != "plain")
    searches = []

    async def fake_search(settings, query, queries):
        searches.append((query, settings["results"], settings["engine"]))
        return {"query": query, "queries": [query], "answer": None,
                "results": [{"title": "Python.org", "url": "https://www.python.org/downloads/", "snippet": "Python 3.14.1"},
                            {"title": "Wiki", "url": "https://en.wikipedia.org/wiki/Python", "snippet": "history"}],
                "pages": [{"url": "https://www.python.org/downloads/", "title": "Python.org", "excerpt": "Latest: 3.14.1"}]}
    monkeypatch.setattr(web_models, "_search", fake_search)
    return searches

def agent_script(messages, params):
    tools = {t["function"]["name"] for t in params.get("tools") or []}
    if any(m.get("role") == "tool" for m in messages):
        assert params.get("tool_choice") == "none"
        return [chunk("Последняя версия — 3.14.1 [1]."), chunk("", finish="stop")]
    assert "web_search" in tools and "calculator" in tools
    return [call_delta(0, "s1", "web_search", '{"query": "python latest version"}'), chunk("", finish="tool_calls")]

def test_web_model_tool_loop_sources_and_listing(web):
    row = web_models.store.upsert("base-x", {"max_steps": 1, "tools": ["calculator"], "progress": "thinking",
                                             "engine": "duckduckgo", "results": 7})
    assert row["id"] == "base-x:web" and web_models.is_web("base-x:web")
    assert any(r["id"] == "base-x:web" and r["web"] for r in web_models.listed())
    svc = FakeService({"base-x": agent_script})
    got = collect(svc, "base-x:web", [{"role": "user", "content": "Какая последняя версия Python?"}])
    text = text_of(got)
    assert text.startswith("Последняя версия — 3.14.1 [1].")
    assert "**Источники:**" in text and "(https://www.python.org/downloads/)" in text and "wikipedia" not in text
    assert web == [("python latest version", 7, "duckduckgo")]
    reasoning = "".join(c["choices"][0]["delta"].get("reasoning_content") or "" for c in got if c.get("choices"))
    assert "Ищу" in reasoning
    final = [c for c in got if c.get("choices") and c["choices"][0].get("finish_reason")]
    assert final[-1]["choices"][0]["finish_reason"] == "stop" and final[-1]["citations"] == ["https://www.python.org/downloads/"]
    assert all(c["model"] == "base-x:web" for c in got if c.get("object") == "chat.completion.chunk")

    tool_msg = next(m for m in svc.calls[-1][1] if m.get("role") == "tool")
    assert "[1] Python.org" in tool_msg["content"] and "Excerpt: Latest: 3.14.1" in tool_msg["content"]

def test_web_model_presearch_for_model_without_tools_and_client_tools_pass_through(web):
    web_models.store.upsert("plain", {"citations": False})

    def plain(messages, params):
        assert "tools" not in params
        ctx = [m for m in messages if m.get("role") == "user" and "untrusted" in str(m.get("content"))]
        assert ctx and messages[-1]["content"] == "новости python"
        return [chunk("Ответ."), chunk("", finish="stop")]
    svc = FakeService({"plain": plain})
    got = collect(svc, "plain:web", [{"role": "user", "content": "новости python"}])
    assert text_of(got) == "Ответ." and web

    web_models.store.upsert("base-y", {})

    def client_tool(messages, params):
        names = [t["function"]["name"] for t in params["tools"]]
        assert "web_search" in names and "lookup_crm" in names
        return [call_delta(0, "k1", "lookup_crm", '{"id": 5}'), chunk("", finish="tool_calls")]
    svc = FakeService({"base-y": client_tool})
    got = collect(svc, "base-y:web", params={"_stream": True, "tools": [
        {"type": "function", "function": {"name": "lookup_crm", "parameters": {"type": "object"}}}]})
    calls = calls_of(got)
    assert calls == [{"index": 0, "id": "k1", "type": "function", "function": {"name": "lookup_crm", "arguments": '{"id": 5}'}}]
    assert got[-1]["choices"][0]["finish_reason"] == "tool_calls"

def test_web_model_settings_normalized_and_delete():
    s = web_models.normalize_settings({"results": 99, "max_steps": 0, "engine": "nope", "tools": ["calculator", "rm"],
                                       "progress": "loud", "lang": "RU!"})
    assert s["results"] == 10 and s["max_steps"] == 1 and s["engine"] == "" and s["tools"] == ["calculator"]
    assert s["progress"] == "off" and s["lang"] == "ru"
    web_models.store.upsert("m1", {})
    assert web_models.store.delete("m1:web") and not web_models.is_web("m1:web")
    assert web_models.calculate("2^10 + sqrt(16)") == "1028"
    assert web_models.calculate("__import__('os')").startswith("error")

def test_web_model_endpoints_openai_and_anthropic(web, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app import admin_api, chat
    from app.config import config_store
    from app.main import create_app
    monkeypatch.setattr(config_store, "authorize_api_request", lambda *a, **k: True)
    monkeypatch.setattr(admin_api, "_auth", lambda request: None)
    monkeypatch.setattr(config_store.settings, "proxy_enabled", True, raising=False)
    original = chat.ChatService.stream_completion

    def fake(self, model_id, messages, params=None, trace=None):
        if web_models.is_web(model_id):
            return original(self, model_id, messages, params, trace)
        return FakeService({"base-x": agent_script}).stream_completion(model_id, messages, params, trace)
    monkeypatch.setattr(chat.ChatService, "stream_completion", fake)
    client = TestClient(create_app())
    r = client.post("/api/admin/web-models/save", json={"base": "base-x", "settings": {"max_steps": 1, "tools": ["calculator"]}})
    assert r.status_code == 200 and r.json()["model"]["id"] == "base-x:web"
    ids = [m["id"] for m in client.get("/v1/models").json()["data"]]
    assert "base-x:web" in ids
    r = client.post("/v1/chat/completions", json={"model": "base-x:web", "stream": False,
                                                  "messages": [{"role": "user", "content": "Какая последняя версия Python?"}]})
    assert r.status_code == 200, r.text
    assert "3.14.1 [1]" in r.json()["choices"][0]["message"]["content"]
    with client.stream("POST", "/v1/messages", json={"model": "base-x:web", "max_tokens": 200, "stream": True,
                                                     "messages": [{"role": "user", "content": "Какая последняя версия Python?"}]}) as resp:
        body = "".join(resp.iter_text())
    assert resp.status_code == 200 and "content_block_delta" in body and "3.14.1" in body
    assert client.post("/api/admin/web-models/delete", json={"id": "base-x:web"}).json()["ok"]
    assert "base-x:web" not in [m["id"] for m in client.get("/v1/models").json()["data"]]

def test_web_models_ui_bilingual_and_hooked():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "/web-models.js" in html and "/proxy-r40.css" in html and 'id="wm-list"' in html
    assert html.index("/web-models.js") > html.index("/model_caps.js")
    assert "MemuWebModels.cardBlock(card)" in (STATIC / "model_caps.js").read_text(encoding="utf-8")
    src = (STATIC / "web-models.js").read_text(encoding="utf-8")
    pairs = re.findall(r'L\(\s*"((?:[^"\\]|\\.)*)"\s*,\s*"((?:[^"\\]|\\.)*)"\s*\)', src)
    pairs += re.findall(r'\[\s*"[^"]*"\s*,\s*"((?:[^"\\]|\\.)*)"\s*,\s*"((?:[^"\\]|\\.)*)"\s*\]', src)
    assert len(pairs) > 40
    assert not [en for _ru, en in pairs if re.search("[А-Яа-яЁё]", en)]
    assert not re.search(r"MemuAI(?! Proxy)", re.sub(r"/\*[\s\S]*?\*/", "", src))
    node = shutil.which("node")
    if node:
        for name in ("web-models.js", "composite.js", "app.js", "model_caps.js"):
            assert subprocess.run([node, "--check", str(STATIC / name)], capture_output=True).returncode == 0, name
