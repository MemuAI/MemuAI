"""Round 31 (r31-quick): Quick Access — vision of the window's model, «no answer», Computer Use on the window's model,
copying anything (context menu / copy buttons), the «возьми управление компьютером» command.

Run: NO_PROXY=127.0.0.1,localhost MEMUAI_DATA_DIR=/tmp/r31q python -m pytest -q tests/round31_quick_test.py
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="r31q-"))

from app import assistant_catalog  # noqa: E402,F401  (_alias_tool_call looks the catalogue up in sys.modules)
from app import assistant_commands as ac  # noqa: E402
from app import assistant_intents as intents  # noqa: E402
from app import model_info  # noqa: E402
from app import quick_ask_api  # noqa: E402
from app import quick_ask_core as core  # noqa: E402
from app import quick_ask_endpoint as endpoint  # noqa: E402
from app import quick_models  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
QUICK = ROOT / "app" / "pgstatic" / "quick"

def read(name: str) -> str:
    return (QUICK / name).read_text(encoding="utf-8")

class VisionTable(unittest.TestCase):
    def test_hosted_qwen_tiers(self):
        for model in ("qwen/qwen3.8-max:free", "qwen3.8-max", "qwen/qwen3.7-flash:free", "qwen3.5-plus", "qwen3.6-omni",
                      "qwen/qwen3-vl-plus:free"):
            self.assertIs(model_info.vision_hint(model), True, model)

    def test_text_only_tiers_stay_text_only(self):
        self.assertIsNot(model_info.vision_hint("qwen/qwen3.7-max:free"), True, "qwen3.7-max answers «no vision» live")
        self.assertIsNot(model_info.vision_hint("qwen3-embedding-8b"), True)
        self.assertIsNot(model_info.vision_hint("qwen3.8-max-embedding"), True)

    def test_vision_guess_on_endpoint_rows(self):
        rows = {r["id"]: r["caps"] for r in endpoint.model_rows(["qwen3.8-max", "some-text-model-7b", "gpt-4o"])}
        self.assertEqual(rows["qwen3.8-max"].get("vision"), True)
        self.assertNotIn("vision_guess", rows["qwen3.8-max"])
        for caps in rows.values():
            if caps.get("vision") is False:
                self.assertTrue(caps.get("vision_guess"), "a «no» from the name is marked as a guess")

        vision_only = lambda caps: {k: v for k, v in caps.items() if k != "reasoning_profile"}  # noqa: E731
        for caps in (r["caps"] for r in endpoint.model_rows(["gpt-4o", "x"], "no")):
            self.assertEqual(vision_only(caps), {"vision": False})
        for caps in (r["caps"] for r in endpoint.model_rows(["x"], "yes")):
            self.assertEqual(vision_only(caps), {"vision": True})

class VisionProbeRoute(unittest.TestCase):
    def client(self, prefs, proxy_reply):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        calls = []

        async def proxy_json(method, path, body=b""):
            calls.append((method, path, json.loads(body or b"{}")))
            return proxy_reply

        app = FastAPI()
        quick_models.register(app, proxy_json=proxy_json)
        patcher = mock.patch("app.pgdata.load_preferences", return_value=prefs)
        patcher.start()
        self.addCleanup(patcher.stop)
        return TestClient(app), calls

    def test_through_proxy_probe(self):
        c, calls = self.client({}, (200, {"vision": True, "verdict": "ok", "answer": "Red"}))
        r = c.post("/api/quick/vision/probe", json={"model": "qwen/qwen3.8-max:free"}).json()
        self.assertEqual((r["ok"], r["vision"]), (True, True))
        self.assertEqual(calls, [("POST", "/api/admin/models/qwen%2Fqwen3.8-max%3Afree/probe", {"confirm": True})])

    def test_proxy_error_and_no_model(self):
        c, _ = self.client({}, (502, {"error": "Нет аккаунтов"}))
        r = c.post("/api/quick/vision/probe", json={"model": "m"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.json()["ok"], r.json()["vision"]), (False, None))
        self.assertIn("Нет аккаунтов", r.json()["error"])
        self.assertEqual(c.post("/api/quick/vision/probe", json={}).status_code, 400)

    def test_own_endpoint_probe(self):
        async def answer(color):
            async def complete(url, key, model, messages, **kw):
                self.assertEqual(messages[0]["content"][1]["type"], "image_url")
                return color
            return complete

        async def scenario(reply):
            with mock.patch("app.quick_models.random.choice", return_value="red"):
                return await quick_models.endpoint_probe("http://x/v1", "k", "m", complete=await answer(reply))
        self.assertEqual(asyncio.run(scenario("Red"))["vision"], True)
        self.assertEqual(asyncio.run(scenario("I cannot see images"))["vision"], False)

        async def rejecting(url, key, model, messages, **kw):
            raise RuntimeError("HTTP 400: image input is not supported")
        r = asyncio.run(quick_models.endpoint_probe("http://x/v1", "k", "m", complete=rejecting))
        self.assertEqual((r["ok"], r["vision"]), (True, False))

def _sse(*pieces):
    for piece in pieces:
        yield "data: " + json.dumps({"choices": [{"delta": {"content": piece}}]}, ensure_ascii=False)
    yield "data: [DONE]"

class NoSilentAnswer(unittest.TestCase):
    def run_loop(self, rounds, settings=None):
        replies = list(rounds)
        requests = []

        async def stream_lines(path, payload):
            requests.append(json.loads(payload.decode("utf-8")))
            for line in _sse(*replies.pop(0)):
                yield line

        async def run_tool(body):
            return {"tool": body["tool"], "results": []}

        async def rich(query, **kw):
            return {"query": query, "results": []}

        async def capture(args):
            return {"ok": False}

        prefs = dict(core.PREF_DEFAULTS, web_search_mode="auto", profile_name="")
        prefs.update(settings or {})

        async def collect():
            out = []
            async for chunk in quick_ask_api.ask_events(
                    text="вопрос", images=[], selection="", model="m1", history=[], vision=True, native_search=False,
                    settings=prefs, lang="ru", stream_lines=stream_lines, run_tool=run_tool, rich_search=rich,
                    rich_payload=lambda d: d, capture=capture):
                out.append(json.loads(chunk[len("data: "):]))
            return out
        return asyncio.run(collect()), requests

    def test_only_an_unknown_tool_twice_is_an_error_not_silence(self):
        block = '```memuai-tool\n[{"tool":"no_such_tool","args":{}}]\n```'
        events, requests = self.run_loop([[block], [block]])
        self.assertEqual(events[-1]["event"], "error", events[-1])
        self.assertEqual(events[-1]["code"], "tools")
        self.assertIn("no_such_tool", events[-1]["error"])
        self.assertEqual(len(requests), 2, "one more round with «there is no tool named …»")
        self.assertIn("There is no tool named", json.dumps(requests[1]["messages"], ensure_ascii=False))

    def test_unknown_tool_then_words(self):
        block = '```memuai-tool\n[{"tool":"no_such_tool","args":{}}]\n```'
        events, _ = self.run_loop([[block], ["Готово."]])
        self.assertEqual(events[-1]["event"], "done")
        self.assertEqual(events[-1]["text"], "Готово.")

    def test_empty_stream_is_an_error(self):
        events, _ = self.run_loop([["```memuai-tool\n[{\"tool\": \"web_se"]])
        self.assertEqual(events[-1]["event"], "error", events)
        self.assertIn(events[-1]["code"], ("empty", "tools", "model"))
        self.assertNotEqual(events[-1].get("text"), "")

    def test_alias_of_an_action_named_as_a_tool(self):
        allowed = {"web_search", "assistant_action"}
        got = quick_ask_api._alias_tool_call({"tool": "computer.use", "args": {"task": "открой блокнот"}}, allowed)
        self.assertEqual(got["tool"], "assistant_action")
        self.assertEqual(got["args"], {"action": "computer.use", "params": {"task": "открой блокнот"}})
        got = quick_ask_api._alias_tool_call({"tool": "volume_set", "args": {"params": {"level": 30}}}, allowed)
        self.assertEqual(got["args"], {"action": "volume.set", "params": {"level": 30}})

        self.assertEqual(quick_ask_api._alias_tool_call({"tool": "web_search", "args": {}}, allowed)["tool"], "web_search")
        self.assertEqual(quick_ask_api._alias_tool_call({"tool": "frobnicate", "args": {}}, allowed)["tool"], "frobnicate")
        self.assertEqual(quick_ask_api._alias_tool_call({"tool": "computer.use", "args": {}}, {"web_search"})["tool"], "computer.use")

    def run_reasoning(self, reasoning, content):
        async def stream_lines(path, payload):
            for piece in reasoning:
                yield "data: " + json.dumps({"choices": [{"delta": {"reasoning_content": piece}}]})
            for piece in content:
                yield "data: " + json.dumps({"choices": [{"delta": {"content": piece}}]}, ensure_ascii=False)
            yield "data: [DONE]"

        async def nop(*a, **k):
            return {}
        prefs = dict(core.PREF_DEFAULTS, web_search_mode="auto", profile_name="")

        async def collect():
            out = []
            async for chunk in quick_ask_api.ask_events(
                    text="q", images=[], selection="", model="m1", history=[], vision=False, native_search=False,
                    settings=prefs, lang="ru", stream_lines=stream_lines, run_tool=nop, rich_search=nop,
                    rich_payload=lambda d: d, capture=nop):
                out.append(json.loads(chunk[len("data: "):]))
            return out
        return asyncio.run(collect())

    def test_reasoning_is_previewed_throttled_and_returned_with_done(self):
        events = self.run_reasoning(["We need ", "to count primes. "] * 200, ["17"])
        previews = [e for e in events if e.get("status") == "reasoning"]
        self.assertLessEqual(len(previews), 3, "throttled, not one event per token")
        self.assertTrue(all(len(e["text"]) <= 222 for e in previews if e.get("text")))
        self.assertEqual(events[-1]["event"], "done")
        self.assertEqual(events[-1]["text"], "17")
        self.assertTrue(events[-1]["reasoning"].endswith("to count primes."))
        self.assertLessEqual(len(events[-1]["reasoning"]), quick_ask_api.REASONING_LIMIT)

    def test_only_reasoning_is_an_explained_error(self):
        events = self.run_reasoning(["Thinking about it a lot…"], [])
        self.assertEqual((events[-1]["event"], events[-1]["code"]), ("error", "empty"))
        self.assertIn("размышляла", events[-1]["error"])
        self.assertIn("Thinking", events[-1]["reasoning"])

    def test_reasoning_tail(self):
        self.assertEqual(quick_ask_api._reasoning_tail("a  b\n c"), "a b c")
        tail = quick_ask_api._reasoning_tail("word " * 200)
        self.assertTrue(tail.startswith("…word"))
        self.assertLessEqual(len(tail), 222)
        js = read("quick.js")
        self.assertIn("qa-think-live", js)
        self.assertIn('details class="qa-think"', js)

    def test_client_guards_an_empty_done(self):
        js = read("quick.js")
        self.assertIn('code: "empty"', js)
        self.assertIn("turn.cardShown = true", js)

class ComputerModel(unittest.TestCase):
    def test_order(self):
        self.assertEqual(core.computer_model({"computer_model": "vl", "quick_ask_model": "q"}), "vl")
        self.assertEqual(core.computer_model({"computer_model": "", "quick_ask_model": "q", "default_model": "d"}), "q")
        self.assertEqual(core.computer_model({"default_model": "d"}), "d")
        self.assertEqual(core.computer_model({}), "")
        own = {"quick_ask_source": "custom", "quick_ask_endpoint_url": "http://127.0.0.1:9/v1",
               "quick_ask_endpoint_model": "local-vl", "computer_model": "proxy-vl"}
        self.assertEqual(core.computer_model(own), "local-vl", "Proxy's model id means nothing to the own endpoint")

    def test_completion_goes_through_the_own_endpoint(self):
        proxy = object()
        self.assertEqual(core.computer_completion({"quick_ask_model": "q"}, proxy), (proxy, "q"))
        own = {"quick_ask_source": "custom", "quick_ask_endpoint_url": "http://127.0.0.1:9/v1/chat/completions",
               "quick_ask_endpoint_model": "local-vl"}
        seen = []

        async def fake_complete(url, key, model, messages, **kw):
            seen.append((url, key, model))
            return '{"action":"done","report":"ok"}'
        with mock.patch.object(endpoint, "get_key", return_value="sk-test"), mock.patch.object(endpoint, "complete", new=fake_complete):
            completion, model = core.computer_completion(own, proxy)
            self.assertIsNot(completion, proxy)
            self.assertEqual(model, "local-vl")
            self.assertIn("done", asyncio.run(completion("local-vl", [{"role": "user", "content": "x"}])))
        self.assertEqual(seen, [("http://127.0.0.1:9/v1", "sk-test", "local-vl")])

        async def empty(*a, **k):
            return "  "
        with mock.patch.object(endpoint, "get_key", return_value=""), mock.patch.object(endpoint, "complete", new=empty):
            completion, _ = core.computer_completion(own, proxy)
            with self.assertRaises(RuntimeError):
                asyncio.run(completion("local-vl", []))

    def test_handler_falls_back_to_the_window_model(self):
        from tests.assistant_commands_test import EngineCase  # noqa: F401  (engine fixtures)
        from app import assistant_r24
        engine = ac.Engine.__new__(ac.Engine)
        engine.handlers = {}
        assistant_r24.bind(engine)
        h = engine.handlers["computer.use"]
        with mock.patch.object(assistant_r24, "computer_supported", return_value=True), \
                mock.patch("app.pgdata.load_preferences", return_value={"quick_ask_model": "qwen/qwen3.8-max:free"}):
            card = asyncio.run(h({"task": "открой блокнот"}, lang="ru"))
        self.assertEqual((card["client"]["model"], card["client"]["model_source"]), ("qwen/qwen3.8-max:free", "quick"))
        with mock.patch.object(assistant_r24, "computer_supported", return_value=True), \
                mock.patch("app.pgdata.load_preferences", return_value={"computer_model": "vl", "quick_ask_model": "q"}):
            card = asyncio.run(h({"task": "открой блокнот"}, lang="ru"))
        self.assertEqual((card["client"]["model"], card["client"]["model_source"]), ("vl", "work"))
        with mock.patch.object(assistant_r24, "computer_supported", return_value=True), \
                mock.patch("app.pgdata.load_preferences", return_value={}):
            with self.assertRaises(Exception) as err:
                asyncio.run(h({"task": "открой блокнот"}, lang="ru"))
            self.assertIn("быстрого доступа", str(err.exception.args[0]))

    def test_step_prompt_names_the_user_language(self):
        src = (ROOT / "app" / "automation_runtime.py").read_text(encoding="utf-8")
        self.assertIn("<user_language>", src)

    def test_run_route_uses_the_quick_completion(self):
        src = (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8")
        self.assertIn("computer_completion(load_preferences(), proxy_completion", src)
        self.assertIn("quick_models.register(app, proxy_json=proxy_json)", src)

    def test_pre_run_vision_gate(self):
        js = read("quick-computer.js")
        for needle in ("function visionGate", 'data-cu-gate="probe"', 'data-cu-gate="force"', 'data-cu-gate="pick"',
                       "model_source === \"work\""):
            self.assertIn(needle, js)

class ComputerCommand(unittest.TestCase):
    def test_phrasings(self):
        for text, task in (("возьми управление компьютером и открой блокнот", "открой блокнот"),
                           ("возьми управление над компьютером: открой блокнот", "открой блокнот"),
                           ("управляй компом открой калькулятор", "открой калькулятор"),
                           ("take control of the computer and open notepad", "open notepad"),
                           ("возьми мышь открой браузер", "открой браузер")):
            got = intents.match(text)
            self.assertIsNotNone(got, text)
            self.assertEqual((got["action"], got["params"]["task"]), ("computer.use", task), text)

    def test_confirmation_text(self):
        ru = ac.describe("computer.use", {"task": "открой блокнот"}, "ru")
        en = ac.describe("computer.use", {"task": "open notepad"}, "en")
        self.assertIn("«открой блокнот»", ru)
        self.assertIn("Esc", ru)
        self.assertNotIn("task:", ru)
        self.assertIn("open notepad", en)
        self.assertIsNone(re.search(r"[А-Яа-яЁё]", en))
        long = ac.describe("computer.use", {"task": "x" * 400}, "ru")
        self.assertLess(len(long), 300)

class PickerAndCopy(unittest.TestCase):
    def test_picker_shows_all_models_with_vision_badges(self):
        js = read("quick.js")
        self.assertNotIn(".slice(0, 80)", js)
        self.assertIn(".slice(0, 600)", js)
        self.assertIn("function visionTag", js)
        self.assertIn('id="qa-picker-count"', js)
        self.assertIn("/api/quick/vision/probe", js)

    def test_selection_survives(self):
        js = read("quick.js")
        self.assertIn("box._qaHtml", js, "renderAnswer keeps the DOM when the markup is the same")
        self.assertRegex(js, r"const selected = .*isCollapsed")

    def test_copy_menu_and_buttons(self):
        js = read("quick-copy.js")
        self.assertIn('addEventListener("contextmenu"', js)
        for label in ("Копировать код", "Копировать таблицу", "Копировать ответ", "Копировать блок", "Вырезать", "Вставить",
                      "Выделить всё", "Спросить об этом"):
            self.assertIn(label, js)
        for block in (".qa-q", ".qa-error", ".mqc-card", ".qe-card", ".qdfy-card", ".qcu-steps", ".qcu-report"):
            self.assertIn(block, js)
        self.assertIn("qc-blk-copy", read("quick-copy.css"))

    def test_en_strings_have_no_cyrillic(self):
        for name in ("quick-copy.js", "quick-computer.js", "quick.js"):
            src = read(name)
            for m in re.finditer(r't\("((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)"\)|tr\("((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)"\)', src):
                en = m.group(2) or m.group(4) or ""
                self.assertIsNone(re.search(r"[А-Яа-яЁё]", en), (name, en))

if __name__ == "__main__":
    unittest.main()
