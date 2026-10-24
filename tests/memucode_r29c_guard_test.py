"""Round 29 (r29c-prompts): MemuCode CLI agent loop — an empty reply in the middle of a task and repeated identical
tool calls (seen live with gpt-oss-120b in tests/prompt_eval/cli_eval.py).

Запуск: python3 -m pytest -q tests/memucode_r29c_guard_test.py
"""
from __future__ import annotations

import unittest

from tests.memucode_cli_test import (Recorder, make_runtime, run,  # noqa: F401  (module fixtures)
                                     setUpModule, tearDownModule)

from app.cli import agent as agent_mod
from app.cli.agent import Agent

READ_A = '```memuai-tool\n[{"tool":"Read","args":{"file_path":"a.txt"}}]\n```'
LS = '```memuai-tool\n[{"tool":"LS","args":{}}]\n```'


def _last_user(request) -> str:
    return str(request.messages[-1].get("content"))


class EmptyReplyTests(unittest.TestCase):
    def test_empty_reply_mid_task_is_nudged_to_continue(self):
        runtime, backend = make_runtime({"a.txt": "alpha\n"}, turns=[READ_A, "", "It says alpha."])
        result = run(Agent(runtime).run_turn("what is in a.txt"))
        self.assertEqual(result.stopped, "done")
        self.assertEqual(result.text, "It says alpha.")
        self.assertEqual(len(backend.requests), 3)
        self.assertIn("Your last reply was empty", _last_user(backend.requests[2]))

    def test_nudges_are_bounded(self):
        runtime, backend = make_runtime({"a.txt": "alpha\n"}, turns=[READ_A, "", "", "", "never asked"])
        result = run(Agent(runtime).run_turn("what is in a.txt"))
        self.assertEqual(result.text, "")
        self.assertEqual(len(backend.requests), 2 + agent_mod.MAX_EMPTY_NUDGES)

    def test_a_normal_answer_is_not_nudged(self):
        runtime, backend = make_runtime(turns=["Hello!"])
        result = run(Agent(runtime).run_turn("hi"))
        self.assertEqual(result.text, "Hello!")
        self.assertEqual(len(backend.requests), 1)


class DuplicateCallTests(unittest.TestCase):
    def test_identical_read_is_answered_not_run_again(self):
        runtime, backend = make_runtime({"a.txt": "alpha\n"}, turns=[READ_A, READ_A, "alpha"])
        result = run(Agent(runtime).run_turn("read a.txt"))
        self.assertEqual(result.text, "alpha")
        reminder = _last_user(backend.requests[2])
        self.assertIn("Not run again", reminder)
        self.assertIn("round 1", reminder)
        self.assertEqual(result.tools, 2)
        reads = [e for e in runtime.ui.events if e[0] == "done" and e[1] == "Read"]
        self.assertEqual([e[2] for e in reads], [True, False])

    def test_read_after_an_edit_runs_again(self):
        edit = ('```memuai-tool\n[{"tool":"Edit","args":{"file_path":"a.txt","old_string":"alpha",'
                '"new_string":"beta"}}]\n```')
        runtime, backend = make_runtime({"a.txt": "alpha\n"}, turns=[READ_A, edit, READ_A, "beta now"])
        result = run(Agent(runtime).run_turn("change a.txt"))
        self.assertEqual(result.text, "beta now")
        third = _last_user(backend.requests[3])
        self.assertNotIn("Not run again", third)
        self.assertIn("beta", third)

    def test_a_turn_of_repeats_only_is_asked_for_the_final_answer(self):
        runtime, backend = make_runtime(turns=[LS] * agent_mod.MAX_DUPLICATE_ROUNDS + [LS, "Nothing more to find."])
        runtime.ui.interactive = False
        result = run(Agent(runtime).run_turn("list forever"))
        self.assertEqual(result.text, "Nothing more to find.")
        self.assertEqual(result.stopped, "stuck")
        self.assertIsNone(backend.requests[-1].tools)
        self.assertIn("Loop guard", _last_user(backend.requests[-1]))
        self.assertEqual(len(backend.requests), 1 + agent_mod.MAX_DUPLICATE_ROUNDS + 1)

    def test_tool_blocks_in_the_forced_final_answer_are_not_shown(self):
        runtime, backend = make_runtime(turns=[LS] * 8)
        runtime.ui.interactive = False
        result = run(Agent(runtime).run_turn("list forever"))
        self.assertIn("kept repeating the same tool calls", result.text)

    def test_bash_repeat_after_another_command_is_allowed(self):
        one = '```memuai-tool\n[{"tool":"Bash","args":{"command":"echo one"}}]\n```'
        two = '```memuai-tool\n[{"tool":"Bash","args":{"command":"echo two"}}]\n```'
        runtime, backend = make_runtime(turns=[one, two, one, "done"], settings={"permission_mode": "bypassPermissions"})
        run(Agent(runtime).run_turn("run them"))
        self.assertNotIn("Not run again", _last_user(backend.requests[3]))


if __name__ == "__main__":
    unittest.main()
