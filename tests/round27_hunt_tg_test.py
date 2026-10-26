"""Round 27, phase 2 (r27-hunt-tg-auto): regressions for bugs found while
driving the Telegram bot, Mini App, Work/automation and companion live.

Offline: fake Bot API (httpx.MockTransport), temporary data dir, stub model streams.
"""
import asyncio
import unittest
from unittest.mock import patch

from app import pgdata, telegram_bot
from tests.telegram_r20_test import BotTestCase

class TelegramNewChatTitle(BotTestCase):
    """The AI title never came for a brand-new Telegram chat: the turn was
    saved into the same dict, so «first turn» looked like «has messages»."""

    async def test_first_message_of_a_new_chat_gets_an_ai_title(self):
        def stream(model, messages, params):
            async def gen():
                text = "Полив фикуса" if "title" in str(messages[-1]["content"]).lower() else "Раз в неделю."
                yield {"choices": [{"delta": {"content": text}}]}
            return gen()

        with patch("app.chat.chat_service.stream_completion", stream):
            await self.send("Как поливать фикус зимой?")
            await self.settle()
        chat = pgdata.load_chats("default")[0]
        self.assertEqual(chat["title"], "Полив фикуса")
        self.assertTrue(chat.get("titleAuto"))
        self.assertEqual(len(chat["messages"]), 2)

    async def test_second_message_does_not_rename(self):
        calls = []

        def stream(model, messages, params):
            calls.append(messages)

            async def gen():
                yield {"choices": [{"delta": {"content": "Ок"}}]}
            return gen()

        pgdata.save_preferences({"chat_titles": "off"})
        with patch("app.chat.chat_service.stream_completion", stream):
            await self.send("Первое")
            await self.send("Второе")
            await self.settle()
        self.assertEqual(len(calls), 2)

class AutomationLogLanguage(unittest.TestCase):
    """Work browser/Computer Use: the action log and the approval card were English in the Russian UI."""

    def test_describe_action_in_both_languages(self):
        from app.automation_runtime import _describe_action
        self.assertEqual(_describe_action({"action": "click", "element": 3, "reason": "go"}), 'click element 3 — "go"')
        self.assertEqual(_describe_action({"action": "click", "element": 3}, "ru"), "нажать элемент 3")
        self.assertEqual(_describe_action({"action": "double_click", "x": 1, "y": 2}, "ru"),
                         "двойной щелчок в точке (1, 2) на мониторе 1")
        self.assertEqual(_describe_action({"action": "scroll", "dy": -300}, "ru"), "прокрутить вверх на 300")
        self.assertEqual(_describe_action({"action": "focus_window", "title": "Telegram"}, "ru"),
                         "переключиться на окно «Telegram»")
        self.assertEqual(_describe_action({"action": "select", "element": 2, "option": "M"}, "ru"),
                         "выбрать «M» в элементе 2")
        for kind in ("navigate", "type", "key", "wait", "back", "reload", "drag", "move", "right_click",
                     "triple_click", "close_window", "minimize_window", "maximize_window", "click_xy", "hover"):
            text = _describe_action({"action": kind, "x": 1, "y": 2, "element": 4, "title": "W"}, "ru")
            self.assertRegex(text, "[а-я]", kind)

    def test_browser_log_is_russian_but_the_model_history_stays_english(self):
        from unittest.mock import AsyncMock
        from app import automation_runtime as ar
        runtime = ar.EdgeAutomation()
        runtime.start = AsyncMock()
        runtime.snapshot = AsyncMock(return_value={"url": "u", "title": "t", "text": "", "elements": []})
        runtime.perform = AsyncMock(side_effect=["clicked via mouse", ValueError("Элемент неактивен (disabled)")])
        replies = iter(['{"action":"click","element":3,"reason":"go"}', '{"action":"click","element":5}',
                        '{"action":"done","report":"ok"}'])
        prompts = []

        async def completion(model, messages):
            prompts.append(messages)
            return next(replies)

        asyncio.run(ar.run_browser_agent(runtime, completion, model="m", task="t", lang="ru"))
        logs = [row["message"] for row in runtime.logs]
        self.assertIn('Действие: нажать элемент 3 — "go"', logs)
        self.assertIn("Выполнено: нажать элемент 3", logs)
        self.assertTrue(any(row.startswith("Не получилось: нажать элемент 5 (Элемент неактивен") for row in logs))
        self.assertIn("click: clicked via mouse — go", prompts[-1][-1]["content"][0]["text"])

    def test_browser_stop_is_logged(self):
        from unittest.mock import AsyncMock
        from app import automation_runtime as ar
        runtime = ar.EdgeAutomation()
        runtime.start = AsyncMock(side_effect=asyncio.CancelledError)
        asyncio.run(ar.run_browser_agent(runtime, AsyncMock(), model="m", task="t", lang="en"))
        self.assertEqual(runtime.status, "cancelled")
        self.assertEqual(runtime.logs[-1]["message"], "Stopped by the user")

class SchedulerLanguage(unittest.IsolatedAsyncioTestCase):
    """A scheduled task's result, Windows/Telegram notifications were Russian in the English UI."""

    async def _run(self, lang, task):
        from app import scheduler
        results, notes = [], []

        def stream(model, messages, params=None):
            async def gen():
                yield {"choices": [{"delta": {"content": "Готово"}}]}
            return gen()

        async def notify(title, text, **kwargs):
            notes.append(title)

        with patch.object(scheduler, "load_preferences", return_value={"language": lang, "notify_windows": False}), \
                patch("app.chat.chat_service.stream_completion", stream), \
                patch.object(scheduler, "_record_result", side_effect=lambda *a: results.append(a)), \
                patch("app.telegram_bot.send_notification", notify):
            await scheduler._run_task(task)
        return results, notes

    async def test_english_titles_and_errors(self):
        results, notes = await self._run("en", {"id": "t", "name": "", "prompt": "x", "model": "m"})
        self.assertEqual(notes, ["Planning · Task finished"])
        results, _ = await self._run("en", {"id": "t", "mode": "work", "project_id": "nope", "prompt": "x"})
        self.assertFalse(results[0][1])
        self.assertIn("Work project of this schedule is unavailable", results[0][2])
        results, notes = await self._run("ru", {"id": "t", "name": "Сводка", "prompt": "x", "model": "m"})
        self.assertEqual(notes, ["Планирование · Сводка"])

class MiniAppNarrowPhone(unittest.TestCase):
    """Mini App at 320–350 px (iPhone SE): the six tab labels ran into each other (1fr grew with the
    longest label) and the default palette was listed twice with both cards highlighted."""

    def test_tabbar_columns_are_equal_and_labels_shrink(self):
        from pathlib import Path
        css = (Path(__file__).resolve().parents[1] / "app/tgapp/app.css").read_text(encoding="utf-8")
        self.assertIn("grid-template-columns: repeat(var(--tabs, 5), minmax(0, 1fr))", css)
        self.assertIn("@media (max-width: 380px) { .tab {", css)

        self.assertLess(css.index(".cell-value { color"), css.index("@media (max-width: 350px) { .cell-value"))
        html = (Path(__file__).resolve().parents[1] / "app/tgapp/index.html").read_text(encoding="utf-8")
        self.assertNotIn("app.css?v=25", html)

    def test_theme_catalog_has_one_default_palette(self):
        from app.tgapp_api import theme_catalog
        for lang in ("ru", "en"):
            for mode, rows in theme_catalog(lang)["variants"].items():
                ids = [row["id"] for row in rows]
                self.assertEqual(len(ids), len(set(ids)), (mode, ids))
                self.assertEqual(ids[0], "")
        self.assertEqual(theme_catalog("en")["variants"]["light"][0]["name"], "Light")

class DeletedChatStaysDeleted(BotTestCase):
    """A chat deleted in MemuAI while the bot / Mini App was answering in it came back on save."""

    async def test_bot_answer_does_not_recreate_a_deleted_chat(self):
        release = asyncio.Event()

        async def slow(*_a, **_k):
            yield {"choices": [{"delta": {"content": "часть"}}]}
            await release.wait()
            yield {"choices": [{"delta": {"content": " конец"}}]}

        async def fast(*_a, **_k):
            yield {"choices": [{"delta": {"content": "Ок"}}]}

        pgdata.save_preferences({"chat_titles": "off"})
        with patch("app.chat.chat_service.stream_completion", lambda *a, **k: fast()):
            await self.send("Первое")
        first = pgdata.load_chats("default")[0]
        self.assertNotIn("_new", first)
        with patch("app.chat.chat_service.stream_completion", lambda *a, **k: slow()):
            task = asyncio.create_task(self.send("Второе"))
            await asyncio.sleep(0.3)
            pgdata.save_chats([], "default")
            release.set()
            await task
        self.assertEqual(pgdata.load_chats("default"), [])

    def test_mini_app_persist_respects_deletion_and_creates_new_chats(self):
        from app import tgapp_chat
        user = {"role": "user", "content": "x", "createdAt": 1}
        answer = {"role": "assistant", "content": "y"}
        with patch.object(tgapp_chat, "_set_meme_for_chat"):
            row = tgapp_chat._persist({"id": "tga-1", "title": "", "messages": [], "_new": True}, user, answer,
                                      "", "m", "default", "")
            self.assertEqual([m["role"] for m in row["messages"]], ["user", "assistant"])
            stored = pgdata.load_chats("default")
            self.assertEqual(len(stored), 1)
            self.assertNotIn("_new", stored[0])
            pgdata.save_chats([], "default")
            self.assertIsNone(tgapp_chat._persist(dict(stored[0]), dict(user, createdAt=2), answer, "",
                                                  "m", "default", ""))
        self.assertEqual(pgdata.load_chats("default"), [])

class MiniAppSignInLimits(unittest.TestCase):
    """The strict sign-in cap counted successful sign-ins of the app itself (and the failure counter was
    never checked): 12 openings a minute locked the owner out, guessing was capped no harder."""

    def test_failures_are_capped_successes_are_not(self):
        from app import tg_webapp
        limiter = tg_webapp.RateLimiter(2, 60)
        self.assertFalse(limiter.full("k", now=0))
        limiter.hit("k", now=0)
        limiter.hit("k", now=1)
        self.assertTrue(limiter.full("k", now=2))
        self.assertFalse(limiter.full("k", now=100))
        src = (__import__("pathlib").Path(__file__).resolve().parents[1] / "app/tgapp_api.py").read_text(encoding="utf-8")
        self.assertIn('if auth_failures.full("f:" + key) or not auth_limiter.hit("a:" + key):', src)
        self.assertIn('"Too often — please wait"', src)

if __name__ == "__main__":
    unittest.main()
