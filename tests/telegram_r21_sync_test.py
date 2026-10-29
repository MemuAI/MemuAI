"""Round 21: settings sync (Mini App / bot → desktop), Mini App tunnel
hardening (timeouts, HTTP/2 fallback, retry cap, named tunnel, DNS/health
check before publishing) and the bot's menu-button propagation."""
import asyncio
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import httpx

from app import pgdata, tg_webapp, telegram_bot

TOKEN = "123456789:AAH" + "x" * 32
OWNER = 305551234
NAMED_TOKEN = "eyJ" + "a" * 120

class DataCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(pgdata, "MEMU_DIR", Path(self.tmp.name))
        self.patch.start()
        pgdata.save_preferences({"telegram_enabled": True, "telegram_bot_token": TOKEN,
                                 "telegram_chat_id": str(OWNER)})

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

class PreferencesSyncTest(DataCase):
    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import playground_api
        app = FastAPI()

        route = next(r for r in playground_api.create_app().routes
                     if getattr(r, "path", "") == "/api/pg/preferences/sync")
        app.router.routes.append(route)
        return TestClient(app)

    def test_revision_changes_when_another_process_saves(self):
        before = pgdata.preferences_revision()
        pgdata.save_preferences({"ai_personality": "pragmatic"})
        self.assertNotEqual(before, pgdata.preferences_revision())

    def test_sync_endpoint_reports_unchanged_and_changes(self):
        with mock.patch.dict(os.environ, {"MEMUAI_PROXY_URL": "http://127.0.0.1:9"}):
            http = self.client()
        first = http.get("/api/pg/preferences/sync").json()
        self.assertIn("preferences", first)
        same = http.get("/api/pg/preferences/sync", params={"rev": first["rev"]}).json()
        self.assertTrue(same["unchanged"])

        from app.tgapp_api import validate_settings
        pgdata.save_preferences(validate_settings({"ai_personality": "pragmatic"}, pgdata.load_preferences()))
        changed = http.get("/api/pg/preferences/sync", params={"rev": first["rev"]}).json()
        self.assertEqual(changed["preferences"]["ai_personality"], "pragmatic")
        self.assertIn("profiles_rev", changed)

    def test_patch_save_keeps_what_another_process_saved(self):

        pgdata.save_preferences({"ai_personality": "pragmatic"})
        pgdata.save_preferences({"sidebar_visible": False})
        prefs = pgdata.load_preferences()
        self.assertEqual(prefs["ai_personality"], "pragmatic")

    def test_bot_setting_round_trip(self):
        patch = telegram_bot._setting_patch("notify", "sound")
        pgdata.save_preferences(patch)
        prefs = pgdata.load_preferences()
        self.assertEqual(prefs["telegram_notify_mode"], "sound")
        self.assertTrue(prefs["telegram_notify"])
        self.assertEqual(telegram_bot._setting_value(prefs, "notify"), "sound")
        pgdata.save_preferences(telegram_bot._setting_patch("history", "100"))
        self.assertEqual(pgdata.load_preferences()["telegram_history_messages"], 100)

    def test_mini_app_profile_name_updates_the_desktop_profile(self):
        from app import tgapp_api
        before = pgdata.profiles_revision()
        tgapp_api._sync_active_profile({"profile_name": "Йоно", "profile_bio": "dev"})
        state = pgdata.load_profiles()
        active = next(i for i in state["items"] if i["id"] == state["active_id"])
        self.assertEqual(active["name"], "Йоно")
        self.assertEqual(active["bio"], "dev")
        self.assertNotEqual(before, pgdata.profiles_revision())
        self.assertEqual(tgapp_api.public_settings(pgdata.load_preferences())["profile_name"], "Йоно")

class DesktopSourceContractTest(unittest.TestCase):
    """The window side is JavaScript; check the contract in the source."""

    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1] / "app" / "pgstatic"
        cls.app = (root / "app.js").read_text(encoding="utf-8")
        cls.tg = (root / "telegram-settings.js").read_text(encoding="utf-8")

    def test_desktop_posts_only_changed_keys(self):
        self.assertRegex(self.app, r'api\("/api/pg/preferences", \{ method: "POST", body: patch(?:,|\s*\})')
        self.assertNotIn('body: snapshot', self.app)

    def test_desktop_polls_preferences_and_profiles(self):
        self.assertIn("setInterval(syncRemotePreferences, 3000)", self.app)
        self.assertIn("/api/pg/preferences/sync", self.app)
        self.assertIn("function syncRemoteProfiles", self.app)
        self.assertIn("applyRemotePreferenceChanges", self.app)

    def test_remote_chat_deletes_and_metadata_are_merged(self):
        self.assertIn("mergeRemoteChats(remote, profileId, true)", self.app)
        self.assertIn("remoteChanged", self.app)

    def test_telegram_panel_saves_single_keys_and_syncs(self):
        self.assertIn("syncFromPrefs", self.tg)
        self.assertIn('"st-tg-language": ["telegram_language"]', self.tg)
        self.assertIn("/api/pg/telegram/restart", self.tg)

class FakeProcess:
    def __init__(self, lines, hold=None):
        self.stdout = _Stream(lines, hold)
        self.terminated = False
        self.returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = self.returncode if self.returncode is not None else 0
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15
        self.stdout.release()

    def kill(self):
        self.terminate()

class _Stream:
    def __init__(self, lines, hold):
        self.lines = [line + "\n" for line in lines]
        self.hold = hold
        self.event = threading.Event()

    def release(self):
        self.event.set()

    def readline(self):
        if self.lines:
            return self.lines.pop(0)
        if self.hold:
            self.event.wait(5)
        return ""

URL_LINE = "INF |  https://fresh-host-name.trycloudflare.com  |"
REG_LINE = "INF Registered tunnel connection connIndex=0 protocol=http2"

class TunnelTest(unittest.TestCase):
    def run_tunnel(self, scripts, until=None, **kwargs):
        """Run a tunnel over scripted processes ([(lines, hold)] per launch)
        until ``until(tunnel, states, launched)``; returns a snapshot."""
        async def scenario():
            launched, states = [], []
            pending = list(scripts)

            def popen(cmd, **popen_kwargs):
                launched.append((cmd, popen_kwargs))
                lines, hold = pending.pop(0) if pending else ([], True)
                return FakeProcess(lines, hold)

            async def fast_sleep(seconds):
                await asyncio.sleep(0)

            tunnel = tg_webapp.CloudflaredTunnel(Path("cloudflared.exe"), 8641, on_url=lambda u: None,
                                                 on_state=lambda s, e: states.append((s, e)), popen=popen,
                                                 sleep=fast_sleep, **kwargs)
            tunnel.start()
            for _ in range(300):
                await asyncio.sleep(0.01)
                if until and until(tunnel, states, launched):
                    break
            snapshot = (list(states), list(launched), tunnel.gave_up, tunnel.use_http2)
            await tunnel.stop()
            return tunnel, snapshot

        return asyncio.run(scenario())

    def test_no_address_in_time_kills_and_falls_back_to_http2(self):
        tunnel, (states, launched, _, http2) = self.run_tunnel(
            [([], True), ([URL_LINE, REG_LINE], True)], url_timeout=0.15,
            until=lambda t, s, l: any(state == "online" for state, _ in s))
        self.assertTrue(any(state == "error" and "адрес" in error for state, error in states))
        self.assertNotIn("--protocol", launched[0][0])
        self.assertEqual(launched[1][0][launched[1][0].index("--protocol") + 1], "http2")
        self.assertTrue(http2)
        self.assertIn(("online", ""), states)

    def test_registration_timeout(self):
        _, (states, launched, _, _) = self.run_tunnel(
            [([URL_LINE], True)], register_timeout=0.15,
            until=lambda t, s, l: any(state == "error" for state, _ in s))
        self.assertTrue(any(state == "error" and "не подключился" in error for state, error in states))

    def test_retry_cap_then_manual_retry(self):
        async def scenario():
            launched, states = [], []

            def popen(cmd, **kwargs):
                launched.append(cmd)
                return FakeProcess(["ERR failed to request quick Tunnel: dial tcp: i/o timeout"], False)

            async def fast_sleep(seconds):
                await asyncio.sleep(0)

            tunnel = tg_webapp.CloudflaredTunnel(Path("c"), 1, on_url=lambda u: None,
                                                 on_state=lambda s, e: states.append((s, e)),
                                                 popen=popen, sleep=fast_sleep, max_failures=3)
            tunnel.start()
            for _ in range(200):
                await asyncio.sleep(0.01)
                if tunnel.gave_up:
                    break
            gave_up, count = tunnel.gave_up, len(launched)
            await asyncio.sleep(0.1)
            idle_count = len(launched)
            tunnel.retry()
            for _ in range(100):
                await asyncio.sleep(0.01)
                if len(launched) > idle_count:
                    break
            after = len(launched)
            await tunnel.stop()
            return gave_up, count, idle_count, after, states

        gave_up, count, idle_count, after, states = asyncio.run(scenario())
        self.assertTrue(gave_up)
        self.assertEqual(count, 3)
        self.assertEqual(idle_count, 3)
        self.assertGreater(after, idle_count)
        failed = [error for state, error in states if state == "failed"]
        self.assertTrue(failed and "Повторить" in failed[0])
        self.assertTrue(any("Cloudflare" in error for _, error in states))

    def test_named_tunnel_keeps_token_off_the_command_line(self):
        tunnel = tg_webapp.CloudflaredTunnel(Path("c"), 8641, on_url=lambda u: None, token=NAMED_TOKEN,
                                             protocol="http2", edge_ip_version="4")
        command = tunnel.command()
        self.assertEqual(command[-1], "run")
        self.assertNotIn(NAMED_TOKEN, " ".join(command))
        self.assertIn("--edge-ip-version", command)
        _, (states, launched, _, _) = self.run_tunnel(
            [([REG_LINE], True)], token=NAMED_TOKEN,
            until=lambda t, s, l: any(state == "online" for state, _ in s))
        self.assertEqual(launched[0][1]["env"]["TUNNEL_TOKEN"], NAMED_TOKEN)
        self.assertIn(("online", ""), states)

    def test_quic_trouble_line_switches_protocol(self):
        tunnel = tg_webapp.CloudflaredTunnel(Path("c"), 1, on_url=lambda u: None)
        tunnel._line("ERR Failed to dial a quic connection error=\"timeout: no recent network activity\"")
        self.assertTrue(tunnel.use_http2)
        self.assertIn("HTTP/2", tunnel.last_error)

class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(tg_webapp, "_memu_dir", return_value=Path(self.tmp.name))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_named_token_and_options(self):
        config = tg_webapp.save_config({"mode": "named", "named_url": "https://memu.example.com/tgapp/",
                                        "named_token": "cloudflared service install " + NAMED_TOKEN,
                                        "cf_protocol": "http2", "cf_edge_ip": "4"})
        self.assertEqual(config["named_token"], NAMED_TOKEN)
        self.assertEqual(config["named_url"], "https://memu.example.com")
        self.assertEqual(config["cf_protocol"], "http2")
        public = tg_webapp.public_config(config)
        self.assertNotIn("named_token", public)
        self.assertTrue(public["named_token_set"])
        with self.assertRaises(ValueError):
            tg_webapp.save_config({"named_token": "short"})
        retried = tg_webapp.save_config({"retry": True})
        self.assertGreater(retried["retry_at"], 0)
        self.assertEqual(tg_webapp.save_config({"cf_protocol": "bogus"})["cf_protocol"], "auto")

class VerifyTest(unittest.TestCase):
    def test_address_is_published_only_after_dns_and_health(self):
        async def scenario():
            manager = tg_webapp.Manager()
            answers = iter([("dns", "Адрес ещё не появился в DNS — жду"), ("http", "x"), ("ok", "")])
            manager.probe = lambda base: next(answers)
            manager.url = "https://fresh-host-name.trycloudflare.com"
            states, published = [], []
            with mock.patch.object(tg_webapp, "VERIFY_EVERY", 0.01), \
                    mock.patch.object(tg_webapp, "notify_bot", published.append), \
                    mock.patch.object(manager, "write_status", lambda: states.append((manager.state, manager.public_url()))):
                manager._set_state("verifying", "")
                manager._start_verify(publish=True)
                await asyncio.wait_for(manager._verify_task, 2)
            return states, published, manager

        states, published, manager = asyncio.run(scenario())
        self.assertIn(("dns_pending", None), states)
        self.assertTrue(all(url is None for state, url in states if state != "online"))
        self.assertEqual(manager.state, "online")
        self.assertTrue(manager.verified)
        self.assertEqual([url for url in published if url], ["https://fresh-host-name.trycloudflare.com/tgapp/"])

    def test_dns_never_appears_requests_a_new_host(self):
        async def scenario():
            manager = tg_webapp.Manager()
            manager.probe = lambda base: ("dns", "нет")
            manager.url = "https://never.trycloudflare.com"
            terminated = []
            manager.tunnel = mock.MagicMock(named=False, retry_at=0.0, failures=0,
                                            effective_protocol=lambda: "", _terminate=lambda: terminated.append(1))
            with mock.patch.object(tg_webapp, "VERIFY_EVERY", 0.01), mock.patch.object(tg_webapp, "VERIFY_TIMEOUT", 0.1), \
                    mock.patch.object(tg_webapp, "notify_bot", lambda url: None), \
                    mock.patch.object(manager, "write_status", lambda: None):
                manager._start_verify(publish=True)
                await asyncio.wait_for(manager._verify_task, 2)
            return manager, terminated

        manager, terminated = asyncio.run(scenario())
        self.assertEqual(manager.state, "error")
        self.assertEqual(terminated, [1])
        self.assertIsNone(manager.public_url())

    def test_doh_and_health_check(self):
        nonce = tg_webapp.instance_nonce()

        def handler(request):
            if request.url.host in ("1.1.1.1", "8.8.8.8"):
                return httpx.Response(200, json={"Status": 0, "Answer": [{"type": 1, "data": "104.16.0.1"}]})
            if request.url.path == "/tgapp/healthz":
                return httpx.Response(200, json={"ok": True, "app": "memuai-tgapp", "nonce": nonce})
            return httpx.Response(404)

        def nxdomain(request):
            if request.url.host in ("1.1.1.1", "8.8.8.8"):
                return httpx.Response(200, json={"Status": 3})
            return httpx.Response(200, json={})

        async def run(handler_):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler_)) as client:
                return await tg_webapp.check_public_url("https://fresh.trycloudflare.com", client)

        self.assertEqual(asyncio.run(run(handler)), ("ok", ""))
        self.assertEqual(asyncio.run(run(nxdomain))[0], "dns")

class MenuButtonTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(pgdata, "MEMU_DIR", Path(self.tmp.name))
        self.patch.start()
        self.prefs = pgdata.save_preferences({"telegram_enabled": True, "telegram_bot_token": TOKEN,
                                              "telegram_chat_id": str(OWNER)})
        self.calls = []
        self.menu = {}
        self.stale_once = False

        def handler(request):
            method = request.url.path.rsplit("/", 1)[-1]
            payload = json.loads(request.content or b"{}")
            self.calls.append((method, payload))
            if method == "setChatMenuButton":
                if self.stale_once and payload.get("chat_id"):
                    self.stale_once = False
                else:
                    self.menu[str(payload.get("chat_id", "default"))] = payload["menu_button"]
            if method == "getChatMenuButton":
                return httpx.Response(200, json={"ok": True, "result": self.menu.get(str(payload["chat_id"]),
                                                                                     {"type": "commands"})})
            if method in ("sendMessage", "editMessageText"):
                return httpx.Response(200, json={"ok": True, "result": {"message_id": 77}})
            return httpx.Response(200, json={"ok": True, "result": True})

        self.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def asyncTearDown(self):
        await self.client.aclose()
        self.patch.stop()
        self.tmp.cleanup()

    async def test_default_and_chat_buttons_are_set_and_verified(self):
        url = "https://new-host.trycloudflare.com/tgapp/"
        self.stale_once = True
        ok = await telegram_bot._apply_menu_button(self.client, TOKEN, self.prefs, url)
        self.assertTrue(ok)
        self.assertEqual(self.menu["default"]["web_app"]["url"], url)
        self.assertEqual(self.menu[str(OWNER)]["web_app"]["url"], url)
        self.assertEqual(sum(1 for m, p in self.calls if m == "setChatMenuButton" and p.get("chat_id")), 2)

    async def test_url_change_refreshes_the_home_card(self):
        with mock.patch.object(telegram_bot, "_model_entries", lambda prefs=None: [("m", "custom")]), \
                mock.patch.object(telegram_bot, "_read_webapp_url", lambda: "https://a.trycloudflare.com/tgapp/"):
            telegram_bot._webapp_cache["at"] = 0.0
            await telegram_bot._show_home(self.client, TOKEN, str(OWNER), self.prefs)
        first = next(p for m, p in self.calls if m == "sendMessage")
        self.assertIn("a.trycloudflare.com", json.dumps(first["reply_markup"]))
        runner = telegram_bot._BotRunner(self.client)
        runner.token = TOKEN
        runner.webapp_url, runner.webapp_applied = "https://a.trycloudflare.com/tgapp/", True
        self.calls.clear()
        with mock.patch.object(telegram_bot, "_model_entries", lambda prefs=None: [("m", "custom")]), \
                mock.patch.object(telegram_bot, "_read_webapp_url", lambda: "https://b.trycloudflare.com/tgapp/"):
            telegram_bot._webapp_cache["at"] = 0.0
            await runner.check_webapp(self.prefs, force=True)
        edits = [p for m, p in self.calls if m == "editMessageText"]
        self.assertTrue(edits, "the menu card is edited in place")
        self.assertIn("b.trycloudflare.com", json.dumps(edits[-1]["reply_markup"]))
        self.assertNotIn("a.trycloudflare.com", json.dumps(edits[-1]["reply_markup"]))
        self.assertEqual(self.menu[str(OWNER)]["web_app"]["url"], "https://b.trycloudflare.com/tgapp/")

if __name__ == "__main__":
    unittest.main()
