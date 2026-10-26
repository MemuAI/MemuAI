"""Round 32 — MemuCode CLI: open error reports, team-style agents, token economy.

Offline (scripted models, no Proxy launch) except the classes marked live, which read keys from env and skip
without them. Run: ``python -m pytest -q tests/round32_memucode_test.py``.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import unittest
from pathlib import Path

from app.cli import compact, errors, lang, prompts
from app.cli.agent import Agent, context_overflow
from app.cli.llm import Backend, LLMError, StreamEvent, Usage
from tests.memucode_cli_test import make_runtime, run, setUpModule, tearDownModule  # noqa: F401  (module fixtures)
from tests.memucode_parallel_test import RuleBackend, block, is_worker, last_text

REAL_ERRORS = [
    (503, "Every “custom” account failed (sol): Upstream error (HTTP 503): No available accounts", "upstream_error",
     "upstream_no_accounts"),
    (503, '{"error":{"message":"No available accounts","type":"server_error"}}', "", "upstream_no_accounts"),
    (429, "Upstream rate limit (HTTP 429): No available accounts", "rate_limit_error", "upstream_no_accounts"),
    (400, "No available channel for model gpt-4o under group default (distributor)", "", "upstream_no_accounts"),
    (503, "Нет доступных аккаунтов «custom».", "service_unavailable", "proxy_no_accounts"),
    (503, "No usable “kiro” accounts.", "service_unavailable", "proxy_no_accounts"),
    (400, "This model's maximum context length is 128000 tokens. However, your messages resulted in 130512 tokens. "
          "Please reduce the length of the messages.", "", "context_overflow"),
    (400, '{"error":{"message":"prompt is too long: 210000 tokens > 200000 maximum","type":"invalid_request_error"}}',
     "", "context_overflow"),
    (400, "context_length_exceeded", "", "context_overflow"),
    (400, "The input token count (1200000) exceeds the maximum number of tokens allowed (1048576).", "",
     "context_overflow"),
    (413, "Слишком много сообщений в запросе: 612 (предел 500)", "http", "too_many_messages"),
    (413, "Too many messages in the request: 6001 (limit 5000)", "http", "too_many_messages"),
    (400, "Слишком большой запрос", "http", "payload_too_large"),
    (413, "Request Entity Too Large", "", "payload_too_large"),
    (429, "Rate limit reached for gpt-4o in organization org-x on tokens per min (TPM): Limit 30000, Used 29000",
     "", "rate_limit"),
    (429, "Request too large for gpt-4o on tokens per min (TPM): Limit 30000, Requested 45000.", "", "rate_limit"),
    (429, "Too Many Requests", "", "rate_limit"),
    (402, "Insufficient credits. Add more using https://openrouter.ai/settings/credits", "", "quota"),
    (429, "You exceeded your current quota, please check your plan and billing details.", "", "quota"),
    (401, '{"error":{"message":"Invalid or missing API key","type":"authentication_error"}}', "", "auth"),
    (401, "MemuAI Proxy rejected the request (401)", "proxy_auth", "proxy_auth"),
    (403, "Forbidden", "", "forbidden"),
    (404, "The model `gpt-9` does not exist or you do not have access to it.", "", "model_not_found"),
    (504, "upstream request timeout", "", "timeout"),
    (0, "Lost the connection to MemuAI Proxy (ReadError)", "network", "network"),
    (0, "MemuAI Proxy does not respond", "offline", "proxy_offline"),
    (502, "Upstream error (HTTP 502): Bad gateway", "upstream_error", "server"),
    (400, "The model refused because of the content management policy", "content_filter", "content_filter"),
    (400, "Model or API rejected the image (HTTP 400). Pick a vision model", "", "image_rejected"),
]

class ErrorClassification(unittest.TestCase):
    def test_real_error_bodies_are_classified_precisely(self):
        for status, body, kind, expected in REAL_ERRORS:
            with self.subTest(body=body[:60]):
                text = errors.classify(status, body, kind)
                self.assertEqual(text.category, expected)

    def test_no_available_accounts_is_never_called_too_large_or_overflow(self):
        for status in (400, 413, 429, 500, 503):
            exc = LLMError("Every “custom” account failed (sol): Upstream error: No available accounts", status=status)
            self.assertFalse(context_overflow(exc), status)
            shown = errors.describe(exc, model="sol-gpt-6.1-sol", provider="custom")
            self.assertNotRegex(shown.lower(), r"too large|слишком больш|context window")
            self.assertIn("No available accounts", shown)
            self.assertIn(f"HTTP {status}", shown)
            self.assertIn("sol-gpt-6.1-sol", shown)

    def test_status_alone_never_means_overflow(self):
        self.assertFalse(context_overflow(LLMError("Bad request", status=400)))
        self.assertFalse(context_overflow(LLMError("HTTP 429: too many requests", status=429)))
        self.assertFalse(context_overflow(LLMError("maximum context length exceeded", status=503)))
        self.assertTrue(context_overflow(LLMError("maximum context length exceeded", status=400)))
        self.assertTrue(context_overflow(LLMError("Слишком много сообщений в запросе: 612 (предел 500)", status=413)))

    def test_describe_keeps_the_real_text_and_gives_advice(self):
        exc = LLMError("Upstream rate limit (HTTP 429): slow down", status=429, kind="rate_limit_error",
                       hint="Провайдер временно ограничил частоту запросов.")
        shown = errors.describe(exc, model="xkiro-qwen", provider="custom")
        lines = shown.splitlines()
        self.assertEqual(len(lines), 3)
        self.assertIn("slow down", lines[1])
        self.assertIn("HTTP 429", lines[0])
        self.assertTrue(lines[2])

class ErrorsReachTheUser(unittest.TestCase):
    """The turn error the terminal/headless shows is the open report, not a reworded guess."""

    def test_a_503_no_available_accounts_turn_error(self):
        async def scenario():
            runtime, _ = make_runtime()

            class Failing(Backend):
                name = "failing"
                native_tools = False
                calls = 0

                async def stream(self, request):
                    Failing.calls += 1
                    raise LLMError("Every “custom” account failed (sol): Upstream error (HTTP 503): No available "
                                   "accounts", status=503, kind="upstream_error", provider="custom")
                    yield  # pragma: no cover

            backend = Failing()
            runtime.router.resolve = lambda model: (backend, model)
            original = asyncio.sleep

            async def quick(_seconds, *args, **kwargs):
                await original(0)
            asyncio.sleep = quick
            try:
                result = await Agent(runtime).run_turn("hello")
            finally:
                asyncio.sleep = original
            return result, Failing.calls

        result, calls = run(scenario())
        self.assertEqual(result.stopped, "error")
        self.assertEqual(result.error_category, "upstream_no_accounts")
        self.assertIn("No available accounts", result.error)
        self.assertIn("HTTP 503", result.error)
        self.assertNotIn("Слишком большой", result.error)
        self.assertEqual(calls, 3)

    def test_the_proxy_message_cap_triggers_compaction_not_a_dead_end(self):
        async def scenario():
            runtime, _ = make_runtime()
            state = {"n": 0}

            class Capped(Backend):
                name = "capped"
                native_tools = False

                async def stream(self, request):
                    state["n"] += 1
                    if state["n"] == 1:
                        raise LLMError("Слишком много сообщений в запросе: 612 (предел 500)", status=413)
                    yield StreamEvent("text", text="done after compaction")
                    yield StreamEvent("done", finish="stop")

            backend = Capped()
            runtime.router.resolve = lambda model: (backend, model)
            called = {"compact": 0}

            async def fake_compact(rt, reason="manual", instructions=""):
                called["compact"] += 1
                return {"ok": True}
            original = compact.compact
            compact.compact = fake_compact
            try:
                result = await Agent(runtime).run_turn("go")
            finally:
                compact.compact = original
            return result, called["compact"]

        result, compactions = run(scenario())
        self.assertEqual(result.text, "done after compaction")
        self.assertEqual(compactions, 1)

    def test_recovery_notice_names_the_real_reason(self):
        """Finisher: the Proxy message cap is not «did not fit the model's context»."""
        async def scenario(message, status):
            runtime, _ = make_runtime()
            notices = []
            runtime.ui.notice = lambda text, level="info": notices.append(text)
            state = {"n": 0}

            class Once(Backend):
                name = "once"
                native_tools = False

                async def stream(self, request):
                    state["n"] += 1
                    if state["n"] == 1:
                        raise LLMError(message, status=status)
                    yield StreamEvent("text", text="ok")
                    yield StreamEvent("done", finish="stop")

            backend = Once()
            runtime.router.resolve = lambda model: (backend, model)

            async def fake_compact(rt, reason="manual", instructions=""):
                return {"ok": True}
            original = compact.compact
            compact.compact = fake_compact
            try:
                await Agent(runtime).run_turn("go")
            finally:
                compact.compact = original
            return " ".join(notices)

        cap = run(scenario("Too many messages in the request: 6001 (limit 5000)", 413))
        self.assertIn("too many messages", cap)
        self.assertNotIn("model's context", cap)
        window = run(scenario("This model's maximum context length is 128000 tokens.", 400))
        self.assertIn("did not fit the model's context", window)

    def test_message_cap_advice_does_not_promise_a_compaction_that_already_ran(self):
        info = errors.classify(413, "Too many messages in the request: 6001 (limit 5000)", "http")
        self.assertEqual(info.category, "too_many_messages")
        self.assertIn("/clear", info.advice)
        self.assertNotRegex(info.advice, r"[А-Яа-яЁё]")

class ProxyErrorTexts(unittest.TestCase):
    def test_custom_provider_keeps_the_upstream_words_for_429_and_402(self):
        from app.providers.base import ProviderQuotaError, ProviderRateLimitError, ProviderServerError
        from app.providers.custom import CustomProvider
        provider = CustomProvider()

        async def raised(status, head):
            try:
                await provider._raise_for_status(status, head)
            except Exception as exc:  # noqa: BLE001
                return exc
        rate = run(raised(429, '{"error":{"message":"No available accounts","type":"server_error"}}'))
        self.assertIsInstance(rate, ProviderRateLimitError)
        self.assertIn("No available accounts", str(rate))
        self.assertTrue(str(rate).startswith("Upstream rate limit (HTTP 429)"))
        quota = run(raised(402, '{"error":{"message":"Insufficient credits"}}'))
        self.assertIsInstance(quota, ProviderQuotaError)
        self.assertIn("Insufficient credits", str(quota))
        long_body = json.dumps({"error": {"message": "x" * 40 + " No available accounts", "type": "server_error",
                                          "param": None, "code": "y" * 200}})
        server = run(raised(503, long_body))
        self.assertIsInstance(server, ProviderServerError)
        self.assertIn("No available accounts", str(server))
        self.assertNotIn('{"error"', str(server))

    def test_completion_error_hint_for_no_available_accounts(self):
        from app.chat import CompletionError
        exc = CompletionError("Все аккаунты «custom» недоступны (sol): Ошибка апстрима (HTTP 503): "
                              "No available accounts", status=503, err_type="upstream_error",
                              message_en="Every “custom” account failed (sol): Upstream error (HTTP 503): "
                                         "No available accounts", provider="custom")
        self.assertIn("не размер", exc.hint)
        self.assertIn("not the size", exc.hint_en)
        self.assertEqual(exc.provider, "custom")
        plain = CompletionError("Upstream rate limit (HTTP 429)", status=429, err_type="rate_limit_error")
        self.assertIn("частоту", plain.hint)

    def test_pg_chat_message_cap_is_high_and_named(self):
        from app import admin_api
        self.assertGreaterEqual(admin_api.PG_CHAT_MAX_MESSAGES, 5000)
        self.assertEqual(admin_api._err_en("Слишком много сообщений в запросе: 6001 (предел 5000)"),
                         "Too many messages in the request: 6001 (limit 5000)")
        source = Path(admin_api.__file__).read_text(encoding="utf-8")
        self.assertNotIn("len(messages) > 500", source)

def _worker_name(request) -> str:
    first = request.messages[0]["content"]
    first = first if isinstance(first, str) else json.dumps(first, ensure_ascii=False)
    found = re.search(r"JOB-(\w+)", first)
    return found.group(1) if found else "?"

def _all_text(request) -> str:
    return "\n".join(m["content"] if isinstance(m["content"], str) else json.dumps(m["content"], ensure_ascii=False)
                     for m in request.messages)

class TeamAgents(unittest.TestCase):
    def test_name_role_and_tools_reach_the_agent(self):
        async def scenario():
            runtime, _ = make_runtime()
            backend = RuleBackend(lambda request: _ret("Result: ok"))
            runtime.router.resolve = lambda model: (backend, model)
            job = runtime.jobs.start("JOB-a look around", name="Scout", role="read-only explorer of src/",
                                     tools=["read", "Grep"], origin="model")
            await job["task"]
            return job, backend.requests[0]

        job, request = run(scenario())
        self.assertEqual(job["name"], "scout")
        self.assertEqual(job["status"], "done")
        self.assertIn("Your role in this team: read-only explorer of src/", request.system)
        self.assertIn("Result:", request.system)
        listing = request.system.split("Available tools:")[-1]
        self.assertIn("Read", listing)
        self.assertIn("Grep", listing)
        self.assertNotIn("\n- Bash", listing)
        self.assertNotIn("Write", listing)

    def test_follow_up_reaches_a_running_agent_at_its_next_step(self):
        async def scenario():
            runtime, _ = make_runtime()
            gate = asyncio.Event()

            async def handler(request):
                text = _all_text(request)
                if "Follow-up instructions from the main agent" in text:
                    return "Result: also checked README as asked"
                if "<tool_results" not in last_text(request):
                    await asyncio.wait_for(gate.wait(), 5)
                    return block(("LS", {"path": "."}))
                return "Result: done without follow-up"

            backend = RuleBackend(handler)
            runtime.router.resolve = lambda model: (backend, model)
            job = runtime.jobs.start("JOB-b list files", name="lister", origin="model")
            await asyncio.sleep(0.05)
            outcome = runtime.jobs.send("lister", "Also check the README")
            gate.set()
            await job["task"]
            return job, outcome

        job, outcome = run(scenario())
        self.assertTrue(outcome["queued"])
        self.assertEqual(job["result"], "Result: also checked README as asked")
        self.assertEqual(job["inbox"], [])

    def test_follow_up_to_a_finished_agent_resumes_it_with_its_context(self):
        async def scenario():
            runtime, _ = make_runtime()

            async def handler(request):
                text = _all_text(request)
                if "Follow-up from the main agent" in text:
                    return "Result: second answer; remembered " + ("FIRST" if "first answer" in text else "nothing")
                return "Result: first answer"

            backend = RuleBackend(handler)
            runtime.router.resolve = lambda model: (backend, model)
            job = runtime.jobs.start("JOB-c answer", name="helper", origin="model")
            await job["task"]
            first = job["result"]
            outcome = runtime.jobs.send(job["id"], "now the second part")
            await job["task"]
            return job, first, outcome

        job, first, outcome = run(scenario())
        self.assertEqual(first, "Result: first answer")
        self.assertTrue(outcome["resumed"])
        self.assertEqual(job["result"], "Result: second answer; remembered FIRST")
        self.assertEqual(job["turns"], 2)

    def test_wait_any_all_and_cancel(self):
        async def scenario():
            runtime, _ = make_runtime()
            release = {"fast": asyncio.Event(), "slow": asyncio.Event()}

            async def handler(request):
                name = _worker_name(request)
                await asyncio.wait_for(release[name].wait(), 5)
                return f"Result: {name} finished"

            backend = RuleBackend(handler)
            runtime.router.resolve = lambda model: (backend, model)
            fast = runtime.jobs.start("JOB-fast", name="fast", origin="model")
            slow = runtime.jobs.start("JOB-slow", name="slow", origin="model")
            release["fast"].set()
            rows = await runtime.jobs.wait(["fast", "slow"], mode="any", timeout=3)
            any_state = {job["name"]: job["status"] for job in rows}
            stopped = runtime.jobs.cancel("slow")
            await asyncio.sleep(0.05)
            rows = await runtime.jobs.wait(["fast", "slow"], mode="all", timeout=3)
            return any_state, stopped, {job["name"]: job["status"] for job in rows}, \
                [runtime.jobs.summary(job) for job in rows]

        any_state, stopped, all_state, summaries = run(scenario())
        self.assertEqual(any_state, {"fast": "done", "slow": "running"})
        self.assertTrue(stopped)
        self.assertEqual(all_state, {"fast": "done", "slow": "cancelled"})
        self.assertEqual(summaries[0]["report"], "Result: fast finished")

    def test_main_agent_drives_agents_with_the_agents_tool(self):
        async def scenario():
            runtime, _ = make_runtime()
            step = {"n": 0}
            seen = []
            release = asyncio.Event()

            async def handler(request):
                if is_worker(request):
                    text = _all_text(request)
                    name = _worker_name(request)
                    if "Follow-up" in text:
                        return f"Result: {name} handled the follow-up"
                    await asyncio.wait_for(release.wait(), 5)
                    return f"Result: {name} explored"
                step["n"] += 1
                seen.append(_all_text(request))
                if step["n"] == 1:
                    return block(("Task", {"description": "explore api", "prompt": "JOB-api explore app/api",
                                           "name": "api", "role": "explorer", "run_in_background": True}),
                                 ("Task", {"description": "explore ui", "prompt": "JOB-ui explore app/ui",
                                           "name": "ui", "run_in_background": True}))
                if step["n"] == 2:
                    release.set()
                    return block(("Agents", {"action": "wait", "agents": ["api", "ui"], "mode": "all", "timeout": 5}))
                if step["n"] == 3:
                    return block(("Agents", {"action": "send", "agent": "api", "message": "also list the routes"}))
                if step["n"] == 4:
                    return block(("Agents", {"action": "wait", "agent": "api", "timeout": 5}))
                if step["n"] == 5:
                    return block(("Agents", {"action": "list"}))
                return "ALL DONE"

            backend = RuleBackend(handler)
            runtime.router.resolve = lambda model: (backend, model)
            result = await Agent(runtime).run_turn("use two agents in parallel to explore the repo")
            return runtime, result, seen

        runtime, result, seen = run(scenario())
        self.assertEqual(result.text, "ALL DONE")
        self.assertEqual([job["name"] for job in runtime.jobs.jobs], ["api", "ui"])
        self.assertIn("Result: api explored", seen[2])
        self.assertIn("Result: ui explored", seen[2])
        self.assertIn("resumed with its previous context", seen[3])
        self.assertIn("api handled the follow-up", seen[4])
        self.assertIn('"status": "done"', seen[5])
        self.assertEqual(runtime.notes, [])

    def test_agent_tools_are_listed_only_when_agents_are_in_play(self):
        runtime, _ = make_runtime()
        names = {tool.name for tool in Agent(runtime).tools()}
        self.assertIn("Task", names)
        self.assertNotIn("Agents", names)
        self.assertNotIn("TaskOutput", names)
        runtime.settings.data["subagents"] = "auto"
        names = {tool.name for tool in Agent(runtime).tools()}
        self.assertIn("Agents", names)
        runtime.settings.data["subagents"] = "off"
        names = {tool.name for tool in Agent(runtime).tools()}
        self.assertFalse({"Task", "Agents", "TaskOutput"} & names)

async def _ret(text):
    return text

class TokenEconomy(unittest.TestCase):
    def test_text_protocol_prompt_is_compact(self):
        self.assertLess(len(prompts.TEXT_PROTOCOL), 1900)
        for must in ("How to call tools", "@content:1", "<memuai-content", "<tool_results>"):
            self.assertIn(must, prompts.TEXT_PROTOCOL)

    def test_english_requests_get_an_english_reminder(self):
        self.assertIn("English", lang.reminder(["The tests fail. Find the bug, fix it and run the tests."]))
        self.assertNotIn("may not be English", lang.reminder(["fix it"]))
        self.assertIn("Russian", lang.reminder(["Почини тесты, пожалуйста"]))
        self.assertFalse(lang.english("Corrige el error en el archivo y ejecuta las pruebas"))

    def test_big_command_output_is_cut_with_a_see_more_file(self):
        from app.cli.tools import shell
        from app.cli.tools.files import own_output
        collector = shell._Collector()
        lines = [f"line {index:06d}" for index in range(8000)]
        for start in range(0, len(lines), 100):
            collector.add("\n".join(lines[start:start + 100]) + "\n")
        text, cut = collector.text()
        self.assertTrue(cut)
        self.assertLessEqual(len(text), shell.MAX_OUTPUT_CHARS + 400)
        self.assertIn("line 007999", text)
        path = re.search(r"saved in (\S+?): Read", text).group(1)
        full = Path(path).read_text(encoding="utf-8")
        self.assertIn("line 004000", full)
        self.assertTrue(own_output(Path(path)))
        self.assertFalse(own_output(Path(tempfile.gettempdir()) / "other.log"))

    def test_old_big_results_are_folded_past_the_budget_in_batches(self):
        async def scenario():
            runtime, _ = make_runtime()
            runtime.settings.data["context_budget"] = 3000
            session = runtime.session
            for index in range(6):
                session.append({"role": "user", "kind": "prompt", "content": f"step {index}"})
                session.append({"role": "assistant", "content": "reading", "tool_calls": [
                    {"id": f"c{index}", "name": "Read", "args": {"file_path": f"f{index}.py"}}]})
                session.append({"role": "tool", "tool_call_id": f"c{index}", "name": "Read",
                                "content": ("x = 1\n" * 900)})
            first = await compact.optimize(runtime, "system", "")
            again = await compact.optimize(runtime, "system", "")
            return first, again, sum(1 for m in session.api_messages() if m.get("elided"))

        first, again, elided = run(scenario())
        self.assertTrue(first and first.get("elided"))
        self.assertIsNone(again)
        self.assertGreaterEqual(elided, 3)

    def test_a_small_budget_still_folds_in_batches_not_every_round(self):
        """Finisher: a live run with context_budget=12000 and ~5k-token Reads folded 1 result every other round."""
        async def scenario(budget):
            runtime, _ = make_runtime()
            runtime.settings.data["context_budget"] = budget
            session = runtime.session
            batches = []
            for index in range(16):
                session.append({"role": "user", "kind": "prompt", "content": f"step {index}"})
                session.append({"role": "assistant", "content": "reading", "tool_calls": [
                    {"id": f"c{index}", "name": "Read", "args": {"file_path": f"f{index}.py"}}]})
                session.append({"role": "tool", "tool_call_id": f"c{index}", "name": "Read",
                                "content": ("value = compute(alpha, beta)\n" * 700)})
                outcome = await compact.optimize(runtime, "system", "")
                if outcome and outcome.get("elided"):
                    batches.append(outcome["elided"])
            return batches

        from app.cli.tokens import estimate_text
        self.assertGreater(estimate_text("value = compute(alpha, beta)\n" * 700), 4000)
        for budget in (12000, 40000):
            batches = run(scenario(budget))
            self.assertTrue(batches, budget)
            self.assertTrue(all(size >= 2 for size in batches[1:]), (budget, batches))
            self.assertLessEqual(len(batches), 8, (budget, batches))

    def test_elide_threshold(self):
        class R:
            def __init__(self, value):
                self.settings = {"context_budget": value}
        self.assertEqual(compact.elide_threshold(R(40000), 262144), 40000)
        self.assertEqual(compact.elide_threshold(R(40000), 32000), int(32000 * compact.ELIDE_SHARE))
        self.assertEqual(compact.elide_threshold(R(0), 262144), int(262144 * compact.ELIDE_SHARE))

FOOT_LEFT = [("class:mode.bypasspermissions", "  ⏵⏵ bypass permissions"), ("class:subtle", " (shift+tab to change)"),
             ("class:info", "  ● 2 agents running · /tasks", lambda event: None)]
FOOT_RIGHT = [("class:footer", "xkiro-qwen/qwen3.8-max:free"), ("class:footer", "  ·  ctx 12%"),
              ("class:footer", "  ·  ⎇ master"), ("class:footer", "  ·  $0.0089  ")]

class FooterFits(unittest.TestCase):
    def _line(self, width, left=FOOT_LEFT, right=FOOT_RIGHT):
        from app.cli.tui.app import _frag_width, fit_footer
        out = fit_footer(left, right, width)
        self.assertLessEqual(_frag_width(out), width - 1, f"footer overflows at {width} columns")
        return "".join(part[1] for part in out)

    def test_left_and_right_never_touch_at_any_width(self):
        for width in (40, 50, 60, 70, 80, 90, 100, 110, 120, 140, 160, 200):
            text = self._line(width)
            self.assertNotIn("agentsxkiro", text.replace(" ", "x", 0))
            self.assertNotRegex(text, r"\S(xkiro|…qwen|…)", f"parts glued at {width}: {text!r}")
            self.assertIn("●", text, f"the agents indicator vanished at {width}")

    def test_what_goes_first_when_narrow(self):
        wide = self._line(200)
        self.assertIn("(shift+tab to change)", wide)
        self.assertIn("⎇ master", wide)
        self.assertIn("2 agents running · /tasks", self._line(110))
        self.assertNotIn("shift+tab", self._line(110))
        mid = self._line(80)
        self.assertIn("xkiro-qwen/qwen3.8-max:free", mid)
        self.assertNotIn("⎇", mid)
        narrow = self._line(60)
        self.assertIn("● 2 agents", narrow)
        self.assertIn("qwen3.8-max:free", narrow)
        tiny = self._line(40)
        self.assertIn("● 2 agents", tiny)
        self.assertNotIn("qwen", tiny)

    def test_click_handlers_survive_cutting(self):
        from app.cli.tui.app import fit_footer
        for width in (40, 60, 110):
            out = fit_footer(FOOT_LEFT, FOOT_RIGHT, width)
            self.assertTrue(any(len(part) > 2 and "●" in part[1] for part in out), width)

    def test_custom_statusline_and_plain_left(self):
        text = self._line(50, [("class:mode.default", "  ○ ask first")], [("class:footer", "x" * 80 + "  ")])
        self.assertTrue(text.startswith("  ○ ask first"))
        self.assertIn("…", text)

    def test_footer_is_one_window(self):
        source = Path(__file__).resolve().parents[1].joinpath("app/cli/tui/app.py").read_text(encoding="utf-8")
        self.assertIn("FormattedTextControl(self._footer_line)", source)

class ErrorViewWraps(unittest.TestCase):
    def test_long_error_lines_keep_a_hanging_indent(self):
        from app.cli.tui.render import Renderer
        from app.cli.tui.theme import palette_for
        renderer = Renderer(palette_for("dark", None), None)
        renderer.width = 60
        out = renderer.error("The account is out of credits or quota (HTTP 429 · custom · sol-gpt-6.1-sol)",
                             "Upstream said: Every “custom” account failed (sol): Upstream rate limit (HTTP 429): "
                             "API key quota exceeded\nWhat to do: Top up, wait for the quota to reset.")
        out = re.sub(r"\x1b\[[0-9;]*m", "", out)
        lines = [line.rstrip() for line in out.splitlines() if line.strip()]
        self.assertTrue(lines[0].startswith("● The account"))
        self.assertTrue(lines[1].startswith("  "), lines)
        self.assertTrue(lines[2].startswith("  ⎿  Upstream said:"))
        for line in lines[3:]:
            self.assertTrue(line.startswith("     "), f"detail wrapped to column 0: {line!r}")
        self.assertIn("quota exceeded", out)

LIVE = os.environ.get("MEMUCODE_LIVE_PROXY")

@unittest.skipUnless(LIVE, "set MEMUCODE_LIVE_PROXY to a running MemuAI Proxy with real accounts")
class LiveProxyErrors(unittest.TestCase):
    def test_unknown_sol_model_reports_no_available_accounts_openly(self):
        import subprocess
        import sys
        env = dict(os.environ, MEMUAI_PROXY_URL=LIVE, MEMUCODE_NO_PROXY_LAUNCH="1", NO_PROXY="127.0.0.1,localhost")
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            done = subprocess.run([sys.executable, str(root / "memucode.py"), "-p", "say hi", "--model",
                                   os.environ.get("MEMUCODE_LIVE_BAD_MODEL", "sol-claude-opus-4.7"),
                                   "--output-format", "json"], cwd=folder, env=env, capture_output=True,
                                  text=True, timeout=240)
        data = json.loads(done.stdout)
        self.assertTrue(data["is_error"])
        self.assertIn("No available accounts", data["result"])
        self.assertEqual(data["error_category"], "upstream_no_accounts")

if __name__ == "__main__":
    unittest.main()
