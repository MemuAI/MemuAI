"""Round 26: MemuCode CLI regressions found by running the CLI against live models."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="memucode-r26-"))
os.environ.setdefault("MEMUCODE_NO_PROXY_LAUNCH", "1")
os.environ.setdefault("MEMUAI_PROXY_URL", "http://127.0.0.1:9")

from app.cli.llm import ThinkTagFilter  # noqa: E402
from app.cli.permissions import Rule, _command_matches  # noqa: E402
from app.cli.tools.shell import is_read_only, is_static_command, split_commands  # noqa: E402


class HarmlessRedirectTests(unittest.TestCase):
    """``2>&1`` split into ``… 2>`` and ``1`` and made the command "dynamic", so ``--allowedTools
    "Bash(python -m unittest:*)"`` never matched what models actually run."""

    def test_stream_merge_is_not_a_separator(self):
        self.assertEqual(split_commands("cd p && python -m unittest 2>&1"), ["cd p", "python -m unittest 2>&1"])
        self.assertEqual(split_commands("ls &> log"), ["ls &> log"])
        self.assertEqual(split_commands("echo hi & rm -rf x"), ["echo hi", "rm -rf x"])

    def test_static_and_read_only(self):
        self.assertTrue(is_static_command("python -m unittest 2>&1"))
        self.assertTrue(is_read_only("git status 2>/dev/null"))
        self.assertTrue(is_read_only("dir 2>nul"))
        self.assertTrue(is_read_only("ls 2>&1 | head"))
        for command in ("ls > out.txt", "ls 2>&1 > x", "ls &> log", "cat a 2>/dev/nullx", "ls >/dev/null; rm x"):
            self.assertFalse(is_read_only(command), command)
        self.assertFalse(is_static_command("ls > out.txt"))

    def test_prefix_rule_matches_part_with_redirect(self):
        rule = Rule("Bash", "python -m unittest:*")
        parts = split_commands("cd /tmp/p && python -m unittest 2>&1")
        self.assertTrue(_command_matches(rule.pattern, parts[1]))


class ServiceTagTests(unittest.TestCase):
    """A direct connection to DeepSeek on Yandex Cloud printed ``<ds_safety>…</ds_safety>`` into the answer."""

    def run_filter(self, pieces):
        flt = ThinkTagFilter()
        out = []
        for piece in pieces:
            out += flt.feed(piece)
        out += flt.flush()
        return ("".join(t for k, t in out if k == "text"), "".join(t for k, t in out if k == "reasoning"))

    def test_split_anywhere(self):
        source = '<think>hm</think>Answer 3 < 5.<ds_safety>{"safe":true}</ds_safety> done <ds'
        for cut in range(1, len(source)):
            for cut2 in range(cut, len(source), 7):
                self.assertEqual(self.run_filter([source[:cut], source[cut:cut2], source[cut2:]]),
                                 ("Answer 3 < 5. done <ds", "hm"))

    def test_unclosed_block_is_dropped(self):
        self.assertEqual(self.run_filter(["ok<ds_safety>verdict"]), ("ok", ""))
        self.assertEqual(self.run_filter(["<ds_safety/>ok"]), ("ok", ""))


class DiffCommandTests(unittest.TestCase):
    """``/diff`` outside a git repository printed code_tools' Russian note in the English-only CLI."""

    def run_diff(self, root):
        import asyncio
        from types import SimpleNamespace
        from app.cli.commands import work_cmds
        said = []
        ctx = SimpleNamespace(runtime=SimpleNamespace(root=root, session=SimpleNamespace(id="r26-diff-none")),
                              args="", say=lambda text, level="info": said.append((text, level)),
                              print=lambda *a, **k: None)
        asyncio.run(work_cmds.diff(ctx))
        return said

    def test_not_a_repository_is_english(self):
        said = self.run_diff(Path(tempfile.mkdtemp()))
        self.assertEqual(said, [("Not a git repository and no edits yet", "info")])

    def test_git_error_is_not_reported_as_missing_repository(self):
        from unittest import mock
        from app import code_tools
        from app.work_tools import WorkError
        with mock.patch.object(code_tools, "tool_git", side_effect=WorkError("boom")):
            said = self.run_diff(Path(tempfile.mkdtemp()))
        self.assertEqual(said, [("git failed: boom", "warn")])


class CommandTextTests(unittest.TestCase):
    """Wording found by walking every slash command in the live TUI."""

    def test_release_notes_follow_the_suite_version(self):
        from app.cli import VERSION
        from app.cli.commands import info_cmds
        shown = []
        from types import SimpleNamespace
        import asyncio
        asyncio.run(info_cmds.release_notes(SimpleNamespace(md=shown.append)))
        self.assertTrue(shown[0].startswith(f"**MemuCode CLI {VERSION}**"))
        self.assertNotIn("1.0", shown[0].splitlines()[0])

    def test_resume_command_articles(self):
        from app.cli.commands import REGISTRY, foreign_cmds  # noqa: F401
        self.assertTrue(REGISTRY["resume-opencode"].en.startswith("Continue an OpenCode session"))
        self.assertTrue(REGISTRY["resume-codex"].en.startswith("Continue a Codex session"))

    def test_dashboard_counts_are_pluralised(self):
        import time
        from app.cli.commands.dashboard_cmds import _summary_line
        self.assertEqual(_summary_line([{"root": "/a", "prompts": 1, "updated": time.time()}]),
                         "1 conversation in 1 project · 1 prompt · this week: 1")

    def test_escape_cancels_bug_report_and_notifications(self):
        import asyncio
        from types import SimpleNamespace
        from unittest import mock
        from app.cli.commands import config_cmds, info_cmds
        said = []
        ctx = SimpleNamespace(ask=mock.AsyncMock(return_value=None), select=mock.AsyncMock(return_value=None),
                              say=lambda text, level="info": said.append(text),
                              settings=SimpleNamespace(get=lambda key, default=None: "auto"))
        with mock.patch("app.cli.doctor.run_checks", mock.AsyncMock(side_effect=AssertionError("ran"))):
            asyncio.run(info_cmds.bug(ctx))
        asyncio.run(config_cmds.notifications(ctx))
        self.assertEqual(said, [])


class LocalConnectionTests(unittest.TestCase):
    """A direct connection to a local server (LM Studio, Ollama) went through HTTP(S)_PROXY and failed
    with the proxy's "HTTP 404: not found"; a dead address gave no hint which address it was."""

    def test_loopback_ignores_system_proxy(self):
        import asyncio
        from unittest import mock
        from app.cli import llm

        async def make(url):
            client = llm.http_client(5, url=url)
            try:
                return client._trust_env
            finally:
                await client.aclose()

        with mock.patch.dict(os.environ, {"HTTP_PROXY": "http://proxy.invalid:1", "HTTPS_PROXY": "http://proxy.invalid:1"}):
            self.assertFalse(asyncio.run(make("http://127.0.0.1:1234/v1")))
            self.assertFalse(asyncio.run(make("http://localhost:11434/v1")))
            self.assertTrue(asyncio.run(make("https://api.openai.com/v1")))

    def test_network_error_names_the_address(self):
        import asyncio
        from app.cli import llm
        backend = llm.OpenAIBackend({"name": "dead", "format": "openai", "base_url": "http://127.0.0.1:9/v1",
                                     "api_key": "x", "models": ["m"]})

        async def go():
            from unittest import mock
            with mock.patch.object(llm.asyncio, "sleep", mock.AsyncMock()):
                async for _ in backend.stream(llm.ChatRequest(model="m", system="", messages=[{"role": "user", "content": "hi"}])):
                    pass

        with self.assertRaises(llm.LLMError) as caught:
            asyncio.run(go())
        self.assertIn("http://127.0.0.1:9/v1", str(caught.exception))


class MemuCodePanelSourceTests(unittest.TestCase):
    """The first project added in the MemuCode section left the right panel at "No project selected":
    refreshTree() without a project never set ``treeFor``, so sync() did not see a project change."""

    def test_no_project_marks_tree_owner(self):
        source = (Path(__file__).resolve().parents[1] / "app" / "pgstatic" / "memucode.js").read_text(encoding="utf-8")
        start = source.index("async function refreshTree()")
        branch = source[start:source.index("return;", start)]
        self.assertIn('state.treeFor = "";', branch)
        self.assertIn("state.treeFor !== undefined && state.treeFor !== projectId()", source)

    def test_refused_command_is_not_exit_undefined(self):
        """A code_run refused by the access policy has no exit_code: the chat showed "exit undefined"."""
        source = (Path(__file__).resolve().parents[1] / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn('<span class="step-note">exit ${row.exit_code}', source)
        self.assertIn('if (tool === "code_run") return result.exit_code == null ? ""', source)
        self.assertIn('stderr: result.stderr || result.error || ""', source)


if __name__ == "__main__":
    unittest.main()
