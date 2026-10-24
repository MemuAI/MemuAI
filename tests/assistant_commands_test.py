"""Round 22: Quick Ask assistant commands (app/assistant_*.py).

Intent matcher (ru/en), parameter validation, dispatch through a fake Windows
backend, permission tiers + confirmation + undo + rate limits, custom commands
(validation, storage, import, routines with conditions), window-layout geometry,
timers/Pomodoro with a fake clock, reminders through the scheduler, voice
preference overlay and the HTTP routes. Nothing here touches Windows.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

from app import assistant_apps as apps  # noqa: E402
from app import assistant_catalog as catalog  # noqa: E402
from app import assistant_commands as ac  # noqa: E402
from app import assistant_intents as intents  # noqa: E402
from app import assistant_layout as geo  # noqa: E402
from app import assistant_timers as timers_mod  # noqa: E402
from app.assistant_actions_win import ActionError, Backend, Unsupported, parse_keys  # noqa: E402

NOW = datetime(2026, 10, 8, 10, 30)

_SAVED: dict = {}


def setUpModule() -> None:  # Round 25: create_app() registers the assistant tool globally — leave no trace
    from app import quick_ask_api
    _SAVED["tools"] = dict(quick_ask_api.EXTRA_TOOLS)
    _SAVED["routes"] = list(quick_ask_api.EXTRA_ROUTES)


def tearDownModule() -> None:   # (quick_ask_test.test_disabled_tools_refuse failed when run after this module)
    from app import quick_ask_api
    quick_ask_api.EXTRA_TOOLS.clear()
    quick_ask_api.EXTRA_TOOLS.update(_SAVED.get("tools", {}))
    quick_ask_api.EXTRA_ROUTES[:] = _SAVED.get("routes", [])


def run(coro):
    return asyncio.run(coro)


class FakeBackend(Backend):
    name = "fake"

    def __init__(self):
        self.calls = []
        self.volume = {"level": 50, "mute": False}
        self.brightness = 70
        self.dark = False
        self.clip = "old clip"
        self.wins = [
            {"hwnd": 101, "title": "Telegram", "pid": 11, "path": r"C:\Apps\Telegram.exe", "exe": "telegram.exe",
             "app": "Telegram", "state": "normal", "rect": [100, 100, 900, 700], "normal_rect": [100, 100, 900, 700], "own": False},
            {"hwnd": 102, "title": "main.py — Visual Studio Code", "pid": 12, "path": r"C:\VS\Code.exe", "exe": "code.exe",
             "app": "Code", "state": "maximized", "rect": [0, 0, 1920, 1040], "normal_rect": [200, 150, 1400, 900], "own": False},
        ]
        self.mons = [{"index": 0, "device": r"\\.\DISPLAY1", "rect": [0, 0, 1920, 1080], "work": [0, 0, 1920, 1040], "dpi": 96, "primary": True},
                     {"index": 1, "device": r"\\.\DISPLAY2", "rect": [-2560, -200, 0, 1240], "work": [-2560, -200, 0, 1200], "dpi": 144, "primary": False}]
        self.procs = [{"name": "chrome.exe", "pid": 501, "memory": 300 << 20}, {"name": "chrome.exe", "pid": 502, "memory": 100 << 20},
                      {"name": "lsass.exe", "pid": 700, "memory": 10 << 20}]

    def _log(self, *args):
        self.calls.append(args)

    def volume_get(self):
        return dict(self.volume)

    def volume_set(self, level):
        self._log("volume_set", level)
        self.volume["level"] = level
        return dict(self.volume)

    def volume_mute(self, mute):
        self.volume["mute"] = (not self.volume["mute"]) if mute is None else bool(mute)
        self._log("mute", self.volume["mute"])
        return dict(self.volume)

    def media_key(self, key):
        self._log("media", key)

    def brightness_get(self):
        return {"level": self.brightness, "displays": []}

    def brightness_set(self, level):
        self.brightness = level
        self._log("brightness", level)
        return {"level": level}

    def theme_get(self):
        return {"dark": self.dark}

    def theme_set(self, dark):
        self.dark = dark
        self._log("theme", dark)
        return {"dark": dark}

    def radio(self, kind, on):
        self._log("radio", kind, on)
        return {"before": "On", "on": on}

    def clipboard_get(self):
        return self.clip

    def clipboard_set(self, text):
        self.clip = text
        self._log("clip", text)

    def windows(self, include_minimized=True):
        return [dict(w) for w in self.wins]

    def foreground(self):
        return dict(self.wins[0])

    def monitors(self):
        return [dict(m) for m in self.mons]

    def place_window(self, hwnd, rect, state="normal"):
        self._log("place", hwnd, tuple(rect), state)

    def window_cmd(self, hwnd, action):
        self._log("wcmd", hwnd, action)

    def close_window(self, hwnd):
        self._log("close", hwnd)

    def focus_window(self, hwnd):
        self._log("focus", hwnd)

    def send_keys(self, combo):
        self._log("keys", combo)

    def type_text(self, text):
        self._log("type", text)

    def app_index(self):
        return apps.merge_index(apps.builtin_entries(), [
            apps._entry("Telegram Desktop", "lnk", r"C:\Start\Telegram Desktop.lnk"),
            apps._entry("Google Chrome", "lnk", r"C:\Start\Google Chrome.lnk"),
            apps._entry("Visual Studio Code", "lnk", r"C:\Start\Visual Studio Code.lnk"),
            apps._entry("Uninstall Chrome", "lnk", r"C:\Start\x.lnk")])

    def launch_app(self, entry):
        self._log("launch", entry["name"])

    def open_target(self, target):
        self._log("open", target)

    def processes(self):
        return list(self.procs)

    def kill(self, pid):
        self._log("kill", pid)

    def shutdown(self, delay=0):
        self._log("shutdown", delay)

    def cancel_shutdown(self):
        self._log("cancel_shutdown")
        return True

    def lock(self):
        self._log("lock")

    def recycle_empty(self):
        return {"items": 3, "bytes": 2048, "emptied": True}

    def sysinfo(self, what="all"):
        return {"battery": {"present": True, "percent": 81, "plugged": False, "seconds_left": 7200}}


class EngineCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.backend = FakeBackend()
        self.clock = [1_000_000.0]
        self.timers = timers_mod.TimerManager(self.tmp / "timers.json", clock=lambda: self.clock[0])
        self.engine = ac.Engine(backend=self.backend, store=ac.Store(self.tmp / "assistant.json"),
                                audit=ac.Audit(self.tmp / "audit.jsonl"), timers=self.timers, now_fn=lambda: NOW)
        self.patch_open = mock.patch.object(ac.sys, "platform", "win32")
        self.patch_open.start()

    def tearDown(self):
        self.patch_open.stop()

    def settings(self, **patch):
        self.engine.store.update(lambda d: d.__setitem__("settings", ac.normalize_settings({**d["settings"], **patch})))


# ---------------------------------------------------------------------- intents

class IntentTest(unittest.TestCase):
    def m(self, text):
        return intents.match(text, now=NOW)

    def test_russian_commands(self):
        cases = {
            "открой телеграм": ("app.open", {"name": "телеграм"}),
            "Мему, запусти блокнот пожалуйста": ("app.open", {"name": "блокнот"}),
            "сделай громкость 30": ("volume.set", {"level": 30.0}),
            "громкость на тридцать пять процентов": ("volume.set", {"level": 35.0}),
            "громче": ("volume.set", {"delta": 10}),
            "тише на 20": ("volume.set", {"delta": -20.0}),
            "выключи звук": ("volume.mute", {"mute": True}),
            "включи звук": ("volume.mute", {"mute": False}),
            "следующий трек": ("media.key", {"key": "next"}),
            "пауза": ("media.key", {"key": "play_pause"}),
            "яркость 50": ("brightness.set", {"level": 50.0}),
            "темная тема": ("theme.set", {"mode": "dark"}),
            "выключи компьютер": ("power.shutdown", {}),
            "перезагрузи компьютер": ("power.restart", {}),
            "заблокируй экран": ("lock.screen", {}),
            "спящий режим": ("power.sleep", {}),
            "отмени выключение": ("power.cancel", {}),
            "включи вайфай": ("wifi.set", {"on": True}),
            "выключи блютуз": ("bluetooth.set", {"on": False}),
            "поставь таймер на 25 минут для чая": ("timer.start", {"seconds": 1500, "label": "для чая"}),
            "засеки полчаса": ("timer.start", {"seconds": 1800}),
            "таймер на 1 час 30 минут": ("timer.start", {"seconds": 5400}),
            "запусти помодоро": ("pomodoro.control", {"action": "start"}),
            "останови помодоро": ("pomodoro.control", {"action": "stop"}),
            "сделай скриншот": ("screenshot.take", {"mode": "screen"}),
            "закажи роллы": ("food.order", {"query": "роллы"}),
            "закажи наушники на озоне": ("shop.search", {"query": "наушники", "service": "ozon"}),
            "вызови такси до аэропорта": ("taxi.order", {"to": "аэропорта"}),
            "нарисуй кота в космосе": ("image.generate", {"prompt": "кота в космосе"}),
            "курс доллара": ("currency.convert", {"amount": 1, "from": "USD", "to": "RUB"}),
            "разверни окно": ("window.action", {"action": "maximize"}),
            "сверни все окна": ("window.minimize_all", {}),
            "перенеси окно на другой монитор": ("window.move", {"monitor": "next"}),
            "расставь окна сеткой": ("window.arrange", {"layout": "grid"}),
            "сохрани раскладку работа": ("layout.capture", {"name": "работа"}),
            "очисти корзину": ("recycle.empty", {}),
            "сколько заряда": ("sysinfo.get", {"what": "battery"}),
            "убей процесс chrome": ("process.kill", {"name": "chrome"}),
            "закрой хром": ("app.close", {"name": "хром"}),
            "открой загрузки": ("open.folder", {"folder": "downloads"}),
            "открой настройки звука": ("open.settings", {"page": "sound"}),
            "открой ютуб": ("open.url", {"url": "https://www.youtube.com"}),
            "загугли рецепт борща": ("web.search", {"query": "рецепт борща", "engine": "google"}),
            "напечатай привет мир": ("type.text", {"text": "привет мир"}),
            "заметка купить молоко": ("note.add", {"text": "купить молоко"}),
            "открой настройки быстрого доступа": ("memuai.open", {"target": "quick_settings"}),
            "что ты умеешь": ("assistant.help", {}),
            "отмени": ("assistant.undo", {}),
            "новый рабочий стол": ("desktop.virtual", {"action": "new"}),
        }
        for text, (action, params) in cases.items():
            with self.subTest(text=text):
                found = self.m(text)
                self.assertIsNotNone(found, text)
                self.assertEqual(found["action"], action)
                self.assertEqual(found["params"], params)

    def test_english_commands(self):
        cases = {
            "open telegram": ("app.open", {"name": "telegram"}),
            "set volume to 40": ("volume.set", {"level": 40.0}),
            "mute": ("volume.mute", {"mute": True}),
            "next track": ("media.key", {"key": "next"}),
            "set a timer for 10 minutes": ("timer.start", {"seconds": 600}),
            "shut down the computer": ("power.shutdown", {}),
            "lock the screen": ("lock.screen", {}),
            "order sushi": ("food.order", {"query": "sushi"}),
            "generate an image of a red fox": ("image.generate", {"prompt": "a red fox"}),
            "play despacito on youtube": ("media.search", {"query": "despacito", "service": "youtube"}),
            "weather in London": ("weather.get", {"when": "now", "city": "London"}),
            "100 usd to eur": ("currency.convert", {"amount": 100.0, "from": "USD", "to": "EUR"}),
            "maximize": ("window.action", {"action": "maximize"}),
            "take a screenshot": ("screenshot.take", {"mode": "screen"}),
            "start pomodoro": ("pomodoro.control", {"action": "start"}),
            "what can you do": ("assistant.help", {}),
        }
        for text, (action, params) in cases.items():
            with self.subTest(text=text):
                found = self.m(text)
                self.assertIsNotNone(found, text)
                self.assertEqual((found["action"], found["params"]), (action, params))

    def test_reminders(self):
        found = self.m("напомни в телеграме в 15:00 купить хлеб")
        self.assertEqual(found["params"], {"channel": "telegram", "at": "2026-10-08T15:00", "text": "купить хлеб"})
        found = self.m("напомни мне через 20 минут позвонить маме")
        self.assertEqual(found["params"], {"channel": "local", "in_minutes": 20.0, "text": "позвонить маме"})
        found = self.m("remind me in telegram at 3pm to call mom")
        self.assertEqual(found["params"], {"channel": "telegram", "at": "2026-10-08T15:00", "text": "call mom"})
        found = self.m("remind me tomorrow at 9 about the meeting")
        self.assertEqual(found["params"]["at"], "2026-10-09T09:00")
        self.assertEqual(found["params"]["text"], "the meeting")
        found = self.m("напомни завтра в 9 утра и здесь и в телеграме про встречу")
        self.assertEqual(found["params"]["channel"], "both")
        self.assertEqual(found["params"]["text"], "встречу")
        # no time → the model asks
        self.assertIsNone(self.m("напомни купить хлеб"))

    def test_alarm_prefers_next_occurrence(self):
        found = self.m("поставь будильник на 7:30")
        self.assertEqual(found["params"], {"time": "07:30", "date": "2026-10-09"})
        found = self.m("разбуди меня завтра в 8 утра")
        self.assertEqual(found["params"], {"time": "08:00", "date": "2026-10-09"})

    def test_questions_and_ambiguous_go_to_the_model(self):
        for text in ("как сделать громче?", "как выключить компьютер", "найди ошибку в коде", "выключи",
                     "how do I mute zoom?", "explain timers", "почему не работает звук", "", "a" * 500,
                     "открой\nтелеграм", "создай план на неделю"):
            with self.subTest(text=text):
                self.assertIsNone(self.m(text))

    def test_questions_allowed_for_info_actions(self):
        self.assertEqual(self.m("what is the volume?")["action"], "volume.get")
        self.assertEqual(self.m("который час?")["action"], "time.now")

    def test_custom_trigger_fuzzy(self):
        commands = [{"id": "gm", "name": "Доброе утро", "enabled": True, "triggers": ["доброе утро", "good morning"], "steps": []},
                    {"id": "off", "name": "Выкл", "enabled": False, "triggers": ["открой телеграм"], "steps": []}]
        hit = intents.match("Доброе утро!", now=NOW, commands=commands)
        self.assertEqual(hit["action"], "command.run")
        self.assertEqual(hit["params"]["id"], "gm")
        self.assertEqual(intents.match("доброе утор", now=NOW, commands=commands)["params"]["id"], "gm")   # typo
        self.assertEqual(intents.match("good mornin", now=NOW, commands=commands)["params"]["id"], "gm")
        # a disabled custom command never shadows the built-in rule
        self.assertEqual(intents.match("открой телеграм", now=NOW, commands=commands)["action"], "app.open")

    def test_disabled_actions_fall_through(self):
        self.assertIsNone(intents.match("громкость 30", now=NOW, disabled=["volume.set"]))

    def test_durations_and_numbers(self):
        self.assertEqual(intents.find_duration("5 минут 30 секунд")[0], 330)
        self.assertEqual(intents.find_duration("полтора часа")[0], 5400)
        self.assertEqual(intents.find_duration("two hours and ten minutes")[0], 7800)
        self.assertEqual(intents.find_duration("пять минут")[0], 300)
        self.assertEqual(intents.find_duration("на 1:30")[0], 90)
        self.assertIsNone(intents.find_duration("купить хлеб"))
        self.assertEqual(intents.parse_number("двадцать пять"), 25)
        self.assertEqual(intents.parse_number("forty two"), 42)
        self.assertEqual(intents.duration_text(3725, "ru"), "1 ч 2 мин 5 с")

    def test_clock_times(self):
        self.assertEqual(intents.find_clock("в 9", NOW)[0], datetime(2026, 10, 8, 21, 0))       # 9 said at 10:30 → 21:00
        self.assertEqual(intents.find_clock("в 9:00", NOW)[0], datetime(2026, 10, 9, 9, 0))     # explicit minutes → tomorrow
        self.assertEqual(intents.find_clock("at 3pm", NOW)[0], datetime(2026, 10, 8, 15, 0))
        self.assertEqual(intents.find_clock("завтра в 10 вечера", NOW)[0], datetime(2026, 10, 9, 22, 0))
        self.assertIsNone(intents.find_clock("на 5 минут", NOW))


# ---------------------------------------------------------------------- catalog

class CatalogTest(unittest.TestCase):
    def test_every_action_is_wellformed(self):
        for row in catalog.ACTIONS:
            self.assertIn(row["tier"], catalog.TIERS)
            for key in row["required"]:
                self.assertIn(key, row["params"])
        power = [r["id"] for r in catalog.ACTIONS if r["cat"] == "power" and r["id"] not in ("lock.screen", "power.cancel")]
        self.assertTrue(all(catalog.BY_ID[a]["tier"] == "dangerous" for a in power))
        self.assertEqual(catalog.BY_ID["shell.run"]["tier"], "dangerous")
        self.assertTrue(catalog.BY_ID["shell.run"]["advanced"])

    def test_validate_params(self):
        self.assertEqual(catalog.validate_params("volume.set", {"level": "35%"}), {"level": 35})
        self.assertEqual(catalog.validate_params("volume.set", {"level": 300}), {"level": 100})
        self.assertEqual(catalog.validate_params("wifi.set", {"on": "да"}), {"on": True})
        self.assertEqual(catalog.validate_params("media.key", {"key": "NEXT", "junk": 1}), {"key": "next"})
        with self.assertRaises(catalog.ParamError):
            catalog.validate_params("media.key", {"key": "explode"})
        with self.assertRaises(catalog.ParamError):
            catalog.validate_params("app.open", {})
        with self.assertRaises(catalog.ParamError):
            catalog.validate_params("volume.set", {"level": float("nan")})

    def test_tool_schemas_are_portable(self):
        tools = catalog.openai_tools()
        self.assertEqual(len(tools), len(catalog.ACTIONS))
        names = {t["function"]["name"] for t in tools}
        self.assertIn("volume_set", names)
        self.assertEqual(catalog.tool_name_to_action("volume_set"), "volume.set")
        json.dumps(tools)          # serialisable
        prompt = ac.tool_prompt(ac.normalize_settings({}), "en")
        self.assertIn("volume.set(", prompt)
        self.assertNotIn("shell.run(", prompt)        # advanced mode off
        self.assertLess(len(prompt), 9000)


# ---------------------------------------------------------------------- engine

class DispatchTest(EngineCase):
    def test_volume_with_undo(self):
        card = run(self.engine.run("volume.set", {"delta": 15}))
        self.assertEqual(card["status"], "done")
        self.assertEqual(self.backend.volume["level"], 65)
        self.assertTrue(card["undo"])
        undo = run(self.engine.undo(card["undo"]))
        self.assertEqual(undo["status"], "done")
        self.assertEqual(self.backend.volume["level"], 50)
        # an undo id works once
        self.assertEqual(run(self.engine.undo(card["undo"]))["status"], "error")

    def test_undo_last_by_phrase(self):
        run(self.engine.run("theme.set", {"mode": "dark"}))
        self.assertTrue(self.backend.dark)
        card = run(self.engine.run("assistant.undo", {}))
        self.assertEqual(card["status"], "done")
        self.assertFalse(self.backend.dark)

    def test_confirm_tier(self):
        card = run(self.engine.run("app.close", {"name": "телеграм"}))
        self.assertEqual(card["status"], "confirm")
        self.assertEqual(card["confirm"]["countdown"], 0)
        self.assertNotIn(("close", 101), self.backend.calls)
        done = run(self.engine.confirm(card["confirm"]["token"], True))
        self.assertEqual(done["status"], "done")
        self.assertIn(("close", 101), self.backend.calls)
        # a token is single-use
        self.assertEqual(run(self.engine.confirm(card["confirm"]["token"], True))["code"], "expired")

    def test_dangerous_needs_countdown_and_can_be_cancelled(self):
        card = run(self.engine.run("power.shutdown", {}))
        self.assertEqual(card["status"], "confirm")
        self.assertEqual(card["confirm"]["tier"], "dangerous")
        self.assertEqual(card["confirm"]["countdown"], 5)
        cancelled = run(self.engine.confirm(card["confirm"]["token"], False))
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertFalse(any(c[0] == "shutdown" for c in self.backend.calls))
        card = run(self.engine.run("power.shutdown", {}))
        done = run(self.engine.confirm(card["confirm"]["token"], True))
        self.assertEqual(done["status"], "done")
        self.assertIn(("shutdown", 5), self.backend.calls)
        # undo of a shutdown = shutdown /a
        run(self.engine.undo(done["undo"]))
        self.assertIn(("cancel_shutdown",), self.backend.calls)

    def test_dangerous_is_never_trusted(self):
        self.settings(trusted_actions=["power.shutdown", "app.close"])
        self.assertEqual(self.engine.settings()["trusted_actions"], ["app.close"])
        self.assertEqual(run(self.engine.run("power.shutdown", {}))["status"], "confirm")
        self.assertEqual(run(self.engine.run("app.close", {"name": "telegram"}))["status"], "done")
        # the model cannot use the trust the user gave to local commands
        self.assertEqual(run(self.engine.run("app.close", {"name": "telegram"}, source="model"))["status"], "confirm")

    def test_always_confirm_and_disabled(self):
        self.settings(always_confirm=["volume.set"], disabled_actions=["lock.screen"])
        self.assertEqual(run(self.engine.run("volume.set", {"level": 10}))["status"], "confirm")
        card = run(self.engine.run("lock.screen", {}))
        self.assertEqual(card["status"], "disabled")
        self.assertNotIn(("lock",), self.backend.calls)
        self.settings(enabled=False)
        self.assertEqual(run(self.engine.run("time.now", {}))["status"], "disabled")

    def test_advanced_shell_is_off_by_default(self):
        card = run(self.engine.run("shell.run", {"command": "Remove-Item C:\\ -Recurse"}, source="model"))
        self.assertEqual(card["status"], "disabled")
        self.assertEqual(card["code"], "advanced_off")
        self.assertIsNone(self.engine.match("shell.run"))

    def test_executables_need_confirmation(self):
        path = self.tmp / "tool.exe"
        path.write_text("x")
        self.assertEqual(run(self.engine.run("open.path", {"path": str(path)}))["status"], "confirm")
        doc = self.tmp / "notes.txt"
        doc.write_text("x")
        self.assertEqual(run(self.engine.run("open.path", {"path": str(doc)}))["status"], "done")

    def test_rate_limit(self):
        self.settings(model_rate_per_minute=3)
        statuses = [run(self.engine.run("time.now", {}, source="model"))["status"] for _ in range(5)]
        self.assertEqual(statuses, ["done", "done", "done", "error", "error"])

    def test_bad_params_and_unknown(self):
        self.assertEqual(run(self.engine.run("volume.set", {"level": "loud"}))["code"], "bad_params")
        self.assertEqual(run(self.engine.run("nuke.everything", {}))["code"], "unknown")
        self.assertEqual(run(self.engine.run("_window.place", {"hwnd": 1}))["code"], "unknown")   # internal: undo only

    def test_unsupported_backend_is_a_card_not_a_crash(self):
        engine = ac.Engine(backend=Backend(), store=self.engine.store, audit=self.engine.audit, timers=self.timers)
        card = run(engine.run("volume.get", {}))
        self.assertEqual(card["status"], "unsupported")

    def test_audit_log(self):
        run(self.engine.run("volume.set", {"level": 20}))
        run(self.engine.run("type.text", {"text": "секретный текст " * 20}))
        rows = self.engine.audit.read()
        self.assertEqual(rows[0]["action"], "type.text")
        self.assertEqual(rows[0]["status"], "confirm")
        self.assertLessEqual(len(rows[0]["params"]["text"]), 81)
        self.assertEqual(rows[1]["action"], "volume.set")

    def test_apps(self):
        card = run(self.engine.run("app.open", {"name": "телега"}))
        self.assertEqual(card["status"], "done")
        self.assertIn(("launch", "Telegram Desktop"), self.backend.calls)
        run(self.engine.run("app.open", {"name": "хром"}))
        self.assertIn(("launch", "Google Chrome"), self.backend.calls)
        run(self.engine.run("app.open", {"name": "блокнот"}))
        self.assertIn(("launch", "Блокнот"), self.backend.calls)
        card = run(self.engine.run("app.open", {"name": "ютуб"}))     # no app → known site
        self.assertIn(("open", "https://www.youtube.com"), self.backend.calls)
        card = run(self.engine.run("app.open", {"name": "фотошопище"}))
        self.assertEqual(card["code"], "not_found")
        card = run(self.engine.run("app.switch", {"name": "vs code"}))
        self.assertIn(("focus", 102), self.backend.calls)

    def test_kill_refuses_system_processes(self):
        card = run(self.engine.run("process.kill", {"name": "lsass"}, confirmed=True))
        self.assertEqual(card["code"], "blocked")
        card = run(self.engine.run("process.kill", {"name": "chrome"}, confirmed=True))
        self.assertEqual(card["status"], "done")
        self.assertIn(("kill", 501), self.backend.calls)
        self.assertIn(("kill", 502), self.backend.calls)

    def test_windows(self):
        card = run(self.engine.run("window.action", {"action": "snap_left"}))
        self.assertIn(("place", 101, (0, 0, 960, 1040), "normal"), self.backend.calls)
        run(self.engine.undo(card["undo"]))
        self.assertIn(("place", 101, (100, 100, 900, 700), "normal"), self.backend.calls)
        run(self.engine.run("window.move", {"monitor": "next"}))
        moved = [c for c in self.backend.calls if c[0] == "place"][-1]
        self.assertLess(moved[2][0], 0)         # on the left monitor (negative X)
        self.assertGreaterEqual(moved[2][0], -2560)

    def test_web_actions_build_urls(self):
        run(self.engine.run("food.order", {"query": "роллы"}, lang="ru"))
        self.assertIn(("open", "https://eda.yandex.ru/search?query=%D1%80%D0%BE%D0%BB%D0%BB%D1%8B"), self.backend.calls)
        card = run(self.engine.run("food.order", {"query": "sushi"}, lang="en"))
        self.assertIn("never orders", card["message"])
        self.assertTrue(any(c[0] == "open" and "ubereats" in c[1] for c in self.backend.calls))
        card = run(self.engine.run("open.url", {"url": "javascript:alert(1)"}))
        self.assertEqual(card["code"], "bad_params")
        self.assertEqual(ac.normalize_url("github.com"), "https://github.com")
        self.assertIn("yandex.ru/maps/?rtext=", ac.map_url("Кремль", "Дом", "yandex_maps"))

    def test_sysinfo_and_info_cards(self):
        card = run(self.engine.run("sysinfo.get", {"what": "battery"}))
        self.assertIn("81%", card["message"])
        card = run(self.engine.run("time.now", {}))
        self.assertTrue(card["message"].startswith("10:30"))


class TimerActionsTest(EngineCase):
    def test_timer_start_cancel_undo(self):
        card = run(self.engine.run("timer.start", {"seconds": 300, "label": "чай"}))
        self.assertEqual(card["status"], "done")
        self.assertEqual(len(self.timers.snapshot()["timers"]), 1)
        cancel = run(self.engine.run("timer.cancel", {"label": "чай"}))
        self.assertEqual(cancel["status"], "done")
        self.assertEqual(len(self.timers.snapshot()["timers"]), 0)
        run(self.engine.undo(cancel["undo"]))
        self.assertEqual(self.timers.snapshot()["timers"][0]["label"], "чай")

    def test_alarm_and_pomodoro(self):
        card = run(self.engine.run("alarm.set", {"time": "07:30"}))
        self.assertIn("07:30", card["message"])
        alarm = self.timers.snapshot()["timers"][0]
        self.assertEqual(datetime.fromtimestamp(alarm["ends_at"]), datetime(2026, 10, 9, 7, 30))
        card = run(self.engine.run("pomodoro.control", {"action": "start", "work": 50}))
        self.assertEqual(self.timers.snapshot()["pomodoro"]["work"], 50)
        self.assertEqual(run(self.engine.run("pomodoro.control", {"action": "stats"}))["status"], "done")

    def test_local_reminder(self):
        card = run(self.engine.run("reminder.create", {"text": "позвонить", "in_minutes": 20}))
        self.assertEqual(card["status"], "done")
        rows = self.timers.snapshot()["timers"]
        self.assertEqual(rows[0]["kind"], "reminder")
        self.assertEqual(rows[0]["text"], "позвонить")
        run(self.engine.undo(card["undo"]))
        self.assertEqual(self.timers.snapshot()["timers"], [])

    def test_past_reminder_is_refused(self):
        card = run(self.engine.run("reminder.create", {"text": "x", "at": "2020-01-01T10:00"}))
        self.assertEqual(card["code"], "past")


class TelegramReminderTest(EngineCase):
    def setUp(self):
        super().setUp()
        from app import pgdata
        self.pgdata = pgdata
        pgdata.save_schedules([])

    def test_telegram_needs_a_paired_bot(self):
        with mock.patch.object(ac, "telegram_status", return_value={"ok": False, "reason": "not paired"}):
            card = run(self.engine.run("reminder.create", {"text": "купить хлеб", "at": "15:00", "channel": "telegram"}))
        self.assertEqual(card["code"], "telegram_off")
        self.assertEqual(self.pgdata.load_schedules(), [])

    def test_telegram_reminder_goes_through_the_scheduler(self):
        with mock.patch.object(ac, "telegram_status", return_value={"ok": True}):
            card = run(self.engine.run("reminder.create", {"text": "купить хлеб", "at": "15:00", "channel": "telegram"}))
        self.assertEqual(card["status"], "done")
        tasks = self.pgdata.load_schedules()
        self.assertEqual(len(tasks), 1)
        task = tasks[0]
        self.assertEqual(task["kind"], "reminder")
        self.assertEqual(task["run_at"], "2026-10-08T15:00:00")
        self.assertTrue(task["notify_telegram"])
        self.assertEqual(ac.list_reminder_tasks()[0]["text"], "купить хлеб")
        # the scheduler delivers it without a model
        from app import scheduler
        sent = []

        async def fake_send(client, token, chat_id, text, markup=None, **kw):
            sent.append((token, chat_id, text, kw.get("silent")))
            return [1]
        prefs = {**self.pgdata.load_preferences(), "telegram_enabled": True, "telegram_bot_token": "1:tok", "telegram_chat_id": "42"}
        with mock.patch("app.telegram_bot._send", fake_send), \
                mock.patch("app.pgdata.load_preferences", return_value=prefs), \
                mock.patch("app.telegram_bot._in_quiet_hours", return_value=False):
            run(scheduler._run_task(task))
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][:2], ("1:tok", "42"))
        self.assertIn("купить хлеб", sent[0][2])
        # undo removes the scheduled task
        run(self.engine.undo(card["undo"]))
        self.assertEqual(self.pgdata.load_schedules(), [])

    def test_scheduler_tick_claims_due_reminder(self):
        from app import scheduler
        when = datetime(2026, 10, 8, 15, 0)
        ac.add_reminder_task("x", when)
        delivered = []

        async def fake_deliver(task):
            delivered.append(task["id"])
            return True, "telegram"
        with mock.patch("app.assistant_commands.deliver_reminder", fake_deliver):
            async def tick():
                await scheduler._scheduler_tick(now=when.timestamp() - 60)      # computes next_run
                started = await scheduler._scheduler_tick(now=when.timestamp() + 1)
                await asyncio.gather(*list(scheduler._ACTIVE_TASKS))
                return started
            self.assertEqual(run(tick()), 1)
        self.assertEqual(len(delivered), 1)
        self.assertFalse(self.pgdata.load_schedules()[0]["enabled"])     # one-shot


# ---------------------------------------------------------------------- custom commands

class CustomCommandTest(EngineCase):
    def command(self, **extra):
        return {"name": "Рабочий режим", "triggers": ["рабочий режим", "work mode", "рабочий режим"],
                "steps": [{"type": "action", "action": "volume.set", "params": {"level": 20}},
                          {"type": "wait", "seconds": 0.1},
                          {"type": "action", "action": "theme.set", "params": {"mode": "dark"}, "when": {"weekdays": [6, 7]}},
                          {"type": "say", "text": "Готово"}], **extra}

    def test_validation(self):
        clean = ac.validate_command(self.command(hotkey="ctrl + alt + w"))
        self.assertEqual(clean["triggers"], ["рабочий режим", "work mode"])     # de-duplicated
        self.assertEqual(clean["hotkey"], "Ctrl+Alt+W")
        self.assertTrue(clean["id"].startswith("cmd-"))
        bad = [{"name": ""}, {"name": "x", "steps": []}, {"name": "x", "steps": [{"type": "action", "action": "rm.rf"}]},
               {"name": "x", "steps": [{"type": "action", "action": "volume.set", "params": {"level": "loud"}}]},
               {"name": "x", "steps": [{"type": "wait", "seconds": "abc"}]}, {"name": "x", "steps": [{"type": "teleport"}]},
               {"name": "x", "steps": [{"type": "say", "text": ""}]}, {"name": "x", "hotkey": "Ctrl+Nope", "steps": [{"type": "say", "text": "a"}]},
               {"name": "x", "steps": [{"type": "say", "text": "a", "when": {"time_from": "25:99"}}]},
               {"name": "x", "steps": [{"type": "layout", "layout": {"monitors": [], "windows": []}}]}]
        for raw in bad:
            with self.subTest(raw=raw), self.assertRaises(ac.CommandError):
                ac.validate_command(raw)

    def test_tiers_of_routines(self):
        self.assertEqual(ac.command_tier(ac.validate_command(self.command())), "safe")
        risky = ac.validate_command({"name": "x", "steps": [{"type": "action", "action": "app.close", "params": {"name": "chrome"}}]})
        self.assertEqual(ac.command_tier(risky), "confirm")
        risky["trusted"] = True
        self.assertEqual(ac.command_tier(risky), "safe")
        night = ac.validate_command({"name": "Ночь", "trusted": True, "steps": [{"type": "action", "action": "power.sleep"}]})
        self.assertEqual(ac.command_tier(night), "dangerous")

    def test_store_roundtrip_and_run(self):
        clean = ac.validate_command(self.command())
        self.engine.store.update(lambda d: d["commands"].append(clean))
        stored = self.engine.store.load()["commands"]
        self.assertEqual(stored[0]["name"], "Рабочий режим")
        hit = self.engine.match("work mode")
        self.assertEqual(hit["action"], "command.run")
        card = run(self.engine.run(hit["action"], hit["params"]))
        self.assertEqual(card["status"], "done", card)
        statuses = [s["status"] for s in card["steps"]]
        self.assertEqual(statuses, ["done", "done", "skipped", "done"])      # Thursday: weekend step skipped
        self.assertEqual(self.backend.volume["level"], 20)
        self.assertFalse(self.backend.dark)
        self.assertEqual(card["speak"], "Готово")

    def test_dangerous_routine_needs_confirmation(self):
        night = ac.validate_command({"name": "Ночь", "triggers": ["спокойной ночи"], "steps": [
            {"type": "action", "action": "volume.set", "params": {"level": 5}}, {"type": "action", "action": "power.shutdown"}]})
        self.engine.store.update(lambda d: d["commands"].append(night))
        card = run(self.engine.run("command.run", {"name": "спокойной ночи"}))
        self.assertEqual(card["status"], "confirm")
        self.assertIn("Ночь", card["message"])
        self.assertFalse(any(c[0] == "shutdown" for c in self.backend.calls))
        done = run(self.engine.confirm(card["confirm"]["token"], True))
        self.assertEqual(done["status"], "done")
        self.assertIn(("shutdown", 5), self.backend.calls)

    def test_recursion_is_bounded(self):
        loop = ac.validate_command({"name": "loop", "steps": [{"type": "action", "action": "command.run", "params": {"name": "loop"}}]})
        self.engine.store.update(lambda d: d["commands"].append(loop))
        card = run(self.engine.run("command.run", {"name": "loop"}))
        self.assertIn(card["status"], ("done", "error"))

    def test_template_validates(self):
        for lang in ("ru", "en"):
            clean = ac.validate_command(ac.template_good_morning(lang))
            self.assertGreaterEqual(len(clean["triggers"]), 3)

    def test_settings_normalization(self):
        s = ac.normalize_settings({"confirm_countdown": 999, "timer_sound": "air-horn", "disabled_actions": ["volume.set", "bogus"],
                                   "voice": {"stt_provider": "realtime", "tts_provider": "windows", "auto_speak": "true"},
                                   "pomodoro": {"work": 0}})
        self.assertEqual(s["confirm_countdown"], 30)
        self.assertEqual(s["timer_sound"], "chime")
        self.assertEqual(s["disabled_actions"], ["volume.set"])
        self.assertEqual(s["voice"]["stt_provider"], "")       # no realtime provider can sneak in
        self.assertEqual(s["voice"]["tts_provider"], "windows")
        self.assertTrue(s["voice"]["auto_speak"])
        self.assertEqual(s["pomodoro"]["work"], 1)


# ---------------------------------------------------------------------- layout geometry

class LayoutTest(unittest.TestCase):
    MONS = [{"device": "D1", "rect": [0, 0, 1920, 1080], "work": [0, 0, 1920, 1040], "primary": True},
            {"device": "D2", "rect": [-2560, -200, 0, 1240], "work": [-2560, -200, 0, 1200]}]

    def windows(self):
        return [{"title": "Chat", "exe": "telegram.exe", "path": r"C:\T\Telegram.exe", "app": "Telegram", "state": "normal", "rect": [-2500, -150, -1300, 900]},
                {"title": "Code", "exe": "code.exe", "path": r"C:\C\Code.exe", "app": "Code", "state": "maximized", "rect": [0, 0, 1920, 1040],
                 "normal_rect": [100, 100, 1500, 1000]},
                {"title": "Notes", "exe": "notepad.exe", "path": r"C:\W\notepad.exe", "app": "Блокнот", "state": "minimized", "rect": [-32000, -32000, -31840, -31970],
                 "normal_rect": [200, 200, 800, 600]}]

    def test_capture(self):
        layout = geo.capture(self.windows(), self.MONS, name="работа", now=1)
        self.assertEqual([w["monitor"] for w in layout["windows"]], [1, 0, 0])
        self.assertEqual(layout["windows"][1]["rect"], [100, 100, 1500, 1000])      # restore rect of a maximized window
        self.assertEqual(layout["windows"][2]["state"], "minimized")
        rel = layout["windows"][0]["rel"]
        self.assertAlmostEqual(rel["x"], 60 / 2560, places=4)
        self.assertEqual(geo.validate(json.loads(json.dumps(layout)))["windows"][0]["app"]["exe"], "telegram.exe")

    def test_restore_same_monitors_is_exact(self):
        layout = geo.capture(self.windows(), self.MONS)
        running = [{"exe": "code.exe", "title": "Code"}, {"exe": "telegram.exe", "title": "Chat"}]
        steps = geo.plan_restore(layout, self.MONS, running)
        by_exe = {s["row"]["app"]["exe"]: s for s in steps}
        self.assertEqual(by_exe["telegram.exe"]["rect"], [-2500, -150, -1300, 900])
        self.assertEqual(by_exe["telegram.exe"]["window"], 1)
        self.assertEqual(by_exe["code.exe"]["state"], "maximized")
        self.assertTrue(by_exe["notepad.exe"]["launch"])
        # bottom-most first so placing rebuilds the z-order
        self.assertEqual(steps[0]["row"]["app"]["exe"], "notepad.exe")

    def test_restore_on_different_monitors_scales_and_clamps(self):
        layout = geo.capture(self.windows(), self.MONS)
        laptop = [{"device": "D9", "rect": [0, 0, 1366, 768], "work": [0, 0, 1366, 728]}]
        steps = geo.plan_restore(layout, laptop, [])
        for step in steps:
            l, t, r, b = step["rect"]
            self.assertGreaterEqual(t, 0)
            self.assertLessEqual(r - l, 1366)
            self.assertLessEqual(b - t, 728)
            self.assertGreater(r, geo.MIN_VISIBLE - 1)
            self.assertLess(l, 1366 - geo.MIN_VISIBLE + 1)
        # the left 2560-wide monitor maps proportionally
        tg = [s for s in steps if s["row"]["app"]["exe"] == "telegram.exe"][0]
        self.assertAlmostEqual((tg["rect"][2] - tg["rect"][0]) / 1366, 1200 / 2560, delta=0.01)

    def test_monitor_mapping_when_order_changes(self):
        saved = [geo.normalize_monitor(m, i) for i, m in enumerate(self.MONS)]
        swapped = [geo.normalize_monitor(self.MONS[1], 0), geo.normalize_monitor(self.MONS[0], 1)]
        mapping = geo.map_monitors(saved, swapped)
        self.assertEqual(mapping[0], (1, True))
        self.assertEqual(mapping[1], (0, True))

    def test_match_windows_prefers_title(self):
        saved = [{"app": {"exe": "code.exe"}, "title": "project-b — Code"}, {"app": {"exe": "code.exe"}, "title": "project-a — Code"}]
        running = [{"exe": "code.exe", "title": "project-a — Code"}, {"exe": "code.exe", "title": "project-b — Code"}]
        self.assertEqual(geo.match_windows(saved, running), [1, 0])

    def test_frames_tiles_and_snaps(self):
        offsets = geo.frame_offsets((93, 100, 1007, 707), (100, 100, 1000, 700))
        self.assertEqual(offsets, (7, 0, 7, 7))
        self.assertEqual(geo.expand((0, 0, 960, 1040), offsets), (-7, 0, 967, 1047))
        self.assertEqual(geo.snap_rect((0, 0, 1920, 1040), "snap_right"), (960, 0, 1920, 1040))
        tiles = geo.tile(3, (0, 0, 1920, 1040), "side_by_side")
        self.assertEqual(tiles[-1][2], 1920)
        grid = geo.tile(5, (0, 0, 1200, 900), "grid")
        self.assertEqual(len(grid), 5)
        self.assertEqual(grid[-1][2], 1200)          # last row stretched
        self.assertEqual(geo.pick_monitor("next", 1, 2), 0)
        self.assertEqual(geo.pick_monitor("2", 0, 3), 1)
        moved = geo.move_to_monitor((100, 100, 1060, 620), (0, 0, 1920, 1040), (-2560, -200, 0, 1200))
        self.assertEqual(moved[0], -2560 + round(100 / 1920 * 2560))

    def test_validate_rejects_junk(self):
        for junk in (None, {}, {"monitors": [{"rect": [0, 0, 10, 10]}], "windows": [{"rect": [5, 5, 1, 1], "app": {"exe": "a"}}]}):
            with self.subTest(junk=junk), self.assertRaises(ValueError):
                geo.validate(junk)


class LayoutEngineTest(EngineCase):
    def test_capture_and_restore_through_the_engine(self):
        card = run(self.engine.run("layout.capture", {"name": "работа"}))
        self.assertEqual(card["status"], "done", card)
        self.assertIn("работа", self.engine.store.load()["layouts"])
        self.backend.calls.clear()
        card = run(self.engine.run("layout.restore", {"name": "Работа"}))
        self.assertEqual(card["status"], "done", card)
        places = [c for c in self.backend.calls if c[0] == "place"]
        self.assertEqual(len(places), 2)
        self.assertIn(("place", 102, (200, 150, 1400, 900), "maximized"), places)


# ---------------------------------------------------------------------- timers

class TimerManagerTest(unittest.TestCase):
    def setUp(self):
        self.now = [1000.0]
        self.path = Path(tempfile.mkdtemp()) / "t.json"
        self.fired = []
        self.tm = timers_mod.TimerManager(self.path, clock=lambda: self.now[0], on_fire=self.fired.append)

    def test_timer_fires_once_and_persists(self):
        timer = self.tm.add_timer(60, "чай")
        self.assertEqual(self.tm.tick(), [])
        self.now[0] += 61
        events = self.tm.tick()
        self.assertEqual([e["label"] for e in events], ["чай"])
        self.assertEqual(self.tm.tick(), [])
        self.assertEqual(self.tm.snapshot()["ringing"][0]["id"], timer["id"])
        self.tm.dismiss(timer["id"])
        self.assertEqual(self.tm.snapshot()["ringing"], [])
        # persisted alarms come back after a restart and ring late
        self.tm.add_timer(0, "будильник", kind="alarm", ends_at=self.now[0] + 30)
        again = timers_mod.TimerManager(self.path, clock=lambda: self.now[0] + 600)
        events = again.tick()
        self.assertEqual(events[0]["kind"], "alarm")
        self.assertTrue(events[0]["late"])

    def test_pause_resume(self):
        timer = self.tm.add_timer(100, "x")
        self.now[0] += 40
        self.tm.pause("x")
        self.now[0] += 500
        self.assertEqual(self.tm.tick(), [])
        self.assertAlmostEqual(self.tm.remaining(self.tm.find(timer["id"])[0]), 60, delta=0.01)
        self.tm.pause("x", resume=True)
        self.now[0] += 61
        self.assertEqual(len(self.tm.tick()), 1)

    def test_cancel_soonest_when_ambiguous(self):
        self.tm.add_timer(500, "a")
        self.tm.add_timer(100, "b")
        removed = self.tm.cancel("")
        self.assertEqual([t["label"] for t in removed], ["b"])

    def test_pomodoro_cycle_and_stats(self):
        self.tm.pomodoro("start", {"work": 25, "short_break": 5, "long_break": 15, "cycles": 2, "auto_breaks": True, "auto_work": False})
        self.now[0] += 25 * 60 + 1
        event = self.tm.tick()[0]
        self.assertEqual((event["ended"], event["next"]), ("work", "short_break"))
        self.now[0] += 5 * 60 + 1
        event = self.tm.tick()[0]
        self.assertEqual(event["next"], "work")
        self.assertTrue(self.tm.state["pomodoro"]["waiting"])        # auto_work off: waits for the user
        self.now[0] += 3600
        self.assertEqual(self.tm.tick(), [])
        self.tm.pomodoro("resume")
        self.now[0] += 25 * 60 + 1
        event = self.tm.tick()[0]
        self.assertEqual(event["next"], "long_break")               # cycle 2 of 2
        stats = self.tm.pomodoro_stats()
        self.assertEqual(stats["today"]["sessions"], 2)
        self.assertEqual(stats["today"]["minutes"], 50)

    def test_stopwatch(self):
        self.tm.stopwatch("start")
        self.now[0] += 12.5
        self.tm.stopwatch("lap")
        self.now[0] += 2.5
        state = self.tm.stopwatch("stop")
        self.assertAlmostEqual(state["value"], 15.0)
        self.assertEqual(state["laps"], [12.5])
        self.now[0] += 100
        self.assertAlmostEqual(self.tm.stopwatch_elapsed(), 15.0)

    def test_ring_wav(self):
        data = timers_mod.ring_wav("chime", 50)
        self.assertTrue(data.startswith(b"RIFF"))
        self.assertGreater(len(data), 20000)


# ---------------------------------------------------------------------- apps, keys, voice

class AppsTest(unittest.TestCase):
    def test_resolve(self):
        index = apps.merge_index(apps.builtin_entries(), [
            apps._entry("Telegram Desktop", "lnk", "t.lnk"), apps._entry("Google Chrome", "lnk", "c.lnk"),
            apps._entry("Visual Studio Code", "lnk", "v.lnk"), apps._entry("Microsoft Word", "uwp", "shell:AppsFolder\\w"),
            apps._entry("Uninstall Telegram", "lnk", "u.lnk"), apps._entry("Spotify", "uwp", "shell:AppsFolder\\s")])
        expect = {"телеграм": "Telegram Desktop", "телега": "Telegram Desktop", "хром": "Google Chrome",
                  "google chrome": "Google Chrome", "vs code": "Visual Studio Code", "вскод": "Visual Studio Code",
                  "ворд": "Microsoft Word", "спотифай": "Spotify", "блокнот": "Блокнот", "калькулятор": "Калькулятор",
                  "диспетчер задач": "Диспетчер задач"}
        for spoken, name in expect.items():
            with self.subTest(spoken=spoken):
                self.assertEqual(apps.resolve(spoken, index)["match"]["name"], name)
        self.assertIsNone(apps.resolve("квантовый холодильник", index)["match"])
        self.assertFalse(any(e["name"].startswith("Uninstall") for e in index))

    def test_process_match(self):
        self.assertGreater(apps.match_process("хром", "chrome.exe"), 0.9)
        self.assertGreater(apps.match_process("vs code", "Code.exe"), 0.85)
        self.assertLess(apps.match_process("телеграм", "chrome.exe"), 0.8)

    def test_startmenu_scan(self):
        root = Path(tempfile.mkdtemp())
        (root / "Sub").mkdir()
        (root / "Sub" / "Obsidian.lnk").write_text("x")
        (root / "Uninstall Foo.lnk").write_text("x")
        (root / "readme.txt").write_text("x")
        rows = apps.merge_index(apps.startmenu_entries([str(root)]))
        self.assertEqual([r["name"] for r in rows], ["Obsidian"])


class KeysAndVoiceTest(unittest.TestCase):
    def test_parse_keys(self):
        self.assertEqual(parse_keys("Ctrl+Shift+T"), [0x11, 0x10, ord("T")])
        self.assertEqual(parse_keys("win + ctrl + right"), [0x5B, 0x11, 0x27])
        self.assertEqual(parse_keys("контрол+c"), [0x11, ord("C")])
        with self.assertRaises(ActionError):
            parse_keys("Ctrl+Alt+Delete")
        with self.assertRaises(ActionError):
            parse_keys("Hyper+Q")

    def test_voice_prefs_overlay(self):
        store = ac.Store(Path(tempfile.mkdtemp()) / "a.json")
        store.update(lambda d: d["settings"]["voice"].update(stt_provider="local", stt_local_model="large-v3", tts_voice="Irina"))
        prefs = {"voice_stt_provider": "api", "voice_stt_api_url": "https://x", "voice_postprocess_enabled": True,
                 "voice_tts_voice": ""}
        self.assertIs(ac.voice_prefs(prefs, {"text": "x"}, store), prefs)            # not Quick Ask → untouched
        out = ac.voice_prefs(prefs, {"quick": True, "voice_stt_api_url": "https://evil"}, store)
        self.assertEqual(out["voice_stt_provider"], "local")
        self.assertEqual(out["voice_stt_local_model"], "large-v3")
        self.assertEqual(out["voice_tts_voice"], "Irina")
        self.assertEqual(out["voice_stt_api_url"], "https://x")                    # the page cannot inject preferences
        self.assertFalse(out["voice_postprocess_enabled"])


# ---------------------------------------------------------------------- HTTP + contract

class RoutesTest(unittest.TestCase):
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

    def test_run_text_and_confirm(self):
        data = self.client.post("/api/assistant/run", json={"text": "громкость 25"}).json()
        self.assertEqual(data["card"]["status"], "done")
        self.assertEqual(self.backend.volume["level"], 25)
        data = self.client.post("/api/assistant/run", json={"text": "как дела?"}).json()
        self.assertIsNone(data["card"])
        data = self.client.post("/api/assistant/run", json={"text": "включи свет"}).json()   # not an app → back to the model
        self.assertTrue(data["card"]["fallback"])
        card = self.client.post("/api/assistant/run", json={"text": "выключи компьютер"}).json()["card"]
        self.assertEqual(card["status"], "confirm")
        done = self.client.post("/api/assistant/confirm", json={"token": card["confirm"]["token"], "accept": True}).json()["card"]
        self.assertEqual(done["status"], "done")
        undo = self.client.post("/api/assistant/undo", json={"undo": done["undo"]}).json()["card"]
        self.assertEqual(undo["status"], "done")

    def test_commands_crud_import_export(self):
        bad = self.client.post("/api/assistant/commands", json={"op": "save", "command": {"name": ""}})
        self.assertEqual(bad.status_code, 400)
        ok = self.client.post("/api/assistant/commands", json={"op": "save", "command": ac.template_good_morning("ru")}).json()
        self.assertEqual(len(ok["commands"]), 1)
        exported = self.client.post("/api/assistant/commands", json={"op": "export"}).json()
        self.assertEqual(exported["kind"], "quick-commands")
        imported = self.client.post("/api/assistant/commands", json={"op": "import", "commands": exported, "replace": True}).json()
        self.assertEqual(imported["imported"], 1)
        self.assertEqual(imported["errors"], [])
        partial = self.client.post("/api/assistant/commands", json={"op": "import", "commands": [{"name": ""}, {"name": "a", "steps": [{"type": "say", "text": "hi"}]}]}).json()
        self.assertEqual(partial["imported"], 1)
        self.assertEqual(len(partial["errors"]), 1)
        test = self.client.post("/api/assistant/commands", json={"op": "test", "command": {"name": "t", "steps": [{"type": "action", "action": "volume.set", "params": {"level": 7}}]}}).json()
        self.assertEqual(test["steps"][0]["status"], "done")
        danger = self.client.post("/api/assistant/commands", json={"op": "test", "command": {"name": "t", "steps": [{"type": "action", "action": "power.restart"}]}})
        self.assertEqual(danger.status_code, 400)

    def test_settings_config_timers_tools(self):
        data = self.client.post("/api/assistant/settings", json={"settings": {"voice": {"auto_speak": True}, "timer_volume": 30}}).json()
        self.assertTrue(data["settings"]["voice"]["auto_speak"])
        self.assertTrue(data["settings"]["voice"]["send_on_release"])           # merged, not replaced
        config = self.client.get("/api/assistant/config?lang=en").json()
        self.assertEqual(len(config["catalog"]), len(catalog.ACTIONS))
        self.assertEqual(config["settings"]["timer_volume"], 30)
        self.client.post("/api/assistant/timers", json={"op": "add", "seconds": 90, "label": "test"})
        state = self.client.get("/api/assistant/state").json()
        self.assertEqual(state["timers"]["timers"][0]["label"], "test")
        tools = self.client.get("/api/assistant/tools").json()
        self.assertEqual(tools["dispatcher"]["function"]["name"], "assistant_action")
        audit = self.client.get("/api/assistant/audit").json()
        self.assertIsInstance(audit["items"], list)

    def test_quick_ask_contract_registration(self):
        from app import quick_ask_api
        if not hasattr(quick_ask_api, "register_tool"):
            self.skipTest("Quick Ask core without the extension contract")
        self.assertIn("assistant_action", quick_ask_api.EXTRA_TOOLS)
        tool = quick_ask_api.EXTRA_TOOLS["assistant_action"]
        description = tool["description"]({}) if callable(tool["description"]) else tool["description"]   # r30: follows settings
        self.assertIn("volume.set(", description)
        result = run(tool["handler"]({"action": "volume.set", "params": {"level": 33}}, {"lang": "en"}))
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["ui"]["action"], "volume.set")
        self.assertEqual(self.backend.volume["level"], 33)
        confirm = run(tool["handler"]({"action": "power.restart"}, {"lang": "en"}))
        self.assertEqual(confirm["status"], "confirm")
        self.assertIn("do NOT say it is done", confirm["note"])
        events = self.client.get("/api/assistant/events?after=0").json()["events"]
        self.assertTrue(any(e["action"] == "power.restart" for e in events))


if __name__ == "__main__":
    unittest.main()
