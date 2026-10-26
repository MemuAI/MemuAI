"""Round 29 phase 2 wave C (r29c-scheduling): Planning 2.0 on the phone, in Quick Access and wording fixes.

Each test names the bug or feature it guards (see .phase2/r29c-scheduling.md)."""
from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

from app import schedule_rules as rules  # noqa: E402
from app import tg_webapp, tgapp_extras  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "123456:TEST-token_for-plan"
USER = 5151

MODEL_JSON = json.dumps({"name": "Новости ИИ", "kind": "chat", "prompt": "Собери короткую сводку новостей об ИИ",
                         "rule": {"when": [{"type": "daily", "times": ["09:00"], "days": [1, 2, 3, 4, 5]}]}},
                        ensure_ascii=False)

class MiniAppPlanning(unittest.TestCase):
    """Mini App: rule summaries/status in schedules.list, create-from-text (parse → preview → create), delete."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from app.pgdata import save_preferences
        from app import tgapp_chat
        save_preferences({"telegram_bot_token": TOKEN, "telegram_chat_id": str(USER), "language": "ru"})
        tg_webapp.save_config({"enabled": True, "mode": "cloudflare"})
        cls.models = mock.patch.object(tgapp_chat, "chat_models", return_value=[
            {"id": "test/model", "name": "Test", "provider": "test", "vision": False, "pinned": False, "default": True}])
        cls.models.start()
        from app.tgapp_api import create_tgapp, auth_failures, auth_limiter
        auth_failures._hits.clear()
        auth_limiter._hits.clear()
        cls.client = TestClient(create_tgapp())
        cls.client.__enter__()
        response = cls.client.post("/tgapp/api/auth", json={"init_data": tg_webapp.sign_init_data(
            {"auth_date": int(time.time()), "query_id": "AAH", "user": {"id": USER, "first_name": "T"}}, TOKEN)})
        cls.auth = {"Authorization": "Bearer " + response.json()["token"]}

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.models.stop()
        tg_webapp.save_config({"enabled": False})

    def post(self, name, body=None):
        return self.client.post("/tgapp/api/" + name, json=body or {}, headers=self.auth)

    def test_list_carries_the_rule_summary_and_status(self):
        from app.pgdata import save_schedules
        save_schedules([{"id": "r", "name": "R", "prompt": "x", "kind": "notify", "enabled": True,
                         "rule": {"when": [{"type": "daily", "times": ["16:00"]}],
                                  "only_if": [{"type": "process", "name": "dota2.exe"}]},
                         },
                        {"id": "l", "name": "L", "prompt": "y", "daily_at": "09:00", "enabled": False}])
        from app.pgdata import _mutate_schedules

        def blocked(rows):
            rows[0]["rule_state"] = {"status": "blocked", "reason": {"ru": "Ждёт: запущен dota2.exe",
                                                                     "en": "Waiting: dota2.exe is running"}}
            return True
        _mutate_schedules(blocked)
        rows = {row["id"]: row for row in self.post("schedules.list").json()["tasks"]}
        self.assertEqual(rows["r"]["summary"], "Каждый день в 16:00, только если запущен dota2.exe")
        self.assertEqual(rows["r"]["state"], {"status": "blocked", "reason": "Ждёт: запущен dota2.exe"})
        self.assertTrue(rows["r"]["rule"])
        self.assertFalse(rows["l"]["rule"])
        self.assertEqual(rows["l"]["summary"], "Каждый день в 09:00", "a legacy task is shown as a summary too")

    def test_last_result_is_the_newest_one(self):

        from app.pgdata import append_schedule_result, save_schedules
        save_schedules([{"id": "n", "name": "N", "prompt": "x", "daily_at": "09:00"}])
        append_schedule_result("n", False, "old")
        time.sleep(0.01)
        append_schedule_result("n", True, "new")
        row = self.post("schedules.list").json()["tasks"][0]
        self.assertEqual(row["last"]["text"], "new")
        self.assertTrue(row["last"]["ok"])

    def test_parse_previews_and_create_saves(self):
        from app.pgdata import load_schedules, save_schedules
        from app import tgapp_chat
        save_schedules([])
        seen = {}

        async def fake_complete(model, messages, **kw):
            seen["model"] = model
            return MODEL_JSON

        with mock.patch.object(tgapp_chat, "complete", fake_complete):
            response = self.post("schedules.parse", {"text": "по будням в 9 сводка новостей ИИ"})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(seen["model"], "test/model")
        self.assertEqual(data["summary"], "По будням в 09:00")
        self.assertTrue(data["next"] and len(data["next"]) <= 5)
        self.assertTrue(data["task"]["notify_telegram"], "a task made on the phone reports back to Telegram")
        self.assertEqual(load_schedules(), [], "parse must not save anything")
        created = self.post("schedules.create", {"task": data["task"]})
        self.assertEqual(created.status_code, 200, created.text)
        rows = load_schedules()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["created_by"], "telegram")
        self.assertEqual(rows[0]["rule"]["when"][0]["days"], [1, 2, 3, 4, 5])
        self.assertIn(rows[0]["id"], [t["id"] for t in created.json()["tasks"]])

    def test_parse_not_a_schedule_is_a_readable_error(self):
        from app import tgapp_chat

        async def fake_complete(model, messages, **kw):
            return '{"error": "not a schedule"}'

        with mock.patch.object(tgapp_chat, "complete", fake_complete):
            response = self.post("schedules.parse", {"text": "какая погода"})
        self.assertEqual(response.status_code, 422)
        self.assertIn("не похоже на расписание", response.text)

    def test_create_validates_and_never_overwrites_by_id(self):
        from app.pgdata import load_schedules, save_schedules
        save_schedules([{"id": "keep", "name": "Keep", "prompt": "x", "daily_at": "09:00"}])
        bad = self.post("schedules.create", {"task": {"name": "", "prompt": "x", "rule": {"when": [{"type": "daily", "times": ["09:00"]}]}}})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("название", bad.json().get("error", "").lower() + bad.text.lower())
        ok = self.post("schedules.create", {"task": {"id": "keep", "name": "New", "prompt": "y", "kind": "notify",
                                                     "rule": {"when": [{"type": "daily", "times": ["10:00"]}]}}})
        self.assertEqual(ok.status_code, 200, ok.text)
        names = sorted(t["name"] for t in load_schedules())
        self.assertEqual(names, ["Keep", "New"])

    def test_delete(self):
        from app.pgdata import load_schedules, save_schedules
        save_schedules([{"id": "a", "name": "A", "prompt": "x", "daily_at": "09:00"},
                        {"id": "b", "name": "B", "prompt": "x", "daily_at": "09:00"}])
        self.assertEqual(self.post("schedules.delete", {"id": "a"}).status_code, 200)
        self.assertEqual([t["id"] for t in load_schedules()], ["b"])
        self.assertEqual(self.post("schedules.delete", {"id": "zzz"}).status_code, 404)

    def test_page_is_wired(self):
        index = (ROOT / "app/tgapp/index.html").read_text(encoding="utf-8")
        self.assertIn('src="plan.js?v=', index)
        self.assertIn('href="plan.css?v=', index)
        self.assertLess(index.index("plan.js"), index.index('src="app.js'))
        source = (ROOT / "app/tgapp/plan.js").read_text(encoding="utf-8")
        for op in ("schedules.parse", "schedules.create", "schedules.delete", "schedules.toggle", "schedules.run"):
            self.assertIn(op, source)
        self.assertNotIn("kit.pages.schedules", (ROOT / "app/tgapp/extras.js").read_text(encoding="utf-8"))

if __name__ == "__main__":
    unittest.main()

class QuickAccessPlanning(unittest.TestCase):
    """Quick Access: «запланируй…» → schedule.plan → a confirm card (summary, next runs) → «Создать задачу» saves."""

    def setUp(self):
        from app.pgdata import save_preferences, save_schedules
        from app import assistant_commands as ac
        save_preferences({"default_model": "test/model", "language": "ru"})
        save_schedules([])
        self.eng = ac.engine()
        self.calls = []

        async def completion(model, messages, **kw):
            self.calls.append((model, messages))
            return self.answer

        self.answer = MODEL_JSON
        self.old = self.eng.completion
        self.eng.completion = completion

    def tearDown(self):
        self.eng.completion = self.old

    def test_intents_route_schedule_phrases(self):
        from app import assistant_intents as intents
        now = datetime(2026, 10, 12, 10, 0)
        for text in ("запланируй каждый день в 9 сводку новостей", "напоминай каждые 2 часа размяться",
                     "каждый будний день в 9:00 присылай погоду", "напомни каждый день в 9 пить воду",
                     "every weekday at 9 send me news", "schedule a backup every friday",
                     "remind me every monday at 10 to call mom"):
            found = intents.match(text, now=now)
            self.assertEqual((found or {}).get("action"), "schedule.plan", text)
            self.assertEqual(found["params"]["text"], text)
            self.assertTrue(found["fallback_on_miss"])

        self.assertEqual(intents.match("напомни в 15:00 позвонить", now=now)["action"], "reminder.create")
        self.assertEqual(intents.match("напомни в телеграме завтра в 9 купить хлеб", now=now)["action"], "reminder.create")
        self.assertIsNone(intents.match("каждый день пить кофе вредно?", now=now))
        self.assertIsNone(intents.match("запланируй", now=now))

    def test_card_then_confirm_creates_the_task(self):
        from app.pgdata import load_schedules
        card = asyncio.run(self.eng.run("schedule.plan", {"text": "по будням в 9 сводка новостей"}, source="local"))
        self.assertEqual(card["status"], "confirm", card)
        self.assertEqual(card["kind"], "plan")
        self.assertEqual(card["message"], "По будням в 09:00")
        self.assertTrue(card["list"] and all(" в 09:00 · через " in row for row in card["list"]), card["list"])
        self.assertEqual(self.calls[0][0], "test/model")
        self.assertNotIn("draft", json.dumps(card), "the draft id never reaches the card (or the model)")
        self.assertEqual(card["plan"]["task"]["rule"]["when"][0]["days"], [1, 2, 3, 4, 5])
        self.assertEqual(load_schedules(), [], "nothing is saved before the confirmation")
        done = asyncio.run(self.eng.confirm(card["confirm"]["token"], True))
        self.assertEqual(done["status"], "done", done)
        rows = load_schedules()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["created_by"], "quick-ask")
        self.assertEqual(done["plan_id"], rows[0]["id"])
        again = asyncio.run(self.eng.confirm(card["confirm"]["token"], True))
        self.assertEqual(again["status"], "error", "a token is single-use")
        self.assertEqual(len(load_schedules()), 1)

    def test_the_model_cannot_skip_the_confirmation(self):
        from app.pgdata import load_schedules
        card = asyncio.run(self.eng.run("schedule.plan", {"text": "каждый день в 9 новости", "draft": "guess"}, source="model"))
        self.assertEqual(card["status"], "confirm")
        self.assertEqual(load_schedules(), [])

    def test_not_a_schedule_falls_back_to_the_model(self):
        self.answer = '{"error": "not a schedule"}'
        card = asyncio.run(self.eng.run("schedule.plan", {"text": "каждый человек уникален"}, source="local"))
        self.assertEqual(card["status"], "error")
        self.assertEqual(card["code"], "not_found")

    def test_cancel(self):
        from app.pgdata import load_schedules
        card = asyncio.run(self.eng.run("schedule.plan", {"text": "по будням в 9 новости"}, source="local"))
        out = asyncio.run(self.eng.confirm(card["confirm"]["token"], False))
        self.assertEqual(out["status"], "cancelled")
        self.assertEqual(load_schedules(), [])

    def test_quick_plugin_and_handoff_are_wired(self):
        js = (ROOT / "app/pgstatic/quick/plugins/commands.js").read_text(encoding="utf-8")
        self.assertIn('card.kind === "plan" ? t("Создать задачу", "Create the task")', js)
        self.assertIn('data-mqc="plan-edit"', js)
        self.assertIn('handoff: { plan }', js)
        app_js = (ROOT / "app/pgstatic/app.js").read_text(encoding="utf-8")
        self.assertIn("data.plan && typeof data.plan", app_js)
        for spec in ("MemuAI.spec", "build.py"):
            self.assertIn("app.schedule_quick", (ROOT / spec).read_text(encoding="utf-8"))
        self.assertIn("app.schedule_rules_api", (ROOT / "MemuAIProxy.spec").read_text(encoding="utf-8"))

    def test_when_text(self):
        from app import schedule_quick as sq
        now = datetime(2026, 10, 12, 10, 0).timestamp()
        self.assertEqual(sq.when_text(datetime(2026, 10, 12, 16, 0).timestamp(), "ru", now), "сегодня в 16:00 · через 6 ч")
        self.assertEqual(sq.when_text(datetime(2026, 10, 13, 9, 0).timestamp(), "en", now), "tomorrow at 09:00 · in 23 h")
        self.assertEqual(sq.when_text(datetime(2026, 10, 16, 9, 30).timestamp(), "ru", now), "пт, 16 октября в 09:30 · через 4 дня")
        self.assertEqual(sq.when_text(datetime(2026, 10, 16, 9, 30).timestamp(), "en", now), "Fri, Oct 16 at 09:30 · in 4 days")

def _js_function(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    depth, i = 0, source.index("{", start)
    while True:
        ch = source[i]
        depth += ch == "{"
        depth -= ch == "}"
        i += 1
        if depth == 0:
            return source[start:i]

class VisualFixes(unittest.TestCase):
    """Planning panel/builder fixes found on the 1366×728 / 1920×1040 screenshots."""

    def node(self, script: str) -> str:
        import shutil
        import subprocess
        if not shutil.which("node"):
            self.skipTest("node is not installed")
        return subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout.strip()

    def test_relative_time_never_says_60_minutes(self):

        for path, tname in (("app/pgstatic/plan.js", "T"), ("app/tgapp/plan.js", "tr")):
            src = (ROOT / path).read_text(encoding="utf-8")
            body = _js_function(src, "relIn") + "\n" + (_js_function(src, "ruPlural") if "function ruPlural(" in src else _js_function(src, "plural"))
            script = (f"const {tname} = (r, e) => r; {body}; const now = Date.now() / 1000; Date.now = () => now * 1000;"
                      "console.log([relIn(now + 2 * 3600 + 59 * 60 + 40), relIn(now + 59 * 60 + 50), relIn(now + 3 * 3600)].join('|'))")
            self.assertEqual(self.node(script), "через 3 ч|через 1 ч|через 3 ч", path)
        from app import schedule_quick as sq
        now = 1_800_000_000.0
        self.assertTrue(sq.when_text(now + 2 * 3600 + 59 * 60 + 40, "ru", now).endswith("через 3 ч"))
        self.assertTrue(sq.when_text(now + 59 * 60 + 50, "ru", now).endswith("через 1 ч"))

    def test_builder_layout_fixes(self):
        js = (ROOT / "app/pgstatic/plan.js").read_text(encoding="utf-8")
        css = (ROOT / "app/pgstatic/plan.css").read_text(encoding="utf-8")

        self.assertIn('if (el.scrollTop) el.scrollTop = 0', js)

        self.assertIn(".pln-root > .panel-return { align-self: flex-start", css)

        self.assertIn("grid-template-rows: subgrid", css)
        self.assertIn('T("без лимита", "no limit")', js)
        self.assertNotIn('T("0 — без лимита"', js)

        self.assertNotIn('T("правило", "rule")', js)

        self.assertIn("@media (max-height: 820px)", css)

class SummaryWording(unittest.TestCase):
    """RU/EN summaries a non-technical user reads under every task."""

    def s(self, trigger, lang="ru"):
        return rules.summary(rules.normalize_rule({"when": [trigger]}), lang)

    def test_event_delay_reads_naturally(self):

        t = {"type": "event", "event": "pc_start", "delay": 2}
        self.assertEqual(self.s(t), "Через 2 мин после включения компьютера")
        self.assertEqual(self.s(t, "en"), "2 min after the computer starts")
        t = {"type": "event", "event": "process_start", "process": "dota2.exe", "delay": 1}
        self.assertEqual(self.s(t), "Через 1 мин после запуска dota2.exe")
        self.assertEqual(self.s(t, "en"), "1 min after dota2.exe starts")
        t = {"type": "event", "event": "task_done", "task_id": "x", "status": "ok", "delay": 5}
        self.assertEqual(self.s(t), "Через 5 мин после задачи «x» (успешно)")
        self.assertEqual(self.s(t, "en"), "5 min after “x” succeeds")

    def test_weekday_lists_and_day_intervals(self):
        t = {"type": "daily", "times": ["09:00"], "days": [1, 3, 5]}
        self.assertEqual(self.s(t), "По пн, ср и пт в 09:00")
        self.assertEqual(self.s(t, "en"), "On Mon, Wed and Fri at 09:00")
        self.assertEqual(self.s({"type": "interval", "every": 2880}), "Каждые 2 дня")
        self.assertEqual(self.s({"type": "interval", "every": 7200}), "Каждые 5 дней")
        self.assertEqual(self.s({"type": "interval", "every": 1}), "Каждую минуту")
        self.assertEqual(self.s({"type": "interval", "every": 1}, "en"), "Every minute")
