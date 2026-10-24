"""Round 21: MemuCode CLI bug hunt — regression tests.

A scripted model replays tool-call sequences against real tools in a temporary project and the outcomes are
asserted: edit matching (whitespace, indentation, quotes, Read prefixes, CRLF, encodings), yes/no arguments sent as
strings, Glob/Grep on Windows-style projects (cp1251, UTF-16, absolute patterns, ".github"), command output in
legacy code pages, native tool calls with unreadable arguments, tool-call/result pairing after crashes and
compaction, context-overflow recovery, the final no-tools round, OpenAI message hygiene and the system prompt.
Everything is offline.
"""
from __future__ import annotations

import asyncio
import json
import os
import unittest
import unittest.mock
from pathlib import Path

from tests.memucode_cli_test import (Recorder, Scripted, make_runtime, run,  # noqa: F401
                                     setUpModule, tearDownModule)

from app.cli import convert, prompts
from app.cli.agent import Agent, context_overflow
from app.cli.llm import (ChatRequest, LLMError, OpenAIBackend, StreamEvent, Usage, openai_messages,
                         to_anthropic_messages, tool_arguments)
from app import code_sandbox
from app.cli.tools.base import ToolError, flag
from app.cli.tools.files import _decode_for_search, apply_edit, glob_regex


def _edit(text, old, new, **kw):
    return apply_edit(text, old, new, **kw)[0]


class EditMatching(unittest.TestCase):
    def test_exact_and_unique(self):
        self.assertEqual(_edit("a = 1\nb = 2\n", "b = 2", "b = 3"), "a = 1\nb = 3\n")

    def test_trailing_newline_with_trailing_spaces_in_file(self):
        # Used to fail: the fuzzy matcher compared the empty piece after the final "\n" with the next line.
        self.assertEqual(_edit("a\nfoo  \nbar\n", "foo\n", "FOO\n"), "a\nFOO\nbar\n")
        self.assertEqual(_edit("a\nfoo  \nbar\n", "foo\n", ""), "a\nbar\n")

    def test_indentation_shift_is_reindented(self):
        text = "class A:\n    def f(self):\n        return 1\n"
        self.assertEqual(_edit(text, "def f(self):\n    return 1", "def f(self):\n    return 2"),
                         "class A:\n    def f(self):\n        return 2\n")
        self.assertEqual(_edit("def f():\n  x = 1\n", "    def f():\n      x = 1", "    def f():\n      x = 2"),
                         "def f():\n  x = 2\n")

    def test_read_line_number_prefixes_are_stripped(self):
        self.assertEqual(_edit("a\nb\nc\n", "     2\tb", "     2\tB"), "a\nB\nc\n")
        self.assertEqual(_edit("x\ny\n", "   1\tx\n   2\ty", "X\nY"), "X\nY\n")

    def test_typographic_quotes(self):
        self.assertEqual(_edit("s = 'it\u2019s'\n", "s = 'it's'", "s = 1"), "s = 1\n")

    def test_ambiguous_match_names_the_lines(self):
        with self.assertRaises(ToolError) as caught:
            apply_edit("x = 1\ny\nx = 1\n", "x = 1", "x = 2")
        self.assertIn("lines 1, 3", str(caught.exception))
        with self.assertRaises(ToolError) as loose:
            apply_edit("if a:\n    go()\nif b:\n  go()\n", "        go()", "stop()")
        self.assertIn("several places", str(loose.exception))

    def test_not_found_points_at_the_closest_text(self):
        with self.assertRaises(ToolError) as caught:
            apply_edit("def main():\n    run_server(port=80)\n", "run_servr(port=80)", "x")
        self.assertIn("line 2", str(caught.exception))

    def test_crlf_old_string_matches_lf_text(self):
        self.assertEqual(_edit("a\nb\n", "a\r\nb", "z"), "z\n")

    def test_string_booleans(self):
        for value in ("false", "False", "0", "no", "нет", "", None, 0, False):
            self.assertFalse(flag(value), value)
        for value in ("true", "TRUE", "1", "yes", 1, True):
            self.assertTrue(flag(value), value)


class EditToolThroughTheAgent(unittest.TestCase):
    def test_replace_all_false_as_a_string_changes_one_place_only(self):
        runtime, _backend = make_runtime({"a.py": "x = 1\nx = 1\n"}, native=True, settings={
            "permission_mode": "bypassPermissions"}, turns=[
            {"calls": [{"id": "r1", "name": "Read", "args": {"file_path": "a.py"}}]},
            {"calls": [{"id": "e1", "name": "Edit", "args": {"file_path": "a.py", "old_string": "x = 1",
                                                             "new_string": "x = 2", "replace_all": "false"}}]},
            "Done."])
        run(Agent(runtime).run_turn("change it"))
        self.assertEqual((runtime.root / "a.py").read_text(encoding="utf-8"), "x = 1\nx = 1\n")   # ambiguous → error
        errors = [m for m in runtime.session.messages if m.get("role") == "tool" and m.get("name") == "Edit"]
        self.assertTrue(errors[0]["is_error"])
        self.assertIn("occurs 2 times", errors[0]["content"])

    def test_edit_keeps_crlf_and_cp1251(self):
        runtime, _backend = make_runtime({}, native=True, settings={"permission_mode": "bypassPermissions"}, turns=[
            {"calls": [{"id": "r1", "name": "Read", "args": {"file_path": "Мой проект/файл.txt"}}]},
            {"calls": [{"id": "e1", "name": "Edit", "args": {"file_path": "Мой проект/файл.txt",
                                                             "old_string": "Привет\n", "new_string": "Пока\n"}}]},
            "Done."])
        folder = runtime.root / "Мой проект"
        folder.mkdir()
        (folder / "файл.txt").write_bytes("Привет  \r\nмир\r\n".encode("cp1251"))
        run(Agent(runtime).run_turn("edit"))
        self.assertEqual((folder / "файл.txt").read_bytes(), "Пока\r\nмир\r\n".encode("cp1251"))


class SearchTools(unittest.TestCase):
    def test_glob_keeps_dot_folders(self):
        self.assertTrue(glob_regex("./.github/*.yml").match(".github/ci.yml"))

    def test_grep_reads_cp1251_and_utf16(self):
        self.assertEqual(_decode_for_search("Ошибка".encode("cp1251")), "Ошибка")
        self.assertEqual(_decode_for_search("Ошибка".encode("utf-16")), "Ошибка")
        runtime, _backend = make_runtime({}, native=True, turns=[
            {"calls": [{"id": "g1", "name": "Grep", "args": {"pattern": "Ошибка", "output_mode": "content"}},
                       {"id": "g2", "name": "Grep", "args": {"pattern": "print(", "-i": "false"}},
                       {"id": "g3", "name": "Glob", "args": {"pattern": "PLACEHOLDER/**/*.txt"}}]},
            "Done."])
        (runtime.root / "old.txt").write_bytes("строка: Ошибка\r\n".encode("cp1251"))
        (runtime.root / "ps.txt").write_bytes("Ошибка из PowerShell\r\n".encode("utf-16"))
        (runtime.root / "code.py").write_text("print(1)\n", encoding="utf-8")
        runtime.files.invalidate()
        _backend.turns[0]["calls"][2]["args"]["pattern"] = runtime.root.as_posix() + "/**/*.txt"
        run(Agent(runtime).run_turn("find"))
        results = {m["tool_call_id"]: m for m in runtime.session.messages if m.get("role") == "tool"}
        self.assertIn("old.txt:1:строка: Ошибка", results["g1"]["content"])
        self.assertIn("ps.txt", results["g1"]["content"])
        self.assertIn("not a valid regular expression", results["g2"]["content"])
        self.assertIn("code.py", results["g2"]["content"])
        self.assertIn("old.txt", results["g3"]["content"])                 # an absolute glob pattern
        self.assertFalse(results["g3"]["is_error"])


class CommandOutput(unittest.TestCase):
    def test_legacy_code_pages_are_decoded(self):
        with unittest.mock.patch.object(code_sandbox, "legacy_codecs", lambda: ["cp866", "cp1251"]):
            decoder = code_sandbox.OutputDecoder()
            text = decoder.decode("ok\n".encode() + "Ошибка компиляции\n".encode("cp866"), final=True)
            self.assertEqual(text, "ok\nОшибка компиляции\n")
            decoder = code_sandbox.OutputDecoder()
            mixed = decoder.decode("Привет\n".encode()[:3]) + decoder.decode("Привет\n".encode()[3:]
                                                                              + "Файл".encode("cp1251"), final=True)
            self.assertEqual(mixed, "Привет\nФайл")

    def test_split_utf8_character_waits_for_the_next_read(self):
        decoder = code_sandbox.OutputDecoder()
        data = "Ж".encode()
        self.assertEqual(decoder.decode(data[:1]) + decoder.decode(data[1:]), "Ж")

    def test_piped_stdin_is_decoded(self):
        from app.cli.main import decode_piped
        self.assertEqual(decode_piped("дифф".encode()), "дифф")
        self.assertEqual(decode_piped("дифф".encode("utf-16")), "дифф")
        self.assertEqual(decode_piped("дифф".encode("cp1251")), "дифф")


class NativeCalls(unittest.TestCase):
    def test_tool_arguments_repair_and_report(self):
        self.assertEqual(tool_arguments('{"command": "ls"}'), {"command": "ls"})
        self.assertEqual(tool_arguments(""), {})
        self.assertEqual(tool_arguments('"{\\"a\\": 1}"'), {"a": 1})                  # double-encoded
        self.assertEqual(tool_arguments('{"command": "ls",}'), {"command": "ls"})      # trailing comma
        self.assertIn("_invalid_json", tool_arguments('{"file_path": "a.py", "content": "unfinished'))

    def test_unreadable_native_arguments_are_explained_not_run(self):
        runtime, backend = make_runtime({}, native=True, settings={"permission_mode": "bypassPermissions"}, turns=[
            {"calls": [{"id": "w1", "name": "Write", "args": {"_invalid_json": '{"file_path": "big.py", "con'}}]},
            "Done."])
        run(Agent(runtime).run_turn("write"))
        result = next(m for m in runtime.session.messages if m.get("role") == "tool")
        self.assertTrue(result["is_error"])
        self.assertIn("not valid JSON or were cut off", result["content"])
        self.assertNotIn("missing required", result["content"])
        self.assertFalse((runtime.root / "big.py").exists())

    def test_reused_call_ids_are_renamed(self):
        runtime, backend = make_runtime({"a.txt": "x\n"}, native=True, turns=[
            {"calls": [{"id": "call_0", "name": "Read", "args": {"file_path": "a.txt"}}]},
            {"calls": [{"id": "call_0", "name": "Glob", "args": {"pattern": "*.txt"}}]},
            "Done."])
        run(Agent(runtime).run_turn("go"))
        ids = [call["id"] for m in runtime.session.messages if m.get("role") == "assistant"
               for call in m.get("tool_calls") or []]
        self.assertEqual(len(ids), len(set(ids)))
        sent = convert.to_native(runtime.session.api_messages())
        results = [m["tool_call_id"] for m in sent if m.get("role") == "tool"]
        self.assertEqual(sorted(results), sorted(ids))


def _sse(*chunks, finish="tool_calls"):
    rows = [json.dumps({"choices": [{"delta": chunk}]}) for chunk in chunks]
    rows.append(json.dumps({"choices": [{"delta": {}, "finish_reason": finish}]}))
    return ("".join(f"data: {row}\n\n" for row in rows) + "data: [DONE]\n\n").encode()


async def _stream_calls(body: bytes):
    import httpx
    backend = OpenAIBackend({"name": "x", "base_url": "https://api.example.com/v1", "api_key": "k"})
    backend._client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})))
    request = ChatRequest(model="m", system="", messages=[{"role": "user", "content": "hi"}])
    events = [event async for event in backend.stream(request)]
    await backend._client.aclose()
    return [event.call for event in events if event.kind == "tool_call"], events[-1].finish


class OpenAIStreaming(unittest.TestCase):
    def test_repeated_names_object_arguments_and_index_zero_parallel_calls(self):
        body = _sse({"tool_calls": [{"index": 0, "id": "a", "function": {"name": "Read", "arguments": ""}}]},
                    {"tool_calls": [{"index": 0, "function": {"name": "Read", "arguments": '{"file_path": "x.py"}'}}]},
                    {"tool_calls": [{"index": 0, "id": "b", "function": {"name": "Grep",
                                                                        "arguments": {"pattern": "def"}}}]})
        calls, finish = run(_stream_calls(body))
        self.assertEqual([call["name"] for call in calls], ["Read", "Grep"])
        self.assertEqual(calls[0]["args"], {"file_path": "x.py"})
        self.assertEqual(calls[1]["args"], {"pattern": "def"})
        self.assertEqual(finish, "tool_calls")

    def test_arguments_cut_by_the_length_limit_are_flagged(self):
        body = _sse({"tool_calls": [{"index": 0, "id": "a", "function": {
            "name": "Write", "arguments": '{"file_path": "a.py", "content": "print(1)'}}]}, finish="length")
        calls, finish = run(_stream_calls(body))
        self.assertEqual(finish, "length")
        self.assertIn("_invalid_json", calls[0]["args"])


class Pairing(unittest.TestCase):
    def test_missing_duplicate_and_stray_results(self):
        messages = [
            {"role": "user", "kind": "prompt", "content": "go"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "a", "name": "Read", "args": {}},
                                                                 {"id": "b", "name": "Read", "args": {}}]},
            {"role": "tool", "tool_call_id": "a", "name": "Read", "content": "one"},
            {"role": "tool", "tool_call_id": "a", "name": "Read", "content": "dup"},
            {"role": "user", "kind": "prompt", "content": "next"},          # crash: b never got a result
            {"role": "tool", "tool_call_id": "zzz", "name": "Read", "content": "stray"},
        ]
        out = convert.to_native(messages)
        tools = [m for m in out if m["role"] == "tool"]
        self.assertEqual([m["tool_call_id"] for m in tools], ["a", "b"])
        self.assertIn("no result was recorded", tools[1]["content"])
        self.assertEqual(out[4]["role"], "user")
        self.assertTrue(any("stray" in str(m.get("content")) and m["role"] == "user" for m in out))
        anthropic = to_anthropic_messages(out, "claude-x")
        uses = [b["id"] for m in anthropic for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_use"]
        results = [b["tool_use_id"] for m in anthropic for b in m["content"]
                   if isinstance(b, dict) and b.get("type") == "tool_result"]
        self.assertEqual(uses, results)

    def test_text_protocol_says_when_results_are_missing(self):
        messages = [{"role": "user", "kind": "prompt", "content": "go"},
                    {"role": "assistant", "content": "Reading.", "tool_calls": [{"id": "a", "name": "Read",
                                                                                "args": {"file_path": "x"}}]},
                    {"role": "user", "kind": "prompt", "content": "and?"}]
        out = convert.to_text_protocol(messages)
        self.assertIn("no result was recorded", out[2]["content"])
        self.assertEqual(out[3]["content"], "and?")

    def test_resumed_crashed_session_is_sent_well_formed(self):
        runtime, backend = make_runtime({}, native=True, turns=["Fine."])
        runtime.session.append({"role": "user", "kind": "prompt", "content": "old"})
        runtime.session.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": "lost", "name": "Bash", "args": {"command": "make"}}]})
        run(Agent(runtime).run_turn("continue"))
        sent = backend.requests[0].messages
        index = next(i for i, m in enumerate(sent) if m.get("tool_calls"))
        self.assertEqual(sent[index + 1]["role"], "tool")
        self.assertEqual(sent[index + 1]["tool_call_id"], "lost")


class OpenAIHygiene(unittest.TestCase):
    def test_session_fields_are_not_sent(self):
        rows = openai_messages([{"role": "assistant", "content": None, "_anthropic": {"x": 1},
                                 "tool_calls": [{"id": "a", "type": "function",
                                                 "function": {"name": "Read", "arguments": "{}"}}]},
                                {"role": "tool", "tool_call_id": "a", "content": "", "is_error": True}])
        self.assertNotIn("_anthropic", rows[0])
        self.assertNotIn("is_error", rows[1])
        self.assertTrue(rows[1]["content"].startswith("Error"))

    def test_tool_choice_none_and_body(self):
        backend = OpenAIBackend({"name": "x", "base_url": "https://api.example.com/v1", "api_key": "k"})
        request = ChatRequest(model="m", system="s", messages=[{"role": "user", "content": "hi", "kind": "prompt"}],
                              tools=[{"name": "Read", "description": "d", "parameters": {"type": "object",
                                                                                         "properties": {}}}],
                              tool_choice="none")
        body = backend._body(request)
        self.assertEqual(body["tool_choice"], "none")
        self.assertNotIn("kind", body["messages"][1])


class FinalRoundAndOverflow(unittest.TestCase):
    def test_round_limit_keeps_tools_declared_for_native_models(self):
        runtime, backend = make_runtime({"a.txt": "x\n"}, native=True, settings={"max_turns": 1}, turns=[
            {"calls": [{"id": "r1", "name": "Read", "args": {"file_path": "a.txt"}}]},
            "Summary."])
        runtime.ui.interactive = False
        result = run(Agent(runtime).run_turn("go"))
        self.assertEqual(result.stopped, "limit")
        final = backend.requests[-1]
        self.assertTrue(final.tools)                       # Anthropic rejects tool_use history without tools
        self.assertEqual(final.tool_choice, "none")

    def test_context_overflow_is_recovered_once(self):
        class Overflowing(Scripted):
            def __init__(self, turns):
                super().__init__(turns, native=True)
                self.failed = False

            async def stream(self, request):
                if not self.failed and request.system and "compress a coding session" not in request.system:
                    self.failed = True
                    raise LLMError("HTTP 400: This model's maximum context length is 128000 tokens", status=400)
                async for event in super().stream(request):
                    yield event

        runtime, _backend = make_runtime({}, native=True)
        backend = Overflowing(["Answer after compaction."])
        runtime.router.resolve = lambda model: (backend, model)
        for index in range(6):
            runtime.session.append({"role": "user", "kind": "prompt", "content": f"request {index} " + "x" * 3000})
            runtime.session.append({"role": "assistant", "content": f"answer {index}"})
        async def quick(system, user, **kw):
            return "summary of the past"
        runtime.quick_answer = quick
        result = run(Agent(runtime).run_turn("now"))
        self.assertEqual(result.stopped, "done", result.error)
        self.assertEqual(result.text, "Answer after compaction.")
        self.assertTrue(any(m.get("kind") == "summary" for m in runtime.session.messages))

    def test_overflow_detection(self):
        self.assertTrue(context_overflow(LLMError("HTTP 400: prompt is too long: 210000 tokens > 200000", status=400)))
        self.assertTrue(context_overflow(LLMError("context_length_exceeded", status=400)))
        self.assertFalse(context_overflow(LLMError("HTTP 401: invalid api key", status=401)))
        self.assertFalse(context_overflow(LLMError("HTTP 429: too many requests", status=429)))


class McpSchemas(unittest.TestCase):
    def test_full_json_schema_becomes_portable(self):
        from app.cli.tools.mcp import MCPTool, portable_schema
        schema = {"$schema": "http://json-schema.org/draft-07/schema#", "type": "object",
                  "additionalProperties": False, "properties": {
                      "url": {"anyOf": [{"type": "string", "format": "uri"}, {"type": "null"}], "description": "page"},
                      "count": {"type": ["integer", "null"], "minimum": 1},
                      "tags": {"type": "array"}, "mode": {"enum": ["a", "b"]}},
                  "required": ["url", "ghost"]}
        out = portable_schema(schema)
        self.assertEqual(out["properties"]["url"], {"type": "string", "description": "page (format: uri)"})
        self.assertEqual(out["properties"]["count"]["type"], "integer")
        self.assertEqual(out["properties"]["tags"]["items"], {"type": "string"})
        self.assertEqual(out["properties"]["mode"], {"type": "string", "enum": ["a", "b"]})
        self.assertEqual(out["required"], ["url"])
        Schemas().walk_portable(out)
        tool = MCPTool("my server.v2", {"name": "x" * 80, "schema": schema})
        self.assertLessEqual(len(tool.name), 64)
        self.assertRegex(tool.name, r"^[A-Za-z0-9_-]+$")


class Cancellation(unittest.TestCase):
    def test_escape_during_a_tool_keeps_one_copy_of_the_text_and_pairs_the_call(self):
        async def scenario():
            runtime, backend = make_runtime({}, native=True, settings={"permission_mode": "bypassPermissions"},
                                            turns=[{"text": "Running the build.",
                                                    "calls": [{"id": "b1", "name": "Bash",
                                                               "args": {"command": "sleep 5"}}]}])
            task = asyncio.ensure_future(Agent(runtime).run_turn("build"))
            for _ in range(100):
                await asyncio.sleep(0.05)
                if any(m.get("tool_calls") for m in runtime.session.messages):
                    break
            await asyncio.sleep(0.3)
            task.cancel()
            return runtime, await task

        runtime, result = run(scenario())
        self.assertEqual(result.stopped, "interrupted")
        texts = [m["content"] for m in runtime.session.messages if m.get("role") == "assistant"]
        self.assertEqual(sum("Running the build." in text for text in texts), 1, texts)
        results = [m for m in runtime.session.messages if m.get("role") == "tool"]
        self.assertEqual([m["tool_call_id"] for m in results], ["b1"])
        self.assertTrue(results[0]["is_error"])


class TextProtocolShapes(unittest.TestCase):
    def test_qwen_xml_calls_run(self):
        from app.cli import protocol
        parsed = protocol.parse("Reading.\n<tool_call>\n<function=Read>\n<parameter=file_path>\na.py\n</parameter>\n"
                                "<parameter=limit>\n20\n</parameter>\n</function>\n</tool_call>", finished=True)
        self.assertEqual([(c.name, c.args) for c in parsed.calls], [("Read", {"file_path": "a.py", "limit": 20})])
        self.assertEqual(parsed.text, "Reading.")

    def test_xml_text_arguments_stay_text(self):
        from app.cli import protocol
        parsed = protocol.parse('<function_calls><invoke name="Write"><parameter name="file_path">n.txt</parameter>'
                                '<parameter name="content">42</parameter></invoke></function_calls>')
        self.assertEqual(parsed.calls[0].args["content"], "42")            # a file with "42", not the number


class Schemas(unittest.TestCase):
    ALLOWED = {"type", "properties", "items", "required", "description", "enum"}

    def walk_portable(self, schema, where="schema"):
        for key in schema:
            assert key in self.ALLOWED, f"{where}: {key}"
        if schema.get("type") == "array":
            assert "items" in schema, where
        for name, prop in (schema.get("properties") or {}).items():
            assert "type" in prop, f"{where}.{name}"
            self.walk_portable(prop, f"{where}.{name}")
        if isinstance(schema.get("items"), dict):
            self.walk_portable(schema["items"], where + "[]")

    def test_every_tool_schema_is_portable(self):
        from app.cli.tools import build_registry
        registry = build_registry()

        def walk(schema, where):
            for key, value in schema.items():
                self.assertIn(key, self.ALLOWED, f"{where}: {key}")
            if schema.get("type") == "array":
                self.assertIn("items", schema, where)
            for name, prop in (schema.get("properties") or {}).items():
                self.assertIn("type", prop, f"{where}.{name} has no type (Gemini rejects it)")
                walk(prop, f"{where}.{name}")
            if isinstance(schema.get("items"), dict):
                walk(schema["items"], where + "[]")

        for tool in registry.tools.values():
            schema = tool.schema()
            self.assertTrue(schema["description"], tool.name)
            walk(schema["parameters"], tool.name)
            for name in schema["parameters"].get("required", []):
                self.assertIn(name, schema["parameters"]["properties"], tool.name)


if __name__ == "__main__":
    unittest.main()
