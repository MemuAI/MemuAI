"""The scenario engine (Round 42, item 8b): variables, files, allowlist, loops, log."""
import asyncio
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import plan_scenarios as S  # noqa: E402


def run(scenario, base, **kwargs):
    return S.execute_sync(scenario, base_dir=base, **kwargs)


class VariablesTest(unittest.TestCase):
    def test_set_inc_dec_and_substitution(self, tmp_path=None):
        import tempfile
        with tempfile.TemporaryDirectory() as base:
            result = run([{"type": "set", "name": "n", "value": "3"},
                          {"type": "set", "name": "n", "op": "inc"},
                          {"type": "set", "name": "n", "op": "dec", "by": 2},
                          {"type": "notify", "text": "n=${n} missing=${nope:-x}"}], base)
        self.assertTrue(result["ok"])
        self.assertEqual(result["variables"]["n"], 2)
        self.assertIn("n=2 missing=x", result["text"])

    def test_unknown_step_and_bad_name_rejected(self):
        with self.assertRaises(S.ScenarioError):
            S.normalize_scenario([{"type": "explode"}])
        with self.assertRaises(S.ScenarioError):
            S.normalize_scenario([{"type": "set", "name": "bad-name!", "value": "1"}])


class FilesTest(unittest.TestCase):
    def test_create_edit_and_idempotent_rerun(self):
        import tempfile
        scenario = [{"type": "file_create", "path": "notes/a.txt", "content": "hello ${who}",
                     "require_confirm": False},
                    {"type": "file_edit", "path": "notes/a.txt", "mode": "replace_first",
                     "find": "hello", "replace": "bye"}]
        with tempfile.TemporaryDirectory() as base:
            first = run(scenario, base, variables={"who": "world"}, confirmed=True)
            self.assertTrue(first["ok"], first["log"])
            self.assertEqual((Path(base) / "notes" / "a.txt").read_text(encoding="utf-8"), "bye world")
            second = run([{"type": "file_edit", "path": "notes/a.txt", "mode": "replace_first",
                           "find": "hello", "replace": "bye"}], base, confirmed=True)
            self.assertTrue(second["ok"], "replacing an absent pattern is a no-op, not an error")
            self.assertEqual((Path(base) / "notes" / "a.txt").read_text(encoding="utf-8"), "bye world")
            again = run(scenario, base, variables={"who": "world"}, confirmed=True)
            self.assertFalse(again["ok"], "creating an existing file without overwrite must fail")
            self.assertEqual((Path(base) / "notes" / "a.txt").read_text(encoding="utf-8"), "bye world")

    def test_dotdot_and_symlink_escape_refused(self):
        import tempfile
        with tempfile.TemporaryDirectory() as base:
            outside = Path(base).parent / "outside.txt"
            result = run([{"type": "file_create", "path": "../outside.txt", "content": "x"}], base, confirmed=True)
            self.assertFalse(result["ok"])
            self.assertFalse(outside.exists())
            link = Path(base) / "escape"
            link.symlink_to(Path(base).parent, target_is_directory=True)
            result = run([{"type": "file_create", "path": "escape/evil.txt", "content": "x"}], base, confirmed=True)
            self.assertFalse(result["ok"])
            self.assertFalse((Path(base).parent / "evil.txt").exists())

    def test_destructive_step_needs_confirmation(self):
        import tempfile
        with tempfile.TemporaryDirectory() as base:
            result = run([{"type": "file_create", "path": "a.txt", "content": "x", "require_confirm": True}],
                         base, confirmed=False)
            self.assertFalse(result["ok"])
            self.assertFalse((Path(base) / "a.txt").exists())
            self.assertIn("onfirm", result["log"][0].get("error", ""))
            ok = run([{"type": "file_create", "path": "a.txt", "content": "x", "require_confirm": True}],
                     base, confirmed=True)
            self.assertTrue(ok["ok"])
            self.assertEqual(len(ok["log"]), 1)
            self.assertEqual(ok["log"][0]["detail"], "a.txt")


class RunTest(unittest.TestCase):
    def test_allowlist_and_refusal(self):
        import tempfile
        with tempfile.TemporaryDirectory() as base:
            ok = run([{"type": "run", "argv": [sys.executable, "-c", "print('hi')"]}], base,
                     allowlist=[Path(sys.executable).name], confirmed=True)
            self.assertTrue(ok["ok"], ok["log"])
            self.assertIn("hi", ok["text"])
            refused = run([{"type": "run", "argv": [sys.executable, "-c", "print('hi')"]}], base,
                          allowlist=[], confirmed=True)
            self.assertFalse(refused["ok"])
            self.assertFalse(S.check_run_allowed([sys.executable, "-c", "x"], []))
            self.assertTrue(S.check_run_allowed(["notepad.exe"], ["Notepad.EXE"]))

    def test_timeout_kills_the_command(self):
        import tempfile
        with tempfile.TemporaryDirectory() as base:
            result = run([{"type": "run", "argv": [sys.executable, "-c", "import time; time.sleep(30)"],
                           "timeout": 1}], base, allowlist=[Path(sys.executable).name], confirmed=True)
            self.assertFalse(result["ok"])


class LogicTest(unittest.TestCase):
    def test_if_repeat_macro_and_loop_variable(self):
        import tempfile
        scenario = [{"type": "set", "name": "total", "value": "0"},
                    {"type": "repeat", "times": 3, "steps": [
                        {"type": "set", "name": "total", "op": "add", "by": 1},
                        {"type": "macro", "name": "mark", "steps": [
                            {"type": "set", "name": "seen", "value": "${loop.index}"}]}]},
                    {"type": "if", "cond": {"left": "${total}", "op": "eq", "right": "3"},
                     "then": [{"type": "notify", "text": "three"}],
                     "else": [{"type": "notify", "text": "other"}]}]
        with tempfile.TemporaryDirectory() as base:
            result = run(scenario, base)
        self.assertTrue(result["ok"])
        self.assertEqual(result["variables"]["total"], 3)
        self.assertEqual(result["variables"]["seen"], 2)
        self.assertIn("three", result["text"])

    def test_endless_while_loop_is_capped(self):
        import tempfile
        scenario = [{"type": "repeat", "while": {"left": "1", "op": "eq", "right": "1"},
                     "steps": [{"type": "set", "name": "n", "op": "inc", "by": 1}]}]
        with tempfile.TemporaryDirectory() as base:
            result = run(scenario, base)
        # A while-repeat without "times" is bounded to MAX_REPEAT iterations.
        self.assertTrue(result["ok"])
        self.assertEqual(result["variables"]["n"], S.MAX_REPEAT)
        nested = [{"type": "repeat", "times": 20, "steps": [
            {"type": "repeat", "times": 20, "steps": [
                {"type": "set", "name": "n", "op": "inc", "by": 1}]}]}]
        with tempfile.TemporaryDirectory() as base:
            capped = run(nested, base)
        self.assertFalse(capped["ok"], "20x20 nested steps must hit the executed-step cap")
        self.assertLessEqual(capped["steps"], S.MAX_EXECUTED)

    def test_integration_step_uses_the_runner(self):
        import tempfile

        async def fake_runner(tool, args):
            self.assertEqual(tool, "telegram_use_send")
            return True, f"sent to {args['chat']}", {}

        with tempfile.TemporaryDirectory() as base:
            result = run([{"type": "integration", "tool": "telegram_use_send",
                           "args": {"chat": "${dest}"}}], base,
                         variables={"dest": "me"}, integration_runner=fake_runner)
        self.assertTrue(result["ok"])
        self.assertIn("sent to me", result["text"])

    def test_every_step_is_logged(self):
        import tempfile
        with tempfile.TemporaryDirectory() as base:
            result = run([{"type": "set", "name": "a", "value": "1"},
                          {"type": "notify", "text": "done"}], base)
        self.assertEqual([entry["type"] for entry in result["log"]], ["set", "notify"])
        self.assertTrue(all("ms" in entry for entry in result["log"]))


if __name__ == "__main__":
    unittest.main()
