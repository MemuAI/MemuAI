"""Round 21: the Telegram bot must heal itself.

A fake Bot API (``httpx.MockTransport`` with an async handler) simulates
network errors, a hanging long poll, 409/401/429/5xx, a crashing handler, a
token change, a sleeping PC (clock jump), a stuck session and «Restart bot»
from Settings. Every pause is shortened by patching the backoff.
"""
import asyncio
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app import pgdata, telegram_bot, telegram_pairing, telegram_runtime
from app.telegram_api import TelegramError

TOKEN = "123456789:AAH" + "x" * 32
TOKEN2 = "987654321:BBH" + "y" * 32
OWNER = 305551234
ORIGINAL_BACKOFF = telegram_bot._poll_backoff

class Api:
    """Scriptable Bot API. ``script`` holds per-method lists of actions that
    are consumed one per call: "ok", "connect", "timeout", "hang", an int HTTP
    error code, or a callable returning an httpx.Response/result."""

    def __init__(self):
        self.calls = []
        self.script = {}
        self.updates = []
        self.next_id = 500
        self.hanging = 0

    async def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        token = request.url.path.split("/bot", 1)[-1].split("/", 1)[0]
        self.calls.append((method, token))
        queue = self.script.get(method) or []
        action = queue.pop(0) if queue else "ok"
        if action == "connect":
            raise httpx.ConnectError("connection refused", request=request)
        if action == "timeout":
            raise httpx.ReadTimeout("read timed out", request=request)
        if action == "protocol":
            raise httpx.RemoteProtocolError("server disconnected", request=request)
        if action == "hang":
            self.hanging += 1
            await asyncio.sleep(3600)
        if isinstance(action, int):
            body = {"ok": False, "error_code": action, "description": {
                409: "Conflict: terminated by other getUpdates request", 401: "Unauthorized",
                429: "Too Many Requests: retry after 1", 502: "Bad Gateway"}.get(action, "error")}
            if action == 429:
                body["parameters"] = {"retry_after": 1}
            return httpx.Response(action, json=body)
        if callable(action):
            return action(request)
        if method == "getUpdates":
            await asyncio.sleep(0.02)
            updates, self.updates = self.updates, []
            return httpx.Response(200, json={"ok": True, "result": updates})
        if method == "getMe":
            bot_id = 1 if token == TOKEN else 2
            return httpx.Response(200, json={"ok": True, "result": {"id": bot_id, "username": f"memu{bot_id}_bot",
                                                                      "first_name": "MemuAI"}})
        if method in ("sendMessage", "editMessageText"):
            self.next_id += 1
            return httpx.Response(200, json={"ok": True, "result": {"message_id": self.next_id}})
        return httpx.Response(200, json={"ok": True, "result": True})

    def count(self, method, token=None):
        return sum(1 for name, tok in self.calls if name == method and (token is None or tok == token))

def message_update(update_id, text, chat_id=OWNER):
    return {"update_id": update_id, "message": {
        "message_id": update_id, "date": int(time.time()), "text": text,
        "chat": {"id": chat_id, "type": "private"}, "from": {"id": chat_id, "first_name": "Yono"}}}

async def until(predicate, timeout=6.0, step=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(step)
    return bool(predicate())

class RuntimeCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [
            patch.object(pgdata, "MEMU_DIR", Path(self.tmp.name)),
            patch.object(telegram_bot, "_poll_backoff", lambda failures, error: 0.05),
            patch.object(telegram_bot, "_jitter", lambda delay: min(delay, 0.05)),
            patch.object(telegram_bot, "LOCAL_CONFLICT_RETRY", 0.05),
        ]
        for item in self.patches:
            item.start()
        pgdata.save_preferences({"telegram_enabled": True, "telegram_bot_token": TOKEN,
                                 "telegram_chat_id": str(OWNER), "telegram_language": "ru",
                                 "telegram_stream_mode": "off"})

        self.patches.append(patch.object(telegram_bot, "_configure_bot", self._no_setup))
        self.patches[-1].start()
        self.api = Api()
        self.clients = []
        self.runner = None
        self.task = None
        telegram_bot._update_locks.clear()
        telegram_bot._generations.clear()

    async def _no_setup(self, *args, **kwargs):
        return None

    def factory(self):
        client = httpx.AsyncClient(transport=httpx.MockTransport(self.api.handler))
        self.clients.append(client)
        return client

    def start(self):
        self.runner = telegram_bot._BotRunner(client_factory=self.factory)
        self.task = asyncio.create_task(self.runner.run())
        return self.runner

    def bot_state(self):
        return pgdata.load_telegram_state().get("bot") or {}

    async def asyncTearDown(self):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.runner is not None:
            await self.runner.shutdown()
        for task in list(telegram_bot._retiring):
            task.cancel()
        await asyncio.gather(*list(telegram_bot._retiring), return_exceptions=True)
        for client in self.clients:
            await client.aclose()
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

class NetworkTest(RuntimeCase):
    async def test_network_errors_recreate_the_client_and_report_reconnecting(self):
        self.api.script["getUpdates"] = ["connect", "timeout", "protocol"]
        runner = self.start()
        self.assertTrue(await until(lambda: self.api.count("getUpdates") >= 4))
        self.assertTrue(await until(lambda: runner.status == "ok"))

        self.assertGreaterEqual(len(self.clients), 4)
        self.assertEqual(runner.failures, 0)

    async def test_reconnecting_status_has_reason_and_next_retry(self):
        self.api.script["getUpdates"] = ["connect"] * 50
        with patch.object(telegram_bot, "_poll_backoff", lambda failures, error: 30.0), \
                patch.object(telegram_bot, "_jitter", lambda delay: delay):
            self.start()
            self.assertTrue(await until(lambda: self.bot_state().get("status") == "reconnecting"))
        bot = self.bot_state()
        self.assertIn("ConnectError", bot["error"])
        self.assertGreater(bot["retry_at"], time.time() + 20)
        status = telegram_pairing.status()
        self.assertEqual(status["phase"], "reconnecting")
        self.assertTrue(status["alive"])
        self.assertGreater(status["retry_in"], 20)

    async def test_hanging_long_poll_hits_the_deadline(self):
        self.api.script["getUpdates"] = ["hang"]
        with patch.object(telegram_bot, "POLL_DEADLINE", 0.3):
            runner = self.start()
            self.assertTrue(await until(lambda: self.api.hanging == 1))
            self.assertTrue(await until(lambda: self.api.count("getUpdates") >= 3))
        self.assertTrue(await until(lambda: runner.status == "ok"))
        self.assertGreaterEqual(len(self.clients), 2)

    async def test_server_errors_and_flood_wait_do_not_stop_polling(self):
        self.api.script["getUpdates"] = [502, 429, 503]
        runner = self.start()
        self.assertTrue(await until(lambda: self.api.count("getUpdates") >= 4))
        self.assertTrue(await until(lambda: runner.status == "ok"))

    async def test_flood_wait_honours_retry_after(self):
        error = TelegramError("Too Many Requests", error_code=429, retry_after=7)
        self.assertEqual(ORIGINAL_BACKOFF(1, error), 7)
        self.assertEqual(ORIGINAL_BACKOFF(1, RuntimeError("x")), 5)
        self.assertEqual(ORIGINAL_BACKOFF(12, RuntimeError("x")), 60)

    async def test_unreadable_preferences_use_the_last_copy(self):
        runner = self.start()
        self.assertTrue(await until(lambda: runner.status == "ok"))
        calls = self.api.count("getUpdates")
        with patch.object(telegram_bot, "load_preferences", side_effect=PermissionError("locked")):
            self.assertTrue(await until(lambda: self.api.count("getUpdates") >= calls + 3))
        self.assertEqual(runner.status, "ok")

class ProtocolErrorTest(RuntimeCase):
    async def test_single_conflict_after_restart_is_not_reported(self):
        self.api.script["getUpdates"] = [409]
        runner = self.start()
        self.assertTrue(await until(lambda: self.api.count("getUpdates") >= 2))
        self.assertTrue(await until(lambda: runner.status == "ok"))
        self.assertNotEqual(self.bot_state().get("status"), "conflict")

    async def test_persistent_conflict_is_reported_and_retried(self):
        self.api.script["getUpdates"] = [409] * 4
        self.start()
        self.assertTrue(await until(lambda: self.bot_state().get("status") == "conflict"))
        self.assertEqual(telegram_pairing.status()["phase"], "conflict")
        self.assertTrue(await until(lambda: self.bot_state().get("status") == "ok"))

    async def test_revoked_token_is_parked_not_spun(self):
        self.api.script["getMe"] = [401] * 10
        runner = self.start()
        self.assertTrue(await until(lambda: self.bot_state().get("status") == "unauthorized"))
        await asyncio.sleep(0.4)
        self.assertEqual(self.api.count("getMe"), 1)
        self.assertEqual(telegram_pairing.status()["phase"], "bad_token")
        self.assertFalse(self.task.done())
        self.assertTrue(runner.rejected)

    async def test_token_revoked_while_polling(self):
        runner = self.start()
        self.assertTrue(await until(lambda: runner.status == "ok"))
        self.api.script["getUpdates"] = [401]
        self.assertTrue(await until(lambda: self.bot_state().get("status") == "unauthorized"))
        self.assertEqual(telegram_pairing.status()["phase"], "bad_token")

    async def test_handler_crash_never_stops_the_loop(self):
        seen = []

        async def flaky(client, token, message, prefs):
            seen.append(message.get("text"))
            if message.get("text") == "boom":
                raise RuntimeError("handler exploded")

        with patch.object(telegram_bot, "_handle_message", flaky):
            runner = self.start()
            self.assertTrue(await until(lambda: runner.status == "ok"))
            self.api.updates = [message_update(10, "boom")]
            self.assertTrue(await until(lambda: "boom" in seen))
            self.api.updates = [message_update(11, "after")]
            self.assertTrue(await until(lambda: "after" in seen))
        self.assertFalse(self.task.done())
        self.assertEqual(runner.offset, 12)

    async def test_malformed_update_is_skipped(self):
        runner = self.start()
        self.assertTrue(await until(lambda: runner.status == "ok"))
        self.api.updates = ["junk", {"update_id": "x"}, {"update_id": 20, "message": "nope"}]
        self.assertTrue(await until(lambda: runner.offset == 21))
        self.assertFalse(self.task.done())

    async def test_hung_handler_is_cancelled_and_frees_the_chat(self):
        seen = []

        async def slow(client, token, message, prefs):
            seen.append(message.get("text"))
            if message.get("text") == "hang":
                await asyncio.sleep(3600)

        with patch.object(telegram_bot, "_handle_message", slow), \
                patch.object(telegram_bot, "HANDLER_TIMEOUT", 0.3):
            runner = self.start()
            self.assertTrue(await until(lambda: runner.status == "ok"))
            self.api.updates = [message_update(30, "hang"), message_update(31, "next")]
            self.assertTrue(await until(lambda: "next" in seen, timeout=4))

class TokenAndLockTest(RuntimeCase):
    async def test_token_change_reactivates_with_the_new_token(self):
        runner = self.start()
        self.assertTrue(await until(lambda: runner.status == "ok" and runner.token == TOKEN))
        first_lock = runner.lock.fingerprint
        pgdata.save_preferences({"telegram_bot_token": TOKEN2})
        self.assertTrue(await until(lambda: runner.token == TOKEN2))
        self.assertEqual(self.api.count("getMe", TOKEN2), 1)
        self.assertTrue(await until(lambda: self.api.count("getUpdates", TOKEN2) >= 1))
        self.assertNotEqual(runner.lock.fingerprint, first_lock)
        self.assertEqual(self.bot_state()["username"], "memu2_bot")

    async def test_disable_is_confirmed_before_stopping(self):
        runner = self.start()
        self.assertTrue(await until(lambda: runner.status == "ok"))
        pgdata.save_preferences({"telegram_enabled": False})
        self.assertTrue(await until(lambda: runner.status == "disabled"))
        self.assertEqual(runner.token, "")
        self.assertIsNone(runner.lock)

    async def test_second_instance_with_the_same_token_waits(self):
        from app.telegram_pairing import token_fingerprint
        other = telegram_runtime.BotInstanceLock(token_fingerprint(TOKEN))
        self.assertTrue(other.acquire())
        try:
            runner = self.start()
            self.assertTrue(await until(lambda: self.bot_state().get("status") == "conflict_local"))
            self.assertEqual(self.api.count("getUpdates"), 0)
            status = telegram_pairing.status()
            self.assertEqual(status["phase"], "conflict")
            self.assertTrue(status["local_conflict"])
        finally:
            other.release()
        self.assertTrue(await until(lambda: runner.status == "ok"))

    def test_lock_is_per_token(self):
        a = telegram_runtime.BotInstanceLock("aaa")
        b = telegram_runtime.BotInstanceLock("aaa")
        c = telegram_runtime.BotInstanceLock("bbb")
        try:
            self.assertTrue(a.acquire())
            self.assertFalse(b.acquire())
            self.assertTrue(c.acquire())
            a.release()
            self.assertTrue(b.acquire())
        finally:
            for lock in (a, b, c):
                lock.release()

class WatchdogTest(RuntimeCase):
    async def _never(self):
        await asyncio.sleep(3600)

    async def test_clock_jump_restarts_the_session(self):
        runner = telegram_bot._BotRunner(client_factory=self.factory)
        task = asyncio.create_task(self._never())
        clock = {"wall": time.time()}
        fake_time = types.SimpleNamespace(monotonic=time.monotonic, time=lambda: clock["wall"])
        with patch.object(telegram_bot, "WATCHDOG_TICK", 0.05), patch.object(telegram_bot, "time", fake_time):
            watch = asyncio.create_task(telegram_bot._watch(runner, task, 0.0))
            await asyncio.sleep(0.12)
            clock["wall"] += 3600
            reason, _ = await asyncio.wait_for(watch, 2)
        task.cancel()
        self.assertEqual(reason, "wake")

    async def test_stuck_session_is_detected(self):
        runner = telegram_bot._BotRunner(client_factory=self.factory)
        runner.progress = time.monotonic() - 10_000
        task = asyncio.create_task(self._never())
        with patch.object(telegram_bot, "WATCHDOG_TICK", 0.05):
            reason, _ = await asyncio.wait_for(telegram_bot._watch(runner, task, 0.0), 2)
        task.cancel()
        self.assertEqual(reason, "stall")

    async def test_long_backoff_sleep_is_not_a_stall(self):
        runner = telegram_bot._BotRunner(client_factory=self.factory)
        runner.progress = time.monotonic() - 10_000
        runner.idle_until = time.monotonic() + 300
        task = asyncio.create_task(self._never())
        with patch.object(telegram_bot, "WATCHDOG_TICK", 0.05):
            watch = asyncio.create_task(telegram_bot._watch(runner, task, 0.0))
            await asyncio.sleep(0.3)
            self.assertFalse(watch.done())
            watch.cancel()
        task.cancel()

    async def test_restart_request_from_settings(self):
        runner = telegram_bot._BotRunner(client_factory=self.factory)
        task = asyncio.create_task(self._never())
        with patch.object(telegram_bot, "WATCHDOG_TICK", 0.05):
            watch = asyncio.create_task(telegram_bot._watch(runner, task, 0.0))
            await asyncio.sleep(0.1)
            stamp = telegram_pairing.request_restart()
            reason, handled = await asyncio.wait_for(watch, 2)
        task.cancel()
        self.assertEqual(reason, "restart")
        self.assertEqual(handled, stamp)

    async def test_heartbeat_keeps_the_bot_alive_while_reconnecting(self):
        runner = telegram_bot._BotRunner(client_factory=self.factory)
        runner.status_token = TOKEN
        runner.status, runner.error = "reconnecting", "x"
        runner.last_heartbeat = 0
        task = asyncio.create_task(self._never())
        with patch.object(telegram_bot, "WATCHDOG_TICK", 0.05):
            watch = asyncio.create_task(telegram_bot._watch(runner, task, 0.0))
            self.assertTrue(await until(lambda: self.bot_state().get("alive", 0) > 0))
            watch.cancel()
        task.cancel()
        self.assertEqual(telegram_pairing.status()["phase"], "reconnecting")

class SupervisorTest(RuntimeCase):
    async def test_supervisor_restarts_a_crashed_session(self):
        original = telegram_bot._BotRunner.run
        runs = {"n": 0}

        async def crashing(runner):
            runs["n"] += 1
            if runs["n"] == 1:
                raise RuntimeError("unexpected")
            await original(runner)

        with patch.object(telegram_bot._BotRunner, "run", crashing), \
                patch.object(telegram_bot, "WATCHDOG_TICK", 0.05), \
                patch.object(telegram_bot, "_new_client", self.factory), \
                patch.object(telegram_bot, "_jitter", lambda delay: 0.05):
            loop_task = asyncio.create_task(telegram_bot.telegram_loop())
            try:
                self.assertTrue(await until(lambda: runs["n"] >= 2 and self.api.count("getUpdates") >= 1))
                self.assertTrue(await until(lambda: self.bot_state().get("status") == "ok"))
            finally:
                loop_task.cancel()
                await asyncio.gather(loop_task, return_exceptions=True)
        self.assertEqual(self.bot_state().get("status"), "stopped")
        self.assertFalse(telegram_pairing.status()["alive"])

    async def test_restart_request_restarts_and_retries_a_rejected_token(self):
        self.api.script["getMe"] = [401]
        with patch.object(telegram_bot, "WATCHDOG_TICK", 0.05), \
                patch.object(telegram_bot, "_new_client", self.factory):
            loop_task = asyncio.create_task(telegram_bot.telegram_loop())
            try:
                self.assertTrue(await until(lambda: self.bot_state().get("status") == "unauthorized"))
                telegram_pairing.request_restart()
                self.assertTrue(await until(lambda: self.bot_state().get("status") == "ok"))
                self.assertEqual(self.api.count("getMe"), 2)
                self.assertGreaterEqual(self.bot_state().get("restarts"), 1)
            finally:
                loop_task.cancel()
                await asyncio.gather(loop_task, return_exceptions=True)

class AnswerTimeoutTest(RuntimeCase):
    async def test_silent_model_is_given_up_and_chat_is_freed(self):
        async def silent(*args, **kwargs):
            yield {"choices": [{"delta": {"content": "Нач"}}]}
            await asyncio.sleep(3600)

        client = self.factory()
        fake_service = types.SimpleNamespace(stream_completion=silent)
        with patch.object(telegram_bot, "ANSWER_IDLE_TIMEOUT", 0.2), \
                patch.object(telegram_bot, "_model_entries", lambda prefs=None: [("m1", "custom")]), \
                patch("app.chat.chat_service", fake_service):
            prefs = pgdata.load_preferences()
            await asyncio.wait_for(telegram_bot._answer_ai(client, TOKEN, str(OWNER), "привет", prefs), 5)
        self.assertNotIn(str(OWNER), telegram_bot._generations)
        sent = [call for call in self.api.calls if call[0] == "sendMessage"]
        self.assertTrue(sent)

class StatusApiTest(RuntimeCase):
    async def test_stalled_when_proxy_is_up_but_no_heartbeat(self):
        from fastapi import FastAPI
        from app import telegram_settings_api

        def health(request):
            return httpx.Response(200, json={"ok": True})

        proxy_client = httpx.AsyncClient(transport=httpx.MockTransport(health))
        self.clients.append(proxy_client)
        app = FastAPI()
        telegram_settings_api._proxy_cache.update(at=0.0, online=False)
        telegram_settings_api.register_telegram_routes(app, proxy_client, proxy_url=lambda: "http://proxy")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://pg") as http:
            status = (await http.get("/api/pg/telegram/status?probe=0")).json()
            self.assertEqual(status["phase"], "stalled")
            self.assertTrue(status["proxy_online"])
            restarted = (await http.post("/api/pg/telegram/restart")).json()
            self.assertTrue(restarted["ok"])
        self.assertGreater(pgdata.load_telegram_state()["control"]["restart"], 0)

    async def test_offline_only_when_proxy_is_down(self):
        from fastapi import FastAPI
        from app import telegram_settings_api

        def down(request):
            raise httpx.ConnectError("refused", request=request)

        proxy_client = httpx.AsyncClient(transport=httpx.MockTransport(down))
        self.clients.append(proxy_client)
        app = FastAPI()
        telegram_settings_api._proxy_cache.update(at=0.0, online=False)
        telegram_settings_api.register_telegram_routes(app, proxy_client, proxy_url=lambda: "http://proxy")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://pg") as http:
            status = (await http.get("/api/pg/telegram/status?probe=0")).json()
        self.assertEqual(status["phase"], "offline")
        self.assertFalse(status["proxy_online"])

if __name__ == "__main__":
    unittest.main()
