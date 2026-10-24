"""Telegram Mini App (Round 20): initData, sessions, whitelist, tunnel, API, MemuCode bridge."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

from app import tg_webapp  # noqa: E402

TOKEN = "123456:TEST-token_for-mini-app"
USER = 4242


def init_data(user_id: int = USER, *, token: str = TOKEN, auth_date: int | None = None, **extra) -> str:
    fields = {"auth_date": int(time.time()) if auth_date is None else auth_date,
              "query_id": "AAH-test", "user": {"id": user_id, "first_name": "Yono", "language_code": "ru"}}
    fields.update(extra)
    return tg_webapp.sign_init_data(fields, token)


class InitDataTest(unittest.TestCase):
    def test_valid_data_returns_the_user(self) -> None:
        data = tg_webapp.validate_init_data(init_data(), TOKEN)
        self.assertEqual(data["user"]["id"], USER)
        self.assertNotIn("hash", data["fields"])

    def test_known_vector_matches_the_documented_algorithm(self) -> None:
        # Independent re-implementation of core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
        import hmac
        from urllib.parse import parse_qsl
        raw = init_data(start_param="chats")
        pairs = dict(parse_qsl(raw))
        received = pairs.pop("hash")
        check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
        secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
        self.assertEqual(hmac.new(secret, check.encode(), hashlib.sha256).hexdigest(), received)
        self.assertEqual(tg_webapp.validate_init_data(raw, TOKEN)["fields"]["start_param"], "chats")

    def test_rejections(self) -> None:
        cases = {
            "bad_hash": init_data(token="999:other"),
            "expired": init_data(auth_date=int(time.time()) - 90000),
            "future": init_data(auth_date=int(time.time()) + 3600),
            "empty": "",
            "no_hash": "auth_date=1&user=%7B%22id%22%3A1%7D",
            "malformed": "a=1&a=2&hash=" + "0" * 64,
        }
        for code, raw in cases.items():
            with self.subTest(code=code):
                with self.assertRaises(tg_webapp.InitDataError) as caught:
                    tg_webapp.validate_init_data(raw, TOKEN)
                self.assertEqual(caught.exception.code, code)

    def test_tampered_field_breaks_the_signature(self) -> None:
        raw = init_data().replace("Yono", "Mallory")
        with self.assertRaises(tg_webapp.InitDataError) as caught:
            tg_webapp.validate_init_data(raw, TOKEN)
        self.assertEqual(caught.exception.code, "bad_hash")

    def test_missing_user_and_token(self) -> None:
        raw = tg_webapp.sign_init_data({"auth_date": int(time.time()), "query_id": "x"}, TOKEN)
        with self.assertRaises(tg_webapp.InitDataError) as caught:
            tg_webapp.validate_init_data(raw, TOKEN)
        self.assertEqual(caught.exception.code, "no_user")
        with self.assertRaises(tg_webapp.InitDataError) as caught:
            tg_webapp.validate_init_data(init_data(), "")
        self.assertEqual(caught.exception.code, "no_token")

    def test_max_age_is_configurable(self) -> None:
        raw = init_data(auth_date=int(time.time()) - 7200)
        tg_webapp.validate_init_data(raw, TOKEN, max_age=3 * 3600)
        with self.assertRaises(tg_webapp.InitDataError):
            tg_webapp.validate_init_data(raw, TOKEN, max_age=3600)


class TokenAndConfigTest(unittest.TestCase):
    def test_session_tokens(self) -> None:
        secret = "ab" * 32
        token, expires = tg_webapp.issue_token("42", secret, ttl=60)
        self.assertGreater(expires, time.time())
        self.assertEqual(tg_webapp.verify_token(token, secret)["u"], "42")
        self.assertIsNone(tg_webapp.verify_token(token, "cd" * 32))
        self.assertIsNone(tg_webapp.verify_token(token, secret, scope="module"))
        self.assertIsNone(tg_webapp.verify_token(token, secret, now=time.time() + 120))
        self.assertIsNone(tg_webapp.verify_token(token[:-2] + "AA", secret))
        self.assertIsNone(tg_webapp.verify_token("garbage", secret))

    def test_custom_url_normalization(self) -> None:
        good = {"https://Memu.Example.com/": "https://memu.example.com",
                "https://h.example:8443/base/tgapp/": "https://h.example:8443/base"}
        for raw, expected in good.items():
            self.assertEqual(tg_webapp.normalize_custom_url(raw), expected)
        for bad in ("http://memu.example.com", "https://u:p@h.example", "https://h.example/?a=1", "ftp://x", "https://"):
            self.assertEqual(tg_webapp.normalize_custom_url(bad), "", bad)

    def test_save_config_accepts_only_known_keys_and_keeps_the_secret(self) -> None:
        before = tg_webapp.load_config()
        config = tg_webapp.save_config({"enabled": True, "secret": "00" * 32, "mode": "nope", "port": 80})
        self.assertTrue(config["enabled"])
        self.assertEqual(config["secret"], before["secret"])
        self.assertEqual(config["mode"], "cloudflare")
        self.assertEqual(config["port"], tg_webapp.DEFAULT_PORT)
        with self.assertRaises(ValueError):
            tg_webapp.save_config({"custom_url": "http://insecure.example"})
        self.assertNotEqual(tg_webapp.regenerate_secret()["secret"], before["secret"])
        self.assertNotIn("secret", tg_webapp.public_config())
        tg_webapp.save_config({"enabled": False})

    def test_paired_user_prefers_a_dedicated_field(self) -> None:
        self.assertEqual(tg_webapp.paired_user_id({"telegram_chat_id": "77"}), "77")
        self.assertEqual(tg_webapp.paired_user_id({"telegram_chat_id": "77", "telegram_user_id": 88}), "88")
        self.assertEqual(tg_webapp.paired_user_id({"telegram_chat_id": "@channel"}), "")

    def test_authorized_users_come_from_the_bot_with_a_fallback(self) -> None:
        prefs = {"telegram_chat_id": "77", "telegram_allowed_users": ["501", "bad", -5]}
        with mock.patch("app.telegram_bot.authorized_user_ids", return_value=["88", "501"]):
            self.assertEqual(tg_webapp.authorized_user_ids(prefs), ["88", "501"])
            self.assertTrue(tg_webapp.is_authorized_user(501, prefs))
            self.assertFalse(tg_webapp.is_authorized_user("77", prefs), "the bot is the authority when it answers")
        with mock.patch("app.telegram_bot.authorized_user_ids", side_effect=RuntimeError("bot broken")):
            self.assertEqual(tg_webapp.authorized_user_ids(prefs), ["77", "501"])
        # A group chat id is never a user; garbage never matches.
        with mock.patch("app.telegram_bot.authorized_user_ids", side_effect=ImportError):
            self.assertEqual(tg_webapp.authorized_user_ids({"telegram_chat_id": "-100123"}), [])
            self.assertFalse(tg_webapp.is_authorized_user("", {"telegram_chat_id": "77"}))
            self.assertFalse(tg_webapp.is_authorized_user("-77", {"telegram_chat_id": "77"}))

    def test_rate_limiter(self) -> None:
        limiter = tg_webapp.RateLimiter(3, 10)
        self.assertEqual([limiter.hit("k", now=t) for t in (0, 1, 2, 3)], [True, True, True, False])
        self.assertTrue(limiter.hit("k", now=11.5))
        self.assertTrue(limiter.hit("other", now=3))


class FakeProcess:
    def __init__(self, lines, *, exit_code=1, hold: threading.Event | None = None):
        text = "".join(line + "\n" for line in lines)
        self._hold = hold
        self.stdout = io.StringIO(text) if hold is None else _HeldStream(text, hold)
        self.returncode = None
        self.exit_code = exit_code
        self.terminated = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self._hold is not None:
            self._hold.wait(timeout or 5)
        self.returncode = self.exit_code
        return self.exit_code

    def terminate(self):
        self.terminated = True
        if self._hold is not None:
            self._hold.set()
        self.returncode = -15

    def kill(self):
        self.terminate()


class _HeldStream:
    """Gives its lines, then blocks like a live process until released."""

    def __init__(self, text, hold):
        self._lines = text.splitlines(True)
        self._hold = hold

    def readline(self):
        if self._lines:
            return self._lines.pop(0)
        self._hold.wait(5)
        return ""


QUICK_TUNNEL_OUTPUT = [
    "2026-10-07T10:00:00Z INF Requesting new quick Tunnel on trycloudflare.com...",
    "2026-10-07T10:00:01Z INF +--------------------------------------------------------------------------------------------+",
    "2026-10-07T10:00:01Z INF |  Your quick Tunnel has been created! Visit it at (it may take some time to be reachable):  |",
    "2026-10-07T10:00:01Z INF |  https://bright-otter-spoon-river.trycloudflare.com                                         |",
    "2026-10-07T10:00:01Z INF +--------------------------------------------------------------------------------------------+",
    "2026-10-07T10:00:02Z INF Registered tunnel connection connIndex=0 connection=abc event=0 ip=198.41.200.13 location=ams01 protocol=quic",
]


class TunnelTest(unittest.TestCase):
    def test_parse_tunnel_url(self) -> None:
        self.assertEqual(tg_webapp.parse_tunnel_url(QUICK_TUNNEL_OUTPUT[3]), "https://bright-otter-spoon-river.trycloudflare.com")
        self.assertIsNone(tg_webapp.parse_tunnel_url("https://api.trycloudflare.com/tunnel"))
        self.assertIsNone(tg_webapp.parse_tunnel_url("visit https://evil.example.com/?x=trycloudflare.com"))
        self.assertIsNone(tg_webapp.parse_tunnel_url(""))

    def test_backoff_grows_and_is_capped(self) -> None:
        delays = [tg_webapp.CloudflaredTunnel.backoff(n) for n in range(1, 10)]
        self.assertEqual(delays[:4], [2.0, 4.0, 8.0, 16.0])
        self.assertEqual(max(delays), 120.0)
        self.assertEqual(delays, sorted(delays))

    def test_lifecycle_url_restart_and_stop(self) -> None:
        async def scenario():
            urls, states, sleeps, launched = [], [], [], []
            hold = threading.Event()

            def popen(cmd, **kwargs):
                launched.append((cmd, kwargs))
                if len(launched) == 1:
                    return FakeProcess(QUICK_TUNNEL_OUTPUT, exit_code=1)        # dies after a while
                return FakeProcess(QUICK_TUNNEL_OUTPUT, hold=hold)              # stays up

            async def fake_sleep(seconds):
                sleeps.append(seconds)

            tunnel = tg_webapp.CloudflaredTunnel(Path("cloudflared.exe"), 8641, on_url=urls.append,
                                                 on_state=lambda s, e: states.append(s), popen=popen, sleep=fake_sleep)
            tunnel.start()
            for _ in range(200):
                if len(launched) >= 2 and tunnel.url:
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(tunnel.url, "https://bright-otter-spoon-river.trycloudflare.com")
            self.assertIn("online", states)
            self.assertIn("error", states)
            self.assertEqual(sleeps, [2.0])                                     # restarted with backoff
            cmd, kwargs = launched[0]
            self.assertEqual(cmd[1:], ["tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:8641"])
            self.assertEqual(kwargs["stdin"], tg_webapp.subprocess.DEVNULL)
            if os.name == "nt":
                self.assertTrue(kwargs["creationflags"] & 0x08000000)          # CREATE_NO_WINDOW
            process = tunnel.process
            await tunnel.stop()
            self.assertTrue(process.terminated)
            self.assertIsNone(tunnel.url)
            self.assertEqual(urls[-1], None)
            self.assertIn("https://bright-otter-spoon-river.trycloudflare.com", urls)
        asyncio.run(scenario())

    def test_config_yaml_conflict_is_explained(self) -> None:
        tunnel = tg_webapp.CloudflaredTunnel(Path("x"), 1, on_url=lambda u: None)
        tunnel._line("ERR Cannot determine default configuration path. No file config.yaml in [~/.cloudflared]")
        self.assertIn("config.yaml", tunnel.last_error)

    def test_download_verifies_the_release_digest(self) -> None:
        import httpx
        payload = b"MZ" + b"\0" * (tg_webapp.CLOUDFLARED_MIN_BYTES + 10)
        digest = hashlib.sha256(payload).hexdigest()
        url = "https://github.com/cloudflare/cloudflared/releases/download/2026.10.0/cloudflared-windows-amd64.exe"

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.github.com":
                return httpx.Response(200, json={"assets": [{"name": tg_webapp.CLOUDFLARED_ASSET, "browser_download_url": url,
                                                             "digest": "sha256:" + state["digest"]}]})
            return httpx.Response(200, content=payload, headers={"content-length": str(len(payload))})

        state = {"digest": digest}

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await tg_webapp.download_cloudflared(client=client)

        with tempfile.TemporaryDirectory() as folder, mock.patch.object(tg_webapp, "bin_dir", return_value=Path(folder)):
            path = asyncio.run(run())
            self.assertEqual(path.read_bytes(), payload)
            path.unlink()
            state["digest"] = "0" * 64
            with self.assertRaises(RuntimeError):
                asyncio.run(run())
            self.assertFalse(any(Path(folder).iterdir()), "a rejected download leaves nothing behind")

    def test_download_rejects_a_small_file(self) -> None:
        import httpx

        def handler(request):
            if request.url.host == "api.github.com":
                return httpx.Response(403)
            return httpx.Response(200, content=b"MZ tiny")

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await tg_webapp.download_cloudflared(client=client)

        with tempfile.TemporaryDirectory() as folder, mock.patch.object(tg_webapp, "bin_dir", return_value=Path(folder)):
            with self.assertRaises(RuntimeError):
                asyncio.run(run())


class ManagerTest(unittest.TestCase):
    def test_disabled_manager_stays_off_and_reports_reasons(self) -> None:
        tg_webapp.save_config({"enabled": False})

        async def run():
            manager = tg_webapp.Manager()
            await manager.reconcile()
            return manager

        manager = asyncio.run(run())
        self.assertEqual(manager.state, "off")
        self.assertIsNone(manager.public_url())
        self.assertIsNone(tg_webapp.webapp_url())
        self.assertFalse(tg_webapp.webapp_enabled())

    def test_custom_mode_serves_and_notifies_the_bot(self) -> None:
        from app.pgdata import save_preferences
        save_preferences({"telegram_bot_token": TOKEN, "telegram_chat_id": str(USER)})
        tg_webapp.save_config({"enabled": True, "mode": "custom", "custom_url": "https://memu.example.com",
                               "port": 18641})
        calls = []
        fake_bot = mock.MagicMock()
        fake_bot.on_webapp_url_changed = calls.append

        async def run():
            manager = tg_webapp.Manager()
            # Hermetic: the diagnostic check of the public address would go to the internet (memu.example.com).
            manager.probe = lambda base: ("ok", "")
            with mock.patch.dict("sys.modules", {"app.telegram_bot": fake_bot}), \
                    mock.patch("app.telegram_bot", fake_bot, create=True):
                import app
                with mock.patch.object(app, "telegram_bot", fake_bot, create=True):
                    await manager.reconcile()
                    url = manager.public_url()
                    import httpx
                    # trust_env=False: a system/env HTTP proxy (no NO_PROXY) would
                    # carry this loopback request elsewhere and answer 404.
                    # A busy preferred port (another app/test server) moves the listener: ask the manager's own port.
                    self.assertTrue(manager.port and manager.server is not None and manager.server.started)
                    async with httpx.AsyncClient(trust_env=False) as client:
                        health = await client.get(f"http://127.0.0.1:{manager.port}/tgapp/health")
                    await manager.stop_all()
                    manager.state = "off"
                    manager._notify()
            return url, health.status_code

        try:
            url, status = asyncio.run(run())
        finally:
            tg_webapp.save_config({"enabled": False, "mode": "cloudflare", "custom_url": "", "port": tg_webapp.DEFAULT_PORT})
        self.assertEqual(url, "https://memu.example.com/tgapp/")
        self.assertEqual(status, 200)
        self.assertEqual(calls, ["https://memu.example.com/tgapp/", None])


# --------------------------------------------------------------------------- HTTP API

class FakeChatService:
    def __init__(self, parts=("Привет", ", мир! [TITLE: Тестовый чат] [MEM: любит чай]")):
        self.parts = parts
        self.calls = []

    async def stream_completion(self, model, messages, params=None, trace=None):
        self.calls.append({"model": model, "messages": messages, "params": params})
        for part in self.parts:
            await asyncio.sleep(0)
            yield {"choices": [{"delta": {"content": part}}]}


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from fastapi.testclient import TestClient
        from app.pgdata import save_preferences
        from app import tgapp_chat
        save_preferences({"telegram_bot_token": TOKEN, "telegram_chat_id": str(USER), "memes": [], "chat_memes": {}})
        tg_webapp.save_config({"enabled": True, "mode": "cloudflare"})
        cls.models = mock.patch.object(tgapp_chat, "chat_models",
                                       return_value=[{"id": "test/model", "name": "Test", "provider": "test",
                                                      "vision": True, "pinned": False, "default": True}])
        cls.models.start()
        from app.tgapp_api import create_tgapp
        # One portal (event loop) for the whole class, like the real single-loop server: jobs outlive requests.
        cls.client = TestClient(create_tgapp())
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.__exit__(None, None, None)
        cls.models.stop()
        tg_webapp.save_config({"enabled": False})

    def auth(self, **kwargs):
        response = self.client.post("/tgapp/api/auth", json={"init_data": init_data(**kwargs)})
        return response

    def headers(self):
        response = self.auth()
        self.assertEqual(response.status_code, 200, response.text)
        return {"Authorization": "Bearer " + response.json()["token"]}

    def test_static_is_served_with_a_strict_policy(self) -> None:
        response = self.client.get("/tgapp/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("MemuAI", response.text)
        policy = response.headers["content-security-policy"]
        self.assertIn("connect-src 'self'", policy)
        self.assertIn("frame-ancestors", policy)
        self.assertEqual(self.client.get("/tgapp/../tg_webapp.py").status_code, 404)
        self.assertEqual(self.client.get("/tgapp/%2e%2e/tg_webapp.py").status_code, 404)
        self.assertEqual(self.client.get("/tgapp/api/bootstrap").status_code, 404)

    def test_the_official_sdk_is_vendored_and_loaded_first(self) -> None:
        sdk = self.client.get("/tgapp/vendor/telegram-web-app.js")
        self.assertEqual(sdk.status_code, 200)
        self.assertIn("javascript", sdk.headers["content-type"])
        self.assertIn("Telegram", sdk.text)
        bridge = self.client.get("/tgapp/bridge.js").text
        self.assertIn('"vendor/telegram-web-app.js"', bridge)
        self.assertLess(bridge.index("loadScript(VENDORED"), bridge.index("loadScript(OFFICIAL"))

    def test_whitelisted_users_sign_in_and_lose_access_when_removed(self) -> None:
        from app.pgdata import save_preferences
        save_preferences({"telegram_allowed_users": ["5151"]})
        try:
            response = self.client.post("/tgapp/api/auth", json={"init_data": init_data(5151)},
                                        headers={"cf-connecting-ip": "10.9.9.1"})
            self.assertEqual(response.status_code, 200, response.text)
            headers = {"Authorization": "Bearer " + response.json()["token"]}
            self.assertEqual(self.client.post("/tgapp/api/bootstrap", json={}, headers=headers).status_code, 200)
        finally:
            save_preferences({"telegram_allowed_users": []})
        self.assertEqual(self.client.post("/tgapp/api/bootstrap", json={}, headers=headers).status_code, 403)

    def test_chat_list_previews_are_plain_text(self) -> None:
        from app.tgapp_api import plain_snippet
        text = "## Итог\nВот **жирный** и `код`, [ссылка](https://x.y) и snake_case_name.\n| a | b |\n|---|---|\n```py\nprint(1)\n```"
        self.assertEqual(plain_snippet(text), "Итог Вот жирный и код, ссылка и snake_case_name. a b print(1)")
        self.assertTrue(plain_snippet("слово " * 60).endswith("…"))
        self.assertLessEqual(len(plain_snippet("слово " * 60)), 140)

    def test_nothing_outside_tgapp_is_reachable(self) -> None:
        for method, path in (("get", "/v1/models"), ("post", "/v1/chat/completions"), ("get", "/api/admin/state"),
                             ("post", "/api/pg/preferences"), ("get", "/")):
            with self.subTest(path=path):
                self.assertEqual(getattr(self.client, method)(path).status_code, 404)

    def test_authentication_failures(self) -> None:
        self.assertEqual(self.auth(user_id=1).status_code, 403)
        self.assertEqual(self.auth(user_id=1).json()["code"], "not_paired")
        self.assertEqual(self.auth(token="1:other").json()["code"], "bad_hash")
        self.assertEqual(self.auth(auth_date=int(time.time()) - 200000).json()["code"], "expired")
        self.assertEqual(self.client.post("/tgapp/api/bootstrap", json={}).status_code, 401)
        self.assertEqual(self.client.post("/tgapp/api/bootstrap", json={},
                                          headers={"Authorization": "Bearer forged.token"}).status_code, 401)

    def test_whitelist_and_unknown_operations(self) -> None:
        headers = self.headers()
        self.assertEqual(self.client.post("/tgapp/api/preferences.save", json={}, headers=headers).status_code, 404)
        bad = self.client.post("/tgapp/api/settings.save", json={"values": {"telegram_bot_token": "x"}}, headers=headers)
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.client.post("/tgapp/api/settings.save", json={"values": {"font_scale": 400}},
                                          headers=headers).status_code, 400)
        self.assertEqual(self.client.post("/tgapp/api/settings.save", json={"values": {"theme": "neon"}},
                                          headers=headers).status_code, 400)
        ok = self.client.post("/tgapp/api/settings.save", json={"values": {"theme": "light", "telegram_notify_mode": "silent"}},
                              headers=headers)
        self.assertEqual(ok.status_code, 200, ok.text)
        from app.pgdata import load_preferences
        prefs = load_preferences()
        self.assertEqual(prefs["theme"], "light")
        self.assertTrue(prefs["telegram_notify"])
        boot = self.client.post("/tgapp/api/bootstrap", json={}, headers=headers).json()
        self.assertNotIn("telegram_bot_token", json.dumps(boot))
        self.assertNotIn(TOKEN, json.dumps(boot))

    def test_secret_reset_and_unpairing_revoke_sessions(self) -> None:
        headers = self.headers()
        self.assertEqual(self.client.post("/tgapp/api/bootstrap", json={}, headers=headers).status_code, 200)
        tg_webapp.regenerate_secret()
        self.assertEqual(self.client.post("/tgapp/api/bootstrap", json={}, headers=headers).status_code, 401)
        headers = self.headers()
        from app.pgdata import save_preferences
        save_preferences({"telegram_chat_id": "99"})
        try:
            self.assertEqual(self.client.post("/tgapp/api/bootstrap", json={}, headers=headers).status_code, 403)
        finally:
            save_preferences({"telegram_chat_id": str(USER)})
        tg_webapp.save_config({"enabled": False})
        try:
            self.assertEqual(self.auth().status_code, 403)
        finally:
            tg_webapp.save_config({"enabled": True})

    def _send_and_finish(self, headers, body, service):
        with mock.patch("app.chat.chat_service", service):
            response = self.client.post("/tgapp/api/chats.send", json=body, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            job = response.json()["job"]
            events = []
            for _ in range(100):
                data = self.client.post("/tgapp/api/jobs.poll", json={"job": job, "from": len(events), "wait": 1},
                                        headers=headers).json()
                events.extend(data["events"])
                if data["done"]:
                    break
            return response.json(), events

    def test_chat_send_streams_saves_titles_and_memory(self) -> None:
        from app.pgdata import load_chats, load_memory
        headers = self.headers()
        service = FakeChatService()
        meta, events = self._send_and_finish(headers, {"text": "Привет", "web_search": False}, service)
        kinds = [event["type"] for event in events]
        self.assertEqual(kinds[0], "start")
        self.assertIn("delta", kinds)
        self.assertEqual(kinds[-1], "done")
        message = next(event["message"] for event in events if event["type"] == "message")
        self.assertEqual(message["content"], "Привет, мир!")
        chat = next(row for row in load_chats() if row["id"] == meta["chat_id"])
        self.assertEqual(chat["title"], "Тестовый чат")
        self.assertEqual([m["role"] for m in chat["messages"]], ["user", "assistant"])
        self.assertIn("любит чай", load_memory()["facts"])
        system = service.calls[0]["messages"][0]
        self.assertEqual(system["role"], "system")
        self.assertIn("mobile_interface", system["content"])
        # stream segment reader returns NDJSON with the same events
        with mock.patch("app.chat.chat_service", FakeChatService(("ok",))):
            started = self.client.post("/tgapp/api/chats.send", json={"chat_id": meta["chat_id"], "text": "ещё"},
                                       headers=headers).json()
            lines = self.client.post("/tgapp/api/jobs.stream", json={"job": started["job"], "from": 0},
                                     headers=headers).text.strip().splitlines()
        types = [json.loads(line)["type"] for line in lines]
        self.assertEqual(types[0], "hello")
        self.assertEqual(types[-1], "done")
        # regenerate replaces the last answer instead of adding one
        with mock.patch("app.chat.chat_service", FakeChatService(("заново",))):
            pass
        meta2, _ = self._send_and_finish(headers, {"chat_id": meta["chat_id"], "regenerate": True},
                                         FakeChatService(("заново",)))
        chat = next(row for row in load_chats() if row["id"] == meta["chat_id"])
        self.assertEqual(chat["messages"][-1]["content"], "заново")
        self.assertEqual([m["role"] for m in chat["messages"]], ["user", "assistant", "user", "assistant"])

    def test_temporary_chat_is_not_saved(self) -> None:
        from app.pgdata import load_chats
        headers = self.headers()
        before = len(load_chats())
        meta, events = self._send_and_finish(headers, {"text": "секрет", "temp": True}, FakeChatService(("ок [MEM: тайна]",)))
        self.assertEqual(len(load_chats()), before)
        self.assertTrue(meta["chat_id"].startswith("temp-"))
        self.assertFalse(any(event["type"] == "memory" for event in events))

    def test_chat_management_and_memes(self) -> None:
        headers = self.headers()
        meta, _ = self._send_and_finish(headers, {"text": "папки"}, FakeChatService(("ok",)))
        chat_id = meta["chat_id"]
        folders = self.client.post("/tgapp/api/folders.save", json={"folders": [{"id": "f-work", "name": "Работа"}]},
                                   headers=headers).json()["folders"]
        self.assertEqual(folders[0]["id"], "f-work")
        updated = self.client.post("/tgapp/api/chats.update", json={"id": chat_id, "title": "Новое имя", "pinned": True,
                                                                     "folder_id": "f-work"}, headers=headers).json()["chat"]
        self.assertEqual((updated["title"], updated["pinned"], updated["folder_id"]), ("Новое имя", True, "f-work"))
        self.assertEqual(self.client.post("/tgapp/api/chats.update", json={"id": chat_id, "folder_id": "missing"},
                                          headers=headers).status_code, 400)
        listed = self.client.post("/tgapp/api/chats.list", json={}, headers=headers).json()
        self.assertIn(chat_id, [row["id"] for row in listed["chats"]])
        self.assertTrue(self.client.post("/tgapp/api/chats.list", json={"rev": listed["rev"]}, headers=headers).json()["unchanged"])
        meme = self.client.post("/tgapp/api/memes.save", json={"meme": {"name": "Редактор", "instructions": "Правь текст",
                                                                         "caps": {"files": False}}}, headers=headers).json()["meme"]
        self.assertTrue(meme["id"].startswith("meme-"))
        self.assertFalse(meme["caps"]["files"])
        with mock.patch("app.chat.chat_service", FakeChatService(("ok",))):
            refused = self.client.post("/tgapp/api/chats.send", json={"text": "x", "meme_id": meme["id"],
                                                                       "attachments": [{"name": "a.txt", "data": "data:text/plain;base64,YQ=="}]},
                                       headers=headers)
        self.assertEqual(refused.status_code, 400)
        service = FakeChatService(("ok",))
        meta2, _ = self._send_and_finish(headers, {"text": "привет", "meme_id": meme["id"]}, service)
        self.assertIn("agent_profile", service.calls[0]["messages"][0]["content"])
        from app.pgdata import load_preferences
        self.assertEqual(load_preferences()["chat_memes"].get(meta2["chat_id"]), meme["id"])
        self.assertEqual(self.client.post("/tgapp/api/memes.delete", json={"id": meme["id"]}, headers=headers).status_code, 200)
        self.assertEqual(self.client.post("/tgapp/api/chats.delete", json={"id": chat_id}, headers=headers).status_code, 200)
        self.assertEqual(self.client.post("/tgapp/api/chats.get", json={"id": chat_id}, headers=headers).status_code, 404)

    def test_module_documents_need_a_module_scoped_token(self) -> None:
        headers = self.headers()
        modules = self.client.post("/tgapp/api/modules.list", json={}, headers=headers).json()["modules"]
        target = modules[0]["id"]
        closed = self.client.post("/tgapp/api/modules.open", json={"id": target}, headers=headers)
        if not modules[0]["enabled"]:
            self.assertEqual(closed.status_code, 403)
        toggled = self.client.post("/tgapp/api/modules.toggle", json={"id": target, "enabled": True}, headers=headers)
        self.assertTrue(toggled.json()["enabled"])
        self.addCleanup(lambda: self.client.post("/tgapp/api/modules.toggle", json={"id": target, "enabled": False},
                                                 headers=headers))
        enabled = {**modules[0], "enabled": True}
        opened = self.client.post("/tgapp/api/modules.open", json={"id": enabled["id"]}, headers=headers).json()
        page = self.client.get(opened["url"])
        self.assertEqual(page.status_code, 200)
        self.assertIn("connect-src 'none'", page.headers["content-security-policy"])
        app_token = headers["Authorization"][7:]
        self.assertEqual(self.client.get(f"/tgapp/m/{enabled['id']}?t={app_token}").status_code, 401)
        other = next((row["id"] for row in modules if row["id"] != enabled["id"]), None)
        if other:
            self.assertEqual(self.client.get(opened["url"].replace(enabled["id"], other, 1)).status_code, 401)
        denied = self.client.post("/tgapp/api/modules.bridge", json={"id": enabled["id"], "action": "download", "payload": {}},
                                  headers=headers)
        self.assertEqual(denied.status_code, 400)


# --------------------------------------------------------------------------- MemuCode bridge

class FakeOption:
    def __init__(self, label, value, text_input=False):
        self.label, self.value, self.text_input, self.description, self.style = label, value, text_input, "", ""


class FakeDialog:
    def __init__(self, request=None):
        self.title = "Разрешить Bash?"
        self.body = "\x1b[1mrm -rf build\x1b[0m"
        self.options = [FakeOption("Да", "yes"), FakeOption("Всегда", "always"),
                        FakeOption("Нет, и сказать иначе", "feedback", True), FakeOption("Нет", "no")]
        self.multi = False
        self.danger = bool(request and request.danger)
        self.guard = True
        self.allow_empty = False
        self.resolved = []
        if request is not None:
            self.remote_request = request

    def resolve(self, value):
        self.resolved.append(value)


class FakeUi:
    def __init__(self):
        session = mock.MagicMock()
        session.id = "sess-1"
        session.title.return_value = "Починить сборку"
        session.todos = [{"content": "шаг", "status": "in_progress"}]
        self.runtime = mock.MagicMock(root="/proj", model="m1", session=session)
        self.runtime.permissions.mode = "default"
        self.busy = False
        self.connecting = False
        self.dialogs = []
        self.active_tools = {}
        self.stream = ""
        self.reasoning = ""
        self.queue = []
        self.status_text = ""
        self.busy_started = 0
        self.dispatched = []
        self.interrupted = 0
        self.history = mock.MagicMock()

    def dispatch(self, text):
        self.dispatched.append(text)

    def _while_busy(self, text):
        return False

    def _content(self, text, images):
        return text

    def invalidate(self):
        pass

    def interrupt(self):
        self.interrupted += 1

    def say(self, *args, **kwargs):
        pass


class RemoteBridgeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"MEMUCODE_CONFIG_DIR": self.folder, "MEMUCODE_NO_REMOTE": ""})
        self.env.start()
        tg_webapp.save_config({"enabled": True, "code_control": True})

    def tearDown(self) -> None:
        self.env.stop()
        tg_webapp.save_config({"enabled": False})
        shutil.rmtree(self.folder, ignore_errors=True)

    def control(self):
        from app.cli import remote
        ui = FakeUi()
        control = remote.RemoteControl(ui)
        control.registered = True
        control.inbox.mkdir(parents=True, exist_ok=True)
        control.publish(force=True)
        return remote, ui, control

    def test_switch_follows_the_mini_app_settings(self) -> None:
        from app.cli import remote
        self.assertTrue(remote.tgapp_allows())
        tg_webapp.save_config({"code_control": False})
        self.assertFalse(remote.tgapp_allows())
        tg_webapp.save_config({"code_control": True})
        with mock.patch.dict(os.environ, {"MEMUCODE_NO_REMOTE": "1"}):
            self.assertFalse(remote.tgapp_allows())

    def test_snapshot_shows_the_exact_tool_call(self) -> None:
        remote, ui, control = self.control()
        request = mock.MagicMock(danger=True, reason="удаление файлов", key="Bash(rm:*)", suggestion_label="всегда rm")
        request.call.args = {"command": "rm -rf build"}
        request.tool.name = "Bash"
        request.prepared.preview = "rm -rf build"
        request.prepared.diff = ""
        ui.dialogs = [FakeDialog(request)]
        control.publish(force=True)
        state = remote.terminal_state(control.id)
        dialog = state["dialog"]
        self.assertEqual(dialog["kind"], "permission")
        self.assertEqual(dialog["detail"]["tool"], "Bash")
        self.assertIn("rm -rf build", dialog["detail"]["args"])
        self.assertTrue(dialog["detail"]["danger"])
        self.assertNotIn("\x1b", dialog["body"])
        self.assertEqual(state["session_id"], "sess-1")
        self.assertEqual([row["session_id"] for row in remote.list_terminals()], ["sess-1"])

    def test_prompt_interrupt_and_answers(self) -> None:
        remote, ui, control = self.control()
        remote.send_command(control.id, {"type": "prompt", "text": "/compact"})
        asyncio.run(control._drain_inbox())
        self.assertEqual(ui.dispatched, ["/compact"])
        ui.busy = True
        remote.send_command(control.id, {"type": "prompt", "text": "потом это"})
        asyncio.run(control._drain_inbox())
        self.assertEqual(ui.queue[-1]["display"], "потом это")
        remote.send_command(control.id, {"type": "interrupt"})
        asyncio.run(control._drain_inbox())
        self.assertEqual(ui.interrupted, 1)

        request = mock.MagicMock(danger=True)
        dialog = FakeDialog(request)
        ui.dialogs = [dialog]
        control.publish(force=True)
        dialog_id = remote.terminal_state(control.id)["dialog"]["id"]
        remote.send_command(control.id, {"type": "answer", "dialog": dialog_id, "choice": 0})
        asyncio.run(control._drain_inbox())
        self.assertEqual(dialog.resolved, [], "a dangerous approval without confirmation is refused")
        self.assertFalse(control.acks[-1]["ok"])
        remote.send_command(control.id, {"type": "answer", "dialog": dialog_id, "choice": 2, "text": "лучше make clean"})
        asyncio.run(control._drain_inbox())
        self.assertEqual(dialog.resolved, [{"value": "feedback", "text": "лучше make clean"}])
        remote.send_command(control.id, {"type": "answer", "dialog": dialog_id, "choice": 0, "confirm": True})
        asyncio.run(control._drain_inbox())
        self.assertEqual(dialog.resolved[-1], "yes")
        remote.send_command(control.id, {"type": "answer", "dialog": dialog_id + 5, "choice": 3})
        asyncio.run(control._drain_inbox())
        self.assertIn("changed", control.acks[-1]["error"])

    def test_stale_commands_are_dropped(self) -> None:
        remote, ui, control = self.control()
        command_id = remote.send_command(control.id, {"type": "prompt", "text": "старое"})
        file = control.inbox / f"{command_id}.json"
        payload = json.loads(file.read_text(encoding="utf-8"))
        payload["ts"] -= 3600
        file.write_text(json.dumps(payload), encoding="utf-8")
        asyncio.run(control._drain_inbox())
        self.assertEqual(ui.dispatched, [])
        self.assertIn("expired", control.acks[-1]["error"])

    def test_server_side_guard_and_validation(self) -> None:
        from app import tgapp_code
        remote, ui, control = self.control()
        ui.dialogs = [FakeDialog(mock.MagicMock(danger=False))]
        control.publish(force=True)
        dialog_id = remote.terminal_state(control.id)["dialog"]["id"]
        with self.assertRaises(PermissionError):
            tgapp_code.send_to_terminal(control.id, {"type": "answer", "dialog": dialog_id, "choice": 1})   # "always"
        tgapp_code.send_to_terminal(control.id, {"type": "answer", "dialog": dialog_id, "choice": 1, "confirm": True})
        with self.assertRaises(ValueError):
            tgapp_code.send_to_terminal(control.id, {"type": "shell", "text": "x"})
        with self.assertRaises(ValueError):
            remote.send_command("../../evil", {"type": "prompt", "text": "x"})
        with self.assertRaises(LookupError):
            remote.send_command("1-deadbeef", {"type": "prompt", "text": "x"})

    def test_stale_terminals_disappear(self) -> None:
        remote, ui, control = self.control()
        data = json.loads(control.file.read_text(encoding="utf-8"))
        data["heartbeat"] = time.time() - 60
        control.file.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(remote.list_terminals(), [])
        self.assertIsNone(remote.terminal_state(control.id))

    def _write_session(self, root: str) -> str:
        from app.cli import paths
        folder = paths.project_sessions(root)
        session_id = "20261007-abcdef"
        events = [
            {"t": "meta", "id": session_id, "root": root, "created": time.time(), "title": "Тест"},
            {"t": "msg", "m": {"id": "u1", "role": "user", "kind": "prompt", "content": "почини тест", "ts": 1}},
            {"t": "msg", "m": {"id": "a1", "role": "assistant", "content": "Смотрю.", "tool_calls": [{"id": "c1", "name": "Read", "args": {"path": "a.py"}}]}},
            {"t": "msg", "m": {"id": "t1", "role": "tool", "tool_call_id": "c1", "name": "Read", "content": "print(1)", "is_error": False}},
        ]
        (folder / f"{session_id}.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
        return session_id

    def test_transcript_reading_is_incremental(self) -> None:
        from app import tgapp_code
        root = tempfile.mkdtemp()
        session_id = self._write_session(root)
        first = tgapp_code.read_transcript(session_id, 0)
        kinds = [item["kind"] for item in first["items"]]
        self.assertEqual(kinds, ["meta", "user", "assistant", "call", "tool"])
        self.assertEqual(first["root"], root)
        again = tgapp_code.read_transcript(session_id, first["cursor"])
        self.assertEqual(again["items"], [])
        file = tgapp_code.find_transcript(session_id)
        with file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"t": "msg", "m": {"id": "a2", "role": "assistant", "content": "Готово"}}) + "\n")
            handle.write('{"t": "msg", "m": {"id": "half"')                 # a line still being written
        more = tgapp_code.read_transcript(session_id, first["cursor"])
        self.assertEqual([item["text"] for item in more["items"]], ["Готово"])
        self.assertIsNone(tgapp_code.find_transcript("../etc/passwd"))
        shutil.rmtree(root, ignore_errors=True)

    def test_headless_run_streams_events(self) -> None:
        from app import tgapp_code
        root = tempfile.mkdtemp()
        session_id = self._write_session(root)
        lines = [json.dumps({"type": "text", "text": "Готово"}),
                 json.dumps({"type": "tool_use", "id": "c9", "name": "Edit", "input": {"path": "a.py"}}),
                 json.dumps({"type": "tool_result", "id": "c9", "name": "Edit", "ok": True, "summary": "1 правка"}),
                 json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "Готово"})]
        seen = {}

        class Proc(FakeProcess):
            def __init__(self, args, **kwargs):
                seen["args"], seen["kwargs"] = args, kwargs
                super().__init__(lines, exit_code=0)
                self.stderr = io.StringIO("")

        async def run():
            with mock.patch.object(tgapp_code, "memucode_command", return_value=["memucode"]):
                job = tgapp_code.start_headless("u", session_id, "--help me", "acceptEdits", popen=Proc)
                for _ in range(200):
                    if job.done:
                        break
                    await asyncio.sleep(0.01)
                return job

        job = asyncio.run(run())
        types = [event["type"] for event in job.events]
        self.assertEqual(types, ["start", "delta", "tool_use", "tool_result", "result", "done"])
        self.assertTrue(job.events[-1]["ok"])
        args = seen["args"]
        self.assertEqual(args[:3], ["memucode", "-r", session_id])
        self.assertIn("--permission-mode", args)
        self.assertEqual(args[-2:], ["--", "--help me"], "the prompt can never be read as an option")
        self.assertEqual(seen["kwargs"]["cwd"], root)
        self.assertEqual(seen["kwargs"]["env"]["MEMUCODE_NO_REMOTE"], "1")
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
