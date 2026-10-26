"""Round 29 (r29b-scheduling): the rule engine of «Планирование» — app/schedule_rules.py."""
import os
import sys
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import schedule_rules as R  # noqa: E402

def ts(text):
    return datetime.fromisoformat(text).timestamp()

def iso(value):
    return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M")

class TZ(unittest.TestCase):
    zone = "Europe/Berlin"

    def setUp(self):
        if not hasattr(time, "tzset"):

            try:
                zone = ZoneInfo(self.zone)
            except ZoneInfoNotFoundError:

                roots = [Path(sys.base_prefix) / "share" / "zoneinfo",
                         Path(sys.base_prefix) / "Library" / "share" / "zoneinfo"]
                source = next((root / self.zone for root in roots if (root / self.zone).is_file()), None)
                if source is None:
                    raise RuntimeError("These DST tests need IANA timezone data; install tzdata in the test environment")
                with source.open("rb") as stream:
                    zone = ZoneInfo.from_file(stream, key=self.zone)
            real_datetime = datetime

            class ZonedDateTime(real_datetime):
                def timestamp(self):
                    value = self.replace(tzinfo=zone) if self.tzinfo is None else self
                    return real_datetime.timestamp(value)

                @classmethod
                def fromtimestamp(cls, value, tz=None):
                    moment = super().fromtimestamp(value, zone if tz is None else tz)
                    return moment.replace(tzinfo=None) if tz is None else moment

                @classmethod
                def now(cls, tz=None):
                    moment = super().now(zone if tz is None else tz)
                    return moment.replace(tzinfo=None) if tz is None else moment

                def astimezone(self, tz=None):
                    value = self.replace(tzinfo=zone) if self.tzinfo is None else self
                    return real_datetime.astimezone(value, zone if tz is None else tz)

            from app import schedule_conds_ext, schedule_triggers_ext
            for target in (sys.modules[__name__], R, schedule_conds_ext, schedule_triggers_ext):
                scoped = patch.object(target, "datetime", ZonedDateTime)
                scoped.start()
                self.addCleanup(scoped.stop)
            return
        self._old = os.environ.get("TZ")
        os.environ["TZ"] = self.zone
        time.tzset()

    def tearDown(self):
        if not hasattr(time, "tzset"):
            return
        if self._old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._old
        time.tzset()

class Normalize(TZ):
    def test_daily_and_days(self):
        rule = R.normalize_rule({"when": {"type": "daily", "times": ["9:00", "16.30", "9:00"], "days": "пн,ср,fri"}})
        self.assertEqual(rule["when"][0], {"type": "daily", "times": ["09:00", "16:30"], "days": [1, 3, 5]})
        self.assertEqual(rule["options"]["catch_up"], "once")

        self.assertEqual(R.normalize_rule({"when": [{"type": "daily", "times": ["10:00"], "days": [1, 2, 3, 4, 5, 6, 7]}]})["when"][0]["days"], [])

    def test_errors_are_bilingual(self):
        for bad in ({}, {"when": []}, {"when": [{"type": "daily", "times": ["25:00"]}]},
                    {"when": [{"type": "interval", "every": 0}]}, {"when": [{"type": "event", "event": "nope"}]},
                    {"when": [{"type": "event", "event": "process_start"}]},
                    {"when": [{"type": "window", "from": "10:00", "to": "10:00"}]},
                    {"when": [{"type": "daily"}], "only_if": [{"type": "process", "name": "a/b:c"}]},
                    {"when": [{"type": "daily"}], "start": "2026-10-10", "until": {"date": "2026-10-01"}}):
            with self.assertRaises(R.RuleError) as ctx:
                R.normalize_rule(bad)
            self.assertTrue(ctx.exception.ru and ctx.exception.en)
            self.assertFalse(any("а" <= ch <= "я" for ch in ctx.exception.en.lower()))

    def test_interval_hours_and_until_day(self):
        rule = R.normalize_rule({"when": [{"type": "interval", "every": 2, "unit": "hours"}],
                                 "until": {"date": "2026-10-31", "max_runs": "5"}})
        self.assertEqual(rule["when"][0]["every"], 120)
        self.assertEqual(rule["until"]["date"], "2026-10-31T23:59")
        self.assertEqual(rule["until"]["max_runs"], 5)
        self.assertEqual(rule["options"]["catch_up"], "skip")

    def test_legacy_rule_view(self):
        self.assertEqual(R.legacy_rule({"daily_at": "08:15"})["when"][0]["times"], ["08:15"])
        self.assertEqual(R.legacy_rule({"interval_minutes": 30})["when"][0]["every"], 30)
        self.assertEqual(R.legacy_rule({"run_at": "2026-10-11T06:00:00"})["when"][0]["at"], "2026-10-11T06:00")
        self.assertIsNone(R.legacy_rule({}))

    def test_sanitize_drops_legacy_fields_and_disables_invalid(self):
        task = R.sanitize_task({"id": "a", "daily_at": "09:00", "rule": {"when": [{"type": "daily", "times": ["10:00"]}]}})
        self.assertNotIn("daily_at", task)
        bad = R.sanitize_task({"id": "b", "enabled": True, "rule": {"when": []}})
        self.assertFalse(bad["enabled"])
        self.assertEqual(bad["rule_state"]["status"], "invalid")
        legacy = {"id": "c", "daily_at": "09:00"}
        self.assertEqual(R.sanitize_task(dict(legacy)), legacy)

class Slots(TZ):
    def test_daily_weekdays(self):
        rule = R.normalize_rule({"when": [{"type": "daily", "times": ["16:00"], "days": [1, 2, 3, 4, 5]}]})

        slot = R.next_slot(rule, ts("2026-10-09T17:00"))
        self.assertEqual(iso(slot["start"]), "2026-10-12 16:00")
        self.assertEqual(iso(R.next_slot(rule, ts("2026-10-12T15:59"))["start"]), "2026-10-12 16:00")

    def test_interval_window_across_midnight(self):
        rule = R.normalize_rule({"when": [{"type": "interval", "every": 60, "from": "22:00", "to": "02:00"}]})
        got = [iso(x) for x in R.preview(rule, ts("2026-10-10T21:30"), 6)]
        self.assertEqual(got, ["2026-10-10 22:00", "2026-10-10 23:00", "2026-10-11 00:00",
                               "2026-10-11 01:00", "2026-10-11 22:00", "2026-10-11 23:00"])

    def test_interval_window_weekday_belongs_to_start_day(self):

        rule = R.normalize_rule({"when": [{"type": "interval", "every": 30, "from": "23:00", "to": "01:00", "days": [5]}]})
        got = [iso(x) for x in R.preview(rule, ts("2026-10-09T22:00"), 5)]
        self.assertEqual(got, ["2026-10-09 23:00", "2026-10-09 23:30", "2026-10-10 00:00",
                               "2026-10-10 00:30", "2026-10-16 23:00"])

    def test_tomorrow_6_to_10(self):
        rule = R.normalize_rule({"when": [{"type": "interval", "every": 60, "from": "06:00", "to": "10:00"}],
                                 "only_if": [{"type": "memuai_open", "value": True}],
                                 "start": "2026-10-11T00:00", "until": {"date": "2026-10-11"}})
        got = [iso(x) for x in R.preview(rule, ts("2026-10-10T14:00"), 10)]
        self.assertEqual(got, ["2026-10-11 06:00", "2026-10-11 07:00", "2026-10-11 08:00", "2026-10-11 09:00"])
        self.assertIn("11", R.summary(rule, "ru"))
        self.assertIn("открыт MemuAI", R.summary(rule, "ru"))

    def test_dst_spring_and_autumn(self):

        daily = R.normalize_rule({"when": [{"type": "daily", "times": ["09:00"]}]})
        a = R.next_slot(daily, ts("2026-03-28T10:00"))["start"]
        b = R.next_slot(daily, a)["start"]
        self.assertEqual(iso(a), "2026-03-29 09:00")
        self.assertEqual(iso(b), "2026-03-30 09:00")
        spring_before = R.next_slot(daily, ts("2026-03-27T10:00"))["start"]
        self.assertEqual(a - spring_before, 23 * 3600)
        self.assertEqual(b - a, 86400)
        c = R.next_slot(daily, ts("2026-10-23T10:00"))["start"]
        d = R.next_slot(daily, c)["start"]
        self.assertEqual(iso(d), "2026-10-25 09:00")
        self.assertEqual(d - c, 25 * 3600)

        night = R.normalize_rule({"when": [{"type": "daily", "times": ["02:30"]}]})
        slot = R.next_slot(night, ts("2026-03-28T12:00"))["start"]
        self.assertEqual(datetime.fromtimestamp(slot).date().isoformat(), "2026-03-29")

        grid = R.normalize_rule({"when": [{"type": "interval", "every": 60, "from": "01:00", "to": "05:00"}]})
        got = [iso(x) for x in R.preview(grid, ts("2026-10-25T00:00"), 4)]
        self.assertEqual(got[0], "2026-10-25 01:00")
        self.assertIn("2026-10-25 04:00", got)

    def test_interval_without_window_anchor_and_days(self):
        rule = R.normalize_rule({"when": [{"type": "interval", "every": 90, "days": [6, 7]}]})
        anchor = ts("2026-10-09T10:00")
        slot = R.next_slot(rule, ts("2026-10-09T10:00"), anchor=anchor)
        self.assertEqual(datetime.fromtimestamp(slot["start"]).isoweekday(), 6)
        self.assertEqual((slot["start"] - anchor) % (90 * 60), 0)
        plain = R.normalize_rule({"when": [{"type": "interval", "every": 15}]})
        self.assertEqual(R.next_slot(plain, anchor, anchor=anchor)["start"], anchor + 900)

    def test_interval_with_start_runs_at_start(self):
        rule = R.normalize_rule({"when": [{"type": "interval", "every": 30}], "start": "2026-10-11T08:00"})
        self.assertEqual(iso(R.next_slot(rule, ts("2026-10-10T12:00"))["start"]), "2026-10-11 08:00")
        self.assertEqual(iso(R.next_slot(rule, ts("2026-10-11T08:00"))["start"]), "2026-10-11 08:30")

    def test_monthly_last_and_short_months(self):
        last = R.normalize_rule({"when": [{"type": "monthly", "day": -1, "time": "20:00"}]})
        self.assertEqual(iso(R.next_slot(last, ts("2027-02-01T00:00"))["start"]), "2027-02-28 20:00")
        d31 = R.normalize_rule({"when": [{"type": "monthly", "day": 31, "time": "08:00"}]})
        self.assertEqual(iso(R.next_slot(d31, ts("2026-11-01T00:00"))["start"]), "2026-11-30 08:00")

    def test_once_and_until(self):
        once = R.normalize_rule({"when": [{"type": "once", "at": "2026-10-11T06:00"}]})
        self.assertEqual(iso(R.next_slot(once, ts("2026-10-10T00:00"))["start"]), "2026-10-11 06:00")
        self.assertIsNone(R.next_slot(once, ts("2026-10-11T06:00")))
        limited = R.normalize_rule({"when": [{"type": "daily", "times": ["09:00"]}], "until": {"date": "2026-10-12"}})
        self.assertEqual(len(R.preview(limited, ts("2026-10-10T10:00"), 10)), 2)

    def test_multiple_triggers_or(self):
        rule = R.normalize_rule({"when": [{"type": "daily", "times": ["18:00"]},
                                          {"type": "once", "at": "2026-10-10T15:00"}]})
        self.assertEqual([iso(x) for x in R.preview(rule, ts("2026-10-10T12:00"), 2)],
                         ["2026-10-10 15:00", "2026-10-10 18:00"])

class SampleConditions(TZ):
    def test_conditions(self):
        sample = {"processes": {"dota2", "steam"}, "memuai_open": True, "idle_seconds": 700, "network": True,
                  "power": "battery", "battery": 40, "focus": {"title": "Dota 2", "process": "dota2.exe"}}
        now = ts("2026-10-10T16:00")
        check = lambda c: R.check_condition(R._normalize_condition(c), sample, now)  # noqa: E731
        self.assertTrue(check({"type": "process", "name": "Dota2.EXE"}))
        self.assertTrue(check({"type": "process", "name": "discord.exe", "running": False}))
        self.assertTrue(check({"type": "idle", "minutes": 10}))
        self.assertFalse(check({"type": "idle", "minutes": 10, "value": False}))
        self.assertFalse(check({"type": "power", "value": "ac"}))
        self.assertTrue(check({"type": "battery", "op": "above", "percent": 30}))
        self.assertTrue(check({"type": "focus", "match": "dota"}))
        self.assertTrue(check({"type": "time", "from": "22:00", "to": "17:00"}))
        self.assertIsNone(R.check_condition(R._normalize_condition({"type": "idle"}), {}, now))

    def test_match_any_and_implicit_memuai(self):
        rule = R.normalize_rule({"when": [{"type": "daily"}], "match": "any",
                                 "only_if": [{"type": "process", "name": "a.exe"}, {"type": "process", "name": "b.exe"}]})
        self.assertTrue(R.conditions_hold(rule, {"processes": {"b"}}, 0)[0])
        self.assertFalse(R.conditions_hold(rule, {"processes": set()}, 0)[0])
        plain = R.normalize_rule({"when": [{"type": "daily"}]})
        ok, failed = R.conditions_hold(plain, {"memuai_open": False}, 0, "dfy")
        self.assertFalse(ok)
        self.assertTrue(failed["implicit"])

    def test_events_edges(self):
        t = lambda **kw: R._normalize_trigger(dict(type="event", **kw))  # noqa: E731
        start = t(event="process_start", process="dota2.exe")
        self.assertFalse(R.event_fired(start, None, {"processes": {"dota2"}}))
        self.assertTrue(R.event_fired(start, {"processes": set()}, {"processes": {"dota2"}}))
        self.assertFalse(R.event_fired(start, {"processes": {"dota2"}}, {"processes": {"dota2"}}))
        stop = t(event="process_stop", process="dota2")
        self.assertTrue(R.event_fired(stop, {"processes": {"dota2"}}, {"processes": set()}))
        idle = t(event="idle_start", minutes=5)
        self.assertTrue(R.event_fired(idle, {"idle_seconds": 290}, {"idle_seconds": 310}))
        back = t(event="idle_end", minutes=5)
        self.assertTrue(R.event_fired(back, {"idle_seconds": 900}, {"idle_seconds": 3}))
        self.assertTrue(R.event_fired(t(event="proxy_start"), None, {"first": True}))
        self.assertFalse(R.event_fired(t(event="pc_start"), None, {"first": True, "fresh_boot": False}))
        self.assertTrue(R.event_fired(t(event="battery_low", percent=20), {"battery": 21}, {"battery": 19}))
        files = t(event="file_changed", path="C:/x")
        key = R.folder_key(files)
        self.assertTrue(R.event_fired(files, {"folders": {key: "a"}}, {"folders": {key: "b"}}))
        self.assertFalse(R.event_fired(files, {"folders": {}}, {"folders": {key: "b"}}))

def task_of(rule, **extra):
    task = {"id": "t1", "name": "T", "enabled": True, "rule": R.normalize_rule(rule)}
    task.update(extra)
    return task

class Decide(TZ):
    def tick(self, task, now, sample=None, prev=None, **kw):
        return R.decide(task, ts(now) if isinstance(now, str) else now, sample or {}, prev, rng=lambda: 0.0, **kw)

    def test_dota_daily_skip(self):
        task = task_of({"when": [{"type": "daily", "times": ["16:00"]}],
                        "only_if": [{"type": "process", "name": "dota2.exe"}]})
        self.assertFalse(self.tick(task, "2026-10-10T15:00")["run"])
        self.assertEqual(iso(task["next_run"]), "2026-10-10 16:00")
        out = self.tick(task, "2026-10-10T16:00:10", {"processes": set()})
        self.assertFalse(out["run"])
        self.assertEqual(task["rule_state"]["status"], "blocked")
        self.assertIn("dota2.exe", task["rule_state"]["reason"]["ru"])

        self.tick(task, "2026-10-10T16:03", {"processes": set()})
        self.assertEqual(iso(task["next_run"]), "2026-10-11 16:00")
        out = self.tick(task, "2026-10-11T16:00:20", {"processes": {"dota2"}})
        self.assertTrue(out["run"])
        self.assertEqual(task["run_count"], 1)
        self.assertEqual(iso(task["next_run"]), "2026-10-12 16:00")

    def test_wait_policy_runs_when_condition_appears(self):
        task = task_of({"when": [{"type": "daily", "times": ["16:00"]}],
                        "only_if": [{"type": "process", "name": "dota2.exe"}],
                        "options": {"if_blocked": "wait", "wait_minutes": 60}})
        self.tick(task, "2026-10-10T15:00")
        self.assertFalse(self.tick(task, "2026-10-10T16:01", {"processes": set()})["run"])
        self.assertTrue(self.tick(task, "2026-10-10T16:40", {"processes": {"dota2"}})["run"])

    def test_window_once_as_soon_as_condition_holds(self):
        task = task_of({"when": [{"type": "window", "from": "06:00", "to": "10:00"}],
                        "only_if": [{"type": "memuai_open"}]})
        self.tick(task, "2026-10-10T05:00")
        self.assertFalse(self.tick(task, "2026-10-10T06:30", {"memuai_open": False})["run"])
        self.assertTrue(self.tick(task, "2026-10-10T08:10", {"memuai_open": True})["run"])
        self.assertFalse(self.tick(task, "2026-10-10T08:30", {"memuai_open": True})["run"])
        self.assertEqual(iso(task["next_run"]), "2026-10-11 06:00")

    def test_window_enabled_inside_window_runs_today(self):
        task = task_of({"when": [{"type": "window", "from": "06:00", "to": "10:00"}]})
        self.assertTrue(self.tick(task, "2026-10-10T07:00")["run"])

    def test_catch_up_once_after_downtime(self):
        task = task_of({"when": [{"type": "daily", "times": ["09:00"]}]})
        self.tick(task, "2026-10-10T08:00")

        out = self.tick(task, "2026-10-10T13:00")
        self.assertTrue(out["run"])
        self.assertTrue(out["late"])
        self.assertEqual(iso(task["next_run"]), "2026-10-11 09:00")
        skip = task_of({"when": [{"type": "daily", "times": ["09:00"]}], "options": {"catch_up": "skip"}})
        self.tick(skip, "2026-10-10T08:00")
        self.assertFalse(self.tick(skip, "2026-10-10T13:00")["run"])
        self.assertEqual(iso(skip["next_run"]), "2026-10-11 09:00")
        too_late = task_of({"when": [{"type": "daily", "times": ["09:00"]}], "options": {"catch_up_hours": 2}})
        self.tick(too_late, "2026-10-10T08:00")
        self.assertFalse(self.tick(too_late, "2026-10-10T13:00")["run"])

    def test_interval_downtime_runs_once_not_a_storm(self):
        task = task_of({"when": [{"type": "interval", "every": 5}]})
        self.tick(task, "2026-10-10T08:00")
        runs = sum(self.tick(task, ts("2026-10-10T12:00") + i * 30)["run"] for i in range(4))
        self.assertEqual(runs, 0)
        self.assertTrue(self.tick(task, task["next_run"] + 1)["run"])

    def test_max_runs_and_until(self):
        task = task_of({"when": [{"type": "interval", "every": 10}], "until": {"max_runs": 2}})
        self.tick(task, "2026-10-10T08:00")
        self.assertTrue(self.tick(task, task["next_run"] + 1)["run"])
        self.assertTrue(self.tick(task, task["next_run"] + 1)["run"])
        self.assertFalse(task["enabled"])
        self.assertEqual(task["rule_state"]["status"], "finished")
        expired = task_of({"when": [{"type": "daily", "times": ["09:00"]}], "until": {"date": "2026-10-09"}})
        self.tick(expired, "2026-10-10T08:00")
        self.assertFalse(expired["enabled"])

    def test_once_finishes(self):
        task = task_of({"when": [{"type": "once", "at": "2026-10-10T09:00"}]})
        self.tick(task, "2026-10-10T08:00")
        self.assertTrue(self.tick(task, "2026-10-10T09:00:05")["run"])
        self.assertFalse(task["enabled"])

    def test_event_process_start_with_delay_and_gap(self):
        task = task_of({"when": [{"type": "event", "event": "process_start", "process": "dota2.exe", "delay": 1}],
                        "options": {"min_gap_minutes": 30}})
        base = ts("2026-10-10T10:00")
        self.assertFalse(self.tick(task, base, {"processes": set()}, None)["run"])
        self.assertFalse(self.tick(task, base + 20, {"processes": {"dota2"}}, {"processes": set()})["run"])
        self.assertTrue(self.tick(task, base + 85, {"processes": {"dota2"}}, {"processes": {"dota2"}})["run"])

        self.tick(task, base + 200, {"processes": set()}, {"processes": {"dota2"}})
        self.tick(task, base + 230, {"processes": {"dota2"}}, {"processes": set()})
        out = self.tick(task, base + 300, {"processes": {"dota2"}}, {"processes": {"dota2"}})
        self.assertFalse(out["run"])
        self.assertIn("Пауза", task["rule_state"]["reason"]["ru"])

    def test_chain(self):
        first = {"id": "a", "name": "A", "enabled": True}
        task = task_of({"when": [{"type": "event", "event": "task_done", "task_id": "a", "status": "ok"}]}, id="b")
        by_id = {"a": first, "b": task}
        self.assertFalse(self.tick(task, "2026-10-10T08:00", tasks_by_id=by_id)["run"])
        R.note_finished(first, True, ts("2026-10-10T08:05"))
        self.assertTrue(self.tick(task, "2026-10-10T08:05:10", tasks_by_id=by_id)["run"])
        self.assertFalse(self.tick(task, "2026-10-10T08:06", tasks_by_id=by_id)["run"])
        R.note_finished(first, False, ts("2026-10-10T09:00"))
        self.assertFalse(self.tick(task, "2026-10-10T09:00:10", tasks_by_id=by_id)["run"])

    def test_chain_cycle_detected(self):
        a = task_of({"when": [{"type": "event", "event": "task_done", "task_id": "b"}]}, id="a")
        b = task_of({"when": [{"type": "event", "event": "task_done", "task_id": "a"}]}, id="b")
        c = task_of({"when": [{"type": "event", "event": "task_done", "task_id": "a"}]}, id="c")
        self.assertEqual(R.chain_cycles([a, b, c]), {"a", "b"})
        self.assertTrue(any("замыкается" in w for w in R.warnings(a, [a, b, c], "ru")))

    def test_run_now_is_a_test_run(self):
        task = task_of({"when": [{"type": "daily", "times": ["16:00"]}],
                        "only_if": [{"type": "process", "name": "dota2.exe"}]}, run_now=1)
        out = self.tick(task, "2026-10-10T10:00", {"processes": set()})
        self.assertTrue(out["run"] and out["test"])
        self.assertNotIn("run_now", task)
        self.assertNotIn("run_count", task)

    def test_max_failures(self):
        task = task_of({"when": [{"type": "interval", "every": 10}], "until": {"max_failures": 2}})
        R.note_finished(task, False, 1)
        self.assertTrue(task["enabled"])
        R.note_finished(task, False, 2)
        self.assertFalse(task["enabled"])

    def test_jitter_inside_window(self):
        task = task_of({"when": [{"type": "daily", "times": ["09:00"]}], "options": {"jitter_minutes": 10}})
        R.decide(task, ts("2026-10-10T08:00"), {}, None, rng=lambda: 0.5)
        self.assertEqual(iso(task["next_run"]), "2026-10-10 09:05")

    def test_invalid_rule_never_runs(self):
        task = {"id": "x", "enabled": True, "rule": {"when": "garbage"}}
        self.assertFalse(R.decide(task, 0, {}, None)["run"])
        self.assertEqual(task["rule_state"]["status"], "invalid")

class Texts(TZ):
    def test_summaries(self):
        rule = {"when": [{"type": "daily", "times": ["16:00"]}], "only_if": [{"type": "process", "name": "dota2.exe"}]}
        self.assertEqual(R.summary(rule, "ru"), "Каждый день в 16:00, только если запущен dota2.exe")
        self.assertEqual(R.summary(rule, "en"), "Every day at 16:00, only if dota2.exe is running")
        weekdays = {"when": [{"type": "daily", "times": ["09:00"], "days": [1, 2, 3, 4, 5]}], "until": {"max_runs": 3}}
        self.assertEqual(R.summary(weekdays, "ru"), "По будням в 09:00, не более 3 раз")
        for event in R.EVENTS:
            trig = {"type": "event", "event": event, "process": "x.exe", "path": "C:/x", "match": "x", "task_id": "z"}
            text = R.summary({"when": [trig]}, "en")
            self.assertFalse(any("а" <= ch <= "я" for ch in text.lower()), text)

    def test_parse_model_json(self):
        data = R.parse_model_json('<think>{"x":1}</think>Sure:\n```json\n{"name":"A","rule":{"when":[{"type":"daily","times":["16:00"]}]},"prompt":"p"}\n```')
        draft = R.draft_from_model(data)
        self.assertEqual(draft["rule"]["when"][0]["times"], ["16:00"])
        with self.assertRaises(R.RuleError):
            R.draft_from_model({"error": "not a schedule"})
        self.assertEqual(R.draft_from_model({"kind": "reminder", "prompt": "x", "rule": {"when": [{"type": "daily"}]}})["kind"], "notify")

    def test_overlap_warning(self):
        a = task_of({"when": [{"type": "daily", "times": ["09:00"]}]}, id="a", name="Alpha")
        b = task_of({"when": [{"type": "daily", "times": ["09:00"]}]}, id="b", kind="dfy")
        legacy = {"id": "c", "name": "Old", "enabled": True, "daily_at": "09:00"}
        warns = R.warnings(b, [a, b, legacy], "en", now=ts("2026-10-10T08:00"))
        self.assertTrue(any("Alpha" in w for w in warns))
        self.assertTrue(any("Old" in w for w in warns))
        self.assertTrue(any("MemuAI window" in w for w in warns))

if __name__ == "__main__":
    unittest.main()
