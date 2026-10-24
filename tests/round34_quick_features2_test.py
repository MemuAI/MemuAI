"""Round 34 (r34-quick-features-2): the wrong-layout phrase, «покажи / открой …» that are not apps, climate questions,
the canvas run errors, Explorer flags given as strings and over-long names. No network.

Run: python -m pytest -q tests/round34_quick_features2_test.py
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import assistant_intents as intents  # noqa: E402

NOW = datetime(2026, 10, 11, 22, 10)


def m(text):
    found = intents.match(text, now=NOW)
    return (found["action"], found["params"]) if found else None


class LayoutPhraseTest(unittest.TestCase):
    def test_wrong_layout_commands_are_read_and_marked(self):
        for typed, action, read in (("jnrhjq [hjv", "app.open", "открой хром"), ("uhjvrjcnm 50", "volume.set", "громкость 50"),
                                    ("щзут сркщьу", "app.open", "open chrome"), ("dsrk.xb pder", "volume.mute", "выключи звук")):
            found = intents.match(typed, now=NOW)
            self.assertIsNotNone(found, typed)
            self.assertEqual((found["action"], found.get("layout")), (action, read), typed)

    def test_ordinary_text_never_starts_matching_after_a_swap(self):
        words = ("hello how are you|what is the capital of france|fix this bug please|translate to english|why is the sky blue|"
                 "thanks|ok|continue|lol|brb|asdf|qwerty|ping|git rebase vs merge|docker compose up|npm install error|"
                 "привет как дела|спасибо|да|нет|ок|давай|продолжи|ещё|фыва|йцукен|тест|лол|хм|ну и что|сделай таблицу").split("|")
        for text in words:
            found = intents.match(text, now=NOW)
            self.assertFalse(found and found.get("layout"), text)

    def test_conversation_and_matched_text_are_not_swapped(self):
        self.assertIsNone(intents.match("jnrhjq [hjv", now=NOW, conversation=True))
        self.assertNotIn("layout", intents.match("открой хром", now=NOW))

    def test_card_shows_how_the_phrase_was_read(self):
        js = (ROOT / "app" / "pgstatic" / "quick" / "plugins" / "commands.js").read_text(encoding="utf-8")
        self.assertIn("data.card.layout = String(data.match.layout)", js)
        self.assertIn('t("Понял как: «" + esc(card.layout)', js)
        self.assertIn(".mqc-layout", (ROOT / "app" / "pgstatic" / "quick" / "plugins" / "commands.css").read_text(encoding="utf-8"))


class NotAnAppTest(unittest.TestCase):
    def test_show_requests_are_questions(self):
        for text in ("покажи код функции", "show code function", "покажи пример", "покажи как сделать цикл", "show me the code",
                     "show the code", "покажи разницу между list и tuple", "покажи markdown список", "show history",
                     "show example of regex", "перейди к следующему пункту", "focus on the task", "go to the code",
                     "покажи таблицу умножения", "покажи мне хром"):
            found = m(text)
            self.assertFalse(found and found[0] == "app.switch", (text, found))

    def test_show_questions_skip_the_ai_router(self):
        from app import assistant_router
        self.assertFalse(assistant_router.looks_like_command("покажи код функции"))
        self.assertFalse(assistant_router.looks_like_command("покажи разницу между list и tuple"))
        for text in ("покажи хром", "покажи громкость", "покажи все окна", "закрой инси"):
            self.assertTrue(assistant_router.looks_like_command(text), text)

    def test_known_apps_still_switch(self):
        for text, name in (("покажи хром", "хром"), ("покажи телегу", "телегу"), ("show chrome", "chrome"),
                           ("покажи incy", "incy"), ("перейди в телеграм", "телеграм"), ("go to chrome", "chrome"),
                           ("switch to slack", "slack"), ("переключись на incy", "incy"), ("show vs code", "vs code")):
            self.assertEqual(m(text), ("app.switch", {"name": name}), text)

    def test_figures_of_speech_do_not_launch_programs(self):
        for text in ("открой мне глаза на квантовую физику", "запусти обсуждение плюсов и минусов",
                     "run the tests for my project", "open the door to my heart", "запусти игру на телефоне"):
            self.assertIsNone(m(text), text)

    def test_real_app_names_still_open(self):
        for text in ("открой хром", "открой visual studio code", "open microsoft edge", "открой диспетчер задач",
                     "запусти adobe photoshop 2024", "открой мне хром", "launch steam", "открой google chrome canary"):
            found = m(text)
            self.assertEqual(found and found[0], "app.open", text)


class ClimateQuestionTest(unittest.TestCase):
    def test_climate_goes_to_the_model(self):
        for text in ("Какая погода обычно в Лондоне в октябре?", "погода в лондоне в октябре", "погода в мае",
                     "what is the weather usually like in london in october?", "погода в Сочи летом", "средняя погода в риме"):
            self.assertIsNone(m(text), text)

    def test_forecast_still_works_and_the_city_is_clean(self):
        self.assertEqual(m("какая погода будет завтра в москве?"), ("weather.get", {"when": "tomorrow", "city": "москве"}))
        self.assertEqual(m("какая погода в марселе"), ("weather.get", {"when": "now", "city": "марселе"}))
        self.assertEqual(m("weather in london"), ("weather.get", {"when": "now", "city": "london"}))
        self.assertEqual(m("погода на неделю"), ("weather.get", {"when": "week"}))


class CanvasRunErrorTest(unittest.TestCase):
    def test_missing_run_is_explained_in_both_languages(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import quick_canvas
        app = FastAPI()
        app.include_router(quick_canvas.router)
        client = TestClient(app)
        r = client.get("/api/quick/canvas/runs/nope?lang=ru")
        self.assertEqual(r.status_code, 404)
        self.assertIn("Запуск уже завершён", r.json()["error"])
        self.assertIn("already finished", client.get("/api/quick/canvas/runs/nope?lang=en").json()["error"])
        r = client.post("/api/quick/canvas/runs/nope/cancel", json={"lang": "en"})
        self.assertEqual((r.status_code, r.json()["code"]), (404, "run_not_found"))
        self.assertEqual(client.post("/api/quick/canvas/runs/nope/zap", json={"lang": "ru"}).json()["error"], "Неизвестная операция")


class ExplorerFlagsTest(unittest.TestCase):
    def setUp(self):
        from app import quick_explorer as qe
        self.qe = qe
        self.base = Path(tempfile.mkdtemp(prefix="r34qf2-"))
        (self.base / "a.txt").write_text("x x x\n", encoding="utf-8")
        self.ctx = {"id": "r34qf2", "folder": str(self.base), "writable": True}

    def test_replace_all_false_as_a_string(self):
        with self.assertRaises(self.qe.FolderError) as err:       # «x» occurs 3 times — not silently replaced everywhere
            self.qe.plan_changes(self.ctx, [{"op": "edit", "path": "a.txt", "old_text": "x", "new_text": "y",
                                             "replace_all": "false"}])
        self.assertNotIn("Errno", str(err.exception.text("ru")))
        change = self.qe.plan_changes(self.ctx, [{"op": "edit", "path": "a.txt", "old_text": "x", "new_text": "y",
                                                  "replace_all": "true"}])
        self.assertEqual(change["ops"][0].get("after"), "y y y\n")

    def test_flag_values(self):
        from app.quick_apps import flag
        for value, want in ((True, True), ("true", True), ("1", True), (1, True), ("да", True), (False, False),
                            ("false", False), ("0", False), ("", False), (None, False), (0, False), ("no", False)):
            self.assertIs(flag(value), want, value)

    def test_too_long_name_is_a_clear_error(self):
        with self.assertRaises(self.qe.FolderError) as err:
            self.qe.plan_changes(self.ctx, [{"op": "write", "path": "n" * 300 + ".txt", "text": "hi"}])
        self.assertIn("Слишком длинное имя", err.exception.text("ru"))
        self.assertLess(len(err.exception.text("ru")), 220)        # the 300-character name is not echoed whole
        self.assertNotIn("Errno", err.exception.text("ru"))

if __name__ == "__main__":
    unittest.main()
