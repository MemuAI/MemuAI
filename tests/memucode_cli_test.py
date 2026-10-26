"""MemuCode CLI: settings, tool protocol, permissions, tools, the agent loop,
sessions, compaction, pricing, the Jev decision client, commands, the
terminal UI and the headless mode.

Everything is offline: a scripted backend stands in for the model, the data
folder is temporary (tests/__init__.py) and MemuAI Proxy is never started.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["MEMUCODE_NO_PROXY_LAUNCH"] = "1"
os.environ["MEMUAI_PROXY_URL"] = "http://127.0.0.1:9"

_SHARED = Path(os.environ.get("MEMUCODE_TEST_DATA") or "")
if _SHARED.name.startswith("memucode-cli-tests-") and _SHARED.is_dir():
    TEST_DATA = _SHARED
else:
    TEST_DATA = Path(tempfile.mkdtemp(prefix="memucode-cli-tests-"))
    os.environ["MEMUCODE_TEST_DATA"] = str(TEST_DATA)
_PATCHES = []
_ENV_BEFORE = os.environ.get("MEMUCODE_CONFIG_DIR")
os.environ["MEMUCODE_CONFIG_DIR"] = str(TEST_DATA / "memucode")

from unittest.mock import patch  # noqa: E402

from app import code_checkpoints, code_stats, pricing  # noqa: E402
from app.cli import compact, convert, paths, protocol  # noqa: E402
from app.cli.agent import Agent  # noqa: E402
from app.cli.i18n import set_language  # noqa: E402
from app.cli.llm import Backend, ModelInfo, StreamEvent, ThinkTagFilter, Usage, to_anthropic_messages  # noqa: E402
from app.cli.permissions import parse_rule, rule_matches  # noqa: E402
from app.cli.runtime import Runtime  # noqa: E402
from app.cli.session import Session, list_sessions  # noqa: E402
from app.cli.settings import Settings, SettingsError  # noqa: E402
from app.cli.tools.shell import is_read_only, rule_prefix, split_commands  # noqa: E402
from app.cli.ui_base import BaseUI  # noqa: E402
from app.decisions import DecisionClient, DecisionsError, Question  # noqa: E402

set_language("en")

def setUpModule():
    import app.config
    import app.mcp_client
    import app.model_info
    import app.pgdata
    from app import code_skills
    from app.cli import llm as cli_llm
    from app.cli import voice as cli_voice

    os.environ["MEMUCODE_CONFIG_DIR"] = str(TEST_DATA / "memucode")
    memu = TEST_DATA / "memuai"
    for module, name, value in (
            (app.config, "DATA_DIR", str(TEST_DATA)), (paths, "DATA_DIR", str(TEST_DATA)),
            (cli_llm, "DATA_DIR", str(TEST_DATA)), (cli_voice, "DATA_DIR", str(TEST_DATA)),
            (app.model_info, "DATA_DIR", str(TEST_DATA)), (app.pgdata, "MEMU_DIR", memu),
            (code_checkpoints, "CHECKPOINT_ROOT", memu / "checkpoints"),
            (code_skills, "PERSONAL_ROOT", memu / "code_skills"),
            (app.mcp_client, "CONFIG_PATH", memu / "mcp.json"),
            (pricing, "MEMU_DIR", memu), (pricing, "OWN_CACHE", memu / "pricing-cache.json"),
            (pricing, "CATALOG_MODULE_CACHE", memu / "modules" / "persist" / "catalog.json"),
            (pricing, "OVERRIDES_FILE", memu / "pricing-overrides.json"),
            (pricing, "_index_stamp", (-2.0, -2.0)),

            (pricing, "refresh_online", lambda **_kwargs: {})):
        patcher = patch.object(module, name, value)
        patcher.start()
        _PATCHES.append(patcher)

    for path in (paths.home(), paths.memu_dir(), paths.sessions_root(), code_stats._path(),
                 code_checkpoints.CHECKPOINT_ROOT, pricing.OWN_CACHE, app.mcp_client.CONFIG_PATH,
                 app.model_info._path(), paths.history_file(), paths.user_settings_file()):
        if not str(Path(path).resolve()).startswith(str(TEST_DATA.resolve())):
            raise RuntimeError(f"test data path escapes the temporary folder: {path}")

def tearDownModule():
    while _PATCHES:
        _PATCHES.pop().stop()
    if _ENV_BEFORE is None:
        os.environ.pop("MEMUCODE_CONFIG_DIR", None)
    else:
        os.environ["MEMUCODE_CONFIG_DIR"] = _ENV_BEFORE

class Scripted(Backend):
    """A model that answers from a list; native mode emits real tool calls."""

    name = "scripted"

    def __init__(self, turns, native=False):
        self.turns = list(turns)
        self.native_tools = native
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        turn = self.turns.pop(0) if self.turns else "Done."
        if isinstance(turn, dict):
            if turn.get("text"):
                yield StreamEvent("text", text=turn["text"])
            for call in turn.get("calls", []):
                yield StreamEvent("tool_call", call=call)
        else:
            for index in range(0, len(turn), 5):
                yield StreamEvent("text", text=turn[index:index + 5])
                await asyncio.sleep(0)
        yield StreamEvent("usage", usage=Usage(input=1000, output=100, estimated=False, requests=1))
        yield StreamEvent("done", finish="stop")

class Recorder(BaseUI):
    interactive = True

    def __init__(self, answers=None):
        self.answers = list(answers or [])
        self.events = []
        self.queued = []

    def tool_started(self, agent, call):
        self.events.append(("start", call.name))

    def tool_finished(self, agent, call, result):
        self.events.append(("done", call.name, result.ok, result.error))

    async def ask_permission(self, request):
        self.events.append(("ask", request.tool.name, request.key))
        return self.answers.pop(0) if self.answers else {"choice": "yes"}

    async def approve_plan(self, plan):
        return {"choice": "auto"}

    def take_queued(self):
        items, self.queued = self.queued, []
        return items

def make_runtime(files=None, *, native=False, turns=(), settings=None, ui=None):
    root = Path(tempfile.mkdtemp(prefix="memucode-test-"))
    for name, text in (files or {}).items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    config = Settings(root, settings or {})
    runtime = Runtime(root, config, ui=ui or Recorder(), model="test-model")
    backend = Scripted(turns, native=native)
    runtime.router.resolve = lambda model: (backend, model)
    runtime.router._models = [ModelInfo(id="test-model", provider="custom", context=200_000, vision=True)]
    return runtime, backend

def run(coro):
    return asyncio.run(coro)

class SettingsTests(unittest.TestCase):
    def test_layers_merge_and_permission_lists_concatenate(self):
        root = Path(tempfile.mkdtemp())
        (root / ".memucode").mkdir()
        (root / ".memucode" / "settings.json").write_text(json.dumps({
            "effort": "high", "permissions": {"allow": ["Bash(npm test:*)"]}, "proxy_api_key": "leak"}), encoding="utf-8")
        (root / ".memucode" / "settings.local.json").write_text(json.dumps({
            "permissions": {"allow": ["Edit(src/**)"]}, "theme": "light"}), encoding="utf-8")
        settings = Settings(root, {"theme": "dark-ansi"})
        settings.trust()
        self.assertEqual(settings.get("effort"), "high")
        self.assertEqual(settings.get("theme"), "dark-ansi")
        self.assertEqual(settings.source_of("theme"), "cli")
        self.assertIn("Bash(npm test:*)", settings.get("permissions.allow"))
        self.assertIn("Edit(src/**)", settings.get("permissions.allow"))
        self.assertEqual(settings.get("proxy_api_key"), "")
        self.assertTrue(any("secret" in warning for warning in settings.warnings))

    def test_set_validates_and_refuses_project_secrets(self):
        root = Path(tempfile.mkdtemp())
        settings = Settings(root)
        settings.trust()
        settings.set("auto_compact_threshold", "150", "local")
        self.assertEqual(settings.get("auto_compact_threshold"), 98)
        settings.set("thinking", "yes", "project")
        self.assertIs(settings.get("thinking"), True)
        with self.assertRaises(SettingsError):
            settings.set("effort", "turbo")
        with self.assertRaises(SettingsError):
            settings.set("decisions.api_key", "x", "project")
        self.assertTrue(settings.unset("thinking", "project"))

    def test_trust_is_remembered_for_subfolders(self):
        root = Path(tempfile.mkdtemp())
        settings = Settings(root)
        self.assertFalse(settings.is_trusted())
        settings.trust()
        (root / "sub").mkdir()
        self.assertTrue(settings.is_trusted(root / "sub"))

class ProtocolTests(unittest.TestCase):
    def test_parser_handles_the_shapes_web_models_produce(self):
        text = ('Reading.\n```memuai-tool\n[{"tool":"Read","args":{"file_path":"a.py"}},'
                '{"name":"Bash","arguments":"{\\"command\\": \\"echo ```x```\\"}"},]\n```\nafter')
        result = protocol.parse(text)
        self.assertEqual([call.name for call in result.calls], ["Read", "Bash"])
        self.assertEqual(result.calls[1].args["command"], "echo ```x```")
        self.assertEqual(result.text, "Reading.\n\nafter")
        tagged = protocol.parse('x <tool_call>{"name":"Grep","arguments":{"pattern":"y"}}</tool_call>')
        self.assertEqual(tagged.calls[0].args, {"pattern": "y"})
        xml = protocol.parse('<function_calls><invoke name="Edit"><parameter name="replace_all">true</parameter>'
                             '</invoke></function_calls>')
        self.assertIs(xml.calls[0].args["replace_all"], True)
        broken = protocol.parse('```memuai-tool\n[{"tool": nope}]\n```')
        self.assertFalse(broken.calls)
        self.assertTrue(broken.errors)

    def test_stream_hider_never_leaks_tool_json(self):
        text = ('Here is code:\n```python\nprint(1)\n```\nNow tools.\n```memuai-tool\n'
                '[{"tool":"Read","args":{"file_path":"z.md"}}]\n```\nTail text')
        for step in (1, 2, 3, 7, 50):
            hider = protocol.StreamHider()
            shown = "".join(hider.feed(text[i:i + step]) for i in range(0, len(text), step)) + hider.flush()
            self.assertNotIn("memuai-tool", shown)
            self.assertNotIn('"tool"', shown)
            self.assertIn("```python", shown)
            self.assertIn("Tail text", shown)
            self.assertTrue(hider.saw_tool)

    def test_think_tags_become_reasoning(self):
        split = ThinkTagFilter()
        parts = []
        for chunk in ("<thi", "nk>\nplan it</th", "ink>\n\nAnswer"):
            parts += split.feed(chunk)
        parts += split.flush()
        reasoning = "".join(text for kind, text in parts if kind == "reasoning")
        answer = "".join(text for kind, text in parts if kind == "text")
        self.assertEqual(reasoning.strip(), "plan it")
        self.assertEqual(answer, "Answer")

class PermissionTests(unittest.TestCase):
    def test_rules_and_command_analysis(self):
        runtime, _ = make_runtime()
        bash = runtime.tools.resolve("Bash")
        edit = runtime.tools.resolve("Edit")
        fetch = runtime.tools.resolve("WebFetch")
        self.assertTrue(rule_matches(parse_rule("Bash(npm test:*)"), bash, "npm test -- --watch"))
        self.assertFalse(rule_matches(parse_rule("Bash(npm test:*)"), bash, "npm testing"))
        self.assertTrue(rule_matches(parse_rule("Edit(src/**)"), edit, "src/a/b.py"))
        self.assertTrue(rule_matches(parse_rule("Edit"), runtime.tools.resolve("Write"), "x"))
        self.assertTrue(rule_matches(parse_rule("WebFetch(domain:github.com)"), fetch, "domain:api.github.com"))
        self.assertEqual(split_commands('git status && echo "a && b" | wc -l'), ["git status", 'echo "a && b"', "wc -l"])
        for command in ("git status", "git log --oneline -5 | head -3", "ls -la", "rg foo src", "Get-ChildItem",
                        "python --version", "npm ls"):
            self.assertTrue(is_read_only(command), command)
        for command in ("rm -rf build", "git push", "echo x > file", "git branch -D main", "npm install",
                        "cat a | xargs rm", "sed -i s/a/b/ f"):
            self.assertFalse(is_read_only(command), command)
        self.assertEqual(rule_prefix("npm run build -- --prod"), "npm run build")
        self.assertEqual(rule_prefix("python -m pytest tests"), "python -m pytest")

    def test_modes_and_deny_rules(self):
        async def scenario():
            runtime, _ = make_runtime({"a.txt": "x\n"}, settings={"permissions": {
                "deny": ["Read(**/.env)"], "allow": ["Bash(echo:*)"]}})
            from app.cli.tools.base import ToolCall, ToolContext
            manager = runtime.permissions

            async def check(name, args):
                tool = runtime.tools.resolve(name)
                call = ToolCall(name=name, args=args)
                prepared = await tool.prepare(ToolContext(runtime, call), args)
                return await manager.check(call, tool, prepared)

            self.assertTrue((await check("Read", {"file_path": "a.txt"})).allow)
            self.assertFalse((await check("Read", {"file_path": ".env"})).allow)
            self.assertTrue((await check("Bash", {"command": "echo hi && git status"})).allow)
            manager.set_mode("bypassPermissions")
            from app.cli.tools.base import ToolError
            with self.assertRaises(ToolError):
                await check("Bash", {"command": "shutdown /s /t 0"})
            self.assertFalse((await check("Read", {"file_path": ".env"})).allow)
            manager.set_mode("plan")
            decision = await check("Write", {"file_path": "b.txt", "content": "y"})
            self.assertFalse(decision.allow)
            self.assertIn("Plan mode", decision.reason)
            manager.set_mode("acceptEdits")
            self.assertTrue((await check("Write", {"file_path": "b.txt", "content": "y"})).allow)
            outside = Path(tempfile.mkdtemp()) / "c.txt"
            runtime.ui.answers = [{"choice": "no"}]
            self.assertFalse((await check("Write", {"file_path": str(outside), "content": "y"})).allow)
            self.assertEqual(runtime.ui.events[-1][0], "ask")

        run(scenario())

class ToolTests(unittest.TestCase):
    def call(self, runtime, name, args, turn="t1"):
        async def go():
            from app.cli.tools.base import ToolCall, ToolContext, ToolError, ToolResult
            tool = runtime.tools.resolve(name)
            call = ToolCall(name=tool.name, args=args)
            ctx = ToolContext(runtime, call, turn=turn)
            try:
                prepared = await tool.prepare(ctx, args)
                return await tool.run(ctx, args, prepared)
            except ToolError as exc:
                return ToolResult.fail(str(exc))
        return run(go())

    def test_edit_needs_a_fresh_read_and_keeps_line_endings(self):
        runtime, _ = make_runtime()
        target = runtime.root / "win.txt"
        target.write_bytes("first\r\n    second  \r\nthird\r\n".encode("utf-8"))
        refused = self.call(runtime, "Edit", {"file_path": "win.txt", "old_string": "first", "new_string": "1st"})
        self.assertFalse(refused.ok)
        self.assertIn("has not been read", refused.error)
        self.assertTrue(self.call(runtime, "Read", {"file_path": "win.txt"}).ok)
        fuzzy = self.call(runtime, "Edit", {"file_path": "win.txt", "old_string": "    second\nthird",
                                            "new_string": "    SECOND\nthird"})
        self.assertTrue(fuzzy.ok, fuzzy.error)
        self.assertIn("ignoring whitespace", fuzzy.output)
        self.assertEqual(target.read_bytes(), b"first\r\n    SECOND\r\nthird\r\n")
        target.write_text("changed elsewhere\n", encoding="utf-8")
        stale = self.call(runtime, "Edit", {"file_path": "win.txt", "old_string": "changed", "new_string": "c"})
        self.assertIn("modified on disk", stale.error)

    def test_write_keeps_cp1251_and_checkpoints_allow_undo(self):
        runtime, _ = make_runtime()
        target = runtime.root / "ru.txt"
        target.write_bytes("Привет\n".encode("cp1251"))
        self.call(runtime, "Read", {"file_path": "ru.txt"})
        result = self.call(runtime, "Write", {"file_path": "ru.txt", "content": "Пока\n"}, turn="turn-a")
        self.assertTrue(result.ok, result.error)
        self.assertEqual(target.read_bytes(), "Пока\n".encode("cp1251"))
        self.call(runtime, "Write", {"file_path": "new/deep.txt", "content": "x"}, turn="turn-a")
        restored = code_checkpoints.restore(runtime.session.id, runtime.root, turn="turn-a")
        self.assertEqual(target.read_bytes(), "Привет\n".encode("cp1251"))
        self.assertFalse((runtime.root / "new" / "deep.txt").exists())
        self.assertTrue(restored["restored"] or restored["removed"])

    def test_read_ranges_dedupe_and_images(self):
        runtime, _ = make_runtime({"big.py": "".join(f"line {n}\n" for n in range(1, 3001))})
        first = self.call(runtime, "Read", {"file_path": "big.py"})
        self.assertIn("continue with offset=301", first.output)
        again = self.call(runtime, "Read", {"file_path": "big.py"})
        self.assertIn("unchanged", again.output)
        window = self.call(runtime, "Read", {"file_path": "big.py", "offset": 2999, "limit": 5})
        self.assertIn("3000\tline 3000", window.output)
        from PIL import Image
        Image.new("RGB", (40, 30), (200, 10, 10)).save(runtime.root / "pic.png")
        image = self.call(runtime, "Read", {"file_path": "pic.png"})
        self.assertEqual(image.images[0]["media_type"], "image/jpeg")
        missing = self.call(runtime, "Read", {"file_path": "bigg.py"})
        self.assertIn("Did you mean", missing.error)

    def test_search_tools(self):
        runtime, _ = make_runtime({"src/app.ts": "export function main() {}\n", "src/util.tsx": "const x = 1\n",
                                   "docs/readme.md": "main entry\n"})
        glob = self.call(runtime, "Glob", {"pattern": "src/**/*.{ts,tsx}"})
        self.assertEqual(sorted(glob.output.splitlines()), ["src/app.ts", "src/util.tsx"])
        grep = self.call(runtime, "Grep", {"pattern": "main", "output_mode": "content", "type": "ts"})
        self.assertIn("src/app.ts:1:export function main", grep.output)
        self.assertNotIn("readme", grep.output)
        count = self.call(runtime, "Grep", {"pattern": "main", "output_mode": "count"})
        self.assertIn("docs/readme.md:1", count.output)
        listing = self.call(runtime, "LS", {})
        self.assertIn("src/", listing.output)

    def test_multi_edit_is_all_or_nothing(self):
        runtime, _ = make_runtime({"m.py": "a = 1\nb = 2\n"})
        self.call(runtime, "Read", {"file_path": "m.py"})
        failed = self.call(runtime, "MultiEdit", {"file_path": "m.py", "edits": [
            {"old_string": "a = 1", "new_string": "a = 10"}, {"old_string": "zzz", "new_string": "q"}]})
        self.assertFalse(failed.ok)
        self.assertEqual((runtime.root / "m.py").read_text(), "a = 1\nb = 2\n")
        done = self.call(runtime, "MultiEdit", {"file_path": "m.py", "edits": [
            {"old_string": "a = 1", "new_string": "a = 10"}, {"old_string": "b = 2", "new_string": "b = 20"}]})
        self.assertTrue(done.ok, done.error)
        self.assertEqual((runtime.root / "m.py").read_text(), "a = 10\nb = 20\n")

    def test_bash_runs_in_the_sandbox_and_tracks_cd(self):
        runtime, _ = make_runtime({"sub/x.txt": "hi"})
        result = self.call(runtime, "Bash", {"command": "echo sandboxed-ok"})
        self.assertTrue(result.ok, result.error)
        self.assertIn("sandboxed-ok", result.output)
        self.assertIn("exit code 0", result.output)
        self.call(runtime, "Bash", {"command": "cd sub"})
        self.assertTrue(str(runtime.session.cwd).endswith("sub"))
        failing = self.call(runtime, "Bash", {"command": "exit 3"})
        self.assertFalse(failing.ok)
        self.assertEqual(failing.meta.get("exit_code"), 3)

class AgentLoopTests(unittest.TestCase):
    def test_text_protocol_round_trip(self):
        runtime, backend = make_runtime({"app.py": "def add(a, b):\n    return a - b\n"}, turns=[
            'Reading.\n```memuai-tool\n[{"tool":"Read","args":{"file_path":"app.py"}},'
            '{"tool":"Grep","args":{"pattern":"def"}}]\n```',
            'Fixing.\n```memuai-tool\n[{"tool":"Edit","args":{"file_path":"app.py","old_string":"a - b",'
            '"new_string":"a + b"}}]\n```',
            "Fixed."])
        result = run(Agent(runtime).run_turn("fix add"))
        self.assertEqual(result.stopped, "done")
        self.assertEqual(result.text, "Fixed.")
        self.assertIn("a + b", (runtime.root / "app.py").read_text())
        second = backend.requests[1].messages[-1]["content"]
        self.assertIn('<result tool="Read"', second)
        self.assertIn("return a - b", second)
        self.assertIn("How to call tools", backend.requests[0].system)
        self.assertIn("app.py", backend.requests[0].system)
        roles = [message["role"] for message in runtime.session.messages]
        self.assertEqual(roles, ["user", "assistant", "tool", "tool", "assistant", "tool", "assistant"])

    def test_native_tools_and_conversion(self):
        runtime, backend = make_runtime({"n.txt": "one\n"}, native=True, turns=[
            {"text": "Looking.", "calls": [{"id": "c1", "name": "Read", "args": {"file_path": "n.txt"}}]},
            {"text": "It says one."}])
        result = run(Agent(runtime).run_turn("what is in n.txt"))
        self.assertEqual(result.text, "It says one.")
        request = backend.requests[1]
        self.assertTrue(request.tools)
        self.assertNotIn("How to call tools", request.system)
        assistant = [m for m in request.messages if m["role"] == "assistant"][0]
        self.assertEqual(assistant["tool_calls"][0]["function"]["name"], "Read")
        tool = [m for m in request.messages if m["role"] == "tool"][0]
        self.assertEqual(tool["tool_call_id"], "c1")
        anthropic = to_anthropic_messages(request.messages, "claude-x")
        self.assertEqual(anthropic[1]["content"][-1]["type"], "tool_use")
        self.assertEqual(anthropic[2]["content"][0]["type"], "tool_result")

    def test_invalid_blocks_unknown_tools_and_denials_are_reported_to_the_model(self):
        runtime, backend = make_runtime(turns=[
            '```memuai-tool\n[{"tool": oops}]\n```',
            '```memuai-tool\n[{"tool":"Teleport","args":{}}]\n```',
            '```memuai-tool\n[{"tool":"Write","args":{"file_path":"x.txt","content":"x"}}]\n```',
            "ok"], ui=Recorder([{"choice": "no"}]))
        result = run(Agent(runtime).run_turn("go"))
        self.assertEqual(result.stopped, "done")
        texts = [str(request.messages[-1]["content"]) for request in backend.requests[1:]]
        self.assertIn("could not be read", texts[0])
        self.assertIn("line 2", texts[0])
        self.assertIn("Unknown or unavailable tool", texts[1])
        self.assertIn("denied", texts[2])
        self.assertFalse((runtime.root / "x.txt").exists())

    def test_non_interactive_round_limit(self):
        loop = '```memuai-tool\n[{"tool":"LS","args":{}}]\n```'
        runtime, backend = make_runtime(turns=[loop] * 3 + ["summary"], settings={"max_turns": 3})
        runtime.ui.interactive = False
        result = run(Agent(runtime).run_turn("loop forever"))
        self.assertEqual(result.stopped, "limit")
        self.assertEqual(result.text, "summary")
        self.assertIsNone(backend.requests[-1].tools)

    def test_interrupt_keeps_partial_text_and_closes_calls(self):
        class Slow(Scripted):
            async def stream(self, request):
                self.requests.append(request)
                yield StreamEvent("text", text="Partial answer")
                await asyncio.sleep(5)
                yield StreamEvent("done")

        async def scenario():
            runtime, _ = make_runtime()
            backend = Slow([])
            runtime.router.resolve = lambda model: (backend, model)
            task = asyncio.ensure_future(Agent(runtime).run_turn("hello"))
            await asyncio.sleep(0.1)
            task.cancel()
            result = await task
            return runtime, result

        runtime, result = run(scenario())
        self.assertEqual(result.stopped, "interrupted")
        kinds = [(m["role"], m.get("kind")) for m in runtime.session.messages]
        self.assertIn(("assistant", None), kinds)
        self.assertEqual(kinds[-1], ("user", "interrupt"))
        self.assertIn("Partial answer", runtime.session.messages[-2]["content"])

    def test_queued_messages_join_the_running_turn(self):
        runtime, backend = make_runtime(turns=['```memuai-tool\n[{"tool":"LS","args":{}}]\n```', "Both done."])
        runtime.ui.queued = [{"content": "also do this", "display": "also do this"}]
        result = run(Agent(runtime).run_turn("first"))
        self.assertEqual(result.text, "Both done.")
        self.assertIn("also do this", json.dumps(backend.requests[1].messages, ensure_ascii=False))

    def test_sub_agent_runs_with_its_own_context(self):
        runtime, backend = make_runtime({"deep.txt": "secret word: kiwi\n"}, turns=[
            '```memuai-tool\n[{"tool":"Task","args":{"description":"find","prompt":"Find the secret word",'
            '"subagent_type":"explore"}}]\n```',
            '```memuai-tool\n[{"tool":"Read","args":{"file_path":"deep.txt"}}]\n```',
            "The word is kiwi.",
            "The sub-agent says: kiwi."])
        result = run(Agent(runtime).run_turn("what is the secret word? ask a sub-agent"))
        self.assertEqual(result.text, "The sub-agent says: kiwi.")
        sub_request = backend.requests[1]
        self.assertIn("Your role: explore", sub_request.system)
        self.assertNotIn("what is the secret word", json.dumps(sub_request.messages, ensure_ascii=False))
        self.assertIn("The word is kiwi.", str(backend.requests[3].messages[-1]["content"]))

    def test_plan_mode_blocks_edits_until_approved(self):
        runtime, _ = make_runtime({"p.txt": "a\n"}, turns=[
            '```memuai-tool\n[{"tool":"Write","args":{"file_path":"p.txt","content":"b"}}]\n```',
            '```memuai-tool\n[{"tool":"ExitPlanMode","args":{"plan":"1. change p.txt"}}]\n```',
            '```memuai-tool\n[{"tool":"Read","args":{"file_path":"p.txt"}}]\n```',
            '```memuai-tool\n[{"tool":"Write","args":{"file_path":"p.txt","content":"b"}}]\n```',
            "done"])
        runtime.permissions.set_mode("plan")
        run(Agent(runtime).run_turn("change p"))
        self.assertEqual(runtime.permissions.mode, "acceptEdits")
        self.assertEqual((runtime.root / "p.txt").read_text(), "b")

class SessionTests(unittest.TestCase):
    def test_transcript_persists_images_rewind_fork_and_listing(self):
        root = Path(tempfile.mkdtemp())
        session = Session(root)
        data = base64.b64encode(b"\x89PNG fake").decode()
        session.append({"role": "user", "kind": "prompt", "content": [{"type": "text", "text": "look"},
                                                                     {"type": "image", "media_type": "image/png", "data": data}]})
        session.append({"role": "assistant", "content": "seen"})
        second = session.append({"role": "user", "kind": "prompt", "content": "again"})
        session.append({"role": "assistant", "content": "ok"})
        session.flush_index()
        loaded = Session.load(root, session.id)
        self.assertEqual(len(loaded.messages), 4)
        stored = loaded.messages[0]["content"][1]
        self.assertNotIn("data", stored)
        self.assertTrue(Path(stored["path"]).is_file())
        loaded.truncate_from(second["id"])
        self.assertEqual(len(Session.load(root, session.id).messages), 2)
        child = loaded.fork("branch")
        self.assertEqual(child.meta["forked_from"], session.id)
        self.assertEqual(len(Session.load(root, child.id).messages), 2)
        ids = [row["id"] for row in list_sessions(root)]
        self.assertIn(session.id, ids)
        self.assertIn(child.id, ids)

    def test_compaction_keeps_the_tail_and_history(self):
        async def scenario():
            runtime, _ = make_runtime(settings={"keep_recent_messages": 2})
            for index in range(8):
                runtime.session.append({"role": "user", "kind": "prompt", "content": f"request {index}"})
                runtime.session.append({"role": "assistant", "content": f"answer {index}"})

            async def fake_answer(system, user, **kwargs):
                self.assertIn("request 0", user)
                return "SUMMARY OF EARLIER WORK"
            runtime.quick_answer = fake_answer
            result = await compact.compact(runtime)
            return runtime, result

        runtime, result = run(scenario())
        self.assertTrue(result["ok"])
        view = runtime.session.api_messages()
        self.assertEqual(view[0]["kind"], "summary")
        self.assertIn("SUMMARY OF EARLIER WORK", view[0]["content"])
        self.assertEqual(view[-1]["content"], "answer 7")
        self.assertNotIn("request 0", json.dumps(view[1:], ensure_ascii=False))
        self.assertEqual(len(runtime.session.messages), 17)

    def test_old_large_tool_results_are_elided(self):
        runtime, _ = make_runtime()
        session = runtime.session
        for index in range(6):
            session.append({"role": "user", "kind": "prompt", "content": f"q{index}"})
            session.append({"role": "assistant", "content": "", "tool_calls": [{"id": f"c{index}", "name": "Read", "args": {}}]})
            session.append({"role": "tool", "tool_call_id": f"c{index}", "name": "Read", "content": "x" * 5000})
        elided = compact.elide_old_results(runtime)
        self.assertGreaterEqual(elided, 3)
        messages = convert.to_text_protocol(session.api_messages())
        self.assertIn(convert.ELIDED, json.dumps(messages, ensure_ascii=False))
        self.assertIn("x" * 100, json.dumps(messages[-1]))

class PricingTests(unittest.TestCase):
    def test_housekeeping_does_not_start_after_shutdown(self):
        runtime, _ = make_runtime()
        calls = []
        runtime._housekeeping = lambda: calls.append(1)

        async def scenario():
            await runtime.startup(load_models=False)
            self.assertTrue(runtime._housekeeping_timer.is_alive())
            await runtime.shutdown()

        run(scenario())
        runtime._housekeeping_timer.join(1)
        self.assertFalse(runtime._housekeeping_timer.is_alive())
        self.assertEqual(calls, [])

    def test_lookup_and_estimates(self):
        opus = pricing.lookup("genspark-claude-opus-5")
        self.assertEqual((opus.input, opus.output), (5.0, 25.0))
        self.assertEqual(pricing.lookup("antigravity-claude-opus-4-6-thinking").output, 25.0)
        self.assertEqual(pricing.lookup("@anthropic:claude-haiku-4-5").input, 1.0)
        self.assertEqual(pricing.lookup("local-gemma-3-1b").source, "local")
        self.assertIsNone(pricing.lookup("totally-unknown-model-xyz"))
        override = pricing.lookup("genspark-claude-opus-5", overrides={"*opus-5*": {"input": 1, "output": 2}})
        self.assertEqual((override.input, override.source), (1.0, "override"))
        estimate = pricing.estimate("claude-sonnet-5", input_tokens=1_000_000, output_tokens=100_000,
                                    cache_read=500_000)
        self.assertAlmostEqual(estimate.cost, 0.5 * 2 + 0.5 * 0.2 + 0.1 * 10, places=6)
        self.assertEqual(pricing.billing_kind("genspark-gpt-5.6-sol"), "subscription")
        self.assertEqual(pricing.billing_kind("@openrouter:x"), "api")
        self.assertEqual(pricing.format_usd(0.1234), "$0.123")

    def test_memucode_statistics_carry_costs(self):
        code_stats.record({"model": "genspark-claude-opus-5", "tokens_in": 200_000, "tokens_out": 10_000,
                           "session": "s1", "user_message": True})
        summary = code_stats.summary(7)
        row = next(item for item in summary["models"] if item["model"] == "genspark-claude-opus-5")
        self.assertAlmostEqual(row["cost"], 0.2 * 5 + 0.01 * 25, places=4)
        self.assertGreaterEqual(summary["cost"]["api_equivalent"], row["cost"])

class DecisionTests(unittest.TestCase):
    def test_http_contract_retries_and_errors(self):
        import httpx
        seen = []

        def handler(request):
            body = json.loads(request.content)
            seen.append((request.url.path, request.headers.get("authorization"), body))
            if len(seen) == 1:
                return httpx.Response(429, headers={"retry-after": "0"})
            return httpx.Response(200, json={"model": "jev-1.13.0", "usage": {"input_tokens": 1000}, "answers": {
                "risk": {"type": "score", "score": 1.5, "legend": {"0": "a", "1": "b", "2": "c", "3": "d"},
                         "probabilities": {"0": 0.1, "1": 0.4, "2": 0.4, "3": 0.1}, "confidence": 0.7},
                "ok": {"type": "noul", "noul": 0.9},
                "pick": {"type": "choice", "choice": "b", "probabilities": {"a": 0.2, "b": 0.8}, "confidence": 0.8}}})

        client = DecisionClient({"provider": "typesafe", "api_key": "k"}, transport=httpx.MockTransport(handler))
        decision = run(client.ask({"x": 1}, {"risk": Question.score("How risky?", ["a", "b", "c", "d"]),
                                            "ok": Question.noul("Is it fine"),
                                            "pick": Question.choice("Which?", {"a": "A", "b": "B"})}))
        path, auth, body = seen[-1]
        self.assertEqual(path, "/v1/systemone")
        self.assertEqual(auth, "Bearer k")
        self.assertEqual(body["model"], "jev-latest")
        self.assertEqual(body["questions"]["pick"]["criteria"], {"a": "A", "b": "B"})
        self.assertAlmostEqual(decision["risk"].normalized, 0.5)
        self.assertEqual(decision["pick"].choice, "b")
        self.assertAlmostEqual(decision.cost, 1000 * 0.042 / 1e6)
        cached = run(client.ask({"x": 1}, {"ok": Question.noul("Is it fine")}))
        self.assertFalse(cached.cached)
        again = run(client.ask({"x": 1}, {"ok": Question.noul("Is it fine")}))
        self.assertTrue(again.cached)

        def denied(request):
            return httpx.Response(401, json={"error": {"message": "bad key"}})
        bad = DecisionClient({"provider": "openrouter", "api_key": "k"}, transport=httpx.MockTransport(denied))
        with self.assertRaises(DecisionsError) as caught:
            run(bad.noul("s", "t"))
        self.assertIn("invalid or missing API key", str(caught.exception))
        with self.assertRaises(DecisionsError):
            run(bad.choose("s", "q", ["only one"]))

    def test_emulated_decisions_with_a_chat_model(self):
        async def llm(system, user):
            self.assertIn("decision function", system)
            return '```json\n{"answers": {"q": {"type": "choice", "probabilities": {"yes": 3, "no": 1}}}}\n```'
        client = DecisionClient({"provider": "model"}, llm=llm)
        answer = run(client.choose("text", "Pick", ["yes", "no"]))
        self.assertEqual(answer.choice, "yes")
        self.assertAlmostEqual(answer.probabilities["yes"], 0.75)

class ProjectTests(unittest.TestCase):
    def test_memory_files_imports_custom_commands_and_skills(self):
        runtime, _ = make_runtime({
            "MEMUCODE.md": "Use pnpm. See @docs/style.md\n",
            "docs/style.md": "Tabs, not spaces.\n",
            ".memucode/commands/deploy/staging.md": "---\ndescription: Deploy it\nargument-hint: <branch>\n---\nDeploy $ARGUMENTS now ($1).",
            ".memucode/skills/fastapi/SKILL.md": "---\nname: FastAPI\ndescription: Build FastAPI apps\n---\nUse routers.",
        })
        runtime.settings.trust()
        runtime.invalidate_prompt(memory=True)
        memory = runtime.memory_files()
        self.assertTrue(any("Tabs, not spaces." in row.text for row in memory))
        commands = runtime.commands()
        self.assertIn("deploy:staging", commands)
        from app.cli.project import expand_command
        self.assertEqual(expand_command(commands["deploy:staging"], "main fast"), "Deploy main fast now (main).")
        self.assertEqual(runtime.skills.get("fastapi")["source"], "project")
        agent = Agent(runtime)
        system = runtime.system_prompt(agent.tools(), native=False)
        self.assertIn("Tabs, not spaces.", system)
        self.assertIn("- fastapi:", system)

    def test_every_command_is_documented_in_both_languages(self):
        import importlib
        from app.cli import commands
        for module in ("config_cmds", "dashboard_cmds", "decisions_cmds", "foreign_cmds", "import_cmds", "info_cmds",
                       "model_cmds", "routing_cmds", "schedule_cmds", "session_cmds", "skill_cmds", "work_cmds"):
            importlib.import_module("app.cli.commands." + module)
        names = set()
        for command in commands.REGISTRY.values():
            self.assertTrue(command.ru and command.en, command.name)
            self.assertIn(command.category, commands.CATEGORIES)
            for name in (command.name, *command.aliases):
                self.assertNotIn(name, names, f"duplicate command name {name}")
                names.add(name)
        for required in ("doctor", "add-dir", "advisor", "compact", "background", "btw", "branch", "cd", "clear",
                         "color", "settings", "context", "usage", "copy", "effort", "goal", "help", "mcp", "memory",
                         "model", "permissions", "plan", "skills", "voice", "resume", "continue", "rewind", "cost",
                         "decisions", "init", "review", "status", "export", "theme", "vim", "hooks", "agents", "tasks"):
            self.assertIn(required, names)
        self.assertGreaterEqual(len(commands.REGISTRY), 60)

class InterfaceTests(unittest.TestCase):
    def test_startup_question_takes_keys_typed_before_the_interface_started(self):
        """An Enter typed while MemuCode starts arrives as a line feed, before the first-run picker existed."""
        from prompt_toolkit.data_structures import Size
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output.vt100 import Vt100_Output
        from app.cli import main as cli_main
        from app.cli.tui.app import TerminalUI

        class Sink(io.StringIO):
            def isatty(self):
                return True

        async def scenario():
            runtime, _ = make_runtime()
            sink = Sink()
            original = sys.stdout
            sys.stdout = sink
            try:
                ui = TerminalUI(runtime)
                output = Vt100_Output(sink, lambda: Size(rows=40, columns=100), term="xterm-256color", enable_cpr=False)
                with create_pipe_input() as keys:
                    keys.send_text("\x1b[B\n")
                    answered = cli_main._onboarding(ui)

                    async def after_start(terminal):
                        await asyncio.wait_for(answered, 5)
                        terminal.exit()

                    with patch("app.cli.commands.config_cmds.apply_live"):
                        await asyncio.wait_for(ui.run(input=keys, output=output, after_start=after_start), 10)
            finally:
                sys.stdout = original
            return runtime

        prefs = Path(paths.user_settings_file())
        backup = prefs.read_bytes() if prefs.exists() else None
        try:
            runtime = run(scenario())
            self.assertEqual(runtime.settings.get("theme"), "light")
            self.assertTrue(runtime.settings.get("onboarded"))
        finally:

            if backup is None:
                prefs.unlink(missing_ok=True)
            else:
                prefs.write_bytes(backup)

    def test_terminal_ui_accepts_keys_and_renders_a_turn(self):
        from prompt_toolkit.data_structures import Size
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output.vt100 import Vt100_Output
        from app.cli.tui.app import TerminalUI

        class Sink(io.StringIO):
            def isatty(self):
                return True

        async def scenario():
            runtime, backend = make_runtime({"a.txt": "alpha\n"}, turns=[
                '```memuai-tool\n[{"tool":"Read","args":{"file_path":"a.txt"}}]\n```', "The file says **alpha**."])
            runtime.settings.overrides["onboarded"] = True
            sink = Sink()
            original = sys.stdout
            sys.stdout = sink
            try:
                ui = TerminalUI(runtime)
                output = Vt100_Output(sink, lambda: Size(rows=40, columns=100), term="xterm-256color", enable_cpr=False)
                with create_pipe_input() as keys:
                    async def drive():
                        await asyncio.sleep(0.2)
                        keys.send_text("line one\x1b\rline two")
                        await asyncio.sleep(0.2)
                        buffer_text = ui.input_buffer.text
                        keys.send_text("\x15" * 3)
                        ui.input_buffer.reset()
                        keys.send_text("what is in a.txt?\r")
                        for _ in range(60):
                            await asyncio.sleep(0.1)
                            if not ui.busy and runtime.session.messages:
                                break
                        keys.send_text("\x1b[Z")
                        await asyncio.sleep(0.2)
                        ui.exit()
                        return buffer_text
                    driver = asyncio.ensure_future(drive())
                    await ui.run(input=keys, output=output)
                    buffer_text = await driver
                await runtime.shutdown()
            finally:
                sys.stdout = original
            plain = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07", "", sink.getvalue())
            return runtime, buffer_text, plain

        runtime, buffer_text, plain = run(scenario())
        self.assertEqual(buffer_text, "line one\nline two")
        self.assertIn("The file says", plain)
        self.assertIn("Read", plain)
        self.assertEqual(runtime.permissions.mode, "acceptEdits")

    def test_fresh_install_without_a_chosen_model(self):

        from app.cli import headless
        prefs = paths.memu_dir() / "preferences.json"
        backup = prefs.read_bytes() if prefs.exists() else None
        root = Path(tempfile.mkdtemp(prefix="memucode-fresh-"))
        try:
            prefs.parent.mkdir(parents=True, exist_ok=True)
            prefs.write_text(json.dumps({"default_model": "genspark-claude-opus-5"}), encoding="utf-8")
            self.assertEqual(Runtime(root, Settings(root, {"model": ""})).model, "genspark-claude-opus-5")
            prefs.unlink()
            runtime = Runtime(root, Settings(root, {"model": ""}))
            self.assertEqual(runtime.model, "")

            async def scenario():
                async def models(refresh=False):
                    return []
                runtime.router.models = models
                buffer = io.StringIO()
                original = sys.stdout
                sys.stdout = buffer
                try:
                    code = await headless.run(runtime, "hello", fmt="json")
                finally:
                    sys.stdout = original
                return code, json.loads(buffer.getvalue())

            code, data = run(scenario())
            self.assertNotEqual(code, 0)
            self.assertTrue(data["is_error"])
            self.assertIn("/proxy add", data["result"])
        finally:
            if backup is not None:
                prefs.write_bytes(backup)
            elif prefs.exists():
                prefs.unlink()

    def test_headless_json_output(self):
        from app.cli import headless
        runtime, _ = make_runtime(turns=["Headless answer."])

        async def scenario():
            async def models(refresh=False):
                return runtime.router._models
            runtime.router.models = models
            buffer = io.StringIO()
            original = sys.stdout
            sys.stdout = buffer
            try:
                code = await headless.run(runtime, "say something", fmt="json")
            finally:
                sys.stdout = original
            return code, json.loads(buffer.getvalue())

        code, data = run(scenario())
        self.assertEqual(code, 0)
        self.assertEqual(data["result"], "Headless answer.")
        self.assertEqual(data["session_id"], runtime.session.id)
        self.assertGreater(data["usage"]["input_tokens"], 0)

class PackagingTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def test_build_installer_and_requirements_ship_the_cli(self):
        sys.path.insert(0, str(self.ROOT))
        import build
        target = build.TARGETS["memucode"]
        self.assertTrue(target["console"])
        self.assertEqual(target["entry"], "memucode.py")
        self.assertIn("--console", build.pyinstaller_cmd(target, False))
        self.assertIn("--windowed", build.pyinstaller_cmd(build.TARGETS["memuai"], False))
        self.assertIn("faster_whisper", target["exclude"])

        self.assertRegex((self.ROOT / "build.py").read_text(encoding="utf-8"),
                         r'\[(?:"[a-z_]+", )*"memucode"(?:, "[a-z_]+")*\]')

        from installer import engine as setup_engine, products
        cli_component = products.SUITE.component("memucode")
        self.assertEqual(cli_component.source, "dist/MemuCode")
        self.assertEqual(cli_component.exe, "MemuCode\\MemuCode.exe")
        self.assertIn(("memucode", "MemuCode"), products.SUITE.path_entries)
        self.assertEqual(setup_engine.add_path_entry("C:\\a", "C:\\x\\MemuCode"), "C:\\a;C:\\x\\MemuCode")
        self.assertEqual(setup_engine.remove_path_entry("C:\\a;C:\\x\\MemuCode", "C:\\x\\MemuCode"), "C:\\a")
        engine_source = (self.ROOT / "installer" / "engine.py").read_text(encoding="utf-8")
        self.assertIn("broadcast_environment", engine_source)
        self.assertIn("removing=True", engine_source)
        winplat = (self.ROOT / "installer" / "winplat.py").read_text(encoding="utf-8")
        self.assertIn('SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment"', winplat)
        requirements = (self.ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
        for package in ("prompt_toolkit", "rich", "anthropic"):
            self.assertIn(package, requirements)

    def test_doctor_lists_importable_components(self):
        import importlib
        from app.cli import doctor
        for module in doctor.COMPONENTS:
            importlib.import_module(module)

if __name__ == "__main__":
    unittest.main()
