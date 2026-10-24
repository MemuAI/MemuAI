"""Round 26 (r26-finisher): регрессии исправлений финального прохода.

1. Computer Use: лог/ошибки в карточке и ошибки /api/work/computer/run — на языке интерфейса (были только ru).
2. /api/dfy/status: поле reason («Microsoft Edge не найден», «DFY выключен…») — ru/en.
3. Telegram-бот: длина истории 30/70 (выставленная в Mini App / окне ПК) отмечается ✓ на экране выбора.
4. Панели: засеянная «Погода» и её «Укажите город…» в английском интерфейсе / английском боте — по-английски.
5. Короткое имя модели Yandex (ya-gpt://b1g…/deepseek-v4.1-flash/latest → deepseek-v4.1-flash) в окне быстрого
   доступа и шапке чата; полный id — в title.
6. MemuCode: ``memucode -r <id> -p`` (фоновый ход из Mini App) — модель сессии, а не модель по умолчанию.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import automation_runtime as ar

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "pgstatic"


class Standin(ar.ComputerAutomation):
    def __init__(self, replies):
        super().__init__()
        self.replies = list(replies)

    def start_overlay(self):
        return None

    def _capture(self):
        return {"screens": [{"monitor": 1, "left": 0, "top": 0, "width": 100, "height": 100, "real_width": 100,
                             "real_height": 100, "scale": 1.0, "signature": [5] * 8, "screenshot": "data:image/jpeg;base64,AAAA"}]}

    def _list_windows(self):
        return []


def run_agent(lang, replies, **kw):
    stand = Standin(replies)

    async def completion(model, messages):
        return stand.replies.pop(0)

    async def perform(action, screen):
        return "ok"
    stand.perform = perform
    asyncio.run(ar.run_computer_agent(stand, completion, model="m", task="t", ask_each_action=False, lang=lang, **kw))
    return stand


class ComputerUseLanguage(unittest.TestCase):
    def test_english_log(self) -> None:
        stand = run_agent("en", ['{"action": "done"}'])
        text = " ".join(row["message"] for row in stand.logs)
        self.assertNotRegex(text, "[а-яё]")
        self.assertIn("Computer agent started", text)
        self.assertEqual(stand.report, "Task completed")

    def test_russian_stays_default(self) -> None:
        stand = run_agent("ru", ['{"action": "done"}'])
        self.assertIn("Компьютерный агент запущен", stand.logs[0]["message"])
        self.assertEqual(stand.report, "Задача завершена")

    def test_english_errors(self) -> None:
        stand = run_agent("en", ['{"action": "click", "x": 1, "y": 1}'] * 10, max_steps=10)
        self.assertEqual(stand.status, "error")
        self.assertIn("The agent is stuck", stand.error)
        stand = run_agent("en", ["nope"] * 3)
        self.assertIn("no valid JSON action", stand.error)
        self.assertIn("not JSON", " ".join(row["message"] for row in stand.logs))

    def test_stop_in_english(self) -> None:
        stand = Standin([])
        stand.lang = "en"
        asyncio.run(stand.stop())
        self.assertEqual(stand.error, "Stopped by the user")


class ComputerRunErrors(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="r26fin-")
        self.env = mock.patch.dict(os.environ, {"MEMUAI_DATA_DIR": self.tmp})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_errors_follow_the_client_language(self) -> None:
        from starlette.testclient import TestClient
        from app import quick_ask_api
        from app.playground_api import create_app
        with mock.patch.dict(quick_ask_api.EXTRA_TOOLS):
            client = TestClient(create_app(), base_url="http://127.0.0.1")
        out = client.post("/api/work/computer/run", json={"source": "quick", "task": "x", "lang": "en"})
        self.assertEqual(out.status_code, 403)
        self.assertEqual(out.json()["error"], "Computer use needs an explicit confirmation")
        out = client.post("/api/work/computer/run", json={"source": "quick", "confirmed": True, "task": "x", "lang": "en"})
        self.assertEqual(out.json()["error"], "Choose a vision model for computer use")
        out = client.post("/api/work/computer/run", json={"source": "quick", "confirmed": True, "model": "m", "lang": "ru"})
        self.assertEqual(out.json()["error"], "Не указана задача управления компьютером")

    def test_clients_send_the_language(self) -> None:
        commands = (STATIC / "quick" / "plugins" / "commands.js").read_text(encoding="utf-8")
        self.assertIn('access_mode: "ask", ai_cursor: true, lang: lang() }', commands)
        app_js = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn('ai_cursor: pref("computer_use_ai_cursor", true), lang: PG_LANG }', app_js)


class DfyReason(unittest.TestCase):
    def manager(self, prefs, root):
        from app.dfy import DFYManager
        from app.dfy_vault import SecretVault
        return DFYManager(lambda *a, **k: None, lambda: dict(prefs), data_root=root, vision_lookup=lambda _m: True,
                          vault=SecretVault(root, use_dpapi=False))

    def test_reason_in_the_ui_language(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            st = self.manager({"dfy_enabled": False, "language": "en"}, root).status()
            self.assertEqual(st["reason"], "DFY is turned off in Settings")
            st = self.manager({"dfy_enabled": False}, root).status()
            self.assertEqual(st["reason"], "DFY выключен в настройках")
            missing = FileNotFoundError("Microsoft Edge не найден. Установите Edge/WebView2 и повторите попытку")
            with mock.patch.object(ar, "_edge_executable", side_effect=missing):
                st = self.manager({"dfy_enabled": True, "dfy_model": "vision/m", "language": "en"}, root).status()
                self.assertFalse(st["ready"])
                self.assertEqual(st["reason"], "Microsoft Edge was not found. Install Edge/WebView2 and try again")
                st = self.manager({"dfy_enabled": True, "dfy_model": "vision/m"}, root).status()
                self.assertIn("Microsoft Edge не найден", st["reason"])


class TelegramHistoryChoice(unittest.TestCase):
    def test_current_value_is_offered(self) -> None:
        from app import telegram_bot as tb
        prefs = {"telegram_history_messages": 70, "telegram_language": "en"}
        self.assertEqual([v for v, _ in tb._setting_options(prefs, "history")], ["20", "40", "60", "70", "100"])
        self.assertEqual(tb._setting_label(prefs, "history"), "70 messages")
        self.assertEqual([v for v, _ in tb._setting_options({"telegram_history_messages": 60}, "history")], ["20", "40", "60", "100"])
        screens = []

        async def screen(client, token, chat_id, text, markup, edit_id=0):
            screens.append(markup)
        with mock.patch.object(tb, "_screen", screen):
            asyncio.run(tb._show_setting(None, "t", "1", {"telegram_history_messages": 30}, "history"))
        marked = [row[0]["text"] for row in screens[0]["inline_keyboard"] if row[0]["text"].startswith("✓")]
        self.assertEqual(marked, ["✓ 30 сообщений"])


class SeededPanelLanguage(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="r26fin-panels-")
        self.env = mock.patch.dict(os.environ, {"MEMUAI_DATA_DIR": self.tmp})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_seed_follows_the_ui_language(self) -> None:
        from app import panels
        with mock.patch("app.pgdata.load_preferences", return_value={"language": "en"}):
            panels._seed(doc := {"seeded": False, "panels": [], "settings": panels.default_settings()})
        weather = doc["panels"][0]
        self.assertEqual(weather["title"], "Weather")
        self.assertEqual(weather["wall"]["message"], "Set a city or allow location lookup")

    def test_language_switch_rewrites_the_wall(self) -> None:
        from app import panels
        from app import telegram_panels
        with mock.patch("app.pgdata.load_preferences", return_value={}):
            doc = panels.load()
        self.assertEqual(doc["panels"][0]["title"], "Погода")
        # the bot in English while the PC window is Russian
        view = telegram_panels.card(doc["panels"][0], "en")
        self.assertEqual((view["title"], view["error"]), ("Weather", "Set a city or allow location lookup"))
        self.assertIn("Weather", telegram_panels.render_html(doc["panels"][0], "en"))
        self.assertEqual(telegram_panels.listing("en")["panels"][0]["title"], "Weather")
        panels.update_settings({"lang": "en"})
        panel = panels.load()["panels"][0]
        self.assertEqual((panel["title"], panel["wall"]["message"]), ("Weather", "Set a city or allow location lookup"))
        with self.assertRaises(panels.Wall) as caught:
            asyncio.run(panels.resolve_location(panels.load()["settings"], {}))
        self.assertEqual(caught.exception.message, "Set a city or allow location lookup")


class ShortModelName(unittest.TestCase):
    CASES = {"ya-gpt://b1g4plmmckqmvs52tqqf/deepseek-v4.1-flash/latest": "deepseek-v4.1-flash",
             "gpt://b1g/speech-realtime-250923/latest": "speech-realtime-250923", "gpt://b1g/yandexgpt": "yandexgpt",
             "qwen/qwen3.8-max:free": "qwen/qwen3.8-max:free", "gemini-2.5-flash": "gemini-2.5-flash"}

    def test_both_windows(self) -> None:
        if not shutil.which("node"):
            self.skipTest("no node")
        app_js = (STATIC / "app.js").read_text(encoding="utf-8")
        quick = (STATIC / "quick" / "quick.js").read_text(encoding="utf-8")
        self.assertIn('$("#model-current").title = cur || "";', app_js)
        self.assertIn("const model = shortModelId(info.model);", app_js, "Live badge uses the same helper")
        self.assertIn('$("qa-model-name").title = S.model ? shown : "";', quick)
        for source in (re.search(r"function shortModelId\(id\) \{ return (.+?); \}", app_js).group(1),
                       re.search(r"const shortModelId = \(id\) => (.+?);\n", quick).group(1)):
            script = f"const f = (id) => {source}; console.log(JSON.stringify({json.dumps(list(self.CASES))}.map(f)))"
            out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
            self.assertEqual(json.loads(out.stdout), list(self.CASES.values()), out.stderr)


class ResumedSessionModel(unittest.TestCase):
    def test_resume_keeps_the_session_model(self) -> None:
        from app.cli.runtime import Runtime
        from app.cli.session import Session
        from app.cli.settings import Settings
        root = Path(tempfile.mkdtemp(prefix="r26fin-code-"))
        try:
            session = Session(root)
            session.meta["model"] = "session-model"
            with mock.patch.object(Runtime, "memuai_prefs", return_value={"default_model": "default-model"}):
                self.assertEqual(Runtime(root, Settings(root, {"model": ""}), session=session).model, "session-model")
                self.assertEqual(Runtime(root, Settings(root, {"model": ""}), session=session, model="asked").model, "asked")
                self.assertEqual(Runtime(root, Settings(root, {"model": ""})).model, "default-model")
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
