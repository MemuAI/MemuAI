"""Round 23 bug hunt: regression tests for the Round 22 backend (r23-bughunt).

Every test here failed on the Round 22 tree and documents one bug from
``/workspace/work/round23/r23-bughunt.md`` (symptom → cause → fix):

* scheduler: SMTP port/TLS validation and a readable error instead of a hang;
* «Планирование» vs Quick Ask: a stale Planning snapshot deleted reminders; editing a
  reminder in Planning did not change the delivered text;
* assistant intents: delayed shutdown, compound phrases, weekdays, «сегодня в 9»,
  «сделай фото» / «запиши видео», one-word chat replies taken for media keys;
* assistant engine: nested routines bypassed confirmation, ``open.path`` executable
  detection, «trusted» for model calls, clipboard read by the model, «закрой X» asking
  for confirmation for a program that is not running, broken timers poisoning the state;
* Windows backend: ``taskkill /T`` killed the whole process tree;
* companion import: Windows-special zip names, encrypted / corrupted archives, runtime
  download streamed with a cap and written all-or-nothing;
* product cards: budgets like «15к», prices without a currency counted as «in budget»;
* model capabilities: a re-pointed public id kept its old cached card.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import smtplib
import ssl
import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path
from unittest import mock

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

from app import assistant_commands as ac  # noqa: E402
from app import assistant_intents as intents  # noqa: E402
from app import assistant_timers as timers_mod  # noqa: E402
from app import companion_avatar as comp  # noqa: E402
from app import pgdata, scheduler  # noqa: E402
from app import product_cards as pc  # noqa: E402

from tests.assistant_commands_test import FakeBackend, NOW  # noqa: E402

THURSDAY_1430 = datetime(2026, 10, 8, 14, 30)      # 2026-10-08 is a Thursday


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------------------------- SMTP

class SmtpTest(unittest.TestCase):
    BASE = {"smtp_host": "smtp.example.com", "smtp_from": "me@example.com", "schedule_email_to": "you@example.com",
            "smtp_user": "me", "smtp_password": "pw"}

    def test_non_numeric_port_is_a_readable_error(self):
        with self.assertRaises(ValueError) as caught:
            scheduler.smtp_settings({**self.BASE, "smtp_port": "abc"})
        self.assertIn("SMTP-порт", str(caught.exception))
        with self.assertRaises(ValueError):
            scheduler.smtp_settings({**self.BASE, "smtp_port": 70000})

    def test_tls_mode_follows_the_well_known_port(self):
        self.assertEqual(scheduler.smtp_settings({**self.BASE, "smtp_port": 465, "smtp_security": "starttls"})["security"], "ssl")
        self.assertEqual(scheduler.smtp_settings({**self.BASE, "smtp_port": "587", "smtp_security": "ssl"})["security"], "starttls")
        self.assertEqual(scheduler.smtp_settings({**self.BASE, "smtp_port": 2465, "smtp_security": "ssl"})["security"], "ssl")
        self.assertEqual(scheduler.smtp_settings({**self.BASE, "smtp_port": None})["port"], 587)

    def test_starttls_on_465_uses_implicit_tls_with_a_timeout(self):
        made = {}

        class FakeSSL:
            def __init__(self, host, port, timeout=None, context=None):
                made.update(cls="ssl", host=host, port=port, timeout=timeout)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def starttls(self, **kw):
                made["starttls"] = True

            def login(self, user, password):
                made["login"] = user

            def send_message(self, message, to_addrs=None):
                made["sent"] = to_addrs

        with mock.patch.object(smtplib, "SMTP_SSL", FakeSSL), \
                mock.patch.object(smtplib, "SMTP", side_effect=AssertionError("plain SMTP must not be used")):
            scheduler._send_schedule_email({**self.BASE, "smtp_port": 465, "smtp_security": "starttls"}, "t", "x")
        self.assertEqual((made["cls"], made["port"]), ("ssl", 465))
        self.assertNotIn("starttls", made)
        self.assertLessEqual(made["timeout"], scheduler.SMTP_TIMEOUT)
        self.assertEqual(made["sent"], ["you@example.com"])

    def test_unreachable_or_tls_mismatch_is_named(self):
        with mock.patch.object(smtplib, "SMTP", side_effect=TimeoutError("timed out")):
            with self.assertRaises(ConnectionError) as caught:
                scheduler._send_schedule_email({**self.BASE, "smtp_port": 2525}, "t", "x")
        self.assertIn("режим шифрования", str(caught.exception))
        with mock.patch.object(smtplib, "SMTP_SSL", side_effect=ssl.SSLError(1, "WRONG_VERSION_NUMBER")):
            with self.assertRaises(ConnectionError) as caught:
                scheduler._send_schedule_email({**self.BASE, "smtp_port": 2465, "smtp_security": "ssl"}, "t", "x")
        self.assertIn("TLS", str(caught.exception))


# ---------------------------------------------------------------------------------------------- schedules race

class SchedulesRaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(pgdata, "MEMU_DIR", Path(self.tmp.name))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_stale_planning_snapshot_keeps_a_reminder_added_meanwhile(self):
        pgdata.save_schedules([{"id": "daily", "name": "News", "prompt": "news", "daily_at": "09:00", "enabled": True}])
        snapshot = pgdata.load_schedules()                       # «Планирование» opened
        reminder_id = ac.add_reminder_task("купить хлеб", datetime(2026, 10, 8, 15, 0))   # Quick Ask meanwhile
        snapshot[0]["enabled"] = False                           # user toggles the old task and saves
        pgdata.save_schedules(snapshot, [t["id"] for t in snapshot])
        ids = {t["id"]: t for t in pgdata.load_schedules()}
        self.assertIn(reminder_id, ids, "the reminder created by Quick Ask was wiped by the stale snapshot")
        self.assertFalse(ids["daily"]["enabled"])

    def test_known_task_can_still_be_deleted_and_old_clients_replace(self):
        pgdata.save_schedules([{"id": "a", "name": "A", "prompt": "a", "daily_at": "09:00"},
                               {"id": "b", "name": "B", "prompt": "b", "daily_at": "10:00"}])
        pgdata.save_schedules([{"id": "a", "name": "A", "prompt": "a", "daily_at": "09:00"}], ["a", "b"])
        self.assertEqual([t["id"] for t in pgdata.load_schedules()], ["a"])
        pgdata.save_schedules([], None)                          # legacy body without known_ids
        self.assertEqual(pgdata.load_schedules(), [])

    def test_editing_a_reminder_text_in_planning_changes_what_is_delivered(self):
        reminder_id = ac.add_reminder_task("купить хлеб", datetime(2026, 10, 8, 15, 0))
        tasks = pgdata.load_schedules()
        tasks[0]["prompt"] = "купить молоко"
        pgdata.save_schedules(tasks, [t["id"] for t in tasks])
        task = next(t for t in pgdata.load_schedules() if t["id"] == reminder_id)
        self.assertEqual(task["reminder_text"], "купить молоко")

    def test_reminder_list_shows_a_planning_edited_time_on_the_local_clock(self):
        import time as _time
        from datetime import timezone
        if hasattr(_time, "tzset"):          # make the check meaningful on a UTC test machine
            previous = os.environ.get("TZ")
            os.environ["TZ"] = "Europe/Moscow"
            _time.tzset()

            def restore():
                if previous is None:
                    os.environ.pop("TZ", None)
                else:
                    os.environ["TZ"] = previous
                _time.tzset()
            self.addCleanup(restore)
        reminder_id = ac.add_reminder_task("x", datetime(2026, 10, 8, 15, 0))
        local = datetime(2026, 10, 9, 18, 45)
        utc = local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")     # what app.js toISOString() saves
        tasks = pgdata.load_schedules()
        tasks[0]["run_at"] = utc
        pgdata.save_schedules(tasks, [reminder_id])
        self.assertEqual(ac.list_reminder_tasks()[0]["when"], "09.10 18:45")

    def test_admin_route_accepts_known_ids(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import admin_api
        app = FastAPI()
        app.include_router(admin_api.router)
        client = TestClient(app, base_url="http://127.0.0.1")
        with mock.patch.object(admin_api, "_auth", return_value=None):
            bad = client.post("/api/admin/pg/schedules", json={"tasks": [], "known_ids": "x"})
            self.assertEqual(bad.status_code, 400)
            ac.add_reminder_task("x", datetime(2026, 10, 8, 15, 0))
            ok = client.post("/api/admin/pg/schedules", json={"tasks": [], "known_ids": []})
            self.assertEqual(ok.status_code, 200)
        self.assertEqual(len(pgdata.load_schedules()), 1)


# ---------------------------------------------------------------------------------------------- intents

class IntentBugsTest(unittest.TestCase):
    def m(self, text, **kw):
        found = intents.match(text, now=THURSDAY_1430, **kw)
        return (found["action"], found["params"]) if found else None

    def test_delayed_shutdown_is_not_closing_an_app_called_computer(self):
        self.assertEqual(self.m("выключи компьютер через час"), ("power.shutdown", {"delay_minutes": 60}))
        self.assertEqual(self.m("выключи компьютер в 23:00"), ("power.shutdown", {"delay_minutes": 510}))
        self.assertEqual(self.m("перезагрузи пк через 10 минут"), ("power.restart", {"delay_minutes": 10}))
        self.assertEqual(self.m("shut down the computer in 2 hours"), ("power.shutdown", {"delay_minutes": 120}))
        self.assertEqual(self.m("выключи компьютер"), ("power.shutdown", {}))

    def test_compound_requests_go_to_the_model(self):
        self.assertIsNone(self.m("открой chrome и найди котиков"))
        self.assertIsNone(self.m("закрой телеграм и открой дискорд"))
        self.assertIsNone(self.m("open spotify and play jazz"))
        self.assertEqual(self.m("открой телеграм"), ("app.open", {"name": "телеграм"}))

    def test_weekday_reminders(self):
        self.assertEqual(self.m("напомни в среду в 10 оплатить"),
                         ("reminder.create", {"channel": "local", "at": "2026-10-14T10:00", "text": "оплатить"}))
        self.assertEqual(self.m("remind me on friday at 5pm to call mom")[1]["at"], "2026-10-09T17:00")
        self.assertEqual(self.m("напомни в следующий четверг в 12 обед")[1]["at"], "2026-10-15T12:00")
        self.assertEqual(self.m("напомни в 15:00 проверить среду разработки")[1]["text"], "проверить среду разработки")

    def test_today_morning_hour_said_in_the_afternoon_is_evening(self):
        self.assertEqual(self.m("напомни сегодня в 9 купить хлеб")[1]["at"], "2026-10-08T21:00")
        self.assertEqual(self.m("напомни сегодня в 9 утра купить хлеб")[1]["at"], "2026-10-08T09:00")

    def test_photo_and_recording_are_not_images_or_notes(self):
        self.assertIsNone(self.m("сделай фото"))
        self.assertIsNone(self.m("создай картинку"))
        self.assertIsNone(self.m("запиши видео"))
        self.assertIsNone(self.m("запиши экран"))
        self.assertEqual(self.m("запиши купить молоко"), ("note.add", {"text": "купить молоко"}))
        self.assertEqual(self.m("нарисуй кота"), ("image.generate", {"prompt": "кота"}))

    def test_one_word_chat_replies_are_not_commands_inside_a_conversation(self):
        for word in ("продолжи", "дальше", "next", "right", "отмени", "пауза", "память"):
            self.assertIsNotNone(self.m(word), word)
            self.assertIsNone(self.m(word, conversation=True), word)
        self.assertEqual(self.m("громкость 30", conversation=True), ("volume.set", {"level": 30}))
        self.assertEqual(self.m("следующий трек", conversation=True), ("media.key", {"key": "next"}))


# ---------------------------------------------------------------------------------------------- engine

class EngineBugsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.backend = FakeBackend()
        self.timers = timers_mod.TimerManager(self.tmp / "timers.json", clock=lambda: 1_000_000.0)
        self.engine = ac.Engine(backend=self.backend, store=ac.Store(self.tmp / "assistant.json"),
                                audit=ac.Audit(self.tmp / "audit.jsonl"), timers=self.timers, now_fn=lambda: NOW)
        self.platform = mock.patch.object(ac.sys, "platform", "win32")
        self.platform.start()

    def tearDown(self):
        self.platform.stop()

    def add(self, command):
        clean = ac.validate_command(command)
        self.engine.store.update(lambda d: d["commands"].append(clean))
        return clean

    def test_safe_routine_cannot_run_a_dangerous_routine_unconfirmed(self):
        self.add({"name": "Ночь", "steps": [{"type": "action", "action": "power.shutdown"}]})
        self.add({"name": "Уют", "steps": [{"type": "action", "action": "volume.set", "params": {"level": 10}},
                                           {"type": "action", "action": "command.run", "params": {"name": "Ночь"}}]})
        card = run(self.engine.run("command.run", {"name": "Уют"}))
        self.assertEqual(card["status"], "confirm")
        self.assertEqual(card["confirm"]["tier"], "dangerous")
        self.assertFalse(any(c[0] == "shutdown" for c in self.backend.calls))

    def test_trusted_routine_is_not_trusted_for_the_model(self):
        self.add({"name": "Закрыть хром", "trusted": True,
                  "steps": [{"type": "action", "action": "app.close", "params": {"name": "telegram"}}]})
        self.assertEqual(run(self.engine.run("command.run", {"name": "Закрыть хром"}))["status"], "done")
        card = run(self.engine.run("command.run", {"name": "Закрыть хром"}, source="model"))
        self.assertEqual(card["status"], "confirm")

    def test_open_path_that_runs_code_needs_confirmation(self):
        self.assertTrue(ac.risky_path("%COMSPEC%"))
        self.assertTrue(ac.risky_path(r"C:\Users\me\Downloads\evil.exe."))
        self.assertTrue(ac.risky_path(r"C:\Users\me\Downloads\invoice.hta"))
        self.assertTrue(ac.risky_path(r"C:\Users\me\Desktop\site.url"))
        self.assertTrue(ac.risky_path(r"\\attacker\share\doc.pdf"))
        self.assertTrue(ac.risky_path(r"C:\x\notes.txt:evil.exe"))
        self.assertTrue(ac.risky_path(r"C:\x\no_extension"))
        self.assertFalse(ac.risky_path(r"C:\x\report.pdf"))
        self.assertFalse(ac.risky_path(str(self.tmp)))
        card = run(self.engine.run("open.path", {"path": r"C:\Users\me\Downloads\invoice.hta"}, source="model"))
        self.assertEqual(card["status"], "confirm")
        self.add({"name": "Запуск", "steps": [{"type": "action", "action": "open.path", "params": {"path": "%COMSPEC%"}}]})
        self.assertEqual(run(self.engine.run("command.run", {"name": "Запуск"}))["status"], "confirm")

    def test_model_must_not_read_the_clipboard_silently(self):
        self.assertEqual(run(self.engine.run("clipboard.get", {}))["status"], "done")
        card = run(self.engine.run("clipboard.get", {}, source="model"))
        self.assertEqual(card["status"], "confirm")
        self.assertNotIn("old clip", json.dumps(ac.model_result(card), ensure_ascii=False))

    def test_delayed_shutdown_runs_with_the_delay(self):
        card = run(self.engine.run("power.shutdown", {"delay_minutes": 60}))
        self.assertEqual(card["status"], "confirm")
        self.assertIn("через 1 ч", card["message"])
        done = run(self.engine.confirm(card["confirm"]["token"], True))
        self.assertEqual(done["status"], "done")
        self.assertIn(("shutdown", 3600), self.backend.calls)
        self.assertIn("11:30", done["message"])
        self.assertTrue(done.get("undo"))

    def test_volume_undo_restores_the_mute_state(self):
        self.backend.volume = {"level": 40, "mute": True}
        original = self.backend.volume_set

        def volume_set(level):                    # like Core Audio in WinBackend: a level > 0 unmutes
            result = original(level)
            if level > 0:
                self.backend.volume["mute"] = False
            return dict(self.backend.volume)
        self.backend.volume_set = volume_set
        card = run(self.engine.run("volume.set", {"level": 70}))
        self.assertFalse(self.backend.volume["mute"])
        undone = run(self.engine.undo(card["undo"]))
        self.assertEqual(undone["status"], "done")
        self.assertEqual(self.backend.volume, {"level": 40, "mute": True})

    def test_broken_persisted_timer_does_not_stop_every_ring(self):
        path = self.tmp / "broken.json"
        path.write_text(json.dumps({"timers": [{"id": "bad", "kind": "timer", "ends_at": "soon"},
                                               {"id": "inf", "kind": "timer", "ends_at": 1e999},
                                               {"id": "ok", "kind": "timer", "label": "чай", "ends_at": 10.0}],
                                    "pomodoro": {"active": True, "phase": "work", "ends_at": None}}), encoding="utf-8")
        manager = timers_mod.TimerManager(path, clock=lambda: 20.0)
        fired = manager.tick()
        self.assertEqual([e["id"] for e in fired], ["ok"])
        self.assertFalse(manager.state["pomodoro"]["active"])
        with self.assertRaises(ValueError):
            manager.add_timer(float("inf"))


class RouteBugsTest(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        self.tmp = Path(tempfile.mkdtemp())
        self.backend = FakeBackend()
        self.engine = ac.Engine(backend=self.backend, store=ac.Store(self.tmp / "a.json"), audit=ac.Audit(self.tmp / "a.jsonl"),
                                timers=timers_mod.TimerManager(self.tmp / "t.json"), now_fn=lambda: NOW)
        ac.set_engine(self.engine)
        app = FastAPI()
        app.state.quick_ask = None
        with mock.patch.dict(os.environ, {"MEMUAI_ASSISTANT_NO_WARM": "1"}):
            ac.register(app)
        self.client = TestClient(app)
        self.platform = mock.patch.object(ac.sys, "platform", "win32")
        self.platform.start()

    def tearDown(self):
        self.platform.stop()
        ac.set_engine(None)

    def test_close_of_something_not_running_goes_to_the_model_without_a_confirm_card(self):
        data = self.client.post("/api/assistant/run", json={"text": "закрой вопрос"}).json()
        self.assertTrue(data["card"]["fallback"])
        self.assertNotEqual(data["card"]["status"], "confirm")
        card = self.client.post("/api/assistant/run", json={"text": "закрой телеграм"}).json()["card"]
        self.assertEqual(card["status"], "confirm")         # Telegram IS running in the fake backend

    def test_in_chat_flag_reaches_the_matcher(self):
        self.assertIsNone(self.client.post("/api/assistant/run", json={"text": "продолжи", "in_chat": True}).json()["card"])
        self.assertEqual(self.client.post("/api/assistant/run", json={"text": "продолжи"}).json()["card"]["action"], "media.key")

    def test_duplicate_global_hotkeys_are_refused_at_save(self):
        first = {"name": "Один", "hotkey": "Ctrl+Alt+K", "steps": [{"type": "say", "text": "1"}]}
        self.assertEqual(self.client.post("/api/assistant/commands", json={"op": "save", "command": first}).status_code, 200)
        second = {"name": "Два", "hotkey": "ctrl+alt+k", "steps": [{"type": "say", "text": "2"}]}
        response = self.client.post("/api/assistant/commands", json={"op": "save", "command": second})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Один", response.json()["error"])
        with mock.patch("app.pgdata.load_preferences", return_value={"quick_ask_hotkey": "Alt+Space"}):
            quick = {"name": "Три", "hotkey": "Alt+Space", "steps": [{"type": "say", "text": "3"}]}
            self.assertEqual(self.client.post("/api/assistant/commands", json={"op": "save", "command": quick}).status_code, 400)

    def test_imported_layout_cannot_launch_an_arbitrary_exe_by_path(self):
        layout = {"monitors": [{"rect": [0, 0, 1920, 1080], "work": [0, 0, 1920, 1040], "dpi": 96, "primary": True}],
                  "windows": [{"app": {"name": "Totally Notepad", "exe": "notepad.exe",
                                       "path": r"C:\Users\me\Downloads\payload.exe"}, "rect": [0, 0, 800, 600]}]}
        shared = {"commands": [{"name": "Утро", "steps": [{"type": "layout", "layout": layout}]}]}
        data = self.client.post("/api/assistant/commands", json={"op": "import", "commands": shared}).json()
        app = data["commands"][0]["steps"][0]["layout"]["windows"][0]["app"]
        self.assertEqual(app["path"], "")
        self.assertEqual(app["exe"], "notepad.exe")

    def test_infinite_timer_values_are_refused_and_state_stays_readable(self):
        for body in ({"op": "add", "seconds": 1e999}, {"op": "add", "seconds": "abc"}, {"op": "add", "seconds": -5},
                     {"op": "snooze", "id": "x", "minutes": "abc"}, {"op": "snooze", "id": "x", "minutes": 1e999}):
            response = self.client.post("/api/assistant/timers", content=json.dumps(body))
            self.assertEqual(response.status_code, 400, body)
        self.assertEqual(self.client.get("/api/assistant/state").status_code, 200)


class LayoutMathTest(unittest.TestCase):
    def test_cascade_of_many_windows_stays_on_the_work_area(self):
        from app import assistant_layout as geo
        work = (0, 0, 1920, 1040)
        for rect in geo.tile(24, work, "cascade"):
            self.assertLessEqual(rect[2], work[2])
            self.assertLessEqual(rect[3], work[3])

    def test_imported_layout_with_huge_numbers_is_a_clean_error(self):
        from app import assistant_layout as geo
        layout = {"monitors": [{"rect": [0, 0, 1920, 1080], "dpi": 1e999}], "captured_at": 1e999,
                  "windows": [{"app": {"exe": "a.exe"}, "rect": [0, 0, 1e999, 10]},
                              {"app": {"exe": "b.exe"}, "rect": [0, 0, 100, 100], "monitor": 1e999, "z": "x"}]}
        clean = geo.validate(layout)                   # used to raise OverflowError (→ HTTP 500)
        self.assertEqual(len(clean["windows"]), 1)
        self.assertEqual(clean["monitors"][0]["dpi"], 96)
        with self.assertRaises(ac.CommandError):
            ac.validate_command({"name": "x", "steps": [{"type": "layout", "layout": {"monitors": [{"rect": [0, 0, 1e999, 1]}]}}]})


# ---------------------------------------------------------------------------------------------- Windows backend

class WinBackendTest(unittest.TestCase):
    def test_kill_does_not_take_the_whole_process_tree(self):
        from app import assistant_actions_win as win
        calls = []
        with mock.patch.object(win, "_run", side_effect=lambda args, timeout=15.0: (calls.append(args) or (0, "ok"))):
            win.WinBackend().kill(1234)
        self.assertEqual(calls, [["taskkill", "/PID", "1234", "/F"]])


# ---------------------------------------------------------------------------------------------- companion

MODEL3 = json.dumps({"Version": 3, "FileReferences": {"Moc": "m.moc3", "Textures": ["t.png"],
                                                      "Motions": {"Idle": [{"File": "idle.motion3.json"}]}}})


def _zip(files, *, compression=zipfile.ZIP_STORED):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buf.getvalue()


GOOD = {"x/x.model3.json": MODEL3, "x/m.moc3": b"MOC3" + b"\0" * 40, "x/t.png": b"\x89PNG..",
        "x/idle.motion3.json": '{"MOTIONDATA-0123456789": 1}'}


class CompanionImportTest(unittest.TestCase):
    def test_windows_special_names_are_refused(self):
        for name in ("x/CON.png", "x/nul", "x/a.txt:stream", "x/.. /evil.js", "x/dir./a.png", "x/a?.png", "x/aux.json"):
            self.assertIsNone(comp._safe_member(name), name)
        self.assertEqual(comp._safe_member("x/console.png"), "x/console.png")
        with self.assertRaises(comp.ModelError) as caught:
            comp.validate_live2d_zip(_zip(dict(GOOD, **{"x/CON.png": b"1"})))
        self.assertEqual(caught.exception.code, "unsafe_path")

    def test_encrypted_archive_is_a_clear_error(self):
        data = bytearray(_zip(GOOD))
        # set the "encrypted" flag bit in every local and central header
        for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
            start = 0
            while (start := data.find(signature, start)) != -1:
                data[start + offset] |= 0x01
                start += 4
        with self.assertRaises(comp.ModelError) as caught:
            comp.validate_live2d_zip(bytes(data))
        self.assertEqual(caught.exception.code, "encrypted")

    def test_corrupted_member_is_a_model_error_and_leaves_nothing_behind(self):
        data = bytearray(_zip(GOOD))
        at = data.find(b"MOTIONDATA-0123456789")
        data[at] = ord("X")                                   # the CRC of the motion file no longer matches
        with tempfile.TemporaryDirectory() as root:
            library = comp.Library(Path(root))
            with self.assertRaises(comp.ModelError) as caught:
                library.import_bytes("model.zip", bytes(data))
            self.assertEqual(caught.exception.code, "bad_zip")
            self.assertEqual([p.name for p in (Path(root) / "models").iterdir()], [])

    def test_runtime_download_is_capped_and_all_or_nothing(self):
        import httpx
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import companion_avatar_api as api
        names = list(comp.RUNTIME_FILES)

        def handler(request):
            if "cubismcore" in str(request.url):
                return httpx.Response(200, content=b"Live2DCubismCore" + b"x" * 100)
            return httpx.Response(200, content=b"PIXI wrong build")    # hash mismatch for the pinned files
        real_client = httpx.AsyncClient

        def client_factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_client(*args, **kwargs)
        with tempfile.TemporaryDirectory() as root:
            app = FastAPI()
            with mock.patch.object(api, "data_root", return_value=Path(root)):
                api.register(app)
                client = TestClient(app)
                with mock.patch.object(httpx, "AsyncClient", client_factory):
                    response = client.post("/api/companion/runtime/install", json={"consent": True})
                self.assertEqual(response.status_code, 502)
                self.assertEqual(response.json()["code"], "runtime_hash")
                folder = Path(root) / "runtime"
                self.assertFalse(any((folder / name).exists() for name in names), "a half-installed runtime was left")

                def huge(request):
                    return httpx.Response(200, content=b"Live2DCubismCore" + b"x" * (comp.RUNTIME_MAX_BYTES + 10))

                def client_factory_big(*args, **kwargs):
                    kwargs["transport"] = httpx.MockTransport(huge)
                    return real_client(*args, **kwargs)
                with mock.patch.object(httpx, "AsyncClient", client_factory_big):
                    response = client.post("/api/companion/runtime/install", json={"consent": True})
                self.assertEqual(response.json()["code"], "runtime_size")


# ---------------------------------------------------------------------------------------------- product cards

class ProductBudgetTest(unittest.TestCase):
    def test_budget_shorthands(self):
        self.assertEqual(pc.budget_in("15к", "RUB")["amount"], 15000)
        self.assertEqual(pc.budget_in("до 20 тыс. ₽", "RUB")["amount"], 20000)
        self.assertEqual(pc.budget_in("$1.5k", "RUB"), {"amount": 1500.0, "currency": "USD", "approx": False})
        self.assertEqual(pc.budget_in({"amount": "15k", "currency": "RUB"}, "RUB")["amount"], 15000)
        self.assertEqual(pc.budget_in("15 000 ₽", "RUB")["amount"], 15000)
        self.assertEqual(pc.budget_in(15000, "RUB")["amount"], 15000)

    def test_normalize_with_shorthand_budget_does_not_mark_everything_over(self):
        block = {"currency": "RUB", "budget": "15к", "items": [
            {"name": "Наушники X", "price": {"amount": 9990, "currency": "RUB"}, "summary": "ok"}]}
        with tempfile.TemporaryDirectory() as root:
            store = pc.Provenance(Path(root) / "cache.json") if "path" in pc.Provenance.__init__.__code__.co_varnames \
                else pc.Provenance(str(Path(root) / "cache.json"))
            result = pc.normalize(json.dumps(block), locale="ru", store=store)
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["budget"]["amount"], 15000)
        self.assertNotEqual(result["data"]["items"][0].get("budget_status"), "over")


# ---------------------------------------------------------------------------------------------- model capabilities

class CapsCacheTest(unittest.TestCase):
    def test_repointed_public_id_gets_a_fresh_card(self):
        from app import model_capabilities as caps

        class Entry:
            def __init__(self, model):
                self.id, self.prefix, self.provider, self.model = "mine-chat", "mine", "custom", model
        caps._summary_cache.clear()
        first = caps.capabilities_for("mine-chat", entry=Entry("deepseek-r1"))
        second = caps.capabilities_for("mine-chat", entry=Entry("text-embedding-3-small"))
        self.assertNotEqual(first.get("kind"), second.get("kind"))


if __name__ == "__main__":
    unittest.main()
