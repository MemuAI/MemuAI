# -*- coding: utf-8 -*-
"""Round 37 (plan-2): the second wave of «Планирование».

- Russia's production calendar (moved days off, working Saturdays, shortened days): built-in years, isdayoff.ru /
  xmlcalendar.ru parsing with a disk cache (network mocked), new calendar modes day_off / short_day /
  first|last_workday_week;
- slow probes (weather, Wi-Fi, calendars) run in a background pool — the Proxy tick does not wait for them;
- the weather TRIGGER (watch source «weather»): fires when the weather crosses the value, hysteresis, cool-down;
- a switched-off condition («off») stays in the rule and is not checked;
- the Mini App conditions ops (schedule_tgapp_ops) and the scenario gallery (plan-templates.js).
No network: every download is patched.
"""
from __future__ import annotations

import calendar
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="memu-r37-plan2-"))

from app import schedule_conds_ext as ext  # noqa: E402
from app import schedule_rules as rules  # noqa: E402
from app import schedule_tgapp_ops as tg_ops  # noqa: E402
from app import schedule_triggers_ext as tx  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "pgstatic"
MOSCOW = {"lat": 55.75, "lon": 37.62, "place": "Москва"}


class ProductionCalendarTest(unittest.TestCase):
    def test_builtin_years_follow_the_decrees(self):
        self.assertEqual(ext.day_type(date(2026, 1, 9), "RU"), "off")       # moved from Sat 3 Jan
        self.assertEqual(ext.day_type(date(2026, 1, 12), "RU"), "work")
        self.assertEqual(ext.day_type(date(2026, 3, 9), "RU"), "off")       # 8 March falls on a Sunday
        self.assertEqual(ext.day_type(date(2026, 4, 30), "RU"), "short")
        self.assertEqual(ext.day_type(date(2026, 12, 31), "RU"), "off")
        self.assertEqual(ext.day_type(date(2025, 11, 1), "RU"), "short")    # a working Saturday, shortened
        self.assertTrue(ext.is_workday(date(2025, 11, 1), "RU"))
        self.assertFalse(ext.is_workday(date(2025, 11, 3), "RU"))           # Monday moved off
        self.assertEqual(ext.day_type(date(2026, 10, 13), "RU"), "work")

    def test_modes(self):
        cond = lambda **kw: rules._normalize_condition({"type": "calendar", "country": "RU", **kw})   # noqa: E731
        self.assertTrue(ext.check_calendar(cond(mode="day_off"), date(2026, 1, 9)))
        self.assertFalse(ext.check_calendar(cond(mode="day_off"), date(2026, 1, 12)))
        self.assertTrue(ext.check_calendar(cond(mode="short_day"), date(2026, 11, 3)))
        self.assertTrue(ext.check_calendar(cond(mode="holiday"), date(2026, 5, 11)))     # moved day off counts
        self.assertTrue(ext.check_calendar(cond(mode="last_workday_week"), date(2026, 12, 30)))   # 31 Dec is off
        self.assertFalse(ext.check_calendar(cond(mode="last_workday_week"), date(2026, 10, 15)))
        self.assertTrue(ext.check_calendar(cond(mode="last_workday_week"), date(2026, 10, 16)))
        self.assertTrue(ext.check_calendar(cond(mode="first_workday_week"), date(2026, 1, 12)))
        self.assertTrue(ext.check_calendar(cond(mode="first_workday_week"), date(2026, 3, 10)))   # Mon 9 March off
        us = rules._normalize_condition({"type": "calendar", "mode": "short_day", "country": "US"})
        self.assertTrue(ext.check_calendar(us, date(2026, 11, 25)))           # the workday before Thanksgiving
        for mode in ("day_off", "short_day", "first_workday_week", "last_workday_week"):
            c = cond(mode=mode)
            ru, en = rules.condition_text(c, "ru"), rules.condition_text(c, "en")
            self.assertTrue(ru and en and not re.search(r"[А-Яа-яЁё]", en), (mode, en))

    def test_parsers(self):
        codes = ("1" * 8 + "0" * 357)
        prod = ext._prod_from_codes(2031, codes, "test")
        self.assertIn(date(2031, 1, 1), prod["off"])
        self.assertIn(date(2031, 1, 11), prod["work"])                       # Saturday marked 0 → a workday
        self.assertIsNone(ext._prod_from_codes(2031, "10", "test"))           # wrong length
        months = [{"month": m, "days": ",".join(str(d) for d in range(1, calendar.monthrange(2031, m)[1] + 1)
                                                if date(2031, m, d).isoweekday() >= 6)} for m in range(1, 13)]
        months[0]["days"] += ",2+,3*"
        parsed = ext._prod_from_xmlcalendar(2031, {"months": months})
        self.assertIn(date(2031, 1, 2), parsed["off"])
        self.assertIn(date(2031, 1, 3), parsed["short"])

    def test_download_and_disk_cache(self):
        year = 2033
        ext._PROD.pop(year, None)
        ext._PROD_TRIED.pop(year, None)
        path = ext._holidays_dir() / f"RU-production-{year}.json"
        path.unlink(missing_ok=True)
        days = (date(year + 1, 1, 1) - date(year, 1, 1)).days
        codes = "".join("1" if (date(year, 1, 1).toordinal() + i) % 7 in (6, 0) or i < 8 else "0" for i in range(days))

        class Resp:
            status_code = 200
            text = codes

        calls = []
        with patch("httpx.get", side_effect=lambda url, **kw: calls.append(url) or Resp()):
            self.assertIsNone(ext.production_calendar(year))                  # no fetch outside the pool
            found = ext.production_calendar(year, fetch=True)
        self.assertEqual(len(calls), 1)
        self.assertIn("isdayoff.ru", calls[0])
        self.assertIn(date(year, 1, 3), found["off"])
        self.assertTrue(path.exists())
        ext._PROD.pop(year, None)
        again = ext.production_calendar(year)                                 # from the disk cache
        self.assertEqual(again["off"], found["off"])
        path.unlink()
        ext._PROD.pop(year, None)

    def test_offline_falls_back_to_fixed_holidays(self):
        year = 2034
        ext._PROD.pop(year, None)
        ext._PROD_TRIED[year] = time.time()                                    # no download in this test
        self.assertIsNone(ext.production_calendar(year))
        self.assertEqual(ext.day_type(date(year, 1, 3), "RU"), "off")          # built-in New Year holidays
        self.assertEqual(ext.day_type(date(year, 2, 23), "RU"), "off")

    def test_needs_marks_russia_for_background_loading(self):
        rule = rules.normalize_rule({"when": [{"type": "daily", "times": ["09:00"]}],
                                     "only_if": [{"type": "calendar", "mode": "workday", "country": "RU"}]})
        self.assertIn("holidays:RU", rules.needs(rule)["ext"])


class BackgroundTest(unittest.TestCase):
    def setUp(self):
        ext._WEATHER.clear()
        ext._BG.clear()

    def test_slow_weather_does_not_block_the_tick(self):
        gate = threading.Event()

        def slow(lat, lon):
            gate.wait(5)
            return {"temp": 3.0, "rain": True}
        spec = {"weather:1.0,2.0": {"lat": 1.0, "lon": 2.0}}
        with patch.object(ext, "fetch_weather", slow):
            started = time.monotonic()
            out = ext.measure(spec, 5000.0)
            self.assertLess(time.monotonic() - started, 1.5)
            self.assertIsNone(out["weather:1.0,2.0"])                         # not loaded yet
            out = ext.measure(spec, 5001.0)                                   # the same job, not a second one
            gate.set()
            for _ in range(50):
                out = ext.measure(spec, 5002.0)
                if out["weather:1.0,2.0"]:
                    break
                time.sleep(0.05)
        self.assertEqual(out["weather:1.0,2.0"]["temp"], 3.0)

    def test_prefetch_blocks_for_check_now(self):
        with patch.object(ext, "fetch_weather", lambda lat, lon: {"temp": -1.0}):
            ext.prefetch({"weather:3.0,4.0": {"lat": 3.0, "lon": 4.0}}, 7000.0)
        self.assertEqual(ext.weather_now("3.0,4.0", 3.0, 4.0, 7001.0, fetch=False)["temp"], -1.0)


class WeatherTriggerTest(unittest.TestCase):
    def setUp(self):
        ext._WEATHER.clear()

    def poll(self, trig, seq, step=1200):
        cur, fired = None, []
        for i, data in enumerate(seq):
            ext._WEATHER.clear()
            with patch.object(ext, "fetch_weather", lambda lat, lon, d=data: d):
                result = tx.poll_source(trig, cur, "ru", 100000 + i * step)
            cur = result.cursor
            fired.append([e["title"] for e in result.events])
        return fired

    def test_rain_starts_with_cooldown(self):
        trig = rules._normalize_trigger({"type": "watch", "source": "weather", "metric": "rain", **MOSCOW})
        self.assertEqual(trig["cooldown"], 180)
        self.assertIn("начнётся дождь", rules.trigger_text(trig, "ru"))
        seq = [{"rain": True}, {"rain": False}, {"rain": True}, {"rain": False}, {"rain": True}]
        fired = self.poll(trig, seq, step=3600)
        self.assertEqual(fired[0], [])                                       # already raining when created: no run
        self.assertTrue(fired[2] and fired[2][0].startswith("Начался дождь"))
        self.assertEqual(fired[4], [])                                       # 2 h later: cool-down
        fired = self.poll(dict(trig, cooldown=60), seq, step=3600)
        self.assertTrue(fired[4])

    def test_temperature_crosses_with_hysteresis(self):
        trig = rules._normalize_trigger({"type": "watch", "source": "weather", "metric": "temp", "op": "lt", "value": 0,
                                         "cooldown": 0, **MOSCOW})
        self.assertEqual(trig["hysteresis"], 1.0)
        temps = [2, -0.5, 0.4, -0.2, 1.5, -1]
        fired = self.poll(trig, [{"temp": t} for t in temps])
        self.assertEqual([bool(f) for f in fired], [False, True, False, False, False, True])
        self.assertIn("Температура < 0°C", fired[1][0])
        self.assertIn("temperature becomes < 0°C", rules.trigger_text(trig, "en"))

    def test_validation_and_check(self):
        with self.assertRaises(rules.RuleError):
            rules._normalize_trigger({"type": "watch", "source": "weather", "metric": "rain"})          # no city
        with self.assertRaises(rules.RuleError):
            rules._normalize_trigger({"type": "watch", "source": "weather", "metric": "hail", **MOSCOW})
        with patch.object(ext, "fetch_weather", lambda lat, lon: {"temp": -3, "rain": False}):
            out = tx.check({"type": "watch", "source": "weather", "metric": "temp", "op": "lt", "value": 0, **MOSCOW})
        self.assertTrue(out["ok"] and out["would_fire"])
        ext._WEATHER.clear()
        with patch.object(ext, "fetch_weather", side_effect=OSError("offline")):
            out = tx.check({"type": "watch", "source": "weather", "metric": "rain", "lat": 10, "lon": 10})
        self.assertFalse(out["ok"])
        self.assertEqual(out["kind"], "network")
        self.assertIn('weather +\"metric\"'.replace("\\", ""), rules.CHAT_PROTOCOL)


class OffConditionTest(unittest.TestCase):
    def test_off_condition_is_kept_but_not_checked(self):
        rule = rules.normalize_rule({"when": [{"type": "daily", "times": ["09:00"]}], "only_if": [
            {"type": "wifi", "ssid": "Home", "off": True},
            {"type": "group", "match": "any", "items": [{"type": "wifi", "ssid": "Office", "off": True}, {"type": "network"}]}]})
        self.assertTrue(rule["only_if"][0]["off"])
        sample = {"processes": set(), "network": True, "ext": {"wifi": "Cafe"}}
        ok, failed = rules.conditions_hold(rule, sample, 0)
        self.assertTrue(ok, failed)
        self.assertNotIn("Home", rules.summary(rule, "ru"))
        self.assertNotIn("wifi", rules.needs(rule).get("ext", {}))
        out = ext.explain(rule, sample, 0, "ru", {})
        self.assertTrue(out["rows"][0]["off"])
        self.assertTrue(out["ok"])


class MiniAppOpsTest(unittest.TestCase):
    def task(self):
        return {"id": "t1", "name": "x", "rule": {"when": [{"type": "daily", "times": ["09:00"]}], "only_if": [
            {"type": "calendar", "mode": "workday", "country": "RU"},
            {"type": "group", "match": "all", "items": [{"type": "network"}, {"type": "wifi", "ssid": "Home"}]}]},
            "rule_state": {"status": "blocked", "reason": {"ru": "x"}}, "next_run": 1}

    def test_rows_and_changes(self):
        task = self.task()
        rows = tg_ops.condition_rows(task, "ru")["rows"]
        self.assertEqual([r["path"] for r in rows], ["0", "1", "1.0", "1.1"])
        self.assertEqual(rows[2]["depth"], 1)
        self.assertTrue(tg_ops.apply_change(task, {"path": "1.1", "off": True}))
        self.assertTrue(task["rule"]["only_if"][1]["items"][1]["off"])
        self.assertNotIn("next_run", task)
        self.assertNotIn("status", task["rule_state"])
        self.assertTrue(tg_ops.apply_change(task, {"path": "0", "not": True}))
        self.assertTrue(tg_ops.apply_change(task, {"path": "1", "match": "any"}))
        self.assertTrue(tg_ops.apply_change(task, {"path": "", "match": "any"}))
        self.assertEqual(task["rule"]["match"], "any")
        self.assertEqual(task["rule"]["only_if"][1]["match"], "any")
        self.assertFalse(tg_ops.apply_change(task, {"path": "7", "off": True}))
        self.assertTrue(tg_ops.apply_change(task, {"path": "1.1", "off": False}))
        self.assertNotIn("off", task["rule"]["only_if"][1]["items"][1])
        rows = tg_ops.condition_rows(task, "en")["rows"]
        self.assertTrue(rows[0]["not"])

    def test_registered_and_wired(self):
        names = []
        tg_ops.register_tgapp_ops(lambda name: (names.append(name), (lambda fn: fn))[1], Exception)
        self.assertEqual(set(names), {"schedules.conds", "schedules.cond_set", "schedules.cond_check"})
        self.assertIn("schedule_tgapp_ops.register_tgapp_ops", (ROOT / "app" / "tgapp_api.py").read_text("utf-8"))
        js = (ROOT / "app" / "tgapp" / "plan.js").read_text("utf-8")
        for op in names:
            self.assertIn(f'"{op}"', js)
        for f in ("build.py", "MemuAI.spec", "MemuAIProxy.spec"):
            self.assertIn("app.schedule_tgapp_ops", (ROOT / f).read_text("utf-8"), f)


class StaticTest(unittest.TestCase):
    def test_gallery_and_weather_trigger_ui(self):
        index = (STATIC / "index.html").read_text("utf-8")
        self.assertIn("/plan-templates.js?v=", index)
        self.assertIn("/plan-templates.css?v=", index)
        self.assertLess(index.index("/plan-templates.js?v="), index.index("/plan.js?v="))
        gallery = (STATIC / "plan-templates.js").read_text("utf-8")
        for marker in ('mode: "last_workday_week"', 'source: "weather"', 'type: "sun"', 'retry_count', 'mode: "short_day"',
                       'match: "any"', 'not: true', 'quiet_from'):
            self.assertIn(marker, gallery)
        self.assertGreaterEqual(len(re.findall(r"\{ cat: \"", gallery)), 8)
        self.assertIn("openGallery", (STATIC / "plan.js").read_text("utf-8"))
        self.assertIn('case "weather": return weatherHtml', (STATIC / "plan-triggers.js").read_text("utf-8"))
        conds = (STATIC / "plan-conds.js").read_text("utf-8")
        for marker in ('"day_off"', '"short_day"', '"last_workday_week"', 'data-act="pcx-off"', "placeHtml, withPlace"):
            self.assertIn(marker, conds)

    def test_js_syntax(self):
        for f in (STATIC / "plan-templates.js", STATIC / "plan-conds.js", STATIC / "plan-triggers.js", STATIC / "plan.js",
                  ROOT / "app" / "tgapp" / "plan.js"):
            done = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, (f, done.stderr))

    def test_templates_normalize(self):
        """Every gallery scenario is a valid rule once a city is chosen (evaluated by node)."""
        script = """
global.window = {}; global.PG_LANG = process.argv[2];
require(process.argv[1]);
const place = { lat: 55.75, lon: 37.62, place: "Moscow" };
const fill = (x) => { if (Array.isArray(x)) return x.map(fill); if (x && typeof x === "object") { const o = {};
  for (const [k, v] of Object.entries(x)) o[k] = fill(v);
  if (["weather", "sun"].includes(o.type) || o.source === "weather") Object.assign(o, place); return o; } return x; };
console.log(JSON.stringify(window.MemuPlanTemplates.scenarios().map((s) => fill(s.task))));
"""
        for lang in ("ru", "en"):
            out = subprocess.run(["node", "-e", script, str(STATIC / "plan-templates.js"), lang], capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            tasks = json.loads(out.stdout)
            self.assertGreaterEqual(len(tasks), 8)
            for task in tasks:
                rule = rules.normalize_rule(task["rule"])
                self.assertTrue(rules.summary(rule, lang), task["name"])


if __name__ == "__main__":
    unittest.main()
