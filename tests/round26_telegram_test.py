"""Round 26: «Telegram-бот очень сильно лагает».

Offline: a fake Bot API with network latency (async ``httpx.MockTransport``),
temporary MemuAI data directory, stub model streams. Timing thresholds are
generous (CI machines are slow) but each one fails on the old behaviour.
The live measurements (real Proxy + fake Bot API over HTTP) are in
UPDATE_ROUND26 / the r26-telegram notes.
"""
import asyncio
import json
import time
import unittest
from unittest.mock import patch

import httpx

from app import telegram_bot, telegram_format, telegram_panels, telegram_stream
from app.telegram_api import BotApi
from app.telegram_stream import StreamReply
from tests.telegram_r20_test import OWNER, TOKEN, BotTestCase, FakeTelegram, message


class LatencyTelegram(FakeTelegram):
    """Fake Bot API: every call takes ``latency`` s and is time-stamped."""

    def __init__(self, latency=0.05, slow=None):
        super().__init__()
        self.latency = latency
        self.slow = dict(slow or {})
        self.stamps = []

    async def ahandler(self, request):
        method = request.url.path.rsplit("/", 1)[-1]
        started = time.monotonic()
        await asyncio.sleep(self.slow.get(method, self.latency))
        response = self.handler(request)
        self.stamps.append((method, started, time.monotonic(), self.calls[-1][1]))
        return response

    def client(self):
        return httpx.AsyncClient(transport=httpx.MockTransport(self.ahandler))

    def first(self, method, *, after=0.0):
        return next((start for name, start, _end, _p in self.stamps if name == method and start >= after), None)


async def until(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


def tokens(count, text="слово "):
    async def stream(*_args, **_kwargs):
        for index in range(count):
            if index % 50 == 0:
                await asyncio.sleep(0)
            yield {"choices": [{"delta": {"content": text}}]}
    return stream


class StreamCleaningTest(unittest.IsolatedAsyncioTestCase):
    """The answer was cleaned (3 regexes over the whole text) on every token."""

    async def test_prepare_runs_per_frame_not_per_token(self):
        fake = FakeTelegram()
        client = fake.client()
        calls = []

        def prepare(text):
            calls.append(len(text))
            return text.replace("[MEM:x]", "")

        stream = StreamReply(BotApi(client, TOKEN), str(OWNER), mode="edit", prepare=prepare)
        await stream.start()
        for _ in range(3000):
            stream.append("ab ")
        stream.append("[MEM:x]")
        await stream.flush()
        await stream._stop_ticker()
        await client.aclose()
        self.assertLessEqual(len(calls), 3)
        self.assertEqual(stream.text, "ab " * 3000)
        edits = [p for m, p in fake.calls if m == "editMessageText"]
        self.assertNotIn("MEM", edits[-1]["text"])

    async def test_update_still_replaces_the_whole_text(self):
        fake = FakeTelegram()
        client = fake.client()
        stream = StreamReply(BotApi(client, TOKEN), str(OWNER), mode="edit")
        stream.update("один")
        stream.update("один два")
        await stream.flush()
        await client.aclose()
        self.assertEqual(stream.text, "один два")


class AnswerLatencyTest(BotTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.fake = LatencyTelegram(0.02)
        await self.client.aclose()
        self.client = self.fake.client()

    async def test_long_answer_streams_in_linear_time(self):
        # 6000 tokens: the old per-token join+clean took several seconds of
        # event-loop time here; per frame it is a few hundred milliseconds.
        with patch("app.chat.chat_service.stream_completion", tokens(6000, "слово [MEM:a] ")):
            started = time.perf_counter()
            await self.send("длинный ответ")
            elapsed = time.perf_counter() - started
        final = self.fake.last("sendMessage")
        self.assertNotIn("MEM", json.dumps(self.fake.calls, ensure_ascii=False))
        self.assertIn("слово", final["text"])
        self.assertLess(elapsed, 4.0)

    async def test_first_reaction_does_not_wait_for_the_chat_history(self):
        await self._set_mode("draft")
        slow = {}
        original = telegram_bot._current_chat

        def slow_current_chat(session):
            time.sleep(0.6)          # a big chats.json
            slow["done"] = time.monotonic()
            return original(session)

        with patch.object(telegram_bot, "_current_chat", slow_current_chat), \
                patch("app.chat.chat_service.stream_completion", tokens(5)):
            sent = time.monotonic()
            await self.send("привет")
        draft = self.fake.first("sendMessageDraft")
        self.assertIsNotNone(draft)
        self.assertLess(draft - sent, 0.3)
        self.assertLess(draft, slow["done"])

    async def test_answer_is_delivered_while_the_turn_is_saved(self):
        marks = {}
        original = telegram_bot._persist_turn

        def slow_persist(*args):
            marks["registered"] = str(OWNER) in telegram_bot._generations
            time.sleep(0.8)          # rewriting a big chats.json
            marks["saved"] = time.monotonic()
            return original(*args)

        with patch.object(telegram_bot, "_persist_turn", slow_persist), \
                patch("app.chat.chat_service.stream_completion", tokens(5)):
            await self.send("привет")
        final = next(start for name, start, _e, payload in self.fake.stamps
                     if name == "sendMessage" and "retry:" in json.dumps(payload.get("reply_markup") or {}))
        self.assertLess(final, marks["saved"])
        self.assertTrue(marks["registered"])      # ↻ cannot start before the turn is saved
        self.assertNotIn(str(OWNER), telegram_bot._generations)
        chats = telegram_bot._chat_rows("default")
        self.assertTrue(any(len(chat.get("messages") or []) == 2 for chat in chats))

    async def test_failed_save_does_not_lose_the_answer(self):
        def broken(*_args):
            raise OSError("disk full")

        with patch.object(telegram_bot, "_persist_turn", broken), \
                patch("app.chat.chat_service.stream_completion", tokens(3, "ответ ")):
            await self.send("привет")
        self.assertTrue(any("ответ" in text for text in self.fake.sent_texts()))
        self.assertNotIn(str(OWNER), telegram_bot._generations)

    async def test_history_error_removes_the_placeholder(self):
        await self._set_mode("edit")

        def broken(_session):
            raise OSError("locked")

        with patch.object(telegram_bot, "_current_chat", broken):
            await self.send("привет")
        self.assertIn("deleteMessage", self.fake.methods())
        self.assertIn("Не получилось получить ответ", self.fake.sent_texts()[-1])
        self.assertNotIn(str(OWNER), telegram_bot._generations)

    async def _set_mode(self, mode):
        from app import pgdata
        pgdata.save_preferences({"telegram_stream_mode": mode})


class ScreenLatencyTest(BotTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.fake = LatencyTelegram(0.02, slow={"deleteMessage": 0.8})
        await self.client.aclose()
        self.client = self.fake.client()

    async def test_old_card_is_deleted_in_the_background(self):
        prefs = telegram_bot.load_preferences()
        await telegram_bot._screen(self.client, TOKEN, str(OWNER), "один", {"inline_keyboard": []})
        started = time.monotonic()
        await telegram_bot._screen(self.client, TOKEN, str(OWNER), "два", {"inline_keyboard": []})
        self.assertLess(time.monotonic() - started, 0.5)
        await self.settle()
        self.assertIn("deleteMessage", self.fake.methods())
        self.assertTrue(prefs)

    async def test_reply_keyboard_tap_shows_the_screen_first(self):
        await self.send("/start")
        self.fake.stamps.clear()
        before = time.monotonic()
        prefs = telegram_bot.load_preferences()
        await telegram_bot._handle_message(self.client, TOKEN, message("💬 Чаты", message_id=77), prefs)
        shown = self.fake.first("sendMessage", after=before) or self.fake.first("editMessageText", after=before)
        self.assertIsNotNone(shown)
        self.assertLess(shown - before, 0.5)
        await self.settle()
        deleted = [p for m, p in self.fake.calls if m == "deleteMessage" and p.get("message_id") == 77]
        self.assertTrue(deleted)


class RunnerLatencyTest(BotTestCase):
    """A batch of updates through the real dispatcher with network latency."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.fake = LatencyTelegram(0.05)
        await self.client.aclose()
        self.client = self.fake.client()

    async def test_batch_of_commands_and_a_button_during_an_answer(self):
        runner = telegram_bot._BotRunner(self.client)
        runner.token = TOKEN
        prefs = telegram_bot.load_preferences()

        async def slow_answer(*_args, **_kwargs):
            await asyncio.sleep(0.3)
            for _ in range(40):
                await asyncio.sleep(0.05)
                yield {"choices": [{"delta": {"content": "текст "}}]}

        with patch("app.chat.chat_service.stream_completion", slow_answer):
            start = time.monotonic()
            runner.dispatch({"update_id": 1, "message": message("вопрос", message_id=10)}, prefs)
            await until(lambda: telegram_bot._generations, 2)
            press = time.monotonic()
            runner.dispatch({"update_id": 2, "callback_query": {
                "id": "cb-r26", "data": "home", "from": {"id": OWNER},
                "message": {"message_id": 50, "chat": {"id": OWNER, "type": "private"}}}}, prefs)
            for index, command in enumerate(["/help", "/models", "/settings"]):
                runner.dispatch({"update_id": 3 + index, "message": message(command, message_id=20 + index)}, prefs)
            self.assertTrue(await until(lambda: self.fake.first("answerCallbackQuery") is not None, 2))
            self.assertLess(self.fake.first("answerCallbackQuery") - press, 0.3)
            self.assertTrue(await until(lambda: len([m for m, _s, _e, p in self.fake.stamps
                                                     if m == "sendMessage" and p.get("reply_markup")]) >= 4, 5))
            commands_done = time.monotonic() - start
            await asyncio.gather(*list(runner.handlers), return_exceptions=True)
            await self.settle()
        self.assertLess(commands_done, 3.0)
        first = self.fake.first("sendChatAction") or self.fake.first("sendMessageDraft") or \
            self.fake.first("sendMessage")
        self.assertLess(first - start, 0.5)


class FreshPreferencesTest(BotTestCase):
    """The long poll held the preferences read before it for up to 25 s."""

    async def test_unpairing_during_the_long_poll_applies_to_its_updates(self):
        from app import pgdata
        runner = telegram_bot._BotRunner(self.client)
        runner.token = TOKEN
        stale = telegram_bot.load_preferences()

        def unpair_then_deliver(payload):
            pgdata.save_preferences({"telegram_chat_id": ""})
            return [{"update_id": 5, "message": message("/help")}]

        self.fake.responders["getUpdates"] = unpair_then_deliver
        await runner.poll_once(stale)
        await asyncio.gather(*list(runner.handlers), return_exceptions=True)
        self.assertFalse(any("Помощь" in text for text in self.fake.sent_texts()))

    async def test_switched_off_during_the_long_poll_answers_nothing(self):
        from app import pgdata
        runner = telegram_bot._BotRunner(self.client)
        runner.token = TOKEN

        def disable_then_deliver(payload):
            pgdata.save_preferences({"telegram_enabled": False})
            return [{"update_id": 9, "message": message("/help")}]

        self.fake.responders["getUpdates"] = disable_then_deliver
        await runner.poll_once(telegram_bot.load_preferences())
        await asyncio.gather(*list(runner.handlers), return_exceptions=True)
        self.assertEqual(self.fake.sent_texts(), [])
        self.assertEqual(runner.offset, 0)


class WebappCacheTest(unittest.TestCase):
    def test_handlers_do_not_read_mini_app_files_between_poller_refreshes(self):
        reads = []
        telegram_bot._webapp_cache.update(at=time.monotonic() - 20, url="https://a.example/tgapp/")
        with patch.object(telegram_bot, "_read_webapp_url", lambda: reads.append(1) or None):
            self.assertEqual(telegram_bot._webapp_url(), "https://a.example/tgapp/")
        self.assertEqual(reads, [])
        self.assertGreater(telegram_bot.WEBAPP_CACHE_TTL, telegram_bot._BotRunner.WEBAPP_EVERY)
        telegram_bot._webapp_cache["at"] = 0.0

    def test_panels_list_uses_the_cached_address(self):
        import inspect
        self.assertIn("cached_webapp_url()", inspect.getsource(telegram_panels.show_list))


class PartialMarkdownTest(unittest.TestCase):
    def test_open_bold_is_closed_while_streaming(self):
        self.assertEqual(telegram_format.render_partial("**Сравнение Python"), "<b>Сравнение Python</b>")
        self.assertEqual(telegram_format.render_partial("Текст **жирный ~~зач"),
                         "Текст <b>жирный <s>зач</s></b>")
        self.assertEqual(telegram_format.render_partial("- пункт\n- **вто"), "• пункт\n• <b>вто</b>")

    def test_code_is_left_alone(self):
        self.assertEqual(telegram_format.render_partial("a `code **x"), "a <code>code **x</code>")
        self.assertIn("x=**2", telegram_format.render_partial("```py\nx=**2"))
        self.assertEqual(telegram_format.render_partial("**a** и **b**"), "<b>a</b> и <b>b</b>")

    def test_long_answer_part_does_not_end_with_a_heading(self):
        # Live Yandex answer: part 1 ended with «На север, к другой широте» and
        # its text started part 2.
        for heading in ("## На север", "**На север, к другой широте**"):
            text = "a" * 50 + "\n\n" + "b " * 30 + "\n\n" + heading + "\n\n" + "c " * 40
            head, _carry, consumed = telegram_format.split_markdown_source(text, 140)
            self.assertNotIn("На север", head)
            self.assertTrue(text[consumed:].startswith(heading))
        head, _carry, consumed = telegram_format.split_markdown_source("x" * 100 + "\n\n" + "y" * 30, 120)
        self.assertEqual(head, "x" * 100)

    def test_partial_frames_stay_valid_html(self):
        text = "## Заголовок\n\n**Жирный** и ~~зачёркнутый~~ `код` [ссылка](https://x.y) и ещё **текст"
        for end in range(1, len(text) + 1):
            html = telegram_format.render_partial(text[:end])
            self.assertEqual(telegram_format.split_telegram_html(html, 4096)[0].count("<b>"),
                             telegram_format.split_telegram_html(html, 4096)[0].count("</b>"), text[:end])


if __name__ == "__main__":
    unittest.main()
