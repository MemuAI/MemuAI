"""Round 26 (r26-quick-commands): every Quick Ask assistant action runs; summary of the open page; clipboard
reads by the model need a click; reasoning is cut from AI answers; intents (units, translate, summary) and
currency names. No network: fetches and models are mocked."""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-r26qc-")

from app import assistant_catalog as catalog  # noqa: E402
from app import assistant_commands as ac  # noqa: E402
from app import assistant_intents as intents  # noqa: E402
from app import instant_answers  # noqa: E402
from app import youtube_analysis as yt  # noqa: E402
from app.assistant_actions_win import NullBackend  # noqa: E402
from tests.assistant_commands_test import NOW, EngineCase, setUpModule, tearDownModule  # noqa: E402,F401

def run(coro):
    return asyncio.run(coro)

def m(text, conversation=False):
    return intents.match(text, now=NOW, conversation=conversation)

SAMPLES = {"text": "hello world", "name": "Telegram", "url": "https://example.com", "query": "test", "path": "/tmp",
           "color": "#ff0000", "time": "10:00", "to": "eur", "from": "usd", "keys": "ctrl+c", "command": "echo 1",
           "goal": "find a table", "task": "open notepad", "prompt": "a cat", "city": "Tokyo", "date": "31.12.2026",
           "items": "a, b", "id": "x", "label": "x"}

def sample(key, spec):
    if spec.get("enum"):
        return next((v for v in spec["enum"] if v), "")
    if spec.get("type") in ("number", "integer"):
        return max(1, spec.get("minimum", 1))
    if spec.get("type") == "boolean":
        return True
    return SAMPLES.get(key, "x")

class Page:
    def __init__(self, page=None, text=""):
        self.page = page
        self._text = text
        self.hwnd = 0

    def page_text(self, page_id="", limit=60000):
        return self._text

class CatalogSmokeTest(EngineCase):
    """Each handler at least once (full and minimal params): no AttributeError/NameError/TypeError anywhere."""

    def setUp(self):
        super().setUp()
        self.settings(advanced_shell=True, rate_per_minute=100000)
        self.engine.rate.allow = lambda *a, **k: True

    def test_every_action_has_a_handler_and_runs(self):
        async def ai(prompt, lang, system=""):
            return "<think>plan</think>AI answer"

        self.engine._ask_ai = ai
        net = mock.patch.multiple(instant_answers, weather=mock.AsyncMock(return_value={"text": "+15"}),
                                  currency=mock.AsyncMock(return_value={"text": "1 USD = 0.9 EUR"}),
                                  crypto=mock.AsyncMock(return_value={"text": "BTC"}))
        fetch = mock.patch("app.websearch.fetch_page", mock.AsyncMock(return_value={"text": "page text " * 50, "title": "T"}))
        with net, fetch, mock.patch.object(yt, "analyze", mock.AsyncMock(side_effect=yt.TranscriptUnavailable("нет", "no_captions", "none"))):
            for action, row in catalog.BY_ID.items():
                self.assertIn(action, self.engine.handlers, action)
                for full in (True, False):
                    params = {k: sample(k, s) for k, s in row["params"].items() if full or k in row["required"]}
                    card = run(self.engine.run(action, params, confirmed=True))
                    self.assertNotEqual(card.get("code"), "failed", (action, params, card.get("message")))
                    self.assertNotEqual(card.get("code"), "unknown", (action, params))
                    self.assertIn(card["status"], ("done", "error", "unsupported", "running", "partial", "disabled"), action)

class SummarizePageTest(EngineCase):
    def setUp(self):
        super().setUp()
        self.prompts = []

        async def ai(prompt, lang, system=""):
            self.prompts.append(prompt)
            return "summary"

        self.engine._ask_ai = ai
        self.backend.clip = "Бенчмарк аквариум: старый текст из буфера"

    def page(self, url="https://news.example/a", text=""):
        manager = Page({"id": "p1", "hwnd": 5, "url": url, "title": "Новость"}, text)
        self.engine.quick_manager = lambda: manager
        return manager

    def test_phrases(self):
        for text in ("саммари страницы", "сделай саммари этой страницы", "кратко перескажи эту страницу",
                     "summarize this page", "дай саммари по статье", "кратко перескажи этот сайт"):

            self.assertIsNone(m(text), text)
        self.assertEqual(m("кратко перескажи")["params"], {"text": ""})
        self.assertEqual(m("кратко перескажи буфер обмена")["params"], {"text": ""})
        self.assertEqual(m("summarize https://example.com/a")["params"], {"text": "https://example.com/a"})

        self.assertIsNone(m("кратко перескажи что такое квантовая физика"))
        self.assertIsNone(m("summarize the history of Rome"))

        self.assertIsNone(m("кратко перескажи", conversation=True))
        self.assertIsNone(m("переведи на английский", conversation=True))

    def test_page_is_read_not_the_clipboard(self):
        self.page()
        with mock.patch("app.websearch.fetch_page", mock.AsyncMock(return_value={"text": "Статья про выборы. " * 30})) as fetch:
            card = run(self.engine.run("text.summarize", {"page": True}))
        self.assertEqual(card["status"], "done", card)
        fetch.assert_awaited_once()
        self.assertIn("выборы", self.prompts[0])
        self.assertNotIn("аквариум", self.prompts[0])
        self.assertEqual(card["links"][0]["url"], "https://news.example/a")

    def test_model_empty_text_means_the_open_tab(self):
        self.page()
        with mock.patch("app.websearch.fetch_page", mock.AsyncMock(return_value={"text": "Статья про выборы. " * 30})):
            card = run(self.engine.run("text.summarize", {}, source="model"))
        self.assertEqual(card["status"], "done", card)
        self.assertNotIn("аквариум", self.prompts[0])

    def test_the_tab_on_screen_wins_over_the_remembered_one(self):
        manager = self.page(url="https://old.example/aquarium")
        manager.current_page = lambda: {"id": "p2", "hwnd": 6, "url": "https://news.example/new", "title": "New"}
        with mock.patch("app.websearch.fetch_page", mock.AsyncMock(return_value={"text": "Свежая статья. " * 30})) as fetch:
            card = run(self.engine.run("text.summarize", {}, source="model"))
        self.assertEqual(fetch.await_args.args[0], "https://news.example/new")
        self.assertEqual(card["status"], "done")
        manager.current_page = lambda: None
        self.assertEqual(run(self.engine.run("text.summarize", {}, source="model"))["status"], "confirm")

    def test_unfetchable_page_falls_back_to_screen_text(self):
        self.page(text="Текст с экрана вкладки. " * 20)
        with mock.patch("app.websearch.fetch_page", mock.AsyncMock(side_effect=ValueError("HTTP 403"))):
            card = run(self.engine.run("text.summarize", {"page": True}))
        self.assertEqual(card["status"], "done", card)
        self.assertIn("с экрана", self.prompts[0])

    def test_no_page(self):
        self.engine.quick_manager = lambda: None
        card = run(self.engine.run("text.summarize", {"page": True}))
        self.assertEqual(card["code"], "no_page")

    def test_broken_link_is_an_error_not_a_summary_of_the_url(self):
        with mock.patch("app.websearch.fetch_page", mock.AsyncMock(side_effect=ValueError("HTTP 404"))):
            card = run(self.engine.run("text.summarize", {"text": "https://example.com/x"}))
        self.assertEqual(card["status"], "error")
        self.assertEqual(self.prompts, [])

    def test_eleven_char_clipboard_is_not_a_video(self):
        self.backend.clip = "password123"
        with mock.patch.object(yt, "analyze", mock.AsyncMock()) as analyze:
            card = run(self.engine.run("text.summarize", {}))
        analyze.assert_not_awaited()
        self.assertEqual(card["status"], "done")

class ModelClipboardTest(EngineCase):
    def test_model_reading_the_clipboard_needs_a_click(self):
        self.engine.quick_manager = lambda: None
        for action, params in (("text.translate", {"to": "en"}), ("text.summarize", {}), ("hash.text", {}),
                               ("text.encode", {"codec": "base64"}), ("text.case", {"case": "upper"}),
                               ("text.stats", {}), ("json.format", {})):
            card = run(self.engine.run(action, params, source="model"))
            self.assertEqual(card["status"], "confirm", action)
            self.assertIn("буфер", card["message"])

        self.assertEqual(run(self.engine.run("hash.text", {"text": "abc"}, source="model"))["status"], "done")
        self.assertEqual(run(self.engine.run("hash.text", {}, source="local"))["status"], "done")

class AiTextTest(unittest.TestCase):
    def test_reasoning_is_removed(self):
        self.assertEqual(ac.clean_ai_text("<think>\nhmm\n</think>\n\nПривет"), "Привет")
        self.assertEqual(ac.clean_ai_text("Hello<ds_safety>safe</ds_safety>"), "Hello")
        self.assertEqual(ac.clean_ai_text("reasoning without an opening tag</think>Answer"), "Answer")
        self.assertEqual(ac.clean_ai_text("◁think▷x◁/think▷ok"), "ok")
        self.assertEqual(ac.clean_ai_text(None), "")

    def test_ask_ai_uses_the_tool_prompt_and_cleans(self):
        tmp = tempfile.mkdtemp()
        eng = ac.Engine(backend=NullBackend(), store=ac.Store(Path(tmp) / "a.json"),
                        audit=ac.Audit(Path(tmp) / "audit.jsonl"), timers=object())
        seen = {}

        async def completion(model, messages):
            seen["system"] = messages[0]["content"]
            return "<think>plan</think>Bonjour"

        eng.completion = completion
        with mock.patch("app.pgdata.load_preferences", return_value={"quick_ask_model": "m"}), \
                mock.patch("app.quick_ask_endpoint.active", return_value=False):
            self.assertEqual(run(eng._ask_ai("x", "ru", system="TOOL")), "Bonjour")
        self.assertTrue(seen["system"].startswith("TOOL"))

class IntentFixesTest(unittest.TestCase):
    def test_units_by_full_name(self):
        cases = {"500 миллиграмм в граммы": ("mg", "g"), "2 гигабайта в мегабайты": ("gb", "mb"), "3 дня в часы": ("day", "h"),
                 "10 kilometers to miles": ("km", "mi"), "70 kilograms to pounds": ("kg", "lb"),
                 "100 градусов в фаренгейты": ("c", "f"), "3 days to hours": ("day", "h")}
        for text, (a, b) in cases.items():
            hit = m(text)
            self.assertEqual((hit["action"], hit["params"]["from"], hit["params"]["to"]), ("unit.convert", a, b), text)

    def test_translate(self):
        self.assertEqual(m("переведи на английский привет мир")["params"], {"to": "английский", "text": "привет мир"})
        self.assertEqual(m("переведи привет на английский")["params"], {"to": "английский", "text": "привет"})
        self.assertEqual(m("translate hello world to french")["params"], {"to": "french", "text": "hello world"})
        self.assertEqual(m("переведи текст на японский")["params"], {"to": "японский", "text": ""})
        self.assertEqual(m("переведи на английский язык")["params"], {"to": "английский", "text": ""})
        self.assertEqual(m("переведи на английский: как дела?")["params"], {"to": "английский", "text": "как дела?"})
        for money in ("переведи на карту", "переведи 500 рублей на карту", "переведи деньги на сбер", "translate to entities"):
            self.assertIsNone(m(money), money)

    def test_volume_and_misc(self):
        self.assertEqual(m("turn up the volume")["params"], {"delta": 10})
        self.assertEqual(m("turn down the volume")["params"], {"delta": -10})
        self.assertEqual(m("громкость на максимум")["params"], {"level": 100})
        self.assertEqual(m("звук на минимум")["params"], {"level": 0})
        self.assertEqual(m("свернуть все окна")["action"], "window.minimize_all")
        self.assertEqual(m("построй маршрут до кремля")["action"], "maps.route")
        self.assertEqual(m("что ты можешь")["action"], "assistant.help")
        self.assertEqual(m("будильник на 7")["params"], {"time": "07:00", "date": "2026-10-09"})
        self.assertEqual(m("поставь будильник на 23")["params"], {"time": "23:00", "date": "2026-10-08"})
        self.assertIsNone(m("будильник на 30"))

class ComputerUseRoutingTest(EngineCase):
    def test_handover_has_no_work_project(self):
        prefs = {"computer_model": "qwen-vl", "computer_max_steps": 9}
        with mock.patch("app.pgdata.load_preferences", return_value=prefs), \
                mock.patch("app.pgdata.load_workspaces", return_value={"active_id": "gone", "items": []}):
            card = run(self.engine.run("computer.use", {"task": "открой блокнот"}, confirmed=True))
        self.assertEqual(card["status"], "running", card)
        self.assertEqual(card["client"], {"type": "computer", "task": "открой блокнот", "model": "qwen-vl",
                                          "model_source": "work", "max_steps": 9})
        self.assertEqual(m("управляй компьютером открой блокнот")["action"], "computer.use")
        self.assertEqual(m("сделай за меня найди билеты в казань")["params"], {"goal": "найди билеты в казань"})

class HelpExamplesTest(unittest.TestCase):
    def test_every_help_example_is_recognized(self):
        for lang in ("ru", "en"):
            for group in ac.help_groups(lang):
                for text in group.get("examples") or []:
                    self.assertIsNotNone(m(text), (lang, text))
        self.assertEqual(m("окно влево")["params"], {"action": "snap_left"})
        self.assertEqual(m("open sound settings")["params"], {"page": "sound"})
        self.assertEqual(m("my ip")["params"], {"what": "network"})
        self.assertEqual(m("сколько оперативки")["params"], {"what": "ram"})

class CurrencyTest(EngineCase):
    def test_names_and_unknown(self):
        self.assertEqual(instant_answers.currency_code("доллары"), "USD")
        self.assertEqual(instant_answers.currency_code("юани"), "CNY")
        self.assertEqual(instant_answers.currency_code("kzt"), "KZT")
        self.assertEqual(instant_answers.currency_code("km"), "")
        with mock.patch.object(instant_answers, "currency", mock.AsyncMock(return_value={"text": "ok"})) as cur:
            self.assertEqual(run(self.engine.run("currency.convert", {"from": "доллары", "to": "евро", "amount": 5}))["status"], "done")
            self.assertEqual(cur.await_args.args[0], "5 USD в EUR")
            card = run(self.engine.run("currency.convert", {"from": "km", "to": "Moscow"}))
        self.assertEqual(card["code"], "bad_params")

    def test_ordinary_words_are_not_currencies(self):
        for text in ("курс лечения сомнительный", "какой курс выбрать на батарею", "Лариса курс"):
            self.assertEqual(instant_answers.intent(text), "", text)
        for text in ("курс доллара", "5 сомов в рубли", "курс лиры", "10 usd to eur", "курс юаня к рублю"):
            self.assertEqual(instant_answers.intent(text), "currency", text)

class DateAndYoutubeTest(EngineCase):
    def test_days_until_needs_a_date(self):
        card = run(self.engine.run("date.calc", {"op": "until"}))
        self.assertEqual(card["code"], "bad_params")

    def test_youtube_errors_follow_the_language(self):
        exc = yt.TranscriptUnavailable("нет субтитров", "no_captions", "no captions")
        self.engine._ask_ai = mock.AsyncMock(return_value="x")
        with mock.patch.object(yt, "analyze", mock.AsyncMock(side_effect=exc)):
            card = run(self.engine.run("youtube.analyze", {"url": "https://youtu.be/dQw4w9WgXcQ"}, lang="en"))
        self.assertEqual((card["code"], card["message"]), ("no_captions", "no captions"))

if __name__ == "__main__":
    unittest.main()
