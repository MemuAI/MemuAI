"""Round 27 bug hunt (r27-hunt-reflect): Reflect, «Время и фокус» on every client, background chat titles.

    python -m unittest tests.round27_hunt_reflect_test

No network: models are scripted, Telegram calls are patched.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import chat_titles, pgdata, reflect, telegram_focus  # noqa: E402

def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")

class LocalTimeAcrossDst(unittest.TestCase):
    """Statistics are in the user's local time, with each moment's own DST offset."""

    def run_in_tz(self, code: str) -> str:
        env = dict(os.environ, TZ="Europe/Berlin", PYTHONPATH=str(ROOT))
        out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    def test_hours_and_bounds_after_the_clocks_change(self):

        code = """
from app import reflect
from tests.timezone_fixture import local_datetime
datetime = reflect.datetime = local_datetime("Europe/Berlin")
now = datetime(2026, 11, 5, 12, 0)
ts = lambda *a: int(datetime(*a).timestamp() * 1000)
start, end = reflect.period_bounds("3m", now)
last_start, last_end = reflect.period_bounds("last_month", now)
conv = {"id": "a", "source": "chat", "title": "t", "messages": [
    {"role": "user", "text": "x", "ts": ts(2026, 10, 20, 10, 30)}, {"role": "user", "text": "y", "ts": ts(2026, 10, 20, 10, 40)}]}
stats = reflect.compute_stats(reflect.in_period([conv], start, end), start, end, local=True)
print(start.hour, last_start.isoformat(), last_end.isoformat(), stats["peak_hour"])
"""
        hour, last_start, last_end, peak = self.run_in_tz(code).split()
        self.assertEqual(hour, "0")
        self.assertEqual(last_start, "2026-10-01T00:00:00+02:00")
        self.assertEqual(last_end, "2026-11-01T00:00:00+01:00")
        self.assertEqual(peak, "10")

    def test_snapshot_uses_local_time(self):
        snap = reflect.snapshot("3m", dict(reflect.PREF_DEFAULTS))
        self.assertIsNone(snap["tz"])

class ScriptedModel:
    def __init__(self, fn):
        self.fn, self.calls = fn, 0

    async def __call__(self, model, messages):
        self.calls += 1
        return await self.fn(messages)

MAP_OK = json.dumps({"topics": [{"name": "Код", "description": "FastAPI", "conversations": 3}], "notes": []})
FINAL_OK = json.dumps({"headline": "Код и испанский", "summary": "Вы чинили FastAPI.",
                       "topics": [{"name": "Код", "description": "FastAPI", "percent": 100}],
                       "skills": {k: {"observation": "Наблюдение.", "next_step": "Шаг.", "action": "none"}
                                  for k in reflect.SKILLS}}, ensure_ascii=False)

def _big_snapshot(n=40):
    now = datetime.now().astimezone()
    convs = []
    for i in range(n):
        stamp = int((now - timedelta(days=i % 20, hours=1)).timestamp() * 1000)
        convs.append({"id": f"c{i}", "title": f"Тема {i} " + "слово " * 20, "source": "chat",
                      "messages": [{"role": "user", "text": f"Вопрос {i} " + "текст " * 60, "ts": stamp}]})
    start, end = reflect.period_bounds("3m")
    return {"period": "3m", "start": start, "end": end, "tz": None, "conversations": convs,
            "stats": reflect.compute_stats(convs, start, end, True), "fingerprint": "x", "sensitive": 0}

class MapReduce(unittest.TestCase):
    def setUp(self):
        self.saved = reflect.DIGEST_CHUNK_CHARS
        reflect.DIGEST_CHUNK_CHARS = 3000

    def tearDown(self):
        reflect.DIGEST_CHUNK_CHARS = self.saved

    def test_one_unreadable_part_does_not_sink_the_report(self):
        async def answer(messages):
            text = messages[-1]["content"]
            if "Digest part" in text:
                return "garbage" if "part 2 of" in text else MAP_OK
            return FINAL_OK
        model = ScriptedModel(answer)
        report = asyncio.run(reflect.generate_report(_big_snapshot(), "ru", "m", model))
        self.assertEqual(report["headline"], "Код и испанский")

    def test_a_provider_error_stops_the_other_parts(self):
        started, finished = [], []

        async def answer(messages):
            text = messages[-1]["content"]
            part = text.split("part ", 1)[1].split(" ", 1)[0] if "part " in text else "final"
            started.append(part)
            if part == "1":
                raise RuntimeError("HTTP 500 upstream")
            await asyncio.sleep(0.3)
            finished.append(part)
            return MAP_OK
        async def main():
            with self.assertRaises(RuntimeError):
                await reflect.generate_report(_big_snapshot(), "ru", "m", ScriptedModel(answer))
            await asyncio.sleep(0.6)
        asyncio.run(main())
        self.assertGreater(len(started), 1)
        self.assertEqual(finished, [])

class TurnedOffWhileWriting(unittest.TestCase):
    def test_report_is_not_saved_after_reflect_was_turned_off(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from tests.round27_reflect_data import make_history
        data = Path(pgdata.MEMU_DIR)
        data.mkdir(parents=True, exist_ok=True)
        names = ("chats.json", "quick_chats.json", "reflect.json")
        saved = {n: (data / n).read_bytes() if (data / n).exists() else None for n in names}
        old_prefs = pgdata.load_preferences()
        try:
            chats, quick = make_history(datetime.now().astimezone())
            (data / "chats.json").write_text(json.dumps(chats, ensure_ascii=False), encoding="utf-8")
            (data / "quick_chats.json").write_text(json.dumps(quick, ensure_ascii=False), encoding="utf-8")
            (data / "reflect.json").unlink(missing_ok=True)
            pgdata.save_preferences({"reflect_enabled": True})
            app = FastAPI()
            reflect.register(app, completion=None)

            async def slow(model, messages):
                await asyncio.to_thread(pgdata.save_preferences, {"reflect_enabled": False})
                return FINAL_OK
            app.state.reflect_completion = slow
            with TestClient(app) as client:
                client.post("/api/reflect/generate", json={"period": "3m", "model": "m"})
                for _ in range(100):
                    job = app.state.reflect_jobs.items
                    if job and all(j["status"] != "running" for j in job.values()):
                        break
                    time.sleep(0.05)
                self.assertEqual([j["status"] for j in app.state.reflect_jobs.items.values()], ["error"])
            self.assertEqual(reflect.load_store()["reports"], {})
        finally:
            pgdata.save_preferences({"reflect_enabled": old_prefs.get("reflect_enabled", True)})
            for n, raw in saved.items():
                if raw is None:
                    (data / n).unlink(missing_ok=True)
                else:
                    (data / n).write_bytes(raw)

class LightPolling(unittest.TestCase):
    def test_status_does_not_read_the_chats(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        reflect.register(app, completion=None)
        with TestClient(app) as client, patch.object(reflect, "snapshot", side_effect=AssertionError("read chats")):
            self.assertEqual(client.get("/api/reflect/status?period=3m&lang=en").json(), {"job": None})
        js = _read("app/pgstatic/reflect.js")
        self.assertIn("ctx.api(`/api/reflect/status?${q}`)", js)

class TelegramFocus(unittest.TestCase):
    QUIET = {"focus_quiet_enabled": True, "focus_quiet_days": [0, 1, 2, 3, 4, 5, 6],
             "focus_quiet_start": "00:00", "focus_quiet_end": "23:59"}

    def setUp(self):
        telegram_focus._held.clear()
        telegram_focus._acknowledged.clear()
        self.tmp = Path(tempfile.mkdtemp(prefix="r27-tgfocus-"))
        self.tracker = reflect.FocusTracker(path=self.tmp / "focus.json")
        self.patch = patch.object(reflect, "shared_tracker", return_value=self.tracker)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def quiet_prefs(self):
        now = datetime.now()
        if now.hour == 23 and now.minute >= 58:
            self.skipTest("too close to the end of the quiet window")
        return dict(self.QUIET)

    def test_quiet_hours_hold_once_per_window(self):
        prefs = self.quiet_prefs()
        msg = {"message_id": 7, "text": "Привет", "date": int(time.time())}
        text, keyboard = telegram_focus.quiet_hold("42", msg, "Привет", prefs)
        self.assertIn("тихие часы", text)
        go, later = [b["callback_data"] for b in keyboard["inline_keyboard"][0]]
        self.assertTrue(go.startswith("fq:go:") and later.startswith("fq:later:"))
        self.assertEqual(telegram_focus.quiet_hold("42", msg, "И ещё", prefs), ("", None))
        held, note = telegram_focus.quiet_answer("42", go, prefs)
        self.assertEqual(held, [(msg, "Привет"), (msg, "И ещё")])
        self.assertIsNone(telegram_focus.quiet_hold("42", msg, "ещё", prefs))
        card43 = telegram_focus.quiet_hold("43", msg, "ещё", prefs)
        self.assertIsNotNone(card43)
        held, note = telegram_focus.quiet_answer("43", card43[1]["inline_keyboard"][0][1]["callback_data"], prefs)
        self.assertEqual(held, [])
        self.assertIsNone(telegram_focus.quiet_hold("1", msg, "x", {**prefs, "focus_quiet_enabled": False}))
        en = telegram_focus.quiet_hold("44", msg, "x", {**prefs, "telegram_language": "en"})[0]
        self.assertIn("quiet hours", en)

    def test_break_card_counts_the_shared_day(self):
        prefs = {"focus_break_enabled": True, "focus_break_minutes": 10}
        self.tracker._update(lambda d: d.update(day=datetime.now().date().isoformat(), used=9.9 * 60,
                                                last=time.time() - 20, snooze_until=0, rearm=0, dismissed=False))
        card = asyncio.run(telegram_focus.break_due(prefs))
        self.assertIsNotNone(card)
        self.assertIn("Сегодня вы уже 10 мин", card[0])
        self.assertIsNone(asyncio.run(telegram_focus.break_due(prefs)))
        self.assertIn("сегодня больше", asyncio.run(telegram_focus.break_answer("fb:dismiss", prefs)))
        self.assertTrue(json.loads((self.tmp / "focus.json").read_text(encoding="utf-8"))["dismissed"])
        self.assertIsNone(asyncio.run(telegram_focus.break_due({"focus_break_enabled": False})))
        self.assertEqual(telegram_focus.duration(65, {"telegram_language": "en"}), "1 h 5 min")

    def test_bot_holds_and_continues(self):
        from app import telegram_bot
        prefs = self.quiet_prefs()
        sent, edited, prompted = [], [], []

        async def fake_send(client, token, chat_id, text, markup=None, **kw):
            sent.append((text, markup))
            return [1]

        async def fake_call(client, token, method, **payload):
            edited.append((method, payload.get("text")))
            return {}

        async def fake_prompt(client, token, chat_id, message, prefs, text):
            prompted.append((message.get("date"), text))

        async def noop(*a, **k):
            return None
        with patch.object(telegram_bot, "_send", fake_send), patch.object(telegram_bot, "_call", fake_call), \
                patch.object(telegram_bot, "_prompt_message", fake_prompt), patch.object(telegram_bot, "_toast", noop), \
                patch.object(telegram_bot, "_allowed", return_value=True):
            msg = {"message_id": 5, "text": "Привет", "date": int(time.time()) - 3600, "chat": {"id": 99}, "from": {"id": 1}}
            asyncio.run(telegram_focus_gate(telegram_bot, msg, prefs))
            self.assertEqual(prompted, [])
            go = sent[-1][1]["inline_keyboard"][0][0]["callback_data"]
            self.assertTrue(go.startswith("fq:go:"))
            cb = {"id": "c", "data": go, "message": {"message_id": 6, "chat": {"id": 99}}, "from": {"id": 1}}
            asyncio.run(telegram_bot._handle_callback(None, "t", cb, prefs))
            self.assertEqual(prompted, [(0, "Привет")])
            self.assertEqual(edited[-1][0], "editMessageText")

    def test_wiring(self):
        bot = _read("app/telegram_bot.py")
        self.assertIn("telegram_focus.quiet_hold(chat_id, message, text, prefs)", bot)
        self.assertIn("nudge = await telegram_focus.break_due(prefs)", bot)
        api = _read("app/tgapp_api.py")
        self.assertIn('raise ApiError(409, "quiet_hours"', api)
        self.assertIn('@op("focus.break")', api)
        js = _read("app/tgapp/app.js")
        self.assertIn('error.code === "quiet_hours"', js)
        self.assertIn("quiet_ack: quietAck", js)
        self.assertIn("if (data.break) breakReminder(data.break.text);", js)
        self.assertEqual(subprocess.run(["node", "--check", str(ROOT / "app/tgapp/app.js")]).returncode, 0)

class MiniAppFocus(unittest.TestCase):
    def test_quiet_hours_and_break_in_the_mini_app(self):
        from fastapi.testclient import TestClient
        from app import tg_webapp, tgapp_chat
        from app.tgapp_api import create_tgapp
        token, user = "123456:R27-hunt-reflect_token", 5151
        now = datetime.now()
        if now.hour == 23 and now.minute >= 58:
            self.skipTest("too close to the end of the quiet window")
        old = pgdata.load_preferences()
        pgdata.save_preferences({"telegram_bot_token": token, "telegram_chat_id": str(user), **TelegramFocus.QUIET,
                                 "focus_break_enabled": True, "focus_break_minutes": 10})
        tg_webapp.save_config({"enabled": True, "mode": "cloudflare"})
        telegram_focus._acknowledged.clear()
        tmp = Path(tempfile.mkdtemp(prefix="r27-tgapp-focus-"))
        tracker = reflect.FocusTracker(path=tmp / "focus.json")
        tracker._update(lambda d: d.update(day=now.date().isoformat(), used=11 * 60, last=time.time() - 20,
                                           snooze_until=0, rearm=0, dismissed=False))
        started = []

        class FakeJob:
            id, meta = "j1", {"chat_id": "c1", "model": "m"}

        def fake_start(**kw):
            started.append(kw["text"])
            return FakeJob()
        init = tg_webapp.sign_init_data({"auth_date": int(time.time()), "query_id": "AAH-r27",
                                         "user": {"id": user, "first_name": "R", "language_code": "ru"}}, token)
        try:
            with patch.object(reflect, "shared_tracker", return_value=tracker), \
                    patch.object(tgapp_chat, "start_send", side_effect=fake_start), TestClient(create_tgapp()) as client:
                auth = client.post("/tgapp/api/auth", json={"init_data": init}).json()
                headers = {"Authorization": "Bearer " + auth["token"]}
                first = client.post("/tgapp/api/chats.send", json={"text": "Привет"}, headers=headers)
                self.assertEqual((first.status_code, first.json()["code"]), (409, "quiet_hours"))
                self.assertEqual(started, [])
                sent = client.post("/tgapp/api/chats.send", json={"text": "Привет", "quiet_ack": True}, headers=headers)
                self.assertEqual(sent.status_code, 200, sent.text)
                self.assertIn("Сегодня вы уже 11 мин", sent.json()["break"]["text"])
                again = client.post("/tgapp/api/chats.send", json={"text": "Ещё"}, headers=headers)
                self.assertEqual(again.status_code, 200)
                self.assertNotIn("break", again.json())
                self.assertEqual(started, ["Привет", "Ещё"])
                ok = client.post("/tgapp/api/focus.break", json={"action": "dismiss"}, headers=headers)
                self.assertEqual(ok.status_code, 200)
                self.assertEqual(client.post("/tgapp/api/focus.break", json={"action": "x"}, headers=headers).status_code, 400)
        finally:
            tg_webapp.save_config({"enabled": False})
            pgdata.save_preferences({k: old.get(k) for k in ("telegram_bot_token", "telegram_chat_id", "focus_quiet_enabled",
                                                            "focus_break_enabled")})
            shutil.rmtree(tmp, ignore_errors=True)

async def telegram_focus_gate(telegram_bot, message, prefs):
    """The bot's message path up to the gate (session/keyboard handling patched out)."""
    with patch.object(telegram_bot, "_session", return_value=({}, {"keyboard_rev": telegram_bot._keyboard_rev(prefs, None)})), \
            patch.object(telegram_bot, "_webapp_url", return_value=None), \
            patch.object(telegram_bot, "_handle_pending_pin", side_effect=_false), \
            patch.object(telegram_bot, "_handle_pending_action", side_effect=_false), \
            patch.object(telegram_bot, "_try_pairing", side_effect=_false):
        await telegram_bot._handle_message(None, "t", message, prefs)

async def _false(*a, **k):
    return False

class ChatTitles(unittest.TestCase):
    def test_cut_inside_reasoning_is_not_a_title(self):
        self.assertEqual(chat_titles.title_from_reply("<think>\nOkay, the user wants a title" * 40), "")
        self.assertEqual(chat_titles.title_from_reply("<think>x</think>\nПолив фикуса"), "Полив фикуса")

    def test_stream_reads_past_long_reasoning(self):
        async def stream(model, messages, params):
            yield {"choices": [{"delta": {"content": "<think>" + "размышляю " * 200}}]}
            yield {"choices": [{"delta": {"content": "</think>Полив фикуса зимой"}}]}
        self.assertEqual(asyncio.run(chat_titles.generate_title(stream, "m", "Как поливать фикус?")), "Полив фикуса зимой")

    def test_apply_title_respects_manual_and_bumps_updated(self):
        row = {"id": "c", "title": "a", "updated": 10}
        self.assertTrue(chat_titles.apply_title(row, "Новое", {}))
        self.assertEqual((row["title"], row["titleAuto"], row["updated"]), ("Новое", True, 11))
        manual = {"id": "c", "title": "Моё", "titleManual": True}
        self.assertFalse(chat_titles.apply_title(manual, "Новое", {}))
        self.assertFalse(chat_titles.apply_title({"id": "c"}, "Новое", {"chat_titles": "off"}))

    def test_patch_chat_never_resurrects(self):
        self.assertIsNone(pgdata.patch_chat("no-such-chat-" + str(time.time()), lambda row: True, "default"))
        self.assertFalse(any(str(r.get("id", "")).startswith("no-such-chat-") for r in pgdata.load_chats("default")))

    def test_window_does_not_ask_again_and_again(self):
        js = _read("app/pgstatic/app.js")
        self.assertIn("(chatTitleFailures.get(chatId) || 0) >= 2", js)
        self.assertIn("if (!applyChatTitle(chatId, title) && current.title === title) current.titleAuto = true;", js)

if __name__ == "__main__":
    unittest.main()
