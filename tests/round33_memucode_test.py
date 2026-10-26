"""Round 33 — MemuCode CLI: /auto-continue (send "continue" by itself when the limits are back) and the built-in
MemuCode guide (the program knows its own commands; generated from the real registry, never drifts).

Offline: a scripted backend stands in for the model. ``LiveSelfQuestions`` asks real models when
``MEMUCODE_LIVE_SELFDOC=1`` and the keys (AWS9_BASE/AWS9_KEY or REFORM_BASE/REFORM_KEY) are in the environment.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
import unittest
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import patch

from tests.memucode_cli_test import Recorder, Scripted, make_runtime, setUpModule, tearDownModule  # noqa: F401
from app.cli import autocontinue as ac
from app.cli import ratelimit, selfdoc
from app.cli.agent import Agent, TurnResult
from app.cli.llm import LLMError, StreamEvent, Usage

NOW = datetime(2026, 10, 11, 16, 0).timestamp()

class ResetTimes(unittest.TestCase):
    def test_headers(self):
        self.assertEqual(ratelimit.from_headers({"retry-after": "30"}, NOW) - NOW, 30)
        self.assertEqual(ratelimit.from_headers({"Retry-After": "2"}, NOW) - NOW, 2)
        self.assertAlmostEqual(ratelimit.from_headers({"retry-after-ms": "1500"}, NOW) - NOW, 1.5)
        self.assertEqual(ratelimit.from_headers({"x-ratelimit-reset-requests": "6m0s"}, NOW) - NOW, 360)
        later = datetime.fromtimestamp(NOW + 1800, timezone.utc)
        at = ratelimit.from_headers({"anthropic-ratelimit-requests-reset": later.strftime("%Y-%m-%dT%H:%M:%SZ")}, NOW)
        self.assertEqual(at, NOW + 1800)
        date = format_datetime(datetime.fromtimestamp(NOW + 3600, timezone.utc), usegmt=True)
        self.assertEqual(ratelimit.from_headers({"retry-after": date}, NOW), NOW + 3600)
        self.assertEqual(ratelimit.from_headers({}, NOW), 0.0)
        self.assertEqual(ratelimit.from_headers({"retry-after": str(30 * 86400)}, NOW), 0.0)

    def test_texts(self):
        cases = {
            "Rate limit reached. Please try again in 1h2m3.5s. Visit https://x": 3723.5,
            "Too many requests, retry after 120 seconds": 120,
            "Your quota resets in 5 hours.": 18000,
            '{"error":{"details":[{"retryDelay": "32s"}]}}': 32,
            "Слишком много запросов, попробуйте через 5 минут": 300,
            '{"retry_after": 42}': 42,
        }
        for text, seconds in cases.items():
            with self.subTest(text=text):
                self.assertAlmostEqual(ratelimit.from_text(text, NOW) - NOW, seconds, places=3)
        self.assertEqual(ratelimit.from_text('{"reset_at": %d}' % (NOW + 600), NOW) - NOW, 600)
        stamp = datetime.fromtimestamp(NOW + 5400, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(ratelimit.from_text(f"limit will reset at {stamp}", NOW), NOW + 5400)
        stamp = datetime.fromtimestamp(NOW + 600, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        self.assertEqual(ratelimit.from_text(f"Available again at {stamp}", NOW), NOW + 600)
        for text in ("429 Too Many Requests", "try again later", "reset at 18:30", ""):
            self.assertEqual(ratelimit.from_text(text, NOW), 0.0, text)

    def test_an_exception_counts_by_its_retry_at_then_its_text(self):
        exc = LLMError("rate limit: please try again in 90s", status=429)
        self.assertAlmostEqual(ratelimit.reset_at(exc, now=NOW) - NOW, 90)
        exc.retry_at = NOW + 1000
        self.assertEqual(ratelimit.reset_at(exc, now=NOW), NOW + 1000)

    def test_llm_backends_keep_the_reset(self):
        from app.cli import llm
        error = llm._proxy_stream_error({"message": "Rate limit", "status": 429, "type": "rate_limit_error",
                                         "retry_after": 120})
        self.assertGreater(error.retry_at, time.time() + 100)
        error = llm._proxy_stream_error({"message": "quota exceeded, try again in 10 minutes", "status": 429})
        self.assertGreater(error.retry_at, time.time() + 590)
        self.assertEqual(llm._limit_reset({"retry-after": "60"}, "") > time.time() + 50, True)

    def test_agent_turn_result_carries_the_reset(self):
        class Limited(Scripted):
            async def stream(self, request):
                error = LLMError("Rate limit exceeded: try again in 600s", status=429, kind="rate_limit_error")
                raise error
                yield  # pragma: no cover

        runtime, _ = make_runtime()
        backend = Limited([])
        runtime.router.resolve = lambda model: (backend, model)
        result = asyncio.run(Agent(runtime).run_turn("hello"))
        self.assertEqual(result.stopped, "error")
        self.assertEqual(result.error_category, "rate_limit")
        self.assertGreater(result.retry_at, time.time() + 500)

class Times(unittest.TestCase):
    def at(self, text):
        return datetime.fromtimestamp(ac.parse_time(text, NOW))

    def test_forms(self):
        self.assertEqual(self.at("18:30"), datetime(2026, 10, 11, 18, 30))
        self.assertEqual(self.at("6:30pm"), datetime(2026, 10, 11, 18, 30))
        self.assertEqual(self.at("at 7pm"), datetime(2026, 10, 11, 19, 0))
        self.assertEqual(self.at("в 18:30"), datetime(2026, 10, 11, 18, 30))
        self.assertEqual(self.at("+2h"), datetime(2026, 10, 11, 18, 0))
        self.assertEqual(self.at("in 45m"), datetime(2026, 10, 11, 16, 45))
        self.assertEqual(self.at("через 2 часа"), datetime(2026, 10, 11, 18, 0))
        self.assertEqual(self.at("1h30m"), datetime(2026, 10, 11, 17, 30))
        self.assertEqual(self.at("45m"), datetime(2026, 10, 11, 16, 45))
        self.assertEqual(self.at("tomorrow 09:00"), datetime(2026, 10, 12, 9, 0))
        self.assertEqual(self.at("завтра 9:00"), datetime(2026, 10, 12, 9, 0))
        self.assertEqual(self.at("tomorrow"), datetime(2026, 10, 12, 9, 0))
        self.assertEqual(self.at("2026-10-12 09:00"), datetime(2026, 10, 12, 9, 0))

    def test_a_passed_clock_time_means_tomorrow_and_bad_input_explains(self):
        self.assertEqual(self.at("15:00"), datetime(2026, 10, 12, 15, 0))
        for text in ("blah", "25:00", "+0h", "2026-10-10 09:00", "in 9 days", ""):
            with self.subTest(text=text), self.assertRaises(ac.TimeError):
                ac.parse_time(text, NOW)

    def test_arguments(self):
        request = ac.parse_args("", NOW)
        self.assertEqual(request.action, "pick")
        for word, action in (("cancel", "cancel"), ("отмена", "cancel"), ("status", "status"), ("now", "now"),
                             ("help", "help")):
            self.assertEqual(ac.parse_args(word, NOW).action, action)
        request = ac.parse_args("in 45m продолжай рефакторинг", NOW)
        self.assertEqual((request.action, request.message), ("arm", "продолжай рефакторинг"))
        self.assertEqual(datetime.fromtimestamp(request.at), datetime(2026, 10, 11, 16, 45))
        request = ac.parse_args("через 2 часа продолжай", NOW)
        self.assertEqual(datetime.fromtimestamp(request.at), datetime(2026, 10, 11, 18, 0))
        self.assertEqual(request.message, "продолжай")
        request = ac.parse_args("auto go on", NOW)
        self.assertTrue(request.auto)
        self.assertEqual(request.message, "go on")
        request = ac.parse_args('+2h --retries 2 --fallback @x:y "keep going"', NOW)
        self.assertEqual((request.retries, request.fallback, request.message), (2, "@x:y", "keep going"))
        self.assertEqual(ac.parse_args("--no-retry 10m", NOW).retries, 0)
        self.assertIn("Examples", ac.parse_args("blah", NOW).error)
        self.assertIn("number", ac.parse_args("10m --retries x", NOW).error)

    def test_backoff_and_countdown(self):
        self.assertEqual([ac.backoff(n) // 60 for n in range(1, 7)], [5, 10, 20, 40, 60, 60])
        at, why = ac.next_try(1, NOW + 3600, NOW)
        self.assertEqual((at, why), (NOW + 3600 + ac.RESET_MARGIN, "reset"))
        at, why = ac.next_try(2, NOW + 2, NOW)
        self.assertEqual((at, why), (NOW + ac.MIN_DELAY, "reset"))
        at, why = ac.next_try(2, 0, NOW)
        self.assertEqual((at - NOW, why), (600, "backoff"))
        self.assertEqual(ac.countdown(42), "0:42")
        self.assertEqual(ac.countdown(14 * 60 + 5), "14:05")
        self.assertEqual(ac.countdown(2 * 3600 + 3 * 60 + 7), "2:03:07")
        self.assertEqual(ac.countdown(27 * 3600), "1d 3h")

class FakeUI:
    interactive = True

    def __init__(self, runtime):
        self.runtime = runtime
        runtime.ui = self
        self.busy = False
        self.dialogs = []
        self.connecting = False
        self.said = []
        self.sent = []
        self.notified = []

    def say(self, text, level="info"):
        self.said.append((level, text))

    def notice(self, text, level="info"):
        self.said.append((level, text))

    def send_prompt(self, text, display=None, kind="prompt"):
        self.sent.append(text)
        self.busy = True

    def _notify(self, title, text, **kwargs):
        self.notified.append(text)

def result(stopped="done", category="", retry_at=0.0, error=""):
    return TurnResult(stopped=stopped, error_category=category, retry_at=retry_at,
                      error=error or ("The provider is rate limiting requests (HTTP 429)" if category else ""))

class Manager(unittest.TestCase):
    def setUp(self):
        self.runtime, _ = make_runtime()
        self.ui = FakeUI(self.runtime)
        self.auto = ac.AutoContinue(self.ui)
        self.auto.tick(NOW)

    def test_arm_persists_counts_down_and_fires_when_free(self):
        plan = self.auto.arm(NOW + 90, message="")
        self.assertEqual(plan.message, "continue")
        self.assertEqual(self.runtime.session.meta["auto_continue"]["at"], NOW + 90)
        self.assertEqual(self.auto.footer(NOW), "◷ continue in 1:30")
        self.assertTrue(self.auto.tick(NOW + 1))
        self.assertEqual(self.ui.sent, [])
        self.ui.busy = True
        self.auto.tick(NOW + 100)
        self.assertEqual(self.ui.sent, [])
        self.ui.busy = False
        self.auto.tick(NOW + 101)
        self.assertEqual(self.ui.sent, ["continue"])
        self.assertEqual(self.auto.footer(NOW + 102), "◷ auto-continue: sent")
        self.auto.turn_finished(result(), NOW + 120)
        self.assertIsNone(self.auto.plan)
        self.assertIsNone(self.runtime.session.meta.get("auto_continue"))
        self.assertTrue(any("worked" in text for _l, text in self.ui.said))
        self.assertTrue(self.ui.notified)

    def test_still_limited_retries_at_the_reset_then_switches_to_the_fallback_and_gives_up(self):
        self.runtime.set_model("test-model")
        self.auto.arm(NOW, retries=2, fallback="other-model")
        self.auto.tick(NOW + 1)
        self.assertEqual(self.ui.sent, ["continue"])
        self.ui.busy = False
        self.auto.turn_finished(result("error", "rate_limit", retry_at=NOW + 1800), NOW + 5)
        self.assertEqual(self.auto.plan.at, NOW + 1800 + ac.RESET_MARGIN)
        self.assertEqual(self.auto.plan.attempt, 1)
        self.assertTrue(any("Still limited" in text and "fallback" in text for _l, text in self.ui.said))
        self.auto.tick(NOW + 1810)
        self.assertEqual(self.runtime.model, "other-model")
        self.assertEqual(len(self.ui.sent), 2)
        self.ui.busy = False
        self.auto.turn_finished(result("error", "quota"), NOW + 1820)
        self.assertEqual(self.auto.plan.at, NOW + 1820 + ac.backoff(2))
        self.auto.tick(NOW + 1820 + ac.backoff(2) + 1)
        self.ui.busy = False
        self.auto.turn_finished(result("error", "rate_limit"), NOW + 9000)
        self.assertIsNone(self.auto.plan)
        self.assertTrue(any("gave up after 3 tries" in text for _l, text in self.ui.said))

    def test_an_error_that_waiting_does_not_cure_stops_it_and_esc_cancels(self):
        self.auto.arm(NOW)
        self.auto.tick(NOW + 1)
        self.auto.turn_finished(result("error", "auth", error="Invalid API key"), NOW + 2)
        self.assertIsNone(self.auto.plan)
        self.assertTrue(any("will not go away" in text for _l, text in self.ui.said))
        self.ui.busy = False
        self.auto.arm(NOW)
        self.auto.tick(NOW + 1)
        self.auto.turn_finished(result("interrupted"), NOW + 2)
        self.assertIsNone(self.auto.plan)

    def test_a_limit_error_is_remembered_and_offered(self):
        self.auto.turn_finished(result("error", "rate_limit", retry_at=NOW + 3600), NOW)
        offer = [text for _l, text in self.ui.said if "/auto-continue auto" in text]
        self.assertTrue(offer, self.ui.said)
        self.assertIn("in 1:00:05", offer[0])
        plan, known = self.auto.arm_auto(now=NOW + 10)
        self.assertTrue(known)
        self.assertEqual((plan.at, plan.source), (NOW + 3600 + ac.RESET_MARGIN, "auto"))
        self.ui.said.clear()
        self.auto.turn_finished(result("error", "server", error="HTTP 500"), NOW + 20)
        self.assertEqual(self.ui.said, [])
        self.auto.cancel()
        self.auto.last_limit = {}
        plan, known = self.auto.arm_auto(now=NOW)
        self.assertFalse(known)
        self.assertEqual((plan.at - NOW, plan.source), (ac.AUTO_FALLBACK, "limit"))
        self.auto.turn_finished(result("error", "rate_limit"), NOW)
        self.assertTrue(any("already set" in text for _l, text in self.ui.said))

    def test_the_plan_lives_with_its_conversation(self):
        first = self.runtime.session
        first.append({"role": "user", "kind": "prompt", "content": "write the parser"})
        self.auto.arm(NOW + 600, message="go on")
        self.runtime.new_session()
        self.auto.tick(NOW + 1)
        self.assertIsNone(self.auto.plan)
        self.assertTrue(any("paused" in text for _l, text in self.ui.said))

        self.runtime.session = first
        self.auto.tick(NOW + 2)
        self.assertEqual((self.auto.plan.at, self.auto.plan.message), (NOW + 600, "go on"))

        from app.cli.session import Session
        reopened = Session.load(self.runtime.root, first.id)
        self.assertEqual(reopened.meta["auto_continue"]["message"], "go on")
        runtime2, _ = make_runtime()
        runtime2.session = reopened
        ui2 = FakeUI(runtime2)
        auto2 = ac.AutoContinue(ui2)
        auto2.tick(NOW + 7200)
        self.assertEqual(auto2.plan.at, NOW + 7200 + ac.GRACE)
        self.assertTrue(any("while MemuCode was closed" in text for _l, text in ui2.said))
        auto2.tick(NOW + 7200 + ac.GRACE + 1)
        self.assertEqual(ui2.sent, ["go on"])

    def test_cancel_and_describe(self):
        self.assertFalse(self.auto.cancel())
        self.assertIsNone(self.runtime.session.meta.get("auto_continue"))
        self.auto.arm(NOW + 3600, retries=1, fallback="m2")
        text = self.auto.describe(NOW)
        self.assertIn("at 17:00", text)
        self.assertIn("try 1 of 2", text)
        self.assertIn("fallback model: m2", text)
        self.assertTrue(self.auto.cancel())

class Terminal(unittest.TestCase):
    def test_limit_error_offer_command_countdown_and_send(self):
        from tests.memucode_ui_test import drive_ui
        from tests.memucode_transcript_test import texts
        from tests.round14_test import need

        class Limited(Scripted):
            def __init__(self):
                super().__init__([])
                self.calls = 0

            async def stream(self, request):
                self.requests.append(request)
                self.calls += 1
                if self.calls == 1:
                    raise LLMError("Rate limit exceeded. Please try again in 2s.", status=429, kind="rate_limit_error",
                                   provider="custom")
                yield StreamEvent("text", text="Picking up where I stopped.")
                yield StreamEvent("usage", usage=Usage(input=10, output=5, estimated=False, requests=1))
                yield StreamEvent("done", finish="stop")

        runtime, _ = make_runtime()
        backend = Limited()
        runtime.router.resolve = lambda model: (backend, model)

        async def drive(ui, keys):
            screen = lambda: "\n".join(texts(ui.transcript.viewport(200, 110)))  # noqa: E731
            keys.send_text("write the parser\r")
            await need(lambda: "/auto-continue auto" in screen(), 8)
            offered = screen()
            keys.send_text("/auto-continue auto\r")
            await need(lambda: ui.auto_continue.plan is not None, 4)
            footer = "".join(fragment[1] for fragment in ui._footer_left())
            await need(lambda: backend.calls >= 2, 8)
            await need(lambda: "Auto-continue worked" in screen(), 8)
            return offered, footer, screen()

        import app.cli.agent as agent_module
        with patch.object(ac, "MIN_DELAY", 1), patch.object(ac, "RESET_MARGIN", 0), \
                patch.object(agent_module, "_retryable", lambda exc: False):
            ui, (offered, footer, final) = drive_ui(runtime, drive)
        self.assertIn("rate limiting", offered)
        self.assertIn("The limit resets at", offered)
        self.assertRegex(footer, r"◷ continue in 0:0\d")
        self.assertIn("Auto-continue: sending “continue”", final)
        self.assertIn("Picking up where I stopped.", final)
        sent = backend.requests[1].messages[-1]["content"]
        self.assertIn("continue", sent if isinstance(sent, str) else str(sent))

    def test_completion_and_registry(self):
        from app.cli.commands import REGISTRY, CommandRegistry  # noqa: F401
        from app.cli.tui.input import MemuCompleter
        from prompt_toolkit.document import Document
        import app.cli.commands.autocontinue_cmds  # noqa: F401
        import app.cli.commands.guide_cmds  # noqa: F401
        self.assertIn("auto-continue", REGISTRY)
        self.assertIn("help-ask", REGISTRY)
        completer = MemuCompleter(lambda: [], lambda: [], lambda: [])
        shown = [item.text for item in completer.get_completions(Document("/auto-continue "))]
        self.assertEqual(shown[:4], ["auto", "status", "cancel", "now"])
        self.assertIn("tomorrow 09:00", shown)
        from app.tgapp_complete import CHOICES
        self.assertEqual(CHOICES["auto-continue"], MemuCompleter.CHOICES["/auto-continue"])
        self.assertEqual(tuple(CHOICES["auto-continue"]), ac.CHOICES)

    def test_settings_default(self):
        runtime, _ = make_runtime()
        self.assertEqual(ac.setting_retries(runtime.settings), ac.DEFAULT_RETRIES)
        self.assertEqual(ac.setting_fallback(runtime.settings), "")
        self.assertEqual(ac.default_message(runtime.settings), "continue")
        runtime, _ = make_runtime(settings={"auto_continue": {"retries": 99, "fallback_model": "x", "message": "дальше"}})
        self.assertEqual(ac.setting_retries(runtime.settings), ac.MAX_RETRIES)
        self.assertEqual(ac.default_message(runtime.settings), "дальше")

class GuideCoverage(unittest.TestCase):
    """The reference never drifts: whatever the program has, the guide names, and it names nothing else."""

    @classmethod
    def setUpClass(cls):
        cls.guide = selfdoc.build_guide()

    def test_every_registered_command_and_alias_is_documented(self):
        from app.cli.commands import REGISTRY
        selfdoc._registry()
        for command in REGISTRY.values():
            if command.hidden:
                continue
            with self.subTest(command=command.name):
                self.assertRegex(self.guide, rf"(?m)^- /{re.escape(command.name)}\b")
                for alias in command.aliases:
                    self.assertIn("/" + alias, self.guide)

    def test_hand_written_text_names_only_real_commands(self):
        from app.cli.commands import REGISTRY
        selfdoc._registry()
        names = set(REGISTRY) | {alias for command in REGISTRY.values() for alias in command.aliases}
        allowed = {"skill-name", "name", "command"}
        texts = [answer for _q, answer in selfdoc.FAQ] + [question for question, _a in selfdoc.FAQ] \
            + list(selfdoc.NOTES.values()) + [selfdoc.POINTER, selfdoc.DESCRIPTION]
        for text in texts:
            for found in re.findall(r"(?<![\w/.:])/([a-z][a-z0-9-]*)", text):
                with self.subTest(command=found, text=text[:60]):
                    self.assertTrue(found in names or found in allowed, f"/{found} is not a MemuCode command")
        for key in selfdoc.NOTES:
            self.assertIn(key, REGISTRY, f"NOTES names /{key}, which does not exist")

    def test_flags_subcommands_settings_keys_and_paths(self):
        from app.cli.main import SUBCOMMANDS, build_parser
        from app.cli.settings import DEFAULTS
        from app.cli.tui.app import SHORTCUT_ROWS
        from app.cli.hooks import EVENTS
        for action in build_parser()._actions:
            for option in action.option_strings:
                self.assertIn(option, self.guide)
            if action.option_strings and action.dest != "help":
                self.assertTrue(action.help or selfdoc.FLAG_NOTES.get(action.dest), action.dest)
        for name in SUBCOMMANDS:
            self.assertIn(f"`memucode {name}`", self.guide)
            self.assertIn(name, selfdoc.SUBCOMMAND_NOTES)
        self.assertEqual(set(selfdoc.SUBCOMMAND_NOTES), set(SUBCOMMANDS))
        for key in DEFAULTS:
            self.assertRegex(self.guide, rf"(?m)^- {re.escape(key)} = ")
            self.assertTrue(selfdoc.SETTING_NOTES.get(key), f"setting {key} has no note")
        self.assertEqual(set(selfdoc.SETTING_NOTES) - set(DEFAULTS), set())
        for key, text in SHORTCUT_ROWS:
            self.assertIn(f"- {key} — {text}", self.guide)
        for event in EVENTS:
            self.assertIn(event, self.guide)
        for mode in ("default", "acceptEdits", "plan", "auto", "bypassPermissions"):
            self.assertIn(f"- {mode} (", self.guide)

    def test_the_planning_answer_is_right_there(self):
        first = self.guide.split("## Quick answers", 1)[1].split("\n- ", 2)[1]
        self.assertIn("planning", first)
        self.assertIn("/plan", first)
        self.assertIn("Shift+Tab", first)

class GuideDelivery(unittest.TestCase):
    def test_skill_catalogue_and_skill_tool(self):
        runtime, _ = make_runtime()
        ids = [row["id"] for row in runtime.skills.catalogue()]
        self.assertIn(selfdoc.GUIDE_ID, ids)
        row = runtime.skills.get(selfdoc.GUIDE_ID)
        self.assertTrue(row["builtin"])
        self.assertIn("/auto-continue", row["prompt"])
        from app.cli.prompts import skills_block
        self.assertIn("- memucode-guide: use when the user asks how to do something in MemuCode", skills_block(runtime))
        from app.cli.tools.base import ToolContext  # noqa: F401
        from app.cli.tools.meta import SkillTool

        class Ctx:
            pass
        ctx = Ctx()
        ctx.runtime, ctx.session = runtime, runtime.session
        out = asyncio.run(SkillTool().run(ctx, {"name": selfdoc.GUIDE_ID}, None))
        self.assertTrue(out.ok)
        self.assertIn("## Slash commands", out.output)

    def test_system_prompt_pointer_and_turn_attachment(self):
        runtime, backend = make_runtime(turns=["Type /plan.", "ok", "ok", "ok"])
        system = runtime.system_prompt(runtime.tools.available(runtime), native=False)
        self.assertIn("# About MemuCode itself", system)
        self.assertIn("/plan or Shift+Tab", system)
        self.assertNotIn("## Slash commands", system)
        asyncio.run(Agent(runtime).run_turn("Как включить в MemuCode CLI планирование?"))
        last = backend.requests[-1].messages[-1]["content"]
        last = last if isinstance(last, str) else str(last)
        self.assertIn('<skill name="memucode-guide">', last)
        self.assertIn("## Slash commands", last)
        asyncio.run(Agent(runtime).run_turn("а как его выключить?"))
        self.assertIn('<skill name="memucode-guide">', str(backend.requests[-1].messages[-1]["content"]))
        asyncio.run(Agent(runtime).run_turn("fix the failing test in tests/test_api.py"))
        self.assertNotIn("memucode-guide", str(backend.requests[-1].messages[-1]["content"]))

        runtime, backend = make_runtime(turns=["ok"], settings={"skills": {"disabled": [selfdoc.GUIDE_ID]}})
        asyncio.run(Agent(runtime).run_turn("как в MemuCode сменить модель?"))
        self.assertNotIn('<skill name="memucode-guide">', str(backend.requests[-1].messages[-1]["content"]))
        self.assertNotIn("# About MemuCode itself", backend.requests[-1].system)

    def test_help_ask_forces_the_guide_and_works_offline(self):
        runtime, backend = make_runtime(turns=["answer"])
        session = runtime.session
        runtime.selfdoc_forced_for = (session.id, len(session.user_prompts()) + 1)
        asyncio.run(Agent(runtime).run_turn("where are you configured", kind="command"))
        self.assertIn('<skill name="memucode-guide">', str(backend.requests[-1].messages[-1]["content"]))
        lines = selfdoc.offline_answer("как включить планирование")
        self.assertTrue(lines and "/plan" in lines[0], lines)
        self.assertTrue(any("/auto-continue" in line for line in selfdoc.offline_answer("лимиты кончились")))
        from app.cli.commands.guide_cmds import offline_markdown
        self.assertIn("/help", offline_markdown("zzzz qqqq"))

    def test_trigger(self):
        yes = ["Как включить в MemuCode CLI планирование?", "как включить планирование?", "что делает /compact?",
               "Какие у тебя горячие клавиши?", "how do I switch the model?", "Как сменить модель?",
               "как в MemuCode продолжить прошлую сессию", "What slash commands do you have?",
               "как отменить автопродолжение", "where are MemuCode settings stored?", "есть ли тут режим плана?"]
        no = ["fix the failing test in tests/test_api.py", "почини баг в app/cli/agent.py где memucode падает",
              "напиши функцию сортировки", "what does this function do?", "/plan", "объясни, как работает этот код",
              "add a /health endpoint to the server", "Refactor the model class in models.py"]
        for text in yes:
            self.assertTrue(selfdoc.is_self_question(text), text)
        for text in no:
            self.assertFalse(selfdoc.is_self_question(text), text)

    def test_docs_of_memucode_are_not_outside(self):
        from app.cli.tools.files import own_output
        from app.docs import docs_dir
        self.assertTrue(own_output(docs_dir() / "en" / "memucode-cli.md"))
        self.assertFalse(own_output(docs_dir().parent / "main.py"))
        self.assertIn(str(docs_dir() / "en" / "memucode-cli.md"), selfdoc.build_guide())

class Docs(unittest.TestCase):
    def test_user_docs_describe_both_features(self):
        root = Path(__file__).resolve().parents[1] / "app" / "docs"
        for lang in ("ru", "en"):
            text = (root / lang / "memucode-cli.md").read_text(encoding="utf-8")
            self.assertIn("/auto-continue", text, lang)
            self.assertIn("/help-ask", text, lang)

LIVE_QUESTIONS = (
    ("Как включить в MemuCode CLI планирование?", [r"/plan|shift\s*\+\s*tab"], [r"--plan\b"]),
    ("Как в MemuCode сменить модель?", [r"/model|alt\s*\+\s*p"], []),
    ("How do I continue an old conversation in MemuCode?", [r"/resume|/continue|memucode -c|-r\b"], []),
    ("Лимиты закончились — как сделать, чтобы MemuCode сам продолжил в 18:30?", [r"/auto-continue 18:30"], []),
    ("Какие горячие клавиши у MemuCode для режима разрешений и отката?", [r"shift\s*\+\s*tab", r"esc"], []),
)

@unittest.skipUnless(os.environ.get("MEMUCODE_LIVE_SELFDOC") == "1", "live check: MEMUCODE_LIVE_SELFDOC=1 + keys")
class LiveSelfQuestions(unittest.TestCase):
    def ask(self, base, key, model):
        from app.cli.llm import OpenAIBackend
        runtime, _ = make_runtime()
        backend = OpenAIBackend({"name": "live", "base_url": base, "api_key": key})
        runtime.router.resolve = lambda name: (backend, model)

        async def everything():
            rows = []
            for question, _must, _never in LIVE_QUESTIONS:
                runtime.new_session()
                result = await Agent(runtime, model=model).run_turn(question)
                rows.append((question, result.text or result.error))
            await backend.close()
            return rows

        answers = asyncio.run(everything())
        report = os.environ.get("MEMUCODE_LIVE_REPORT")
        if report:
            with open(report, "a", encoding="utf-8") as handle:
                for question, answer in answers:
                    handle.write(f"\n## {model}: {question}\n\n{answer}\n")
        for (question, must, never), (_q, answer) in zip(LIVE_QUESTIONS, answers):
            low = (answer or "").lower()
            for pattern in must:
                self.assertRegex(low, pattern, f"{model}: {question}\n{answer}")
            for pattern in never:
                self.assertNotRegex(low, pattern, f"{model}: {question}\n{answer}")
        return answers

    def test_aws9(self):
        if not os.environ.get("AWS9_KEY"):
            self.skipTest("AWS9 keys absent")
        self.ask(os.environ["AWS9_BASE"], os.environ["AWS9_KEY"], os.environ.get("AWS9_MODEL", "gpt-6.1-sol"))

    def test_reform(self):
        if not os.environ.get("REFORM_KEY"):
            self.skipTest("REFORM keys absent")
        self.ask(os.environ["REFORM_BASE"], os.environ["REFORM_KEY"], os.environ.get("REFORM_MODEL", "qwen3.8-flash"))

if __name__ == "__main__":
    unittest.main()
