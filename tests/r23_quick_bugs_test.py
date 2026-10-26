"""Round 23: Quick Ask backend bugs found by r23-bughunt, now fixed by r23-quick.

Originally written as ``expectedFailure`` tests; every fix landed, so the decorators are gone and
these are plain regression tests (incl. #11 plugin tool timeout and #12 page tool results only for
pending calls).
"""
from __future__ import annotations

import asyncio
import json
import math
import unittest
from pathlib import Path

from app import quick_ask_api
from app import quick_ask_core as core

ROOT = Path(__file__).resolve().parents[1]

class QuickCoreRobustness(unittest.TestCase):
    def test_screenshot_monitor_infinity_does_not_crash_the_answer_stream(self):

        self.assertEqual(core.screenshot_args({"monitor": "inf"})["monitor"], "cursor")

    def test_screenshot_region_nan_is_rejected(self):
        region = core.screenshot_args({"region": {"left": float("nan"), "top": 0, "width": 1, "height": 1}})["region"]
        self.assertTrue(region is None or all(math.isfinite(v) for v in region.values()))

    def test_chat_turn_with_infinite_numbers_is_cleaned(self):
        turn = core._clean_turn({"q": "a", "t": float("inf"), "images": float("inf")})
        self.assertIsNotNone(turn)

    def test_one_bad_stored_chat_does_not_block_saving_new_ones(self):
        rows = core.upsert_chat([{"id": "zzzz", "updated": "abc"}], {"id": "abcd", "turns": [{"q": "hi"}]})
        self.assertEqual(rows[0]["id"], "abcd")

    def test_css_colour_with_a_broken_alpha_is_unusable_not_an_exception(self):
        self.assertIsNone(core.parse_css_color("rgba(1, 2, 3, 1.2.3)"))

class QuickToolContract(unittest.TestCase):
    """#11: plugin tools have a documented 60 s limit; #12: page tool results only for pending calls."""

    def test_plugin_tool_timeout_is_a_named_documented_constant(self):
        self.assertEqual(quick_ask_api.EXTRA_TOOL_TIMEOUT, 60.0)
        self.assertIn("EXTRA_TOOL_TIMEOUT", quick_ask_api.register_tool.__doc__ or "")
        source = (ROOT / "app" / "quick_ask_api.py").read_text(encoding="utf-8")
        self.assertIn('wait_for(tool["handler"](args, ctx), EXTRA_TOOL_TIMEOUT)', source)

    def test_unknown_malformed_or_finished_ids_are_refused(self):
        self.assertFalse(quick_ask_api.resolve_client_tool("", {"x": 1}))
        self.assertFalse(quick_ask_api.resolve_client_tool("../../etc", {"x": 1}))
        self.assertFalse(quick_ask_api.resolve_client_tool("0123456789abcdef", {"x": 1}))

        async def run():
            loop = asyncio.get_running_loop()
            fut = loop.create_future()
            quick_ask_api._CLIENT_CALLS["0123456789abcdef"] = fut
            quick_ask_api._CLIENT_CALL_TOOLS["0123456789abcdef"] = "page_js"
            try:
                wrong_tool = quick_ask_api.resolve_client_tool("0123456789abcdef", {"x": 1}, "other_tool")
                ok = quick_ask_api.resolve_client_tool("0123456789abcdef", {"x": 2}, "page_js")
                value = await asyncio.wait_for(fut, 1)
                again = quick_ask_api.resolve_client_tool("0123456789abcdef", {"x": 3}, "page_js")
                return wrong_tool, ok, value, again
            finally:
                quick_ask_api._CLIENT_CALLS.pop("0123456789abcdef", None)
                quick_ask_api._CLIENT_CALL_TOOLS.pop("0123456789abcdef", None)
        wrong_tool, ok, value, again = asyncio.run(run())
        self.assertFalse(wrong_tool)
        self.assertTrue(ok)
        self.assertEqual(value, {"x": 2})
        self.assertFalse(again)

    def test_route_answers_404_for_a_call_that_is_not_pending(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()

        async def nothing(*_a, **_k):
            return {}
        quick_ask_api.register(app, stream_lines=nothing, run_tool=nothing, rich_search=nothing,
                               rich_payload=lambda payload: payload)
        client = TestClient(app)
        r = client.post("/api/quick/tool_result", json={"id": "0123456789abcdef", "result": {"ok": True}})
        self.assertEqual(r.status_code, 404)
        self.assertFalse(r.json()["ok"])

    def test_page_sends_the_tool_name_with_its_result(self):
        js = (ROOT / "app" / "pgstatic" / "quick" / "quick.js").read_text(encoding="utf-8")
        self.assertIn('api("/api/quick/tool_result", { body: { id: event.id, tool: String(event.tool || "")', js)

NODE = __import__("shutil").which("node")

@unittest.skipUnless(NODE, "node is not installed")
class QuickSettingsRobustness(unittest.TestCase):
    """The Quick Ask settings page renders even when /api/quick/personalities returns {}, null, junk."""

    def render(self, personalities, presets) -> str:
        import subprocess
        script = r"""
const vm = require("vm"), fs = require("fs");
const sandbox = { window: {}, document: { getElementById: () => null, documentElement: { lang: "ru" } }, console };
sandbox.window.document = sandbox.document;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), sandbox);
const QS = sandbox.window.MemuQuickSettings;
const prefs = { voice_live_personality_presets: JSON.parse(process.argv[3]), quick_ask_personality: "preset:x" };
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, "");
const ctx = { en: false, pref: (k, d) => (k in prefs ? prefs[k] : d), esc, models: [], api: async () => JSON.parse(process.argv[2]) };
if (QS.__setPersonalities) QS.__setPersonalities(JSON.parse(process.argv[2]));
const html = QS.html(ctx);
process.stdout.write(String(html.includes('id="st-qa-personality"')) + " " + String(html.length > 1000));
"""
        out = subprocess.run([NODE, "-e", script, str(ROOT / "app" / "pgstatic" / "quick-settings.js"),
                              json.dumps(personalities), json.dumps(presets)],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout

    def test_malformed_personalities_do_not_break_the_page(self):
        for data in ({}, None, [], {"builtin": {}, "presets": "x"}, {"builtin": [None, 1, {"id": "a"}], "presets": [{}]}):
            for presets in ({}, None, [None, {"id": "x"}]):
                with self.subTest(data=data, presets=presets):
                    self.assertEqual(self.render(data, presets), "true true")

if __name__ == "__main__":
    unittest.main()
