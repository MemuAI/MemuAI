# -*- coding: utf-8 -*-
"""Round 37 (plan): more conditions and options for Planning — no network.

- conditions calendar / sun / weather / wifi / last_result / group + "not" (normalize, check, texts);
- triggers sun and monthly nth_weekday / workday (slots);
- options quiet hours (postpone), max_per_day, retries with back-off (decide / note_finished);
- the sampler measures weather / Wi-Fi only when a rule needs them (cached);
- explain() for «Проверить сейчас», {{date}} variables, HTTP routes (geocoding mocked);
- the chat protocol copies stay equal and mention the new shapes; the front-end hooks exist.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="r37-plan-"))

from app import schedule_conds_ext as ext  # noqa: E402
from app import schedule_rules as rules  # noqa: E402
from app import schedule_sampler  # noqa: E402

VILNIUS = {"lat": 54.69, "lon": 25.28, "place": "Vilnius"}

def ts(text: str) -> float:
    return datetime.fromisoformat(text).timestamp()

class CalendarTest(unittest.TestCase):
    def test_holidays_and_workdays(self):
        self.assertEqual(ext._easter(2026), date(2026, 4, 5))
        self.assertEqual(ext._easter(2026, orthodox=True), date(2026, 4, 12))
        self.assertIn(date(2026, 1, 7), ext.holidays(2026, "RU"))
        self.assertIn(date(2026, 4, 6), ext.holidays(2026, "LT"))
        self.assertIn(date(2026, 11, 26), ext.holidays(2026, "US"))
        self.assertIn(date(2026, 7, 3), ext.holidays(2026, "US"))
        self.assertFalse(ext.is_workday(date(2026, 1, 5), "RU"))
        self.assertTrue(ext.is_workday(date(2026, 1, 12), "RU"))
        self.assertFalse(ext.is_workday(date(2026, 1, 10), "RU"))

    def test_unknown_country_uses_cached_nager_file_or_unknown(self):
        ext._HOLIDAYS.pop(("ZZ", 2026), None)
        (ext._holidays_dir() / "ZZ-2026.json").unlink(missing_ok=True)
        ext._HOL_TRIED[("ZZ", 2026)] = time.time()
        self.assertIsNone(ext.is_workday(date(2026, 5, 5), "ZZ"))
        folder = ext._holidays_dir()
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "ZZ-2026.json").write_text(json.dumps(["2026-05-05"]), "utf-8")
        self.assertFalse(ext.is_workday(date(2026, 5, 5), "ZZ"))
        self.assertTrue(ext.is_workday(date(2026, 5, 6), "ZZ"))
        (folder / "ZZ-2026.json").unlink()

    def test_modes(self):
        cond = lambda **kw: rules._normalize_condition({"type": "calendar", **kw})   # noqa: E731
        d = date(2026, 10, 13)
        self.assertTrue(ext.check_calendar(cond(mode="nth_weekday", nth=2, weekday=2), d))
        self.assertFalse(ext.check_calendar(cond(mode="nth_weekday", nth=-1, weekday=2), d))
        self.assertTrue(ext.check_calendar(cond(mode="nth_weekday", nth=-1, weekday=2), date(2026, 10, 27)))
        self.assertTrue(ext.check_calendar(cond(mode="week_parity", value="even"), d))
        self.assertTrue(ext.check_calendar(cond(mode="month_days", days="1-15"), d))
        self.assertTrue(ext.check_calendar(cond(mode="month_days", days=[-1]), date(2026, 2, 28)))
        self.assertTrue(ext.check_calendar(cond(mode="months", months=[10]), d))
        self.assertTrue(ext.check_calendar(cond(mode="date_range", **{"from": "20.12", "to": "10.01"}), date(2027, 1, 3)))
        self.assertFalse(ext.check_calendar(cond(mode="date_range", **{"from": "12-20", "to": "01-10"}), d))
        self.assertTrue(ext.check_calendar(cond(mode="last_workday", country="RU"), date(2026, 10, 30)))

        self.assertTrue(ext.check_calendar(cond(mode="first_workday", country="RU"), date(2026, 1, 12)))
        with self.assertRaises(rules.RuleError):
            cond(mode="nope")

class SunTest(unittest.TestCase):
    def test_sun_times_are_plausible(self):
        rise = ext.sun_event(date(2026, 6, 21), 55.75, 37.62, True)
        sset = ext.sun_event(date(2026, 6, 21), 55.75, 37.62, False)
        self.assertAlmostEqual((sset - rise) / 3600, 17.55, delta=0.2)
        self.assertIsNone(ext.sun_event(date(2026, 6, 21), 78.2, 15.6, True))

    def test_sun_condition_and_trigger(self):
        cond = rules._normalize_condition({"type": "sun", "value": "night", **VILNIUS})
        d = date(2026, 10, 13)
        noon = ext.sun_event(d, VILNIUS["lat"], VILNIUS["lon"], True) + 4 * 3600
        self.assertFalse(rules.check_condition(cond, {}, noon))
        self.assertTrue(rules.check_condition(dict(cond, value="day"), {}, noon))
        self.assertTrue(rules.check_condition(dict(cond, value="day", **{"not": True}), {}, noon) is False)
        rule = rules.normalize_rule({"when": [{"type": "sun", "event": "sunset", "offset": -15, **VILNIUS}]})
        slot = rules.next_slot(rule, ts("2026-10-13T00:00"))
        expected = ext.sun_event(d, VILNIUS["lat"], VILNIUS["lon"], False) - 900
        self.assertAlmostEqual(slot["start"], expected, delta=1)
        self.assertIn("заката", rules.summary(rule, "ru"))
        with self.assertRaises(rules.RuleError):
            rules.normalize_rule({"when": [{"type": "sun", "event": "sunset"}]})

class MonthlyTest(unittest.TestCase):
    def test_nth_weekday_and_workday_slots(self):
        rule = rules.normalize_rule({"when": [{"type": "monthly", "mode": "nth_weekday", "nth": 2, "weekday": 2, "time": "10:00"}]})
        first = rules.next_slot(rule, ts("2026-10-14T00:00"))
        self.assertEqual(datetime.fromtimestamp(first["start"]), datetime(2026, 11, 10, 10, 0))
        self.assertIn("во второй вторник", rules.summary(rule, "ru"))
        last = rules.normalize_rule({"when": [{"type": "monthly", "mode": "workday", "nth": -1, "country": "RU", "time": "18:00"}]})
        slot = rules.next_slot(last, ts("2026-12-01T00:00"))
        self.assertEqual(datetime.fromtimestamp(slot["start"]), datetime(2026, 12, 30, 18, 0))
        third = rules.normalize_rule({"when": [{"type": "monthly", "mode": "workday", "nth": 3, "country": "RU", "time": "09:00"}]})
        slot = rules.next_slot(third, ts("2026-12-31T19:00"))
        self.assertEqual(datetime.fromtimestamp(slot["start"]), datetime(2027, 1, 13, 9, 0))

        classic = rules.normalize_rule({"when": [{"type": "monthly", "day": 5, "time": "09:00"}]})
        self.assertEqual(classic["when"][0], {"type": "monthly", "day": 5, "time": "09:00"})

class WeatherWifiTest(unittest.TestCase):
    def setUp(self):
        ext._WEATHER.clear()
        ext._WIFI.update(at=0.0, value=None)

    def test_weather_check_and_cache(self):
        cond = rules._normalize_condition({"type": "weather", "metric": "temp", "op": "lt", "value": 0, **VILNIUS})
        calls = []

        def fake(lat, lon):
            calls.append((lat, lon))
            return {"temp": -3.5, "rain": False, "snow": True, "wind": 12}
        needs = rules.needs(rules.normalize_rule({"when": [{"type": "daily", "times": ["09:00"]}], "only_if": [cond]}))
        self.assertIn("weather:" + ext.weather_key(cond), needs["ext"])
        with patch.object(ext, "fetch_weather", fake):
            sampler = schedule_sampler.Sampler()
            _p, sample = sampler.get(needs, now=1000.0)
            self.assertTrue(rules.check_condition(cond, sample, 1000.0))
            self.assertTrue(rules.check_condition(dict(cond, metric="snow"), sample, 1000.0))
            _p, sample = sampler.get(needs, now=1000.0 + 600, fresh=True)
            self.assertEqual(len(calls), 1)
        with patch.object(ext, "fetch_weather", side_effect=OSError("offline")):
            data = ext.weather_now(ext.weather_key(cond), 1, 1, 1000.0 + ext.WEATHER_TTL + 5)
            self.assertEqual(data["temp"], -3.5)
            self.assertIsNone(ext.weather_now(ext.weather_key(cond), 1, 1, 1000.0 + ext.WEATHER_STALE + ext.WEATHER_RETRY + 10))
        self.assertIsNone(rules.check_condition(cond, {"ext": {}}, 0))

    def test_wifi(self):
        cond = rules._normalize_condition({"type": "wifi", "ssid": "home"})
        self.assertTrue(rules.check_condition(cond, {"ext": {"wifi": "Home"}}, 0))
        self.assertFalse(rules.check_condition(cond, {"ext": {"wifi": "Office"}}, 0))
        self.assertFalse(rules.check_condition(cond, {"ext": {"wifi": ""}}, 0))
        self.assertIsNone(rules.check_condition(cond, {"ext": {"wifi": None}}, 0))
        any_wifi = rules._normalize_condition({"type": "wifi", "value": False})
        self.assertTrue(rules.check_condition(any_wifi, {"ext": {"wifi": ""}}, 0))
        probe = []
        out = ext.measure({"wifi": {}}, 50.0, probes={"wifi": lambda: probe.append(1) or "Home"})
        ext.measure({"wifi": {}}, 60.0, probes={"wifi": lambda: probe.append(1) or "Home"})
        self.assertEqual(out["wifi"], "Home")
        self.assertEqual(len(probe), 1)

    def test_netsh_parsing(self):
        class Done:
            returncode = 0
            stdout = "    Name                   : Wi-Fi\n    BSSID                  : aa:bb\n    SSID                   : My Net\n".encode()
        with patch.object(ext.os, "name", "nt"), patch.object(ext.subprocess, "run", return_value=Done()):
            self.assertEqual(ext.wifi_ssid(), "My Net")

class LogicTest(unittest.TestCase):
    def test_group_and_not(self):
        rule = rules.normalize_rule({"when": [{"type": "daily", "times": ["09:00"]}], "only_if": [
            {"type": "group", "match": "any", "items": [
                {"type": "process", "name": "steam.exe"},
                {"type": "wifi", "ssid": "Home", "not": True}]}]})
        group = rule["only_if"][0]
        self.assertEqual(group["items"][1]["not"], True)
        ok, _ = rules.conditions_hold(rule, {"processes": set(), "ext": {"wifi": "Office"}}, 0)
        self.assertTrue(ok)
        ok, failed = rules.conditions_hold(rule, {"processes": set(), "ext": {"wifi": "Home"}}, 0)
        self.assertFalse(ok)
        self.assertIn("НЕ подключён Wi-Fi «Home»", rules.condition_text(failed, "ru"))
        needs = rules.needs(rule)
        self.assertIn("steam", needs["processes"])
        with self.assertRaises(rules.RuleError):
            rules.normalize_rule({"when": [{"type": "daily", "times": ["09:00"]}], "only_if": [
                {"type": "group", "items": [{"type": "group", "items": [{"type": "wifi"}]}]}]})

    def test_last_result(self):
        cond = rules._normalize_condition({"type": "last_result", "status": "ok", "contains": "скидка"})
        task = {"last_finished": 100.0, "last_status": "ok", "last_text": "Нашёл скидку 20% на билеты"}
        self.assertFalse(rules.check_condition(cond, {}, 200.0, {"task": task}))
        task["last_text"] = "Есть скидка 20%"
        self.assertTrue(rules.check_condition(cond, {}, 200.0, {"task": task}))
        other = rules._normalize_condition({"type": "last_result", "task_id": "b", "status": "fail", "within_hours": 1})
        by_id = {"b": {"last_finished": 100.0, "last_status": "fail"}}
        self.assertTrue(rules.check_condition(other, {}, 200.0, {"tasks_by_id": by_id}))
        self.assertFalse(rules.check_condition(other, {}, 100.0 + 7200, {"tasks_by_id": by_id}))
        self.assertIsNone(rules.check_condition(other, {}, 200.0, {"tasks_by_id": {}}))
        t = {"rule": {"when": []}, "rule_state": {}}
        rules.note_finished(t, True, 5.0, text="x" * 2000)
        self.assertEqual(len(t["last_text"]), ext.LAST_TEXT)

def _task(rule, **kw):
    return {"id": "t1", "kind": "notify", "prompt": "hi", "enabled": True, "rule": rule, **kw}

class OptionsTest(unittest.TestCase):
    def test_old_options_unchanged(self):
        rule = rules.normalize_rule({"when": [{"type": "daily", "times": ["09:00"]}]})
        self.assertNotIn("quiet_from", rule["options"])
        self.assertNotIn("retry_count", rule["options"])

    def test_quiet_hours_postpone(self):
        rule = {"when": [{"type": "daily", "times": ["23:30"]}], "options": {"quiet_from": "23:00", "quiet_to": "07:00"}}
        task = _task(rule)
        start = ts("2026-10-13T23:00")
        rules.decide(task, start, {}, None, rng=lambda: 0.0)
        due = ts("2026-10-13T23:30")
        out = rules.decide(task, due, {}, None, rng=lambda: 0.0)
        self.assertFalse(out["run"])
        self.assertIn("Тихие часы", task["rule_state"]["reason"]["ru"])
        self.assertGreaterEqual(task["rule_state"]["deadline"], ts("2026-10-14T07:00"))
        out = rules.decide(task, ts("2026-10-14T07:00:30"), {}, None, rng=lambda: 0.0)
        self.assertTrue(out["run"])
        self.assertIn("тихие часы 23:00–07:00", rules.summary(rule, "ru"))

    def test_max_per_day(self):
        rule = {"when": [{"type": "interval", "every": 10}], "options": {"max_per_day": 2}}
        task = _task(rule)
        now = ts("2026-10-13T08:00")
        rules.decide(task, now, {}, None, rng=lambda: 0.0)
        runs = 0
        for k in range(1, 8):
            out = rules.decide(task, now + k * 600 + 1, {}, None, rng=lambda: 0.0)
            runs += out["run"]
        self.assertEqual(runs, 2)
        self.assertIn("Дневной лимит", task["rule_state"]["reason"]["ru"])

    def test_retry_with_backoff_keeps_a_one_time_task_alive(self):
        at = "2026-10-13T09:00"
        rule = {"when": [{"type": "once", "at": at}], "options": {"retry_count": 2, "retry_minutes": 5}}
        task = _task(rule)
        t0 = ts(at)
        rules.decide(task, t0 - 60, {}, None, rng=lambda: 0.0)
        out = rules.decide(task, t0 + 1, {}, None, rng=lambda: 0.0)
        self.assertTrue(out["run"])
        self.assertFalse(task["enabled"])
        rules.note_finished(task, False, t0 + 10)
        self.assertTrue(task["enabled"])
        self.assertAlmostEqual(task["rule_state"]["retry_at"], t0 + 10 + 300)
        self.assertFalse(rules.decide(task, t0 + 200, {}, None, rng=lambda: 0.0)["run"])
        self.assertTrue(task["enabled"])
        out = rules.decide(task, t0 + 320, {}, None, rng=lambda: 0.0)
        self.assertTrue(out["run"])
        self.assertEqual(out["source"], "retry")
        rules.note_finished(task, False, t0 + 330)
        self.assertAlmostEqual(task["rule_state"]["retry_at"], t0 + 330 + 600)
        rules.decide(task, t0 + 940, {}, None, rng=lambda: 0.0)
        rules.note_finished(task, False, t0 + 950)
        self.assertNotIn("retry_at", task["rule_state"])
        self.assertFalse(task["enabled"])

    def test_retry_cleared_on_success_and_not_for_test_runs(self):
        task = _task({"when": [{"type": "daily", "times": ["09:00"]}], "options": {"retry_count": 3}}, rule_state={})
        rules.note_finished(task, False, 10.0, test=True)
        self.assertNotIn("retry_at", task["rule_state"])
        rules.note_finished(task, False, 10.0)
        self.assertIn("retry_at", task["rule_state"])
        rules.note_finished(task, True, 20.0)
        self.assertNotIn("retry_at", task["rule_state"])

class ExplainVarsTest(unittest.TestCase):
    def test_explain(self):
        rule = rules.normalize_rule({"when": [{"type": "daily", "times": ["09:00"]}], "only_if": [
            {"type": "calendar", "mode": "weekend"},
            {"type": "group", "match": "any", "items": [{"type": "wifi", "ssid": "Home"}, {"type": "network"}]}],
            "options": {"max_per_day": 1}})
        now = ts("2026-10-17T12:00")
        out = ext.explain(rule, {"ext": {"wifi": "Home"}, "network": True}, now, "en",
                          {"state": {"day_runs": {"d": "2026-10-17", "n": 1}}})
        self.assertEqual([r["depth"] for r in out["rows"]], [0, 0, 1, 1])
        self.assertTrue(all(r["ok"] for r in out["rows"]))
        self.assertFalse(out["ok"])
        self.assertIn("Daily limit", out["notes"][0])

    def test_render_vars(self):
        now = ts("2026-10-13T09:05")
        task = {"prompt": "Сводка на {{date}} ({{weekday}}), прошлое: {{last_result}} {{ trigger.title }}", "last_text": "ok"}
        out = ext.render_vars(task, now, "ru")
        self.assertEqual(out["prompt"], "Сводка на 2026-10-13 (вторник), прошлое: ok {{ trigger.title }}")
        plain = {"prompt": "plain"}
        self.assertIs(ext.render_vars(plain, now), plain)

class RoutesTest(unittest.TestCase):
    def test_check_and_geo_routes(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        ext.register_routes(app)
        client = TestClient(app)
        body = {"lang": "en", "task": {"kind": "notify", "rule": {"when": [{"type": "daily", "times": ["09:00"]}],
                                                                  "only_if": [{"type": "calendar", "mode": "months", "months": list(range(1, 13))}]}}}
        res = client.post("/api/plan/conds/check", json=body)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json()["ok"])
        bad = client.post("/api/plan/conds/check", json={"task": {"rule": {"when": []}}})
        self.assertEqual(bad.status_code, 400)
        with patch.object(ext, "geocode", return_value=[{"place": "Šiauliai", "label": "Šiauliai, Lithuania", "lat": 55.93, "lon": 23.31, "country": "LT"}]):
            self.assertEqual(client.get("/api/plan/conds/geo", params={"q": "Šiau"}).json()["places"][0]["country"], "LT")
        with patch.object(ext, "geocode", side_effect=OSError("offline")):
            res = client.get("/api/plan/conds/geo", params={"q": "Šiau", "lang": "en"})
            self.assertEqual(res.status_code, 502)
            self.assertIn("unavailable", res.json()["error"])

class ProtocolAndFrontendTest(unittest.TestCase):
    def test_protocol(self):
        text = rules.CHAT_PROTOCOL
        for marker in ('"type":"calendar"', '"type":"sun"', '"type":"weather"', '"type":"wifi"', '"type":"group"',
                       '"not":true', '"mode":"nth_weekday"', '"quiet_from"', '"retry_count"', "{{last_result}}"):
            self.assertIn(marker, text)
        js = (ROOT / "app/pgstatic/plan.js").read_text("utf-8")
        self.assertEqual(json.loads(re.search(r'const PROTOCOL = (".*?");\n', js, re.S).group(1)), text)
        for marker in ('"type":"calendar"', '"mode":"nth_weekday"', '"retry_count"', '"type":"group"'):
            self.assertIn(marker, rules.PARSE_PROMPT)

    def test_protocol_examples_normalize(self):
        rule = rules.normalize_rule({"when": [{"type": "monthly", "mode": "workday", "nth": -1, "country": "RU", "time": "18:00"},
                                              {"type": "sun", "event": "sunrise", "offset": 10, "lat": 55.7, "lon": 37.6}],
                                     "only_if": [{"type": "calendar", "mode": "workday", "country": "RU"},
                                                 {"type": "weather", "metric": "rain", "lat": 55.7, "lon": 37.6, "not": True},
                                                 {"type": "last_result", "status": "fail"}],
                                     "options": {"quiet_from": "22:00", "quiet_to": "08:00", "max_per_day": 2,
                                                 "retry_count": 3, "retry_minutes": 10}})
        self.assertEqual(rule["options"]["retry_count"], 3)
        self.assertTrue(rules.summary(rule, "en"))

    def test_frontend_hooks(self):
        plan = (ROOT / "app/pgstatic/plan.js").read_text("utf-8")
        for hook in ("MemuPlanConds.catalog()", "MemuPlanConds.triggers()", "MemuPlanConds.row(cond, p, condCtx())",
                     "MemuPlanConds.head(cond, p)", "MemuPlanConds.whenBody(t, p, condCtx())", "MemuPlanConds.optionsHtml(o, condCtx())",
                     "MemuPlanConds.checkHtml()", "MemuPlanConds.bind(root, condCtx())", "MemuPlanConds.defaults(cond)"):
            self.assertIn(hook, plan)
        index = (ROOT / "app/pgstatic/index.html").read_text("utf-8")
        self.assertLess(index.index("/plan-conds.js?v="), index.index("/plan.js?v="))
        self.assertIn("/plan-conds.css?v=", index)
        conds = (ROOT / "app/pgstatic/plan-conds.js").read_text("utf-8")
        self.assertNotIn("setInterval", conds)

        for m in re.finditer(r'T\("([^"]*)", "([^"]*)"\)', conds):
            self.assertTrue(m.group(2).strip(), m.group(0))
        for spec in ("MemuAI.spec", "MemuAIProxy.spec", "build.py"):
            self.assertIn("app.schedule_conds_ext", (ROOT / spec).read_text("utf-8"), spec)

sys.path.insert(0, str(ROOT / "tests"))
try:
    from round27_ui_lang_test import _chromium, _has_playwright  # noqa: E402
except Exception:  # noqa: BLE001
    _chromium = _has_playwright = lambda: False   # noqa: E731

@unittest.skipUnless(_chromium() and _has_playwright(), "headless Chromium / playwright is not available")
class BuilderBrowserTest(unittest.TestCase):
    """The real window: add a calendar condition, invert it, build a group, a sun trigger, «Проверить сейчас»."""

    def test_builder_flow(self):
        import httpx
        from playwright.sync_api import sync_playwright
        from ui_lang_scan import Servers
        servers = Servers().start()
        self.addCleanup(servers.stop)
        with httpx.Client(timeout=30, trust_env=False) as http:
            http.post(servers.pg + "/api/pg/preferences", json={"profile_onboarded": True, "language": "en",
                                                                 "updates_enabled": False, "ui_motion": "reduced"})
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])
            try:
                self._flow(browser, servers)
            finally:
                browser.close()

    def _flow(self, browser, servers) -> None:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors: list = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(servers.pg + "/")
        page.wait_for_selector("body.boot-ready", timeout=90000)
        page.evaluate("(async () => { await showSchedule(true); MemuPlan.openBuilder({id: 'b1', name: 'x', kind: 'notify', prompt: 'hi'}); })()")
        page.wait_for_selector("#pln-builder [data-act='add-if']")

        def menu_pick(anchor: str, title: str) -> None:
            page.click(anchor)
            page.locator(".pln-pop button", has_text=title).first.click()
            page.wait_for_timeout(150)

        menu_pick("#pln-builder [data-act='add-if']", "Calendar: workday")
        page.wait_for_selector("#pb-if [data-pcx-rerender]", state="attached")
        page.click("#pb-if [data-act='pcx-not']")
        menu_pick("#pln-builder [data-act='add-if']", "A group")
        page.click("#pb-if [data-act='pcx-add']")
        page.locator(".pln-pop button", has_text="Wi-Fi network").first.click()
        page.wait_for_selector("#pb-if .pcx-sub")
        menu_pick("#pln-builder [data-act='add-when']", "Sunrise / sunset")
        page.wait_for_selector("#pb-when [data-pcx-geo]")
        page.wait_for_timeout(500)
        summary = page.inner_text("#pln-summary")
        self.assertIn("city", summary.lower())

        page.evaluate("""() => { const b = document.createElement('button'); b.dataset.act = 'pcx-pick';
            b.dataset.path = 'rule.when.1'; b.dataset.lat = '54.69'; b.dataset.lon = '25.28'; b.dataset.place = 'Vilnius';
            document.querySelector('#pb-when').appendChild(b); b.click(); }""")
        page.wait_for_timeout(700)
        summary = page.inner_text("#pln-summary")
        self.assertIn("sunset (Vilnius)", summary)
        self.assertIn("NOT a workday", summary)
        page.click("#pb-if [data-act='pcx-check']")
        page.wait_for_selector("#pb-if .pcx-check-box", timeout=20000)
        self.assertEqual(page.locator("#pb-if .pcx-check-box li").count(), 3)
        self.assertEqual(errors, [])

if __name__ == "__main__":
    unittest.main()
