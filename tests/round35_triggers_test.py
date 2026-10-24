"""Round 35 (triggers): data & integration triggers of «Планирование» (app/schedule_triggers_ext.py).

Integrations (Gmail, Calendar, GitHub, Sheets, Drive, Notion), the web, panels, quota and Proxy models are
mocked; the engine path (normalize → tick → pending → decide → run meta → apply_trigger) is real.

Run: NO_PROXY=127.0.0.1,localhost python -m pytest -q tests/round35_triggers_test.py
Live (needs keys in env, skipped otherwise): see test_live_nl_parse_*.
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
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="memu-r35-triggers-"))
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")

from app import schedule_rules as rules  # noqa: E402
from app import schedule_triggers_ext as ext  # noqa: E402


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    from app import pgdata
    monkeypatch.setattr(pgdata, "MEMU_DIR", tmp_path / "memuai")
    (tmp_path / "memuai").mkdir(parents=True, exist_ok=True)
    ext._INFLIGHT.clear()
    ext._HOOK_HITS.clear()
    monkeypatch.setattr(ext, "service_status", lambda: {"gmail": True, "google_calendar": True, "github": True,
                                                        "google_sheets": True, "google_drive": True, "notion": True})
    yield tmp_path
    ext._INFLIGHT.clear()


def N(**raw):
    return ext.normalize({"type": "watch", **raw})


# --------------------------------------------------------------------------- normalization & text

ALL_VALID = [
    dict(source="gmail", query="from:bank.ru"),
    dict(source="calendar_before", minutes=10),
    dict(source="calendar_created", match="созвон"),
    dict(source="calendar_changed"),
    dict(source="github_issue", repo="octo/hello", labels="bug"),
    dict(source="github_pr", repo="https://github.com/octo/hello.git"),
    dict(source="github_review"),
    dict(source="github_actions", repo="octo/hello", branch="main", workflow="CI"),
    dict(source="github_release", repo="octo/hello"),
    dict(source="sheets_row", sheet="https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/edit#gid=0", tab="Заявки"),
    dict(source="sheets_cell", sheet="1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789", cell="Sheet1!B2", op=">", value="1 000,5"),
    dict(source="drive_file", folder="https://drive.google.com/drive/folders/1FolderIdXYZ123", name_has="счёт"),
    dict(source="notion_page", page="https://www.notion.so/My-page-0123456789abcdef0123456789abcdef"),
    dict(source="notion_db", database="0123456789abcdef0123456789abcdef", mode="changed"),
    dict(source="rss", url="habr.com/ru/rss/all/", match="python"),
    dict(source="web_page", url="https://example.com/p", selector="#price", mode="contains", text="В наличии"),
    dict(source="webhook"),
    dict(source="panel", panel="p_abcdef12", key="rate", op="gt", value=100, hysteresis=2),
    dict(source="quota_low", level="exhausted"),
    dict(source="proxy_model", match="claude"),
    dict(source="folder_file", path="%USERPROFILE%\\Downloads", pattern="*.pdf"),
    dict(source="system", metric="ram_pct", op="gt", value=90),
    dict(source="weather", metric="temp", op="lt", value=0, lat=55.75, lon=37.62, place="Moscow"),   # r37 plan-2
    dict(source="all", within=30, items=[dict(source="gmail", query="a"), dict(source="github_pr", repo="a/b")]),
]


def test_every_source_normalizes_and_reads_well():
    seen = set()
    for raw in ALL_VALID:
        trig = rules._normalize_trigger({"type": "watch", **raw})
        assert trig["type"] == "watch" and trig["source"] == raw["source"]
        seen.add(raw["source"])
        ru, en = rules.trigger_text(trig, "ru"), rules.trigger_text(trig, "en")
        assert ru and en and ru != en
        assert not re.search(r"[А-Яа-яЁё]", en.replace("счёт", "").replace("созвон", "").replace("Заявки", "")
                             .replace("В наличии", "")), en
    assert seen == set(ext.SOURCES), set(ext.SOURCES) - seen


def test_normalize_cleans_and_rejects():
    assert N(source="github_pr", repo="https://github.com/octo/hello.git")["repo"] == "octo/hello"
    assert N(source="sheets_row", sheet=ALL_VALID[9]["sheet"])["sheet"] == "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
    assert N(source="notion_page", page=ALL_VALID[12]["page"])["page"] == "01234567-89ab-cdef-0123-456789abcdef"
    assert N(source="drive_file", folder=ALL_VALID[11]["folder"])["folder"] == "1FolderIdXYZ123"
    assert N(source="rss", url="habr.com/rss")["url"] == "https://habr.com/rss"
    assert N(source="sheets_cell", sheet="1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789", cell="B2", op=">", value="1 000,5")["value"] == 1000.5
    tok = N(source="webhook")["token"]
    assert len(tok) >= 16 and N(source="webhook", token=tok)["token"] == tok
    hours = N(source="gmail", query="x", hours={"from": "9", "to": "18:00", "days": [1, 2, 3, 4, 5]})["hours"]
    assert hours == {"from": "09:00", "to": "18:00", "days": [1, 2, 3, 4, 5]}
    bad = [dict(source="nope"), dict(source="gmail"), dict(source="github_issue", repo="not a repo"),
           dict(source="sheets_cell", sheet="1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789", cell="zz"),
           dict(source="panel", panel="p_x"), dict(source="panel", panel="p_x", key="a", op="gt", value="abc"),
           dict(source="web_page", url="ftp://x"), dict(source="web_page", url="https://x.io", mode="contains"),
           dict(source="all", items=[dict(source="gmail", query="a")]),
           dict(source="all", items=[dict(source="webhook"), dict(source="gmail", query="a")]),
           dict(source="system", metric="cpu_pct", op="changed")]
    for raw in bad:
        with pytest.raises(rules.RuleError):
            N(**raw)


def test_rule_with_watch_is_event_only_and_keys_are_stable():
    rule = rules.normalize_rule({"when": [{"type": "watch", "source": "gmail", "query": "from:bank"}]})
    assert not rules.has_time_triggers(rule) and rules.has_event_triggers(rule)
    assert rules.preview(rule, time.time(), 3) == []
    assert rules.summary(rule, "ru").startswith("Когда придёт письмо")
    t = rule["when"][0]
    assert ext.watch_key("a", t) == ext.watch_key("a", dict(t, every=10, delay=3))   # poll options keep the cursor
    assert ext.watch_key("a", t) != ext.watch_key("a", dict(t, query="from:other"))


def test_parse_number():
    cases = {"1 234,5 ₽": 1234.5, "-3.2%": -3.2, "92,15": 92.15, "1,234.5": 1234.5, "1.234,5": 1234.5, 42: 42.0,
             "−7": -7.0, "abc": None, "": None, None: None}
    for raw, want in cases.items():
        assert ext.parse_number(raw) == want, raw


# --------------------------------------------------------------------------- comparison & hysteresis

def test_threshold_hysteresis_fires_once_per_crossing():
    cfg = {"op": "gt", "value": 100.0, "hysteresis": 5.0, "initial": True}
    cur, fires = None, []
    for v in [99, 101, 103, 97, 99, 94, 101, 102]:
        fire, cur = ext.threshold_step(cfg, v, cur)
        fires.append(fire)
    assert fires == [False, True, False, False, False, False, True, False]
    # initial = true: already above → fires on the first look; false → waits for a fresh crossing
    assert ext.threshold_step(cfg, 150, None)[0] is True
    cfg2 = dict(cfg, initial=False)
    fire, cur = ext.threshold_step(cfg2, 150, None)
    assert fire is False
    fire, cur = ext.threshold_step(cfg2, 160, cur)
    assert fire is False
    fire, cur = ext.threshold_step(cfg2, 90, cur)
    fire, cur = ext.threshold_step(cfg2, 120, cur)
    assert fire is True
    # «below» with hysteresis
    low = {"op": "lt", "value": 10.0, "hysteresis": 2.0, "initial": True}
    cur, out = None, []
    for v in [15, 9, 8, 11, 13, 9]:
        f, cur = ext.threshold_step(low, v, cur)
        out.append(f)
    assert out == [False, True, False, False, False, True]
    # «changed» never fires on the baseline; a hysteresis ignores tiny moves
    ch = {"op": "changed", "hysteresis": 1.0}
    f, cur = ext.threshold_step(ch, "10", None)
    assert f is False
    f, cur = ext.threshold_step(ch, "10.4", cur)
    assert f is False
    f, cur = ext.threshold_step(ch, "11.2", cur)
    assert f is True
    # text operators
    t = {"op": "contains", "value": "в наличии", "initial": True}
    f, cur = ext.threshold_step(t, "Нет в продаже", None)
    assert f is False
    f, cur = ext.threshold_step(t, "Товар В наличии", cur)
    assert f is True
    f, cur = ext.threshold_step(t, "Товар В наличии!", cur)
    assert f is False


# --------------------------------------------------------------------------- fake integrations

class FakeGoogle:
    def __init__(self):
        self.messages = []          # [(id, subject, from)]
        self.events = []
        self.sheet = [["Имя", "Email"], ["Аня", "a@x.ru"]]
        self.cell = "5"
        self.files = []
        self.calls = []

    def __call__(self, service, method, url, **kw):
        self.calls.append((service, url, kw.get("params")))
        if service == "gmail" and url.endswith("/messages"):
            return {"messages": [{"id": m[0]} for m in reversed(self.messages)]}
        if service == "gmail" and "/messages/" in url:
            mid = url.rsplit("/", 1)[1]
            m = next(x for x in self.messages if x[0] == mid)
            return {"id": mid, "threadId": "t" + mid, "snippet": "текст " + mid,
                    "payload": {"headers": [{"name": "Subject", "value": m[1]}, {"name": "From", "value": m[2]}]}}
        if service == "google_calendar":
            return {"items": list(self.events)}
        if service == "google_sheets" and "/values/" in url:
            if "B2" in url:
                return {"values": [[self.cell]]}
            return {"values": self.sheet}
        if service == "google_drive":
            return {"files": list(self.files)}
        raise AssertionError(url)


def test_gmail_baseline_then_only_new(monkeypatch):
    g = FakeGoogle()
    monkeypatch.setattr(ext, "google", g)
    g.messages = [("m1", "Старое", "bank@x.ru")]
    cfg = N(source="gmail", query="from:bank")
    r = ext.poll_source(cfg, None, "ru")
    assert r.events == [] and r.info["preview"][0]["title"] == "Старое"
    assert "newer_than:3d" in g.calls[0][2]["q"]
    r2 = ext.poll_source(cfg, r.cursor, "ru")
    assert r2.events == []
    g.messages += [("m2", "Выписка", "Банк <bank@x.ru>"), ("m3", "Код", "bank@x.ru")]
    r3 = ext.poll_source(cfg, r2.cursor, "ru")
    assert len(r3.events) == 1 and r3.events[0]["count"] == 2
    assert r3.events[0]["title"].startswith("Выписка") and "ещё 1" in r3.events[0]["title"]
    assert r3.events[0]["from"] == "Банк <bank@x.ru>" and r3.events[0]["url"].endswith("m2")
    assert ext.poll_source(cfg, r3.cursor, "ru").events == []        # no duplicates
    each = N(source="gmail", query="from:bank", each=True)
    g.messages += [("m4", "A", "b"), ("m5", "B", "b")]
    r4 = ext.poll_source(each, r3.cursor, "en")
    assert [e["title"] for e in r4.events] == ["A", "B"]


def _ev(eid, start_ts, title="Встреча", **extra):
    iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(start_ts))
    return {"id": eid, "summary": title, "start": {"dateTime": iso}, "end": {"dateTime": iso},
            "htmlLink": "https://cal/" + eid, "updated": extra.pop("updated", "2026-01-01T00:00:00Z"),
            "created": extra.pop("created", "2026-01-01T00:00:00Z"), **extra}


def test_calendar_minutes_before_fires_once_with_hint(monkeypatch):
    g = FakeGoogle()
    monkeypatch.setattr(ext, "google", g)
    monkeypatch.setattr(ext, "_ig", lambda: type("IG", (), {"CALENDAR_API": "https://cal", "DRIVE_API": "https://drive"}))
    now = time.time()
    g.events = [_ev("e1", now + 30 * 60, "Созвон", hangoutLink="https://meet/x"), _ev("e2", now + 9 * 60, "Скоро")]
    cfg = N(source="calendar_before", minutes=10)
    r = ext.poll_source(cfg, None, "ru", now)
    assert [e["title"] for e in r.events] == ["Скоро"]
    assert r.events[0]["minutes_left"] == 9 and r.events[0]["source"] == "calendar_before"
    assert r.next_check and r.next_check <= now + 10 * 60 + 1          # the list refresh (every 10 min) or the next mark
    r2 = ext.poll_source(cfg, r.cursor, "ru", now + 60)
    assert r2.events == []                               # cached list, no refetch, no duplicate
    calls = len(g.calls)
    r3 = ext.poll_source(cfg, r2.cursor, "ru", now + 20 * 60 + 5)
    assert [e["title"] for e in r3.events] == ["Созвон"] and r3.events[0]["meet"] == "https://meet/x"
    assert len(g.calls) >= calls


def test_calendar_created_and_changed(monkeypatch):
    g = FakeGoogle()
    monkeypatch.setattr(ext, "google", g)
    monkeypatch.setattr(ext, "_ig", lambda: type("IG", (), {"CALENDAR_API": "https://cal"}))
    now = time.time()
    created = N(source="calendar_created")
    changed = N(source="calendar_changed")
    rc = ext.poll_source(created, None, "ru", now)
    rh = ext.poll_source(changed, None, "ru", now)
    later = now + 120
    iso = lambda ts: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))  # noqa: E731
    g.events = [_ev("new", now + 3600, "Новая", created=iso(now + 60), updated=iso(now + 60)),
                _ev("old", now + 7200, "Перенесли", created=iso(now - 86400), updated=iso(now + 90))]
    rc2 = ext.poll_source(created, rc.cursor, "ru", later)
    rh2 = ext.poll_source(changed, rh.cursor, "ru", later)
    assert [e["title"] for e in rc2.events] == ["Новая"]
    assert [e["title"] for e in rh2.events] == ["Перенесли"]
    assert ext.poll_source(changed, rh2.cursor, "ru", later + 60).events == []


class FakeGitHub:
    def __init__(self):
        self.issues, self.pulls, self.runs, self.releases, self.reviews = [], [], [], [], []

    def __call__(self, method, path, **kw):
        if path == "/search/issues":
            assert "review-requested:@me" in kw["params"]["q"]
            return {"items": list(self.reviews)}
        if path.endswith("/issues"):
            return list(self.issues)
        if path.endswith("/pulls"):
            return list(self.pulls)
        if path.endswith("/actions/runs"):
            return {"workflow_runs": list(self.runs)}
        if path.endswith("/releases"):
            return list(self.releases)
        if path == "/search/issues":
            assert "review-requested:@me" in kw["params"]["q"]
            return {"items": list(self.reviews)}
        raise AssertionError(path)


def test_github_sources(monkeypatch):
    gh = FakeGitHub()
    monkeypatch.setattr(ext, "github", gh)
    gh.issues = [{"number": 5, "title": "old", "user": {"login": "a"}}, {"number": 6, "title": "PR", "pull_request": {}}]
    cfg = N(source="github_issue", repo="o/r")
    r = ext.poll_source(cfg, None)
    assert r.events == []
    gh.issues.insert(0, {"number": 7, "title": "Падает сборка", "user": {"login": "bob"}, "html_url": "https://gh/7",
                         "labels": [{"name": "bug"}], "body": "stack"})
    r2 = ext.poll_source(cfg, r.cursor)
    assert r2.events[0]["number"] == 7 and r2.events[0]["author"] == "bob" and r2.events[0]["labels"] == ["bug"]
    assert ext.poll_source(cfg, r2.cursor).events == []
    # Actions: only failures newer than the baseline
    run = lambda i, c, name="CI": {"id": i, "name": name, "head_branch": "main", "conclusion": c, "html_url": f"https://gh/run/{i}",  # noqa: E731
                                   "head_commit": {"message": f"commit {i}"}, "actor": {"login": "bob"}, "head_sha": "abc"}
    gh.runs = [run(10, "failure")]
    acfg = N(source="github_actions", repo="o/r", workflow="ci")
    ra = ext.poll_source(acfg, None)
    assert ra.events == []
    gh.runs = [run(12, "failure"), run(11, "success"), run(13, "failure", "Deploy"), run(10, "failure")]
    ra2 = ext.poll_source(acfg, ra.cursor)
    assert [e["id"] for e in ra2.events] == ["12"] and ra2.events[0]["commit"] == "commit 12"
    # releases, PR, review
    gh.releases = [{"id": 1, "tag_name": "v1"}]
    rr = ext.poll_source(N(source="github_release", repo="o/r"), None)
    gh.releases = [{"id": 2, "tag_name": "v2", "name": "Версия 2", "body": "notes"}, {"id": 3, "tag_name": "v3", "draft": True}, {"id": 1, "tag_name": "v1"}]
    assert [e["tag"] for e in ext.poll_source(N(source="github_release", repo="o/r"), rr.cursor).events] == ["v2"]
    gh.reviews = [{"id": 100, "number": 1, "title": "x", "repository_url": "https://api/repos/o/r"}]
    rv = ext.poll_source(N(source="github_review"), None)
    gh.reviews.insert(0, {"id": 101, "number": 2, "title": "Посмотри", "repository_url": "https://api/repos/o/r", "html_url": "u"})
    ev = ext.poll_source(N(source="github_review"), rv.cursor).events
    assert ev[0]["title"] == "Посмотри" and ev[0]["repo"] == "o/r"


def test_sheets_rows_cells_and_drive(monkeypatch):
    g = FakeGoogle()
    monkeypatch.setattr(ext, "google", g)
    monkeypatch.setattr(ext, "_ig", lambda: type("IG", (), {"DRIVE_API": "https://drive"}))
    sid = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
    rows = N(source="sheets_row", sheet=sid)
    r = ext.poll_source(rows, None)
    assert r.events == [] and r.cursor["rows"] == 2
    g.sheet = g.sheet + [["Боря", "b@x.ru"], [], ["", ""]]
    r2 = ext.poll_source(rows, r.cursor)
    assert r2.events[0]["fields"] == {"Имя": "Боря", "Email": "b@x.ru"} and r2.events[0]["row"] == 3
    assert ext.poll_source(rows, r2.cursor).events == []
    cell = N(source="sheets_cell", sheet=sid, cell="B2", op="gt", value=10)
    rc = ext.poll_source(cell, None)
    assert rc.events == []
    g.cell = "12,5"
    rc2 = ext.poll_source(cell, rc.cursor)
    assert rc2.events and rc2.events[0]["value"] == "12,5" and rc2.events[0]["previous"] == "5"
    # Drive
    drive = N(source="drive_file", folder="1FolderIdXYZ123")
    rd = ext.poll_source(drive, None, now=time.time())
    g.files = [{"id": "f1", "name": "Счёт.pdf", "createdTime": "2030-01-01T00:00:00Z", "webViewLink": "https://d/f1"}]
    rd2 = ext.poll_source(drive, rd.cursor, now=time.time() + 60)
    assert rd2.events[0]["title"] == "Счёт.pdf"
    q = g.calls[-1][2]["q"]
    assert "'1FolderIdXYZ123' in parents" in q and "createdTime >" in q
    assert ext.poll_source(drive, rd2.cursor, now=time.time() + 120).events == []


def test_notion_page_and_database(monkeypatch):
    state = {"edited": "2026-10-01T10:00:00.000Z", "rows": []}

    def fake(method, path, **kw):
        if path.startswith("/pages/"):
            return {"last_edited_time": state["edited"], "url": "https://notion/p",
                    "properties": {"title": {"type": "title", "title": [{"plain_text": "План"}]}}}
        return {"results": list(state["rows"])}

    monkeypatch.setattr(ext, "notion", fake)
    page = N(source="notion_page", page="0123456789abcdef0123456789abcdef")
    r = ext.poll_source(page, None)
    assert r.events == []
    state["edited"] = "2026-10-01T11:00:00.000Z"
    r2 = ext.poll_source(page, r.cursor)
    assert r2.events and r2.events[0]["url"] == "https://notion/p"
    db = N(source="notion_db", database="0123456789abcdef0123456789abcdef", mode="new")
    now = time.time()
    rd = ext.poll_source(db, None, now=now)
    iso = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(now + 30))
    state["rows"] = [{"id": "r1", "created_time": iso, "last_edited_time": iso, "url": "https://notion/r1",
                      "properties": {"Name": {"type": "title", "title": [{"plain_text": "Задача"}]}}}]
    rd2 = ext.poll_source(db, rd.cursor, now=now + 60)
    assert [e["title"] for e in rd2.events] == ["Задача"]
    assert ext.poll_source(db, rd2.cursor, now=now + 120).events == []


RSS = """<?xml version="1.0"?><rss><channel><title>T</title>
<item><title>Первая</title><link>https://x/1</link><guid>1</guid><description>&lt;b&gt;о python&lt;/b&gt;</description></item>
{extra}</channel></rss>"""
PAGE = """<html><body><div id="price"><span class="v">1 000 ₽</span></div><ul class="stock"><li>Нет в наличии</li></ul>
<script>var x = 1;</script></body></html>"""


def test_rss_and_web_page(monkeypatch):
    pages = {"https://x/feed": RSS.format(extra=""), "https://shop/p": PAGE}
    monkeypatch.setattr(ext, "http_get", lambda url, **kw: (pages[url], url))
    feed = N(source="rss", url="https://x/feed", match="python")
    r = ext.poll_source(feed, None)
    assert r.events == [] and r.info["preview"][0]["title"] == "Первая"
    pages["https://x/feed"] = RSS.format(extra="<item><title>Вторая про Python</title><link>https://x/2</link><guid>2</guid></item>"
                                         "<item><title>Без слова</title><guid>3</guid></item>")
    r2 = ext.poll_source(feed, r.cursor)
    assert [e["title"] for e in r2.events] == ["Вторая про Python"]
    # selector diff
    web = N(source="web_page", url="https://shop/p", selector="#price .v")
    w = ext.poll_source(web, None)
    assert w.events == []
    pages["https://shop/p"] = PAGE.replace("1 000 ₽", "900 ₽")
    w2 = ext.poll_source(web, w.cursor)
    assert w2.events and "900 ₽" in w2.events[0]["added"] and "1 000 ₽" in w2.events[0]["removed"]
    assert ext.poll_source(web, w2.cursor).events == []
    # text appears
    stock = N(source="web_page", url="https://shop/p", selector="ul.stock > li", mode="contains", text="Есть в наличии")
    s1 = ext.poll_source(stock, None)
    assert s1.events == []
    pages["https://shop/p"] = PAGE.replace("Нет в наличии", "Есть в наличии: 3 шт.")
    s2 = ext.poll_source(stock, s1.cursor)
    assert s2.events and s2.events[0]["source"] == "web_page" and "Есть в наличии" in s2.events[0]["title"]
    assert ext.poll_source(stock, s2.cursor).events == []
    # a selector that matches nothing is a config error the UI shows
    with pytest.raises(ext.SourceError):
        ext.poll_source(N(source="web_page", url="https://shop/p", selector="#missing"), None)


def test_css_select_subset():
    html = '<div class="a b" id="x"><p>one</p><section><p data-k="1">two</p></section></div><p>three</p>'
    assert ext.select(html, "#x p") == ["one", "two"]
    assert ext.select(html, "div.a > p") == ["one"]
    assert ext.select(html, "p[data-k=1]") == ["two"]
    assert ext.select(html, "p") == ["one", "two", "three"]


def test_public_host_guard():
    with pytest.raises(ext.SourceError):
        ext._public_host("127.0.0.1")
    with pytest.raises(ext.SourceError):
        ext._public_host("192.168.1.1")


def test_panel_quota_models_system(monkeypatch):
    panels = {"p_abcdef12": {"id": "p_abcdef12", "title": "Курс USD", "data": {"rate": "92,5 ₽", "trend": "up"},
                             "last_refresh": time.time()}}
    monkeypatch.setattr(ext, "_panels_by_id", lambda: panels)
    cfg = N(source="panel", panel="p_abcdef12", key="rate", op="gt", value=95, hysteresis=1)
    r = ext.poll_source(cfg, None)
    assert r.events == [] and r.info["value"] == "92,5 ₽"
    panels["p_abcdef12"]["data"]["rate"] = "95,3 ₽"
    r2 = ext.poll_source(cfg, r.cursor)
    assert r2.events and r2.events[0]["value"] == "95,3 ₽" and r2.events[0]["previous"] == "92,5 ₽"
    panels["p_abcdef12"]["data"]["rate"] = "94,5"
    r3 = ext.poll_source(cfg, r2.cursor)
    panels["p_abcdef12"]["data"]["rate"] = "96"
    assert ext.poll_source(cfg, r3.cursor).events == []          # 94.5 > 95 - 1 → not re-armed
    assert ext.panel_fields(panels["p_abcdef12"])[0]["key"] == "rate"
    with pytest.raises(ext.SourceError):
        ext.poll_source(N(source="panel", panel="p_gone0000", key="rate", op="changed"), None)
    # quota: fires per account once until it is ok again
    accounts = [{"id": "a1", "name": "Claude", "status": "ok", "score": 0.9}]
    monkeypatch.setattr(ext, "_quota_accounts", lambda: accounts)
    q = N(source="quota_low", level="runs_out")
    rq = ext.poll_source(q, None)
    assert rq.events == []
    accounts[0]["status"] = "runs_out"
    rq2 = ext.poll_source(q, rq.cursor)
    assert rq2.events and rq2.events[0]["account"] == "a1"
    assert ext.poll_source(q, rq2.cursor).events == []
    # models
    models = ["a", "claude-4"]
    monkeypatch.setattr(ext, "proxy_models", lambda: list(models))
    m = N(source="proxy_model", match="claude")
    rm = ext.poll_source(m, None)
    models.append("claude-5")
    rm2 = ext.poll_source(m, rm.cursor)
    assert rm2.events[0]["models"] == ["claude-5"]
    assert ext.poll_source(m, rm2.cursor).events == []
    # system metric
    values = iter([50.0, 95.0, 96.0])
    monkeypatch.setattr(ext, "system_metric", lambda metric, drive="": next(values))
    s = N(source="system", metric="ram_pct", op="gt", value=90)
    a = ext.poll_source(s, None)
    b = ext.poll_source(s, a.cursor)
    c = ext.poll_source(s, b.cursor)
    assert (a.events, bool(b.events), c.events) == ([], True, [])


def test_folder_new_file_waits_until_written(tmp_path):
    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "old.pdf").write_text("x")
    cfg = N(source="folder_file", path=str(folder), pattern="*.pdf")
    now = time.time()
    r = ext.poll_source(cfg, None, now=now)
    assert r.events == []
    new = folder / "new.pdf"
    new.write_text("abc")
    os.utime(new, (now - 10, now - 10))
    (folder / "skip.txt").write_text("x")
    r2 = ext.poll_source(cfg, r.cursor, now=now + 60)
    assert r2.events == [] and r2.next_check          # seen once — wait for a stable size
    r3 = ext.poll_source(cfg, r2.cursor, now=now + 65)
    assert [e["title"] for e in r3.events] == ["new.pdf"] and r3.events[0]["path"].endswith("new.pdf")
    assert ext.poll_source(cfg, r3.cursor, now=now + 70).events == []
    with pytest.raises(ext.SourceError):
        ext.poll_source(N(source="folder_file", path=str(tmp_path / "missing")), None)


def test_compound_all_within_window(monkeypatch):
    feed = {"https://a/feed": RSS.format(extra=""), "https://b/feed": RSS.format(extra="")}
    monkeypatch.setattr(ext, "http_get", lambda url, **kw: (feed[url], url))
    cfg = N(source="all", within=10, items=[dict(source="rss", url="https://a/feed", every=5), dict(source="rss", url="https://b/feed", every=5)])
    now = time.time()
    r = ext.poll_source(cfg, None, now=now)
    assert r.events == []
    feed["https://a/feed"] = RSS.format(extra="<item><title>A2</title><guid>a2</guid></item>")
    r2 = ext.poll_source(cfg, r.cursor, now=now + 301)
    assert r2.events == [] and "1" in r2.info["summary"]
    feed["https://b/feed"] = RSS.format(extra="<item><title>B2</title><guid>b2</guid></item>")
    r3 = ext.poll_source(cfg, r2.cursor, now=now + 602)
    assert r3.events and r3.events[0]["title"] == "A2 + B2" and len(r3.events[0]["parts"]) == 2
    # outside the window the first hit expires
    feed["https://a/feed"] = RSS.format(extra="<item><title>A3</title><guid>a3</guid></item>")
    r4 = ext.poll_source(cfg, r3.cursor, now=now + 903)
    feed["https://b/feed"] = RSS.format(extra="<item><title>B3</title><guid>b3</guid></item>")
    r5 = ext.poll_source(cfg, r4.cursor, now=now + 903 + 20 * 60)
    assert r5.events == []


# --------------------------------------------------------------------------- tick → decide → run (real engine)

def _save_tasks(tasks):
    from app import pgdata
    pgdata._mutate_schedules(lambda rows: rows.extend(tasks) or True)


def _tick(tasks, now):
    return asyncio.run(ext.tick(tasks, now, "ru"))


def test_tick_pending_take_decide_and_no_duplicates_after_restart(monkeypatch):
    g = FakeGoogle()
    monkeypatch.setattr(ext, "google", g)
    g.messages = [("m1", "Старое", "bank")]
    task = {"id": "t1", "name": "Банк", "kind": "chat", "prompt": "Перескажи письмо", "enabled": True,
            "rule": rules.normalize_rule({"when": [{"type": "watch", "source": "gmail", "query": "from:bank"}]})}
    now = time.time()
    assert _tick([task], now) == {}                                   # baseline
    state = ext.load_state()["watches"]
    key = ext.watch_key("t1", task["rule"]["when"][0])
    assert state[key]["cursor"]["seen"] == ["m1"] and state[key]["next_check"] > now
    assert _tick([task], now + 10) == {}                              # not due yet: no poll
    assert len([c for c in g.calls if c[1].endswith("/messages")]) == 1
    g.messages.append(("m2", "Выписка за октябрь", "bank"))
    pending = _tick([task], now + 200)
    assert list(pending["t1"]) == [key] and pending["t1"][key][0]["payload"]["title"] == "Выписка за октябрь"
    d = rules.decide(task, now + 200, {"watch": pending}, None)
    assert d["run"] and d["trigger"] == "event" and d["payload"]["title"] == "Выписка за октябрь"
    assert task["rule_state"]["watch_seq"][key] == 1
    # the same pending event is not taken twice
    assert not rules.decide(task, now + 201, {"watch": pending}, None)["run"]
    # restart: in-memory state gone, the file keeps cursor and consumed events are pruned
    ext._INFLIGHT.clear()
    pending2 = _tick([task], now + 400)
    assert pending2 == {}
    assert ext.load_state()["watches"][key]["pending"] == []
    # disabling the task forgets the cursor → a re-enabled task starts with a fresh baseline
    _tick([dict(task, enabled=False)], now + 500)
    assert key not in ext.load_state()["watches"]


def test_tick_errors_backoff_and_inbox(monkeypatch):
    posted, resolved = [], []

    class FakeInbox:
        @staticmethod
        def post(kind, title, body="", **kw):
            posted.append((kind, title, kw.get("dedupe_key")))
            return "id"

        @staticmethod
        def resolve(key, *a, **k):
            resolved.append(key)
            return True

    monkeypatch.setitem(sys.modules, "app.inbox", FakeInbox)
    import app
    monkeypatch.setattr(app, "inbox", FakeInbox, raising=False)
    fail = {"on": True}

    def broken(method, path, **kw):
        if fail["on"]:
            raise ext.SourceError("GitHub ответил 502", "network", "github")
        return []

    monkeypatch.setattr(ext, "github", broken)
    task = {"id": "t2", "name": "CI", "kind": "notify", "prompt": "x", "enabled": True,
            "rule": rules.normalize_rule({"when": [{"type": "watch", "source": "github_actions", "repo": "o/r"}]})}
    key = ext.watch_key("t2", task["rule"]["when"][0])
    now = time.time()
    gaps = []
    for _ in range(3):
        _tick([task], now)
        entry = ext.load_state()["watches"][key]
        gaps.append(entry["next_check"] - now)
        now = entry["next_check"] + 1
    assert gaps[0] < gaps[1] < gaps[2] and entry["fails"] == 3 and entry["error_kind"] == "network"
    assert posted and posted[0][2] == "trigger:" + key and "CI" in posted[0][1]
    fail["on"] = False
    _tick([task], now)
    entry = ext.load_state()["watches"][key]
    assert entry["fails"] == 0 and "error" not in entry and resolved == ["trigger:" + key]
    # health for the UI
    _save_tasks([task])
    h = ext.health("ru")["t2"][0]
    assert h["source"] == "github_actions" and h["fails"] == 0 and h["last_check"]


def test_connect_error_is_reported_at_once(monkeypatch):
    posted = []

    class FakeInbox:
        post = staticmethod(lambda kind, title, body="", **kw: posted.append(title))
        resolve = staticmethod(lambda *a, **k: True)

    monkeypatch.setitem(sys.modules, "app.inbox", FakeInbox)
    import app
    monkeypatch.setattr(app, "inbox", FakeInbox, raising=False)

    def off(*a, **k):
        raise ext.SourceError("Gmail не подключён", "connect", "gmail")

    monkeypatch.setattr(ext, "google", off)
    task = {"id": "t3", "name": "Почта", "kind": "notify", "prompt": "x", "enabled": True,
            "rule": rules.normalize_rule({"when": [{"type": "watch", "source": "gmail", "query": "x"}]})}
    now = time.time()
    _tick([task], now)
    entry = next(iter(ext.load_state()["watches"].values()))
    assert entry["error_kind"] == "connect" and entry["next_check"] - now >= 15 * 60
    assert posted and "Gmail" in posted[0]


def test_hours_window_skips_events(monkeypatch):
    feed = {"u": RSS.format(extra="")}
    monkeypatch.setattr(ext, "http_get", lambda url, **kw: (feed["u"], url))
    trig = {"type": "watch", "source": "rss", "url": "https://u", "hours": {"from": "00:00", "to": "00:01"}}
    monkeypatch.setattr(ext, "http_get", lambda url, **kw: (feed["u"], url))
    task = {"id": "t4", "name": "RSS", "kind": "notify", "prompt": "x", "enabled": True,
            "rule": rules.normalize_rule({"when": [trig]})}
    noon = time.mktime(time.strptime(time.strftime("%Y-%m-%d") + " 12:00", "%Y-%m-%d %H:%M"))
    _tick([task], noon)
    feed["u"] = RSS.format(extra="<item><title>N</title><guid>n</guid></item>")
    pending = _tick([task], noon + 3600)
    assert pending == {}
    assert next(iter(ext.load_state()["watches"].values()))["skipped"] == 1


def test_scheduler_claims_with_payload_and_prompt_gets_context(monkeypatch):
    from app import scheduler, schedule_runtime
    g = FakeGoogle()
    monkeypatch.setattr(ext, "google", g)
    g.messages = [("m1", "Старое", "bank")]
    task = {"id": "t5", "name": "Банк", "kind": "chat", "prompt": "Кратко перескажи", "enabled": True,
            "rule": {"when": [{"type": "watch", "source": "gmail", "query": "from:bank"}]}}
    _save_tasks([task])
    started = []
    monkeypatch.setattr(scheduler, "_start_claimed_task", lambda t, tid, ts: started.append(t))
    monkeypatch.setattr(schedule_runtime.SAMPLER, "get", lambda needs, now=None: (None, {}))
    now = time.time()
    asyncio.run(scheduler._scheduler_tick(now=now))
    assert started == []
    g.messages.append(("m2", "Выписка", "Банк"))
    from app import pgdata
    key = next(iter(ext.load_state()["watches"]))
    ext.mutate_state(lambda w: w[key].update(next_check=0))
    asyncio.run(scheduler._scheduler_tick(now=now + 5))
    if not started:      # the event is taken on the tick after the poll
        asyncio.run(scheduler._scheduler_tick(now=now + 10))
    assert len(started) == 1
    meta = started[0]["_run_meta"]
    assert meta["trigger"] == "event" and meta["payload"]["title"] == "Выписка"
    applied = ext.apply_trigger(started[0])
    assert applied["trigger"]["from"] == "Банк" and "Выписка" in applied["prompt"] and applied["prompt"].startswith("Кратко перескажи")
    stored = next(t for t in pgdata.load_schedules() if t["id"] == "t5")
    assert stored["rule_state"]["last_trigger"]["title"] == "Выписка"
    # placeholders instead of the context block; a manual test run gets nothing
    t = dict(started[0], kind="notify", prompt="Письмо: {{trigger.title}} от {{trigger.from}}")
    assert ext.apply_trigger(t)["prompt"] == "Письмо: Выписка от Банк"
    manual = dict(started[0], _run_meta={"trigger": "manual", "test": True})
    assert ext.apply_trigger(manual)["prompt"] == "Кратко перескажи"


def test_edit_keeps_consumed_marks(monkeypatch):
    task = {"id": "t6", "rule": rules.normalize_rule({"when": [{"type": "watch", "source": "gmail", "query": "x"}]}),
            "rule_state": {"watch_seq": {"k": 3}, "status": "waiting", "anchor": 1}}
    rules.reset_plan(task)
    assert task["rule_state"] == {"watch_seq": {"k": 3}}


def test_payload_clip_and_context_text():
    big = {"source": "gmail", "title": "T", "text": "x" * 20000, "items": [{"title": str(i)} for i in range(200)]}
    small = ext._clip(big)
    assert len(json.dumps(small, ensure_ascii=False)) <= ext.MAX_PAYLOAD and small["title"] == "T"
    text = ext.context_text({"kind": "Новое письмо", "title": "Выписка", "from": "Банк", "url": "https://m"}, "ru")
    assert text.startswith("[Событие, которое запустило задачу]") and "От: Банк" in text
    en = ext.context_text({"kind": "New email", "title": "Hi", "from": "Bank"}, "en")
    assert "From: Bank" in en and not re.search(r"[А-Яа-я]", en)
    assert ext.render("{{trigger.data.n}} / {{ trigger.items }}", {"data": {"n": 7}, "items": [{"title": "a"}, {"title": "b"}]}) == "7 / a, b"


# --------------------------------------------------------------------------- webhook

def test_webhook_call(monkeypatch):
    tok = "abcdefghijklmnop1234"
    task = {"id": "t7", "name": "Hook", "kind": "notify", "prompt": "x", "enabled": True,
            "rule": rules.normalize_rule({"when": [{"type": "watch", "source": "webhook", "token": tok}]})}
    _save_tasks([task])
    assert ext.hook_call("wrongtokenwrongtoken0", {})[0] == 404
    assert ext.hook_call("bad", {})[0] == 404
    status, out = ext.hook_call(tok, {"title": "Сборка готова", "n": 1})
    assert status == 200 and out["tasks"] == 1
    pending = _tick([task], time.time())
    payload = pending["t7"][ext.watch_key("t7", task["rule"]["when"][0])][0]["payload"]
    assert payload["title"] == "Сборка готова" and payload["data"]["n"] == 1
    for _ in range(40):
        last = ext.hook_call(tok, {})
    assert last[0] == 429


def test_webhook_route_on_proxy_app():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    ext.register_proxy(app)
    client = TestClient(app)
    assert client.post("/hook/plan/nonexistenttoken000", json={}).status_code == 404
    assert client.post("/hook/plan/nonexistenttoken000", content=b"x" * (70 * 1024)).status_code == 413


# --------------------------------------------------------------------------- window routes

def test_window_routes(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    ext.register_routes(app)
    client = TestClient(app)
    monkeypatch.setattr(ext, "_panels_by_id", lambda: {"p_abcdef12": {"id": "p_abcdef12", "title": "Курс", "data": {"rate": "92"}}})
    cat = client.get("/api/plan/triggers/catalog?lang=en").json()
    assert {c["id"] for c in cat["categories"]} == {c[0] for c in ext.CATEGORIES}
    assert cat["panels"][0]["fields"][0] == {"key": "rate", "value": "92", "numeric": True}
    assert all(not re.search(r"[А-Яа-я]", c["title"]) for c in cat["categories"] + cat["sources"])
    monkeypatch.setattr(ext, "system_metric", lambda m, d="": 42.0)
    r = client.post("/api/plan/triggers/check", json={"trigger": {"type": "watch", "source": "system", "metric": "ram_pct", "op": "gt", "value": 40}}).json()
    assert r["ok"] and r["would_fire"] is True and r["value"] == 42.0
    bad = client.post("/api/plan/triggers/check", json={"trigger": {"type": "watch", "source": "gmail"}, "lang": "en"}).json()
    assert not bad["ok"] and bad["kind"] == "config" and "email" in bad["error"].lower()

    def off(*a, **k):
        raise ext.SourceError("GitHub не подключён", "connect", "github")

    monkeypatch.setattr(ext, "github", off)
    r = client.post("/api/plan/triggers/check", json={"trigger": {"type": "watch", "source": "github_pr", "repo": "o/r"}}).json()
    assert r["kind"] == "connect" and r["service_title"] == "GitHub"
    p = client.get("/api/plan/triggers/pick?what=github_repos")
    assert p.status_code == 400 and p.json()["kind"] == "connect"
    hook = client.post("/api/plan/triggers/check", json={"trigger": {"type": "watch", "source": "webhook", "token": "abcdefghijklmnop1234"}}).json()
    assert hook["url"].endswith("/hook/plan/abcdefghijklmnop1234")
    assert client.get("/api/plan/triggers/health").json()["tasks"] == {}


def test_plan_tasks_carries_watch_health_and_preview_warns(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app import schedule_rules_api
    app = FastAPI()
    app.state.plan_no_executor = True
    schedule_rules_api.register(app)
    client = TestClient(app)
    monkeypatch.setattr(ext, "service_status", lambda: {"github": False})
    task = {"id": "t8", "name": "PR", "kind": "notify", "prompt": "Новый PR: {{trigger.title}}",
            "rule": {"when": [{"type": "watch", "source": "github_pr", "repo": "o/r"}]}}
    assert client.post("/api/plan/save", json={"task": task}).json()["ok"]
    pv = client.post("/api/plan/preview", json={"task": task, "lang": "en"}).json()
    assert any("GitHub is not connected" in w for w in pv["warnings"]) and pv["events"]
    rows = client.get("/api/plan/tasks?lang=ru").json()["tasks"]
    row = next(r for r in rows if r["id"] == "t8")
    assert row["event_only"] and row["watch_health"][0]["source"] == "github_pr"
    assert row["summary"].startswith("Когда в o/r новый pull request")


# --------------------------------------------------------------------------- natural language (mocked model)

def test_nl_parse_with_watch(monkeypatch):
    from app import schedule_rules_api
    seen = {}

    async def completion(model, messages, **kw):
        seen["system"] = messages[0]["content"]
        seen["user"] = messages[1]["content"]
        return json.dumps({"name": "Встречи", "kind": "notify", "prompt": "Через 10 минут: {{trigger.title}}",
                           "rule": {"when": [{"type": "watch", "source": "calendar_before", "minutes": 10}]}})

    monkeypatch.setattr(ext, "_panels_by_id", lambda: {"p_abcdef12": {"id": "p_abcdef12", "title": "Курс", "data": {"rate": "92"}}})
    draft = asyncio.run(schedule_rules_api.parse_text("за 10 минут до встречи напомни", "m", completion, lang="ru", tasks=[], commands=[]))
    assert draft["rule"]["when"][0] == {"type": "watch", "source": "calendar_before", "minutes": 10, "calendar": "primary", "skip_all_day": True}
    assert '"source":S' in seen["system"].replace(" ", "") or "source\":S" in seen["system"]
    ctx = json.loads(seen["user"].split("\n\nREQUEST:")[0])
    assert ctx["panels"][0]["id"] == "p_abcdef12" and "gmail" in ctx["connected_integrations"]


# --------------------------------------------------------------------------- UI wiring

def test_ui_wiring_and_strings():
    js = (ROOT / "app/pgstatic/plan-triggers.js").read_text(encoding="utf-8")
    plan = (ROOT / "app/pgstatic/plan.js").read_text(encoding="utf-8")
    html = (ROOT / "app/pgstatic/index.html").read_text(encoding="utf-8")
    assert "plan-triggers.js" in html and "plan-triggers.css" in html
    assert html.index("plan-triggers.js") < html.index("/plan.js")
    for hook in ("MemuPlanTriggers.row(", "MemuPlanTriggers.menuItems(", "MemuPlanTriggers.bind(", "MemuPlanTriggers.cardHealth("):
        assert hook in plan
    # every source of the backend has a form and a title in the UI
    for sid in ext.SOURCES:
        assert re.search(r"\b%s: \{ cat:" % sid, js), sid
    # EN halves of T("ru", "en") never contain Cyrillic
    for ru, en_text in re.findall(r'T\("((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)"\)', js):
        assert not re.search(r"[А-Яа-яЁё]", en_text), en_text
    node = shutil.which("node")
    if node:
        assert subprocess.run([node, "--check", str(ROOT / "app/pgstatic/plan-triggers.js")]).returncode == 0
    css = (ROOT / "app/pgstatic/plan-triggers.css").read_text(encoding="utf-8")
    assert "var(--accent)" in css and "ptx-picker" in css


def test_build_and_spec_include_module():
    build = (ROOT / "build.py").read_text(encoding="utf-8")
    assert build.count('"app.schedule_triggers_ext"') >= 3
    for spec in ("MemuAI.spec", "MemuAIProxy.spec"):
        assert "'app.schedule_triggers_ext'" in (ROOT / spec).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- live (real model, skipped without keys)

LIVE_CASES = [
    ("Когда придёт письмо от банка Тинькофф — кратко перескажи его", "gmail"),
    ("За 10 минут до каждой встречи напомни мне о ней", "calendar_before"),
    ("Если упадёт Actions в репозитории octo/hello — разберись почему и напиши в Telegram", "github_actions"),
    ("Когда в Google Таблице https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/edit появится новая строка — сообщи", "sheets_row"),
    ("Если курс доллара на панели станет больше 100 — напомни купить", "panel"),
    ("Когда на странице https://example.com/item появится текст «В наличии» — уведоми", "web_page"),
]


def _live_completion(base: str, key: str, model: str):
    import httpx

    async def completion(m, messages, **kw):
        async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
            r = await client.post(base.rstrip("/") + "/chat/completions", headers={"Authorization": "Bearer " + key} if key else {},
                                  json={"model": model, "messages": messages, "temperature": 0})
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]

    return completion


@pytest.mark.parametrize("env_base,env_key,model", [("LIVE_PROXY_BASE", "LIVE_PROXY_KEY", "LIVE_PROXY_MODEL")])
def test_live_nl_parse_through_proxy(monkeypatch, env_base, env_key, model):
    """LIVE_PROXY_BASE=http://127.0.0.1:8733/v1 LIVE_PROXY_KEY=… LIVE_PROXY_MODEL=sol/gpt-6.1-sol (a Custom account in the Proxy)."""
    base, key, name = os.environ.get(env_base), os.environ.get(env_key, ""), os.environ.get(model)
    if not base or not name:
        pytest.skip("live Proxy is not configured")
    from app import schedule_rules_api
    monkeypatch.setattr(ext, "_panels_by_id", lambda: {"p_usd00001": {"id": "p_usd00001", "title": "Курс доллара", "data": {"usd": "92,4 ₽", "eur": "100,1 ₽"}}})
    completion = _live_completion(base, key, name)
    for text, source in LIVE_CASES:
        draft = asyncio.run(schedule_rules_api.parse_text(text, name, completion, lang="ru", tasks=[], commands=[]))
        sources = [t.get("source") for t in draft["rule"]["when"]]
        assert source in sources, (text, draft)


def test_nl_working_hours_wait(monkeypatch):
    from app import schedule_rules_api

    async def completion(model, messages, **kw):
        return json.dumps({"name": "Ревью", "kind": "notify", "prompt": "Ревью: {{trigger.title}}",
                           "rule": {"when": [{"type": "watch", "source": "github_review"}],
                                    "only_if": [{"type": "time", "from": "09:00", "to": "18:00", "days": [1, 2, 3, 4, 5]}]}})

    draft = asyncio.run(schedule_rules_api.parse_text("когда попросят о ревью, только в рабочее время", "m", completion,
                                                      lang="ru", tasks=[], commands=[]))
    assert draft["rule"]["options"]["if_blocked"] == "wait" and draft["rule"]["options"]["wait_minutes"] >= 960
