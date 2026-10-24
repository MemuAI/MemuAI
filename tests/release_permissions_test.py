"""Offline regression proof. Run with MemuAI's Python from the repository root."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests.memucode_cli_test import make_runtime, setUpModule, tearDownModule
from app.cli.tools.shell import find_git_bash, is_read_only
from app.cli.tools.base import ToolCall, ToolContext, Prepared
from app.cli.permissions import parse_rule


class ShellPermissionSecurity(unittest.TestCase):
    def test_benign_reads(self):
        for command in ("git status", "git log --oneline -5 | head -3", "ls -la", "rg foo src",
                        "Get-ChildItem", "python --version", "npm ls", "git diff --stat", "env",
                        'echo "data with > and ; and &&"', "sort file.txt", "uniq -c file.txt",
                        "sed -n '1,20p' file.txt", "Get-Content 'C:/some folder/a.txt'",
                        "git reflog", "git reflog show --oneline HEAD", "git grep -n needle",
                        "find . -type f -print0", "sort -fr input.txt", "file -b input.txt",
                        "hostname", "hostname -f", "hostname --all-ip-addresses", "date +%F",
                        "date --date=2026-10-02 +%F", "date -d 'last Sunday'", "date -u",
                        "tree -L 2", "yq -P . input.yaml", "terraform plan",
                        "sed -n '1p' one.txt two.txt", "sed -n '1p' -- -i input.txt"):
            self.assertTrue(is_read_only(command), command)

    def test_mutating_reads(self):
        for command in ('env python -c "print(1)"', "git diff --output=changed.txt",
                        'git diff "--output=changed.txt"', "sort -o changed.txt input.txt",
                        "sort --output=changed.txt input.txt", "uniq input.txt output.txt",
                        "sed -n 'w output.txt' input.txt", 'awk \'BEGIN {system("touch out")}\'',
                        "rg --pre python foo .", "rg --pre=python foo .", "yq -i 'x' a.yaml",
                        "go env -w GOPATH=x", "ipconfig /release", "nvidia-smi -pm 1",
                        'echo "$(touch x)"', 'Write-Output "$(New-Item x)"', "Get-Content (New-Item x)",
                        "Get-ChildItem | Select-Object {New-Item x}", "echo hi\nNew-Item x",
                        "LD_PRELOAD=x cat a.txt", "git diff @options"):
            self.assertFalse(is_read_only(command), command)

    def test_read_commands_with_mutating_subcommands_and_options(self):
        commands = (
            "git reflog expire --expire=all --all", "git reflog delete HEAD",
            "git reflog drop --all", "git reflog write HEAD new-message",
            "git grep --open-files-in-pager=command needle", "git grep -nOcommand needle",
            "git grep -O command needle", "find . -fprint0 marker.txt",
            "find . '-fprint0' marker.txt", "find . -fprintf marker.txt format",
            "find . -fprint marker.txt", "find . -fls marker.txt", "find . -delete",
            "sort -rooutput.txt input.txt", "sort --compress-program=command input.txt",
            "sort --compress-program command input.txt", "tree -aooutput.txt",
            "file --compile", "file --compile=magic", "file -bC magic",
            "hostname maliciousname", "hostname -F names.txt", "hostname --file=names.txt",
            "date 100210102026", "date --set=2026-10-02", "date -us100210102026",
            "yq -iP . input.yaml", "yq -Pi . input.yaml", "yq --inplace=true . input.yaml",
            "yq --in-place=true . input.yaml", "yq --split-exp=.name . input.yaml",
            "yq -s .name . input.yaml", "terraform plan -out=plan.bin",
            "terraform plan -out plan.bin", "terraform plan -generate-config-out=result.tf",
            "printf -v PATH .",
            "sed -n '1p' -i input.txt", "sed -n '1p' --in-place input.txt",
            "sed -n '1p' -i.bak input.txt", "sed -n '1p' --in-place=.bak input.txt",
            "sed -n '1p' -e 'w marker.txt' input.txt",
            "sed -n '1p' --expression='w marker.txt' input.txt",
            "sed -n '1p' -f program.sed input.txt", "awk -f program.awk input.txt",
            "awk 'BEGIN {print}'", "awk '{print}' input.txt",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertFalse(is_read_only(command))

    def test_default_and_plan_require_permission_for_mutating_read_options(self):
        for mode in ("default", "plan"):
            for command in ("git reflog expire --expire=all --all", "find . -fprint0 marker.txt",
                            "git grep -nOcommand needle", "yq -Pi . input.yaml"):
                with self.subTest(mode=mode, command=command):
                    self.assertFalse(self.check(command, mode).allow)

    def test_reflog_expire_has_a_real_write_effect(self):
        git = shutil.which("git")
        if not git:
            self.skipTest("git is unavailable")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
            def run(*args):
                return subprocess.run([git, *args], cwd=root, env=env, check=True, capture_output=True)
            run("init")
            (root / "a.txt").write_text("original", encoding="utf-8")
            run("add", "a.txt")
            run("-c", "user.name=Audit", "-c", "user.email=audit@example.invalid",
                "-c", "core.hooksPath=" + str(root / "no-hooks"), "commit", "-m", "initial")
            log = root / ".git" / "logs" / "HEAD"
            self.assertTrue(log.read_bytes())
            self.assertFalse(is_read_only("git reflog expire --expire=all --all"))
            run("reflog", "expire", "--expire=all", "--all")
            self.assertEqual(log.read_bytes(), b"")

    def test_find_fprint0_has_a_real_output_file(self):
        bash = find_git_bash() if os.name == "nt" else shutil.which("bash")
        if not bash:
            self.skipTest("Bash find is unavailable")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "input.txt").write_text("original", encoding="utf-8")
            command = "find . -maxdepth 1 -name input.txt -fprint0 marker.txt"
            self.assertFalse(is_read_only(command))
            subprocess.run([bash, "-c", command], cwd=root, check=True, capture_output=True)
            self.assertEqual((root / "marker.txt").read_bytes(), b"./input.txt\x00")

    def test_sed_trailing_flags_execute_an_additional_program_and_write_in_place(self):
        bash = find_git_bash() if os.name == "nt" else shutil.which("bash")
        if not bash:
            self.skipTest("Bash sed is unavailable")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input.txt"
            source.write_text("first\nsecond\n", encoding="utf-8")
            command = "sed -n '1p' -e 'w marker.txt' input.txt"
            self.assertFalse(is_read_only(command))
            # The added -e makes sed treat the original 1p as an input filename.
            # Its nonzero exit still leaves the writing program's output file.
            subprocess.run([bash, "-c", command], cwd=root, capture_output=True)
            self.assertEqual((root / "marker.txt").read_text(encoding="utf-8"), "first\nsecond\n")
            command = "sed -n '1p' -i input.txt"
            self.assertFalse(is_read_only(command))
            subprocess.run([bash, "-c", command], cwd=root, check=True, capture_output=True)
            self.assertEqual(source.read_text(encoding="utf-8"), "first\n")

    def check(self, command, mode="default", rules=()):
        async def scenario():
            runtime, _ = make_runtime(settings={"permission_mode": mode})
            runtime.ui.interactive = False
            runtime.permissions.session_allow.extend(parse_rule(rule, "session") for rule in rules)
            call = ToolCall("Bash", {"command": command})
            tool = runtime.tools.resolve("Bash")
            prepared = await tool.prepare(ToolContext(runtime, call), call.args)
            return await runtime.permissions.check(call, tool, prepared)
        return asyncio.run(scenario())

    def test_default_and_plan_deny_expansions_and_newline_commands(self):
        for mode in ("default", "plan"):
            for command in ('Write-Output "$(New-Item x)"', "echo hi\nNew-Item x",
                            "git diff --output=x", 'env python -c "print(1)"'):
                self.assertFalse(self.check(command, mode).allow, (mode, command))

    def test_prefix_permission_cannot_clear_substitution(self):
        self.assertFalse(self.check('echo "$(New-Item x)"', rules=["Bash(echo:*)"]).allow)
        self.assertTrue(self.check("echo hi && git status", rules=["Bash(echo:*)"]).allow)

    def test_exact_rule_is_deliberate_permission(self):
        command = 'echo "$(New-Item x)"'
        self.assertTrue(self.check(command, rules=["Bash(" + command + ")"]).allow)

    def test_auto_floor_runs_before_cache(self):
        async def scenario():
            runtime, _ = make_runtime(settings={"permission_mode": "auto"})
            runtime.small_model_available = lambda: False
            tool = runtime.tools.resolve("Bash")
            safe = "npm test " + "x" * 600
            for command, expected in ((safe, True), (safe + "; git push origin main", False)):
                verdict = await runtime.permissions._classify(
                    ToolCall("Bash", {"command": command}), tool, command, Prepared(key=command))
                self.assertEqual(verdict[0], expected)
        asyncio.run(scenario())

    def test_cache_respects_diff_and_request(self):
        async def scenario():
            runtime, _ = make_runtime(settings={"permission_mode": "auto"})
            runtime.small_model_available = lambda: True
            answers = iter(("allow", "ask", "ask"))
            called = []
            async def answer(system, user):
                called.append(user)
                return json.dumps({"decision": next(answers), "reason": "test"})
            runtime.quick_answer = answer
            tool = runtime.tools.resolve("Write")
            call = ToolCall("Write", {"file_path": "a.py"})
            first = Prepared(key="a.py", diff="+pass")
            changed = Prepared(key="a.py", diff="+new risky change")
            self.assertTrue((await runtime.permissions._classify(call, tool, "a.py", first))[0])
            self.assertTrue((await runtime.permissions._classify(call, tool, "a.py", first))[0])
            self.assertFalse((await runtime.permissions._classify(call, tool, "a.py", changed))[0])
            runtime.session.append({"role": "user", "kind": "prompt", "content": "Changed request"})
            self.assertFalse((await runtime.permissions._classify(call, tool, "a.py", first))[0])
            self.assertEqual(len(called), 3)
        asyncio.run(scenario())

    def test_heuristic_checks_every_compound_part(self):
        runtime, _ = make_runtime(settings={"permission_mode": "auto"})
        tool = runtime.tools.resolve("Bash")
        for command, expected in (("npm test && git status", True),
                                  ('npm test; python -c "print(1)"', False),
                                  ('npm test && echo "$(touch marker)"', False)):
            self.assertEqual(runtime.permissions._heuristic(tool, command, Prepared(key=command))[0], expected)


if __name__ == "__main__":
    unittest.main()
