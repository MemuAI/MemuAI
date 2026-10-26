"""MemuCode agent-loop regression tests.

The real tool round-trip loop (parse hidden ```memuai-tool blocks, run them,
feed the JSON result back, repeat) lives in app/pgstatic/app.js
(generate/runHiddenTools/resumeAfterAutomation) — there is no Python
implementation to import. So this file does two things:

1. A pure-Python harness (`run_tool_loop`) that reproduces the same
   contract — fed by a scripted `FakeModel` instead of a real LLM — while
   calling the REAL tool backends (app.work_tools / app.code_context), the
   same code a live MemuCode session uses to read files. This catches
   regressions in the nonexistent-file error path and in a naive "keep
   calling tools forever" model without needing a browser or a real model.
2. Source-level assertions against app.js itself for the two contract
   properties that only exist in the JS orchestration layer: that MemuCode
   ("code" mode) actually receives the real project file listing before its
   first turn, and that a tool-round continuation replays the model's own
   prior visible text instead of silently dropping it.

Bug report this guards against: asked to analyze a 2-file project, MemuCode
"read" files that do not exist (README.md, package.json) without treating the
failure as a failure, then produced a different, contradictory "conclusion"
about the project on almost every round until the user stopped it manually.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import code_context
from app.work_tools import WorkError, read_text, resolve_inside

APP_JS = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")

TOOL_RE = re.compile(r"```memuai-tool\s*(?:\r?\n)?([\s\S]*?)```", re.I)

def _tool_round_limit_from_source() -> int:
    """Read TOOL_ROUND_LIMIT out of app.js so the harness never drifts from
    the real guard instead of hardcoding a second copy of the number."""
    match = re.search(r"const TOOL_ROUND_LIMIT\s*=\s*(\d+)", APP_JS)
    assert match, "TOOL_ROUND_LIMIT constant not found in app.js"
    return int(match.group(1))

class FakeModel:
    """Scripted turns; once they run out, keeps re-requesting a file that
    does not exist -- exactly what an under-informed/looping model does."""

    def __init__(self, turns=()):
        self._turns = list(turns)
        self.seen_conversations: list[list[str]] = []

    def __call__(self, conversation: list[str]) -> str:
        self.seen_conversations.append(list(conversation))
        if self._turns:
            return self._turns.pop(0)
        return '```memuai-tool\n[{"tool":"work_read","args":{"path":"README.md"}}]\n```'

def _execute_tool_call(root: Path, call: dict) -> dict:
    """Mirrors code_routes._one_sync for the one tool this harness needs:
    a real filesystem read that must fail cleanly for a missing path."""
    name = str(call.get("tool") or "")
    args = call.get("args") or {}
    if name == "work_read":
        try:
            path = resolve_inside(root, str(args.get("path") or ""))
            text, truncated = read_text(path)
            return {"tool": name, "path": args.get("path"), "text": text, "truncated": truncated}
        except WorkError as exc:
            return {"tool": name, "path": args.get("path"), "error": str(exc)}
    return {"tool": name, "error": f"unknown tool in harness: {name}"}

def run_tool_loop(root: Path, model: FakeModel, project_files: list[str], round_limit: int) -> dict:
    """Python mirror of the app.js loop's externally observable contract:
    send the real tree first, execute whatever the model asks for, always
    feed the JSON result back as the next turn's observation, and stop once
    round_limit is reached instead of continuing forever."""
    conversation = [
        "<project_structure files=\"%d\">\n%s\n</project_structure>"
        % (len(project_files), "\n".join(project_files))
    ]
    rounds = 0
    while True:
        rounds += 1
        if rounds > round_limit:
            return {"stopped": "round_limit", "rounds": rounds - 1, "conversation": conversation}
        reply = model(conversation)
        match = TOOL_RE.search(reply)
        if not match:
            return {"stopped": "final_answer", "rounds": rounds, "final_text": reply, "conversation": conversation}
        try:
            calls = json.loads(match.group(1))
        except ValueError:
            calls = []
        if isinstance(calls, dict):
            calls = [calls]
        results = [_execute_tool_call(root, call) for call in calls]
        conversation.append("Tool results:\n" + json.dumps(results))

class MissingFileErrorTests(unittest.TestCase):
    """The exact failure the user saw a tool log "succeed" for."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "main.py").write_text("print('hi')\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_work_read_of_a_nonexistent_file_raises_a_clear_error(self) -> None:
        path = resolve_inside(self.root, "README.md")
        self.assertFalse(path.exists())
        with self.assertRaises(WorkError):
            read_text(path)

    def test_code_context_read_of_a_nonexistent_file_is_an_error_dict_not_content(self) -> None:
        engine = code_context.engine_for("missing-file-test", self.root)
        engine.refresh()
        result = engine.read_cached("package.json", 1, 0)
        self.assertIn("error", result)
        self.assertNotIn("text", result)

    def test_harness_tool_call_for_a_missing_file_is_reported_as_an_error(self) -> None:
        result = _execute_tool_call(self.root, {"tool": "work_read", "args": {"path": "README.md"}})
        self.assertIn("error", result)
        self.assertNotIn("text", result)

class ToolLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "main.py").write_text("print('hello project')\n", encoding="utf-8")
        (self.root / "notes.txt").write_text("just two files here\n", encoding="utf-8")
        self.round_limit = _tool_round_limit_from_source()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_a_model_that_never_stops_asking_for_tools_is_cut_off_at_the_round_limit(self) -> None:

        model = FakeModel(turns=[])
        result = run_tool_loop(self.root, model, ["main.py", "notes.txt"], self.round_limit)
        self.assertEqual(result["stopped"], "round_limit")
        self.assertEqual(result["rounds"], self.round_limit)

        self.assertLessEqual(len(model.seen_conversations), self.round_limit)

    def test_tool_results_are_fed_back_into_the_next_round_not_dropped(self) -> None:
        model = FakeModel(turns=[
            '```memuai-tool\n[{"tool":"work_read","args":{"path":"main.py"}}]\n```',
            "This project is a tiny Python script.",
        ])
        result = run_tool_loop(self.root, model, ["main.py", "notes.txt"], self.round_limit)
        self.assertEqual(result["stopped"], "final_answer")
        self.assertEqual(result["rounds"], 2)

        second_call_conversation = model.seen_conversations[1]
        self.assertTrue(any("hello project" in turn for turn in second_call_conversation),
                         "the real file content from round 1 must reach round 2")

    def test_initial_turn_carries_the_real_project_listing_not_a_guess(self) -> None:
        model = FakeModel(turns=["Two files: main.py and notes.txt."])
        result = run_tool_loop(self.root, model, ["main.py", "notes.txt"], self.round_limit)
        first_conversation = model.seen_conversations[0]
        self.assertTrue(any("main.py" in turn and "notes.txt" in turn for turn in first_conversation))
        self.assertFalse(any("README.md" in turn or "package.json" in turn for turn in first_conversation),
                          "the harness must not inject files that were never on disk")

class AppJsContractTests(unittest.TestCase):
    """The two properties above only exist for real inside app.js; assert
    them at the source level so a regression there fails a test even though
    no JS runtime is available here."""

    def test_project_structure_is_fetched_for_work_and_code_modes_alike(self) -> None:

        self.assertIn('if (isProjectMode(sourceMode) && sourceWorkspaces.active_id)', APP_JS)
        self.assertNotIn('if (sourceMode === "work" && sourceWorkspaces.active_id)', APP_JS)

    def test_tool_round_continuation_replays_the_assistant_own_prior_text(self) -> None:

        self.assertIn('if (m === assistant && !String(m.content || "").trim()) continue;', APP_JS)

    def test_round_limit_constant_is_still_a_small_positive_number(self) -> None:
        limit = _tool_round_limit_from_source()
        self.assertGreater(limit, 0)
        self.assertLessEqual(limit, 50)

    def test_code_mode_rules_forbid_treating_a_failed_read_as_analyzed(self) -> None:
        self.assertIn("means that read failed", APP_JS)

if __name__ == "__main__":
    unittest.main()
