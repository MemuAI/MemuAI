"""Round 25: Telegram bug hunt (b4-telegram).

Offline: fake Bot API (httpx.MockTransport from telegram_r20_test), temporary
MemuAI data directory, fake sender for panel notifications.
"""
import asyncio
import os
import re
import tempfile
import time
import unittest
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

import httpx  # noqa: E402

from app import pgdata, telegram_bot, telegram_format, telegram_panels, tg_webapp  # noqa: E402
from app.telegram_pairing import token_fingerprint  # noqa: E402
from tests.round24_telegram_test import WEATHER, cb, make_panel  # noqa: E402
from tests.telegram_r20_test import OWNER, TOKEN, BotTestCase  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


class _Nesting(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.error = [], ""

    def handle_starttag(self, tag, attrs):
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.error = f"</{tag}> closes {self.stack}"
        else:
            self.stack.pop()


def nests(html):
    parser = _Nesting()
    parser.feed(html)
    parser.close()
    return not parser.error and not parser.stack


# ===================================================================== sessions


class SessionMergeTest(BotTestCase):
    """Buttons and messages of a chat run in separate lanes since Round 24; a
    handler saving its whole session snapshot reverted what the other lane (or
    the menu-card bookkeeping) wrote in between."""

    async def test_save_keeps_fields_written_concurrently(self):
        chat = str(OWNER)
        state, session = telegram_bot._session(chat)               # handler A reads
        await telegram_bot._remember_card(chat, 777, "settings")    # the other lane draws a card
        other_state, other = telegram_bot._session("999")           # another chat's handler
        other["pending_action"] = "search"
        telegram_bot._save_session(other_state)
        session["pending_action"] = "rename:abc"                    # handler A finishes
        telegram_bot._save_session(state)
        saved = pgdata.load_telegram_state()["sessions"]
        self.assertEqual(saved[chat]["pending_action"], "rename:abc")
        self.assertEqual(saved[chat]["menu_message_id"], 777, "card id was reverted by a stale snapshot")
        self.assertEqual(saved[chat]["menu_screen"], "settings")
        self.assertEqual(saved["999"]["pending_action"], "search", "another chat's session was reverted")

    async def test_second_save_of_the_same_state_writes_only_newer_edits(self):
        chat = str(OWNER)
        state, session = telegram_bot._session(chat)
        session["temp"] = True
        telegram_bot._save_session(state)
        await telegram_bot._remember_card(chat, 55, "home")
        session["chat_query"] = "milk"
        telegram_bot._save_session(state)
        saved = pgdata.load_telegram_state()["sessions"][chat]
        self.assertTrue(saved["temp"])
        self.assertEqual(saved["chat_query"], "milk")
        self.assertEqual(saved["menu_message_id"], 55)


# ===================================================================== callbacks


class SlowAckTelegram:
    """Bot API whose answerCallbackQuery takes a while (slow network)."""

    def __init__(self, delay):
        self.delay, self.calls = delay, []

    async def handler(self, request):
        method = request.url.path.rsplit("/", 1)[-1]
        import json
        payload = json.loads(request.content or b"{}")
        self.calls.append((method, payload))
        if method == "answerCallbackQuery":
            await asyncio.sleep(self.delay)
        return httpx.Response(200, json={"ok": True, "result": True})


class CallbackAckRaceTest(unittest.IsolatedAsyncioTestCase):
    async def test_toast_during_the_fallback_request_is_not_a_second_answer(self):
        fake = SlowAckTelegram(0.3)
        client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
        runner = telegram_bot._BotRunner(client)
        runner.token = TOKEN

        async def late_handler(client_, token, callback, prefs):
            await asyncio.sleep(0.15)       # after the fallback started, before it finished
            await telegram_bot._toast(client_, token, callback["id"], "🤖 Model")

        try:
            with patch.object(telegram_bot, "TOAST_FALLBACK", 0.05), \
                    patch.object(telegram_bot, "_handle_callback", late_handler):
                runner.dispatch({"update_id": 1, "callback_query": cb("model:x", "race-1")}, {})
                await asyncio.sleep(0.8)
        finally:
            await asyncio.gather(*runner.handlers, return_exceptions=True)
            await client.aclose()
        answers = [p for m, p in fake.calls if m == "answerCallbackQuery"]
        self.assertEqual(len(answers), 1, answers)


# ===================================================================== offset


class OffsetTokenSwitchTest(BotTestCase):
    async def test_pending_offset_of_the_old_token_is_not_stored_for_the_new_one(self):
        old_token, new_token = TOKEN, "987654321:BBH" + "y" * 32
        runner = telegram_bot._BotRunner(self.client)
        runner.token = old_token
        runner.offset = 900_000_123
        runner.save_offset_soon()              # background save still pending …
        await runner.activate(new_token, pgdata.load_preferences())   # … when the token changes
        await runner.flush_offset()
        poll = pgdata.load_telegram_state().get("poll") or {}
        self.assertEqual(runner.offset, 0, "the new bot must start from its own offset")
        if poll.get("token_fp") == token_fingerprint(new_token):
            self.assertNotEqual(poll.get("offset"), 900_000_123)
        else:
            self.assertEqual(poll, {"token_fp": token_fingerprint(old_token), "offset": 900_000_123})


# ===================================================================== formatting


class InlineNestingTest(unittest.TestCase):
    CASES = ["**b b****b b***i*", "- **b b****b b***i**i*", "w**x****y***z*", "*a **b* c**",
             "~~a **b~~ c**", "||a *b|| c*", "***a** b*", "**a *b** c*", "plain **bold** *it* ~~s~~"]

    def test_emphasis_never_crosses(self):
        for source in self.CASES:
            html = telegram_format.markdown_to_telegram_html(source)
            self.assertTrue(nests(html), f"{source!r} → {html!r}")

    def test_words_survive_the_repair(self):
        for source in self.CASES:
            html = telegram_format.markdown_to_telegram_html(source)
            plain = re.sub(r"[*_~|•\s]", "", telegram_format.html_to_plain(html))
            self.assertEqual(plain, re.sub(r"[*_~|\s]", "", source.lstrip("- ")))

    def test_ordinary_markdown_is_unchanged(self):
        self.assertEqual(telegram_format.markdown_to_telegram_html("*a* **b** ~~c~~ ||d||"),
                         "<i>a</i> <b>b</b> <s>c</s> <tg-spoiler>d</tg-spoiler>")

    def test_split_parts_stay_valid(self):
        source = " ".join(self.CASES * 200)
        for part in telegram_format.split_telegram_html(telegram_format.markdown_to_telegram_html(source), 500):
            self.assertTrue(nests(part), part[:200])
            self.assertLessEqual(telegram_format.utf16_len(part), 500)


# ===================================================================== panels


def _quiet_window_away_from_now():
    """Quiet hours that do NOT include the real current time (the old code
    looked at the wall clock instead of the ``now`` it was given)."""
    hour = (datetime.now().hour + 12) % 24
    return hour, f"{hour:02d}:00", f"{(hour + 1) % 24:02d}:00"


class PanelsQuietTimingTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(pgdata, "MEMU_DIR", Path(self.tmp.name))
        self.patch.start()
        self.hour, start, end = _quiet_window_away_from_now()
        pgdata.save_preferences({"telegram_enabled": True, "telegram_bot_token": TOKEN,
                                 "telegram_chat_id": str(OWNER), "telegram_language": "ru",
                                 "telegram_quiet_hours": True, "telegram_quiet_from": start,
                                 "telegram_quiet_to": end})
        self.sent = []

        async def sender(prefs, text, markup, silent):
            self.sent.append({"text": text, "silent": silent})
            return True

        telegram_panels.set_sender(sender)
        self.weather = make_panel("weather", dict(WEATHER))

    async def asyncTearDown(self):
        telegram_panels.set_sender(None)
        self.patch.stop()
        self.tmp.cleanup()

    def stamp(self, minute):
        day = datetime.now().replace(hour=self.hour, minute=0, second=0, microsecond=0)
        return day.timestamp() + minute * 60

    async def check(self, now):
        return await telegram_panels.check_all(now=now, force=True, prefs=pgdata.load_preferences())

    def set_temp(self, value):
        from app import panels as P

        def apply(doc):
            P._find(doc, self.weather)["data"]["temp"] = value
        P.mutate(apply)

    async def test_quiet_hours_follow_the_checked_time(self):
        telegram_panels.subscribe(self.weather, on=True, mode="every", quiet="defer")
        telegram_panels.mutate(lambda doc: doc["settings"].update(min_gap_minutes=0))
        await self.check(self.stamp(-120))                  # baseline, outside quiet hours
        self.set_temp(30)
        self.assertEqual(await self.check(self.stamp(30)), 0, "deferred inside quiet hours")
        self.assertEqual(await self.check(self.stamp(61)), 1, "sent when quiet hours end")
        self.assertFalse(self.sent[-1]["silent"])

    async def test_digest_waits_for_quiet_hours_when_deferred(self):
        telegram_panels.subscribe(self.weather, on=True, mode="digest", at=f"{self.hour:02d}:10", quiet="defer")
        self.assertEqual(await self.check(self.stamp(20)), 0)
        self.assertEqual(await self.check(self.stamp(65)), 1)
        self.assertIn("Сводка панелей", self.sent[-1]["text"])
        self.assertEqual(await self.check(self.stamp(80)), 0)    # once a day

    async def test_silent_digest_is_sent_without_sound(self):
        telegram_panels.subscribe(self.weather, on=True, mode="digest", at=f"{self.hour:02d}:10", quiet="silent")
        self.assertEqual(await self.check(self.stamp(20)), 1)
        self.assertTrue(self.sent[-1]["silent"])


# ===================================================================== Mini App


class MiniAppAuthLanguageTest(unittest.TestCase):
    def test_init_data_errors_are_translated(self):
        from fastapi.testclient import TestClient
        from app import tgapp_api
        config = {**tg_webapp.load_config(), "enabled": True}
        with patch.object(tgapp_api, "_prefs", return_value={"language": "en", "telegram_bot_token": TOKEN}), \
                patch.object(tg_webapp, "load_config", return_value=config):
            client = TestClient(tgapp_api.create_tgapp())
            stale = tg_webapp.sign_init_data({"auth_date": int(time.time()) - 10 * 86400,
                                              "user": {"id": OWNER}}, TOKEN)
            response = client.post("/tgapp/api/auth", json={"init_data": stale})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "expired")
        self.assertEqual(response.json()["error"], tg_webapp.INIT_DATA_ERRORS_EN["expired"])

    def test_every_init_data_error_has_an_english_text(self):
        source = (ROOT / "app" / "tg_webapp.py").read_text(encoding="utf-8")
        codes = set(re.findall(r'InitDataError\("(\w+)"', source))
        self.assertTrue(codes)
        self.assertEqual(codes - set(tg_webapp.INIT_DATA_ERRORS_EN), set())


class MiniAppDesignsTest(unittest.TestCase):
    def test_every_design_has_mini_app_styles(self):
        from app import themes
        css = (ROOT / "app" / "tgapp" / "app.css").read_text(encoding="utf-8")
        ids = [d["id"] if isinstance(d, dict) else d for d in themes.DESIGNS]
        for design in ("soft", "terminal", "brutal"):
            self.assertIn(design, ids)
            self.assertTrue(f':root[data-design="{design}"]' in css, f"no Mini App styles for {design}")


if __name__ == "__main__":
    unittest.main()
