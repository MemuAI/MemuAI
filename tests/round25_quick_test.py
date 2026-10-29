"""Round 25 (a6-quick): new Quick Ask tools, the pinned window, quick-window bug fixes.

No network. The headless part runs tests/round25_quick_shots.py when Playwright + Chromium are installed.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock
from tests.round25_chat_test import _chromium

os.environ.setdefault("MEMUAI_ASSISTANT_NO_WARM", "1")

from app import assistant_commands as ac  # noqa: E402  (installs the Round 24/25 actions)
from app import assistant_catalog as catalog  # noqa: E402
from app import assistant_intents as intents  # noqa: E402
from app import assistant_r25 as r25  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
QUICK = ROOT / "app" / "pgstatic" / "quick"
NOW = datetime(2026, 10, 8, 15, 0)

def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")

class Phrases(unittest.TestCase):
    def m(self, text):
        found = intents.match(text, now=NOW)
        return (found["action"], found["params"]) if found else None

    def test_tool_phrases(self) -> None:
        cases = {
            "#ff8800 в rgb": ("color.convert", {"color": "#ff8800"}),
            "rgb(255, 136, 0)": ("color.convert", {"color": "rgb(255, 136, 0)"}),
            "декодируй base64 aGVsbG8=": ("text.encode", {"codec": "base64", "op": "decode", "text": "aGVsbG8="}),
            "url encode привет мир": ("text.encode", {"codec": "url", "op": "encode", "text": "привет мир"}),
            "отформатируй json из буфера": ("json.format", {"mode": "pretty"}),
            "сожми json": ("json.format", {"mode": "minify"}),
            'json {"a":1}': ("json.format", {"mode": "pretty", "text": '{"a":1}'}),
            "sha256 привет": ("hash.text", {"algo": "sha256", "text": "привет"}),
            "md5 буфера": ("hash.text", {"algo": "md5"}),
            "в верхний регистр привет": ("text.case", {"case": "upper", "text": "привет"}),
            "snake_case Hello World": ("text.case", {"case": "snake", "text": "Hello World"}),
            "сколько слов в буфере": ("text.stats", {}),
            "сколько слов: раз два три": ("text.stats", {"text": "раз два три"}),
            "сколько дней до нового года?": ("date.calc", {"op": "until", "date": "нового года"}),
            "сколько дней между 01.01.2026 и 15.03.2026": ("date.calc", {"op": "between", "date": "01.01.2026", "date2": "15.03.2026"}),
            "какой день недели 12.05.2026": ("date.calc", {"op": "weekday", "date": "12.05.2026"}),
            "через 30 дней какая дата": ("date.calc", {"op": "add", "amount": 30, "unit": "days"}),
            "30 дней назад": ("date.calc", {"op": "add", "amount": -30, "unit": "days"}),
            "время в токио": ("time.zone", {"city": "tokyo"}),
            "сколько времени в нью-йорке?": ("time.zone", {"city": "new_york"}),
            "15:00 по москве в токио": ("time.zone", {"time": "15:00", "from": "moscow", "city": "tokyo"}),
            "3pm new york to london": ("time.zone", {"time": "3pm", "from": "new_york", "city": "london"}),
            "брось монетку": ("random.pick", {"kind": "coin"}),
            "брось 2 кубика": ("random.pick", {"kind": "dice", "count": 2, "sides": 6}),
            "roll 2d6": ("random.pick", {"kind": "dice", "count": 2, "sides": 6}),
            "случайное число от 1 до 10": ("random.pick", {"kind": "number", "min": 1, "max": 10}),
            "выбери случайно: пицца, суши, бургер": ("random.pick", {"kind": "choice", "items": "пицца, суши, бургер"}),
            "lorem ipsum 3 абзаца": ("lorem.generate", {"paragraphs": 3}),
            "рыба текст на 50 слов": ("lorem.generate", {"words": 50}),
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.m(text), want)

    def test_not_stolen_from_the_model_or_other_commands(self) -> None:
        for text in ("url https://ya.ru", "проверь json схему", "сколько слов в романе война и мир", "костюм",
                     "выбери файл, папку", "через 30 дней напомни", "base64"):
            with self.subTest(text=text):
                self.assertIsNone(self.m(text))
        self.assertEqual(self.m("таймер 10 минут")[0], "timer.start")
        self.assertEqual(self.m("10 usd в рубли")[0], "currency.convert")
        self.assertEqual(self.m("который час")[0], "time.now")
        self.assertNotEqual((self.m("напомни через 30 дней позвонить маме") or ("",))[0], "date.calc")

    def test_unit_convert_question_regression(self) -> None:
        """R24 bug: «сколько будет 5 км в милях?» ended in «?» → handed to the model (unit.convert not question-safe)."""
        self.assertEqual(self.m("сколько будет 5 км в милях?"), ("unit.convert", {"amount": 5.0, "from": "km", "to": "mi"}))

    def test_catalog_help_and_prompt(self) -> None:
        self.assertIn("tools", catalog.CATEGORIES)
        for action_id in r25.NEW_IDS:
            self.assertIn(action_id, catalog.BY_ID)
            self.assertEqual(catalog.BY_ID[action_id]["cat"], "tools")
        self.assertIn("tools", [g["id"] for g in ac.help_groups("ru")])
        self.assertLess(len(ac.tool_prompt(ac.normalize_settings({}), "en")), 9000)
        r25.install()
        self.assertEqual(sum(1 for name in intents.rules() if name == "r25.color"), 1)
        self.assertEqual(sum(1 for a in catalog.ACTIONS if a["id"] == "color.convert"), 1)

class Pure(unittest.TestCase):
    def test_colors(self) -> None:
        self.assertEqual(r25.parse_color("#f80"), (255, 136, 0, None))
        self.assertEqual(r25.parse_color("hsl(32, 100%, 50%)")[:3], (255, 136, 0))
        self.assertEqual(r25.color_forms(255, 136, 0)["hsl"], "hsl(32, 100%, 50%)")
        self.assertEqual(r25.color_forms(0, 0, 0, 0.5)["hex"], "#00000080")
        self.assertIsNone(r25.parse_color("#12345"))
        self.assertIsNone(r25.parse_color("12"))

    def test_encode_json_hash_case_stats(self) -> None:
        self.assertEqual(r25.encode_text("привет", "base64", "encode")[0], "0L/RgNC40LLQtdGC")
        self.assertEqual(r25.encode_text("0L/RgNC40LLQtdGC", "base64"), ("привет", "decode"))
        self.assertEqual(r25.encode_text("hello", "base64")[1], "encode", "a plain word is not mistaken for Base64")
        with self.assertRaises(ValueError):
            r25.encode_text("!!", "base64", "decode")
        self.assertEqual(r25.encode_text("a b&c", "url"), ("a%20b%26c", "encode"))
        self.assertEqual(r25.encode_text("a%20b", "url"), ("a b", "decode"))
        self.assertEqual(r25.format_json('{"a":[1,2]}', "minify"), '{"a":[1,2]}')
        with self.assertRaises(json.JSONDecodeError):
            r25.format_json("{a:1}")
        self.assertEqual(r25.hash_text("hello", "md5"), "5d41402abc4b2a76b9719d911017c592")
        self.assertEqual(r25.change_case("Hello big World", "camel"), "helloBigWorld")
        self.assertEqual(r25.change_case("someVariableName", "snake"), "some_variable_name")
        self.assertEqual(r25.change_case("Щука Ёж", "translit"), "Shchuka Yozh")
        self.assertEqual(r25.change_case("ПРИВЕТ. как дела? хорошо", "sentence"), "Привет. Как дела? Хорошо")
        stats = r25.text_stats("Раз два три. Четыре!\n\nПять")
        self.assertEqual((stats["words"], stats["sentences"], stats["paragraphs"], stats["lines"]), (5, 3, 2, 3))
        self.assertTrue(r25.lorem(words=12).startswith("Lorem ipsum dolor sit amet"))
        self.assertEqual(len(r25.lorem(words=12).split()), 12)
        self.assertEqual(r25.lorem(paragraphs=3).count("\n\n"), 2)

    def test_dates(self) -> None:
        today = date(2026, 10, 8)
        self.assertEqual(r25.parse_date("нового года", today), date(2027, 1, 1))
        self.assertEqual(r25.parse_date("1 января", today), date(2027, 1, 1), "a past day without a year is next year's")
        self.assertEqual(r25.parse_date("1 января", today, future=False), date(2026, 1, 1))
        self.assertEqual(r25.parse_date("May 9, 2027", today), date(2027, 5, 9))
        self.assertEqual(r25.parse_date("31.12.26", today), date(2026, 12, 31))
        self.assertIsNone(r25.parse_date("31.02.2026", today))
        self.assertEqual(r25.add_to_date(date(2026, 1, 31), 1, "months"), date(2026, 2, 28))
        self.assertEqual(r25.add_to_date(date(2024, 2, 29), 1, "years"), date(2025, 2, 28))
        self.assertEqual(r25.plural_ru(21, "день", "дня", "дней"), "день")
        self.assertEqual(r25.plural_ru(12, "день", "дня", "дней"), "дней")

    def test_time_zones_and_daylight_saving(self) -> None:
        self.assertEqual(r25.zone_key("в Лондоне"), "london")
        self.assertEqual(r25.zone_key("по Москве"), "moscow")
        self.assertEqual(r25.zone_key("Нью-Йорке"), "new_york")
        self.assertEqual(r25.zone_key("Атлантида"), "")
        self.assertEqual(r25.utc_offset("london", datetime(2026, 7, 1, 12)), 60)
        self.assertEqual(r25.utc_offset("london", datetime(2026, 1, 15, 12)), 0)
        self.assertEqual(r25.utc_offset("london", datetime(2026, 3, 29, 0, 59)), 0)
        self.assertEqual(r25.utc_offset("london", datetime(2026, 3, 29, 1, 0)), 60)
        self.assertEqual(r25.utc_offset("new_york", datetime(2026, 3, 8, 6, 59)), -300)
        self.assertEqual(r25.utc_offset("new_york", datetime(2026, 3, 8, 7, 0)), -240)
        self.assertEqual(r25.utc_offset("new_york", datetime(2026, 11, 1, 6, 0)), -300)
        self.assertEqual(r25.utc_offset("sydney", datetime(2026, 1, 10)), 660)
        self.assertEqual(r25.utc_offset("sydney", datetime(2026, 7, 10)), 600)
        self.assertEqual(r25.utc_offset("tokyo", datetime(2026, 7, 10)), 540)
        self.assertEqual(r25.parse_clock("3pm"), (15, 0))
        self.assertEqual(r25.parse_clock("12am"), (0, 0))
        self.assertIsNone(r25.parse_clock("15"), "a bare number is not a time (10 usd в рубли)")

class FakeBackend(ac.Backend):
    name = "fake"

    def __init__(self) -> None:
        self.clip = '{"b": [1, 2], "a": "Привет"}'

    def clipboard_get(self):
        return self.clip

    def clipboard_set(self, text):
        self.clip = text

class Handlers(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.backend = FakeBackend()
        self.engine = ac.Engine(backend=self.backend, store=ac.Store(self.tmp / "a.json"), audit=ac.Audit(self.tmp / "a.jsonl"),
                                clock=lambda: datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc).timestamp(), now_fn=lambda: NOW)

    def run_action(self, action, params, lang="ru"):
        return asyncio.run(self.engine.run(action, params, lang=lang))

    def test_cards(self) -> None:
        card = self.run_action("color.convert", {"color": "hsl(32,100%,50%)"})
        self.assertEqual((card["status"], card["message"], card["swatch"]), ("done", "#ff8800", "#ff8800"))
        card = self.run_action("json.format", {"mode": "pretty"})
        self.assertEqual(card["status"], "done")
        self.assertEqual(self.backend.clip, json.dumps({"b": [1, 2], "a": "Привет"}, ensure_ascii=False, indent=2), "result copied")
        card = self.run_action("json.format", {"text": "{a:1}"}, lang="en")
        self.assertEqual(card["status"], "error")
        self.assertIn("line 1, column 2", card["message"])
        card = self.run_action("text.encode", {"codec": "base64", "op": "decode", "text": "!!"})
        self.assertEqual((card["status"], card["code"]), ("error", "bad_input"))
        card = self.run_action("date.calc", {"op": "until", "date": "нового года"})
        self.assertIn("85 дней", card["message"])
        card = self.run_action("date.calc", {"op": "between", "date": "01.01.2026", "date2": "15.03.2026"}, lang="en")
        self.assertIn("73 days", card["message"])
        card = self.run_action("time.zone", {"city": "new_york", "time": "15:00", "from": "moscow"})
        self.assertEqual(card["message"], "15:00 Москва = 08:00 Нью-Йорк")
        card = self.run_action("time.zone", {"city": "tokyo"}, lang="en")
        self.assertEqual(card["message"], "It is 21:00 in Tokyo")
        card = self.run_action("time.zone", {"city": "atlantis"})
        self.assertEqual(card["status"], "error")
        card = self.run_action("random.pick", {"kind": "dice", "count": 3, "sides": 6})
        rolls = [int(x) for x in card["message"].split("=")[0].split("+")]
        self.assertEqual(len(rolls), 3)
        self.assertTrue(all(1 <= r <= 6 for r in rolls))
        card = self.run_action("random.pick", {"kind": "number", "min": 10, "max": 1})
        self.assertTrue(1 <= int(card["message"]) <= 10)
        self.backend.clip = ""
        card = self.run_action("hash.text", {"algo": "md5"})
        self.assertEqual((card["status"], card["code"]), ("error", "empty"), "an empty clipboard is an honest error")

    def test_private_text_never_reaches_the_journal(self) -> None:
        self.run_action("text.encode", {"codec": "base64", "op": "encode", "text": "мой секретный текст"})
        self.run_action("hash.text", {"algo": "sha256", "text": "пароль123"})
        self.run_action("text.translate", {"text": "личное письмо", "to": "en"})
        journal = (self.tmp / "a.jsonl").read_text(encoding="utf-8")
        for secret in ("мой секретный текст", "0LzQvtC5", "пароль123", "личное письмо"):
            self.assertNotIn(secret, journal)
        self.assertIn('"action": "text.encode"', journal)

class PinnedWindow(unittest.TestCase):
    class Api:
        def __init__(self, foreground=200, foreground_topmost=False):
            self.calls, self.fg, self.fg_topmost = [], foreground, foreground_topmost

        def GetForegroundWindow(self):
            return self.fg

        def GetWindowLong(self, hwnd, index):
            return 0x8 if self.fg_topmost else 0

        def SetWindowPos(self, hwnd, insert, x, y, w, h, flags):
            self.calls.append((insert, flags))
            return True

    def manager(self, api, pinned=False):
        from app.quick_ask import QuickAskManager
        m = QuickAskManager.__new__(QuickAskManager)
        import threading
        m.api, m.hwnd, m.visible, m.pinned, m.lowered, m._dragging, m._lock = api, 100, True, pinned, False, False, threading.RLock()

        m.load_prefs = lambda: {"quick_ask_on_leave": "back"}
        return m

    def test_unpinned_steps_back_behind_the_program_the_user_went_to(self) -> None:
        from app import quick_ask as qa
        api = self.Api()
        m = self.manager(api)
        self.assertTrue(m.step_back())
        self.assertTrue(m.lowered)
        self.assertEqual([c[0] for c in api.calls], [qa.HWND_NOTOPMOST, 200])
        self.assertTrue(all(c[1] & qa.SWP_NOACTIVATE and c[1] & qa.SWP_NOMOVE for c in api.calls))

        self.assertEqual(m._z_order(0), (0, qa.SWP_NOZORDER))
        self.assertTrue(m.raise_to_top())
        self.assertFalse(m.lowered)
        self.assertEqual(api.calls[-1][0], qa.HWND_TOPMOST)
        self.assertEqual(m._z_order(0), (qa.HWND_TOPMOST, 0))

    def test_topmost_foreground_only_drops_the_topmost_flag(self) -> None:
        from app import quick_ask as qa
        api = self.Api(foreground_topmost=True)
        m = self.manager(api)
        self.assertTrue(m.step_back())
        self.assertEqual([c[0] for c in api.calls], [qa.HWND_NOTOPMOST])

    def test_pinned_and_own_window_never_step_back(self) -> None:
        api = self.Api()
        self.assertFalse(self.manager(api, pinned=True).step_back())
        self.assertFalse(self.manager(self.Api(foreground=100)).step_back(), "still our own window in front")
        self.assertEqual(api.calls, [])

    def test_blur_still_never_hides(self) -> None:
        self.assertFalse(self.manager(self.Api()).blur())

    def test_set_pinned_persists_open_state_and_raises(self) -> None:
        from app import quick_ask as qa
        saved = {}
        api = self.Api()
        m = self.manager(api)
        m.lowered = True
        with mock.patch.object(qa, "save_state", lambda patch: saved.update(patch)), \
                mock.patch.object(qa, "load_state", lambda: dict(saved)):
            self.assertTrue(m.set_pinned(True))
            self.assertEqual(saved, {"pinned": True, "pinned_open": True})
            self.assertFalse(m.lowered)
            self.assertEqual(api.calls[-1][0], qa.HWND_TOPMOST)
            m._remember_open(False)
            self.assertFalse(saved["pinned_open"])
            self.assertFalse(m.set_pinned(False))
            self.assertEqual(saved, {"pinned": False, "pinned_open": False})

    def test_source_contracts(self) -> None:
        qa = read(ROOT / "app" / "quick_ask.py")
        self.assertIn('if self.pinned and load_state().get("pinned_open"):', qa, "a pinned open window comes back after a restart")
        self.assertIn("fresh = not self.pinned and", qa, "a pinned window keeps its conversation")
        self.assertIn('mode = "last" if self.pinned else', qa, "a pinned window reopens where it was")
        api = read(ROOT / "app" / "quick_ask_api.py")
        self.assertIn('action == "inactive"', api)
        self.assertIn('action == "active"', api)

class PageContracts(unittest.TestCase):
    def test_quick_js(self) -> None:
        js = read(QUICK / "quick.js")
        self.assertIn('$("qa-pin").hidden = !NATIVE;', js, "the pin button is always visible in the app")
        self.assertIn('card.dataset.pinned = S.pinned ? "1" : "0";', js)
        self.assertIn('windowAction("inactive")', js)
        self.assertIn('windowAction("active")', js)
        self.assertIn("if (S.busy) { toast(", js, "Enter during an answer no longer stops it")

        self.assertIn('keys: ["Ctrl", "Shift", "↵"], run: openInMemuAI', js)
        self.assertNotIn("Продолжить в MemuAI (Ctrl+Enter)", js)
        self.assertIn('document.addEventListener("drop"', js)
        html = read(QUICK / "index.html")
        self.assertRegex(html, r"quick\.js\?v=3\.\d+-r[234]\d")
        self.assertIn('[data-pinned="1"]', read(QUICK / "quick.css"))

    def test_commands_plugin_swatch_is_validated(self) -> None:
        js = read(QUICK / "plugins" / "commands.js")
        self.assertIn("/^#[0-9a-f]{6}$/i.test(String(card.swatch", js)
        self.assertIn("tools:", js)

@unittest.skipUnless(_chromium(), "no Chromium")
class Headless(unittest.TestCase):
    def test_window_checks(self) -> None:
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("no Playwright")
        out = Path(tempfile.mkdtemp())
        env = dict(os.environ, PYTHONUTF8="1", NO_PROXY="*")
        result = subprocess.run([sys.executable, str(ROOT / "tests" / "round25_quick_shots.py"), str(out)], cwd=str(ROOT),
                                capture_output=True, text=True, timeout=110, env=env)
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])

if __name__ == "__main__":
    unittest.main()
