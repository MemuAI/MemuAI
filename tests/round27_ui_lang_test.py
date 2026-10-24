"""Round 27 (r27-hunt-ui): language and look of the interfaces.

* the language scan (``tests/ui_lang_scan.py``) of the English UI finds no Cyrillic anywhere — MemuAI,
  MemuAI Proxy, the Telegram Mini App and the installer (skipped without headless Chromium);
* Proxy «Uptime» units follow the language (``fmtDuration`` printed «1m 59s» in the Russian panel);
* the DFY vault note has an English twin (``note_en``);
* spell check: every unavailable reason has ``reason_en``, an empty word is not a success;
* every built-in internal prompt has a Russian name (``name_ru``);
* Mini App: changing the language in its settings moves the panels' language too; the panel list speaks
  the Mini App's language (``language``), not the chat bot's (``telegram_language``, «ru» by default);
  API errors come in the Mini App's language («Память этого профиля меняется на компьютере» reached
  the English Mini App).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-r27-uilang-")

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "123456:R27-ui-lang_token-abc"
USER = 4747


def _chromium() -> str:
    for candidate in (os.environ.get("MEMU_TEST_CHROMIUM", ""), "/workspace/work/chromium/chrome",
                      "/usr/bin/chromium", shutil.which("chromium") or ""):
        if candidate and os.path.exists(candidate):
            return candidate
    return ""


def _has_playwright() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except Exception:
        return False
    return True


@unittest.skipUnless(_chromium() and _has_playwright(), "headless Chromium / playwright is not available")
class EnglishScanTest(unittest.TestCase):
    """The whole English UI, opened screen by screen in real servers: no Cyrillic letter is left."""

    def test_english_ui_has_no_cyrillic(self) -> None:
        with tempfile.TemporaryDirectory(prefix="memuai-r27-scan-") as tmp:
            out = Path(tmp) / "scan.json"
            env = {**os.environ, "MEMUAI_DATA_DIR": str(Path(tmp) / "data"), "MEMU_TEST_CHROMIUM": _chromium(),
                   "NO_PROXY": "127.0.0.1,localhost"}
            proc = subprocess.run([sys.executable, str(ROOT / "tests" / "ui_lang_scan.py"), "--lang", "en",
                                   "--json", str(out)], cwd=ROOT, env=env, capture_output=True, text=True,
                                  timeout=900)
            self.assertTrue(out.exists(), proc.stdout[-2000:] + proc.stderr[-2000:])
            report = json.loads(out.read_text(encoding="utf-8"))
        findings = [f"{f['product']} {f['state']} {f['where']}: {f['text']}" for f in report["findings"]]
        self.assertEqual(findings, [], "\n".join(findings))
        # the scan itself must have reached every screen: a page error is a broken screen
        self.assertEqual(report["errors"], [])


@unittest.skipUnless(_chromium() and _has_playwright(), "headless Chromium / playwright is not available")
class StartScreenFitsTest(unittest.TestCase):
    """MemuCode / Work start screens at 1360×820: the greeting shrinks and scrolls inside the chat. The panels'
    «keep the greeting whole» rule matched there too (the grid is hidden by CSS, not [hidden]) — the whole
    view was pushed up and MemuCode's heading was cut off at the top."""

    def test_code_and_work_start_screens_fit(self) -> None:
        import httpx
        sys.path.insert(0, str(ROOT / "tests"))
        from ui_lang_scan import Servers
        from playwright.sync_api import sync_playwright
        servers = Servers().start()
        try:
            httpx.post(servers.pg + "/api/pg/preferences", json={"profile_onboarded": True, "profile_name": "Alex",
                                                                  "language": "ru"}, trust_env=False, timeout=20)
            with sync_playwright() as play:
                browser = play.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])
                page = browser.new_page(viewport={"width": 1360, "height": 820})
                page.goto(servers.pg + "/")
                page.wait_for_selector("body.boot-ready", timeout=90000)
                for nav in ("nav-code", "nav-work"):
                    page.click("#" + nav)
                    page.wait_for_timeout(1000)
                    page.keyboard.press("Escape")       # the first-run intro
                    page.wait_for_timeout(300)
                    state = page.evaluate("""() => { const main = document.querySelector('.main-content');
                      const chat = document.getElementById('chat').getBoundingClientRect();
                      const head = document.querySelector('#greeting .greet-head').getBoundingClientRect();
                      return {scroll: main.scrollTop, chatTop: chat.top, headTop: head.top}; }""")
                    self.assertEqual(state["scroll"], 0, (nav, state))
                    self.assertGreaterEqual(state["headTop"] + 1, state["chatTop"], (nav, state))
                browser.close()
        finally:
            servers.stop()


def _node(script: str) -> str:
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("node is not available")
    return subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30, check=True).stdout


def _js_function(path: Path, name: str) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.index(f"function {name}(")
    depth, i = 0, text.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
        if depth == 0:
            return text[start:i]


class ProxyDurationTest(unittest.TestCase):
    def test_uptime_units_follow_language(self) -> None:
        i18n = (ROOT / "app" / "static" / "i18n.js").read_text(encoding="utf-8")
        import re
        forms = re.findall(r'"metrics\.dur":\s*"([^"]+)"', i18n)
        self.assertEqual(len(forms), 2, "metrics.dur must exist in ru and en")
        fn = _js_function(ROOT / "app" / "static" / "app.js", "fmtDuration")
        script = f"""
        const FORMS = {json.dumps(forms)}; let LANG = 0;
        const t = (key) => FORMS[LANG];
        {fn}
        const out = [];
        for (LANG of [0, 1]) out.push([fmtDuration(119), fmtDuration(3 * 3600 + 120), fmtDuration(2 * 86400 + 3600), fmtDuration(-5)]);
        console.log(JSON.stringify(out));"""
        ru, en = json.loads(_node(script))
        self.assertEqual(ru, ["1 мин 59 с", "3 ч 2 мин", "2 д 1 ч", "0 с"])
        self.assertEqual(en, ["1m 59s", "3h 2m", "2d 1h", "0s"])


class DfyVaultNoteTest(unittest.TestCase):
    def test_note_has_english_twin(self) -> None:
        from app import dfy_vault
        vault = dfy_vault.SecretVault.__new__(dfy_vault.SecretVault)
        for dpapi in (True, False):
            vault.use_dpapi = dpapi
            info = vault.info()
            self.assertRegex(info["note"], "[А-Яа-я]")
            self.assertTrue(info["note_en"])
            self.assertNotRegex(info["note_en"], "[А-Яа-яЁё]")
        script = (ROOT / "app" / "pgstatic" / "dfy-settings.js").read_text(encoding="utf-8")
        self.assertIn("note_en", script)


class SpellcheckLanguageTest(unittest.TestCase):
    def test_unavailable_reasons_have_english(self) -> None:
        from app import spellcheck
        with mock.patch.object(spellcheck, "IS_WINDOWS", False):
            for result in (spellcheck.spell_status(), spellcheck.check_text("тест"), spellcheck.add_word("тест")):
                self.assertFalse(result["available"])
                self.assertRegex(result["reason"], "[А-Яа-я]")
                self.assertNotRegex(result["reason_en"], "[А-Яа-яЁё]")

    def test_empty_word_is_not_success(self) -> None:
        from app import spellcheck
        with mock.patch.object(spellcheck, "IS_WINDOWS", True):
            result = spellcheck.add_word("   ")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason_en"], "Empty word")


class TunnelStatusEnglishTest(unittest.TestCase):
    """The Mini App tunnel status («cloudflared не скачан: …») reached the English window in Russian."""

    def test_every_status_text_has_english_twin(self) -> None:
        import re
        from app import tg_webapp
        composed = [
            "cloudflared не скачан: Автоматическая загрузка cloudflared есть только в Windows; установите cloudflared вручную",
            "cloudflared завершился (код 1) · 5 попыток подряд — нажмите «Повторить»",
            "Туннель не подключился к Cloudflare за 30 с — пробую HTTP/2; повтор через 10 с",
            "Проверка через интернет не прошла (Не открывается через интернет: ConnectError) — адрес всё равно отправлен в Telegram",
            "Адрес туннеля так и не появился в DNS — запрашиваю новый", "Не удалось открыть порт 8641",
            "Адрес отвечает, но не MemuAI (HTTP 502)", "Проверка через интернет не прошла: Адрес ещё не появился в DNS — жду",
        ]
        for line in ("ERR invalid tunnel secret", "ERR config.yaml", "429 Too Many Requests error",
                     "ERR failed to request quick tunnel", "ERR quic failed to dial", "ERR no such host",
                     "ERR tls handshake timeout", "ERR i/o timeout"):
            composed.append(tg_webapp._tunnel_error_hint(line) + "; повтор через 5 с")
        for text in composed:
            self.assertRegex(text, "[А-Яа-я]")
            self.assertNotRegex(tg_webapp.status_en(text), "[А-Яа-яЁё«»]", text)
        self.assertEqual(tg_webapp.status_en("ERR failed to dial: timeout"), "ERR failed to dial: timeout")
        # every Russian literal of the tunnel's status code paths is covered by the table
        source = (ROOT / "app" / "tg_webapp.py").read_text(encoding="utf-8")
        self.assertIn('"error_en": status_en(self.error)', source)
        script = (ROOT / "app" / "pgstatic" / "tgapp-settings.js").read_text(encoding="utf-8")
        self.assertNotRegex(script, r"esc\(status\.(error|detail)\)")
        self.assertTrue(re.search(r'statusText\(status, "error"\)', script))


class NetworkErrorTextTest(unittest.TestCase):
    """An account card showed «сеть недоступна: ConnectionError: Failed to perform, curl: (7) … See
    https://curl.se/libcurl/c/libcurl-errors.html first for more details.» — now only the curl sentence."""

    def test_curl_noise_is_trimmed(self) -> None:
        from app.providers import http
        raw = ConnectionError("Failed to perform, curl: (7) Failed to connect to 127.0.0.1:38583 after 114 ms: "
                              "Could not connect to server. See https://curl.se/libcurl/c/libcurl-errors.html "
                              "first for more details.")
        self.assertEqual(http.network_error_text(raw), "Failed to connect to 127.0.0.1:38583 after 114 ms: "
                                                       "Could not connect to server")
        self.assertEqual(http.network_error_text(TimeoutError()), "TimeoutError")

    def test_custom_check_uses_short_text(self) -> None:
        import asyncio
        from app.providers import custom, http

        class Account:
            id, fields, proxy = "a1", {"base_url": "http://127.0.0.1:9/v1", "api_key": "k"}, ""

        provider = custom.CustomProvider()
        with mock.patch.object(http, "get_session", side_effect=ConnectionError(
                "Failed to perform, curl: (7) Could not connect. See https://curl.se/libcurl/c/x.html for more")):
            ok, message = asyncio.run(provider.check(Account()))
        self.assertIsNone(ok)
        self.assertEqual(message, "сеть недоступна: Could not connect")


class VoiceLanguagesRussianTest(unittest.TestCase):
    """The voice model cards showed «◎ English · US», «99+ languages» and «〽 Streaming» in the Russian UI."""

    def test_every_catalog_language_has_russian_name(self) -> None:
        from app import voice_runtime
        for model in voice_runtime.STT_MODELS + voice_runtime.TTS_MODELS:
            self.assertIn(model["languages"], voice_runtime.LANGUAGES_RU, model["id"])
            self.assertRegex(model["languages_ru"], r"[а-яё]", model["id"])

    def test_cards_use_russian_languages(self) -> None:
        source = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("${esc(m.languages)}", source)
        self.assertNotIn("<span>〽 Streaming</span>", source)


class InternalPromptNamesTest(unittest.TestCase):
    def test_every_internal_prompt_has_russian_name(self) -> None:
        from app import pgdata
        for row in pgdata.INTERNAL_PROMPTS:
            self.assertTrue(row.get("name"), row["id"])
            self.assertRegex(row.get("name_ru") or "", "[А-Яа-я]", row["id"])
        self.assertTrue(all(item.get("name_ru") for item in pgdata.builtin_prompt_set()["internal_prompts"]))


class MiniAppLanguageTest(unittest.TestCase):
    def setUp(self) -> None:
        from fastapi.testclient import TestClient
        from app import pgdata, tg_webapp
        from app.tgapp_api import create_tgapp
        self.tg_webapp = tg_webapp
        pgdata.save_preferences({"telegram_bot_token": TOKEN, "telegram_chat_id": str(USER), "language": "ru",
                                 "telegram_language": "ru"})
        tg_webapp.save_config({"enabled": True, "mode": "cloudflare"})
        self.client = TestClient(create_tgapp()).__enter__()
        init = tg_webapp.sign_init_data({"auth_date": int(time.time()), "query_id": "AAH-r27",
                                         "user": {"id": USER, "first_name": "Yono", "language_code": "en"}}, TOKEN)
        token = self.client.post("/tgapp/api/auth", json={"init_data": init}).json()["token"]
        self.headers = {"Authorization": "Bearer " + token}

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self.tg_webapp.save_config({"enabled": False})

    def api(self, name: str, body: dict | None = None):
        return self.client.post(f"/tgapp/api/{name}", json=body or {}, headers=self.headers)

    def test_language_switch_moves_panels_and_list(self) -> None:
        from app import panels
        panels.update_settings({"lang": "ru"})
        self.assertEqual(self.api("settings.save", {"values": {"language": "en"}}).status_code, 200)
        self.assertEqual(panels.load()["settings"]["lang"], "en")
        # the chat bot stays Russian (telegram_language) — the Mini App list follows the Mini App
        rows = self.api("panels.list").json()["panels"]
        weather = [row for row in rows if row["kind"] == "weather"]
        self.assertTrue(weather)
        self.assertEqual(weather[0]["title"], "Weather")
        self.assertNotRegex(json.dumps(weather[0]["card"], ensure_ascii=False), "Укажите город|Погода")
        self.api("settings.save", {"values": {"language": "ru"}})
        self.assertEqual(self.api("panels.list").json()["panels"][0]["title"], "Погода")

    def test_api_errors_follow_language(self) -> None:
        from app import pgdata
        for lang, unknown, bad in (("ru", "Неизвестная операция", "Модуль не найден"),
                                   ("en", "Unknown operation", "Module not found")):
            pgdata.save_preferences({"language": lang})
            self.assertEqual(self.api("no.such.op").json()["error"], unknown)
            self.assertEqual(self.api("modules.toggle", {"id": "../x", "enabled": True}).json()["error"], bad)
            error = self.api("settings.save", {"values": {"default_model": 5}}).json()["error"]
            self.assertEqual(bool(__import__("re").search("[А-Яа-я]", error)), lang == "ru", error)
            error = self.api("panels.refresh", {"id": "p_missing"}).json()["error"]
            self.assertEqual(error, "Панель не найдена" if lang == "ru" else "Panel not found")

    def test_every_russian_api_error_has_english(self) -> None:
        """Static guard: a Russian literal in ApiError(...) needs its English twin (or a _t(...) call)."""
        import re
        for path in (ROOT / "app" / "tgapp_api.py", ROOT / "app" / "telegram_panels.py"):
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r"ApiError\((\d+),\s*([^,]+),\s*(f?\"[^\"]*\")\s*\)", text):
                self.assertNotRegex(match.group(3), "[А-Яа-я]", f"{path.name}: {match.group(0)}")


if __name__ == "__main__":
    unittest.main()
