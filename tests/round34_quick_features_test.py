"""Round 34 (r34-quick-features): Quick Access features — commands, calculator, clipboard history, explorer.

Every case here was a real bug found by the round-34 beta test (phrase matrix, a fake Windows backend, headless UI):
* the instant calculator read «1 000 + 1» as 1·000+1 = 1, phones / dates / IP addresses as products;
* «прогноз продаж», «type of variable in python» were weather / typing commands; «kill -9 1234» an app name;
* «очисти буфер» failed with «missing: text»; «открой телегу» found nothing; «убей процесс 4» asked first, refused after;
* bank cards and more provider tokens went into the clipboard history;
* the AI router's prompt now says physical-world things («turn off the lights») are chat.
No network, no Windows.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import assistant_apps as apps  # noqa: E402
from app import assistant_commands as ac  # noqa: E402
from app import assistant_intents as intents  # noqa: E402
from app import assistant_router as router  # noqa: E402
from app import quick_features  # noqa: E402

NOW = datetime(2026, 10, 11, 22, 10)
CALC = ROOT / "app" / "pgstatic" / "quick" / "quick-calc.js"


def run(coro):
    return asyncio.run(coro)


def m(text):
    found = intents.match(text, now=NOW)
    return (found["action"], found["params"]) if found else None


@unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
class CalculatorTest(unittest.TestCase):
    def calc(self, *cases):
        script = (f"const c = require({json.dumps(str(CALC))});"
                  f"console.log(JSON.stringify({json.dumps(list(cases))}.map((q) => {{ const r = c.evaluate(q, 'ru'); return r && r.value; }})));")
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_digit_groups_are_one_number(self):
        self.assertEqual(self.calc("1 000 + 1", "1 000 000 * 2", "2 000 * 3", "1 000,5 + 1", "12 345 678 / 2"),
                         [1001, 2000000, 6000, 1001.5, 6172839])

    def test_phones_dates_addresses_are_not_maths(self):
        self.assertEqual(self.calc("2 3", "192.168.1.1", "11.10.2026", "2024-10-11", "+7 999 123-45-67", "8 800 555 35 35",
                                   "123-45-67", "(495) 123-45-67", "5 % 3"), [None] * 9)

    def test_implicit_multiplication_that_is_meant(self):
        values = self.calc("2π", "2 pi", "3 sqrt 9", "2(3)", "(2+3)*4", "2^10 + 15%", "100-10%")
        self.assertAlmostEqual(values[0], 6.283185, places=5)
        self.assertAlmostEqual(values[1], 6.283185, places=5)
        self.assertEqual(values[2:], [9, 6, 20, 1177.6, 90])

    def test_accusative_unit_names(self):
        values = self.calc("1 гигабайт в мегабайты", "10 мин в часы", "1 неделю в днях", "5 км в метры", "2 часа в минуты")
        self.assertEqual(values[0], 1024)
        self.assertAlmostEqual(values[1], 1 / 6, places=6)
        self.assertEqual(values[2:], [7, 5000, 120])


@unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
class CurrencyHintTest(unittest.TestCase):
    def test_codes_after_k_and_m_and_words_that_are_not_money(self):
        extras = ROOT / "app" / "pgstatic" / "quick" / "plugins" / "extras.js"
        cases = ["5 KRW", "100 kzt", "5 mdl", "12.5k rub", "1 try", "10 sum", "5 вон", "1 try в rub", "100 usd в rub"]
        script = (f"const x = require({json.dumps(str(extras))});"
                  f"console.log(JSON.stringify({json.dumps(cases)}.map((c) => x.parseCurrency(c))));")
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
        got = json.loads(out.stdout)
        self.assertEqual([g and g["from"] for g in got[:4]], ["KRW", "KZT", "MDL", "RUB"])
        self.assertEqual(got[3]["amount"], 12500)
        self.assertEqual(got[4:7], [None, None, None])
        self.assertEqual(got[7], {"amount": 1, "from": "TRY", "to": "RUB"})
        self.assertEqual(got[8], {"amount": 100, "from": "USD", "to": "RUB"})


class IntentFixesTest(unittest.TestCase):
    def test_forecast_of_something_else_is_not_the_weather(self):
        self.assertIsNone(m("прогноз продаж на квартал"))
        self.assertIsNone(m("forecast revenue for 2027"))
        self.assertEqual(m("прогноз на завтра"), ("weather.get", {"when": "tomorrow"}))
        self.assertEqual(m("forecast for tomorrow"), ("weather.get", {"when": "tomorrow"}))
        self.assertEqual(m("weather for tomorrow in London"), ("weather.get", {"when": "tomorrow", "city": "London"}))
        self.assertEqual(m("прогноз погоды в Ростове-на-Дону")[1]["city"], "Ростове-на-Дону")

    def test_type_as_a_noun_is_not_typing(self):
        for text in ("type of variable in python", "type hints in typescript", "type error in my code", "type safety"):
            self.assertIsNone(m(text), text)
        self.assertEqual(m("type: hello"), ("type.text", {"text": "hello"}))
        self.assertEqual(m("type hello world"), ("type.text", {"text": "hello world"}))
        self.assertEqual(m('type "of course"'), ("type.text", {"text": "of course"}))

    def test_kill_by_pid(self):
        for text in ("kill -9 1234", "taskkill /f /pid 1234", "убей 1234", "kill 1234"):
            self.assertEqual(m(text), ("process.kill", {"pid": 1234}), text)

    def test_bare_number_timer_is_minutes(self):
        self.assertEqual(m("таймер 10"), ("timer.start", {"seconds": 600}))
        self.assertEqual(m("таймер на 10"), ("timer.start", {"seconds": 600}))
        self.assertEqual(m("timer 5"), ("timer.start", {"seconds": 300}))
        self.assertIsNone(m("таймер 0"))
        self.assertEqual(m("таймер на 3 минуты для яиц"), ("timer.start", {"seconds": 180, "label": "для яиц"}))

    def test_this_window(self):
        self.assertEqual(m("закрой это"), ("window.action", {"action": "close"}))
        self.assertEqual(m("закрой его"), ("window.action", {"action": "close"}))
        self.assertEqual(m("сверни это"), ("window.action", {"action": "minimize"}))
        self.assertEqual(m("разверни его"), ("window.action", {"action": "maximize"}))
        self.assertEqual(m("сверни хром"), ("window.action", {"action": "minimize", "target": "хром"}))

    def test_clear_clipboard_phrase(self):
        self.assertEqual(m("очисти буфер"), ("clipboard.set", {"clear": True}))

    def test_router_prompt_keeps_the_physical_world_out(self):
        system = router.build_messages("turn off the lights", {"disabled_actions": []}, {})[0]["content"]
        self.assertIn("lights", system)
        self.assertIn('{"a":"chat"}', system.replace("\\", ""))


class AppNamesTest(unittest.TestCase):
    def test_accusative_alias(self):
        index = apps.merge_index(apps.builtin_entries(), [apps._entry("Telegram Desktop", "lnk", r"C:\S\Telegram Desktop.lnk"),
                                                          apps._entry("Opera", "lnk", r"C:\S\Opera.lnk")])
        self.assertEqual(apps.resolve("телегу", index)["match"]["name"], "Telegram Desktop")
        self.assertEqual(apps.resolve("оперу", index)["match"]["name"], "Opera")
        self.assertIsNone(apps.resolve("игру", index)["match"])


class EngineFixesTest(unittest.TestCase):
    def setUp(self):
        import round33_quick_commands_test as T
        self.T = T
        self.case = T.Base()
        self.case.setUp()
        self.engine = self.case.engine
        self.engine.rate.allow = lambda *_a, **_k: True

    def tearDown(self):
        self.case.tearDown()

    def go(self, text, lang="ru"):
        found = self.engine.match(text, lang)
        card = run(self.engine.run(found["action"], found["params"], lang=lang))
        if card["status"] == "confirm":
            card = run(self.engine.confirm(card["confirm"]["token"], True, lang))
        return card

    def test_clear_clipboard_works(self):
        card = self.go("очисти буфер")
        self.assertEqual(card["status"], "done", card)
        self.assertEqual(card["message"], "Буфер обмена очищен")
        self.assertEqual(self.case.backend.clip, "")
        bad = run(self.engine.run("clipboard.set", {}, lang="ru"))
        self.assertEqual(bad["status"], "error")
        self.assertNotIn("missing", bad["message"])

    def test_param_errors_are_russian(self):
        card = run(self.engine.run("volume.set", {"level": "громко"}, lang="ru"))
        self.assertEqual(card["code"], "bad_params")
        self.assertIn("нужно число", card["message"])
        card = run(self.engine.run("volume.set", {"level": "loud"}, lang="en"))
        self.assertIn("expected a number", card["message"])

    def test_settings_page_has_a_human_name(self):
        self.assertTrue(self.go("открой настройки звука")["message"].endswith("· Звук"))
        self.assertTrue(self.go("open sound settings", "en")["message"].endswith("· Sound"))

    def test_system_process_refused_before_the_question(self):
        for text in ("убей процесс 4", "убей процесс csrss", "заверши процесс System"):
            found = self.engine.match(text)
            card = run(self.engine.run(found["action"], found["params"], lang="ru"))
            self.assertEqual((card["status"], card.get("code")), ("error", "blocked"), text)
        found = self.engine.match("убей процесс chrome.exe")
        self.assertEqual(run(self.engine.run(found["action"], found["params"]))["status"], "confirm")


class ClipboardSecretsTest(unittest.TestCase):
    def test_cards_and_tokens_are_secret(self):
        for text in ("4111 1111 1111 1111", "4111-1111-1111-1111", "5500000000000004", "hf_" + "A" * 34, "glpat-" + "x" * 20,
                     "xai-" + "a" * 60, "gsk_" + "a" * 50, "npm_" + "a" * 36, "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123"):
            self.assertTrue(quick_features.looks_secret(text), text)

    def test_ordinary_numbers_are_not(self):
        for text in ("1234567890123456", "+7 999 123 45 67", "ISBN 978-3-16-148410-0", "0000 0000 0000 0000",
                     "2026-10-11 22:00:00", "заказ 100000000000000", "обычный текст"):
            self.assertFalse(quick_features.looks_secret(text), text)


class PageReadTest(unittest.TestCase):
    HTML = """<html><head><title>repo</title></head><body><header>nav</header>
    <main><react-partial><div class="files"><table><tr><td>src</td><td>update deps and things</td></tr>
    <tr><td>build</td><td>more commit messages that are long enough</td></tr></table></div>
    <turbo-frame><article class="markdown-body"><h1>Project</h1><h2>Install</h2>
    <p>""" + "Install the project with the package manager and read the guide carefully. " * 8 + """</p>
    <ul><li>first step of the setup</li><li>second step of the setup</li></ul>
    <h2>Usage</h2><p>""" + "Run the tool from the command line with the options you need for the job. " * 8 + """</p>
    </article></turbo-frame></react-partial></main></body></html>"""

    def test_custom_elements_keep_structure_and_the_inner_article_wins(self):
        from app import quick_page_read as P
        text = P.extract(self.HTML, "https://example.com/repo")["text"]
        self.assertTrue(text.startswith("# Project"), text[:200])
        self.assertIn("\n## Install\n", text)
        self.assertIn("\n- first step of the setup\n- second step of the setup", text)
        self.assertIn("\n## Usage\n", text)
        self.assertNotIn("update deps", text)        # the file list of the <main> is not the content


class ExplorerUndoTest(unittest.TestCase):
    def test_undo_of_a_copy_keeps_work_done_in_it(self):
        from app import quick_explorer as qe
        base = Path(tempfile.mkdtemp(prefix="r34-explorer-"))
        (base / "src").mkdir()
        (base / "src" / "a.txt").write_text("a", encoding="utf-8")
        ctx = {"id": "r34", "folder": str(base), "writable": True}
        change = qe.plan_changes(ctx, [{"op": "copy", "path": "src", "to": "copy"}])
        qe.apply_set(change["id"])
        (base / "copy" / "mine.txt").write_text("my work", encoding="utf-8")     # the user works in the copy
        result = qe.undo(qe.get_set(change["id"])["undo_id"])
        self.assertEqual(result["undone"], 0)
        self.assertTrue((base / "copy" / "mine.txt").is_file())
        self.assertIn("копия изменена", " ".join(result["problems"]))
        # an untouched copy is still undone
        change = qe.plan_changes(ctx, [{"op": "copy", "path": "src", "to": "copy2"}])
        qe.apply_set(change["id"])
        self.assertEqual(qe.undo(qe.get_set(change["id"])["undo_id"])["undone"], 1)
        self.assertFalse((base / "copy2").exists())

    def test_more_double_click_runners_are_not_opened(self):
        from app import quick_files
        for suffix in (".url", ".cpl", ".msc", ".py", ".appref-ms", ".settingcontent-ms"):
            self.assertIn(suffix, quick_files.EXECUTABLE_SUFFIXES)


class CanvasHotkeyTest(unittest.TestCase):
    def test_global_button_hotkey_never_takes_a_standard_shortcut(self):
        from app import quick_actions as qa
        wait = [{"type": "wait", "ms": 100}]
        for combo in ("Alt+F4", "Ctrl+S", "Ctrl+W", "Ctrl+T", "Ctrl+F", "Ctrl+R", "Ctrl+Shift+T", "Ctrl+F5"):
            with self.assertRaises(qa.ActionInvalid, msg=combo) as caught:
                qa.validate_action({"label": "x", "hotkey": combo, "global": True, "steps": wait})
            self.assertTrue(caught.exception.en and caught.exception.ru != caught.exception.en, combo)
        # inside the Quick Ask window only (not global) Ctrl+R is fine; a free global combo too
        self.assertEqual(qa.validate_action({"label": "x", "hotkey": "Ctrl+R", "steps": wait})["hotkey"], "Ctrl+R")
        self.assertTrue(qa.validate_action({"label": "x", "hotkey": "Ctrl+Alt+7", "global": True, "steps": wait})["global"])


class CommandsUiSourceTest(unittest.TestCase):
    def test_stale_confirms_are_retired(self):
        js = (ROOT / "app" / "pgstatic" / "quick" / "plugins" / "commands.js").read_text(encoding="utf-8")
        self.assertIn("function dropStaleConfirms()", js)
        self.assertIn("dropStaleConfirms();\n    const pending", js)
        self.assertIn("card.status = before.status", js)


if __name__ == "__main__":
    unittest.main()
