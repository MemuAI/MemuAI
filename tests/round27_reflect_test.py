"""Round 27: «Рефлексия» (Reflect) and «Время и фокус» — app/reflect.py, reflect.js, quick/plugins/focus.js.

Offline: the model is a scripted coroutine, no network. Covers the deterministic statistics, periods,
sessions, what is read (temporary chats, attachments, internal rows, Quick Ask / Telegram switches),
the privacy rules for sensitive topics, strict JSON extraction/validation with retries, map → reduce,
the cache, feedback and every endpoint, quiet hours (Python and the JS copy agree) and the break tracker.
Headless screenshots: tests/round27_reflect_shots.py.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import pgdata, reflect
from tests.round27_reflect_data import (ATTACHMENT_MARKER, CRISIS_MARKER, HEALTH_MARKER, TEMP_MARKER,
                                        make_history)

ROOT = Path(__file__).resolve().parents[1]
TZ = timezone(timedelta(hours=3))

def ms(*args) -> int:
    return int(datetime(*args, tzinfo=TZ).timestamp() * 1000)

def conv(cid, rows, source="chat", title="t"):
    return {"id": cid, "title": title, "source": source,
            "messages": [{"role": r, "text": t, "ts": ts} for r, t, ts in rows]}

GOOD_FINAL = {
    "headline": "Код MemuAI, испанский и поездка",
    "summary": "Больше всего вы чинили FastAPI и писали тесты. Параллельно учили испанский. Планировали Тбилиси.",
    "topics": [{"name": "Код MemuAI", "description": "FastAPI, тесты", "percent": 50},
               {"name": "Испанский", "description": "грамматика", "percent": 30},
               {"name": "Путешествия", "description": "Тбилиси", "percent": 20}],
    "skills": {k: {"observation": f"obs {k}", "next_step": f"step {k}", "action": "project"} for k in reflect.SKILLS},
}

class Scripted:
    """A fake ``proxy_completion``: answers from a list (or a function) and records every prompt."""

    def __init__(self, answers=None, fn=None):
        self.answers = list(answers or [])
        self.fn = fn
        self.calls = []

    async def __call__(self, model, messages):
        self.calls.append((model, messages))
        if self.fn:
            return self.fn(model, messages)
        item = self.answers.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

def run(coro):
    return asyncio.run(coro)

class DataDir:
    """Writes chats into the test data dir and restores the previous files afterwards."""
    names = ("chats.json", "quick_chats.json", "reflect.json", "preferences.json")

    def __enter__(self):
        self.dir = Path(pgdata.MEMU_DIR)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.saved = {n: (self.dir / n).read_bytes() if (self.dir / n).exists() else None for n in self.names}
        for n in self.names:
            (self.dir / n).unlink(missing_ok=True)
        return self

    def write(self, chats=None, quick=None):
        if chats is not None:
            (self.dir / "chats.json").write_text(json.dumps(chats, ensure_ascii=False), encoding="utf-8")
        if quick is not None:
            (self.dir / "quick_chats.json").write_text(json.dumps(quick, ensure_ascii=False), encoding="utf-8")

    def __exit__(self, *exc):
        for n, data in self.saved.items():
            if data is None:
                (self.dir / n).unlink(missing_ok=True)
            else:
                (self.dir / n).write_bytes(data)

class PeriodsAndStats(unittest.TestCase):
    def test_period_bounds(self):
        now = datetime(2026, 10, 9, 12, 3, tzinfo=TZ)
        self.assertEqual(reflect.period_bounds("this_month", now), (datetime(2026, 10, 1, tzinfo=TZ), now))
        self.assertEqual(reflect.period_bounds("last_month", now), (datetime(2026, 9, 1, tzinfo=TZ), datetime(2026, 10, 1, tzinfo=TZ)))
        self.assertEqual(reflect.period_bounds("3m", now)[0], datetime(2026, 7, 9, tzinfo=TZ))
        self.assertEqual(reflect.period_bounds("12m", now)[0], datetime(2025, 10, 9, tzinfo=TZ))

        jan = datetime(2027, 1, 15, tzinfo=TZ)
        self.assertEqual(reflect.period_bounds("last_month", jan)[0], datetime(2026, 12, 1, tzinfo=TZ))
        self.assertEqual(reflect.period_bounds("3m", datetime(2026, 5, 31, tzinfo=TZ))[0], datetime(2026, 2, 28, tzinfo=TZ))
        with self.assertRaises(ValueError):
            reflect.period_bounds("week", now)

    def test_sessions_and_time(self):
        base = ms(2026, 9, 1, 10, 0)
        stamps = [base, base + 5 * 60_000, base + 15 * 60_000,
                  base + 60 * 60_000,
                  base + 130 * 60_000, base + 149 * 60_000]
        spans = reflect.sessions(stamps)
        self.assertEqual(len(spans), 3)
        self.assertEqual([round(reflect.session_minutes(s)) for s in spans], [17, 1, 21])

    def test_compute_stats(self):
        start, end = datetime(2026, 9, 1, tzinfo=TZ), datetime(2026, 10, 1, tzinfo=TZ)
        convs = [
            conv("a", [("user", "x", ms(2026, 9, 1, 14, 5)), ("assistant", "y", ms(2026, 9, 1, 14, 7)),
                       ("user", "x", ms(2026, 9, 1, 14, 20))]),
            conv("b", [("user", "x", ms(2026, 9, 8, 14, 40))]),
            conv("c", [("user", "x", ms(2026, 9, 3, 9, 0)), ("user", "x", ms(2026, 9, 4, 9, 0))]),
        ]
        stats = reflect.compute_stats(convs, start, end)
        self.assertEqual(stats["conversations"], 3)
        self.assertEqual(stats["user_messages"], 5)
        self.assertEqual(stats["days"], 30)
        self.assertEqual(len(stats["series"]), 30)
        self.assertEqual(stats["active_days"], 4)
        self.assertEqual(stats["most_active_weekday"], 1)
        self.assertEqual(stats["peak_hour"], 14)
        self.assertEqual(stats["series"][0], {"date": "2026-09-01", "conversations": 1, "messages": 2, "minutes": 17.0})
        self.assertEqual(stats["busiest_day"]["date"], "2026-09-01")
        self.assertEqual(stats["time"]["sessions"], 4)
        self.assertEqual(stats["time"]["minutes"], 17 + 1 + 1 + 1)

    def test_empty_stats(self):
        start, end = datetime(2026, 9, 1, tzinfo=TZ), datetime(2026, 10, 1, tzinfo=TZ)
        stats = reflect.compute_stats([], start, end)
        self.assertEqual((stats["conversations"], stats["most_active_weekday"], stats["peak_hour"], stats["busiest_day"]), (0, None, None, None))
        self.assertEqual(stats["time"]["minutes"], 0)

    def test_in_period_cuts_messages(self):
        start, end = datetime(2026, 9, 1, tzinfo=TZ), datetime(2026, 10, 1, tzinfo=TZ)
        old = conv("o", [("user", "x", ms(2026, 8, 31, 23, 59)), ("assistant", "y", ms(2026, 9, 1, 0, 1))])
        mixed = conv("m", [("user", "x", ms(2026, 8, 20, 10)), ("user", "x", ms(2026, 9, 2, 10))])
        result = reflect.in_period([old, mixed], start, end)
        self.assertEqual([c["id"] for c in result], ["m"])
        self.assertEqual(len(result[0]["messages"]), 1)

class Collect(unittest.TestCase):
    def test_what_is_read(self):
        with DataDir() as data:
            now = datetime.now().astimezone()
            chats, quick = make_history(now)
            chats.append({"id": "chat-int", "title": "i", "updated": 1, "messages": [
                {"role": "user", "content": "видимый", "createdAt": ms(2026, 9, 2, 10)},
                {"role": "user", "internal": True, "content": "ВНУТРЕННЕЕ", "createdAt": ms(2026, 9, 2, 10)},
                {"role": "tool", "content": "ИНСТРУМЕНТ", "createdAt": ms(2026, 9, 2, 10)},
                {"role": "user", "content": [{"type": "text", "text": "часть"}, {"type": "image_url", "image_url": {"url": "data:x"}}],
                 "createdAt": ms(2026, 9, 2, 11)}]})
            data.write(chats, quick)
            prefs = dict(reflect.PREF_DEFAULTS)
            convs = reflect.collect_conversations(prefs)
            text = json.dumps(convs, ensure_ascii=False)
            for marker in (TEMP_MARKER, ATTACHMENT_MARKER, "ВНУТРЕННЕЕ", "ИНСТРУМЕНТ", "data:x"):
                self.assertNotIn(marker, text)
            self.assertIn("часть", text)
            sources = {c["source"] for c in convs}
            self.assertTrue({"chat", "work", "quick", "telegram"} <= sources)
            prefs.update(reflect_include_quick=False, reflect_include_telegram=False)
            sources = {c["source"] for c in reflect.collect_conversations(prefs)}
            self.assertNotIn("quick", sources)
            self.assertNotIn("telegram", sources)

    def test_injected_blocks_are_not_user_words(self):
        self.assertEqual(reflect._user_words("Перескажи <attached_file name='a'>СЕКРЕТ</attached_file> коротко"), "Перескажи коротко")

class Privacy(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(reflect.sensitivity("Как настроить ракету в KSP"), 0)
        self.assertEqual(reflect.sensitivity("зависимости в package.json"), 0)
        self.assertEqual(reflect.sensitivity("мои анализы крови"), 1)
        self.assertEqual(reflect.sensitivity("у мамы рак"), 1)
        self.assertEqual(reflect.sensitivity("я не хочу жить"), 2)
        self.assertEqual(reflect.sensitivity("I think I have depression"), 2)

    def test_digest_withholds_sensitive(self):
        now = datetime.now().astimezone()
        chats, quick = make_history(now)
        with DataDir() as data:
            data.write(chats, quick)
            snap = reflect.snapshot("3m", dict(reflect.PREF_DEFAULTS))
        lines, withheld = reflect.digest_lines(snap["conversations"])
        text = "\n".join(lines)
        self.assertEqual(withheld, 2)
        self.assertEqual(snap["sensitive"], 2)
        for marker in (CRISIS_MARKER, HEALTH_MARKER, TEMP_MARKER, ATTACHMENT_MARKER):
            self.assertNotIn(marker, text)
        self.assertTrue(all(len(line) < 520 for line in lines))

    def test_apply_privacy(self):
        report = reflect.validate_final({**GOOD_FINAL,
            "headline": "Месяц, когда была депрессия",
            "summary": "Вы много говорили о депрессии. Ещё вы писали код. И учили испанский.",
            "topics": [{"name": "Тревога и депрессия", "description": "личное", "percent": 40}, *GOOD_FINAL["topics"]]})
        out = reflect.apply_privacy(report, "ru")
        self.assertNotIn("депресс", out["headline"].lower())
        self.assertTrue(out["summary"].startswith("Ещё вы писали код"))
        self.assertEqual([t["name"] for t in out["topics"]], ["Код MemuAI", "Испанский", "Путешествия"])
        self.assertEqual(sum(t["percent"] for t in out["topics"]), 100)

    def test_percents(self):
        self.assertEqual(sum(reflect.percents([1, 1, 1])), 100)
        self.assertEqual(reflect.percents([50, 30, 20]), [50, 30, 20])
        self.assertEqual(reflect.percents([0, 0]), [50, 50])
        self.assertEqual(sum(reflect.percents([33.3, 33.3, 33.4, 0.01])), 100)

class StrictJson(unittest.TestCase):
    def test_extract(self):
        self.assertEqual(reflect.extract_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(reflect.extract_json('<think>{"draft": true}</think>Вот: {"a": "}{"} спасибо'), {"a": "}{"})
        self.assertEqual(reflect.extract_json('<ds_safety>safe {x}</ds_safety>{"a": 2}'), {"a": 2})
        self.assertEqual(reflect.extract_json('{"a": 3}<ds_safety>unterminated {'), {"a": 3})
        self.assertEqual(reflect.extract_json('broken {"a": } then {"b": [1, {"c": "\\""}]}'), {"b": [1, {"c": '"'}]})
        with self.assertRaises(reflect.ReportError):
            reflect.extract_json("нет JSON")

    def test_validate(self):
        out = reflect.validate_final(GOOD_FINAL)
        self.assertEqual(set(out["skills"]), set(reflect.SKILLS))
        bad = json.loads(json.dumps(GOOD_FINAL))
        del bad["skills"]["diligence"]
        with self.assertRaisesRegex(reflect.ReportError, "diligence"):
            reflect.validate_final(bad)
        bad = {**GOOD_FINAL, "topics": [{"name": "x", "percent": "много"}]}
        with self.assertRaises(reflect.ReportError):
            reflect.validate_final(bad)
        weird = json.loads(json.dumps(GOOD_FINAL))
        weird["skills"]["delegation"]["action"] = "rm -rf"
        weird["summary"] = "x" * 2000
        out = reflect.validate_final(weird)
        self.assertEqual(out["skills"]["delegation"]["action"], "none")
        self.assertLessEqual(len(out["summary"]), 701)

    def test_retry_then_success(self):
        model = Scripted(["Конечно! Вот отчёт без JSON", "{\"headline\": 1}", json.dumps(GOOD_FINAL)])
        out = run(reflect._ask_json(model, "m", [{"role": "user", "content": "go"}], reflect.validate_final))
        self.assertEqual(out["headline"], GOOD_FINAL["headline"])
        self.assertEqual(len(model.calls), 3)
        self.assertIn("not valid", model.calls[1][1][-1]["content"])

    def test_retry_gives_up(self):
        model = Scripted(["нет", "нет", "нет"])
        with self.assertRaises(reflect.ReportError):
            run(reflect._ask_json(model, "m", [{"role": "user", "content": "go"}], reflect.validate_final))

class Generation(unittest.TestCase):
    def snap(self):
        now = datetime.now().astimezone()
        chats, quick = make_history(now)
        with DataDir() as data:
            data.write(chats, quick)
            return reflect.snapshot("3m", dict(reflect.PREF_DEFAULTS))

    def test_single_call(self):
        snap = self.snap()
        model = Scripted([json.dumps(GOOD_FINAL)])
        report = run(reflect.generate_report(snap, "en", "m", model))
        self.assertEqual(len(model.calls), 1)
        system, user = model.calls[0][1][0]["content"], model.calls[0][1][1]["content"]
        self.assertIn("Write in English", system)
        self.assertIn("withheld", user)
        for marker in (CRISIS_MARKER, HEALTH_MARKER, TEMP_MARKER, ATTACHMENT_MARKER):
            self.assertNotIn(marker, user)
        self.assertEqual(sum(t["percent"] for t in report["topics"]), 100)
        self.assertLess(len(user), reflect.DIGEST_CHUNK_CHARS + 6000)

    def test_map_reduce(self):
        snap = self.snap()
        stages = []

        def answer(model, messages):
            return json.dumps({"topics": [{"name": "Код", "description": "d", "conversations": 5}], "notes": ["n"]}) \
                if "Digest part" in messages[-1]["content"] else json.dumps(GOOD_FINAL)
        model = Scripted(fn=answer)
        old = reflect.DIGEST_CHUNK_CHARS
        reflect.DIGEST_CHUNK_CHARS = 2500
        try:
            run(reflect.generate_report(snap, "ru", "m", model, lambda s, d, t: stages.append((s, d, t))))
        finally:
            reflect.DIGEST_CHUNK_CHARS = old
        maps = [c for c in model.calls if "Digest part" in c[1][-1]["content"]]
        self.assertGreater(len(maps), 2)
        self.assertEqual(len(model.calls), len(maps) + 1)
        self.assertIn('"Код"', model.calls[-1][1][-1]["content"])
        self.assertEqual(stages[-1][0], "writing")
        self.assertEqual(stages[-1][1], stages[-1][2])

    def test_question_rotates_weekly(self):
        a = reflect.question_for(datetime(2026, 10, 5).date())
        self.assertEqual(a, reflect.question_for(datetime(2026, 10, 11).date()))
        self.assertNotEqual(a, reflect.question_for(datetime(2026, 10, 12).date()))
        self.assertTrue(reflect.question_for(lang="en")["text"].endswith("?"))

class Endpoints(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        self.data = DataDir().__enter__()
        chats, quick = make_history(datetime.now().astimezone())
        self.data.write(chats, quick)
        self.model = Scripted(fn=lambda m, msgs: json.dumps(GOOD_FINAL))
        app = FastAPI()
        reflect.register(app, completion=self.model)
        self.app = app
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.data.__exit__(None, None, None)

    def wait(self, period="3m", lang="ru"):
        for _ in range(100):
            data = self.client.get(f"/api/reflect?period={period}&lang={lang}").json()
            if not data.get("job") or data["job"]["status"] != "running":
                return data
            time.sleep(0.05)
        self.fail("job did not finish")

    def test_full_flow(self):
        first = self.client.get("/api/reflect?period=3m&lang=ru").json()
        self.assertEqual(first["state"], "ready")
        self.assertIsNone(first["report"])
        expected = len(reflect.snapshot("3m")["conversations"])
        self.assertGreater(expected, 50)
        self.assertEqual(first["stats"]["conversations"], expected)
        self.assertEqual(first["support"]["title"], reflect.SUPPORT["ru"]["title"])
        self.assertIsNone(first["job"])
        job = self.client.post("/api/reflect/generate", json={"period": "3m", "lang": "ru", "model": "demo"}).json()["job"]
        self.assertEqual(job["status"], "running")
        done = self.wait()
        self.assertEqual(done["job"]["status"], "done")
        self.assertEqual(done["report"]["headline"], GOOD_FINAL["headline"])
        self.assertFalse(done["stale"])
        self.assertEqual(done["model"], "demo")

        calls = len(self.model.calls)
        again = self.client.get("/api/reflect?period=3m&lang=ru").json()
        self.assertEqual(again["report"], done["report"])
        self.assertEqual(len(self.model.calls), calls)

        self.assertIsNone(self.client.get("/api/reflect?period=3m&lang=en").json()["report"])

        self.client.post("/api/reflect/feedback", json={"period": "3m", "lang": "ru", "generated_at": done["generated_at"], "value": "up"})
        self.assertEqual(self.client.get("/api/reflect?period=3m&lang=ru").json()["feedback"], "up")
        self.assertEqual(self.client.post("/api/reflect/feedback", json={"value": "maybe"}).status_code, 400)
        store = json.loads((Path(pgdata.MEMU_DIR) / "reflect.json").read_text(encoding="utf-8"))
        self.assertIn("3m:ru", store["reports"])

        chats = json.loads((Path(pgdata.MEMU_DIR) / "chats.json").read_text(encoding="utf-8"))
        chats[0]["messages"].append({"role": "user", "content": "ещё вопрос", "createdAt": int(time.time() * 1000) - 1000})
        self.data.write(chats)
        self.assertTrue(self.client.get("/api/reflect?period=3m&lang=ru").json()["stale"])

        self.client.post("/api/reflect/clear")
        self.assertIsNone(self.client.get("/api/reflect?period=3m&lang=ru").json()["report"])

    def test_disabled(self):
        reflect.mutate_store(lambda d: d["reports"].update({"3m:ru": {"report": {}}}))
        pgdata.save_preferences({"reflect_enabled": False})
        self.assertEqual(self.client.get("/api/reflect").json()["enabled"], False)
        self.assertFalse((Path(pgdata.MEMU_DIR) / "reflect.json").exists())
        self.assertEqual(self.client.post("/api/reflect/generate", json={"model": "m"}).status_code, 409)

    def test_no_model(self):
        pgdata.save_preferences({"default_model": "", "reflect_model": ""})
        r = self.client.post("/api/reflect/generate", json={"period": "3m", "lang": "en"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("model", r.json()["error"])

    def test_empty_and_few(self):
        self.data.write([], [])
        data = self.client.get("/api/reflect?period=this_month").json()
        self.assertEqual(data["state"], "empty")
        now = int(time.time() * 1000)
        self.data.write([{"id": "c1", "title": "t", "updated": now, "messages": [{"role": "user", "content": "привет", "createdAt": now - 5000}]}], [])
        data = self.client.get("/api/reflect?period=this_month").json()
        self.assertEqual((data["state"], data["stats"]["conversations"]), ("few", 1))
        self.client.post("/api/reflect/generate", json={"period": "this_month", "model": "m"})
        done = self.wait("this_month")
        self.assertEqual(done["job"]["status"], "error")
        self.assertIn("мало", done["job"]["error"])
        self.assertEqual(self.model.calls, [])

    def test_model_failure_is_friendly(self):
        self.app.state.reflect_completion = Scripted(fn=lambda m, msgs: (_ for _ in ()).throw(RuntimeError("HTTP 429 rate limit")))
        self.client.post("/api/reflect/generate", json={"period": "3m", "lang": "en", "model": "m"})
        done = self.wait("3m", "en")
        self.assertEqual(done["job"]["status"], "error")
        self.assertIn("limit", done["job"]["error"])
        self.assertIn("429", done["job"]["detail"])
        self.assertIsNone(done["report"])

    def test_bad_json_is_friendly(self):
        self.app.state.reflect_completion = Scripted(fn=lambda m, msgs: "<think>…</think> не JSON")
        self.client.post("/api/reflect/generate", json={"period": "3m", "lang": "ru", "model": "m"})
        done = self.wait()
        self.assertIn("не в том формате", done["job"]["error"])

    def test_one_job_per_period(self):
        gate = asyncio.Event()

        async def slow(model, messages):
            await gate.wait()
            return json.dumps(GOOD_FINAL)
        self.app.state.reflect_completion = slow
        a = self.client.post("/api/reflect/generate", json={"period": "3m", "model": "m"}).json()["job"]
        b = self.client.post("/api/reflect/generate", json={"period": "3m", "model": "m"}).json()["job"]
        self.assertEqual(a["id"], b["id"])
        self.client.portal.call(gate.set)
        self.assertEqual(self.wait()["job"]["status"], "done")

    def test_focus_routes(self):
        pgdata.save_preferences({"focus_break_enabled": True, "focus_break_minutes": 10})
        state = self.client.get("/api/focus/state").json()
        self.assertTrue(state["break_enabled"])
        tracker = self.app.state.focus_tracker
        self.assertEqual(tracker.path, reflect.focus_store_path())
        tracker.activity(10, True)
        tracker._update(lambda data: data.update(used=11 * 60, last=time.time() - 30, snooze_until=0, dismissed=False))
        out = self.client.post("/api/focus/activity", json={}).json()
        self.assertTrue(out["break_due"])
        self.assertGreaterEqual(out["minutes_today"], 11)
        self.assertFalse(self.client.post("/api/focus/activity", json={}).json()["break_due"])
        self.assertEqual(self.client.post("/api/focus/break", json={"action": "boom"}).status_code, 400)
        self.client.post("/api/focus/break", json={"action": "dismiss"})
        self.assertTrue(json.loads(reflect.focus_store_path().read_text(encoding="utf-8"))["dismissed"])

class Focus(unittest.TestCase):
    PREFS = {"focus_quiet_enabled": True, "focus_quiet_days": [0, 1, 2, 3, 4], "focus_quiet_start": "22:00", "focus_quiet_end": "07:00"}

    def test_quiet_overnight(self):
        q = reflect.quiet_now
        self.assertIsNotNone(q(self.PREFS, datetime(2026, 10, 9, 23, 0)))
        self.assertIsNotNone(q(self.PREFS, datetime(2026, 10, 10, 6, 59)))
        self.assertIsNone(q(self.PREFS, datetime(2026, 10, 10, 23, 0)))
        self.assertIsNone(q(self.PREFS, datetime(2026, 10, 12, 6, 0)))
        self.assertIsNone(q(self.PREFS, datetime(2026, 10, 9, 7, 0)))
        info = q(self.PREFS, datetime(2026, 10, 9, 23, 0))
        self.assertEqual(info["until"], "07:00")
        self.assertEqual(info["ends_at"], int(datetime(2026, 10, 10, 7, 0).timestamp() * 1000))

    def test_quiet_daytime_and_off(self):
        prefs = {**self.PREFS, "focus_quiet_start": "13:00", "focus_quiet_end": "14:30"}
        self.assertIsNotNone(reflect.quiet_now(prefs, datetime(2026, 10, 9, 14, 0)))
        self.assertIsNone(reflect.quiet_now(prefs, datetime(2026, 10, 9, 14, 30)))
        self.assertIsNone(reflect.quiet_now({**prefs, "focus_quiet_enabled": False}, datetime(2026, 10, 9, 14, 0)))
        self.assertIsNone(reflect.quiet_now({**prefs, "focus_quiet_days": []}, datetime(2026, 10, 9, 14, 0)))
        self.assertIsNone(reflect.quiet_now({**prefs, "focus_quiet_end": "13:00"}, datetime(2026, 10, 9, 13, 0)))

    def test_js_quiet_matches_python(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        cases = []
        for prefs in (self.PREFS, {**self.PREFS, "focus_quiet_start": "09:15", "focus_quiet_end": "17:45", "focus_quiet_days": [5, 6]}):
            for day in range(7, 15):
                for hour in (0, 6, 7, 9, 12, 17, 18, 21, 22, 23):
                    moment = datetime(2026, 9, day, hour, 30)
                    expected = reflect.quiet_now(prefs, moment)
                    cases.append({"prefs": prefs, "t": [2026, 8, day, hour, 30], "expected": expected})
        script = """
global.window = {}; global.document = {readyState: 'complete', getElementById: () => null, addEventListener() {}, querySelector: () => null, querySelectorAll: () => []};
require(process.argv[1]);
const cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const out = cases.map((c) => window.MemuFocus.quietNow(c.prefs, new Date(...c.t)));
process.stdout.write(JSON.stringify(out));
"""
        result = subprocess.run([node, "-e", script, str(ROOT / "app/pgstatic/reflect.js")], input=json.dumps(cases),
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        got = json.loads(result.stdout)
        self.assertEqual(got, [c["expected"] for c in cases])

    def test_tracker_counts_the_day(self):

        clock = [datetime(2026, 10, 9, 9, 0).timestamp()]
        t = reflect.FocusTracker(clock=lambda: clock[0])
        self.assertEqual(t.activity(30, True)["minutes_today"], 1)
        for _ in range(3):
            clock[0] += 5 * 60
            self.assertFalse(t.activity(30, True)["break_due"])
        clock[0] += 3 * 3600
        out = t.activity(30, True)
        self.assertEqual(out["minutes_today"], 17)
        self.assertFalse(out["break_due"])
        for _ in range(3):
            clock[0] += 5 * 60
            out = t.activity(30, True)
        self.assertTrue(out["break_due"])
        self.assertEqual(out["minutes_today"], 32)
        clock[0] += 60
        self.assertFalse(t.activity(30, True)["break_due"])
        t.action("snooze")
        clock[0] += 9 * 60
        self.assertFalse(t.activity(30, True)["break_due"])
        clock[0] += 2 * 60
        self.assertTrue(t.activity(30, True)["break_due"])
        t.action("break")
        clock[0] += 15 * 60
        for _ in range(6):
            clock[0] += 5 * 60
            self.assertFalse(t.activity(30, True)["break_due"])
        clock[0] += 5 * 60
        self.assertTrue(t.activity(30, True)["break_due"])
        t.action("dismiss")
        for _ in range(30):
            clock[0] += 5 * 60
            if datetime.fromtimestamp(clock[0]).date() != datetime(2026, 10, 9).date():
                break
            self.assertFalse(t.activity(30, True)["break_due"])
        clock[0] = datetime(2026, 10, 10, 8, 0).timestamp()
        self.assertEqual(t.activity(30, True)["minutes_today"], 1)
        self.assertFalse(t.activity(30, False)["break_due"])

    def test_tracker_is_shared_through_the_file(self):

        path = Path(tempfile.mkdtemp(prefix="r27-focus-")) / "focus.json"
        clock = [datetime(2026, 10, 9, 20, 0).timestamp()]
        window = reflect.FocusTracker(clock=lambda: clock[0], path=path)
        bot = reflect.FocusTracker(clock=lambda: clock[0], path=path)
        window.activity(20, True)
        for i in range(3):
            clock[0] += 5 * 60
            self.assertFalse((bot if i % 2 else window).activity(20, True)["break_due"])
        clock[0] += 5 * 60
        out = bot.activity(20, True)
        self.assertTrue(out["break_due"])
        self.assertFalse(window.activity(20, True)["break_due"])
        bot.action("dismiss")
        self.assertTrue(json.loads(path.read_text(encoding="utf-8"))["dismissed"])
        path.write_text("{broken", encoding="utf-8")
        self.assertEqual(window.activity(20, True)["minutes_today"], 1)
        shutil.rmtree(path.parent, ignore_errors=True)

    def test_prefs_normalized(self):
        out = reflect.normalize_prefs({"focus_quiet_days": [3, "1", 9, 3, "x"], "focus_quiet_start": "7:5",
                                       "focus_quiet_end": "7:05", "focus_break_minutes": "1000", "reflect_enabled": "0"})
        self.assertEqual(out["focus_quiet_days"], [1, 3])
        self.assertEqual((out["focus_quiet_start"], out["focus_quiet_end"]), ("22:00", "07:05"))
        self.assertEqual(out["focus_break_minutes"], reflect.BREAK_MAX)
        self.assertFalse(out["reflect_enabled"])
        saved = pgdata.load_preferences()
        for key in reflect.PREF_DEFAULTS:
            self.assertIn(key, saved)

class Wiring(unittest.TestCase):
    def test_hooks(self):
        app_js = (ROOT / "app/pgstatic/app.js").read_text(encoding="utf-8")
        index = (ROOT / "app/pgstatic/index.html").read_text(encoding="utf-8")
        self.assertIn('P("reflect", "you"', app_js)
        self.assertIn('P("focus", "you"', app_js)
        self.assertIn("MemuFocus.beforeSend()", app_js)
        self.assertIn('MemuReflect.mount(reflectSettingsContext())', app_js)
        self.assertLess(index.index("/settings.css"), index.index("/reflect.css"))
        self.assertLess(index.index("/reflect.js"), index.index("/app.js"))
        plugins = json.loads((ROOT / "app/pgstatic/quick/plugins/index.json").read_text(encoding="utf-8"))["plugins"]
        self.assertTrue(any(p.get("id") == "focus" and p.get("src") == "focus.js" for p in plugins))
        api = (ROOT / "app/playground_api.py").read_text(encoding="utf-8")
        self.assertIn("reflect.register(app, completion=proxy_completion)", api)
        for name in ("reflect.js", "quick/plugins/focus.js"):
            check = subprocess.run(["node", "--check", str(ROOT / "app/pgstatic" / name)], capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stderr)

if __name__ == "__main__":
    unittest.main()
