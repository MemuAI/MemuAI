"""Round 26 (r26-tgapp): Telegram Mini App and the Telegram settings in the MemuAI window.

* «Test connection» / «Connect Telegram» reach api.telegram.org like the bot does (proxy-aware
  client), not through the window's loopback-only client;
* modules.toggle with a bad id is a 404, not an internal error;
* a Proxy that shuts down leaves a stale status, so the window says «Proxy is not running» at once;
* the window's «Messages in AI context» select keeps a value set in the Mini App (30, 70…);
* the Mini App panel in the window: indented custom-address block, pretty selects, no redraw
  (and no closed picker) on every 3-second poll;
* the Mini App re-reads settings changed on the computer while it stays open, and its hints
  point to Settings → Telegram.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-r26-tgapp-")

import httpx  # noqa: E402

from app import tg_webapp  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PG = ROOT / "app" / "pgstatic"
TGAPP = ROOT / "app" / "tgapp"
TOKEN = "123456:R26-token_for-mini-app"
USER = 4343

def init_data(user_id: int = USER, **extra) -> str:
    fields = {"auth_date": int(time.time()), "query_id": "AAH-r26",
              "user": {"id": user_id, "first_name": "Yono", "language_code": "ru"}}
    fields.update(extra)
    return tg_webapp.sign_init_data(fields, TOKEN)

def _chromium() -> str:
    for candidate in (os.environ.get("MEMU_TEST_CHROMIUM", ""), "/usr/bin/chromium",
                      shutil.which("chromium") or "", "/workspace/work/chromium/chrome",
                      r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                      r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"):
        if candidate and os.path.exists(candidate):
            return candidate
    return ""

try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover - optional dependency
    sync_playwright = None

def _browser(play):
    path = _chromium()
    if not path:
        raise unittest.SkipTest("chromium missing")
    return play.chromium.launch(executable_path=path or None, args=["--no-sandbox"])

class TelegramSettingsNetworkTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from app import pgdata, telegram_settings_api
        pgdata.save_preferences({"telegram_bot_token": "123456789:AAH-r26_window_token_abcdef",
                                 "telegram_chat_id": "5150", "telegram_enabled": True})
        telegram_settings_api._bot_cache.update(fp="", at=0.0, bot={}, error="")
        self.clients = []

    async def asyncTearDown(self) -> None:
        for client in self.clients:
            await client.aclose()

    def client(self, handler) -> httpx.AsyncClient:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.clients.append(client)
        return client

    async def test_telegram_calls_do_not_use_the_loopback_client(self) -> None:
        from fastapi import FastAPI
        from app import telegram_settings_api

        def loopback(request):

            raise AssertionError("api.telegram.org was called through the loopback client: " + str(request.url))

        seen = []

        def telegram(request):
            seen.append(request.url.path.rsplit("/", 1)[-1])
            if request.url.path.endswith("/getMe"):
                return httpx.Response(200, json={"ok": True, "result": {"id": 1, "username": "r26_bot",
                                                                      "first_name": "R26"}})
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

        app = FastAPI()
        telegram_settings_api.register_telegram_routes(app, self.client(loopback), proxy_url=None,
                                                       telegram_client=self.client(telegram))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://pg") as http:
            tested = (await http.post("/api/pg/telegram/test", json={})).json()
            paired = (await http.post("/api/pg/telegram/pair", json={})).json()
            status = (await http.get("/api/pg/telegram/status")).json()
        self.assertTrue(tested.get("ok"), tested)
        self.assertTrue(tested.get("sent"), tested)
        self.assertTrue(paired.get("ok"), paired)
        self.assertEqual(status["bot"]["username"], "r26_bot")
        self.assertIn("getMe", seen)
        self.assertIn("sendMessage", seen)

    async def test_default_telegram_client_honours_the_system_proxy(self) -> None:
        from app import telegram_settings_api
        async with telegram_settings_api._telegram_http(None) as client:
            self.assertTrue(client.trust_env)
        shared = self.client(lambda request: httpx.Response(200))
        async with telegram_settings_api._telegram_http(shared) as client:
            self.assertIs(client, shared)

class ModulesToggleTest(unittest.TestCase):
    def test_bad_module_id_is_not_found_not_internal(self) -> None:
        from fastapi.testclient import TestClient
        from app.pgdata import save_preferences
        from app.tgapp_api import create_tgapp
        save_preferences({"telegram_bot_token": TOKEN, "telegram_chat_id": str(USER)})
        tg_webapp.save_config({"enabled": True, "mode": "cloudflare"})
        try:
            with TestClient(create_tgapp()) as client:
                token = client.post("/tgapp/api/auth", json={"init_data": init_data()}).json()["token"]
                headers = {"Authorization": "Bearer " + token}
                for bad in ("", "5", "../x"):
                    response = client.post("/tgapp/api/modules.toggle", json={"id": bad, "enabled": True},
                                           headers=headers)
                    self.assertEqual(response.status_code, 404, (bad, response.text))
                    self.assertEqual(response.json()["code"], "not_found")
        finally:
            tg_webapp.save_config({"enabled": False})

class ManagerShutdownTest(unittest.TestCase):
    def test_window_sees_proxy_offline_right_after_shutdown(self) -> None:
        tg_webapp.save_config({"enabled": False, "mode": "cloudflare"})

        async def run():
            task = asyncio.ensure_future(tg_webapp.run_manager(poll=0.05))
            await asyncio.sleep(0.3)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(run())
        self.assertIsNone(tg_webapp.manager())
        data = json.loads(tg_webapp.status_path().read_text(encoding="utf-8"))
        self.assertEqual(data["state"], "off")

        self.assertEqual(tg_webapp.read_status()["state"], "proxy_offline")

@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TelegramSettingsHtmlTest(unittest.TestCase):
    def render(self, history) -> str:
        script = """
const fs = require("fs"), vm = require("vm");
const window = {}; const document = { getElementById: () => null, querySelectorAll: () => [] };
vm.runInNewContext(fs.readFileSync(process.argv[1], "utf8"), { window, document, console, setTimeout, clearTimeout });
const prefs = { telegram_history_messages: JSON.parse(process.argv[2]) };
const esc = (v) => String(v == null ? "" : v);
process.stdout.write(window.MemuTelegramSettings.html({ en: false, esc, models: [], pref: (k, d) => (k in prefs ? prefs[k] : d) }));
"""
        out = subprocess.run(["node", "-e", script, str(PG / "telegram-settings.js"), json.dumps(history)],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        start = out.stdout.index('id="st-tg-history"')
        return out.stdout[start:out.stdout.index("</select>", start)]

    def test_value_from_the_mini_app_is_shown(self) -> None:
        for value in (30, 70, 10):
            select = self.render(value)
            self.assertIn(f'<option value="{value}" selected>', select)
        select = self.render(60)
        self.assertIn('<option value="60" selected>', select)
        self.assertEqual(select.count("<option"), 4)

@unittest.skipUnless(sync_playwright is not None, "playwright is not installed")
class WindowPanelsBrowserTest(unittest.TestCase):
    """telegram-settings.js syncFromPrefs and tgapp-settings.js in a real browser (stubbed API)."""

    def test_sync_keeps_a_non_standard_history_value(self) -> None:
        html = """<!doctype html><html><body><div id="host"></div>
<script src="telegram-settings.js"></script><script>
const prefs = { telegram_history_messages: 60 };
document.getElementById("host").innerHTML = MemuTelegramSettings.html({ en: false, esc: (v) => String(v == null ? "" : v), models: [], pref: (k, d) => (k in prefs ? prefs[k] : d) });
MemuTelegramSettings.syncFromPrefs({ telegram_history_messages: 30 }, new Set(["telegram_history_messages"]));
window.result = document.getElementById("st-tg-history").value;
</script></body></html>"""
        with tempfile.TemporaryDirectory() as folder:
            shutil.copy(PG / "telegram-settings.js", folder)
            page_file = Path(folder) / "page.html"
            page_file.write_text(html, encoding="utf-8")
            with sync_playwright() as play:
                browser = _browser(play)
                page = browser.new_page()
                page.goto(page_file.as_uri())
                self.assertEqual(page.evaluate("window.result"), "30")
                browser.close()

    def test_mini_app_panel_is_enhanced_indented_and_not_redrawn_on_every_poll(self) -> None:
        html = """<!doctype html><html><body><div class="sx-shell"><div id="pane-remote" class="active"><div id="tg-settings"></div></div></div>
<script>
window.enhanced = 0;
window.enhanceSelects = (root) => { window.enhanced++; root.querySelectorAll("select").forEach((s) => s.dataset.pretty = "1"); };
window.beat = 1;
window.fetch = async () => ({ ok: true, json: async () => ({
  config: { enabled: true, mode: "custom", custom_url: "https://memu.example.com", port: 8641, max_age_hours: 24, code_control: true },
  status: { state: "online", url: "https://memu.example.com/tgapp/", port: 8641, heartbeat: window.beat++, verified: true, download: {} },
  problems: [], cloudflared: false }) });
</script>
<script src="tgapp-settings.js"></script></body></html>"""
        with tempfile.TemporaryDirectory() as folder:
            shutil.copy(PG / "tgapp-settings.js", folder)
            page_file = Path(folder) / "page.html"
            page_file.write_text(html, encoding="utf-8")
            with sync_playwright() as play:
                browser = _browser(play)
                page = browser.new_page(viewport={"width": 900, "height": 900})
                page.goto(page_file.as_uri())
                page.wait_for_selector("#tgapp-settings .tga-card #tga-age")
                page.wait_for_timeout(300)
                self.assertEqual(page.get_attribute("#tga-age", "data-pretty"), "1")
                padding = page.evaluate("getComputedStyle(document.querySelector('#tgapp-settings .tga-custom')).paddingLeft")
                self.assertEqual(padding, "18px")
                page.evaluate("window.marker = document.querySelector('#tgapp-settings .tga-card')")
                renders = page.evaluate("window.enhanced")
                page.wait_for_timeout(3600)
                self.assertTrue(page.evaluate("window.beat > 2"))
                self.assertTrue(page.evaluate("window.marker === document.querySelector('#tgapp-settings .tga-card')"))
                self.assertEqual(page.evaluate("window.enhanced"), renders)
                browser.close()

class MiniAppHintsTest(unittest.TestCase):
    def test_hints_point_to_the_telegram_settings_page(self) -> None:
        source = (TGAPP / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("Интеграции → Telegram", source)
        self.assertNotIn("Integrations → Telegram", source)
        self.assertIn("Настройки → Telegram", source)
        self.assertIn("Settings → Telegram", source)

@unittest.skipUnless(sync_playwright is not None, "playwright is not installed")
class MiniAppSettingsRefreshBrowserTest(unittest.TestCase):
    """A setting changed on the computer shows up in the open Mini App (before: only after a restart)."""

    def test_language_changed_on_the_computer_is_picked_up(self) -> None:
        import uvicorn
        from app import tgapp_chat
        from app.pgdata import save_preferences
        from app.tgapp_api import create_tgapp
        save_preferences({"telegram_bot_token": TOKEN, "telegram_chat_id": str(USER), "language": "ru",
                          "telegram_notify_mode": "off"})
        tg_webapp.save_config({"enabled": True, "mode": "cloudflare"})
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        models = mock.patch.object(tgapp_chat, "chat_models", return_value=[
            {"id": "test/model", "name": "Test", "provider": "test", "vision": False, "pinned": False, "default": True}])
        models.start()
        server = uvicorn.Server(uvicorn.Config(create_tgapp(), host="127.0.0.1", port=port, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.05)
            hash_part = urllib.parse.urlencode({"tgWebAppData": init_data(), "tgWebAppVersion": "8.0",
                                                "tgWebAppPlatform": "android",
                                                "tgWebAppThemeParams": json.dumps({"bg_color": "#17212b"})})
            with sync_playwright() as play:
                browser = _browser(play)
                page = browser.new_page(viewport={"width": 390, "height": 844})
                page.goto(f"http://127.0.0.1:{port}/tgapp/?tab=settings#{hash_part}")
                page.wait_for_selector("#app:not([hidden]) .tab", timeout=15000)
                self.assertIn("Настройки", page.inner_text("#tabbar"))
                save_preferences({"language": "en", "telegram_notify_mode": "sound"})
                page.wait_for_timeout(3200)
                page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
                for _ in range(80):
                    if "Settings" in page.inner_text("#tabbar"):
                        break
                    page.wait_for_timeout(100)
                self.assertIn("Settings", page.inner_text("#tabbar"))
                self.assertIn("With sound", page.inner_text("#views"))
                browser.close()
        finally:
            server.should_exit = True
            thread.join(5)
            models.stop()
            save_preferences({"language": "ru", "telegram_notify_mode": "off"})
            tg_webapp.save_config({"enabled": False})

if __name__ == "__main__":
    unittest.main()
