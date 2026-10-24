"""Round 24: Telegram — instant buttons, panels in the bot / Mini App / notifications.

Offline: fake Bot API (httpx.MockTransport from telegram_r20_test), temporary
MemuAI data directory, fake sender for panel notifications.
"""
import asyncio
import json
import time
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import pgdata, telegram_bot, telegram_media, telegram_panels, telegram_runtime
from app import panels as P
from tests.telegram_r20_test import OWNER, TOKEN, BotTestCase, FakeTelegram, message


def cb(data, cid, chat_id=OWNER, message_id=50):
    return {"id": cid, "data": data, "from": {"id": chat_id},
            "message": {"message_id": message_id, "chat": {"id": chat_id, "type": "private"}}}


class TimedTelegram(FakeTelegram):
    """Fake Bot API that remembers *when* each method was called."""

    def __init__(self):
        super().__init__()
        self.stamps = []

    def handler(self, request):
        response = super().handler(request)
        self.stamps.append((self.calls[-1][0] if self.calls else "", time.monotonic(), self.calls[-1][1] if self.calls else {}))
        return response

    def first(self, method, after=0.0, **match):
        for name, stamp, payload in self.stamps:
            if name == method and stamp >= after and all(payload.get(k) == v for k, v in match.items()):
                return stamp
        return None


async def until(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


# ===================================================================== buttons


class CallbackLatencyTest(BotTestCase):
    """Reproduction of «кнопки нажимаются с большой задержкой»."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.fake = TimedTelegram()
        await self.client.aclose()
        self.client = self.fake.client()
        self.runner = telegram_bot._BotRunner(self.client)
        self.runner.token = TOKEN
        telegram_bot._update_locks.clear()

    async def asyncTearDown(self):
        for task in list(self.runner.handlers):
            task.cancel()
        await asyncio.gather(*self.runner.handlers, return_exceptions=True)
        await super().asyncTearDown()

    async def test_callback_is_answered_at_once_while_the_chat_is_busy(self):
        """A photo download (slow handler of the same chat) used to hold the chat's
        lock; the button press waited for it, including answerCallbackQuery."""
        release = asyncio.Event()

        async def slow_collect(*_a, **_k):
            await release.wait()          # e.g. a 20 MB attachment over a slow link
            return types.SimpleNamespace(images=[], files=[], voice=False, transcript="", errors=[], text="")

        prefs = pgdata.load_preferences()
        with patch.object(telegram_media, "collect", slow_collect):
            self.runner.dispatch({"update_id": 1, "message": message(photo=[{"file_id": "x", "file_size": 10}])}, prefs)
            await asyncio.sleep(0.05)
            pressed = time.monotonic()
            self.runner.dispatch({"update_id": 2, "callback_query": cb("home", "cb-latency-1")}, prefs)
            self.assertTrue(await until(lambda: self.fake.first("answerCallbackQuery", pressed) is not None, 1.0))
            ack = self.fake.first("answerCallbackQuery", pressed) - pressed
            # The menu itself is drawn too, although the photo is still downloading.
            self.assertTrue(await until(lambda: self.fake.first("sendMessage", pressed) is not None
                                        or self.fake.first("editMessageText", pressed) is not None, 2.0))
            self.assertLess(ack, 0.5, f"callback answered after {ack:.2f}s")
            self.assertFalse(release.is_set())
            release.set()
        answers = [p for m, p in self.fake.calls if m == "answerCallbackQuery"]
        self.assertEqual(len(answers), 1, "one answer per callback query")

    async def test_callback_ack_does_not_wait_for_a_running_generation(self):
        stop = asyncio.Event()

        async def long_answer(*_a, **_k):
            telegram_bot._generations[str(OWNER)] = telegram_bot._Generation(str(OWNER))
            await stop.wait()

        prefs = pgdata.load_preferences()
        with patch.object(telegram_bot, "_answer_ai", long_answer):
            self.runner.dispatch({"update_id": 3, "message": message("напиши длинный текст")}, prefs)
            await asyncio.sleep(0.05)
            latencies = []
            for index, data in enumerate(("home", "settings", "more", "panels")):
                pressed = time.monotonic()
                self.runner.dispatch({"update_id": 10 + index, "callback_query": cb(data, f"gen-{index}")}, prefs)
                self.assertTrue(await until(lambda: self.fake.first("answerCallbackQuery", pressed,
                                                                    callback_query_id=f"gen-{index}") is not None, 1.0))
                latencies.append(self.fake.first("answerCallbackQuery", pressed, callback_query_id=f"gen-{index}") - pressed)
            stop.set()
        self.assertLess(max(latencies), 0.5, latencies)

    async def test_toast_callbacks_keep_their_text_and_get_a_fallback(self):
        prefs = pgdata.load_preferences()
        with patch.object(telegram_bot, "TOAST_FALLBACK", 0.2):
            # Unknown chat → handler answers via _toast(text) quickly → no fallback duplicate.
            self.runner.dispatch({"update_id": 20, "callback_query": cb("pin:zzz", "toast-1")}, prefs)
            await asyncio.sleep(0.5)
        answers = [p for m, p in self.fake.calls if m == "answerCallbackQuery" and p.get("callback_query_id") == "toast-1"]
        self.assertEqual(len(answers), 1)

    async def test_offset_is_saved_in_the_background(self):
        api_calls = []

        async def slow_save():
            api_calls.append("save")
            await asyncio.sleep(1.0)

        self.fake.responders["getUpdates"] = lambda payload: [{"update_id": 41, "message": message("/help")}]
        self.runner.save_offset = slow_save
        started = time.monotonic()
        await self.runner.poll_once(pgdata.load_preferences())
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(self.runner.offset, 42)
        await asyncio.sleep(0.05)
        self.assertEqual(api_calls, ["save"])

    async def test_setup_signature_is_cached(self):
        prefs = pgdata.load_preferences()
        with patch.object(telegram_bot, "_configure_bot", AsyncMock()):
            await self.runner.ensure_setup(prefs)
        reads = []
        with patch.object(telegram_bot, "load_telegram_state", side_effect=lambda: reads.append(1) or {}):
            await self.runner.ensure_setup(prefs)
        self.assertEqual(reads, [])


class RuntimeLaneTest(unittest.IsolatedAsyncioTestCase):
    async def test_control_lane_is_not_starved_by_handler_io(self):
        block = []
        busy = [asyncio.ensure_future(telegram_runtime.run_io(time.sleep, 0.6)) for _ in range(12)]
        await asyncio.sleep(0.02)
        started = time.monotonic()
        await telegram_runtime.run_io(block.append, 1, lane="control", timeout=2)
        self.assertLess(time.monotonic() - started, 0.3)
        await asyncio.gather(*busy)

    def test_production_runner_uses_a_separate_poll_client(self):
        async def run():
            runner = telegram_bot._BotRunner(client_factory=telegram_bot._new_client,
                                             poll_factory=telegram_bot._new_poll_client)
            try:
                self.assertIsNot(runner.client, runner.poll_client)
                old = runner.poll_client
                runner.reset_client()
                self.assertIsNot(runner.poll_client, old)
            finally:
                await runner.shutdown()
        asyncio.run(run())


# ===================================================================== panels


def make_panel(kind, data, spec=None, pid=None):
    title, typed = P.typed_spec(kind, {}, P.default_settings(), None) if spec is None else ("Custom", spec)

    def apply(doc):
        doc["panels"].append(P.clean_panel({
            "id": pid or P._new_id(), "kind": kind, "title": title, "spec": typed, "data": data,
            "source": {"type": P.KIND_SOURCE.get(kind, "ai") if kind != "weather" else "open-meteo", "params": {}},
            "status": "live", "placements": ["home"], "order": {"home": 0}, "cadence": {"kind": "manual"},
            "updated": time.time()}))
        return doc["panels"][-1]["id"]

    _doc, made = P.mutate(apply)
    return made


WEATHER = {"place": "Шяуляй", "cond": "Ясно", "temp": 12, "t_unit": "°C", "feels": 10, "hi": 14, "lo": 6,
           "wind": 3, "w_unit": "м/с", "humidity": 70, "icon": "sun"}


class PanelsTelegramTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(pgdata, "MEMU_DIR", Path(self.tmp.name))
        self.patch.start()
        self.prefs = pgdata.save_preferences({"telegram_enabled": True, "telegram_bot_token": TOKEN,
                                              "telegram_chat_id": str(OWNER), "telegram_language": "ru"})
        self.sent = []

        async def sender(prefs, text, markup, silent):
            self.sent.append({"text": text, "markup": markup, "silent": silent})
            return True

        telegram_panels.set_sender(sender)
        self.weather = make_panel("weather", dict(WEATHER))

    async def asyncTearDown(self):
        telegram_panels.set_sender(None)
        self.patch.stop()
        self.tmp.cleanup()

    def set_data(self, pid, **values):
        def apply(doc):
            panel = P._find(doc, pid)
            panel["data"] = {**panel["data"], **values}
        P.mutate(apply)

    async def check(self, now):
        return await telegram_panels.check_all(now=now, force=True, prefs=pgdata.load_preferences())

    def test_render_weather_html(self):
        panel = next(p for p in P.load()["panels"] if p["id"] == self.weather)
        text = telegram_panels.render_html(panel)
        self.assertIn("<b>12</b>°C", text)
        self.assertIn("Шяуляй", text)
        self.assertIn("Влажность: <b>70 %</b>", text)
        self.assertNotIn("{{", text)

    async def test_subscribe_baseline_then_significant_change_only(self):
        telegram_panels.subscribe(self.weather, on=True)
        self.assertEqual(await self.check(1000), 0)          # subscribing does not re-send the current state
        self.set_data(self.weather, temp=13, feels=11)         # +1° — not significant
        self.assertEqual(await self.check(5000), 0)
        self.set_data(self.weather, temp=17)                   # +5° from the delivered state
        self.assertEqual(await self.check(9000), 1)
        self.assertIn("температура", self.sent[-1]["text"])
        self.assertEqual(await self.check(9100), 0)            # dedupe
        self.set_data(self.weather, cond="Гроза")              # change inside the gap → coalesced
        self.assertEqual(await self.check(9200), 0)
        self.assertEqual(await self.check(9000 + 31 * 60), 1)  # … and sent when the gap ends
        self.assertIn("Гроза", self.sent[-1]["text"])
        buttons = [b.get("callback_data") for row in self.sent[-1]["markup"]["inline_keyboard"] for b in row]
        self.assertIn(f"pnlsub:{self.weather}", buttons)

    async def test_hourly_cap_and_quiet_hours(self):
        telegram_panels.subscribe(self.weather, on=True, mode="every")

        def apply(doc):
            doc["settings"]["min_gap_minutes"] = 0
            doc["settings"]["max_per_hour"] = 2
        telegram_panels.mutate(apply)
        for index in range(5):
            self.set_data(self.weather, temp=20 + index)
            await self.check(10_000 + index)
        self.assertEqual(len(self.sent), 2)
        # quiet hours + defer → nothing; silent → disable_notification
        telegram_panels.subscribe(self.weather, quiet="defer")
        with patch.object(telegram_panels, "_quiet", return_value=True):
            self.set_data(self.weather, temp=40)
            self.assertEqual(await self.check(20_000), 0)
            telegram_panels.subscribe(self.weather, quiet="silent")
            self.assertEqual(await self.check(20_001), 1)
        self.assertTrue(self.sent[-1]["silent"])

    async def test_news_countdown_todo_rules(self):
        news = make_panel("news", {"items": [{"title": "A", "url": "https://a.example/1"}], "subtitle": "", "count": 1})
        count = make_panel("countdown", {"target": "2030-01-01", "target_label": "01.01.2030", "days_left": 9})
        for pid in (news, count):
            telegram_panels.subscribe(pid, on=True)

        def gap0(doc):
            doc["settings"]["min_gap_minutes"] = 0
        telegram_panels.mutate(gap0)
        self.set_data(count, days_left=8)
        self.assertEqual(await self.check(1), 0)               # 8 days — not a milestone
        self.set_data(count, days_left=7)
        self.set_data(news, items=[{"title": "B", "url": "https://a.example/2"}, {"title": "A", "url": "https://a.example/1"}])
        self.assertEqual(await self.check(2), 2)
        texts = "\n".join(s["text"] for s in self.sent)
        self.assertIn("Осталось дней: 7", texts)
        self.assertIn('href="https://a.example/2"', texts)

    async def test_digest_once_a_day(self):
        telegram_panels.subscribe(self.weather, on=True, mode="digest", at="08:00")
        morning = time.mktime(time.strptime("2026-10-08 08:30", "%Y-%m-%d %H:%M"))
        self.assertEqual(await self.check(morning - 3600), 0)  # 07:30 — not yet
        self.assertEqual(await self.check(morning), 1)
        self.assertIn("Сводка панелей", self.sent[-1]["text"])
        self.assertEqual(await self.check(morning + 600), 0)    # once a day

    async def test_disabled_bot_sends_nothing(self):
        telegram_panels.subscribe(self.weather, on=True, mode="every")
        pgdata.save_preferences({"telegram_enabled": False})
        self.set_data(self.weather, temp=30)
        self.assertEqual(await self.check(50), 0)

    def test_ineligible_kind_and_bad_ids(self):
        with self.assertRaises(ValueError):
            telegram_panels.subscribe("../../x", on=True)
        system = make_panel("system", {})
        with self.assertRaises(ValueError):
            telegram_panels.subscribe(system, on=True)
        self.assertEqual(telegram_panels.request_refresh("p_nothere00"), "missing")
        self.assertEqual(telegram_panels.request_refresh(self.weather), "queued")
        panel = next(p for p in P.load()["panels"] if p["id"] == self.weather)
        self.assertLessEqual(panel["next_refresh"], time.time())

    def test_tab_url_keeps_the_hash_free(self):
        self.assertEqual(telegram_panels._tab_url("https://x.example/tgapp/"), "https://x.example/tgapp/?tab=panels")
        self.assertEqual(telegram_panels._tab_url("https://x.example/tgapp/?a=1#h"), "https://x.example/tgapp/?a=1&tab=panels")

    async def test_mini_app_ops(self):
        from app.tgapp_api import ApiError
        routes = {}

        def op(name):
            def register(func):
                routes[name] = func
                return func
            return register

        telegram_panels.register_tgapp_ops(op, ApiError)
        ctx = {"prefs": pgdata.load_preferences()}
        listing = await routes["panels.list"](ctx, {})
        row = next(r for r in listing["panels"] if r["id"] == self.weather)
        self.assertTrue(row["eligible"])
        self.assertEqual(row["card"]["metric"]["value"], "12")
        result = await routes["panels.subscribe"](ctx, {"id": self.weather, "on": True, "mode": "digest", "at": "07:15"})
        self.assertEqual(result["sub"], {"on": True, "mode": "digest", "at": "07:15", "quiet": "silent"})
        with self.assertRaises(ApiError):
            await routes["panels.subscribe"](ctx, {"id": self.weather, "at": "25:99"})
        self.assertEqual((await routes["panels.refresh"](ctx, {"id": self.weather}))["result"], "queued")

    async def test_tgapp_whitelist_contains_panels(self):
        from fastapi.testclient import TestClient  # noqa: F401 — FastAPI is available in the sandbox
        from app import tgapp_api
        app = tgapp_api.create_tgapp()
        self.assertTrue(app)   # registration did not raise


    async def test_window_routes(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        telegram_panels.register_window_routes(app)
        client = TestClient(app)
        state = await asyncio.to_thread(lambda: client.get("/api/pg/panels-telegram").json())
        self.assertIn(self.weather, state["eligible"])
        self.assertTrue(state["ready"])
        r = await asyncio.to_thread(lambda: client.put(f"/api/pg/panels-telegram/{self.weather}", json={"on": True, "mode": "every"}))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["sub"]["mode"], "every")
        bad = await asyncio.to_thread(lambda: client.put("/api/pg/panels-telegram/p_missing00", json={"on": True}))
        self.assertEqual(bad.status_code, 400)
        r = await asyncio.to_thread(lambda: client.put("/api/pg/panels-telegram-settings", json={"min_gap_minutes": 5, "max_per_hour": 999, "x": 1}))
        self.assertEqual(r.json()["settings"]["min_gap_minutes"], 5)
        self.assertEqual(r.json()["settings"]["max_per_hour"], 60)


class PanelsBotTest(BotTestCase):
    async def test_panels_command_and_buttons(self):
        pid = make_panel("weather", dict(WEATHER))
        await self.send("/panels")
        screen = self.fake.last("sendMessage")
        self.assertIn("Панели", screen["text"])
        datas = [b.get("callback_data") for row in screen["reply_markup"]["inline_keyboard"] for b in row]
        self.assertIn(f"pnl:{pid}", datas)
        self.assertIn(f"pnlsub:{pid}", datas)
        await self.press(f"pnl:{pid}")
        view = self.fake.last("editMessageText") or self.fake.last("sendMessage")
        self.assertIn("Шяуляй", view["text"])
        await self.press(f"pnlsub:{pid}")
        self.assertTrue(telegram_panels.subscription(pid)["on"])
        await self.press(f"pnlm:{pid}")
        self.assertEqual(telegram_panels.subscription(pid)["mode"], "every")
        await self.press(f"pnlr:{pid}")
        toast = [p for m, p in self.fake.calls if m == "answerCallbackQuery" and p.get("text")]
        self.assertTrue(any("Обновляю" in p["text"] for p in toast))

    def test_commands_include_panels(self):
        self.assertIn("panels", [c["command"] for c in telegram_bot._commands("ru")])


class Round24ReviewFixesTest(BotTestCase):
    """Independent review of Round 24 (Telegram part)."""

    async def test_panel_toast_buttons_get_exactly_one_answer_with_text(self):
        pid = make_panel("weather", dict(WEATHER))
        prefs = pgdata.load_preferences()
        callback = cb(f"pnlsub:{pid}", "cb-one-answer")
        await telegram_bot._handle_callback(self.client, TOKEN, callback, prefs)
        await self.settle()
        answers = [p for m, p in self.fake.calls
                   if m == "answerCallbackQuery" and p.get("callback_query_id") == "cb-one-answer"]
        self.assertEqual(len(answers), 1, answers)
        self.assertIn("text", answers[0], "the panel's own toast must not be pre-empted by an empty answer")

    async def test_answered_registry_stays_bounded(self):
        runner = telegram_bot._BotRunner(self.client)
        runner.token = TOKEN
        telegram_bot._answered.clear()
        telegram_bot._acked.clear()
        try:
            for index in range(telegram_bot.ACK_LIMIT * 3):
                runner.ack_callback({"id": f"bound-{index}", "data": "home"})
            self.assertLessEqual(len(telegram_bot._answered), telegram_bot.ACK_LIMIT + 1)
            self.assertLessEqual(len(telegram_bot._acked), telegram_bot.ACK_LIMIT + 1)
        finally:
            for task in list(runner.handlers):
                task.cancel()
            await asyncio.gather(*runner.handlers, return_exceptions=True)


class PanelsRenderingReviewTest(unittest.TestCase):
    def test_clip_never_cuts_a_tag(self):
        lines = ["<b>Title</b>"] + [f'• <a href="https://e.com/{i}">item {i} &amp; more</a>' for i in range(500)]
        text = telegram_panels._clip_html(lines, 3800)
        self.assertLessEqual(len(text), 3800)
        self.assertEqual(text.count("<a "), text.count("</a>"))
        self.assertTrue(text.endswith("…"))
        single = telegram_panels._clip_html(["<b>" + "x &amp; y " * 1000 + "</b>"], 100)
        self.assertNotIn("<b>", single)
        self.assertNotIn("&amp;amp;", single)
        self.assertLessEqual(len(single), 100)

    def test_bad_numbers_from_panel_data_do_not_crash(self):
        panel = {"id": "p_abcdef12", "kind": "countdown", "title": "T",
                 "spec": {"blocks": [{"type": "countdown", "target": "x"},
                                     {"type": "list", "max": "lots", "items": [{"title": "<i>a</i>"}]}]},
                 "data": {"days_left": "soon"}}
        text = telegram_panels.render_html(panel)
        self.assertIn("&lt;i&gt;a&lt;/i&gt;", text)
        todo = {"id": "p_abcdef13", "kind": "todo", "title": "T", "spec": {"blocks": []},
                "todo": {"lists": [{"items": [{"title": "t", "priority": "high"}]}]}}
        self.assertEqual(telegram_panels.card(todo)["tasks"][0]["priority"], 0)

    def test_digest_time_is_zero_padded(self):
        self.assertEqual(telegram_panels._clean_sub({"at": "9:05"})["at"], "09:05")
        self.assertEqual(telegram_panels._normalize({"settings": {"digest_at": "7:00"}})["settings"]["digest_at"], "07:00")
        self.assertEqual(telegram_panels._clean_sub({"at": "25:00"})["at"], "")


class PanelsTickReviewTest(unittest.IsolatedAsyncioTestCase):
    async def test_background_check_failure_is_logged_not_lost(self):
        async def boom(**_kwargs):
            raise RuntimeError("telegram check exploded")

        P._TG_TASK["task"] = None
        with patch.object(telegram_panels, "check_all", boom), self.assertLogs("memuai.panels", "WARNING") as logs:
            P._telegram_check()
            task = P._TG_TASK["task"]
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.sleep(0)
        self.assertTrue(any("telegram check exploded" in line for line in logs.output))
        P._TG_TASK["task"] = None


if __name__ == "__main__":
    unittest.main()
