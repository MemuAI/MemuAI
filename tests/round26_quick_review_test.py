"""Round 26 (r26-quick-review): «Быстрый доступ» — регрессии десяти исправлений свежего прохода.

1. Напоминание в Telegram: в карточке был внутренний код («(telegram_enabled=false)») — ``telegram_problem`` ru/en.
2./5./6. Подтверждённая команда, Computer Use и DFY из окна пишут итог в чат (headless, моки /api/*).
3. В чат пишутся и details карточки (RGB/HSL у цвета).
4. index.json грузит companion.js/commands.js с ``?v=`` их текущей VERSION (иначе WebView2 отдаёт старый файл).
7. Тосты error/warn — красные (headless).
8. Двойной клик по «Отправить» не останавливает ответ (headless).
9. Длинный текст режется посередине — вопрос в конце остаётся (``clip_middle``).
10. Без чипа страницы общий «кратко» не читает вкладку (``names_page`` / about_page в /api/quick/ask).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import assistant_commands
from app import quick_ask_api

ROOT = Path(__file__).resolve().parents[1]
QUICK = ROOT / "app" / "pgstatic" / "quick"

class ClipMiddle(unittest.TestCase):
    def test_short_text_is_kept(self) -> None:
        self.assertEqual(quick_ask_api.clip_middle("вопрос?"), "вопрос?")
        self.assertEqual(quick_ask_api.clip_middle(None), "")

    def test_question_after_a_long_paste_survives(self) -> None:
        text = "СТАРТ\n" + "строка лога\n" * 20000 + "Какой номер последней строки?"
        out = quick_ask_api.clip_middle(text, 24000)
        self.assertEqual(len(out), 24000)
        self.assertTrue(out.startswith("СТАРТ"))
        self.assertTrue(out.endswith("Какой номер последней строки?"))
        self.assertRegex(out, r"\[… \d+ characters omitted …\]")

class PageWords(unittest.TestCase):
    def test_names_page_needs_a_pointer_at_the_page(self) -> None:
        for text in ("перескажи эту страницу", "саммари статьи", "summarize the tab", "кратко: о чём эта вкладка"):
            self.assertTrue(quick_ask_api.names_page(text), text)
        for text in ("Кратко: что такое фотосинтез?", "В чём суть теории относительности? Кратко.", "summary of WW2"):
            self.assertFalse(quick_ask_api.names_page(text), text)
            self.assertTrue(quick_ask_api.wants_page(text), text)

class AboutPageRoute(unittest.TestCase):
    """/api/quick/ask: without the page chip a generic «кратко» does not read the open tab."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="r26qr-")
        self.env = mock.patch.dict(os.environ, {"MEMUAI_DATA_DIR": self.tmp})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ask(self, body):
        from starlette.testclient import TestClient
        from app.playground_api import create_app
        seen = {}
        calls = []

        class Manager:
            page = None

            def current_page(self):
                calls.append(1)
                return {"id": "t-1", "hwnd": 5, "url": "https://guppy.example/", "title": "Гуппи", "browser": "Chrome"}

        async def fake_events(**kw):
            seen.update(kw)
            yield "data: {}\n\n"
        with mock.patch.dict(quick_ask_api.EXTRA_TOOLS):
            app = create_app()
        app.state.quick_ask = Manager()
        with mock.patch.object(quick_ask_api, "ask_events", fake_events):
            TestClient(app, base_url="http://127.0.0.1").post("/api/quick/ask", json=dict({"model": "m"}, **body))
        return seen, calls

    def test_generic_summary_without_chip_does_not_read_the_tab(self) -> None:
        seen, calls = self.ask({"text": "В чём суть теории относительности? Кратко."})
        self.assertEqual(calls, [])
        self.assertIsNone(seen["page"])
        self.assertFalse(seen["preread_page"])
        self.assertFalse(seen["no_page"])

    def test_explicit_page_question_still_reads_it(self) -> None:
        seen, calls = self.ask({"text": "перескажи эту страницу"})
        self.assertEqual(len(calls), 1)
        self.assertEqual(seen["page"]["url"], "https://guppy.example/")
        self.assertTrue(seen["preread_page"])

    def test_chip_on_makes_generic_words_about_the_page(self) -> None:
        seen, _ = self.ask({"text": "кратко", "page": {"id": "t-1", "url": "https://guppy.example/"}})
        self.assertTrue(seen["preread_page"])

    def test_long_text_keeps_the_question(self) -> None:
        seen, _ = self.ask({"text": "x" * 50000 + " последний вопрос?"})
        self.assertTrue(seen["text"].endswith("последний вопрос?"))
        self.assertLessEqual(len(seen["text"]), quick_ask_api.MAX_TEXT)

class TelegramProblem(unittest.TestCase):
    def test_reasons_are_human(self) -> None:
        for reason in ("telegram_enabled=false", "no token", "not paired", ""):
            for lang in ("ru", "en"):
                text = assistant_commands.telegram_problem({"reason": reason}, lang)
                self.assertNotIn("(" + reason + ")" if reason else "()", text)
                self.assertNotIn("telegram_enabled", text)
                self.assertIn("Telegram", text)
                cyr = bool(re.search("[а-яё]", text, re.I))
                self.assertEqual(cyr, lang == "ru", (lang, text))
        self.assertIn("Settings → Telegram", assistant_commands.telegram_problem({"reason": "no token"}, "en"))
        self.assertIn("выключен", assistant_commands.telegram_problem({"reason": "telegram_enabled=false"}, "ru"))

class PluginVersions(unittest.TestCase):
    def test_index_json_matches_the_scripts(self) -> None:
        index = {row["id"]: row for row in json.loads((QUICK / "plugins" / "index.json").read_text(encoding="utf-8"))["plugins"]}
        companion = (QUICK / "plugins" / "companion.js").read_text(encoding="utf-8")

        self.assertEqual(index["companion"]["v"], re.search(r'const VERSION = "([^"]+)"', companion).group(1))

        for plugin in ("commands", "features", "focus", "wake", "canvas"):
            if plugin not in index:
                continue
            script = (QUICK / "plugins" / index[plugin]["src"]).read_text(encoding="utf-8")
            rounds = [int(n) for n in re.findall(r"\bRound (\d+)", script)]
            if rounds:
                self.assertTrue(index[plugin]["v"].endswith("-r%d" % max(rounds)), (plugin, index[plugin]["v"], max(rounds)))

class Scripts(unittest.TestCase):
    def test_contracts(self) -> None:
        commands = (QUICK / "plugins" / "commands.js").read_text(encoding="utf-8")
        self.assertIn("if (card && card.ask) recordResult(card.ask, data.card);", commands)
        self.assertIn("Array.isArray(card.details)", commands)
        quick = (QUICK / "quick.js").read_text(encoding="utf-8")
        self.assertIn('kind === "error" || kind === "warn" ? "err" : kind', quick)
        dfy = (QUICK / "quick-dfy.js").read_text(encoding="utf-8")
        self.assertIn("record(entry)", dfy)

@unittest.skipUnless(Path("/workspace/work/chromium/chrome").exists() or Path("/usr/bin/chromium").exists(), "no Chromium")
class Headless(unittest.TestCase):
    def test_window_checks(self) -> None:
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("no Playwright")
        out = Path(tempfile.mkdtemp())
        env = dict(os.environ, PYTHONUTF8="1", NO_PROXY="*")
        result = subprocess.run([sys.executable, str(ROOT / "tests" / "round26_quick_review_shots.py"), str(out)], cwd=str(ROOT),
                                capture_output=True, text=True, timeout=110, env=env)
        shutil.rmtree(out, ignore_errors=True)
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])

if __name__ == "__main__":
    unittest.main()
