"""Round 26 (panels-2): stale settings paths, the localized default profile, one preferences/chats poller,
To-Do repeats (month/year anchor, «every N» kept on edit, English errors), at most a few model refreshes at
once after sleep, the «AI limits» setup state after a refresh, English errors of the voice «▶ Test»."""
from __future__ import annotations

import asyncio
import re
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from app import panels, panels_local, pgdata

ROOT = Path(__file__).resolve().parents[1] / "app"

def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")

class StaleSettingsPathsTest(unittest.TestCase):
    """The settings pages are «Голос и Live» / «Voice & Live», «Telegram», «Системные промпты» (app.js P(...))."""

    FILES = ("pgstatic/quick/plugins/commands-settings.js", "pgstatic/quick/plugins/commands.js",
             "pgstatic/quick-settings.js", "pgstatic/telegram-settings.js", "telegram_bot.py",
             "assistant_commands.py", "assistant_r24.py", "telegram_media.py", "product_cards.py")

    def test_page_names_exist(self):
        app = read("pgstatic/app.js")
        self.assertIn('"Голос и Live", "Voice & Live"', app)
        self.assertIn('"Telegram", "Telegram"', app)
        self.assertIn('"Системные промпты", "System prompts"', app)

    def test_no_stale_names_in_user_strings(self):
        stale = re.compile(r"Настройки → Голос(?! и Live)|Settings → Voice(?! & Live)|«Голос»|Voice settings"
                           r"|Удалённый доступ|Settings → Remote|Настройки → Промпты|as in Voice\"|Open Voice\"")
        for rel in self.FILES + ("pgstatic/app.js",):
            for number, line in enumerate(read(rel).splitlines(), 1):
                code = line.split("//")[0] if rel.endswith(".js") else line
                if code.lstrip().startswith(("#", "*", "/*")) or rel.endswith(".py") and code.strip().startswith('"""'):
                    continue
                if rel == "pgstatic/app.js" and "Голос" not in code and "Промпты" not in code and "Voice" not in code:
                    continue
                with self.subTest(file=rel, line=number):
                    self.assertIsNone(stale.search(code), code.strip()[:160])

class DefaultProfileNameTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(pgdata, "MEMU_DIR", Path(self.tmp.name))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_placeholder_follows_ui_language(self):
        pgdata.save_preferences({"language": "en"})
        self.assertEqual(pgdata.load_profiles()["items"][0]["name"], "Default")
        pgdata.save_preferences({"language": "ru"})
        self.assertEqual(pgdata.load_profiles()["items"][0]["name"], "По умолчанию")

    def test_own_name_is_kept(self):
        pgdata.save_preferences({"language": "en"})
        state = pgdata.load_profiles()
        state["items"][0]["name"] = "Аня"
        pgdata.save_profiles(state)
        self.assertEqual(pgdata.load_profiles()["items"][0]["name"], "Аня")

    def test_frontend_localizes_placeholder(self):
        body = re.search(r"function getProfiles\(\) \{(.*?)\n\}", read("pgstatic/app.js"), re.S).group(1)
        self.assertIn('PG_LANG === "en" ? "Default" : "По умолчанию"', body)

class SinglePollerTest(unittest.TestCase):
    """5 window «focus» events in a row used to fire 5 identical /preferences/sync?rev=… requests."""

    def setUp(self):
        self.app = read("pgstatic/app.js")

    def body(self, name):
        return re.search(r"async function " + name + r"\(\) \{(.*?)\n\}", self.app, re.S).group(1)

    def test_preferences_sync_is_gated(self):
        body = self.body("syncRemotePreferences")
        self.assertRegex(body, r"if \(prefsSyncBusy \|\| document\.hidden \|\| Date\.now\(\) - prefsSyncAt < 1000\) return;")
        self.assertIn("prefsSyncAt = Date.now();", body)
        self.assertIn("AbortSignal.timeout(", body)
        self.assertIn("prefsSyncBusy = false", body)

    def test_chats_sync_is_gated_and_released(self):
        body = self.body("syncRemoteChats")
        self.assertIn("chatsSyncBusy || Date.now() - chatsSyncAt < 1000", body)
        self.assertIn("finally {\n    chatsSyncBusy = false;", body)

    def test_one_timer_each(self):
        self.assertEqual(self.app.count("setInterval(syncRemotePreferences"), 1)
        self.assertEqual(self.app.count("setInterval(syncRemoteChats"), 1)
        focus = re.search(r'window\.addEventListener\("focus", \(\) => \{(.*?)\n  \}\);', self.app, re.S).group(1)
        self.assertIn("Date.now() - prefsSyncAt >= 1000", focus)

class TodoRepeatTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(pgdata, "MEMU_DIR", Path(self.tmp.name))
        self.patch.start()
        self.saved = dict(panels_local.REMINDERS)
        panels_local.REMINDERS["schedule"] = lambda item, lang: {}
        panels_local.REMINDERS["cancel"] = lambda ids: None
        self.lid = panels_local.ensure_default_list("Задачи")["id"]

    def tearDown(self):
        panels_local.REMINDERS.update(self.saved)
        self.patch.stop()
        self.tmp.cleanup()

    def done_on(self, iid, day, changes=None):
        class Today(date):
            @classmethod
            def today(cls):
                return day
        with patch.object(panels_local, "date", Today):
            return panels_local.update_item(self.lid, iid, changes or {"done": True})

    def test_next_due_counts_from_the_original_day(self):
        nd = panels_local.next_due
        self.assertEqual(nd("2026-01-31", {"every": "month", "n": 1}, today=date(2026, 3, 1)), "2026-03-31")
        self.assertEqual(nd("2026-02-28", {"every": "month", "n": 1, "day": 31}, today=date(2026, 2, 28)), "2026-03-31")
        self.assertEqual(nd("2025-02-28", {"every": "year", "n": 1, "day": 29}, today=date(2027, 3, 1)), "2028-02-29")
        self.assertEqual(nd("2026-10-09T18:00", {"every": "day", "n": 2}, today=date(2026, 10, 9)), "2026-10-11T18:00")

    def test_month_end_does_not_drift(self):
        item = panels_local.add_item(self.lid, {"title": "Аренда", "due": "2026-01-31", "repeat": {"every": "month"}})
        self.assertEqual(self.done_on(item["id"], date(2026, 1, 31))["due"], "2026-02-28")

        self.done_on(item["id"], date(2026, 2, 1), {"title": "Аренда!", "due": "2026-02-28", "repeat": {"every": "month"}})
        self.assertEqual(self.done_on(item["id"], date(2026, 2, 28))["due"], "2026-03-31")

    def test_new_date_is_the_new_anchor(self):
        item = panels_local.add_item(self.lid, {"title": "Счёт", "due": "2026-01-31", "repeat": {"every": "month"}})
        self.done_on(item["id"], date(2026, 1, 31))
        moved = self.done_on(item["id"], date(2026, 2, 1), {"due": "2026-02-10", "repeat": {"every": "month"}})
        self.assertNotIn("day", moved["repeat"])
        self.assertEqual(self.done_on(item["id"], date(2026, 2, 10))["due"], "2026-03-10")

    def test_edit_keeps_every_n(self):
        item = panels_local.add_item(self.lid, {"title": "Полив", "due": "2026-10-09", "repeat": {"every": "week", "n": 2}})
        edited = self.done_on(item["id"], date(2026, 10, 9), {"title": "Полив цветов", "repeat": {"every": "week"}})
        self.assertEqual(edited["repeat"]["n"], 2)
        self.assertEqual(self.done_on(item["id"], date(2026, 10, 9))["due"], "2026-10-23")

    def test_errors_have_english(self):
        with self.assertRaises(panels_local.LocalError) as caught:
            panels_local.update_item("nope", "x", {"done": True})
        self.assertEqual((str(caught.exception), caught.exception.en), ("Список не найден", "List not found"))
        with self.assertRaises(panels_local.LocalError) as caught:
            panels_local.update_item(self.lid, "x", {"done": True})
        self.assertEqual(caught.exception.en, "Task not found")

class PanelsSchedulerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(pgdata, "MEMU_DIR", Path(self.tmp.name))
        self.patch.start()
        panels._TASKS.clear()

    def tearDown(self):
        panels._TASKS.clear()
        self.patch.stop()
        self.tmp.cleanup()

    def test_wake_from_sleep_starts_few_model_refreshes(self):
        ids = [panels.create_panel(subject=f"Тема {i}", start=False)["id"] for i in range(6)]
        system = panels.create_panel(template="system", start=False)["id"]

        def overdue(doc):
            for p in doc["panels"]:
                p["status"], p["next_refresh"], p["data"] = "live", time.time() - 8 * 3600, {"x": 1}
        panels.mutate(overdue)
        started = []
        with patch.object(panels, "start_refresh", side_effect=lambda pid, **kw: started.append(pid)), \
                patch.object(panels, "_telegram_check"):
            asyncio.run(panels.tick())
        self.assertIn(system, started)
        self.assertEqual(len([pid for pid in started if pid in ids]), panels.MAX_MODEL_REFRESHES)

    def test_usage_without_accounts_stays_setup_after_refresh(self):
        pid = panels.create_panel(template="usage", start=False)["id"]
        self.assertEqual(panels.load()["panels"][0]["status"], "setup")
        out = asyncio.run(panels.refresh_panel(pid, force=True))
        self.assertEqual(out["status"], "setup")
        self.assertEqual(out["wall"]["status"], "needs_connector:usage")

    def test_usage_setup_subtitle_is_not_the_title(self):
        js = read("pgstatic/panels.js")
        self.assertIn('head(t("Нужен аккаунт", "Account needed"))', js)

class VoiceTestErrorsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(pgdata, "MEMU_DIR", Path(self.tmp.name))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_windows_voice_error_in_english(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import playground_api, voice_runtime
        route = next(r for r in playground_api.create_app().routes if getattr(r, "path", "") == "/api/voice/speak")
        app = FastAPI()
        app.router.routes.append(route)
        http = TestClient(app)
        pgdata.save_preferences({"language": "en", "voice_tts_provider": "windows"})
        with patch.object(voice_runtime.VoiceRuntime, "windows_voices", return_value=[]), \
                patch.object(voice_runtime.VoiceRuntime, "speak_windows",
                             side_effect=RuntimeError("Локальное озвучивание доступно только в Windows")):
            out = http.post("/api/voice/speak", json={"text": "Hello there"}).json()
        self.assertEqual(out["error"], "Windows voices exist only in the Windows app")
        pgdata.save_preferences({"language": "ru"})
        with patch.object(voice_runtime.VoiceRuntime, "windows_voices", return_value=[]), \
                patch.object(voice_runtime.VoiceRuntime, "speak_windows",
                             side_effect=RuntimeError("Windows не удалось озвучить сообщение")):
            out = http.post("/api/voice/speak", json={"text": "Привет"}).json()
        self.assertEqual(out["error"], "Windows не удалось озвучить сообщение")

class ApiModelsPluralTest(unittest.TestCase):
    def test_one_source(self):
        self.assertIn('selected.length === 1 ? "source" : "sources"', read("module_builtins/api-models/main.js"))

if __name__ == "__main__":
    unittest.main()
