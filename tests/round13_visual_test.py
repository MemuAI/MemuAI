"""Round 13: the visual test — a model writes a page, a person looks at it. No AI grades it."""
from __future__ import annotations

import os
import tempfile

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

import asyncio  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import patch  # noqa: E402

from app import model_call, visual_test  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PAGE = "<!DOCTYPE html>\n<html><head><title>Fish</title></head><body><script>let a = 1;</script></body></html>"


def model(answer: str, usage=None, fail_with_limit: bool = False):
    calls = []

    async def stream(name, messages, params=None):
        calls.append({"model": name, "prompt": messages[-1]["content"], "params": dict(params or {})})
        if fail_with_limit and "max_tokens" in (params or {}):
            raise RuntimeError("max_tokens is too large: 32000 > 8192")
        yield {"type": "reasoning", "text": "thinking about fish"}
        for index in range(0, len(answer), 40):
            await asyncio.sleep(0)
            yield {"type": "text", "text": answer[index:index + 40]}
        if usage:
            yield {"type": "usage", "usage": usage}
    return stream, calls


class ExtractPageTest(unittest.TestCase):
    def test_a_fenced_page_is_taken_whole_and_only_the_page(self) -> None:
        found = visual_test.extract_page(f"Here it is:\n```html\n{PAGE}\n```\nEnjoy! <html> is great.")
        self.assertEqual((found["kind"], found["complete"]), ("html", True))
        self.assertEqual(found["html"].strip(), PAGE)

    def test_the_largest_page_wins_and_a_bare_document_works(self) -> None:
        small = "<html><body>draft</body></html>"
        found = visual_test.extract_page(f"```html\n{small}\n```\nBetter:\n```html\n{PAGE}\n```")
        self.assertIn("<title>Fish</title>", found["html"])
        bare = visual_test.extract_page("Sure!\n" + PAGE + "\nThat is all.")
        self.assertEqual(bare["html"].strip(), PAGE)

    def test_a_cut_off_page_is_kept_and_marked(self) -> None:
        found = visual_test.extract_page("```html\n<!doctype html><html><body><canvas></canvas><script>const scene = new THREE.Sc")
        self.assertEqual((found["kind"], found["complete"]), ("html", False))
        self.assertTrue(found["html"].startswith("<!doctype html>"))

    def test_an_svg_is_wrapped_into_a_page(self) -> None:
        found = visual_test.extract_page('```svg\n<svg viewBox="0 0 10 10"><circle r="3"/></svg>\n```', "Pelican <b>&")
        self.assertEqual((found["kind"], found["complete"]), ("svg", True))
        self.assertIn('<svg viewBox="0 0 10 10"><circle r="3"/></svg>', found["html"])
        self.assertIn("<title>Pelican b</title>", found["html"], "the title cannot break out of its tag")
        self.assertEqual(visual_test.extract_page("I cannot draw that.")["kind"], "")


class VisualRunsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self._patch = patch.object(visual_test, "_folder", lambda: Path(self._temp.name))
        self._patch.start()
        visual_test._runs.clear()

    def tearDown(self) -> None:
        self._patch.stop()
        self._temp.cleanup()

    def run_to_end(self, stream, **kwargs) -> dict:
        async def go():
            with patch.object(model_call, "stream", stream):
                run = await visual_test.start(**kwargs)
                for _ in range(500):
                    await asyncio.sleep(0.005)
                    state = visual_test.get(run["id"])
                    if state["state"] != "running":
                        return state
            return state
        return asyncio.run(go())

    def test_the_built_in_set_has_the_two_featured_tasks_and_the_3d_collection(self) -> None:
        rows = visual_test.prompt_list()
        self.assertEqual([row["id"] for row in rows[:2]], ["aquarium", "pelican"])
        self.assertEqual([row["group"] for row in rows[:2]], ["featured", "featured"])
        self.assertGreaterEqual(len(rows), 65)
        self.assertEqual(len({row["id"] for row in rows}), len(rows), "ids are unique")
        self.assertTrue(all("prompt" not in row and row["chars"] > 200 for row in rows), "the list carries no long texts")
        self.assertIn("clownfish", visual_test.find_prompt("aquarium")["prompt"])
        self.assertIn("pelican riding a bicycle", visual_test.find_prompt("pelican")["prompt"])
        self.assertIsNone(visual_test.find_prompt("../etc"))

    def test_a_run_saves_the_page_and_opens_only_with_its_key(self) -> None:
        stream, calls = model(f"```html\n{PAGE}\n```", usage={"completion_tokens": 420, "estimated": False})
        state = self.run_to_end(stream, model="demo-model", prompt_id="aquarium")
        self.assertEqual((state["state"], state["kind"], state["complete"]), ("done", "html", True), state)
        self.assertEqual((state["tokens"], state["title"], state["model"]), (420, "Аквариум", "demo-model"))
        self.assertGreater(state["reasoning_chars"], 0)
        self.assertEqual(calls[0]["params"], {"max_tokens": visual_test.MAX_OUTPUT_TOKENS})
        self.assertIn("clownfish", calls[0]["prompt"])
        self.assertEqual(visual_test.page(state["id"], state["key"]).strip(), PAGE)
        self.assertIsNone(visual_test.page(state["id"], "wrong-key"))
        self.assertIsNone(visual_test.page(state["id"], ""))
        self.assertIsNone(visual_test.page("../../secret", state["key"]))
        self.assertIn("```html", visual_test.answer(state["id"]))
        self.assertEqual(visual_test.runs()[0]["id"], state["id"], "the run is in the saved history")
        visual_test._runs.clear()
        self.assertEqual(visual_test.page(state["id"], state["key"]).strip(), PAGE, "and survives a restart")
        visual_test.delete(state["id"])
        self.assertEqual(visual_test.runs(), [])
        self.assertIsNone(visual_test.page(state["id"], state["key"]))

    def test_a_refused_output_limit_is_retried_with_the_provider_default(self) -> None:
        stream, calls = model('<svg viewBox="0 0 1 1"></svg>', fail_with_limit=True)
        state = self.run_to_end(stream, model="small-gateway", prompt_id="pelican")
        self.assertEqual((state["state"], state["kind"]), ("done", "svg"))
        self.assertEqual([call["params"] for call in calls], [{"max_tokens": 32000}, {}])

    def test_answers_without_a_page_and_bad_requests_are_reported(self) -> None:
        stream, _calls = model("I am sorry, I cannot produce that.")
        state = self.run_to_end(stream, model="demo", prompt_id="pelican")
        self.assertEqual(state["state"], "error")
        self.assertIn("нет страницы", state["error"])
        self.assertIn("cannot produce", visual_test.answer(state["id"]), "the answer is still readable")
        with self.assertRaisesRegex(ValueError, "модель"):
            asyncio.run(visual_test.start("", "pelican"))
        with self.assertRaisesRegex(ValueError, "не найдено"):
            asyncio.run(visual_test.start("demo", "no-such-prompt"))

    def test_only_the_newest_runs_are_kept(self) -> None:
        stream, _calls = model('<svg viewBox="0 0 1 1"></svg>')
        with patch.object(visual_test, "KEEP_RUNS", 2):
            ids = [self.run_to_end(stream, model=f"m{index}", prompt_id="pelican")["id"] for index in range(3)]
        self.assertEqual([row["id"] for row in visual_test.runs()], [ids[2], ids[1]])
        self.assertFalse((Path(self._temp.name) / f"{ids[0]}.html").exists(), "files of dropped runs are removed")


class VisualRoutesTest(unittest.TestCase):
    def test_the_page_is_served_sandboxed_and_the_panel_has_the_buttons(self) -> None:
        api = (ROOT / "app" / "proxy_tools_api.py").read_text(encoding="utf-8")
        self.assertIn('"Content-Security-Policy": "sandbox allow-scripts', api)
        self.assertNotIn("allow-same-origin", api, "model output never gets the panel's origin")
        page_route = api[api.index('@router.get("/detector/visual/runs/{run_id}/page")'):]
        page_route = page_route[:page_route.index("@router.post")]
        self.assertNotIn("_auth(request)", page_route, "the link opens in a new tab: its own key guards it")
        self.assertIn("visual_test.page, run_id, key", page_route)
        js = (ROOT / "app" / "static" / "detector.js").read_text(encoding="utf-8")
        for needle in ('id="det-visual"', "/api/admin/detector/visual/run", 'data-vis="main"', 'row.group === "featured"',
                       'rel="noopener"', "без оценки ИИ"):
            self.assertIn(needle, js)
        self.assertTrue((ROOT / "app" / "static" / "visual-prompts.json").exists())


if __name__ == "__main__":
    unittest.main()
