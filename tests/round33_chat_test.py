"""Round 33 (r33-chat): #2 hidden internals, #20 tool details, #21 phantom spellcheck.

python3 -m pytest -q tests/round33_chat_test.py
"""
import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import answer_guard  # noqa: E402

SAMPLES = json.loads((ROOT / "tests" / "data" / "round33_leak_samples.json").read_text(encoding="utf-8"))["samples"]
LEAK = re.compile(r"</?tool_call|<\|[^|\n]{0,40}\|>|<｜|DSML|<function|<invoke|<arg_key|\[TOOL_|<tool_response|_report>|"
                  r"<web_search>|<docs_read>|</think>|</?tool|</?web_|</?docs_|<\|")


def _prose(text):
    return "".join(chunk for code, chunk in answer_guard.segments(text) if not code)


def _expect_calls(sample, calls):
    want = sample.get("calls")
    if want is None:
        return
    got = [(c["tool"], c["args"]) for c in calls]
    assert len(got) == len(want), (sample["name"], got)
    for (name, args), (want_name, want_args) in zip(got, want):
        assert name == want_name, (sample["name"], got)
        for key, value in want_args.items():
            assert key in args, (sample["name"], key, args)
            if value is not None:
                assert args[key] == value, (sample["name"], key, args)


class AnswerGuardPython(unittest.TestCase):
    def test_samples_final(self):
        for sample in SAMPLES:
            full = "".join(sample.get("chunks") or [sample["text"]])
            visible = answer_guard.clean(full).strip()
            if "visible" in sample:
                self.assertEqual(visible, sample["visible"], sample["name"])
            if sample["name"] != "code-example-kept" and not sample.get("prose"):
                self.assertFalse(LEAK.search(_prose(visible)), (sample["name"], visible))
            _text, calls = answer_guard.extract_calls(full)
            _expect_calls(sample, calls)

    def test_samples_streaming_never_flash(self):
        """Character by character (the worst chunking): a partial view never shows internal markup."""
        for sample in SAMPLES:
            if sample["name"] == "code-example-kept" or sample.get("prose"):
                continue
            raw = ""
            for ch in "".join(sample.get("chunks") or [sample["text"]]):
                raw += ch
                if sample.get("inline_reasoning") and "</think>" not in raw:
                    continue
                shown = answer_guard.clean(raw, partial=True)
                self.assertFalse(LEAK.search(_prose(shown)), (sample["name"], shown[-80:]))

    def test_stream_guard_monotonic(self):
        for sample in SAMPLES:
            guard = answer_guard.StreamGuard()
            out = "".join(guard.feed(piece) for piece in (sample.get("chunks") or [sample["text"]]))
            out += guard.finish()
            if sample.get("inline_reasoning"):
                self.assertTrue(guard.diverged, sample["name"])
                out = guard.final()
            if sample["name"] != "code-example-kept" and not sample.get("prose"):
                self.assertFalse(LEAK.search(_prose(out)), (sample["name"], out))
            if "visible" in sample and not guard.diverged:
                self.assertEqual(out.strip(), sample["visible"], sample["name"])

    def test_plain_text_untouched(self):
        for text in ("x < y and y > z", "Use [brackets] and <b>bold</b>", "Ответ: 2 < 3, a <kbd>Esc</kbd>",
                     "Список:\n- a\n- b", "```html\n<div>|x|</div>\n```", "Стрелка <- и <= тоже",
                     # Prose that MENTIONS the tags (live: a model explaining why it will not use <tool_call>).
                     "Формат <tool_call> я использовать не могу\n\nДальше текст.",
                     "Модели пишут <tool_call> и </tool_call> вокруг JSON.", "Ответ <tool_response> тут",
                     "Тег <web_fetch> ничего не вызовет — он останется текстом."):
            self.assertEqual(answer_guard.clean(text), text)
            self.assertEqual(answer_guard.extract_calls(text), (text, []))

    def test_allowed_filter(self):
        text = '<tool_call>{"name": "web_search", "arguments": {"query": "x"}}</tool_call>'
        self.assertEqual(answer_guard.extract_calls(text, allowed={"web_fetch"})[1], [])
        self.assertEqual(answer_guard.extract_calls(text, allowed={"web_search"})[1][0]["tool"], "web_search")

    def test_clean_ai_text_uses_guard(self):
        from app.assistant_commands import clean_ai_text
        self.assertEqual(clean_ai_text("Готово.<|im_end|>"), "Готово.")
        self.assertEqual(clean_ai_text('Ок\n<tool_response>{"a":1}</tool_response>'), "Ок")

    def test_telegram_cleaner(self):
        from app.telegram_bot import _clean_internal_protocols
        self.assertEqual(_clean_internal_protocols("Привет<|eot_id|>"), "Привет")
        self.assertEqual(_clean_internal_protocols("Думаю… </think> Ответ"), "Ответ")
        self.assertEqual(_clean_internal_protocols("Ищу <tool_c", partial=True), "Ищу")


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class AnswerGuardJs(unittest.TestCase):
    def test_same_rules_in_js(self):
        out = subprocess.run(["node", str(ROOT / "tests" / "round33_answer_guard_check.js")],
                             capture_output=True, text=True, timeout=120, check=True).stdout
        data = json.loads(out)
        for sample in SAMPLES:
            row = data[sample["name"]]
            self.assertEqual(row["stream_bad"], [], sample["name"])
            if "visible" in sample:
                self.assertEqual(row["visible"], sample["visible"], sample["name"])
            _expect_calls(sample, row["calls"])
            # Python and JS agree on the visible text.
            full = "".join(sample.get("chunks") or [sample["text"]])
            self.assertEqual(row["visible"], answer_guard.clean(full).strip(), sample["name"])


CYR = re.compile(r"[А-Яа-яЁё]")
USER_DATA_CYR = ("погода Москва", "Гидрометцентр", "Прогноз", "Яндекс Погода", "Быстрый доступ (Alt+Space)",
                 "Помощник поверх всех программ", "Как пользоваться", "Сочетание клавиш", "Чат", "Орфография",
                 "Пьёт кофе без сахара", "Живёт в Москве")


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class ToolDetailsJs(unittest.TestCase):
    """#20: every tool row says what it did — readable title, subject, meta (count/duration), expandable body."""

    @classmethod
    def setUpClass(cls):
        out = subprocess.run(["node", str(ROOT / "tests" / "round33_tool_details_check.js")],
                             capture_output=True, text=True, timeout=60, check=True).stdout
        cls.data = json.loads(out)

    def test_rows_are_expandable_with_meaningful_detail(self):
        en = self.data["en"]
        for name, row in en.items():
            self.assertIn('<details class="tool-x', row["html"], name)
            self.assertIn("tool-x-body", row["html"], name)
        t = _text(en["web_search"]["html"])
        self.assertIn("Web search", t)
        self.assertIn("погода Москва", t)
        self.assertIn("6 results", t)
        self.assertIn("3.0 s", t)
        self.assertIn('href="https://meteoinfo.ru/forecasts"', en["web_search"]["html"])
        self.assertIn("Opened page", _text(en["web_fetch"]["html"]))
        self.assertIn("Example Domain", _text(en["web_fetch"]["html"]))
        self.assertIn("1256 characters", _text(en["web_fetch"]["html"]))
        docs = en["docs_read"]["html"]
        self.assertIn("Read the article", _text(docs))
        self.assertIn('data-doc="quick-ask"', docs)
        self.assertIn("Sections", _text(docs))
        code = _text(en["code_run"]["html"])
        self.assertIn("npm test", code)
        self.assertIn("exit 1", code)
        self.assertIn("Error: boom", code)
        self.assertIn("Created file", _text(en["work_write"]["html"]))
        self.assertIn("a.py:3", _text(en["work_search"]["html"]))
        mem = _text(en["memory_row"]["html"])
        self.assertIn("Saved to memory", mem)
        self.assertIn("Пьёт кофе без сахара", mem)
        self.assertIn("(updated)", mem)
        self.assertIn("await your approval", mem)
        self.assertIn("112 GB free of 512 GB", _text(en["disk_space"]["html"]))
        tree = _text(en["project_tree"]["html"])
        self.assertIn("Project structure", tree)
        self.assertIn("3 files", tree)
        self.assertIn("src/app.py", tree)
        self.assertIn("File exists", _text(en["file_exists"]["html"]))
        self.assertIn("No such file", _text(en["file_missing"]["html"]))
        self.assertIn("Windows-11", _text(en["system_info"]["html"]))
        self.assertIn("8 CPU", _text(en["system_info"]["html"]))
        failed = en["failed"]["html"]
        self.assertIn("is-failed", failed)
        self.assertIn("HTTP 404", _text(failed))

    def test_russian_rows(self):
        ru = self.data["ru"]
        self.assertIn("Поиск в интернете", _text(ru["web_search"]["html"]))
        self.assertIn("6 результатов", _text(ru["web_search"]["html"]))
        self.assertIn("Прочитана статья", _text(ru["docs_read"]["html"]))
        self.assertIn("Сохранено в память", _text(ru["memory_row"]["html"]))
        self.assertIn("ошибка", _text(ru["failed"]["html"]))
        self.assertIn("свободно 112 ГБ из 512 ГБ", _text(ru["disk_space"]["html"]))
        self.assertIn("3 файла", _text(ru["project_tree"]["html"]))
        self.assertIn("Файл есть", _text(ru["file_exists"]["html"]))

    def test_english_has_no_cyrillic_besides_user_data(self):
        for name, row in self.data["en"].items():
            text = _text(row["html"])
            for value in USER_DATA_CYR:
                text = text.replace(value, "")
            self.assertIsNone(CYR.search(text), (name, text))

    def test_escaping_and_unsafe_links(self):
        for lang in ("ru", "en"):
            for name, row in self.data[lang].items():
                self.assertNotIn("<img", row["html"], (lang, name))
                self.assertNotIn("javascript:", row["html"], (lang, name))

    def test_legacy_rows_without_detail(self):
        self.assertIn("Read the article", _text(self.data["legacy"]))
        self.assertIn("quick-ask", _text(self.data["legacy"]))

    def test_doc_chip_has_one_icon(self):
        # .doc-chip draws its book icon with ::before; a second inline icon doubled it.
        docs = self.data["en"]["docs_read"]["html"]
        chip = re.search(r'<button type="button" class="doc-chip[^>]*>(.*?)</button>', docs, re.S).group(1)
        self.assertNotIn("<svg", chip)


class PhantomSpellcheckStatic(unittest.TestCase):
    """#21: the composer's value setter hooks must chain, not replace each other."""

    def test_cmd_highlight_chains_existing_setter(self):
        src = (ROOT / "app" / "pgstatic" / "cmd-highlight.js").read_text(encoding="utf-8")
        self.assertIn('Object.getOwnPropertyDescriptor(field, "value") || Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")', src)

    def test_spellcheck_chains_existing_setter(self):
        src = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        self.assertIn('const nativeValue = Object.getOwnPropertyDescriptor(input, "value") || Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value");', src)


def _browser_ready():
    try:
        import playwright  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError:
        return False
    return Path("/usr/bin/chromium").exists() or bool(shutil.which("chromium"))


@unittest.skipUnless(_browser_ready(), "playwright/chromium not available")
class PhantomSpellcheckBrowser(unittest.TestCase):
    """#21 end to end: a misspelled draft, then the field is cleared by code (send) / «New chat» / a command —
    no red underline may stay over the home screen. /api/pg/spellcheck is the Windows API, mocked here."""

    @classmethod
    def setUpClass(cls):
        import os
        import socket
        import tempfile
        import threading
        import time
        import uvicorn
        cls.tmp = tempfile.mkdtemp(prefix="memu-r33-spell-")
        os.environ["MEMUAI_DATA_DIR"] = cls.tmp
        for key in ("NO_PROXY", "no_proxy"):
            os.environ[key] = "127.0.0.1,localhost"
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            dead = s.getsockname()[1]
        os.environ["MEMUAI_PROXY_URL"] = f"http://127.0.0.1:{dead}"
        try:
            import webview  # noqa: F401
        except ImportError:
            import types
            sys.modules["webview"] = types.SimpleNamespace(FOLDER_DIALOG=20, OPEN_DIALOG=10, SAVE_DIALOG=30)
        from app.playground_api import create_app
        from app import pgdata
        pgdata.save_preferences({"profile_onboarded": True, "profile_name": "Test", "language": "ru"})
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        cls.server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="error"))
        threading.Thread(target=cls.server.run, daemon=True).start()
        for _ in range(400):
            if cls.server.started:
                break
            time.sleep(0.05)
        cls.url = f"http://127.0.0.1:{port}/"

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_no_phantom_underline(self):
        from playwright.sync_api import sync_playwright
        bad = {"превет", "дила", "helo"}

        def mock(route):
            body = json.loads(route.request.post_data or "{}")
            if body.get("action") == "check":
                errs = [{"start": m.start(), "length": len(m.group(0)), "word": m.group(0), "suggestions": ["привет"]}
                        for m in re.finditer(r"\w+", body.get("text") or "") if m.group(0).lower() in bad]
                return route.fulfill(json={"available": True, "errors": errs})
            return route.fulfill(json={"available": True, "languages": ["ru-RU", "en-US"]})

        state = """() => { const l = document.getElementById('spell-layer'); const i = document.getElementById('input');
          return {marks: l ? l.querySelectorAll('mark').length : 0, text: l ? l.textContent : '', value: i.value,
                  kind: i.__memuCmd ? i.__memuCmd.kind : 'n/a'}; }"""
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path="/usr/bin/chromium", args=["--no-proxy-server"])
            try:
                page = browser.new_page(viewport={"width": 1200, "height": 820})
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.route("**/api/pg/spellcheck", mock)
                page.goto(self.url, wait_until="domcontentloaded", timeout=90000)
                page.wait_for_function("typeof S !== 'undefined' && !!document.querySelector('#input') && !!document.querySelector('#input').__memuCmd", timeout=90000)

                def misspell():
                    # A fresh profile opens the welcome panel over the page; the composer is reached by focus.
                    page.evaluate("() => { if (typeof closePanel === 'function') closePanel(); document.getElementById('input').focus(); }")
                    page.fill("#input", "превет как дила helo")
                    try:
                        page.wait_for_function("() => document.querySelectorAll('#spell-layer mark').length >= 3", timeout=8000)
                    except Exception:
                        raise AssertionError(page.evaluate("() => ({value: document.getElementById('input').value, active: document.activeElement && document.activeElement.id, enabled: spellcheckEnabled(), st: JSON.stringify(spellState).slice(0, 300)})"))

                # 1. send clears the field by code (no input event)
                misspell()
                page.evaluate("() => { document.getElementById('input').value = ''; }")
                page.wait_for_timeout(600)
                st = page.evaluate(state)
                self.assertEqual(st["marks"], 0, st)
                # 2. … then «New chat» moves the composer to the home screen: still nothing
                page.evaluate("() => newChat()")
                page.wait_for_timeout(600)
                self.assertEqual(page.evaluate(state)["marks"], 0)
                # 3. a draft replaced by code (command, history) — old marks never sit over other text
                misspell()
                page.evaluate("() => { document.getElementById('input').value = 'другой текст'; }")
                page.wait_for_timeout(700)
                st = page.evaluate(state)
                self.assertTrue(st["marks"] == 0 or st["text"] == st["value"], st)
                # 4. the /image chip (cmd-highlight.js) still follows code-set values through the chained setter
                page.evaluate("() => { document.getElementById('input').value = '/image кот'; }")
                self.assertEqual(page.evaluate(state)["kind"], "image")
                page.evaluate("() => { document.getElementById('input').value = ''; }")
                self.assertIn(page.evaluate(state)["kind"], ("", None))
                self.assertEqual(errors, [])
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
