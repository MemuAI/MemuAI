"""Commands run modes: standard vs economy (app/assistant_commands.py, assistant_router.py).

Economy must measurably spend fewer tokens than standard on the same scenario:
tool prompt -77% (8962 -> 2018 chars), router prompt -45% (5277 -> 2894 chars),
YouTube summary 13 -> 5 model calls. Nothing here touches Windows or the network.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

from app import assistant_catalog as catalog  # noqa: E402
from app import assistant_commands as ac  # noqa: E402
from app import assistant_router as router  # noqa: E402
from app import youtube_analysis as yt  # noqa: E402
from app.assistant_actions_win import Backend  # noqa: E402

NOW = datetime(2026, 10, 8, 10, 30)


def run(coro):
    return asyncio.run(coro)


class FakeBackend(Backend):
    name = "fake"

    def __init__(self):
        self.calls = []
        self.windows_calls = 0
        self.wins = [
            {"hwnd": 101, "title": "Telegram", "pid": 11, "path": r"C:\Apps\Telegram.exe", "exe": "telegram.exe",
             "app": "Telegram", "state": "normal", "rect": [100, 100, 900, 700],
             "normal_rect": [100, 100, 900, 700], "own": False},
            {"hwnd": 102, "title": "main.py — Visual Studio Code", "pid": 12, "path": r"C:\VS\Code.exe",
             "exe": "code.exe", "app": "Code", "state": "maximized", "rect": [0, 0, 1920, 1040],
             "normal_rect": [200, 150, 1400, 900], "own": False},
        ]
        self.procs = [{"name": "incy.exe", "pid": 501, "memory": 300 << 20},
                      {"name": "lsass.exe", "pid": 700, "memory": 10 << 20}]

    def windows(self, include_minimized=True):
        self.windows_calls += 1
        return [dict(w) for w in self.wins]

    def procs(self):
        return [dict(p) for p in self.procs]


class EngineCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.backend = FakeBackend()
        self.engine = ac.Engine(backend=self.backend, store=ac.Store(self.tmp / "assistant.json"),
                                audit=ac.Audit(self.tmp / "audit.jsonl"), timers=None, now_fn=lambda: NOW)
        self.platform = mock.patch.object(ac.sys, "platform", "win32")
        self.platform.start()
        self.prefs = mock.patch("app.pgdata.load_preferences",
                                return_value={"quick_ask_model": "fast-model"})
        self.prefs.start()
        self.seen_completion = []

        async def completion(model, messages):
            self.seen_completion.append((model, messages))
            return "ok"

        self.engine.completion = completion

    def tearDown(self):
        self.prefs.stop()
        self.platform.stop()

    def settings(self, **patch):
        self.engine.store.update(lambda d: d.__setitem__(
            "settings", ac.normalize_settings({**d["settings"], **patch})))

    def command(self, name="Тест", mode="inherit", steps=None):
        return ac.validate_command({"name": name, "mode": mode, "steps": steps or [
            {"type": "ask_ai", "prompt": "скажи привет"}]})


class SettingsTest(unittest.TestCase):
    def test_mode_defaults_to_standard(self):
        s = ac.normalize_settings({})
        self.assertEqual(s["mode"], "standard")
        self.assertEqual(s["router_model_eco"], "")

    def test_mode_values(self):
        self.assertEqual(ac.normalize_settings({"mode": "economy"})["mode"], "economy")
        self.assertEqual(ac.normalize_settings({"mode": "economy", "router_model_eco": "  eco-mini "})["router_model_eco"], "eco-mini")
        self.assertEqual(ac.normalize_settings({"mode": "loud"})["mode"], "standard")
        self.assertEqual(ac.normalize_settings({"mode": ""})["mode"], "standard")

    def test_validate_command_mode(self):
        self.assertEqual(ac.validate_command({"name": "x", "steps": [{"type": "say", "text": "hi"}]})["mode"], "inherit")
        self.assertEqual(ac.validate_command({"name": "x", "mode": "economy",
                                             "steps": [{"type": "say", "text": "hi"}]})["mode"], "economy")
        self.assertEqual(ac.validate_command({"name": "x", "mode": "standard",
                                             "steps": [{"type": "say", "text": "hi"}]})["mode"], "standard")
        self.assertEqual(ac.validate_command({"name": "x", "mode": "bogus",
                                             "steps": [{"type": "say", "text": "hi"}]})["mode"], "inherit")

    def test_old_stored_commands_load_as_inherit(self):
        store = ac.Store(Path(tempfile.mkdtemp()) / "assistant.json")
        store.update(lambda d: d.__setitem__("commands", [
            {"name": "Старая", "steps": [{"type": "say", "text": "hi"}]}]))
        self.assertEqual(store.load()["commands"][0]["mode"], "inherit")


class EffectiveModeTest(unittest.TestCase):
    def test_resolution_order(self):
        self.assertEqual(ac.effective_mode({"mode": "standard"}, {"mode": "economy"}), "economy")
        self.assertEqual(ac.effective_mode({"mode": "economy"}, {"mode": "inherit"}), "economy")
        self.assertEqual(ac.effective_mode({"mode": "economy"}, None), "economy")
        self.assertEqual(ac.effective_mode({"mode": "standard"}, {"mode": "standard"}), "standard")
        self.assertEqual(ac.effective_mode({}, {}), "standard")
        self.assertEqual(ac.effective_mode(None, None), "standard")

class ParallelRoutineModeTest(EngineCase):
    def test_two_routines_keep_their_own_mode(self):
        async def scenario():
            economy_started = asyncio.Event()
            standard_started = asyncio.Event()
            economy_checked = asyncio.Event()

            async def steps(command, **_):
                if command["mode"] == "economy":
                    economy_started.set()
                    await standard_started.wait()
                    economy_checked.set()
                else:
                    await economy_started.wait()
                    standard_started.set()
                    await economy_checked.wait()
                return self.engine.mode()

            with mock.patch.object(self.engine, "_run_routine_steps", side_effect=steps):
                return await asyncio.wait_for(asyncio.gather(
                    self.engine.run_routine(self.command(mode="economy")),
                    self.engine.run_routine(self.command(mode="standard")),
                ), timeout=2)

        self.assertEqual(run(scenario()), ["economy", "standard"])


class PromptSizeTest(unittest.TestCase):
    """Fixed scenario: the full action catalog + 24 running apps with titles + 20 commands."""

    def setUp(self):
        self.std = ac.normalize_settings({})
        self.eco = ac.normalize_settings({"mode": "economy"})
        self.ctx = {"running": [f"App{i} — Some window title number {i} with text" for i in range(24)],
                    "commands": [f"команда {i}" for i in range(20)]}

    def test_tool_prompt_economy_is_much_shorter(self):
        std = ac.tool_prompt(self.std, "en")
        eco = ac.tool_prompt(self.eco, "en", economy=True)
        self.assertIn("volume.set(", eco)
        self.assertNotIn("shell.run(", eco)
        self.assertLess(len(eco), len(std) * 0.5, f"economy tool prompt {len(eco)} chars vs standard {len(std)}")
        self.assertLess(len(std), 9000)

    def test_router_prompt_economy_is_shorter(self):
        std = router.build_messages("закрой инси", self.std, self.ctx)
        eco = router.build_messages("закрой инси", self.eco, self.ctx, economy=True)
        std_len = sum(len(m["content"]) for m in std)
        eco_len = sum(len(m["content"]) for m in eco)
        self.assertLess(eco_len, std_len * 0.7, f"economy router {eco_len} chars vs standard {std_len}")
        self.assertLess(len(std[0]["content"]), 4500, "the standard router prompt must stay tiny")
        self.assertIn("app.close(name)", "\n".join(router.action_lines(self.std)))
        self.assertIn("app.close(name)", "\n".join(router.action_lines(self.eco, economy=True)))
        self.assertIn("Running: App0 — Some window title number 0 with text", std[1]["content"])
        self.assertIn("команда 19", std[1]["content"])
        self.assertNotIn("App12", eco[1]["content"], "economy caps the running list at 12")
        self.assertNotIn("команда 15", eco[1]["content"], "economy caps the command list at 10")

    def test_economy_action_lines_drop_enums(self):
        joined = "\n".join(router.action_lines(self.eco, economy=True))
        self.assertNotIn("=", joined, "economy action lines carry no enum values")
        self.assertIn("=", "\n".join(router.action_lines(self.std)))

    def test_running_apps_without_titles(self):
        wins = [{"app": "Telegram", "title": "A very long chat window title here", "own": False}]
        self.assertEqual(router.running_apps(wins, [], titles=False), ["Telegram"])
        self.assertEqual(router.running_apps(wins, [], titles=True)[0][:8], "Telegram")

    def test_model_result_caps(self):
        card = {"action": "app.open", "status": "done", "message": "x" * 5000,
                "answer": "y" * 5000, "details": [[str(i), "v"] for i in range(20)],
                "list": list(range(40))}
        std, eco = ac.model_result(card), ac.model_result(card, economy=True)
        self.assertEqual((len(std["message"]), len(std["answer"]), len(std["details"]), len(std["list"])),
                         (1500, 3000, 12, 30))
        self.assertEqual((len(eco["message"]), len(eco["answer"]), len(eco["details"]), len(eco["list"])),
                         (800, 1500, 6, 15))


class RouterModeTest(EngineCase):
    def answer(self, raw):
        async def call(model, messages):
            self.seen_router = (model, messages)
            return raw
        self.engine.router_call = call

    def test_economy_picks_the_cheap_router_model(self):
        self.settings(mode="economy", router_model="big-model", router_model_eco="eco-mini")
        self.answer('{"a":"chat"}')
        decision = run(router.route(self.engine, "закрой инси"))
        self.assertEqual(decision["action"], "chat")
        self.assertEqual(self.seen_router[0], "eco-mini")

    def test_standard_ignores_the_cheap_router_model(self):
        self.settings(mode="standard", router_model="big-model", router_model_eco="eco-mini")
        self.answer('{"a":"chat"}')
        run(router.route(self.engine, "закрой инси"))
        self.assertEqual(self.seen_router[0], "big-model")

    def test_economy_without_cheap_model_uses_the_usual_one(self):
        self.settings(mode="economy", router_model_eco="")
        self.answer('{"a":"chat"}')
        run(router.route(self.engine, "закрой инси"))
        self.assertEqual(self.seen_router[0], "fast-model")

    def test_economy_caches_live_context(self):
        self.settings(mode="economy")
        self.answer('{"a":"chat"}')
        run(router.route(self.engine, "закрой инси"))
        run(router.route(self.engine, "закрой инси"))
        self.assertEqual(self.backend.windows_calls, 1, "economy re-reads the desktop at most once per 10 s")

    def test_standard_reads_live_context_every_time(self):
        self.answer('{"a":"chat"}')
        run(router.route(self.engine, "закрой инси"))
        run(router.route(self.engine, "закрой инси"))
        self.assertEqual(self.backend.windows_calls, 2)


class AskAiModeTest(EngineCase):
    def test_ask_ai_system_prompt_standard_vs_economy(self):
        cmd = self.command()
        run(self.engine.run_routine(cmd, lang="ru"))
        std_system = self.seen_completion[-1][1][0]["content"]
        self.assertIn("Local time:", std_system)
        self.assertIn("Answer briefly and directly", std_system)

        self.settings(mode="economy")
        self.seen_completion.clear()
        run(self.engine.run_routine(cmd, lang="ru"))
        eco_system = self.seen_completion[-1][1][0]["content"]
        self.assertNotIn("Local time:", eco_system)
        self.assertLess(len(eco_system), len(std_system))
        self.assertEqual(eco_system, ac.ASK_AI_SYSTEM_ECO)

    def test_nested_command_inherits_outer_mode(self):
        inner = self.command(name="Внутренняя", mode="inherit")
        self.engine.store.update(lambda d: d.__setitem__("commands", [inner]))
        outer = self.command(name="Внешняя", mode="economy", steps=[
            {"type": "action", "action": "command.run", "params": {"name": "Внутренняя"}}])
        run(self.engine.run_routine(outer, lang="ru"))
        self.assertEqual(self.seen_completion[-1][1][0]["content"], ac.ASK_AI_SYSTEM_ECO)

    def test_nested_command_explicit_mode_wins(self):
        inner = self.command(name="Внутренняя", mode="standard")
        self.engine.store.update(lambda d: d.__setitem__("commands", [inner]))
        outer = self.command(name="Внешняя", mode="economy", steps=[
            {"type": "action", "action": "command.run", "params": {"name": "Внутренняя"}}])
        run(self.engine.run_routine(outer, lang="ru"))
        self.assertIn("Local time:", self.seen_completion[-1][1][0]["content"])

    def test_global_mode_is_restored_after_routine(self):
        self.settings(mode="economy")
        async def scenario():
            await self.engine.run_routine(self.command(mode="standard"), lang="ru")
            self.assertTrue(self.engine.economy())
        run(scenario())


class YouTubeModeTest(unittest.TestCase):
    def test_summarize_call_counts(self):
        segs = [{"start": i * 60, "text": f"word{i} " * 1000} for i in range(20)]
        meta = {"id": "dQw4w9WgXcQ", "title": "Test", "author": "Tester"}
        calls = {"n": 0}

        async def complete(prompt: str) -> str:
            calls["n"] += 1
            return "note"

        run(yt.summarize(meta, segs, complete, mode="brief", lang="ru", max_chunks=12))
        self.assertEqual(calls["n"], 13, "standard: 12 chunks + 1 reduce call")
        calls["n"] = 0
        run(yt.summarize(meta, segs, complete, mode="brief", lang="ru", max_chunks=4))
        self.assertEqual(calls["n"], 5, "economy: 4 chunks + 1 reduce call")

    def test_analyze_accepts_max_chunks(self):
        params = inspect.signature(yt.analyze).parameters
        self.assertIn("max_chunks", params)
        self.assertEqual(params["max_chunks"].default, 12)


if __name__ == "__main__":
    unittest.main()
