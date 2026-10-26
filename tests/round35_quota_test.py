"""Round 35 (quota): «Планировщик квот» — разбор источников, сбросы, прогноз, темп, маршрутизация, API, UI.

Запуск: NO_PROXY=127.0.0.1,localhost python -m pytest -q tests/round35_quota_test.py
Все часы детерминированы (quota_planner.set_clock / явный now), часовой пояс — UTC.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="memu-r35-quota-"))

from app import quota_planner as qp  # noqa: E402
from round35_quota_sim import simulate  # noqa: E402

H = 3600.0

NOON = 1791806400.0

@pytest.fixture(autouse=True)
def utc_tz(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    if hasattr(time, "tzset"):
        time.tzset()
    yield
    qp.set_clock(None)

def fresh(tmp_path, **settings) -> qp.Planner:
    p = qp.Planner(path=str(tmp_path / "qp.json"))
    p._loaded = True
    p.settings.update({"notify": False, **settings})
    return p

def row(remaining, reset, key="w", title="Окно — 5 часов", period=5 * H, limit=100, unit="%"):
    title_en = "Window — 5h" if title == "Окно — 5 часов" else title
    return {"key": key, "title": title, "title_en": title_en, "remaining": remaining, "reset": reset,
            "limit": limit, "unit": unit, "unit_en": unit, "period": period}

def linear(p, acc, *, start, now, rem0, rate, reset, step=600, key="w", period=5 * H, reqs_per_step=0):
    t, rem = start, rem0
    while t <= now + 1e-6:
        p.observe_rows(acc, [row(max(0.0, rem), reset, key=key, period=period)], ts=t)
        if reqs_per_step:
            p.note_request(acc, ts=t, count=reqs_per_step)
        rem -= rate * step / H
        t += step

def test_parse_helpers():
    assert qp.parse_duration("6m0s") == 360
    assert qp.parse_duration("1h2m3.5s") == pytest.approx(3723.5)
    assert qp.parse_duration("20ms") == pytest.approx(0.02)
    assert qp.parse_duration("59") == 59
    assert qp.parse_duration("") is None
    assert qp.parse_ts(1791806400000) == 1791806400.0
    assert qp.parse_ts("2026-10-12T12:00:00Z") == NOON
    assert qp.parse_ts(None) == 0.0
    assert qp.period_from_title("Gemini — 5 часов") == 5 * H
    assert qp.period_from_title("Claude / GPT — week") == qp.WEEK
    assert qp.period_from_title("Claude — за 7 дней (оценка)") is None

def test_rows_from_usage_antigravity_and_kiro():
    usage = {"rows": [
        {"display": "Gemini — 5 часов", "display_en": "Gemini — 5h", "used": 40, "limit": 100, "unit": "%", "reset": NOON + 2 * H},
        {"display": "Gemini — за 7 дней (оценка)", "used": 3.5, "limit": None, "unit": "окон по 5 ч"},
        {"display": "broken", "used": "x", "limit": 100},
    ], "next_reset": NOON + 3 * H}
    rows = qp.rows_from_usage(usage, NOON)
    assert len(rows) == 1
    assert rows[0]["remaining"] == pytest.approx(0.6)
    assert rows[0]["period"] == 5 * H and rows[0]["reset"] == NOON + 2 * H
    kiro = {"subscription": "Pro", "rows": [{"display": "Credits", "used": 250, "limit": 1000, "unit": "credits"}],
            "next_reset": NOON + 10 * 86400}
    rows = qp.rows_from_usage(kiro, NOON)
    assert rows[0]["remaining"] == pytest.approx(0.75) and rows[0]["reset"] == NOON + 10 * 86400
    assert rows[0]["period"] is None
    assert qp.rows_from_usage(None, NOON) == [] and qp.rows_from_usage({"loaded": []}, NOON) == []

def test_rows_from_headers_openai_anthropic_openrouter():
    openai = {"x-ratelimit-limit-requests": "14400", "x-ratelimit-remaining-requests": "14000",
              "x-ratelimit-reset-requests": "2h30m", "x-ratelimit-limit-tokens": "6000",
              "x-ratelimit-remaining-tokens": "5000", "x-ratelimit-reset-tokens": "7.66s"}
    rows = qp.rows_from_headers(openai, NOON, "ab12")
    assert len(rows) == 1
    assert rows[0]["remaining"] == pytest.approx(14000 / 14400)
    assert rows[0]["reset"] == pytest.approx(NOON + 2.5 * H)
    assert rows[0]["key"].startswith("hdr-") and "#ab12" in rows[0]["title"]
    anthropic = {"anthropic-ratelimit-tokens-limit": "1000000", "anthropic-ratelimit-tokens-remaining": "250000",
                 "anthropic-ratelimit-tokens-reset": "2026-10-12T15:00:00Z",
                 "anthropic-ratelimit-requests-limit": "50", "anthropic-ratelimit-requests-remaining": "49",
                 "anthropic-ratelimit-requests-reset": "2026-10-12T12:00:30Z"}
    rows = qp.rows_from_headers(anthropic, NOON)
    assert [r["unit_en"] for r in rows] == ["tokens"] and rows[0]["remaining"] == 0.25
    openrouter = {"X-RateLimit-Limit": "50", "X-RateLimit-Remaining": "12", "X-RateLimit-Reset": str(int((NOON + 9 * H) * 1000))}
    rows = qp.rows_from_headers(openrouter, NOON)
    assert rows[0]["remaining"] == pytest.approx(0.24) and rows[0]["reset"] == NOON + 9 * H
    assert qp.rows_from_headers({}, NOON) == [] and qp.rows_from_headers(None, NOON) == []

def test_observe_headers_never_stores_the_key(tmp_path, monkeypatch):
    p = fresh(tmp_path)
    monkeypatch.setattr(qp, "planner", p)
    secret = "sk-test-SECRET-1234567890"
    hdr = {"x-ratelimit-limit-requests": "100", "x-ratelimit-remaining-requests": "80", "x-ratelimit-reset-requests": "5h"}
    assert qp.observe_headers("acc1", hdr, secret, ts=NOON) == 1
    p.save(force=True)
    text = Path(p.path).read_text("utf-8")
    assert secret not in text and "SECRET" not in text and "1234567890" not in text

def test_reset_detection_and_merge():
    s = qp.new_series("a", row(1, NOON + 5 * H))
    assert not qp.add_point(s, NOON, 0.9, NOON + 5 * H)
    assert not qp.add_point(s, NOON + 600, 0.8, NOON + 5 * H)
    assert not qp.add_point(s, NOON + 630, 0.79, NOON + 5 * H)
    assert len(s["points"]) == 2 and s["points"][-1][1] == 0.79

    assert not qp.add_point(s, NOON + 1800, 0.81, NOON + 5 * H)

    assert qp.add_point(s, NOON + 5 * H + 120, 0.95, NOON + 10 * H)
    assert s["resets"][-1] == NOON + 5 * H and s["cycle_start"] == NOON + 5 * H + 120

    s2 = qp.new_series("a", row(1, 0, period=None))
    qp.add_point(s2, NOON, 0.2)
    assert qp.add_point(s2, NOON + 600, 0.9)

    assert not qp.add_point(s2, NOON, 0.1) and s2["points"][-1][1] == 0.9

def test_period_learned_from_resets():
    s = qp.new_series("a", row(1, 0, period=None))
    t = NOON
    for k in range(4):
        qp.add_point(s, t, 0.3)
        t += 3 * H
        qp.add_point(s, t, 1.0)
        t += 60 * 5
    assert s["period"] == pytest.approx(3 * H + 300, rel=0.01)

def test_steady_burn_runs_out_with_correct_eta(tmp_path):
    p = fresh(tmp_path)
    reset = NOON + 3 * H
    linear(p, "a", start=NOON - 2 * H, now=NOON, rem0=0.9, rate=0.2, reset=reset)
    fc = qp.forecast(p.series["a|w"], NOON)
    assert fc["status"] == "runs_out"
    assert fc["remaining"] == pytest.approx(0.5, abs=0.01)
    assert fc["eta"] - NOON == pytest.approx(2.5 * H, rel=0.08)
    assert fc["eta_early"] <= fc["eta"]
    assert fc["eta_late"] is None or fc["eta"] <= fc["eta_late"]
    assert fc["rem_at_reset"] == 0

    assert fc["pace_pct"] == pytest.approx((0.5 - qp.KEEP_RESERVE) / 3 * 100, rel=0.02)
    assert fc["projection"][0][1] == pytest.approx(0.5, abs=0.01)
    assert all(lo <= mid <= hi for _, mid, lo, hi in fc["projection"])

def test_light_burn_is_ok_and_mid_is_tight(tmp_path):
    p = fresh(tmp_path)
    linear(p, "ok", start=NOON - 2 * H, now=NOON, rem0=0.9, rate=0.05, reset=NOON + 3 * H)
    fc = qp.forecast(p.series["ok|w"], NOON)
    assert fc["status"] == "ok" and fc["eta"] is None
    assert fc["rem_at_reset"] == pytest.approx(0.65, abs=0.03)
    linear(p, "tight", start=NOON - 2 * H, now=NOON, rem0=0.9, rate=0.15, reset=NOON + 3 * H)
    fc = qp.forecast(p.series["tight|w"], NOON)
    assert fc["status"] == "tight"
    assert 0 < fc["rem_at_reset"] < 0.2

def test_insufficient_exhausted_and_no_reset(tmp_path):
    p = fresh(tmp_path)
    p.observe_rows("few", [row(0.7, NOON + H)], ts=NOON - 300)
    p.observe_rows("few", [row(0.69, NOON + H)], ts=NOON)
    fc = qp.forecast(p.series["few|w"], NOON)
    assert fc["status"] == "insufficient" and fc["remaining"] == 0.69 and fc["eta"] is None
    linear(p, "zero", start=NOON - H, now=NOON, rem0=0.2, rate=0.4, reset=NOON + 2 * H)
    assert qp.forecast(p.series["zero|w"], NOON)["status"] == "exhausted"
    linear(p, "nor", start=NOON - 2 * H, now=NOON, rem0=0.9, rate=0.1, reset=0, period=None)
    fc = qp.forecast(p.series["nor|w"], NOON)
    assert fc["status"] == "no_reset" and fc["reset"] is None
    assert fc["eta"] - NOON == pytest.approx(7 * H, rel=0.1)
    assert qp.forecast({"points": []}, NOON)["status"] == "insufficient"

def test_reset_estimated_from_period_when_provider_omits_it():
    s = qp.new_series("a", row(1, 0, period=5 * H))
    qp.add_point(s, NOON - 4.5 * H, 0.2, NOON - 4 * H)
    assert qp.add_point(s, NOON - 4 * H + 60, 1.0, 0)
    qp.add_point(s, NOON - 30 * 60, 0.8, 0)
    qp.add_point(s, NOON, 0.78, 0)
    fc = qp.forecast(s, NOON)
    assert fc["reset_estimated"] is True and fc["reset"] == pytest.approx(NOON + H)

def test_time_of_day_profile_beats_naive_ewma(tmp_path):
    """Busy evening, idle night: at 20:00 the naive rate says «runs out», the daily profile knows the night is quiet."""
    p = fresh(tmp_path)
    now = NOON + 8 * H
    day = 86400.0
    profile = lambda h: 1.0 if 9 <= h < 21 else 0.0   # noqa: E731
    simulate(p, "acc", "Пул — сутки", title_en="pool day", period=day, now=now, days=4, per_hour=0.07,
             seed=5, anchor=NOON - 3 * H, profile=profile, local_hour=lambda ts: time.gmtime(ts).tm_hour)
    s = p.series["acc|pool-day"]
    fc = qp.forecast(s, now)
    assert fc["reset"] == pytest.approx(NOON + 21 * H)
    naive_left = fc["remaining"] - (fc["rate"] or 0) * 13
    assert naive_left < 0
    assert fc["status"] in ("ok", "tight") and fc["eta"] is None
    assert fc["rem_at_reset"] > 0

def test_pace_in_requests_and_units(tmp_path):
    p = fresh(tmp_path)
    reset = NOON + 3 * H
    linear(p, "r", start=NOON - 3 * H, now=NOON, rem0=0.9, rate=0.1, reset=reset, reqs_per_step=5)
    fc = qp.forecast(p.series["r|w"], NOON, p.requests["r"])

    assert fc["rate_now"] == pytest.approx(30, rel=0.15)
    assert fc["pace_req"] == pytest.approx(57, rel=0.15)
    s = qp.new_series("u", row(1, reset, unit="tokens", limit=1_000_000))
    qp.add_point(s, NOON - 600, 0.5, reset)
    qp.add_point(s, NOON, 0.5, reset)
    fc = qp.forecast(s, NOON)
    assert fc["pace_units"] == pytest.approx((0.5 - 0.03) / 3 * 1_000_000, rel=0.01)

def test_requests_not_attributed_across_pools(tmp_path):
    p = fresh(tmp_path)
    reset = NOON + 3 * H
    linear(p, "ag", start=NOON - 3 * H, now=NOON, rem0=0.9, rate=0.1, reset=reset, key="g", reqs_per_step=5)
    for key, title in (("g", "Gemini — 5h"), ("c", "Claude / GPT — 5h")):
        p.series.setdefault(f"ag|{key}", qp.new_series("ag", row(1, reset, key=key, title=title)))["title_en"] = title
    linear(p, "ag", start=NOON - 3 * H, now=NOON, rem0=1.0, rate=0.0, reset=reset, key="c")
    assert p._requests_for("ag") is None
    view = p.view(NOON, [types.SimpleNamespace(id="ag", label="AG", provider="antigravity", enabled=True, priority="normal")])
    assert all(w["pace_req"] is None for w in view["accounts"][0]["windows"])

def _three(p):
    reset = NOON + 3 * H
    linear(p, "good", start=NOON - 2 * H, now=NOON, rem0=0.95, rate=0.02, reset=reset)
    linear(p, "tight", start=NOON - 2 * H, now=NOON, rem0=0.9, rate=0.15, reset=reset)
    linear(p, "bad", start=NOON - 2 * H, now=NOON, rem0=0.9, rate=0.3, reset=reset)

def test_score_rank_and_reorder(tmp_path, monkeypatch):
    p = fresh(tmp_path)
    qp.set_clock(lambda: NOON)
    monkeypatch.setattr(qp, "planner", p)
    _three(p)
    sg, st, sb = qp.score("good"), qp.score("tight"), qp.score("bad")
    assert 1 >= sg > st > sb >= 0
    assert qp.status("bad") == "runs_out" and qp.status("good") == "ok"
    assert qp.score("unknown") is None and qp.status("unknown") is None
    assert qp.rank(["bad", "unknown", "tight", "good"]) == ["good", "unknown", "tight", "bad"]
    A = lambda i, prio="normal": types.SimpleNamespace(id=i, priority=prio)   # noqa: E731
    pool = [A("bad"), A("tight"), A("x"), A("good"), A("bad2", "reserve"), A("good2", "reserve")]
    assert qp.reorder(pool) is pool
    p.settings["save_quota"] = True
    out = [a.id for a in qp.reorder(pool)]
    assert out == ["x", "good", "tight", "bad", "bad2", "good2"]

def test_pool_uses_reorder(tmp_path, monkeypatch):
    from app import pool as pool_mod
    p = fresh(tmp_path, save_quota=True)
    qp.set_clock(lambda: NOON)
    monkeypatch.setattr(qp, "planner", p)
    _three(p)
    A = lambda i: types.SimpleNamespace(id=i, priority="normal")   # noqa: E731
    monkeypatch.setattr(pool_mod.AccountPool, "_ordered_candidates",
                        lambda self, provider, model="", prefix="": [A("bad"), A("good"), A("tight")])
    assert [a.id for a in pool_mod.pool.ordered_candidates("custom")] == ["good", "tight", "bad"]

def test_persistence_roundtrip_and_forget(tmp_path):
    p = fresh(tmp_path, save_quota=True)
    linear(p, "a", start=NOON - H, now=NOON, rem0=0.9, rate=0.1, reset=NOON + H, reqs_per_step=2)
    p.save(force=True)
    data = json.loads(Path(p.path).read_text("utf-8"))
    assert data["version"] == 1 and "a|w" in data["series"]
    q = qp.Planner(path=p.path)
    q.load()
    assert q.settings["save_quota"] is True and len(q.series["a|w"]["points"]) == len(p.series["a|w"]["points"])
    assert sum(q.requests["a"].values()) == sum(p.requests["a"].values())
    q.forget_account("a")
    assert not q.series and "a" not in q.requests
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", "utf-8")
    r = qp.Planner(path=str(broken))
    r.load()
    assert r.series == {}

def test_inbox_notified_once_and_resolved_on_reset(tmp_path, monkeypatch):
    posted, resolved = [], []
    fake = types.ModuleType("app.inbox")
    fake.post = lambda kind, title, **kw: posted.append((kind, title, kw)) or "ib_1"
    fake.resolve = lambda key, **kw: resolved.append(key) or True
    monkeypatch.setitem(sys.modules, "app.inbox", fake)
    import app
    monkeypatch.setattr(app, "inbox", fake, raising=False)
    p = fresh(tmp_path, notify=True)
    monkeypatch.setattr(qp, "_account_label", lambda acc: ("Мой аккаунт", "antigravity"))
    monkeypatch.setattr(qp, "_ui_lang", lambda: "ru")
    reset = NOON + 3 * H
    linear(p, "a", start=NOON - 2 * H, now=NOON, rem0=0.9, rate=0.3, reset=reset)
    p.observe_rows("a", [row(0.28, reset)], ts=NOON + 300)
    assert len(posted) == 1
    kind, title, kw = posted[0]
    assert kind == "quota" and "закончится примерно" in title and kw["severity"] == "warn"
    assert kw["dedupe_key"] == "quota:a:w" and kw["source"] == "proxy"
    assert kw["actions"][0]["kind"] == "ui"
    assert re.search(r"[А-Яа-яЁё]", kw["meta"]["title_en"]) is None or "Мой аккаунт" in kw["meta"]["title_en"]

    p.observe_rows("a", [row(1.0, reset + 5 * H)], ts=reset + 60)
    assert resolved == ["quota:a:w"]

def test_api_routes(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import app.admin_api as admin_api
    monkeypatch.setattr(admin_api, "_auth", lambda request: None)
    p = fresh(tmp_path)
    monkeypatch.setattr(qp, "planner", p)
    qp.set_clock(lambda: NOON)
    _three(p)
    accs = [types.SimpleNamespace(id=i, label=i.title(), provider="custom", enabled=True, priority="normal")
            for i in ("good", "tight", "bad")]
    monkeypatch.setattr(qp, "_all_accounts", lambda: accs)
    api = FastAPI()
    qp.register(api)
    c = TestClient(api)
    v = c.get("/api/admin/quota/planner").json()
    assert [a["id"] for a in v["accounts"]] == ["bad", "tight", "good"]
    assert v["counts"]["runs_out"] == 1 and v["prefer"] == ["good"]
    w = v["accounts"][0]["windows"][0]
    assert w["history"] and w["projection"] and w["eta"]
    b = c.get("/api/admin/quota/planner/badges").json()
    assert b["badges"]["bad"]["status"] == "runs_out"
    r = c.post("/api/admin/quota/planner/settings", json={"save_quota": True, "poll_minutes": 1})
    assert r.json()["settings"]["save_quota"] is True and r.json()["settings"]["poll_minutes"] == 3
    assert c.post("/api/admin/quota/planner/settings", json={"poll_minutes": "x"}).status_code == 400
    assert c.post("/api/admin/quota/planner/settings", json=[1]).status_code == 400
    r = c.post("/api/admin/quota/planner/refresh").json()
    assert r["added"] == 0 and len(r["accounts"]) == 3

def test_static_wiring_and_english_without_cyrillic():
    html = (ROOT / "app/static/index.html").read_text("utf-8")
    assert 'data-tab="quota"' in html and 'id="tab-quota"' in html and 'id="quota-root"' in html
    assert html.index("quota-planner-i18n.js") < html.index("/app.js")
    assert html.index("/app.js") < html.index("quota-planner.js?")
    assert "quota-planner.css" in html
    js = (ROOT / "app/static/quota-planner.js").read_text("utf-8")

    pairs = re.findall(r'L\(\s*(`[^`]*`|"[^"]*")\s*,\s*(`[^`]*`|"[^"]*")\s*\)', js)
    assert len(pairs) > 40
    bad = [en for _, en in pairs if re.search(r"[А-Яа-яЁё]", en)]
    assert not bad, bad
    for st in ("ok", "tight", "runs_out", "exhausted", "insufficient", "no_reset"):
        assert f"{st}:" in js
    i18n = (ROOT / "app/static/quota-planner-i18n.js").read_text("utf-8")
    en_block = i18n[i18n.index("I18N.en"):]
    assert not re.search(r"[А-Яа-яЁё]", en_block)

def test_build_lists_module():
    assert '"app.quota_planner"' in (ROOT / "build.py").read_text("utf-8")
    for spec in ("MemuAI.spec", "MemuAIProxy.spec"):
        assert "'app.quota_planner'" in (ROOT / spec).read_text("utf-8")

def test_rows_from_headers_tooken_style_epoch_resets():
    """Real gateway headers (seen live): absolute epoch resets per window, minute windows skipped."""
    hdr = {"x-ratelimit-limit-requests": "90", "x-ratelimit-remaining-requests": "88",
           "x-ratelimit-limit-requests-hour": "2400", "x-ratelimit-remaining-requests-hour": "2397",
           "x-ratelimit-limit-requests-minute": "90", "x-ratelimit-remaining-requests-minute": "88",
           "x-ratelimit-limit-tokens-minute": "2400000", "x-ratelimit-remaining-tokens-minute": "2394969",
           "x-ratelimit-reset": str(int(NOON + 13)), "x-ratelimit-reset-requests-hour": str(int(NOON + 3300)),
           "x-ratelimit-reset-requests-minute": str(int(NOON + 13)), "x-ratelimit-reset-tokens-minute": str(int(NOON + 13)),
           "x-ratelimit-scope": "api_key", "x-ratelimit-idempotent-replay": "false"}
    rows = qp.rows_from_headers(hdr, NOON, "2dbe")
    assert len(rows) == 1
    r = rows[0]
    assert r["title"] == "Запросы в час · ключ #2dbe" and r["title_en"] == "Requests per hour · key #2dbe"
    assert r["period"] == qp.HOUR and r["reset"] == NOON + 3300 and r["remaining"] == pytest.approx(2397 / 2400)

def test_series_stays_compact_under_per_request_samples():
    s = qp.new_series("a", row(1, NOON + 30 * 86400, period=30 * 86400))
    t, rem = NOON, 1.0
    for i in range(3 * 24 * 60):
        rem -= 0.00005
        if i == 2000:
            rem = 1.0
        qp.add_point(s, t, rem, NOON + 30 * 86400)
        t += 61
    pts = s["points"]
    assert len(pts) < 900
    recent = [p for p in pts if p[0] >= t - qp.DENSE_SECONDS]
    assert len(recent) > 300
    assert any(b[1] > a[1] + 0.05 for a, b in zip(pts, pts[1:]))
    assert all(b[0] > a[0] for a, b in zip(pts, pts[1:]))

def test_inbox_message_in_english_for_english_ui(tmp_path, monkeypatch):
    posted = []
    fake = types.ModuleType("app.inbox")
    fake.post = lambda kind, title, **kw: posted.append((title, kw)) or "ib_1"
    fake.resolve = lambda key, **kw: True
    monkeypatch.setitem(sys.modules, "app.inbox", fake)
    import app
    monkeypatch.setattr(app, "inbox", fake, raising=False)
    monkeypatch.setattr(qp, "_account_label", lambda acc: ("Work", "kiro"))
    monkeypatch.setattr(qp, "_ui_lang", lambda: "en")
    p = fresh(tmp_path, notify=True)
    linear(p, "a", start=NOON - 2 * H, now=NOON, rem0=0.9, rate=0.3, reset=NOON + 3 * H)
    title, kw = posted[0]
    for text in (title, kw["body"], kw["actions"][0]["label"]):
        assert not re.search(r"[А-Яа-яЁё]", text), text

def test_js_syntax():
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    for name in ("quota-planner.js", "quota-planner-i18n.js"):
        r = subprocess.run([node, "--check", str(ROOT / "app/static" / name)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
