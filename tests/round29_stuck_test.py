"""Round 29 — stuck detector (item 12): app/stuck_detector.py, app/stuck_api.py, pgstatic/stuck.js/.css and the
hooks in DFY (app/dfy.py), Work Browser / Computer Use (app/automation_runtime.py), MemuCode CLI (app/cli/agent.py)
and Work in the window (app.js handlePostGeneration).

Synthetic traces (tests/round29_stuck_traces.json) run through the Python detector AND the JS mirror
(tests/round29_stuck_runner.cjs) — both must give the same verdict at every step."""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from app import stuck_detector as sd

ROOT = Path(__file__).resolve().parents[1]
TRACES = json.loads((ROOT / "tests" / "round29_stuck_traces.json").read_text(encoding="utf-8"))["traces"]

def _steps(trace):
    if "repeat" in trace:
        return [trace["repeat"]["step"]] * trace["repeat"]["times"]
    return trace["steps"]

def run_python(trace, sensitivity="normal"):
    detector = sd.StuckDetector(sensitivity)
    out = []
    for st in _steps(trace):
        verdict = detector.observe(st["a"], state=st.get("s"), progress=st.get("p"), claimed=bool(st.get("c")),
                                   files_changed=st.get("f", 0), model_stuck=bool(st.get("stuck")),
                                   model_reason="model" if st.get("stuck") else "")
        if st.get("e"):
            detector.note_result(error=st["e"])
        out.append(f"{verdict.level}:{verdict.reason}")
    return out

def first_pause(verdicts):
    return next((i + 1 for i, v in enumerate(verdicts) if v.startswith("pause")), 0)

class DetectorTraces(unittest.TestCase):
    def test_every_trace(self):
        for trace in TRACES:
            with self.subTest(trace["name"]):
                verdicts = run_python(trace)
                self.assertEqual(verdicts[-1], trace["expect"], verdicts)
                self.assertEqual(first_pause(verdicts), trace["first_pause"], verdicts)

    def test_sensitivity_changes_the_threshold(self):
        trace = next(t for t in TRACES if t["name"] == "repeat_same_click_unchanged_page")
        longer = {**trace, "steps": trace["steps"] * 2}
        self.assertEqual(first_pause(run_python(longer, "high")), 3)
        self.assertEqual(first_pause(run_python(longer, "normal")), 4)
        self.assertEqual(first_pause(run_python(longer, "low")), 6)
        self.assertEqual(first_pause(run_python(longer, "off")), 0)

    def test_off_never_judges(self):
        for trace in TRACES:
            self.assertTrue(all(v == "ok:" for v in run_python(trace, "off")), trace["name"])

    def test_continue_makes_it_patient_and_restarts(self):
        d = sd.StuckDetector("normal")
        verdicts = [d.observe({"action": "click", "element": 1}, state="abcdabcd") for _ in range(4)]
        self.assertTrue(verdicts[-1].paused)
        d.acknowledge("continue")
        after = [d.observe({"action": "click", "element": 1}, state="abcdabcd").level for _ in range(6)]
        self.assertNotIn("pause", after[:5])
        self.assertEqual(after[5], "pause")
        d.acknowledge("hint")
        self.assertEqual(d.observe({"action": "click", "element": 1}, state="abcdabcd").level, "ok")

    def test_card_contract(self):
        d = sd.StuckDetector("normal", area="dfy")
        for _ in range(3):
            d.observe({"action": "click", "element": 4, "reason": "x"}, state="0123456789ab", label="click element 4")
        v = d.observe({"action": "click", "element": 4}, state="0123456789ab", label="click element 4")
        card = v.to_card()
        for key in ("id", "kind", "reason", "area", "title", "title_en", "detail", "detail_en", "steps", "options"):
            self.assertIn(key, card)
        self.assertEqual(card["options"], ["continue", "hint", "replan", "stop"])
        self.assertEqual(card["steps"], [{"label": "click element 4", "count": 4}])
        self.assertIsNone(re.search(r"[А-Яа-яЁё]", card["title_en"] + card["detail_en"]))
        self.assertIn("×4", sd.telegram_text(card, "en"))
        self.assertIn("Продолжить", sd.telegram_text(card, "ru"))

    def test_signature_ignores_reasons_and_buckets_coordinates(self):
        a = sd.action_signature({"action": "click", "x": 500, "y": 300, "reason": "first"})
        b = sd.action_signature({"action": "click", "x": 504, "y": 297, "reason": "second", "id": "call_1"})
        c = sd.action_signature({"action": "click", "x": 560, "y": 300})
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertEqual(sd.action_signature({"action": "type", "text": "  Hello   World "}),
                         sd.action_signature({"action": "type", "text": "hello world"}))

    def test_decisions(self):
        self.assertEqual(sd.parse_decision({"decision": "hint", "hint": "use search"}), {"decision": "hint", "hint": "use search"})
        self.assertEqual(sd.parse_decision({"decision": "hint"})["decision"], "continue")
        self.assertEqual(sd.parse_decision("Сменить подход")["decision"], "replan")
        self.assertEqual(sd.parse_decision("Stop")["decision"], "stop")
        self.assertEqual(sd.parse_decision("Продолжить")["decision"], "continue")
        self.assertEqual(sd.parse_decision("click the blue button"), {"decision": "hint", "hint": "click the blue button"})
        self.assertEqual(sd.parse_decision({"approve": False})["decision"], "stop")
        self.assertIn("CHANGE THE APPROACH", sd.model_note("replan"))
        self.assertIn("follow it: x", sd.model_note("hint", "x"))
        self.assertEqual(sd.normalize_sensitivity("HIGH"), "high")
        self.assertEqual(sd.normalize_sensitivity("weird"), "normal")

    def test_cli_round(self):
        step = sd.cli_round([{"name": "BashOutput", "args": {"id": "b1"}}])
        self.assertTrue(step["passive"])
        step = sd.cli_round([{"name": "Read", "args": {"file_path": "a.py"}}, {"name": "__invalid__", "args": {}}])
        self.assertFalse(step["passive"])
        self.assertIn("Read a.py", step["label"])

@unittest.skipUnless(shutil.which("node"), "node is not installed")
class JsMirror(unittest.TestCase):
    def run_js(self, sensitivity="normal"):
        out = subprocess.run(["node", str(ROOT / "tests" / "round29_stuck_runner.cjs"), sensitivity],
                             capture_output=True, text=True, timeout=60, check=True).stdout
        return {row["name"]: row for row in map(json.loads, out.strip().splitlines())}

    def test_same_verdicts_as_python(self):
        for sensitivity in ("low", "normal", "high", "off"):
            js = self.run_js(sensitivity)
            for trace in TRACES:
                with self.subTest(sensitivity=sensitivity, trace=trace["name"]):
                    self.assertEqual(js[trace["name"]]["verdicts"], run_python(trace, sensitivity))

    def test_card_and_decisions(self):
        js = self.run_js()
        html = js["__card__"]["html"]
        for marker in ('data-stuck-act="continue"', 'data-stuck-act="hint-open"', 'data-stuck-act="replan"',
                       'data-stuck-act="stop"', 'data-stuck-source="browser"', "×3", "Continue", "Change approach"):
            self.assertIn(marker, html)
        self.assertIsNone(re.search(r"[А-Яа-яЁё]", html), "English card must have no Cyrillic")
        work = js["__work__"]
        self.assertEqual(work["stalled"], [False, False, False, False, True, False])
        self.assertEqual(work["paused"], "plan_stalled")
        self.assertFalse(any(work["busy"]), "edits of different files are progress")
        self.assertEqual(work["goalReason"], "no_evidence")
        self.assertTrue(work["tools"][-1])
        self.assertFalse(any(work["polls"]), "polling agent_status is not a loop")
        values = js["__parse__"]["values"]
        self.assertEqual([v["decision"] for v in values], ["hint", "replan", "stop", "hint", "continue", "stop"])

from tests import round24_dfy_test as r24   # noqa: E402

class DFYPause(r24.DFYBase):
    async def stuck_task(self, extra=()):
        model = r24.ScriptedModel([{"action": "click", "element": 4}] * 4 + list(extra))
        mgr = self.manager(model)
        task = await mgr.create("x")
        await self.wait_status(mgr, task, ("waiting_user",))
        self.assertEqual(task.question["kind"], "stuck")
        self.assertEqual(len(self.browser.clicked), 3)
        return mgr, task, model

    async def test_pause_card_is_a_question(self):
        mgr, task, _ = await self.stuck_task()
        q = task.public()["question"]
        self.assertEqual(q["card"]["reason"], "repeat")
        self.assertEqual(len(q["choices"]), 4)
        self.assertTrue(q["prompt_en"])
        self.assertEqual(task.steps[-1]["action"], "stuck")
        await mgr.cancel(task.id)

    async def test_continue_resumes_without_repeating_the_held_action(self):
        mgr, task, model = await self.stuck_task([{"action": "done", "result": "ok"}])
        await mgr.answer(task.id, {"decision": "continue"})
        await self.wait_status(mgr, task, ("done",))
        self.assertEqual(len(self.browser.clicked), 3)
        self.assertIn("chose to continue", "".join(task.history))

    async def test_hint_reaches_the_model(self):
        mgr, task, model = await self.stuck_task([{"action": "done", "result": "ok"}])
        await mgr.answer(task.id, {"answer": "use the search field at the top"})
        await self.wait_status(mgr, task, ("done",))
        self.assertIn("use the search field at the top", model.prompts[-1])
        self.assertIn("Hint after a stuck pause", model.prompts[-1])

    async def test_replan_and_stop(self):
        mgr, task, model = await self.stuck_task([{"action": "done", "result": "ok"}])
        await mgr.answer(task.id, {"decision": "replan"})
        await self.wait_status(mgr, task, ("done",))
        self.assertIn("CHANGE THE APPROACH", model.prompts[-1])
        self.browser.clicked.clear()
        mgr, task, _ = await self.stuck_task()
        await mgr.answer(task.id, {"decision": "stop"})
        await self.wait_status(mgr, task, ("cancelled",))
        self.assertIn("паузы", task.error)

    async def test_model_stuck_action_pauses(self):
        model = r24.ScriptedModel([{"action": "stuck", "reason": "a captcha blocks me"}, {"action": "done", "result": "ok"}])
        mgr = self.manager(model)
        task = await mgr.create("x")
        await self.wait_status(mgr, task, ("waiting_user",))
        self.assertEqual(task.question["card"]["reason"], "model_signal")
        self.assertIn("captcha", task.question["card"]["detail"])
        await mgr.answer(task.id, {"decision": "continue"})
        await self.wait_status(mgr, task, ("done",))

    async def test_a_card_number_is_not_accepted_as_a_hint(self):
        mgr, task, _ = await self.stuck_task()
        with self.assertRaises(ValueError):
            await mgr.answer(task.id, {"decision": "hint", "hint": "4111 1111 1111 1111"})
        await mgr.cancel(task.id)

    async def test_stuck_api_answers_dfy(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import stuck_api
        mgr, task, _ = await self.stuck_task([{"action": "done", "result": "ok"}])
        app = FastAPI()
        app.state.dfy_manager = mgr
        stuck_api.register(app, runtimes={})
        pauses = stuck_api.collect_pauses(app, {})
        self.assertEqual(pauses[0]["source"], "dfy")
        self.assertEqual(pauses[0]["task_id"], task.id)

        route = next(r for r in app.routes if getattr(r, "path", "") == "/api/stuck/decide")

        class Req:
            async def json(self_inner):
                return {"source": "dfy", "task_id": task.id, "decision": "continue"}
        result = await route.endpoint(Req())
        self.assertEqual(result, {"ok": True})
        await self.wait_status(mgr, task, ("done",))

from tests import round24_automation_test as r24a   # noqa: E402
from app import automation_runtime as ar   # noqa: E402

class AutomationPause(unittest.IsolatedAsyncioTestCase):
    async def test_browser_pauses_and_continues(self):
        edge = r24a.FakeEdge()
        calls = {"n": 0}

        async def completion(model, messages):
            calls["n"] += 1
            if calls["n"] > 6:
                return '{"action": "done", "report": "ok"}'
            return '{"action": "click", "element": 1, "reason": "again"}'

        job = asyncio.create_task(ar.run_browser_agent(edge, completion, model="m", task="t", max_steps=10,
                                                       stuck_sensitivity="normal"))
        for _ in range(300):
            await asyncio.sleep(0.01)
            if edge.status == "paused":
                break
        self.assertEqual(edge.status, "paused")
        state_card = sd.public_pause(edge)
        self.assertEqual(state_card["reason"], "repeat")
        clicks = len(edge.methods("Input.dispatchMouseEvent"))
        await asyncio.sleep(0.05)
        self.assertEqual(len(edge.methods("Input.dispatchMouseEvent")), clicks)
        sd.gate_of(edge).decide({"decision": "hint", "hint": "try the menu"})
        await asyncio.wait_for(job, 5)
        self.assertEqual(edge.status, "done")
        self.assertIsNone(sd.public_pause(edge))

    async def test_browser_stop_from_the_card(self):
        edge = r24a.FakeEdge()

        async def completion(model, messages):
            return '{"action": "click", "element": 1}'

        job = asyncio.create_task(ar.run_browser_agent(edge, completion, model="m", task="t", max_steps=10,
                                                       stuck_sensitivity="high"))
        for _ in range(300):
            await asyncio.sleep(0.01)
            if edge.status == "paused":
                break
        sd.gate_of(edge).decide({"decision": "stop"})
        await asyncio.wait_for(job, 5)
        self.assertEqual(edge.status, "cancelled")
        self.assertIn("паузы", edge.error)
        self.assertIn("stopped the run", edge.report)

    async def test_computer_pauses(self):
        stand = r24a.ComputerStandin([[5] * 8] * 30)

        async def completion(model, messages):
            return '{"action": "click", "x": 10, "y": 10}'

        async def perform(action, screen):
            return "clicked"
        stand.perform = perform
        job = asyncio.create_task(ar.run_computer_agent(stand, completion, model="m", task="t", ask_each_action=False,
                                                        max_steps=10, stuck_sensitivity="normal"))
        for _ in range(300):
            await asyncio.sleep(0.01)
            if stand.status == "paused":
                break
        self.assertEqual(stand.status, "paused")
        state = await stand.public_state()
        self.assertEqual(state["stuck"]["reason"], "repeat")
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)
        self.assertEqual(stand.status, "cancelled")

    def test_legacy_callers_keep_the_hard_stop(self):
        edge = r24a.FakeEdge()

        async def completion(model, messages):
            return '{"action": "click", "element": 1}'
        asyncio.run(ar.run_browser_agent(edge, completion, model="m", task="t", max_steps=10))
        self.assertEqual(edge.status, "error")

class CliGate(unittest.IsolatedAsyncioTestCase):
    def make_agent(self, answers):
        from app.cli.agent import Agent

        class UI:
            interactive = True

            def __init__(self):
                self.asked = []
                self.notices = []

            async def ask_question(self, question, options, multi=False):
                self.asked.append((question, options))
                return answers.pop(0) if answers else None

            def notice(self, text, level="info"):
                self.notices.append(text)

        class Session:
            def __init__(self):
                self.rows = []

            def append(self, row):
                self.rows.append(row)

        class Settings:
            def get(self, key, default=None):
                return "normal" if key == "stuck_sensitivity" else default

        class Runtime:
            settings = Settings()

            def memuai_prefs(self):
                return {}

        agent = Agent.__new__(Agent)
        agent.runtime, agent.ui, agent.session, agent.agent_def = Runtime(), UI(), Session(), None
        agent.depth = 0
        return agent

    async def test_pause_then_hint_then_stop(self):
        calls = [{"id": "t1", "name": "Read", "args": {"file_path": "a.py"}}]
        agent = self.make_agent(["Change approach", "Stop"])
        verdicts = [await agent._stuck_gate(calls, "turn1") for _ in range(4)]
        self.assertEqual(verdicts[:3], ["run", "run", "run"])
        self.assertEqual(verdicts[3], "skip")
        rows = agent.session.rows
        self.assertTrue(any(r.get("role") == "tool" and r.get("tool_call_id") == "t1" and "NOT executed" in r["content"]
                            for r in rows))
        self.assertTrue(any("CHANGE THE APPROACH" in str(r.get("content")) for r in rows if r.get("role") == "user"))
        verdicts = [await agent._stuck_gate(calls, "turn1") for _ in range(4)]
        self.assertEqual(verdicts[-1], "stop")

    async def test_headless_stops_instead_of_looping(self):
        calls = [{"id": "t1", "name": "Bash", "args": {"command": "make"}}]
        agent = self.make_agent([])
        agent.ui.interactive = False
        verdicts = [await agent._stuck_gate(calls, "t") for _ in range(4)]
        self.assertEqual(verdicts[-1], "stop")
        self.assertTrue(agent.ui.notices)

class Wiring(unittest.TestCase):
    def read(self, rel):
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_settings_key(self):
        from app import pgdata
        self.assertEqual(pgdata.DEFAULT_PREFERENCES["stuck_sensitivity"], "normal")
        self.assertEqual(pgdata._normalize_preferences({"stuck_sensitivity": "bogus"})["stuck_sensitivity"], "normal")
        self.assertEqual(pgdata._normalize_preferences({"stuck_sensitivity": "high"})["stuck_sensitivity"], "high")
        self.assertIn('stuck_sensitivity: "normal"', self.read("app/pgstatic/app.js"))

    def test_static_hooks(self):
        index, quick = self.read("app/pgstatic/index.html"), self.read("app/pgstatic/quick/index.html")
        for page in (index, quick):
            self.assertIn('/stuck.js?v=1.0-r29', page)
            self.assertIn('/stuck.css?v=1.1-r34', page)
        self.assertTrue((ROOT / "app/pgstatic/stuck.js").is_file())
        self.assertTrue((ROOT / "app/pgstatic/stuck.css").is_file())
        app_js = self.read("app/pgstatic/app.js")
        for hook in ('MemuStuck?.workGate(assistant, { phase: "tools"', 'MemuStuck?.workGate(assistant, { phase: "plan"',
                     'MemuStuck?.workGate(assistant, { phase: "goal"', 'MemuStuck?.automation("browser", state)',
                     'MemuStuck?.automation("computer", state)', 'MemuStuck.settingsHtml', 'MemuStuck.bindSettings'):
            self.assertIn(hook, app_js)
        self.assertIn('q.kind === "stuck" && window.MemuStuck', self.read("app/pgstatic/dfy-client.js"))
        self.assertIn('q.kind === "stuck" && window.MemuStuck', self.read("app/pgstatic/quick/quick-dfy.js"))

    def test_routes_and_packaging(self):
        self.assertIn("stuck_api.register(app", self.read("app/playground_api.py"))
        self.assertEqual(self.read("app/playground_api.py").count('stuck_sensitivity=load_preferences().get("stuck_sensitivity"'), 2)
        for rel, names in (("MemuAI.spec", ("app.stuck_detector", "app.stuck_api")), ("MemuCode.spec", ("app.stuck_detector",)),
                           ("MemuAIProxy.spec", ("app.stuck_detector",)), ("build.py", ("app.stuck_detector", "app.stuck_api"))):
            for name in names:
                self.assertIn(f"'{name}'" if rel.endswith(".spec") else f'"{name}"', self.read(rel), (rel, name))

    def test_prompts_document_the_stuck_action(self):
        from app.dfy import SYSTEM_PROMPT
        self.assertIn('"action":"stuck"', SYSTEM_PROMPT)
        self.assertIn('"action":"stuck"', ar.BROWSER_SYSTEM_PROMPT)
        self.assertIn('"action":"stuck"', ar.COMPUTER_SYSTEM_PROMPT)

    def test_docs(self):
        for lang, words in (("ru", ("зацикл", "Продолжить", "Сменить подход", "Чувствительность")),
                            ("en", ("stuck", "Continue", "Change approach", "Sensitivity"))):
            text = self.read(f"app/docs/{lang}/stuck-detector.md")
            for word in words:
                self.assertIn(word, text, lang)
        self.assertIn('"stuck-detector"', self.read("app/docs/index.json"))

    def test_js_syntax(self):
        if not shutil.which("node"):
            self.skipTest("node is not installed")
        for rel in ("app/pgstatic/stuck.js", "app/pgstatic/app.js", "app/pgstatic/dfy-client.js", "app/pgstatic/quick/quick-dfy.js"):
            subprocess.run(["node", "--check", str(ROOT / rel)], check=True, capture_output=True, timeout=60)

if __name__ == "__main__":
    unittest.main()
