"""Round 26 (r26-quick-2): «Быстрый доступ», второй проход — регрессии найденных багов.

1. ``/api/quick/hotkey`` падал 500 на мусорном ``seconds``/``timeout`` (``float(...)``); inf отпускал клавишу навсегда.
2. Ошибки Windows-бэкенда команд (``assistant_actions_win``) были только по-русски — в en-UI карточка по-русски.
3. Ошибки снимка экрана / «Пустой вопрос» / «только в Windows» — только по-русски.
4. Свой эндпоинт: ``<think>…</think>`` и ``<ds_safety>…</ds_safety>`` модели шли в ответ и в сохранённый чат.
5. Неверная модель своего эндпоинта → «HTTP 404: на сервере нет /chat/completions» (путь-то есть); прокси → «Сетевая
   ошибка: 403 Forbidden».
6. Окно (headless, ``round26_quick2_shots.py``): открытие старого чата перезаписывало его текущим; «Новый чат» посреди
   ответа терял частичный ответ; удалённый чат возвращался отложенным сохранением; KaTeX не загружался в окне
   (формулы — сырой TeX); «Снимок при открытии» молча зависел от «Снимок экрана по просьбе».
"""
from __future__ import annotations

import asyncio
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

from app import assistant_actions_win as win
from app import quick_ask_api
from app import quick_ask_endpoint as endpoint

ROOT = Path(__file__).resolve().parents[1]
QUICK = ROOT / "app" / "pgstatic" / "quick"


class ApiCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="r26q2-")
        self.env = mock.patch.dict(os.environ, {"MEMUAI_DATA_DIR": self.tmp})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def client(self, manager=None):
        from starlette.testclient import TestClient
        from app.playground_api import create_app
        app = create_app()
        app.state.quick_ask = manager
        return TestClient(app, base_url="http://127.0.0.1")


class HotkeySecondsTests(ApiCase):
    def test_junk_durations_are_clamped_not_500(self) -> None:
        calls = []

        class Manager:
            def suspend_hotkey(self, seconds):
                calls.append(("suspend", seconds))
                return {"ok": True}

            def capture_combo(self, timeout):
                calls.append(("capture", timeout))
                return {"ok": False, "code": "timeout", "hotkey": ""}
        client = self.client(Manager())
        for value, expected in (("abc", 20.0), ([1], 20.0), ({"x": 1}, 20.0), ("nan", 20.0), ("inf", 20.0),
                                (1e12, 60.0), (-5, 1.0), (None, 20.0), ("", 20.0), (7.5, 7.5), ("30", 30.0)):
            calls.clear()
            response = client.post("/api/quick/hotkey", json={"action": "suspend", "seconds": value})
            self.assertEqual(response.status_code, 200, value)
            self.assertEqual(calls, [("suspend", expected)], value)
        for value, expected in (("junk", 15.0), (999, 20.0), (0, 1.0)):
            calls.clear()
            response = client.post("/api/quick/hotkey", json={"action": "capture", "timeout": value})
            self.assertEqual(response.status_code, 200, value)
            self.assertEqual(calls, [("capture", expected)], value)

    def test_helper(self) -> None:
        self.assertEqual(quick_ask_api._seconds(float("-inf"), 3.0, 1.0, 5.0), 3.0)
        self.assertEqual(quick_ask_api._seconds(True, 3.0, 1.0, 5.0), 1.0)


class EnglishErrorsTests(ApiCase):
    def test_screenshot_errors_follow_the_language(self) -> None:
        client = self.client(None)
        ru = client.post("/api/quick/screenshot", json={}).json()
        en = client.post("/api/quick/screenshot", json={"lang": "en"}).json()
        self.assertIn("Windows", ru["error"])
        self.assertTrue(re.search("[а-я]", ru["error"]))
        self.assertFalse(re.search("[а-яё]", en["error"], re.I), en)

        class Manager:
            def capture(self, monitor, region, max_side=1920, lang="ru"):
                return {"ok": False, "error": "Screenshot failed: boom" if lang == "en" else "Снимок не удался: boom"}
        en = self.client(Manager()).post("/api/quick/screenshot", json={"lang": "en"}).json()
        self.assertEqual(en["error"], "Screenshot failed: boom")

    def test_empty_question_in_english(self) -> None:
        response = self.client(None).post("/api/quick/ask", json={"lang": "en", "text": "  "})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Empty question")

    def test_manager_capture_without_windows(self) -> None:
        from app.quick_ask import QuickAskManager
        manager = QuickAskManager.__new__(QuickAskManager)
        manager.native_ok = False
        self.assertEqual(manager.capture(lang="en")["error"], "Screenshots work only on Windows")
        self.assertIn("Windows", manager.capture()["error"])
        manager.hotkey_status = {}
        status = manager.apply_preferences({"language": "en"})
        self.assertEqual(status["error"], "Quick Ask works only in the MemuAI Windows app")


class WinBackendLanguageTests(unittest.TestCase):
    def test_every_backend_error_has_an_english_text(self) -> None:
        import ast
        tree = ast.parse((ROOT / "app" / "assistant_actions_win.py").read_text(encoding="utf-8"))
        checked = 0
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "ActionError"):
                continue
            texts = [part.value for part in ast.walk(node) if isinstance(part, ast.Constant) and isinstance(part.value, str)]
            if not any(re.search("[А-Яа-яЁё]", text) for text in texts):
                continue
            checked += 1
            english = [text for text in texts if not re.search("[А-Яа-яЁё]", text) and re.search(r"[a-z]+ [a-z]+", text)]
            self.assertTrue(english, f"no English text: {ast.unparse(node)[:160]}")
        self.assertGreater(checked, 30)

    def test_text_picks_the_language(self) -> None:
        err = win.ActionError("Нельзя завершить сам MemuAI", "blocked", "MemuAI cannot end itself")
        self.assertEqual(err.text("en"), "MemuAI cannot end itself")
        self.assertEqual(err.text("ru"), "Нельзя завершить сам MemuAI")
        self.assertEqual(win.ActionError("только ru").text("en"), "только ru", "no English → the original")
        self.assertIn("Windows app", win.Unsupported().text("en"))
        with self.assertRaises(win.ActionError) as ctx:
            win.parse_keys("ctrl+alt+delete")
        self.assertIn("Windows protects", ctx.exception.text("en"))
        with self.assertRaises(win.ActionError) as ctx:
            win.parse_keys("ctrl+зюзя")
        self.assertEqual(ctx.exception.text("en"), "Unknown key: зюзя")

    def test_engine_card_uses_the_english_text(self) -> None:
        from app import assistant_commands as ac
        from app import assistant_catalog as catalog
        tmp = tempfile.mkdtemp(prefix="r26q2-eng-")
        try:
            with mock.patch.dict(os.environ, {"MEMUAI_DATA_DIR": tmp}):
                eng = ac.Engine()

                async def boom(params, **_):
                    raise win.ActionError("Спящий режим недоступен", en="Sleep is unavailable")
                action = "volume.get"
                self.assertIsNotNone(catalog.get(action))
                eng.handlers[action] = boom
                with mock.patch.object(eng, "_finish", side_effect=lambda card, *a, **k: card):
                    en = asyncio.run(eng.run(action, {}, lang="en", confirmed=True))
                    ru = asyncio.run(eng.run(action, {}, lang="ru", confirmed=True))
                self.assertEqual((en["status"], en["message"]), ("error", "Sleep is unavailable"))
                self.assertEqual(ru["message"], "Спящий режим недоступен")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class ReasoningFilterTests(unittest.TestCase):
    CASES = {
        "<think>hmm\nlet me see</think>\n\nОтвет 42.<ds_safety>safe</ds_safety>": "Ответ 42.",
        '<ds_safety verdict="ok">x</ds_safety>Hello': "Hello",
        "Answer <b>bold</b> and x < y, then a<think> literal": "Answer <b>bold</b> and x < y, then a<think> literal",
        "Use `<thinking>` in HTML? Yes.": "Use `<thinking>` in HTML? Yes.",
        "<think>unclosed reasoning": "",
        "a <ds_safety/> b": "a  b",
        "x<y and y>z": "x<y and y>z",
    }

    def test_whole_and_split_at_every_position(self) -> None:
        for text, expected in self.CASES.items():
            self.assertEqual(endpoint.ReasoningFilter().clean(text), expected, text)
            for size in (1, 2, 3, 7):
                filt = endpoint.ReasoningFilter()
                out = "".join(filt.feed(text[i:i + size])[0] for i in range(0, len(text), size)) + filt.finish()
                self.assertEqual(out.strip(), expected, (text, size))

    def test_reasoning_goes_to_reasoning_content(self) -> None:
        filt = endpoint.ReasoningFilter()
        line = "data: " + json.dumps({"choices": [{"delta": {"content": "<think>step one</think>Hi"}}]})
        (out,) = endpoint._filter_line(line, filt)
        delta = json.loads(out[6:])["choices"][0]["delta"]
        self.assertEqual(delta["content"], "Hi")
        self.assertEqual(delta["reasoning_content"], "step one")

    def test_stream_lines_and_saved_chat_are_clean(self) -> None:
        chunks = ["<th", "ink>reason", "ing</thi", "nk>", "Ответ: ", "391.", "<ds_saf", "ety>unsafe?", "</ds_safety>"]
        body = "".join("data: " + json.dumps({"choices": [{"delta": {"content": c}}]}, ensure_ascii=False) + "\n\n"
                       for c in chunks) + "data: [DONE]\n\n"

        class Response:
            status_code = 200
            headers = {"content-type": "text/event-stream"}

            async def aiter_lines(self):
                for line in body.split("\n"):
                    yield line

        class Stream:
            async def __aenter__(self):
                return Response()

            async def __aexit__(self, *a):
                return False

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            def stream(self, *a, **k):
                return Stream()

        async def collect():
            events = []

            def lines(_path, payload):
                return endpoint.stream_lines("https://example.com/v1", "", payload)
            async for raw in quick_ask_api.ask_events(
                    text="17*23", images=[], selection="", model="m", history=[], vision=False, native_search=False,
                    settings={"quick_ask_web_search": False}, lang="ru", stream_lines=lines, run_tool=None,
                    rich_search=None, rich_payload=None, capture=None):
                events.append(json.loads(raw[6:]))
            return events
        with mock.patch.object(endpoint, "_client", lambda *a, **k: Client()):
            events = asyncio.run(collect())
        shown = "".join(e["text"] for e in events if e["event"] == "delta")
        done = next(e for e in events if e["event"] == "done")
        self.assertEqual(shown.strip(), "Ответ: 391.")
        self.assertEqual(done["text"], "Ответ: 391.")
        self.assertTrue(any(e.get("status") == "reasoning" for e in events), "the reasoning shows as a status")


class EndpointErrorTests(unittest.TestCase):
    def test_unknown_model_is_named(self) -> None:
        body = json.dumps({"error": {"message": 'Model "no/such-model" does not exist.'}})
        for lang, needle in (("ru", "не знает эту модель"), ("en", "does not know this model")):
            text = endpoint.explain(404, body, url="https://api.example.com/v1", lang=lang, what="/chat/completions")
            self.assertIn(needle, text)
            self.assertNotIn("/chat/completions", text)
        text = endpoint.explain(400, json.dumps({"error": {"message": "Failed to get model"}}),
                                url="https://x/v1", lang="ru", what="/chat/completions")
        self.assertIn("не знает эту модель", text)
        # a real wrong path still says so
        text = endpoint.explain(404, "<html>404 Not Found nginx</html>", url="https://x", lang="ru", what="/chat/completions")
        self.assertIn("/v1", text)
        self.assertIn("/chat/completions", text)

    def test_unsupported_parameter_is_not_an_unknown_model(self) -> None:
        body = json.dumps({"error": {"message": "Unsupported parameter: 'max_tokens' is not supported with this model. "
                                                "Use 'max_completion_tokens' instead."}})
        text = endpoint.explain(400, body, url="https://api.openai.com/v1", lang="en", what="/chat/completions")
        self.assertNotIn("does not know this model", text)

    def test_connection_test_retries_with_max_completion_tokens(self) -> None:
        sent = []

        class Response:
            def __init__(self, status, data):
                self.status_code, self._data = status, data
                self.text = json.dumps(data)

            def json(self):
                return self._data

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, content, headers):
                body = json.loads(content)
                sent.append(body)
                if "max_tokens" in body:
                    return Response(400, {"error": {"message": "Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens' instead."}})
                return Response(200, {"choices": [{"message": {"content": "<think>x</think>OK"}}]})
        with mock.patch.object(endpoint, "_client", lambda *a, **k: Client()):
            answer = asyncio.run(endpoint.complete("https://api.openai.com/v1", "k", "gpt-5", [{"role": "user", "content": "hi"}],
                                                   max_tokens=8))
        self.assertEqual(answer, "OK")
        self.assertEqual(len(sent), 2)
        self.assertNotIn("max_tokens", sent[1])
        self.assertGreaterEqual(sent[1]["max_completion_tokens"], 8)

    def test_proxy_error(self) -> None:
        import httpx
        text = endpoint.network_error(httpx.ProxyError("403 Forbidden"), url="https://a.example/v1", lang="en")
        self.assertIn("proxy server", text)
        self.assertIn("a.example", text)


class SettingsTests(unittest.TestCase):
    def test_endpoint_url_rejects_bogus_ports_and_schemes(self) -> None:
        for bad in ("javascript:alert(1)", "localhost:abc", "https://x:99999/v1", "ftp://x/v1"):
            self.assertEqual(endpoint.normalize_url(bad), "", bad)
        self.assertEqual(endpoint.normalize_url("localhost:11434"), "http://localhost:11434")
        self.assertEqual(endpoint.normalize_url("api.xkiro.com/v1/chat/completions"), "https://api.xkiro.com/v1")

    def test_browser_switch_off_is_explained(self) -> None:
        from app import quick_ask_core as core
        off = core.system_prompt(lang="en", no_page=True, browser_off=True)
        on = core.system_prompt(lang="en", no_page=True)
        self.assertIn("turned off in Settings", off)
        self.assertNotIn("No browser tab is open", off)
        self.assertIn("No browser tab is open", on)

        async def collect():
            seen = []

            async def lines(_path, payload):
                seen.append(json.loads(payload.decode("utf-8")))
                yield "data: " + json.dumps({"choices": [{"delta": {"content": "Off."}}]})
                yield "data: [DONE]"
            async for _ in quick_ask_api.ask_events(
                    text="перескажи эту страницу", images=[], selection="", model="m", history=[], vision=False,
                    native_search=False, settings={"quick_ask_web_search": False, "quick_ask_browser": False}, lang="ru",
                    stream_lines=lines, run_tool=None, rich_search=None, rich_payload=None, capture=None, no_page=True):
                pass
            return seen
        sent = asyncio.run(collect())
        self.assertIn("turned off in Settings", sent[0]["messages"][0]["content"])


class WindowStaticTests(unittest.TestCase):
    def setUp(self) -> None:
        self.js = (QUICK / "quick.js").read_text(encoding="utf-8")
        self.html = (QUICK / "index.html").read_text(encoding="utf-8")

    def test_katex_is_loaded(self) -> None:
        self.assertIn("/vendor/katex/katex.min.js", self.html)
        self.assertIn("/vendor/katex/katex.min.css", self.html)
        self.assertTrue((ROOT / "app" / "pgstatic" / "vendor" / "katex" / "katex.min.js").exists())

    def test_auto_screenshot_is_its_own_switch(self) -> None:
        line = next(l for l in self.js.splitlines() if "quick_ask_auto_screenshot" in l)
        self.assertNotIn("quick_ask_screenshot_tool", line)

    def test_save_takes_the_chat_id_now(self) -> None:
        block = self.js[self.js.index("function saveChat()"):self.js.index("function newChat(")]
        self.assertIn("const id = S.chatId", block)
        self.assertNotIn("id: S.chatId", block)

    def test_js_syntax(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        result = subprocess.run([node, "--check", str(QUICK / "quick.js")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class WindowHeadlessTests(unittest.TestCase):
    """tests/round26_quick2_shots.py in headless Chromium (skipped without playwright/Chromium)."""

    def test_headless(self) -> None:
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("playwright is not installed")
        out = Path(tempfile.mkdtemp(prefix="r26q2-shots-"))
        try:
            env = dict(os.environ, PYTHONUTF8="1", NO_PROXY="*")
            result = subprocess.run([sys.executable, str(ROOT / "tests" / "round26_quick2_shots.py"), str(out)],
                                    capture_output=True, text=True, timeout=180, env=env, cwd=str(ROOT))
            if "Executable doesn't exist" in result.stderr or "missing dependencies" in result.stderr:
                self.skipTest("no Chromium for playwright")
            self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-3000:])
        finally:
            shutil.rmtree(out, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
