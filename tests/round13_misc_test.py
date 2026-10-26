"""Round 13: benchmarks, the sketch canvas, MemuAI's self-knowledge, the Proxy model list."""
from __future__ import annotations

import os
import tempfile

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

import json  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import patch  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app import docs, pgdata  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PG = ROOT / "app" / "pgstatic"
PROXY = ROOT / "app" / "static"
NODE = shutil.which("node")

def run_node(script: str) -> dict:
    done = subprocess.run([NODE, str(ROOT / "tests" / script)], capture_output=True, text=True, encoding="utf-8", timeout=90)
    lines = [line for line in done.stdout.splitlines() if line.strip().startswith("{")]
    if not lines:
        raise AssertionError(done.stderr[-1500:] or "the runner printed nothing")
    out = json.loads(lines[-1])
    if "error" in out:
        raise AssertionError(out["error"])
    return out

@unittest.skipUnless(NODE, "Node.js is required for module runners")
class BenchmarksTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.out = run_node("benchmarks_arena_runner.cjs")

    def test_the_agent_arena_is_read_in_its_own_format(self) -> None:
        agent = self.out["agent"]
        self.assertEqual(self.out["agentError"], "")
        self.assertEqual(agent["count"], 3)
        first = agent["first"]
        self.assertEqual((first["model"], first["org"], first["rank"], first["votes"], first["priceIn"]),
                         ("Claude Fable 5.1 (Max)", "Anthropic", 1, 1000, 4))
        self.assertAlmostEqual(first["score"], 0.1455)
        self.assertAlmostEqual(first["lo"], 0.1255)
        self.assertEqual((self.out["agentScoreText"], self.out["agentRankText"]), ("14.5", "1–3"))
        self.assertEqual(self.out["agentScoreLabel"], "Прирост, %", "a share, not an Elo rating")
        self.assertEqual(agent["categories"], ["signal-task_outcome_explicit", "signal-steering_burden"])
        self.assertEqual(agent["titles"], ["Confirmed Success", "Steerability"])

    def test_each_signal_of_the_agent_arena_is_a_category(self) -> None:
        signal = self.out["signal"]
        self.assertEqual((signal["first"], signal["score"], signal["votes"], signal["price"]), ("Claude Opus 5.5 (High)", 0.12, 2000, 4))
        self.assertEqual(signal["url"], "https://arena.ai/leaderboard/agent", "signals come from the same page")
        self.assertEqual(signal["description"], "About Steerability.")
        self.assertIn("формат", self.out["unknownSignal"])

    def test_regular_arenas_still_load_and_a_changed_page_is_reported(self) -> None:
        self.assertEqual(self.out["text"], {"first": "Alpha 1", "score": 1500})
        self.assertIn("изменил формат", self.out["broken"])
        self.assertEqual(self.out["nav"], ["text", "agent"], "the pareto view is not an arena")

    def test_the_header_tells_the_age_of_what_is_on_screen(self) -> None:

        self.assertIn("4 часа назад", self.out["statusOverview"])
        self.assertIn("только что", self.out["statusBoard"])
        self.assertIn("8 дня назад", self.out["statusOldBoard"], "the stale board itself is still dated honestly")
        self.assertIn("30 часа назад", self.out["statusIndex"], "the index is as old as its oldest source")

class SketchTest(unittest.TestCase):
    def test_the_sketch_tool_is_in_the_plus_menu_and_attaches_a_png(self) -> None:
        tools = (PG / "tools.js").read_text(encoding="utf-8")
        self.assertIn('id: "sketch"', tools)
        self.assertIn("MemuSketch.open()", tools)
        html = (PG / "index.html").read_text(encoding="utf-8")
        self.assertLess(html.index("/tools.js"), html.index("/sketch.js"))
        sketch = (PG / "sketch.js").read_text(encoding="utf-8")
        for tool in ("pen", "marker", "eraser", "line", "arrow", "rect", "ellipse", "text", "image", "move"):
            self.assertIn(f'["{tool}"', sketch, tool)
        for needle in ("addAttachmentFiles([new File([blob]", 'image/png', "destination-out", 'ctx.fillStyle = "#ffffff"',
                       "history", 'event.code === "KeyZ"', "loadImage(event.dataTransfer"):
            self.assertIn(needle, sketch)
        css = (PG / "design.css").read_text(encoding="utf-8")
        self.assertIn(".sketch-card", css)

class SelfKnowledgeTest(unittest.TestCase):
    def test_the_base_prompt_describes_memuai_in_both_copies(self) -> None:
        js = (PG / "app.js").read_text(encoding="utf-8")
        for text in (pgdata.DEFAULT_PROMPT, js):
            self.assertIn("<about_memuai>", text)
            self.assertIn("MemuAI Proxy", text)
            self.assertIn("@!web-search", text)
        self.assertLess(len(pgdata.DEFAULT_PROMPT[pgdata.DEFAULT_PROMPT.index("<about_memuai>"):pgdata.DEFAULT_PROMPT.index("</about_memuai>")]), 1500,
                        "it rides on every request: keep it short")

    def test_the_guide_skill_is_built_in_inline_and_removable(self) -> None:
        skills = (PG / "skills.js").read_text(encoding="utf-8")
        block = skills[skills.index('id: "memuai_guide"'):]
        block = block[:block.index("\n  },")]
        self.assertIn("inline: true", block)
        self.assertIn("removable: true", block)
        self.assertIn("docs_read", block)
        self.assertIn("docs_search", block)
        js = (PG / "app.js").read_text(encoding="utf-8")
        self.assertIn("filter(s => s.enabled && !s.inline)", js, "it is not offered through the load-on-demand catalogue")
        self.assertIn("memu_skills_removed", js)
        self.assertIn("MemuSelfDocs.systemBlock()", js)
        self.assertIn("MemuSelfDocs.mentionContext(", js)
        selfdocs = (PG / "selfdocs.js").read_text(encoding="utf-8")
        for needle in ('SKILL_ID = "memuai_guide"', "<memuai_documentation>", "<documentation_index>", "doc-chip", "MemuDocs.open("):
            self.assertIn(needle, selfdocs)

    def test_documentation_tools_exist_only_with_the_skill(self) -> None:
        from app.playground_api import create_app
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        with patch.object(pgdata, "MEMU_DIR", Path(temp.name)), TestClient(create_app(), base_url="http://127.0.0.1") as client:
            call = lambda body: client.post("/api/pg/tools", json=body).json()
            refused = call({"tool": "docs_read", "args": {"id": "web-search"}})
            self.assertIn("turned off", refused["error"])
            page = call({"tool": "docs_read", "args": {"id": "@!web-search"}, "docs_skill": True, "lang": "ru"})
            self.assertEqual((page["id"], page["mention"]), ("web-search", "@!web-search"))
            self.assertIn("Веб-поиск", page["title"])
            self.assertGreater(len(page["text"]), 1500)
            self.assertTrue(page["sections"])
            english = call({"tool": "docs_read", "args": {"id": "web-search"}, "docs_skill": True, "lang": "en"})
            self.assertIn("Web search", english["title"])
            part = call({"tool": "docs_read", "args": {"id": "web-search", "section": page["sections"][0]}, "docs_skill": True, "lang": "ru"})
            self.assertLess(len(part["text"]), len(page["text"]))
            self.assertTrue(part["text"].lstrip().startswith("#"))
            self.assertIn("error", call({"tool": "docs_read", "args": {"id": "../secret"}, "docs_skill": True}))
            found = call({"tool": "docs_search", "args": {"query": "экспорт чатов"}, "docs_skill": True, "lang": "ru"})
            self.assertTrue(found["results"])
            self.assertTrue(all(docs.page("ru", row["id"]) for row in found["results"]))
            self.assertIn("error", call({"tool": "docs_search", "args": {"query": "a"}, "docs_skill": True}))

class ProxyModelListTest(unittest.TestCase):
    def test_a_big_catalogue_is_offered_for_choice_after_adding_an_account(self) -> None:
        js = (PROXY / "app.js").read_text(encoding="utf-8")
        self.assertIn("const MODEL_PICK_MIN = 30;", js)
        self.assertEqual(js.count("offerNewModels(before, added && added.account && added.account.id);") + js.count("offerNewModels(await before, accountId);"), 3,
                         "the preset, the form and the browser sign-in all offer the choice")
        self.assertIn("offerNewModels(before, added", (PROXY / "local.js").read_text(encoding="utf-8"))
        picker = js[js.index("function openModelPicker(models)"):js.index("async function modelsBulk(action)")]
        for needle in ('action: "disable"', 'action: "enable"', "shown().forEach", 'id="mp-filter"'):
            self.assertIn(needle, picker)
        self.assertNotIn('"delete"', picker, "unticked models are switched off, never deleted")

    def test_the_selection_bar_no_longer_moves_the_list(self) -> None:
        js = (PROXY / "app.js").read_text(encoding="utf-8")
        bar = js[js.index("function syncModelsBulkBar()"):js.index("let lastPickedModel")]
        self.assertIn("bar.hidden = false", bar)
        self.assertNotIn("bar.hidden = modelsSelected.size === 0", bar)
        html = (PROXY / "index.html").read_text(encoding="utf-8")
        self.assertIn('<div class="bulk-bar idle" id="models-bulk-bar">', html)
        self.assertIn("marquee.scroll0 = list.scrollTop", js)
        self.assertIn('window.addEventListener("scroll", marqueeScrolled, true)', js)

class QuietTerminalTest(unittest.TestCase):
    def test_memucode_rings_only_when_asked(self) -> None:
        source = (ROOT / "app" / "cli" / "tui" / "app.py").read_text(encoding="utf-8")
        self.assertLess(source.index('os.environ.setdefault("PROMPT_TOOLKIT_BELL", "false")'),
                        source.index("from prompt_toolkit.application import"), "set before the toolkit builds its output")
        notify = source[source.index("def _notify("):]
        notify = notify[:notify.index("\n    def ", 10)]
        self.assertIn('if mode == "bell":', notify)
        self.assertNotIn("output.bell()", source, "the toolkit's bell is never used")

if __name__ == "__main__":
    unittest.main()

class CancelAndCatalogueTest(unittest.TestCase):
    def test_a_close_survives_the_cancellation_of_the_request(self) -> None:
        import asyncio
        from app.providers import http

        state = {"closed": False}

        async def closer():
            await asyncio.sleep(0.05)
            state["closed"] = True

        async def request():
            try:
                await asyncio.sleep(5)
            finally:
                await http.close_quietly(closer)

        async def go():
            task = asyncio.create_task(request())
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(go())
        self.assertTrue(state["closed"], "the upstream connection must be closed even when Stop cancels the task")
        source = (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8")
        self.assertIn("close_quietly(resp.aclose)", source)
        self.assertNotIn("[:300]", (ROOT / "app" / "admin_api.py").read_text(encoding="utf-8").split("dynamic = await provider.dynamic_models(acc)")[1][:200],
                         "a catalogue of 500 models is not cut to 300")
