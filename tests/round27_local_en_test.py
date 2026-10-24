"""Round 27: errors of the local models in both languages (no network).

``LocalModelError`` carries a Russian (``str``) and an English (``message_en``) text; the local
provider passes the English one as the provider message and the Russian one as ``message_ru``,
so the MemuAI chat in English, API clients and the panel routes no longer show Russian.
"""
from __future__ import annotations

import asyncio
import ast
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import local_models as lm

ROOT = Path(__file__).resolve().parents[1]
CYRILLIC = re.compile("[А-Яа-яЁё]")

sys.path.insert(0, str(ROOT / "tests"))
from local_models_test import write_model  # noqa: E402


class LocalModelErrorTextTests(unittest.TestCase):
    def test_every_raise_in_local_models_has_an_english_text(self):
        tree = ast.parse((ROOT / "app" / "local_models.py").read_text(encoding="utf-8"))
        single = [node.lineno for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "LocalModelError"
                  and len(node.args) < 2 and not any(k.arg == "message_en" for k in node.keywords)]
        self.assertEqual(single, [], "LocalModelError without an English text on lines %s" % single)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "LocalModelError" \
                    and len(node.args) == 2 and isinstance(node.args[1], (ast.Constant, ast.JoinedStr)):
                english = ast.unparse(node.args[1])
                self.assertIsNone(CYRILLIC.search(english), english)

    def test_errors_keep_russian_str_and_english_message_en(self):
        with tempfile.TemporaryDirectory() as folder:
            missing = Path(folder) / "none.gguf"
            with self.assertRaises(lm.LocalModelError) as caught:
                lm.register_file(missing)
            self.assertEqual(str(caught.exception), "Файл не найден")
            self.assertEqual(caught.exception.message_en, "File not found")
            junk = Path(folder) / "junk.gguf"
            junk.write_bytes(b"NOPE" + b"\0" * 32)
            with self.assertRaises(lm.LocalModelError) as caught:
                lm.read_gguf(junk)
            self.assertIn("GGUF signature", caught.exception.message_en)
        with self.assertRaises(lm.LocalModelError) as caught:
            lm.start_download("org/repo", ["Model-draft-Q4_K_M.gguf"])
        self.assertIn("draft", str(caught.exception))
        self.assertIsNone(CYRILLIC.search(caught.exception.message_en), caught.exception.message_en)
        # A plain error without an English text still reads the same in both.
        self.assertEqual(lm.LocalModelError("x").message_en, "x")

    def test_crash_description_in_english(self):
        log = ["load_tensors: something", "error: missing tensor blk.12"]
        english = lm.describe_exit(-1073741819, log, english=True)
        self.assertIn("memory access violation", english)
        self.assertIn("0xC0000005", english)
        self.assertIn("error: missing tensor blk.12", english)
        self.assertIsNone(CYRILLIC.search(english), english)
        self.assertIn("нарушение доступа", lm.describe_exit(-1073741819, log))
        self.assertEqual(lm.describe_exit(3, [], english=True), "llama-server exited with code 3.")

    def test_failed_download_and_install_report_both_languages(self):
        job = lm.Download("org/repo", ["m.gguf"], "", "")
        with patch.object(lm, "_repo_folder", return_value=Path(tempfile.mkdtemp())), \
                patch.object(lm, "_client", side_effect=lm.LocalModelError("Репозиторий не найден", "Repository not found")):
            lm._run_download(job)
        public = job.public()
        self.assertEqual((public["error"], public["error_en"]), ("Репозиторий не найден", "Repository not found"))
        with patch.dict(lm._install), patch.object(lm, "_release_assets", side_effect=lm.LocalModelError(
                "Не удалось узнать последнюю сборку llama.cpp на GitHub", "Could not find the latest llama.cpp build on GitHub")):
            lm._run_install("cpu")
            status = lm.runtime_status()["install"]
        self.assertEqual(status["state"], "error")
        self.assertEqual(status["error_en"], "Could not find the latest llama.cpp build on GitHub")


class LocalProviderLanguageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.patches = [patch.object(lm, "DATA_DIR", str(self.data)), patch.object(lm, "sync_catalog", lambda: None)]
        for item in self.patches:
            item.start()
        lm.pool.command_override = [sys.executable, str(ROOT / "tests" / "fake_llama_server.py")]

    def tearDown(self) -> None:
        lm.pool.stop_all()
        lm.pool.command_override = None
        for item in self.patches:
            item.stop()
        self.temp.cleanup()

    def _fail(self, model, messages=None):
        from app.providers.local import LocalProvider

        async def run():
            async for _ in LocalProvider().chat_stream(None, model, messages or [{"role": "user", "content": "hi"}], {}):
                pass
        with self.assertRaises(Exception) as caught:
            asyncio.run(run())
        return caught.exception

    def test_provider_errors_are_english_with_a_russian_variant(self):
        from app.providers.base import ProviderModelUnavailableError, ProviderServerError
        exc = self._fail("nope")
        self.assertIsInstance(exc, ProviderModelUnavailableError)
        self.assertEqual(exc.message, "Local model “nope” is not installed")
        self.assertEqual(exc.message_ru, "Локальная модель «nope» не установлена")

        slug = lm.register_file(write_model(self.data))["id"]
        image = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}]}]
        exc = self._fail(slug, image)
        self.assertIsInstance(exc, ProviderServerError)
        self.assertIsNone(CYRILLIC.search(exc.message), exc.message)
        self.assertIn("mmproj", exc.message_ru)

        lm.update_model(slug, {"extra_args": "--crash"})
        exc = self._fail(slug)
        self.assertEqual(exc.status, 422)
        self.assertIn("llama-server exited with code", exc.message)
        self.assertIsNone(CYRILLIC.search(exc.message.split("\n\n")[0]), exc.message)
        self.assertIn("llama-server завершился", exc.message_ru)
        state = lm.get_model(slug)
        self.assertIn("exited with code", state["error_en"])
        self.assertIn("завершился", state["error"])


class ChatLanguageTests(unittest.TestCase):
    """The chat error of a local model that cannot start: Russian text Russian, English text English."""

    def _complete(self, provider_error):
        from app.chat import ChatService, CompletionError
        from app.config import config_store
        from app.routing import Resolved
        from app.state import Account

        account = Account("local", {}, label="Локальные модели")

        class Provider:
            id = "local"

            async def chat_stream(self, *_args, **_kwargs):
                raise provider_error
                yield {}

        async def run():
            service = ChatService()
            with patch.object(config_store.settings, "proxy_enabled", True), \
                    patch("app.chat.resolve", return_value=Resolved(None, "local", "tiny", "local")), \
                    patch.object(service, "_provider", return_value=Provider()), \
                    patch("app.chat.pool.ordered_candidates", return_value=[account]), \
                    patch("app.state.state.save_accounts"), patch("app.state.state.save_accounts_soon"):
                with self.assertRaises(CompletionError) as caught:
                    async for _chunk in service.stream_completion("local-tiny", [{"role": "user", "content": "x"}], {}):
                        pass
            return caught.exception
        return asyncio.run(run()), account

    def test_start_failure_reaches_the_chat_in_both_languages(self):
        from app.providers.base import ProviderServerError
        error = lm.LocalModelError("Движок llama.cpp не установлен. Откройте «Локальные модели» и нажмите «Установить движок».",
                                   "The llama.cpp engine is not installed. Open “Local models” and press “Install engine”.")
        exc, _ = self._complete(ProviderServerError(error.message_en, 422, message_ru=error.message))
        self.assertEqual(exc.status, 422)
        self.assertIn("Движок llama.cpp не установлен", exc.message)
        self.assertEqual(exc.message_en, error.message_en)

    def test_account_card_keeps_the_russian_wording(self):
        from app.pool import pool
        from app.providers.base import ProviderServerError
        from app.state import Account
        account = Account("local", {}, label="Локальные модели")
        with patch("app.state.state.save_accounts_soon"):
            pool.report_failure(account, ProviderServerError("The connection to llama-server broke off: x",
                                                             message_ru="Связь с llama-server оборвалась: x"))
        self.assertEqual(account.status_message, "Связь с llama-server оборвалась: x")

    def test_model_unavailable_and_retries_keep_each_language(self):
        from app.providers.base import ProviderModelUnavailableError, ProviderServerError
        exc, _ = self._complete(ProviderModelUnavailableError("Local model “tiny” is not installed", "tiny",
                                                              message_ru="Локальная модель «tiny» не установлена"))
        self.assertIn("Локальная модель «tiny» не установлена", exc.message)
        self.assertNotIn("Local model", exc.message)
        self.assertIn("Local model “tiny” is not installed", exc.message_en)
        self.assertIsNone(CYRILLIC.search(exc.message_en.replace("Локальные модели", "")), exc.message_en)
        with patch("app.chat.config_store.settings.retry_delay_ms", 0):
            exc, _ = self._complete(ProviderServerError("The connection to llama-server broke off: ReadError: x",
                                                        message_ru="Связь с llama-server оборвалась: ReadError: x"))
        self.assertIn("Связь с llama-server оборвалась", exc.message)
        self.assertIn("The connection to llama-server broke off", exc.message_en)
        self.assertNotIn("Связь", exc.message_en)

    def test_api_clients_get_english_and_the_russian_window_russian(self):
        from app import openai_api
        from app.chat import CompletionError
        from app.config import config_store
        from round27_proxy_code_test import _request

        def failing(*_args, **_kwargs):
            async def gen():
                raise CompletionError("Все аккаунты «local» недоступны (Локальные модели): Модель не успела загрузиться",
                                      status=502, message_en="Every “local” account failed (Локальные модели): "
                                                             "The model did not load in the allotted time")
                yield {}
            return gen()

        async def run(headers):
            with patch.object(config_store, "authorize_api_request", return_value=True), \
                    patch("app.chat.chat_service.stream_completion", failing):
                response = await openai_api.chat_completions(_request(
                    {"model": "local-tiny", "messages": [{"role": "user", "content": "hi"}]}, headers=headers))
            return json.loads(response.body)["error"]

        self.assertIn("did not load", asyncio.run(run({}))["message"])
        self.assertIn("не успела", asyncio.run(run({"X-MemuAI-Lang": "ru"}))["message"])


class PanelRouteTests(unittest.TestCase):
    """/api/admin/local/* answer with the exception's own English text (no lookup table)."""

    def test_routes_return_message_en(self):
        from app import proxy_tools_api
        self.assertNotIn("Репозиторий не найден", proxy_tools_api._EN)
        response = proxy_tools_api._local_error(lm.LocalModelError("Репозиторий не найден", "Repository not found"), 502)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(json.loads(response.body), {"error": "Репозиторий не найден", "error_en": "Repository not found"})
        dynamic = lm.LocalModelError("Hugging Face ответил HTTP 418", "Hugging Face answered HTTP 418")
        self.assertEqual(json.loads(proxy_tools_api._local_error(dynamic).body)["error_en"], "Hugging Face answered HTTP 418")
        # A ValueError of the settings route has one text only.
        self.assertEqual(json.loads(proxy_tools_api._local_error(ValueError("bad")).body)["error_en"], "bad")

    def test_update_route_end_to_end(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import proxy_tools_api
        app = FastAPI()
        app.include_router(proxy_tools_api.router)
        with patch.object(proxy_tools_api, "_auth", return_value=None), \
                patch.object(lm, "_load_catalog", return_value={"models": {}, "settings": {}, "runtime": {}}), \
                patch.object(lm, "_mutate", side_effect=lambda change: change({"models": {}, "settings": {}})):
            response = TestClient(app).post("/api/admin/local/models/missing", json={"name": "x"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "Модель не найдена", "error_en": "Model not found"})

    def test_local_tab_shows_the_english_error_fields(self):
        source = (ROOT / "app" / "static" / "local.js").read_text(encoding="utf-8")
        for field in ("install.error_en", "d.error_en", "m.error_en"):
            self.assertIn(field, source)


class ProxyOwnErrorLanguageTests(unittest.TestCase):
    """The Proxy's own errors (validation, routing, tools) follow the same scheme as provider errors:
    API clients read English, the MemuAI chat (``X-MemuAI-Lang: ru``) reads Russian."""

    def test_every_russian_api_error_has_an_english_text(self):
        for name in ("openai_api.py", "anthropic_api.py", "responses_api.py", "chat.py"):
            tree = ast.parse((ROOT / "app" / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if func not in ("_openai_error", "CompletionError", "_anthropic_error"):
                    continue
                source = ast.unparse(node)
                english = any(k.arg == "message_en" for k in node.keywords) or \
                    (func == "_anthropic_error" and len(node.args) >= 3)
                self.assertTrue(english or not CYRILLIC.search(source), f"{name}:{node.lineno} {source[:120]}")

    def _post(self, body, headers=None, raw=None):
        from app import openai_api
        from app.config import config_store
        from round27_proxy_code_test import _request
        request = _request(body, headers=headers)
        if raw is not None:
            async def receive():
                return {"type": "http.request", "body": raw, "more_body": False}
            request._receive = receive

        async def run():
            with patch.object(config_store, "authorize_api_request", return_value=True):
                return await openai_api.chat_completions(request)
        response = asyncio.run(run())
        return response.status_code, json.loads(response.body)["error"]

    def test_validation_errors_in_the_request_language(self):
        status, error = self._post({"model": "m", "messages": []})
        self.assertEqual(status, 400)
        self.assertEqual(error["message"], "'messages' is required and must not be empty")
        self.assertEqual(error["message_ru"], "Поле 'messages' обязательно и непустое")
        _, error = self._post({"model": "m", "messages": []}, {"X-MemuAI-Lang": "ru"})
        self.assertEqual(error["message"], "Поле 'messages' обязательно и непустое")
        _, error = self._post({"messages": [{"role": "user", "content": "x"}]})
        self.assertEqual(error["message"], "'model' is required")
        _, error = self._post({"model": "m", "messages": [{"role": "user", "content": "x"}], "stream": "yes"})
        self.assertEqual(error["message"], "'stream' must be a boolean")
        _, error = self._post({"model": "m", "messages": [{"role": "user", "content": "x"}], "tools": 5})
        self.assertEqual(error["message"], "'tools' must be an array of objects")
        _, error = self._post({}, raw=b"{oops")
        self.assertEqual(error["message"], "The request body must be valid JSON")

    def test_tool_provider_error_in_both_languages(self):
        with patch("app.openai_api.tool_provider_supported", return_value=False):
            _, english = self._post({"model": "m", "messages": [{"role": "user", "content": "x"}],
                                     "tools": [{"type": "function", "function": {"name": "f"}}]})
            _, russian = self._post({"model": "m", "messages": [{"role": "user", "content": "x"}],
                                     "tools": [{"type": "function", "function": {"name": "f"}}]}, {"X-MemuAI-Lang": "ru"})
        self.assertTrue(english["message"].startswith("Tool calls require"), english)
        self.assertTrue(russian["message"].startswith("Вызовы инструментов"), russian)

    def test_unroutable_model_reaches_api_clients_in_english(self):
        from app.chat import ChatService, CompletionError
        from app.config import config_store

        async def run():
            with patch.object(config_store.settings, "proxy_enabled", True), \
                    patch("app.chat.resolve", return_value=None):
                with self.assertRaises(CompletionError) as caught:
                    async for _chunk in ChatService().stream_completion("nope/xyz", [{"role": "user", "content": "x"}], {}):
                        pass
            return caught.exception
        exc = asyncio.run(run())
        self.assertIn("не маршрутизируется", exc.message)
        self.assertIsNone(CYRILLIC.search(exc.message_en), exc.message_en)


def _installer_browser_ok() -> bool:
    try:
        from tests.installer_layout_test import _playwright_ok
    except Exception:  # noqa: BLE001
        return False
    return _playwright_ok()


@unittest.skipUnless(_installer_browser_ok(), "Playwright/Chromium not available")
class UninstallKeepOffTests(unittest.TestCase):
    """Turning «Keep my data» off adds the red warning: the screen refits at once (the buttons
    slid below the panel on a small window) and the switch's subtitle never ends in a lone word."""

    def test_keepoff_refits_and_has_no_orphan(self):
        from tests.installer_layout_test import UI, chromium_path

        async def run():
            from playwright.async_api import async_playwright
            out = []
            async with async_playwright() as p:
                browser = await p.chromium.launch(executable_path=chromium_path(),
                                                  args=["--no-sandbox", "--hide-scrollbars", "--disable-webgl"])
                for w, h, lang in ((860, 540, "en"), (960, 620, "en"), (960, 620, "ru"), (1280, 800, "en")):
                    page = await browser.new_page(viewport={"width": w, "height": h})
                    await page.goto(f"{UI.as_uri()}?mock=1&shot=1&mode=uninstall&lang={lang}", wait_until="load")
                    await page.wait_for_timeout(700)
                    # No __memuFit() call here: the change handler alone must refit the screen.
                    out.append(await page.evaluate("""(() => {
                      const keep = document.getElementById('opt-keep');
                      keep.checked = false; keep.dispatchEvent(new Event('change'));
                      const panel = document.getElementById('panel').getBoundingClientRect();
                      const go = document.getElementById('btn-un-go').getBoundingClientRect();
                      const small = document.querySelector('#row-keep .row-text small');
                      let lastLine = '';
                      if (small && small.offsetHeight) {
                        const range = document.createRange(); range.selectNodeContents(small);
                        const rects = Array.from(range.getClientRects());
                        const bottom = Math.max(...rects.map(r => r.bottom));
                        lastLine = rects.filter(r => r.bottom === bottom).map(r => r.width).reduce((a, b) => a + b, 0);
                      }
                      return { size: innerWidth + 'x' + innerHeight, go: go.bottom, panel: panel.bottom,
                               lastLine, smallWidth: small ? small.getBoundingClientRect().width : 0,
                               wrap: small ? getComputedStyle(small).textWrap || getComputedStyle(small).textWrapStyle : '' };
                    })()"""))
                    await page.close()
                await browser.close()
            return out

        for row in asyncio.run(run()):
            self.assertLessEqual(row["go"], row["panel"] - 4, row)
            self.assertIn("pretty", row["wrap"], row)
            if row["lastLine"]:
                # a lone «off.» is ~20 px of a ~350 px line
                self.assertGreater(row["lastLine"], row["smallWidth"] * 0.12, row)


if __name__ == "__main__":
    unittest.main()
