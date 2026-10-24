"""Round 23 panels backend: content options per kind, custom cadence (seconds/days/hours/times) with floors,
per-panel AI model, To-Do store (CRUD, quick add, repeats, reminders), notes, new kinds (proxy, calendar,
github, clock, timers), size migration, preview route and the extended PC sample."""
from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from app import panels, panels_local, panels_options, pgdata

ROOT = Path(__file__).resolve().parents[1]


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patch = patch.object(pgdata, "MEMU_DIR", self.root)
        self.patch.start()
        panels._TASKS.clear()
        self.saved_ctx = panels.context()
        self.reminders = []
        self.cancelled = []
        self.saved_rem = dict(panels_local.REMINDERS)
        panels_local.REMINDERS["schedule"] = lambda item, lang: (self.reminders.append(item["remind"]) or {"local": f"r{len(self.reminders)}"})
        panels_local.REMINDERS["cancel"] = lambda ids: self.cancelled.append(ids)

    def tearDown(self) -> None:
        panels_local.REMINDERS.update(self.saved_rem)
        panels.set_context(self.saved_ctx)
        panels._TASKS.clear()
        self.patch.stop()
        self.tmp.cleanup()


# ================================================================== options
class OptionsTest(Base):
    def test_defaults_and_cleaning(self):
        o = panels_options.clean_options("system", {"metrics": ["ram", "bogus", "cpu", "ram"], "style": "nope", "live": 1,
                                                    "procs_count": 99, "views": {"cpu": "chart", "net": "ring", "x": "bar"}})
        self.assertEqual(o["metrics"], ["ram", "cpu"])          # order kept, unknown and duplicates dropped
        self.assertEqual(o["style"], "rings")                   # bad choice → default
        self.assertEqual(o["live"], "1")
        self.assertEqual(o["procs_count"], 10)                  # clamped
        self.assertEqual(o["views"], {"cpu": "chart"})          # net cannot be a ring
        w = panels_options.clean_options("weather", {"fields": "wind, uv", "days": "9"})
        self.assertEqual(w["fields"], ["wind", "uv"])
        self.assertEqual(w["days"], 7)
        c = panels_options.clean_options("clock", {"zones": ["Europe/London | Лондон", "not a zone!", "UTC"]})
        self.assertEqual(c["zones"], ["Europe/London|Лондон", "UTC"])
        self.assertTrue(panels_options.needs_refetch("weather", {"fields": []}, {"fields": ["uv"]}))
        self.assertFalse(panels_options.needs_refetch("system", {"style": "rings"}, {"style": "bars"}))

    def test_every_kind_and_option_combo_gives_a_valid_spec(self):
        settings = panels.default_settings()
        for kind in panels_options.SCHEMA:
            if kind in ("custom", "sports", "web"):
                continue
            variants = [{}]
            for opt in panels_options.SCHEMA[kind]:
                for c in (opt.get("choices") or [])[:6]:
                    variants.append({opt["key"]: [c[0]] if opt["type"] == "multi" else c[0]})
                if opt["type"] == "bool":
                    variants.append({opt["key"]: False})
            if kind == "system":
                variants.append({"metrics": [c[0] for c in panels_options.SYSTEM_METRICS], "disk_mode": "all", "net_mode": "each"})
                for style in ("rings", "bars", "numbers", "charts"):
                    variants.append({"metrics": [c[0] for c in panels_options.SYSTEM_METRICS], "style": style})
            params = {"base": "USD", "quote": "RUB", "coin": "bitcoin", "symbol": "SBER", "url": "https://e.org/rss",
                      "target": "2031-01-01", "topic": "ai"}
            for opts in variants:
                with self.subTest(kind=kind, opts=opts):
                    _title, spec = panels.typed_spec(kind, params, settings, opts)
                    clean, errors = panels.validate_spec(spec)
                    self.assertEqual(errors, [], (kind, opts))
                    self.assertTrue(clean["blocks"])

    def test_system_spec_follows_metrics_and_views(self):
        settings = panels.default_settings()
        _t, spec = panels.typed_spec("system", {}, settings, {"metrics": ["cpu", "cores", "procs", "net"], "style": "charts",
                                                                "net_units": "bits", "temp_unit": "f"})
        text = json.dumps(spec)
        self.assertIn("{{cpu_hist}}", text)               # chart view
        self.assertIn('"type": "bars"', text)             # per-core bars
        self.assertIn("{{procs}}", text)
        self.assertIn("{{net_down_bits}}", text)          # network in bits
        self.assertNotIn("{{ram", text)
        _t, spec = panels.typed_spec("system", {}, settings, {"metrics": ["disks"], "disk_mode": "all"})
        self.assertIn("{{disks_list}}", json.dumps(spec))
        # mini chip follows the first metric
        _t, spec = panels.typed_spec("system", {}, settings, {"metrics": ["ram", "cpu"]})
        self.assertEqual(spec["mini"]["value"], "{{ram_text}}")

    def test_options_round_trip_through_update_panel(self):
        p = panels.create_panel(template="weather", params={"city": "Рыбинск", "lat": 58.0, "lon": 38.8}, start=False)
        self.assertEqual(p["options"]["fields"], ["feels", "hilo", "wind", "humidity"])
        with patch.object(panels, "start_refresh") as started:
            out = panels.update_panel(p["id"], {"options": {"fields": ["uv", "pressure"], "forecast": "hourly"}})
        self.assertEqual(out["options"]["fields"], ["uv", "pressure"])
        self.assertIn("{{hours}}", json.dumps(out["spec"]))
        self.assertIn("{{uv}}", json.dumps(out["spec"]))
        started.assert_called_once_with(p["id"], rerun=True)            # new fields need a refetch → refreshed right away
        # a purely visual option re-renders without going to the network
        sysp = panels.create_panel(template="system", start=False)
        with patch.object(panels, "start_refresh") as started:
            out_sys = panels.update_panel(sysp["id"], {"options": {"style": "bars"}})
        started.assert_not_called()
        self.assertEqual(out_sys["options"]["style"], "bars")
        # partial options keep the rest
        with patch.object(panels, "start_refresh"):
            out = panels.update_panel(p["id"], {"options": {"days": 3}})
        self.assertEqual(out["options"]["fields"], ["uv", "pressure"])
        self.assertEqual(out["options"]["days"], 3)


# ================================================================== cadence
class CadenceTest(Base):
    def test_custom_seconds_days_hours_and_times(self):
        self.assertEqual(panels._clean_cadence({"kind": "interval", "seconds": 20}, kind="system"), {"kind": "interval", "minutes": 1})
        self.assertEqual(panels._clean_cadence({"kind": "interval", "seconds": 90}, kind="weather")["minutes"], 15)
        self.assertEqual(panels._clean_cadence("45s", kind="rss")["minutes"], 5)
        self.assertEqual(panels._clean_cadence("2h", kind="rss")["minutes"], 120)
        self.assertEqual(panels._clean_cadence("3d", kind="rss")["minutes"], 4320)
        c = panels._clean_cadence({"kind": "interval", "minutes": 30, "days": [4, 0, 9, 0], "from": "9:00", "to": "18:00"}, kind="weather")
        self.assertEqual(c, {"kind": "interval", "minutes": 30, "days": [0, 4], "from": "09:00", "to": "18:00"})
        self.assertNotIn("days", panels._clean_cadence({"kind": "interval", "minutes": 30, "days": list(range(7))}))
        self.assertEqual(panels._clean_cadence({"kind": "times", "at": ["19:00", "8:00", "x"], "days": [5, 6]}),
                         {"kind": "times", "at": ["08:00", "19:00"], "days": [5, 6]})
        self.assertEqual(panels._clean_cadence({"kind": "times", "at": ["07:30"]}), {"kind": "daily", "at": "07:30"})
        self.assertEqual(panels._clean_cadence({"kind": "interval", "minutes": 5}, kind="todo"), {"kind": "manual"})

    def test_next_refresh_respects_days_and_window(self):
        sat_noon = datetime(2026, 10, 10, 12, 0).timestamp()              # Saturday
        weekdays = {"kind": "interval", "minutes": 30, "days": [0, 1, 2, 3, 4], "from": "09:00", "to": "18:00"}
        self.assertEqual(datetime.fromtimestamp(panels.next_refresh_at(weekdays, now=sat_noon)), datetime(2026, 10, 12, 9, 0))
        mon_10 = datetime(2026, 10, 12, 10, 0).timestamp()
        self.assertAlmostEqual(panels.next_refresh_at(weekdays, now=mon_10), mon_10 + 1800, delta=1)
        night = {"kind": "interval", "minutes": 60, "from": "22:00", "to": "06:00"}
        noon = datetime(2026, 10, 12, 12, 0).timestamp()
        self.assertEqual(datetime.fromtimestamp(panels.next_refresh_at(night, now=noon)), datetime(2026, 10, 12, 22, 0))
        late = datetime(2026, 10, 12, 23, 30).timestamp()
        self.assertAlmostEqual(panels.next_refresh_at(night, now=late), late + 3600, delta=1)   # overnight window
        times = {"kind": "times", "at": ["08:00", "19:00"], "days": [0]}
        self.assertEqual(datetime.fromtimestamp(panels.next_refresh_at(times, now=sat_noon)), datetime(2026, 10, 12, 8, 0))
        self.assertEqual(datetime.fromtimestamp(panels.next_refresh_at(times, now=datetime(2026, 10, 12, 9, 0).timestamp())),
                         datetime(2026, 10, 12, 19, 0))

    def test_floors_reasons_and_snapshot_limits(self):
        snap = panels.snapshot()
        self.assertIn("floor_reasons", snap["limits"])
        for kind in panels.KINDS:
            self.assertIn(kind, panels.KIND_FLOOR_MINUTES, kind)
            if kind not in panels.LOCAL_KINDS:
                self.assertIn(kind, panels.FLOOR_REASONS, kind)
        self.assertIn("system", snap["option_schema"])


# ==================================================================== model
class ModelTest(Base):
    def test_panel_model_beats_global_which_beats_default(self):
        panels.set_context(panels.PanelContext(default_model=lambda: "default-m"))
        panel = {"model": "chat-m", "ai_model": ""}
        self.assertEqual(panels._model_for(panel, {"model": ""}), "chat-m")
        self.assertEqual(panels._model_for(panel, {"model": "global-m"}), "global-m")
        self.assertEqual(panels._model_for({**panel, "ai_model": "own-m"}, {"model": "global-m"}), "own-m")
        self.assertEqual(panels._model_for({}, {"model": ""}), "default-m")

    def test_ai_model_is_stored_and_editable(self):
        p = panels.create_panel(subject="Курс золота", start=False, ai_model="own-model")
        self.assertEqual(p["ai_model"], "own-model")
        out = panels.update_panel(p["id"], {"ai_model": ""})
        self.assertEqual(out["ai_model"], "")


# ==================================================================== To-Do
class TodoTest(Base):
    def test_quick_add_parsing(self):
        today = date(2026, 10, 8)                                          # Thursday
        p = panels_local.parse_quick("Купить хлеб завтра в 18:00 !1 #дом #Еда", today=today)
        self.assertEqual(p["title"], "Купить хлеб")
        self.assertEqual(p["due"], "2026-10-09T18:00")
        self.assertEqual(p["priority"], 1)
        self.assertEqual(p["tags"], ["дом", "еда"])
        self.assertEqual(panels_local.parse_quick("Отчёт пт", today=today)["due"], "2026-10-09")
        self.assertEqual(panels_local.parse_quick("Отчёт в чт", today=today)["due"], "2026-10-15")   # next Thursday, not today
        self.assertEqual(panels_local.parse_quick("Налог 01.10", today=today)["due"], "2027-10-01")  # past date → next year
        self.assertEqual(panels_local.parse_quick("Налог 25.10.2026", today=today)["due"], "2026-10-25")
        r = panels_local.parse_quick("Полить цветы каждую неделю", today=today)
        self.assertEqual(r["repeat"], {"every": "week", "n": 1})
        self.assertEqual(r["due"], "2026-10-08")
        self.assertEqual(panels_local.parse_quick("Standup every weekday 10:00 !!", today=today)["priority"], 1)
        self.assertEqual(panels_local.parse_quick("Цена 100!1", today=today)["title"], "Цена 100!1")   # not a priority token

    def test_crud_repeat_subtasks_move_and_restore(self):
        work = panels_local.create_list("Работа", "blue")
        home = panels_local.create_list("Дом")
        a = panels_local.add_item(work["id"], {"title": "Ревью !2 #код"}, quick=True)
        self.assertEqual((a["priority"], a["tags"]), (2, ["код"]))
        b = panels_local.add_item(work["id"], {"title": "Отчёт", "subtasks": [{"title": "Цифры"}, {"title": "Графики"}]})
        sub = b["subtasks"][0]["id"]
        b = panels_local.update_item(work["id"], b["id"], {"toggle_subtask": sub})
        self.assertTrue(b["subtasks"][0]["done"])
        # plain task: done stays done
        a2 = panels_local.update_item(work["id"], a["id"], {"done": True})
        self.assertTrue(a2["done"])
        self.assertIsNotNone(a2["done_at"])
        # repeating task moves forward instead
        rep = panels_local.add_item(work["id"], {"title": "Бэкап", "due": "2020-01-06", "repeat": {"every": "week"},
                                                "subtasks": [{"title": "x", "done": True}]})
        moved = panels_local.update_item(work["id"], rep["id"], {"done": True})
        self.assertFalse(moved["done"])
        self.assertGreater(moved["due"], date.today().isoformat())
        self.assertEqual(date.fromisoformat(moved["due"]).weekday(), 0)       # still Mondays
        self.assertEqual(moved["completed_count"], 1)
        self.assertFalse(moved["subtasks"][0]["done"])
        self.assertTrue(moved["advanced"])
        # move to another list and to the top
        panels_local.move_item(work["id"], b["id"], to_list=home["id"], index=0)
        snap = {l["id"]: l for l in panels_local.snapshot()["lists"]}
        self.assertEqual(snap[home["id"]]["items"][0]["id"], b["id"])
        self.assertNotIn(b["id"], [i["id"] for i in snap[work["id"]]["items"]])
        # delete + undo
        gone = panels_local.delete_item(home["id"], b["id"])
        self.assertEqual(panels_local.snapshot()["lists"][1]["items"], [])
        panels_local.restore_item(home["id"], gone, 0)
        self.assertEqual(panels_local.snapshot()["lists"][1]["items"][0]["title"], "Отчёт")
        self.assertEqual(panels_local.clear_done(work["id"]), 1)
        with self.assertRaises(panels_local.LocalError):
            panels_local.add_item(work["id"], {"title": "   "})
        with self.assertRaises(panels_local.LocalError):
            panels_local.update_item(work["id"], "t_missing0000", {"done": True})

    def test_reminders_are_scheduled_moved_and_cancelled(self):
        lst = panels_local.create_list("R")
        item = panels_local.add_item(lst["id"], {"title": "Позвонить", "remind": "2031-05-01T09:30"})
        self.assertEqual(self.reminders, ["2031-05-01T09:30"])
        self.assertEqual(panels_local.snapshot()["lists"][0]["items"][0]["reminder"], {"local": "r1"})
        panels_local.update_item(lst["id"], item["id"], {"notes": "только заметка"})
        self.assertEqual(len(self.reminders), 1)                               # unrelated edit keeps it
        panels_local.update_item(lst["id"], item["id"], {"remind": "2031-05-02T10:00"})
        self.assertEqual(self.cancelled, [{"local": "r1"}])
        self.assertEqual(self.reminders[-1], "2031-05-02T10:00")
        panels_local.update_item(lst["id"], item["id"], {"done": True})
        self.assertEqual(len(self.cancelled), 2)                               # done → cancelled
        # a failing reminder backend never breaks the task
        panels_local.REMINDERS["schedule"] = lambda item, lang: (_ for _ in ()).throw(RuntimeError("no engine"))
        ok = panels_local.add_item(lst["id"], {"title": "Ещё", "remind": "2031-05-03T10:00"})
        self.assertEqual(ok["reminder"], {})

    def test_reminders_use_the_real_quick_ask_timer_api(self):
        """Not a stub: the real TimerManager of Quick Ask gets a «reminder» timer with the task's moment,
        and the To-Do cancel only ever removes that exact timer."""
        from app import assistant_commands
        from app.assistant_timers import TimerManager
        panels_local.REMINDERS.update(self.saved_rem)                       # real schedule/cancel
        timers = TimerManager(None)

        class Eng:
            def __init__(self):
                self.timers, self.channel = timers, "local"

            def settings(self):
                return {"reminder_channel": self.channel}
        eng = Eng()
        stranger = timers.add_timer(0, "Позвонить маме", kind="reminder", text="чужое", ends_at=time.time() + 7200)
        sent, removed = [], []
        with patch.object(assistant_commands, "engine", lambda: eng), \
                patch.object(assistant_commands, "add_reminder_task", lambda text, when, lang="ru": sent.append((text, when)) or "rem_x1"), \
                patch.object(assistant_commands, "remove_reminder_task", lambda ref, match_text=False: removed.append(ref) or 1), \
                patch.object(assistant_commands, "telegram_status", lambda: {"ok": True}):
            lst = panels_local.create_list("R")
            item = panels_local.add_item(lst["id"], {"title": "Позвонить", "remind": "2031-05-01T09:30"})
            local_id = item["reminder"]["local"]
            row = next(t for t in timers.state["timers"] if t["id"] == local_id)
            self.assertEqual(row["kind"], "reminder")
            self.assertEqual(row["ends_at"], datetime(2031, 5, 1, 9, 30).timestamp())
            self.assertEqual(row["meta"], {"todo": item["id"]})
            self.assertIn("Позвонить", row["text"])
            # done → the timer is gone, the stranger stays
            panels_local.update_item(lst["id"], item["id"], {"done": True})
            self.assertEqual([t["id"] for t in timers.state["timers"]], [stranger["id"]])
            # a stale id (already rang and removed) never cancels someone else's reminder by substring
            panels_local._cancel_reminder({"local": "Позвонить"})
            self.assertEqual([t["id"] for t in timers.state["timers"]], [stranger["id"]])
            # past moments are not scheduled at all
            past = panels_local.add_item(lst["id"], {"title": "Вчера", "remind": "2020-01-01T10:00"})
            self.assertEqual(past["reminder"], {})
            # Telegram channel goes to the Proxy scheduler, both → both
            eng.channel = "both"
            both = panels_local.add_item(lst["id"], {"title": "Оба", "remind": "2031-05-02T10:00"})
            self.assertEqual(both["reminder"]["telegram"], "rem_x1")
            self.assertIn("local", both["reminder"])
            self.assertEqual(sent[0][1].replace(tzinfo=None), datetime(2031, 5, 2, 10, 0))
            panels_local.delete_item(lst["id"], both["id"])
            self.assertEqual(removed, ["rem_x1"])
        # Telegram not paired → the reminder still rings on this computer instead of being lost
        eng.channel = "telegram"
        with patch.object(assistant_commands, "engine", lambda: eng), \
                patch.object(assistant_commands, "telegram_status", lambda: {"ok": False, "reason": "not paired"}), \
                patch.object(assistant_commands, "add_reminder_task", lambda *a, **k: self.fail("must not queue an undeliverable Telegram task")):
            only = panels_local.add_item(lst["id"], {"title": "Без бота", "remind": "2031-05-03T10:00"})
        self.assertEqual(set(only["reminder"]), {"local"})

    def test_todo_panel_and_routes(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import panels_api
        app = FastAPI()
        panels_api.register_panel_routes(app)
        client = TestClient(app)
        created = client.post("/api/pg/panels", json={"template": "todo", "placements": ["home"]}).json()["panel"]
        self.assertEqual(created["status"], "live")
        self.assertEqual(created["cadence"], {"kind": "manual"})
        lid = created["options"]["lists"][0]
        self.assertEqual(created["todo"]["lists"][0]["id"], lid)
        r = client.post(f"/api/pg/panels-todo/{lid}/items", json={"title": "Хлеб завтра !1", "quick": True}).json()
        self.assertEqual(r["item"]["title"], "Хлеб")
        iid = r["item"]["id"]
        self.assertEqual(client.patch(f"/api/pg/panels-todo/{lid}/items/{iid}", json={"done": True}).json()["item"]["done"], True)
        snap = client.get("/api/pg/panels").json()
        todo = next(p for p in snap["panels"] if p["kind"] == "todo")
        self.assertEqual(todo["todo"]["lists"][0]["items"][0]["id"], iid)
        self.assertEqual(client.delete(f"/api/pg/panels-todo/{lid}/items/{iid}").status_code, 200)
        self.assertEqual(client.delete(f"/api/pg/panels-todo/{lid}/items/{iid}").status_code, 404)
        self.assertEqual(client.post("/api/pg/panels-todo-parse", json={"text": "x #y"}).json()["parsed"]["tags"], ["y"])
        new = client.post("/api/pg/panels-todo", json={"name": "Покупки"}).json()["list"]
        self.assertEqual(client.patch(f"/api/pg/panels-todo/{new['id']}", json={"name": "Магазин"}).json()["list"]["name"], "Магазин")
        # refresh of a local panel never goes to a model or the network
        out = asyncio.run(panels.refresh_panel(created["id"], force=True))
        self.assertEqual(out["status"], "live")
        # notes
        note = client.post("/api/pg/panels", json={"template": "notes"}).json()["panel"]
        self.assertEqual(client.put(f"/api/pg/panels-note/{note['id']}", json={"text": "[ ] дело"}).status_code, 200)
        snap = client.get("/api/pg/panels").json()
        self.assertEqual(next(p for p in snap["panels"] if p["id"] == note["id"])["note"]["text"], "[ ] дело")
        client.delete(f"/api/pg/panels/{note['id']}?purge=1")
        self.assertNotIn(note["id"], panels_local.load()["notes"])
        # preview route
        prev = client.post("/api/pg/panels-preview", json={"kind": "weather", "options": {"fields": ["uv"]}, "before": {}}).json()
        self.assertTrue(prev["refetch"])
        self.assertIn("{{uv}}", json.dumps(prev["spec"]))

    def test_brief_asking_for_a_todo_list_makes_a_local_panel(self):
        p = panels.create_panel(subject="Список дел", start=False)
        self.assertEqual(p["kind"], "todo")


# ============================================================ kinds + data
class FakeNet:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    async def __call__(self, url, *, limit=0):
        self.calls.append(url)
        for key, value in self.routes.items():
            if key in url:
                return value if isinstance(value, str) else json.dumps(value)
        raise ValueError(f"HTTP 404 {url}")


class KindsTest(Base):
    def test_migration_old_large_becomes_wide(self):
        path = panels._path()
        path.write_text(json.dumps({"version": 1, "panels": [{"id": "p_old000000001", "kind": "custom", "title": "X", "size": "l",
                                                             "spec": {"blocks": [{"type": "text", "text": "x"}]}}]}), encoding="utf-8")
        old = next(p for p in panels.load()["panels"] if p["id"] == "p_old000000001")   # the seed adds «Погода» too
        self.assertEqual(old["size"], "w")
        panels.update_panel("p_old000000001", {"size": "l"})
        old = next(p for p in panels.load()["panels"] if p["id"] == "p_old000000001")
        self.assertEqual(old["size"], "l")             # after migration «l» means a detailed half-width card
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["version"], panels.SCHEMA_VERSION)

    def test_weather_options_shape_the_request_and_values(self):
        data = {"current": {"temperature_2m": 10, "apparent_temperature": 8, "relative_humidity_2m": 70, "weather_code": 1,
                            "wind_speed_10m": 3, "is_day": 1, "pressure_msl": 1013},
                "daily": {"time": ["2026-10-08", "2026-10-09", "2026-10-10"], "temperature_2m_max": [12, 13, 9],
                          "temperature_2m_min": [5, 6, 2], "weather_code": [1, 2, 3], "sunrise": ["2026-10-08T07:01"],
                          "sunset": ["2026-10-08T18:10"], "precipitation_probability_max": [40]},
                "hourly": {"time": ["2026-10-08T12:00", "2026-10-08T13:00", "2026-10-08T14:00"], "temperature_2m": [10, 11, 12],
                           "weather_code": [1, 61, 2], "precipitation_probability": [5, 60, 20], "is_day": [1, 1, 1],
                           "uv_index": [3.2, 3, 2], "visibility": [24000, 20000, 18000]}}
        net = FakeNet({"api.open-meteo.com": data})
        panel = {"source": {"params": {"lat": 58, "lon": 38, "place": "Рыбинск"}}, "history": {},
                 "options": {"fields": ["pressure", "uv", "sun", "precip", "visibility"], "forecast": "hourly", "hours": 6,
                             "pressure_unit": "mmhg", "wind_unit": "kmh"}}
        with patch.object(panels, "_http_text", net):
            values, _ = asyncio.run(panels.fetch_weather(panel, panels.default_settings()))
        self.assertIn("hourly=", net.calls[0])
        self.assertIn("wind_speed_unit=kmh", net.calls[0])
        self.assertEqual(values["pressure"], 760)
        self.assertEqual(values["uv"], 3.2)
        self.assertEqual((values["sunrise"], values["sunset"]), ("07:01", "18:10"))
        self.assertEqual(values["precip"], 5)
        self.assertEqual(values["visibility"], 24.0)
        self.assertEqual(values["hours"][1]["sub"], "60%")
        self.assertEqual(values["hours"][0]["label"], "Сейчас")

    def test_currency_extra_rows_and_history(self):
        cbr = {"Date": "2026-10-08T11:30:00+03:00", "Valute": {
            "USD": {"ID": "R01235", "Nominal": 1, "Value": 81.5, "Previous": 81.2},
            "EUR": {"ID": "R01239", "Nominal": 1, "Value": 95.1, "Previous": 95.3},
            "CNY": {"ID": "R01375", "Nominal": 1, "Value": 11.2, "Previous": 11.1}}}
        xml = ('<ValCurs><Record Date="01.10.2026" Id="R01235"><Nominal>1</Nominal><Value>80,1</Value></Record>'
               '<Record Date="02.10.2026" Id="R01235"><Nominal>1</Nominal><Value>80,9</Value></Record></ValCurs>')
        net = FakeNet({"cbr-xml-daily": cbr, "XML_dynamic": xml})
        panel = {"source": {"params": {"base": "USD", "quote": "RUB"}}, "history": {}, "options": {"extra": ["eur", "CNY"], "period": "30"}}
        with patch.object(panels, "_http_text", net):
            values, _ = asyncio.run(panels.fetch_fx(panel, panels.default_settings()))
        self.assertEqual([r["title"] for r in values["rows"]], ["EUR → RUB", "CNY → RUB"])
        self.assertEqual(values["hist"], [80.1, 80.9, 81.5])
        self.assertEqual(values["period_label"], "за месяц")

    def test_proxy_stats_panel(self):
        from app import metrics
        path = self.root / "metrics.json"
        m = metrics.Metrics(str(path))
        for ok in (True, True, False):
            m.record(provider="p", model="gpt-x", ok=ok, ms=120, prompt_tokens=10, completion_tokens=5)
        m.flush(force=True)
        self.assertTrue(path.exists())
        with patch.object(metrics, "METRICS_PATH", str(path)):
            values, _ = asyncio.run(panels.fetch_proxy({"options": {"period": "today"}}, panels.default_settings()))
        self.assertEqual(values["requests"], "3")
        self.assertEqual(values["success"], 67)
        self.assertEqual(values["tokens"], "45")
        self.assertEqual(values["models"][0]["title"], "gpt-x")
        self.assertEqual(values["period_label"], "Сегодня")
        with patch.object(metrics, "METRICS_PATH", str(self.root / "missing.json")):
            with self.assertRaises(panels.Wall) as wall:
                asyncio.run(panels.fetch_proxy({"options": {}}, panels.default_settings()))
        self.assertTrue(wall.exception.status.startswith("source_gone:"))

    def test_calendar_and_github_honest_walls_and_mapping(self):
        from app import integrations

        def not_connected(*_a, **_k):
            raise integrations.IntegrationError("Google Calendar не подключён. Откройте Настройки → Интеграции")
        with patch.object(integrations, "run_tool", not_connected):
            with self.assertRaises(panels.Wall) as wall:
                asyncio.run(panels.fetch_calendar({"options": {}}, panels.default_settings()))
        self.assertEqual(wall.exception.status, "needs_connector:google_calendar")
        events = {"events": [{"summary": "Созвон", "start": datetime.now().replace(hour=23, minute=0).isoformat(), "location": "Zoom"},
                             {"summary": "Отпуск", "start": "2031-01-02"}]}
        with patch.object(integrations, "run_tool", lambda name, args: events):
            values, _ = asyncio.run(panels.fetch_calendar({"options": {"days": "3"}}, panels.default_settings()))
        self.assertEqual(values["events"][0]["title"], "Созвон")
        self.assertIn("Zoom", values["events"][0]["subtitle"])
        self.assertIn("весь день", values["events"][1]["subtitle"])
        rows = [{"subject": {"title": "Fix bug", "url": "https://api.github.com/repos/o/r/pulls/12"},
                 "repository": {"full_name": "o/r"}, "reason": "review_requested", "updated_at": "2026-10-08T10:00:00Z"}]
        with patch.object(integrations, "_github", lambda *a, **k: rows):
            values, _ = asyncio.run(panels.fetch_github({"options": {"view": "notifications"}}, panels.default_settings()))
        self.assertEqual(values["items"][0]["url"], "https://github.com/o/r/pull/12")
        self.assertIn("ревью", values["items"][0]["subtitle"])

        def forbidden(*_a, **_k):
            raise integrations.IntegrationError("GitHub запретил действие (403: x)")
        with patch.object(integrations, "_github", forbidden):
            with self.assertRaises(panels.Wall) as wall:
                asyncio.run(panels.fetch_github({"options": {}}, panels.default_settings()))
        self.assertEqual(wall.exception.status, "needs_login:github")

        def rate_limited(*_a, **_k):
            raise integrations.IntegrationError("GitHub: превышен лимит запросов (403). Подождите")
        with patch.object(integrations, "_github", rate_limited):
            with self.assertRaises(panels.Wall) as wall:
                asyncio.run(panels.fetch_github({"options": {}}, panels.default_settings()))
        self.assertEqual(wall.exception.status, "blocked:rate-limit")          # a pause, not «sign in»

        def bad_token(*_a, **_k):
            raise integrations.IntegrationError("GitHub отклонил токен (401: Bad credentials).")
        with patch.object(integrations, "_github", bad_token):
            with self.assertRaises(panels.Wall) as wall:
                asyncio.run(panels.fetch_github({"options": {}}, panels.default_settings()))
        self.assertEqual(wall.exception.status, "needs_login:github")

    def test_local_kinds_are_manual_and_live(self):
        for kind in ("clock", "timers", "notes"):
            p = panels.create_panel(template=kind, start=False, options={"zones": ["UTC"]} if kind == "clock" else None)
            self.assertEqual(p["status"], "live", kind)
            self.assertIsNone(p.get("next_refresh"), kind)
            self.assertEqual(p["cadence"], {"kind": "manual"})
        self.assertNotIn(p["id"], panels.due_ids(panels.load(), time.time() + 10 * 86400))

    def test_sample_has_round23_fields(self):
        from app import panels_system
        prev = {"t": 0, "cpu": (10, 100), "cpu_per": [(5, 50), (5, 50)], "nets": {"eth0": (0, 0), "lo": (0, 0)}, "net": (0, 0),
                "disk_io": (0, 0)}
        cur = {"t": 1, "cpu": (20, 200), "cpu_per": [(10, 100), (6, 100)], "nets": {"eth0": (1_000_000, 500_000), "lo": (9, 9)},
               "net": (1_000_000, 500_000), "disk_io": (0, 0), "mem": {"total": 8e9, "used": 4e9}, "swap": {"total": 2e9, "used": 5e8},
               "temps": [("cpu", 55.0)], "fans": [("fan1", 1200.0)], "disks": [{"mount": "C:", "total": 5e11, "used": 1e11}],
               "uptime": 90000, "pdh": {}}
        with patch.object(panels_system, "_nvidia", lambda: None):
            out = panels_system.build(prev, cur, 1.0, lang="ru")
        self.assertEqual([c["value"] for c in out["cores"]], [90.0, 98.0])
        self.assertEqual(out["cpu"], 90.0)
        self.assertEqual(out["swap"], 25.0)
        self.assertEqual(out["ram"], 50.0)
        self.assertEqual([n["title"] for n in out["nets"]], ["eth0"])           # loopback hidden
        self.assertIn("Мбит/с", out["nets_bits"][0]["value"])
        self.assertIn("Мбит/с", out["net_down_bits"])
        self.assertEqual(out["temps_rows"][0]["value"], "55°")
        self.assertEqual(out["temps_rows_f"][0]["value"], "131°")
        self.assertEqual(out["fans_rows"][0]["value"], "1200 об/мин")
        self.assertEqual(out["uptime_text"], panels_system.fmt_duration(90000, "ru"))
        self.assertEqual(out["disks_rows"][0]["title"], "C:")
        self.assertEqual(out["battery_text"], "")                                  # desktop: no battery row, not «unknown»

    def _fresh_system_state(self, panels_system):
        saved = dict(panels_system._STATE)
        panels_system._STATE.update({"prev": None, "cache": None, "cache_at": 0.0, "pdh": None, "static": None})
        self.addCleanup(lambda: (panels_system._STATE.clear(), panels_system._STATE.update(saved)))

    def test_pc_sample_without_psutil_linux_proc(self):
        """psutil is declared in requirements, but a build without it must still show the card (/proc path)."""
        import sys as _sys
        from app import panels_system
        if not _sys.platform.startswith("linux"):
            self.skipTest("the /proc fallback is Linux-only")
        self._fresh_system_state(panels_system)
        with patch.object(panels_system, "_psutil", lambda: None), patch.object(panels_system, "IS_WINDOWS", False), \
                patch.object(panels_system, "_nvidia", lambda: None):
            out = panels_system.sample(lang="ru")
            procs = panels_system.top_processes(5)
        self.assertIsNotNone(out["cpu"])
        self.assertIsNotNone(out["ram"])
        self.assertIsInstance(out["cores"], list)
        self.assertTrue(out["cores"], "per-core load comes from /proc/stat without psutil")
        self.assertNotEqual(out["uptime_text"], "—")
        self.assertEqual(procs["procs"], [])
        self.assertIn("psutil", procs["procs_note"])
        # the whole panel fetch survives too, with «procs» asked for
        panel = {"options": {"metrics": ["cpu", "ram", "procs", "cores"]}, "history": {}}
        with patch.object(panels_system, "_psutil", lambda: None), patch.object(panels_system, "_nvidia", lambda: None):
            values, _ = asyncio.run(panels.fetch_system(panel, panels.default_settings()))
        self.assertIn("cpu_hist", values)
        self.assertEqual(values["procs"], [])

    def test_pc_sample_without_psutil_windows_path(self):
        """Windows without psutil: kernel32 counters + PDH (mocked — the ctypes calls only run on Windows)."""
        from app import panels_system
        self._fresh_system_state(panels_system)
        ticks = iter([(100.0, 1000.0), (150.0, 1200.0)])

        class FakePdh:
            def read(self):
                return {"net_rx": [("Intel(R) Ethernet", 2_000_000.0), ("isatap.{x}", 5.0)], "net_tx": [("Intel(R) Ethernet", 1000.0)],
                        "disk_r": [("_Total", 4096.0)], "disk_w": [("_Total", 0.0)],
                        "gpu": [("pid_1_luid_0x1_0x2_phys_0_eng_0_engtype_3D", 30.0), ("pid_2_luid_0x1_0x2_phys_0_eng_1_engtype_3D", 12.0)],
                        "gpu_mem": [("luid_0x1_0x2_phys_0", 2e9)], "thermal": [("\\_TZ.THM0", 320.0)],
                        "cores": [("0", 10.0), ("1", 30.0), ("_Total", 20.0), ("10", 5.0), ("2", 50.0)], "swap": [("_Total", 12.5)]}

        with patch.object(panels_system, "_psutil", lambda: None), patch.object(panels_system, "IS_WINDOWS", True), \
                patch.object(panels_system, "_win_cpu_times", lambda: next(ticks)), \
                patch.object(panels_system, "_win_memory", lambda: {"total": 16e9, "used": 8e9}), \
                patch.object(panels_system, "_win_disks", lambda: [{"mount": "C:", "total": 5e11, "used": 2.5e11}]), \
                patch.object(panels_system, "_win_battery", lambda: None), patch.object(panels_system, "_win_uptime", lambda: 7200.0), \
                patch.object(panels_system, "_win_registry", lambda *_a: "Intel(R) Core(TM) i7 CPU"), \
                patch.object(panels_system, "_win_gpu_name", lambda: "GeForce"), patch.object(panels_system, "_nvidia", lambda: None), \
                patch.object(panels_system, "_Pdh", FakePdh), patch.object(panels_system.time, "sleep", lambda *_: None):
            out = panels_system.sample(lang="ru")
        self.assertEqual(out["cpu"], 75.0)
        self.assertEqual(out["ram"], 50.0)
        self.assertEqual(out["disk_label"], "Диск C:")
        self.assertEqual([c["value"] for c in out["cores"]], [10.0, 30.0, 50.0, 5.0])     # numeric order, no _Total
        self.assertEqual(out["swap"], 12.5)
        self.assertEqual([n["title"] for n in out["nets"]], ["Intel(R) Ethernet"])          # tunnels hidden
        self.assertEqual(out["gpu"], 42.0)                                                    # engines of one adapter add up
        self.assertEqual(out["temp_src"], "acpi")
        self.assertEqual(out["battery_text"], "")
        self.assertEqual(out["cpu_name"], "Intel Core i7")
        self.assertIn("Windows", out["fans_note"])

    def test_short_countdown_label_and_thread_plurals(self):
        year = datetime.now().year
        s = panels.default_settings()
        self.assertEqual(panels.countdown_values(f"{year}-12-28T09:00", s)["target_label"], "28.12 09:00")
        self.assertEqual(panels.countdown_values(f"{year + 2}-01-02T00:00", s)["target_label"], f"02.01.{year + 2}")
        labels = {n: panels.system_values({"threads": n}, "ru")["cores_label"] for n in (1, 2, 5, 11, 12, 21, 22, 24)}
        self.assertEqual(labels, {1: "1 поток", 2: "2 потока", 5: "5 потоков", 11: "11 потоков", 12: "12 потоков",
                                  21: "21 поток", 22: "22 потока", 24: "24 потока"})
        self.assertEqual(panels.system_values({"threads": 1}, "en")["cores_label"], "1 thread")

    def test_new_modules_are_bundled(self):
        for name in ("build.py", "MemuAI.spec"):
            text = (ROOT / name).read_text(encoding="utf-8")
            for module in ("app.panels_options", "app.panels_local"):
                self.assertIn(module, text, f"{module} missing in {name}")


if __name__ == "__main__":
    unittest.main()
