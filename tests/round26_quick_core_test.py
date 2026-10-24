"""Round 26 (r26-quick-core): «Быстрый доступ» — регрессии найденных багов.

1. «Сделай саммари страницы» пересказывало чужой текст (буфер обмена / старую вкладку «WebGL Aquarium»):
   страница берётся из ТЕКУЩЕЙ вкладки (``QuickAskManager.current_page``), без вкладки — честное «не нашёл»,
   ``assistant_action text.summarize`` без текста при вопросе о странице не читает буфер.
2. Результат команды (карточка) не попадал в чат, «Недавние» лишь вставляли вопрос в поле.
3. Свой эндпоинт на localhost шёл через системный/env-прокси.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from app import quick_ask_api
from app import quick_ask_browser as qb
from app import quick_ask_core as core
from app import quick_ask_endpoint as endpoint

ROOT = Path(__file__).resolve().parents[1]
QUICK = ROOT / "app" / "pgstatic" / "quick"


def sse(*items):
    for item in items:
        yield "data: " + json.dumps(item, ensure_ascii=False)
    yield "data: [DONE]"


def delta(text):
    return {"choices": [{"delta": {"content": text}}]}


def run_events(**kw):
    requests = []
    replies = list(kw.pop("replies"))

    async def stream_lines(path, payload):
        requests.append(json.loads(payload.decode("utf-8")))
        for line in sse(delta(replies.pop(0))):
            yield line

    async def collect():
        out = []
        args = dict(images=[], selection="", model="m", history=[], vision=True, native_search=False,
                    settings=dict(core.PREF_DEFAULTS, quick_ask_web_search=False), lang="ru", stream_lines=stream_lines,
                    run_tool=None, rich_search=None, rich_payload=None, capture=None)
        args.update(kw)
        async for chunk in quick_ask_api.ask_events(**args):
            out.append(json.loads(chunk[6:]))
        return out
    return asyncio.run(collect()), requests


SUMMARIZE_CALL = '```memuai-tool\n[{"tool":"assistant_action","args":{"action":"text.summarize","params":{}}}]\n```'


class PageQuestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clipboard_used = []

        async def handler(args, ctx):
            self.clipboard_used.append(args)
            return {"ok": True, "message": "Бенчмарк аквариум: …"}
        self._saved = quick_ask_api.EXTRA_TOOLS.get("assistant_action")
        quick_ask_api.EXTRA_TOOLS["assistant_action"] = {"description": "{action, params} — run", "handler": handler,
                                                        "label": None, "enabled": None}

    def tearDown(self) -> None:
        if self._saved is None:
            quick_ask_api.EXTRA_TOOLS.pop("assistant_action", None)
        else:
            quick_ask_api.EXTRA_TOOLS["assistant_action"] = self._saved

    def test_phrases_that_mean_the_page(self) -> None:
        for text in ("Сделай саммари страницы", "самари", "резюме статьи", "что на этой вкладке", "summary of the page",
                     "sum up the article", "кратко перескажи страницу"):
            self.assertTrue(quick_ask_api.wants_page(text), text)
        for text in ("сколько будет 2+2", "какая погода завтра", "привет"):
            self.assertFalse(quick_ask_api.wants_page(text), text)

    def test_summarize_tool_reads_the_tab_not_the_clipboard(self) -> None:
        reads = []

        async def read_page(max_chars=24000):
            reads.append(max_chars)
            return {"tool": "read_page", "source": "fetch", "url": "https://news.dev/a", "title": "A", "text": "ТЕКСТ ВКЛАДКИ", "chars": 13}
        page = {"id": "1-1", "url": "https://news.dev/a", "title": "A", "browser": "Google Chrome"}
        events, requests = run_events(text="Сделай саммари страницы", page=page, read_page=read_page,
                                      preread_page=False, replies=[SUMMARIZE_CALL, "Суть статьи."])
        self.assertEqual(self.clipboard_used, [])
        self.assertIn("ТЕКСТ ВКЛАДКИ", requests[1]["messages"][-1]["content"])
        self.assertIn("not the clipboard", requests[1]["messages"][-1]["content"])
        self.assertEqual(events[-1]["text"], "Суть статьи.")

    def test_no_tab_is_said_honestly(self) -> None:
        events, requests = run_events(text="Сделай саммари страницы", page=None, read_page=None, no_page=True,
                                      replies=[SUMMARIZE_CALL, "Не нашёл открытую страницу."])
        system = requests[0]["messages"][0]["content"]
        self.assertIn("No browser tab is open", system)
        self.assertIn("Never summarize the clipboard", system)
        self.assertEqual(self.clipboard_used, [])
        self.assertIn("clipboard is NOT the page", requests[1]["messages"][-1]["content"])

    def test_real_text_still_goes_to_the_action(self) -> None:
        long_text = "Длинный текст пользователя. " * 6
        call = '```memuai-tool\n' + json.dumps([{"tool": "assistant_action", "args": {
            "action": "text.summarize", "params": {"text": long_text}}}], ensure_ascii=False) + '\n```'
        run_events(text="кратко перескажи это", page=None, read_page=None, replies=[call, "Ок."])
        self.assertEqual(len(self.clipboard_used), 1)
        self.assertTrue(quick_ask_api._clipboard_text_action("assistant_action", {"action": "text.translate"}))
        self.assertFalse(quick_ask_api._clipboard_text_action("assistant_action", {"action": "volume.set"}))
        self.assertFalse(quick_ask_api._clipboard_text_action(
            "assistant_action", {"action": "text.summarize", "params": {"text": "https://a.dev/x"}}))


class RouteUsesTheCurrentTab(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="r26qc-")
        self.env = mock.patch.dict(os.environ, {"MEMUAI_DATA_DIR": self.tmp})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ask(self, manager, body):
        from starlette.testclient import TestClient
        from app.playground_api import create_app
        seen = {}

        async def fake_events(**kw):
            seen.update(kw)
            yield "data: {}\n\n"
        # create_app() registers the assistant_action tool globally — restore it (tests.quick_ask_test expects none)
        with mock.patch.dict(quick_ask_api.EXTRA_TOOLS):
            app = create_app()
        app.state.quick_ask = manager
        with mock.patch.object(quick_ask_api, "ask_events", fake_events):
            client = TestClient(app, base_url="http://127.0.0.1")
            client.post("/api/quick/ask", json=dict({"model": "m"}, **body))
        return seen

    def test_stale_chip_is_replaced_by_the_tab_the_user_is_on(self) -> None:
        class Manager:
            page = {"id": "a-1", "hwnd": 10, "url": "https://webglsamples.org/aquarium/aquarium.html", "title": "WebGL Aquarium"}

            def current_page(self):
                return {"id": "b-2", "hwnd": 11, "url": "https://habr.com/ru/articles/1/", "title": "Статья", "browser": "Chrome"}
        seen = self.ask(Manager(), {"text": "Сделай саммари страницы", "page": {"id": "a-1", "url": Manager.page["url"]}})
        self.assertEqual(seen["page"]["url"], "https://habr.com/ru/articles/1/")
        self.assertTrue(seen["preread_page"])
        self.assertFalse(seen["no_page"])

    def test_without_a_browser_the_model_is_told_so(self) -> None:
        class Manager:
            page = None

            def current_page(self):
                return None
        seen = self.ask(Manager(), {"text": "Сделай саммари страницы"})
        self.assertIsNone(seen["page"])
        self.assertTrue(seen["no_page"])
        self.assertFalse(seen["preread_page"])
        seen = self.ask(Manager(), {"text": "сколько будет 2+2"})
        self.assertFalse(seen["no_page"], "an ordinary question does not mention pages")


class CurrentPageTests(unittest.TestCase):
    def test_pick_top_browser(self) -> None:
        rows = [{"hwnd": 1, "exe": "memuai.exe", "title": "MemuAI", "visible": True},
                {"hwnd": 2, "exe": "chrome.exe", "title": "", "visible": True},           # a helper window
                {"hwnd": 3, "exe": "msedge.exe", "title": "Old", "visible": True, "iconic": True},
                {"hwnd": 4, "exe": "firefox.exe", "title": "Other desktop", "visible": True, "cloaked": True},
                {"hwnd": 5, "exe": "chrome.exe", "title": "Новости - Google Chrome", "visible": True},
                {"hwnd": 6, "exe": "chrome.exe", "title": "WebGL Aquarium - Google Chrome", "visible": True}]
        self.assertEqual(qb.pick_top_browser(rows), 5)
        self.assertEqual(qb.pick_top_browser(rows, exclude=(5,)), 6)
        self.assertEqual(qb.pick_top_browser(rows[:4]), 0)

    def manager(self):
        from app.quick_ask import QuickAskManager
        m = QuickAskManager.__new__(QuickAskManager)
        m.native_ok, m.api, m.hwnd, m.page, m._page_token = True, object(), 100, None, 0
        m.pushed = []
        m._push = m.pushed.append
        return m

    def test_current_page_follows_the_tab_and_reports_none(self) -> None:
        m = self.manager()
        m.page = {"id": "6-1", "hwnd": 6, "url": "https://webglsamples.org/aquarium/aquarium.html", "title": "WebGL Aquarium"}
        fresh = {"hwnd": 5, "url": "https://habr.com/x", "title": "Новости", "browser": "Google Chrome", "source": "uia"}
        with mock.patch.object(qb, "top_level_windows", lambda: [{"hwnd": 5, "exe": "chrome.exe", "title": "Новости", "visible": True}]), \
                mock.patch.object(qb, "detect", lambda hwnd: dict(fresh)):
            page = m.current_page()
            self.assertEqual(page["url"], "https://habr.com/x")
            self.assertIs(m.page, page)
            self.assertTrue(page["_shown"].is_set())
            self.assertIn("QuickAsk.onPage(", m.pushed[-1])
            self.assertIn(", true)", m.pushed[-1])
            again = m.current_page()
            self.assertIs(again, page, "the same tab keeps its id (the UIA text fallback matches it)")
            self.assertEqual(len(m.pushed), 1)
        with mock.patch.object(qb, "top_level_windows", lambda: []):
            self.assertIsNone(m.current_page())
            self.assertIsNone(m.page)
            self.assertEqual(m.pushed[-1], "window.QuickAsk && QuickAsk.onPage && QuickAsk.onPage(null, true)")

    def test_raise_refreshes_the_page(self) -> None:
        src = (ROOT / "app" / "quick_ask.py").read_text(encoding="utf-8")
        block = src[src.index("    def toggle(self)"):src.index("    def show(self")]
        self.assertIn("target=self.current_page", block)


class LocalEndpointProxy(unittest.TestCase):
    def test_is_local(self) -> None:
        for url in ("http://localhost:11434/v1", "http://127.0.0.1:1234/v1", "http://192.168.1.5:8080/v1",
                    "http://[::1]:11434/v1", "http://gpu.local:8000/v1"):
            self.assertTrue(endpoint.is_local(url), url)
        for url in ("https://api.openai.com/v1", "https://8.8.8.8/v1", ""):
            self.assertFalse(endpoint.is_local(url), url)

    def test_local_client_ignores_proxy_env(self) -> None:
        with mock.patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9",
                                          "NO_PROXY": ""}):
            local = endpoint._client(url="http://localhost:11434/v1")
            remote = endpoint._client(url="https://api.openai.com/v1")
            try:
                self.assertFalse(local._trust_env)
                self.assertTrue(remote._trust_env)
            finally:
                asyncio.run(local.aclose())
                asyncio.run(remote.aclose())


class PageScripts(unittest.TestCase):
    def test_commands_plugin_leaves_page_questions_to_the_model(self) -> None:
        js = (QUICK / "plugins" / "commands.js").read_text(encoding="utf-8")
        ask = re.search(r"const PAGE_ASK = /(.+)/i;", js).group(1)
        noun = re.search(r"const PAGE_NOUN = /(.+)/i;", js).group(1)
        if not shutil.which("node"):
            self.skipTest("no node")
        script = ("const a=new RegExp(%s,'i'),n=new RegExp(%s,'i');" % (json.dumps(ask.replace("\\\\", "\\")), json.dumps(noun.replace("\\\\", "\\")))
                  + "console.log(JSON.stringify(%s.map(t=>a.test(t)&&n.test(t))))")
        phrases = ["Сделай саммари страницы", "кратко перескажи эту страницу", "summarize this page",
                   "закрой вкладку", "громкость 50", "кратко перескажи: текст"]
        out = subprocess.run(["node", "-e", script % json.dumps(phrases, ensure_ascii=False)], capture_output=True, text=True, timeout=20)
        self.assertEqual(json.loads(out.stdout), [True, True, True, False, False, False])
        self.assertIn("recordResult(text, data.card);", js)

    def test_quick_js_contracts(self) -> None:
        js = (QUICK / "quick.js").read_text(encoding="utf-8")
        self.assertIn("recordTurn: (row) => recordTurn(row)", js)
        self.assertIn('api("/api/quick/chats?limit=3")', js)
        self.assertIn("run: () => openChat(chat.id)", js, "«Recent» opens the chat with its answer")
        self.assertIn("onPage(page, replace = false)", js)
        self.assertIn("if (event.url || event.title) turn.page", js)


@unittest.skipUnless(Path("/workspace/work/chromium/chrome").exists() or Path("/usr/bin/chromium").exists(), "no Chromium")
class Headless(unittest.TestCase):
    def test_window_checks(self) -> None:
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("no Playwright")
        import sys
        out = Path(tempfile.mkdtemp())
        env = dict(os.environ, PYTHONUTF8="1", NO_PROXY="*")
        result = subprocess.run([sys.executable, str(ROOT / "tests" / "round26_quick_core_shots.py"), str(out)], cwd=str(ROOT),
                                capture_output=True, text=True, timeout=110, env=env)
        shutil.rmtree(out, ignore_errors=True)
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
