"""Round 15 (version 2.0): unreadable tool calls, tokens per second, pictures for models that cannot see,
the Browser, web search, collapsed thoughts, the stats layout, the version and the installer page.

Everything is offline except two checks that are skipped when their prerequisite is missing: the Browser run needs
Microsoft Edge (it drives a private headless copy on a local page; nothing of the user's is touched), and nothing
here ever starts Computer Use on the real desktop — its loop runs against a stand-in with no operating-system
calls."""
from __future__ import annotations

import asyncio
import base64
import http.server
import json
import os
import re
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from tests.memucode_cli_test import (TEST_DATA, Recorder, Scripted, make_runtime, run,  # noqa: F401
                                     setUpModule, tearDownModule)

from app import automation_runtime, instant_answers, model_info, search_engines, version, websearch
from app.cli import paths, protocol
from app.cli import vision as pictures
from app.cli.agent import MAX_INVALID_STREAK, Agent, _invalid_text
from app.cli.llm import Backend, LLMError, ModelInfo, StreamEvent, Usage
from app.cli.tokens import RateMeter, chunk_tokens
from app.cli.tools.base import ToolCall, ToolResult
from app.cli.tools.files import MultiEditTool
from app.cli.tui.activity import Step, classify, summary_parts

ROOT = Path(__file__).resolve().parents[1]
PNG = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")

class ToolCallsThatWereUnreadable(unittest.TestCase):
    """The three failures from the report, and the neighbours of each."""

    def test_a_todo_list_with_a_missing_bracket_and_a_missing_brace_is_read(self):
        text = ('Создаю сайт.\n```memuai-tool\n[{"tool": "TodoWrite", "args": {"todos": [{"content": "Setup", '
                '"status": "completed", "activeForm": "Setting up"}, {"content": "Build", "status": "in_progress", '
                '"activeForm": "Создаю адаптивный React-сайт"}}, {"tool": "Write", "args": {"file_path", '
                '"package.json", "content": "{}"}}]\n```')
        parsed = protocol.parse(text)
        self.assertEqual(parsed.errors, [])
        self.assertEqual([call.name for call in parsed.calls], ["TodoWrite", "Write"])
        self.assertEqual(len(parsed.calls[0].args["todos"]), 2)
        self.assertEqual(parsed.calls[1].args, {"file_path": "package.json", "content": "{}"})
        self.assertEqual(parsed.text, "Создаю сайт.")

    def test_one_missing_closing_bracket_does_not_cost_the_call(self):
        parsed = protocol.parse('```memuai-tool\n[{"tool": "TodoWrite", "args": {"todos": [{"content": "A", '
                                '"status": "pending"}}}, {"tool": "Read", "args": {"file_path": "a.py"}}]\n```')
        self.assertEqual((parsed.errors, [call.name for call in parsed.calls]), ([], ["TodoWrite", "Read"]))

    def test_content_references_find_their_blocks_however_the_blocks_are_spelled(self):
        edit = ('```memuai-tool\n[{"tool": "MultiEdit", "args": {"file_path": "src/App.jsx", "edits": '
                '[{"old_string": "@content:1", "new_string": "@content:2"}]}}]\n```\n')
        cases = {
            "other ids": '<memuai-content id="a">\nold\n</memuai-content>\n<memuai-content id="b">\nnew\n</memuai-content>',
            "no quotes": "<memuai-content id=1>\nold\n</memuai-content>\n<memuai-content id=2>\nnew\n</memuai-content>",
            "underscore": '<memuai_content id="1">\nold\n</memuai_content>\n<memuai_content id="2">\nnew\n</memuai_content>',
            "no closing tags": '<memuai-content id="1">\nold\n<memuai-content id="2">\nnew\n',
            "no ids at all": "<memuai-content>\nold\n</memuai-content>\n<memuai-content>\nnew\n</memuai-content>",
        }
        for label, blocks in cases.items():
            parsed = protocol.parse(edit + blocks, finished=True)
            self.assertEqual(parsed.errors, [], label)
            self.assertEqual(parsed.calls[0].args["edits"], [{"old_string": "old\n", "new_string": "new\n"}], label)

    def test_a_missing_block_is_one_clear_error_and_nothing_is_written_half(self):
        edit = ('```memuai-tool\n[{"tool": "MultiEdit", "args": {"file_path": "src/App.jsx", "edits": '
                '[{"old_string": "@content:1", "new_string": "@content:2"}]}}]\n```')
        parsed = protocol.parse(edit)
        self.assertEqual(parsed.calls, [])
        self.assertEqual(len(parsed.errors), 1, "one message for the call, not one per reference")
        self.assertIn("MultiEdit(src/App.jsx)", parsed.errors[0])
        self.assertIn('"@content:1"', parsed.errors[0])
        self.assertIn('"@content:2"', parsed.errors[0])
        cut = protocol.parse(edit + '\n<memuai-content id="1">\nold\n</memuai-content>\n<memuai-content id="2">\nnew')
        self.assertEqual(cut.calls, [], "an answer cut in the middle of a line is never written")
        self.assertEqual(cut.cut_off, ["MultiEdit"])
        whole = protocol.parse(edit + '\n<memuai-content id="1">\nold\n</memuai-content>\n<memuai-content id="2">\nnew\n',
                               finished=True)
        self.assertEqual(whole.errors, [], "a forgotten closing tag after a complete last line is forgiven")

    def test_a_fence_the_model_put_around_a_file_is_not_part_of_the_file(self):
        parsed = protocol.parse('```memuai-tool\n[{"tool": "Write", "args": {"file_path": "a.py", "content": '
                                '"@content:1"}}]\n```\n<memuai-content id="1">\n```python\nprint(1)\n```\n</memuai-content>')
        self.assertEqual(parsed.calls[0].args["content"], "print(1)\n")
        inside = protocol.parse('```memuai-tool\n[{"tool": "Write", "args": {"file_path": "a.md", "content": '
                                '"@content:1"}}]\n```\n<memuai-content id="1">\n```a\n```\nmid\n```b\n```\n</memuai-content>')
        self.assertIn("mid", inside.calls[0].args["content"])
        self.assertTrue(inside.calls[0].args["content"].startswith("```a"), "fences inside the text stay")

    def test_an_entry_that_is_not_a_call_does_not_fail_the_calls_around_it(self):
        parsed = protocol.parse('```memuai-tool\n[{"tool": "Read", "args": {"file_path": "src/App.jsx"}}, '
                                '{"tool": "Read", "args": {"file_path": "src/main.jsx"}}, '
                                '{"memuai-tool--redacted-arg--": "nope"}]\n```')
        self.assertEqual((parsed.errors, len(parsed.calls)), ([], 2))
        alone = protocol.parse('```memuai-tool\n[{"memuai-tool--redacted-arg--": "nope"}]\n```')
        self.assertEqual(alone.calls, [])
        self.assertIn("holds no tool call", alone.errors[0], "but a block with nothing in it is still reported")

    def test_other_shapes_models_write_are_read_too(self):
        flat = protocol.parse('```memuai-tool\n[{"tool": "Read", "file_path": "a.py"}, {"tool": "Skill", "name": "pdf"}]\n```')
        self.assertEqual([(call.name, call.args) for call in flat.calls],
                         [("Read", {"file_path": "a.py"}), ("Skill", {"name": "pdf"})])
        lines = protocol.parse('```memuai-tool\n{"tool": "Read", "args": {"file_path": "a.py"}}\n'
                               '{"tool": "Grep", "args": {"pattern": "x"}}\n```')
        self.assertEqual([call.name for call in lines.calls], ["Read", "Grep"], "one call per line")
        split = protocol.parse('```memuai-tool\n[{"tool": "Read"}, {"file_path": "a.py"}]\n```')
        self.assertEqual(split.calls[0].args, {"file_path": "a.py"})
        self.assertEqual([call.name for call in protocol.parse('```memuai-tool\n[["Read", {"file_path": "a.py"}]]\n```').calls],
                         ["Read"])
        self.assertEqual(protocol.parse('```memuai-tool\n[{"tool": "Bash", "args": {"command": "ls", "description": }}]\n```').errors, [])
        self.assertEqual(protocol.parse('```memuai-tool\n[{"tool": "Read", "args": {"file_path": "a.py"}}}]\n```').errors, [])

    def test_valid_json_is_still_taken_exactly_as_written(self):
        parsed = protocol.parse('```memuai-tool\n[{"tool": "Edit", "args": {"file_path": "a.py", "old_text": "x"}}]\n```')
        self.assertEqual(parsed.calls[0].args, {"file_path": "a.py", "old_text": "x"})
        self.assertEqual(parsed.repairs, [])

    def test_the_stream_hider_hides_every_spelling_of_a_content_block(self):
        hider = protocol.StreamHider()
        shown = hider.feed("Hi <memuai_content id=1>\nsecret\n</memuai_content> tail")
        self.assertEqual(shown + hider.flush(), "Hi  tail")

    def test_multiedit_accepts_the_shapes_models_send_its_edits_in(self):
        edit_list = MultiEditTool.edit_list
        want = [{"old_string": "a", "new_string": "b"}]
        self.assertEqual(edit_list(want), want)
        self.assertEqual(edit_list(json.dumps(want)), want)
        self.assertEqual(edit_list({"old_string": "a", "new_string": "b"}), want)
        self.assertEqual(edit_list({"edits": want}), want)
        self.assertEqual(edit_list([["a", "b"]]), want)
        self.assertEqual(edit_list([{"old_text": "a", "new_text": "b"}]), want)
        self.assertEqual(edit_list("nothing"), [])

    def test_todowrite_accepts_a_string_a_dict_and_plain_lines(self):
        runtime, backend = make_runtime(turns=[
            '```memuai-tool\n[{"tool":"TodoWrite","args":{"todos":"[{\\"content\\":\\"A\\",\\"status\\":\\"pending\\"}]"}}]\n```',
            "ok"])
        run(Agent(runtime).run_turn("go"))
        self.assertEqual([todo["content"] for todo in runtime.session.todos], ["A"])
        runtime, backend = make_runtime(turns=[
            '```memuai-tool\n[{"tool":"TodoWrite","args":{"todos":"- first\\n- second"}}]\n```', "ok"])
        run(Agent(runtime).run_turn("go"))
        self.assertEqual([todo["content"] for todo in runtime.session.todos], ["first", "second"])

    def test_the_model_is_told_how_to_say_it_right_and_blunter_when_it_repeats_itself(self):
        first = _invalid_text({"kind": "invalid", "error": "MultiEdit(a.js): content block missing: the arguments say "
                                                          '"@content:1" but this message has no <memuai-content> block'})
        self.assertIn("NOTHING was changed", first)
        self.assertIn("<memuai-content id=\"1\">", first)
        self.assertNotIn("in a row", first)
        third = _invalid_text({"kind": "invalid", "error": "line 1, column 3: expected ',' or ']'", "streak": "3"})
        self.assertIn("3rd unreadable call in a row", third)
        self.assertIn("balance", third)

    def test_a_model_that_cannot_get_out_of_it_is_stopped_with_a_reason(self):
        bad = '```memuai-tool\n[{"tool": oops}]\n```'
        runtime, backend = make_runtime(turns=[bad] * (MAX_INVALID_STREAK + 3))
        result = run(Agent(runtime).run_turn("go"))
        self.assertEqual(result.stopped, "error")
        self.assertEqual(len(backend.requests), MAX_INVALID_STREAK)
        self.assertIn(str(MAX_INVALID_STREAK), result.error)
        self.assertEqual(result.tools, 0, "an unreadable call is not a tool run")

        runtime, backend = make_runtime({"a.txt": "x\n"}, turns=[bad, bad, '```memuai-tool\n[{"tool":"LS","args":{}}]\n```',
                                                                  bad, bad, "done"])
        self.assertEqual(run(Agent(runtime).run_turn("go")).stopped, "done")

    def test_a_resent_call_is_quiet_in_the_activity_summary(self):
        invalid = Step(ToolCall(name="__invalid__", args={}), ToolResult.fail("x"))
        invalid.call.status = "error"
        read = Step(ToolCall(name="Read", args={"file_path": "a.py"}), ToolResult(ok=True, summary="ok"))
        read.call.status = "done"
        self.assertEqual(classify("__invalid__"), "badcall")
        text = "".join(part for part, _style in summary_parts([read, invalid]))
        self.assertNotIn("__invalid__", text)
        self.assertNotIn("failed", text)
        alone = "".join(part for part, _style in summary_parts([invalid]))
        self.assertEqual(alone.strip(), "Resent a tool call")

class TokensPerSecond(unittest.TestCase):
    def feed(self, pieces):
        meter, now, last = RateMeter(), 100.0, None
        for gap, tokens in pieces:
            now += gap
            meter.add(tokens, now)
            last = meter.rate(now)
        return last

    def test_a_steady_stream_shows_its_real_speed(self):
        self.assertAlmostEqual(self.feed([(0.125, 5.0)] * 80), 40.0, delta=1.0)
        self.assertAlmostEqual(self.feed([(0.5, 5.0)] * 20), 10.0, delta=0.5)

    def test_nothing_is_shown_until_there_is_a_stream_to_measure(self):
        self.assertIsNone(self.feed([(0.125, 5.0)] * 8), "one second of text is not a speed")
        self.assertIsNone(self.feed([(25.0, 800.0)]), "the whole answer arriving at once is not a speed")
        self.assertIsNone(self.feed([(20.0, 300.0), (2.0, 300.0)]), "two buffered dumps are not a speed either")

    def test_a_dump_does_not_poison_what_streams_after_it(self):
        speed = self.feed([(25.0, 800.0)] + [(0.1, 6.0)] * 20)
        self.assertAlmostEqual(speed, 60.0, delta=3.0)
        self.assertLess(speed, 100)

    def test_a_stalled_stream_shows_nothing(self):
        meter = RateMeter()
        for index in range(30):
            meter.add(5.0, 0.1 * index)
        self.assertIsNone(meter.rate(0.1 * 30 + 4.0))

    def test_every_request_is_measured_alone(self):
        """The bug: tokens of every round of the turn were divided by the time of the last round."""
        meter = RateMeter()
        for index in range(60):
            meter.add(5.0, 100.0 + index * 0.1)
        meter.reset()
        meter.add(5.0, 200.0)
        meter.add(5.0, 200.1)
        self.assertIsNone(meter.rate(200.2))

    def test_russian_text_costs_more_tokens_per_character_than_english(self):
        self.assertGreater(chunk_tokens("привет мир " * 10), chunk_tokens("hello world " * 10))

    def test_the_interface_counts_per_request_and_per_turn(self):
        source = read("app/cli/tui/app.py")
        self.assertIn("self.rate.reset()", source)
        self.assertNotIn("turn_out_chars", source)
        self.assertNotIn("tokens / since", source)

class Noting(Recorder):
    def notice(self, text, level="info"):
        self.events.append(("notice", text))

    def status(self, text):
        self.events.append(("status", text))

class Refuser(Backend):
    """A model that says it cannot take images, the way a real API does — but only when it gets one."""
    name = "refuser"

    def __init__(self):
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        if pictures.request_has_images(request.messages):
            raise LLMError("This model does not support image input", status=400)
        yield StreamEvent("text", text="I read the description.")
        yield StreamEvent("usage", usage=Usage(input=10, output=5, estimated=False, requests=1))
        yield StreamEvent("done", finish="stop")

def image_message(text="what does it say?"):
    return [{"type": "text", "text": text}, {"type": "image", "media_type": "image/png", "data": PNG}]

def pictures_runtime(main, backends, infos):
    runtime, _backend = make_runtime(ui=Noting())
    runtime.model = main
    runtime.router.resolve = lambda model: (backends[model], model)
    runtime.router._models = infos
    (paths.home() / pictures.LEARNED_FILE).unlink(missing_ok=True)
    pictures._cache["stamp"] = None
    return runtime

def info(model_id, vision, provider="custom", **extra):
    return ModelInfo(id=model_id, provider=provider, context=200_000, vision=vision, **extra)

class PicturesForModelsWithoutEyes(unittest.TestCase):
    def test_a_name_says_what_a_provider_flag_does_not(self):
        sees = ["antigravity-claude-opus-4-6-thinking", "kiro-claude-sonnet-4.5", "clodex-gemini-3.8-flash", "gpt-4o-mini",
                "o3", "grok-4", "qwen3-vl-235b", "llama-3.2-90b-vision-instruct", "llama-4-maverick", "gemma-3-27b-it",
                "kimi-k2.5", "glm-4.5v", "pixtral-large"]
        blind = ["deepseek-chat", "deepseek-reasoner", "qwen3-coder-480b", "gpt-oss-120b", "kimi-k2-thinking", "glm-4.6",
                 "llama-3.3-70b-instruct", "gpt-3.5-turbo", "o3-mini", "claude-2.1", "grok-3", "phi-4",
                 "gemini-2.5-flash-image", "gpt-image-1", "text-embedding-3-small"]
        for name in sees:
            self.assertIs(model_info.vision_hint(name), True, name)
        for name in blind:
            self.assertIs(model_info.vision_hint(name), False, name)
        self.assertIsNone(model_info.vision_hint("openrouter-stealth/space-bunny-alpha"))

    def test_the_verdict_comes_from_the_surest_source(self):
        runtime = pictures_runtime("m", {}, [
            info("proxy-gemini-flash", True), info("proxy-deepseek-chat", True), info("proxy-mystery", True),
            info("grok-web", False), info("dall-e", True, image=True),
            info("@or:vendor/model-a", True, vision_source="provider", backend="direct"),
            info("@or:vendor/model-b", False, vision_source="provider", backend="direct"),
            info("@claude:claude-x", True, vision_source="api", backend="direct")])
        verdict = lambda model: tuple(pictures.support(runtime, model))  # noqa: E731
        self.assertEqual(verdict("proxy-gemini-flash"), (True, "name"))
        self.assertEqual(verdict("proxy-deepseek-chat"), (False, "name"), "a text-only model behind a vision provider")
        self.assertEqual(verdict("proxy-mystery"), (None, "unknown"), "unknown: the picture is tried")
        self.assertEqual(verdict("grok-web"), (False, "adapter"))
        self.assertEqual(verdict("dall-e"), (False, "adapter"))
        self.assertEqual(verdict("@or:vendor/model-a"), (True, "list"))
        self.assertEqual(verdict("@or:vendor/model-b"), (False, "list"))
        self.assertEqual(verdict("@claude:claude-x"), (True, "api"))
        pictures.learn("proxy-mystery", False, "refused")
        self.assertEqual(verdict("proxy-mystery"), (False, "learned"), "what a real request taught wins over everything")
        pictures.learn("proxy-gemini-flash", False, "once refused")
        self.assertEqual(verdict("proxy-gemini-flash"), (False, "learned"))

    def test_a_picture_is_described_for_the_task_and_the_main_model_gets_text(self):
        main, seer = Scripted(["The note says: the build failed on line 12."]), Scripted(
            ["Screenshot of a terminal.\nERROR build failed at line 12 (verbatim: `E1204 boom`)."])
        runtime = pictures_runtime("blind-model", {"blind-model": main, "seer-gemini-flash": seer},
                                   [info("blind-model", False), info("seer-gemini-flash", True)])
        result = run(Agent(runtime).run_turn(image_message("why did the build fail?")))
        self.assertEqual(result.stopped, "done")

        asked = seer.requests[0]
        self.assertTrue(pictures.request_has_images(asked.messages))
        self.assertIn("why did the build fail?", json.dumps(asked.messages, ensure_ascii=False))
        self.assertIn("verbatim", asked.system)

        sent = main.requests[0].messages
        self.assertFalse(pictures.request_has_images(sent))
        text = json.dumps(sent, ensure_ascii=False)
        self.assertIn("E1204 boom", text)
        self.assertIn("described_by", text)
        self.assertIn("untrusted", text)
        self.assertIn("You cannot see images", main.requests[0].system)
        self.assertIn("seer-gemini-flash", main.requests[0].system)
        self.assertTrue(any(kind == "notice" and "seer-gemini-flash" in text for kind, text in
                            [event[:2] for event in runtime.ui.events if event[0] == "notice"]))

        self.assertEqual(len(seer.requests), 1)
        main.turns.append("fine")
        run(Agent(runtime).run_turn("and the line?"))
        self.assertEqual(len(seer.requests), 1)
        self.assertIn("E1204 boom", json.dumps(main.requests[-1].messages, ensure_ascii=False))

    def test_the_description_survives_a_resume(self):
        main, seer = Scripted(["ok"]), Scripted(["a green circle"])
        runtime = pictures_runtime("blind-model", {"blind-model": main, "seer-gemini-flash": seer},
                                   [info("blind-model", False), info("seer-gemini-flash", True)])
        run(Agent(runtime).run_turn(image_message()))
        from app.cli.session import Session
        again = Session.load(runtime.root, runtime.session.id)
        self.assertEqual([row["text"] for row in again.image_notes.values()], ["a green circle"])

    def test_a_model_that_sees_gets_the_picture_itself(self):
        main = Scripted(["a blue dot"])
        runtime = pictures_runtime("proxy-gemini-flash", {"proxy-gemini-flash": main}, [info("proxy-gemini-flash", True)])
        run(Agent(runtime).run_turn(image_message()))
        self.assertTrue(pictures.request_has_images(main.requests[0].messages))
        self.assertNotIn("You cannot see images", main.requests[0].system)

    def test_a_refusal_nobody_predicted_is_learned_described_and_retried(self):
        main, seer = Refuser(), Scripted(["a red square"])
        runtime = pictures_runtime("mystery-model", {"mystery-model": main, "seer-gemini-flash": seer},
                                   [info("mystery-model", True), info("seer-gemini-flash", True)])
        self.assertIsNone(pictures.support(runtime, "mystery-model").can)
        result = run(Agent(runtime).run_turn(image_message()))
        self.assertEqual((result.stopped, result.text), ("done", "I read the description."))
        self.assertEqual(len(main.requests), 2, "tried the picture, then the description")
        self.assertTrue(pictures.request_has_images(main.requests[0].messages))
        self.assertFalse(pictures.request_has_images(main.requests[1].messages))
        self.assertEqual(tuple(pictures.support(runtime, "mystery-model")), (False, "learned"))
        self.assertIn("a red square", json.dumps(main.requests[1].messages))

    def test_an_unknown_model_that_takes_the_picture_is_remembered_as_seeing(self):
        main = Scripted(["a cat"])
        runtime = pictures_runtime("mystery-model", {"mystery-model": main}, [info("mystery-model", True)])
        run(Agent(runtime).run_turn(image_message()))
        self.assertEqual(tuple(pictures.support(runtime, "mystery-model")), (True, "learned"))

    def test_no_image_model_means_a_plain_note_and_a_hint_how_to_choose_one(self):
        main = Scripted(["ok"])
        runtime = pictures_runtime("blind-model", {"blind-model": main}, [info("blind-model", False)])
        run(Agent(runtime).run_turn(image_message()))
        self.assertIn("could not be shown or described", json.dumps(main.requests[0].messages))
        self.assertTrue(any("/vision" in event[1] for event in runtime.ui.events if event[0] == "notice"))

    def test_descriptions_can_be_switched_off(self):
        main, seer = Scripted(["ok"]), Scripted(["never asked"])
        runtime = pictures_runtime("blind-model", {"blind-model": main, "seer-gemini-flash": seer},
                                   [info("blind-model", False), info("seer-gemini-flash", True)])
        runtime.settings.set("vision_mode", "off")
        self.addCleanup(runtime.settings.set, "vision_mode", "auto")
        run(Agent(runtime).run_turn(image_message()))
        self.assertEqual(seer.requests, [])
        self.assertIn("could not be shown or described", json.dumps(main.requests[0].messages))

    def test_a_picture_a_tool_returned_is_described_for_the_question_asked(self):
        (Path(tempfile.mkdtemp(prefix="r15-img-")) / "x").parent.mkdir(exist_ok=True)
        main = Scripted(['```memuai-tool\n[{"tool":"Read","args":{"file_path":"shot.png","question":"what colour is the button?"}}]\n```',
                         "The button is green."])
        seer = Scripted(["The Save button is green (#2e7d32)."])
        runtime = pictures_runtime("blind-model", {"blind-model": main, "seer-gemini-flash": seer},
                                   [info("blind-model", False), info("seer-gemini-flash", True)])
        from PIL import Image
        Image.new("RGB", (16, 16), "green").save(runtime.root / "shot.png")
        result = run(Agent(runtime).run_turn("check the screenshot"))
        self.assertEqual(result.text, "The button is green.")
        self.assertIn("what colour is the button?", json.dumps(seer.requests[0].messages))
        self.assertIn("#2e7d32", json.dumps(main.requests[-1].messages))
        self.assertFalse(pictures.request_has_images(main.requests[-1].messages))

    def test_the_choice_is_shared_with_memuai_and_read_back(self):
        runtime = pictures_runtime("m", {}, [info("m", False), info("seer-gemini-flash", True)])
        self.assertEqual(pictures.configured_model(runtime), "")
        self.assertTrue(pictures.save_choice(runtime, "seer-gemini-flash"))
        from app import pgdata
        self.assertEqual(pgdata.load_preferences()["vision_fallback_model"], "seer-gemini-flash")
        self.assertEqual(pictures.configured_model(runtime), "seer-gemini-flash")
        self.assertEqual(pictures.image_model(runtime), "seer-gemini-flash")
        pictures.save_choice(runtime, "")
        self.assertEqual(pgdata.load_preferences()["vision_fallback_model"], "")

    def test_the_probe_sends_a_real_picture_and_checks_what_comes_back(self):
        runtime = pictures_runtime("m", {}, [info("m", True)])
        seen = {}

        def make(read_it):
            async def completion(model, messages):
                parts = messages[-1]["content"]
                seen["images"] = sum(1 for part in parts if part["type"] == "image_url")
                if not read_it:
                    return "I see a landscape."
                return "The code is " + seen["code"]
            return completion

        real = pictures._probe_picture

        def spy(code):
            seen["code"] = code
            return real(code)

        with patch.object(pictures, "_probe_picture", spy), patch.object(runtime, "completion_fn", lambda: make(True)):
            self.assertEqual(run(pictures.probe(runtime, "m")), (True, ""))
        self.assertEqual(seen["images"], 1)
        self.assertEqual(tuple(pictures.support(runtime, "m")), (True, "learned"))
        with patch.object(pictures, "_probe_picture", spy), patch.object(runtime, "completion_fn", lambda: make(False)):
            ok, why = run(pictures.probe(runtime, "m"))
        self.assertFalse(ok)
        self.assertEqual(tuple(pictures.support(runtime, "m")), (False, "learned"))

class BrowserAndComputer(unittest.TestCase):
    def test_a_reply_that_is_not_json_is_asked_again_instead_of_ending_the_run(self):
        calls = {"n": 0}

        async def completion(model, messages):
            calls["n"] += 1
            return "I will click it." if calls["n"] == 1 else '{"action": "done", "report": "ok", "reason": "x"}'

        class Fake:
            logs = []

            def log(self, message, kind="info"):
                self.logs.append(message)

        action = run(automation_runtime._next_action(Fake(), completion, "m", [{"role": "user", "content": "x"}]))
        self.assertEqual((action["action"], calls["n"]), ("done", 2))

        async def never(model, messages):
            return "no json here"
        with self.assertRaises(ValueError):
            run(automation_runtime._next_action(Fake(), never, "m", []))

    def test_a_browser_action_is_described_by_its_element_not_by_pixels(self):
        text = automation_runtime._describe_action
        self.assertEqual(text({"action": "click", "element": 3, "reason": "go"}), 'click element 3 — "go"')
        self.assertIn("into element 1", text({"action": "type", "element": 1, "text": "hello"}))
        self.assertIn("(10, 20)", text({"action": "click", "x": 10, "y": 20, "monitor": 1}))

    def test_computer_use_survives_a_bad_reply_and_a_refused_step_without_touching_the_desktop(self):
        class Standin(automation_runtime.ComputerAutomation):
            """Every operating-system call is replaced: no window, no screenshot, no click."""

            def __init__(self):
                super().__init__()
                self.performed = []

            def start_overlay(self):
                return None

            def _capture(self):
                return {"screens": [{"monitor": 1, "width": 10, "height": 10, "screenshot": "data:image/jpeg;base64,AAAA"}]}

            def _list_windows(self):
                return []

            async def perform(self, action, screen):
                self.performed.append(action)
                if action.get("action") == "type":
                    raise PermissionError("typing that is blocked")
                return "clicked"

        replies = iter(['{"action": "click", "x": 1, "y": 2, "reason": "a"}', "prose, not json",
                        '{"action": "type", "text": "x", "reason": "b"}',
                        '{"action": "done", "report": "finished", "reason": "c"}'])

        async def completion(model, messages):
            return next(replies)

        stand = Standin()
        run(automation_runtime.run_computer_agent(stand, completion, model="m", task="t", ask_each_action=False,
                                                  lang="en"))
        self.assertEqual((stand.status, stand.report), ("done", "finished"))
        self.assertEqual([item["action"] for item in stand.performed], ["click", "type"])
        self.assertTrue(any("FAILED" in row for row in [log["message"] for log in stand.logs]))

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_the_real_hidden_browser_fills_a_form_on_a_local_page(self):
        try:
            automation_runtime._edge_executable()
        except FileNotFoundError:
            self.skipTest("Microsoft Edge is not installed")
        page = (b"<!doctype html><html><head><meta charset=utf-8><title>Check</title></head><body>"
                b"<input id=n aria-label=name><button onclick=\"document.getElementById('o').textContent="
                b"'Hi, '+document.getElementById('n').value+'!'\">Greet</button><p id=o>nobody</p></body></html>")

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)

            def log_message(self, *args):
                return None

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        sessions = Path(tempfile.mkdtemp(prefix="r15-browser-"))
        seen = []

        async def scripted(model, messages):
            user = next(m["content"] for m in messages if isinstance(m["content"], list))
            seen.append(any(part.get("type") == "image_url" for part in user[1:]))
            state = json.loads(user[0]["text"].split("<page_state>")[1].split("</page_state>")[0])
            step = len(json.loads(user[0]["text"].split("<recent_actions>")[1].split("</recent_actions>")[0]))
            if step == 0:
                return json.dumps({"action": "type", "element": next(e["id"] for e in state["elements"] if e["tag"] == "input"),
                                   "text": "Мемю", "reason": "name"})
            if step == 1:
                return json.dumps({"action": "click", "element": next(e["id"] for e in state["elements"] if e["tag"] == "button"),
                                   "reason": "greet"})
            return json.dumps({"action": "done", "report": state["text"], "reason": "done"})

        edge = automation_runtime.EdgeAutomation()

        async def exercise():
            with patch.object(automation_runtime, "DATA_DIR", str(sessions)):
                try:
                    await automation_runtime.run_browser_agent(edge, scripted, model="m", task="greet",
                                                               initial_url=f"http://127.0.0.1:{server.server_address[1]}/", max_steps=5)
                    self.assertEqual((edge.status, edge.error), ("done", ""))
                    self.assertIn("Hi, Мемю!", edge.report)
                    self.assertTrue(all(seen), "every step got a screenshot")
                finally:
                    await edge.close_browser()

        try:
            run(exercise())
        finally:
            server.shutdown()
            server.server_close()
        leftovers = list((sessions / "memuai" / "browser-sessions").glob("session-*")) if (sessions / "memuai").exists() else []
        self.assertEqual(leftovers, [], "the private profile is removed")

    def test_the_browser_refuses_private_addresses_files_and_credentials(self):
        for bad in ("http://192.168.0.1/", "file:///C:/Windows/win.ini", "http://user:pw@example.com/"):
            with self.assertRaises(ValueError):
                automation_runtime._safe_navigation_url(bad)

    def test_browser_and_computer_fall_back_to_an_image_model_when_the_chat_model_is_blind(self):
        from app.cli.tools.automation import _vision_model
        runtime = pictures_runtime("blind-model", {}, [info("blind-model", False), info("seer-gemini-flash", True)])
        self.assertEqual(_vision_model(runtime, "browser_model"), "seer-gemini-flash")
        runtime.settings.set("browser_model", "my-own")
        self.assertEqual(_vision_model(runtime, "browser_model"), "my-own")

class CollapsedByDefault(unittest.TestCase):
    def test_thoughts_and_tools_start_closed_and_the_users_choice_survives_a_redraw(self):
        js = read("app/pgstatic/app.js")
        reasoning = js[js.index("cls: \"step-reasoning\""):][:400]
        self.assertIn("open: false", reasoning)
        self.assertNotIn("open: thinkingNow", js)
        tools = js[js.index("title: en ? \"Tools\" : \"Инструменты\""):][:700]
        self.assertIn("open: false", tools)
        self.assertNotIn("open: running,\n      body: notes", js)
        self.assertIn('stepKey(i, "reasoning")', js)
        self.assertIn('stepKey(i, "tools")', js)
        self.assertIn("stepUserOpen", js)
        self.assertIn("data-step-key", js)

class StatisticsLayout(unittest.TestCase):
    def test_a_value_wider_than_its_old_box_pushes_the_name_not_the_neighbour(self):
        css = read("app/static/style.css")
        self.assertIn(".usage-row .u-val { flex: 0 0 auto; min-width: 110px;", css)
        self.assertNotIn(".usage-row .u-val { width: 110px;", css)
        self.assertIn(".usage-row .u-name { flex: 0 1 38%; min-width: 0;", css)
        self.assertIn("grid-template-columns: minmax(0, 1fr) minmax(0, 1fr)", css)
        self.assertIn(".grid-2 > *, .grid-3 > * { min-width: 0; }", css)
        self.assertIn("#metrics-errors .row-line .mono { min-width: 0; overflow-wrap: anywhere; }", css)

class OneVersion(unittest.TestCase):
    def test_every_copy_of_the_version_says_the_release_number(self):
        self.assertRegex(version.APP_VERSION, r"^\d+\.\d+(?:\.\d+){0,2}$")
        self.assertEqual(version.windows_version(), ".".join(map(str, version.version_tuple())))
        from app import cli
        self.assertEqual(cli.VERSION, version.APP_VERSION, "MemuCode CLI has no version of its own")
        for name in ("memuai-version.txt", "memuai-proxy-version.txt", "memucode-version.txt", "memunote-version.txt", "memufiles-version.txt", "memu-version.txt"):
            text = read(name)
            self.assertIn(f"filevers={version.version_tuple()}", text, name)
            self.assertIn(f"'FileVersion', '{version.windows_version()}'", text, name)
            self.assertIn(f"'ProductVersion', '{version.windows_version()}'", text, name)

        build_script = read("build.py")
        for product in ("memuai", "proxy"):
            self.assertIn(f'build_setup.build_setup(sys.modules[__name__], "{product}", version', build_script)
        self.assertIn("version = app_version()", build_script)
        setup = read("installer/build_setup.py")
        self.assertIn('"version": version', setup)
        self.assertIn("build.write_version_file(target, version)", setup)
        self.assertIn(f'id="brand-ver">{version.APP_VERSION}<', read("installer/ui/index.html"))

    def test_nothing_that_is_shown_to_the_user_carries_the_old_number(self):
        for name in ("app/version.py", "app/cli/__init__.py", "installer/products.py", "installer/build_setup.py",
                     "installer/ui/index.html", "installer/ui/i18n.js", "app/pgstatic/app.js",
                     "memuai-version.txt", "memuai-proxy-version.txt", "memucode-version.txt", "build.py"):
            self.assertNotIn("1.200", read(name), name)
        self.assertNotIn("Версия 1.0", read("app/pgstatic/app.js"))
        self.assertNotIn('"version": "1.0"', read("app/mcp_client.py"))
        self.assertNotIn('"version": "1.0"', read("app/cli/tools/mcp.py"))

    def test_the_build_gives_every_executable_the_bundle_version(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("memuai_build_script", ROOT / "build.py")
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)
        self.assertEqual(build.app_version(), version.APP_VERSION)
        self.assertEqual({build.target_version(key, version.APP_VERSION) for key in build.TARGETS}, {version.APP_VERSION})

    def test_the_banner_and_the_version_command_read_the_same_number(self):
        from app.cli import VERSION
        self.assertEqual(VERSION, version.APP_VERSION)

class InstallerOptionsPage(unittest.TestCase):
    """Round 15 made the five option rows of the Inno page share its height. The
    custom installer (Round 20) builds the rows from the product's tasks; every
    task must get a row with a caption in both languages, and the rows must sit
    in a grid that cannot run past the panel."""

    def test_five_rows_share_the_height_of_the_page(self):
        from installer import products
        app_js = read("installer/ui/app.js")
        i18n = read("installer/ui/i18n.js")
        css = read("installer/ui/style.css")
        self.assertIn('var host = $("#toggles")', app_js)
        self.assertIn('id="toggles"', read("installer/ui/index.html"))
        for product in products.PRODUCTS.values():
            for task in product.tasks:
                self.assertEqual(i18n.count(f'"task.{task.id}":'), 2, task.id)
        self.assertEqual(i18n.count('"edition.cli.t":'), 2, "the fifth option (MemuCode CLI) lives on the edition card")
        rule = re.search(r"\.toggles\s*\{([^}]*)\}", css)
        self.assertIsNotNone(rule)
        self.assertIn("grid-template-columns", rule.group(1), "two columns: four tasks never overflow the panel")

BING = ('<ol id="b_results"><li class="b_algo" data-id iid=SERP.1><div class="b_tpcn"><a class="tilk" href="https://www.bing.com/ck/a?!&amp;'
        'u=a1aHR0cHM6Ly9leGFtcGxlLmNvbS93ZWF0aGVy&amp;ntb=1"><cite>example.com</cite></a></div>'
        '<h2 class=""><a target="_blank" href="https://www.bing.com/ck/a?!&amp;&amp;p=1&amp;u=a1aHR0cHM6Ly9leGFtcGxlLmNvbS93ZWF0aGVy&amp;ntb=1">'
        '<strong>Погода</strong> в Москве</a></h2><div class="b_caption"><p class="b_lineclamp2">Сегодня облачно, +11 °C …</p></div></li>'
        '<li class="b_algo"><h2><a href="https://other.example/page">Other <strong>page</strong></a></h2>'
        '<div class="b_caption"><p>Second snippet</p></div></li></ol>')

class FakeWeb:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    async def _call(self, method, url, **kwargs):
        self.calls.append((method, url))
        for prefix, reply in self.routes.items():
            if url.startswith(prefix):
                return reply() if callable(reply) else reply
        raise RuntimeError("no route " + url)

    async def get(self, url, **kwargs):
        return await self._call("GET", url, **kwargs)

    async def post(self, url, **kwargs):
        return await self._call("POST", url, **kwargs)

class Reply:
    def __init__(self, body, status=200):
        self.status_code = status
        self.text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
        self.headers = {}

    def json(self):
        return json.loads(self.text)

class WebSearchStack(unittest.TestCase):
    def setUp(self):
        websearch._CACHE.clear()
        websearch._cold.clear()

    def run_with(self, web, coro_factory):
        async def session(_proxy=""):
            return web

        async def go():
            with patch("app.providers.http.get_session", session):
                return await coro_factory()
        return asyncio.run(go())

    def test_bing_is_read_with_its_redirect_links_unwrapped(self):
        web = FakeWeb({"https://www.bing.com": Reply(BING)})
        rows = self.run_with(web, lambda: websearch.run_engine("bing", "погода в Москве", 5))
        self.assertEqual(rows[0], {"title": "Погода в Москве", "url": "https://example.com/weather",
                                   "snippet": "Сегодня облачно, +11 °C …"})
        self.assertEqual(rows[1]["url"], "https://other.example/page")
        self.assertIn("bing", search_engines.ENGINES)
        self.assertEqual(search_engines.FREE_RACE, ("bing", "duckduckgo"))

    def test_free_sources_run_together_and_the_fastest_answer_wins(self):
        order = []

        async def slow(query, limit, proxy, snippet_chars=500):
            order.append("slow-start")
            await asyncio.sleep(30)
            return [{"title": "slow", "url": "https://slow.example", "snippet": ""}]

        async def quick(query, limit, proxy, snippet_chars=500):
            order.append("quick-start")
            await asyncio.sleep(0.05)
            return [{"title": "quick", "url": "https://quick.example", "snippet": ""}]

        async def go():
            with patch.dict(websearch._FREE, {"bing": quick, "duckduckgo": slow}):
                return await asyncio.wait_for(websearch.free_search("q"), 5)
        rows = asyncio.run(go())
        self.assertEqual(rows[0]["url"], "https://quick.example")
        self.assertEqual(sorted(order), ["quick-start", "slow-start"], "both were asked at once")

    def test_a_blocked_source_is_skipped_for_a_while_and_a_hung_one_does_not_hold_the_answer(self):
        async def blocked(query, limit, proxy, snippet_chars=500):
            raise RuntimeError("DuckDuckGo asks to confirm you are human")

        async def empty(query, limit, proxy, snippet_chars=500):
            return []

        async def yahoo(query, limit, proxy, snippet_chars=500):
            return [{"title": "y", "url": "https://yahoo.example", "snippet": ""}]

        async def go():
            with patch.dict(websearch._FREE, {"bing": empty, "duckduckgo": blocked, "yahoo": yahoo}):
                return await websearch.free_search("q")
        self.assertEqual(asyncio.run(go())[0]["url"], "https://yahoo.example", "the slow last resort answers when both are empty")
        self.assertIn("duckduckgo", websearch._cold)

    def test_auto_prefers_kiro_only_when_it_is_about_as_quick(self):
        async def free(query, limit, proxy, snippet_chars=500):
            return [{"title": "free", "url": "https://free.example", "snippet": ""}]

        async def kiro_fast(query, limit=5):
            await asyncio.sleep(0.1)
            return [{"title": "kiro", "url": "https://kiro.example", "snippet": ""}]

        async def kiro_slow(query, limit=5):
            await asyncio.sleep(30)
            return [{"title": "kiro", "url": "https://kiro.example", "snippet": ""}]

        async def kiro_none(query, limit=5):
            return None

        async def pick(kiro):
            with patch.object(websearch, "free_search", free), patch.object(websearch, "kiro_search", kiro):
                return (await asyncio.wait_for(websearch.run_engine("auto", "q"), 5))[0]["title"]
        self.assertEqual(asyncio.run(pick(kiro_fast)), "free" if websearch._KIRO_GRACE < 0.1 else "kiro")
        self.assertEqual(asyncio.run(pick(kiro_slow)), "free")
        self.assertEqual(asyncio.run(pick(kiro_none)), "free")

    def test_excerpts_keep_the_passages_that_answer_the_question(self):
        filler = "\n".join(f"Раздел {n}. Общие слова о проекте, ничего конкретного, просто текст для объёма." for n in range(60))
        text = filler + "\nКурс доллара на сегодня составляет 83,4 рубля по данным биржи.\n" + filler
        excerpt = websearch.relevant_excerpt(text, "курс доллара сегодня", 600)
        self.assertIn("83,4", excerpt)
        self.assertLessEqual(len(excerpt), 600)
        self.assertEqual(websearch.relevant_excerpt("short", "x"), "short")

    def test_one_call_returns_the_results_a_ready_answer_and_page_excerpts(self):
        async def ask(text, count):
            return [{"title": f"{text} 1", "url": "https://a.example/1", "snippet": "s"},
                    {"title": f"{text} 2", "url": "https://b.example/2", "snippet": "s"},
                    {"title": "video", "url": "https://youtube.com/watch?v=1", "snippet": "s"}]

        async def page(url, **kwargs):
            return {"url": url, "title": "T " + url, "text": "Сегодня в городе тепло, " + "слово " * 40 + "курс доллара 83,4", "truncated": False}

        async def go():
            with patch.object(websearch, "fetch_page", page), patch.object(
                    websearch.instant_answers, "answer", AsyncMock(return_value=None)):
                return await websearch.rich_search("курс", queries=["rate", "курс"], limit=5, read=2, search_fn=ask)
        data = asyncio.run(go())
        self.assertEqual(data["queries"], ["курс", "rate"], "the same phrasing twice is one query")
        self.assertEqual({row["url"] for row in data["results"]}, {"https://a.example/1", "https://b.example/2",
                                                                    "https://youtube.com/watch?v=1"})
        self.assertEqual(len(data["pages"]), 2)
        self.assertNotIn("youtube", json.dumps(data["pages"]), "video pages are not read")
        self.assertIn("83,4", data["pages"][0]["excerpt"])

    def test_a_ready_answer_replaces_reading_pages_and_a_failed_search_is_still_an_error(self):
        async def boom(text, count):
            raise RuntimeError("search is down")

        answer = {"kind": "weather", "text": "Weather — X: +10", "note": "", "source": "Open-Meteo", "url": "u"}

        async def go(quick):
            with patch.object(websearch.instant_answers, "answer", AsyncMock(return_value=quick)):
                return await websearch.rich_search("weather", search_fn=boom)
        data = asyncio.run(go(answer))
        self.assertEqual((data["answer"]["kind"], data["results"], data["pages"]), ("weather", [], []))
        with self.assertRaisesRegex(RuntimeError, "search is down"):
            asyncio.run(go(None))

    def test_only_strong_signs_start_a_search_before_the_model_answers(self):
        yes = ["Какая сегодня погода?", "найди в интернете последние новости про Rust", "сколько стоит RTX 5090",
               "курс доллара к рублю", "кто выиграл вчера матч Спартак Зенит", "weather in Paris", "what's the latest news on Rust"]
        no = ["привет", "переведи на английский: погода хорошая", "что такое рекурсия", "fix my code, the weather widget crashes",
              "напиши функцию, которая получает погоду", "```python\nprint('weather')\n```", "x" * 500 + " погода"]
        for text in yes:
            self.assertTrue(websearch.prefetch_query(text), text)
        for text in no:
            self.assertEqual(websearch.prefetch_query(text), "", text)
        self.assertEqual(websearch.prefetch_query("найди в интернете последние новости про Rust"), "последние новости про Rust")

class InstantAnswers(unittest.TestCase):
    def test_the_intent_and_the_place_come_from_the_words(self):
        cases = {"Какая сегодня погода?": ("weather", ""), "погода в Москве": ("weather", "Москве"),
                 "weather in New York today": ("weather", "New York"), "прогноз погоды в Ростове-на-Дону": ("weather", "Ростове-на-Дону"),
                 "будет ли завтра дождь в Лондоне": ("weather", "Лондоне"), "курс доллара": ("currency", ""),
                 "100 usd в rub": ("currency", None), "цена биткоина": ("crypto", ""), "сколько времени в Токио": ("time", "Токио"),
                 "what time is it in London": ("time", None), "как работает температура в GPT": ("", None),
                 "расскажи про погоду как явление": ("", None), "fix my python function": ("", None)}
        for query, (kind, place) in cases.items():
            self.assertEqual(instant_answers.intent(query), kind, query)
            if place is not None:
                self.assertEqual(instant_answers.place_from_query(query), place, query)

    def test_a_russian_place_in_the_prepositional_case_is_tried_in_the_nominative(self):
        names = lambda word: instant_answers._nominative_candidates(word)  # noqa: E731
        self.assertIn("Казань", names("Казани"))
        self.assertIn("Москв", names("Москве"))
        self.assertIn("Лондон", names("Лондоне"))
        self.assertIn("Ростов-на-Дону", names("Ростове-на-Дону"))
        self.assertIn("Нью-Йорк", names("Нью-Йорке"))

    def test_the_weather_is_composed_from_the_forecast_and_names_an_approximate_place(self):
        forecast = {"current": {"time": "2026-10-02T18:00", "temperature_2m": 11.4, "apparent_temperature": 9.1,
                                "relative_humidity_2m": 69, "precipitation": 0.0, "weather_code": 3, "wind_speed_10m": 1.6},
                    "daily": {"time": ["a", "b", "c"], "weather_code": [61, 3, 3], "temperature_2m_min": [4, 6, 4],
                              "temperature_2m_max": [12, 10, 11], "precipitation_probability_max": [13, 3, 3],
                              "precipitation_sum": [0.3, 0, 0]}}

        async def get_json(url, params, proxy=""):
            if "geocoding" in url:
                if params["name"] == "Казань":
                    return {"results": [{"name": "Казань", "country": "Россия", "latitude": 55.8, "longitude": 49.1, "population": 1200000}]}
                return {}
            return forecast

        async def place_from_ip(proxy="", lang="en"):
            return {"name": "Рыбинск", "country": "Россия", "lat": 58.0, "lon": 38.8, "approximate": True}

        async def go(query):
            with patch.object(instant_answers, "_get_json", get_json), patch.object(instant_answers, "place_from_ip", place_from_ip):
                return await instant_answers.answer(query, lang="ru")
        named = asyncio.run(go("погода в Казани"))
        self.assertIn("Казань, Россия", named["text"])
        self.assertIn("+11 °C", named["text"])
        self.assertIn("пасмурно", named["text"])
        self.assertFalse(named["approximate"])
        guessed = asyncio.run(go("Какая сегодня погода?"))
        self.assertTrue(guessed["approximate"])
        self.assertIn("по IP", guessed["text"])
        self.assertIn("назовите", guessed["note"])

    def test_a_currency_rate_is_converted_and_a_dead_source_is_simply_no_answer(self):
        async def get_json(url, params, proxy=""):
            return {"rates": {"RUB": 83.4361, "EUR": 0.88}, "time_last_update_utc": "Fri, 02 Oct 2026 00:00:01 +0000"}

        async def go(query, source):
            with patch.object(instant_answers, "_get_json", source):
                return await instant_answers.answer(query, lang="ru")
        answer = asyncio.run(go("100 usd в rub", get_json))
        self.assertIn("100 USD = 8 343.61 RUB", answer["text"])
        self.assertIn("1 USD = 83.4361 RUB", asyncio.run(go("курс доллара", get_json))["text"])

        async def dead(url, params, proxy=""):
            raise RuntimeError("offline")
        self.assertIsNone(asyncio.run(go("курс доллара", dead)))

    def test_the_search_routes_serve_a_prefetch_that_says_no_to_a_greeting(self):
        from fastapi.testclient import TestClient

        from app import pgdata
        from app.playground_api import create_app
        canned = {"query": "погода", "queries": ["погода"], "results": [{"title": "t", "url": "https://x.example", "snippet": "s"}],
                  "count": 1, "answer": {"kind": "weather", "text": "Weather — X", "note": "", "source": "Open-Meteo", "url": "u"},
                  "pages": [], "took_ms": 700}
        with tempfile.TemporaryDirectory() as folder, patch.object(pgdata, "MEMU_DIR", Path(folder)), \
                patch.dict(os.environ, {"MEMUAI_PROXY_URL": "http://127.0.0.1:9"}), \
                patch.object(websearch, "rich_search", AsyncMock(return_value=canned)) as rich:
            client = TestClient(create_app(), base_url="http://127.0.0.1")
            hello = client.post("/api/pg/websearch/prefetch", json={"text": "привет"}).json()
            self.assertEqual(hello, {"used": False})
            rich.assert_not_called()
            weather = client.post("/api/pg/websearch/prefetch", json={"text": "Какая сегодня погода?", "lang": "ru"}).json()
            self.assertTrue(weather["used"])
            self.assertEqual(weather["answer"]["kind"], "weather")
            self.assertIn("Untrusted", weather["note"])
            off = client.post("/api/pg/websearch/prefetch", json={"text": "Какая сегодня погода?", "allow_web": False}).json()
            self.assertEqual(off, {"used": False})

    def test_the_window_searches_before_the_first_round_and_counts_the_whole_turn(self):
        js = read("app/pgstatic/app.js")
        self.assertIn("/api/pg/websearch/prefetch", js)
        self.assertIn("<web_prefetch", js)
        self.assertIn("function turnElapsed(", js)
        self.assertIn("turnElapsed(assistant, t0, !turnContinues)", js)
        self.assertNotIn("assistant.ms = Math.round(performance.now() - t0);\n    generationJobs.delete(sourceChatId);\n"
                         "    generating = generationJobs.size > 0 || turnContinues;", js)
        self.assertIn("web_prefetch", js[js.index("const WEB_TOOLS_PROMPT"):js.index("</web_tools>`;")])

if __name__ == "__main__":  # pragma: no cover
    unittest.main()

class InTheTerminal(unittest.TestCase):
    """The real interface on a pipe: the new command, and the speed the status line shows."""

    def runtime(self):
        main, seer = Scripted(["ok"]), Scripted(["a green circle"])
        runtime = pictures_runtime("blind-model", {"blind-model": main, "seer-gemini-flash": seer},
                                   [info("blind-model", False), info("seer-gemini-flash", True)])

        async def fetch(refresh=False):
            return runtime.router._models
        runtime.router.models = fetch
        return runtime

    def test_vision_status_is_typed_at_the_prompt_and_says_what_happens_to_a_picture(self):
        from tests.memucode_transcript_test import texts
        from tests.memucode_ui_test import drive_ui, until
        runtime = self.runtime()

        async def drive(ui, keys):
            keys.send_text("/vision status")
            await until(lambda: bool(ui.menu and ui.menu.items), 3)
            keys.send_text("\x1b")
            await until(lambda: ui.menu is None, 2)
            keys.send_text("\r")
            await until(lambda: "cannot see images" in "\n".join(texts(ui.transcript.viewport(40, 110))), 3)
            return "\n".join(texts(ui.transcript.viewport(40, 110)))

        _ui, shown = drive_ui(runtime, drive)
        self.assertIn("blind-model cannot see images", shown)
        self.assertIn("seer-gemini-flash describes", shown)
        self.assertIn("No image model chosen", shown)

    def test_the_status_line_shows_a_real_speed_and_stays_silent_for_a_dump(self):
        from app.cli.tui import app as app_module
        from tests.memucode_ui_test import drive_ui
        runtime = self.runtime()

        async def drive(ui, keys):
            ui.busy, ui.busy_started, ui.first_token_at, ui.stream_started_at = True, 0.0, 1.0, 0.5
            ui.turn_out_tokens = 400.0

            ui.rate.reset()
            for step in range(40):
                ui.rate.add(5.0, 10.0 + step * 0.1)
            with patch.object(app_module.time, "monotonic", return_value=14.0):
                streaming = "".join(part[1] for part in ui._spinner_line(100))

            ui.rate.reset()
            ui.rate.add(800.0, 20.0)
            with patch.object(app_module.time, "monotonic", return_value=21.0):
                dumped = "".join(part[1] for part in ui._spinner_line(100))
            return streaming, dumped

        _ui, (streaming, dumped) = drive_ui(runtime, drive)
        match = re.search(r"(\d+) tok/s", streaming)
        self.assertIsNotNone(match, streaming)
        self.assertAlmostEqual(int(match.group(1)), 50, delta=3)
        self.assertNotIn("tok/s", dumped)
        self.assertIn("↓ 400 tokens", dumped, "the count stays; only the speed is withheld")

class SessionPoolAndLoops(unittest.TestCase):
    """curl_cffi calls back into the event loop a session was made on. A session that outlived its loop used to raise
    'Event loop is closed' inside a cffi callback — and on Windows cffi answers that with a 'Python-CFFI error' window
    on the desktop for as long as the process lives (it appeared during test runs)."""

    def test_every_loop_gets_its_own_sessions_and_a_closed_loop_leaves_the_pool(self):
        from app.providers import http

        async def grab():
            first = await http.get_session("")
            _ = first.loop
            again = await http.get_session("")
            return first, again

        a1, a2 = asyncio.run(grab())
        self.assertIs(a1, a2, "inside one loop the session is shared, as before")
        b1, _b2 = asyncio.run(grab())
        self.assertIsNot(a1, b1, "another loop never receives a session that belongs to a closed one")

        async def count():
            await http.get_session("")
            return len(http._sessions)
        self.assertLessEqual(asyncio.run(count()), 2, "sessions of loops that are gone are forgotten, not piled up")

        async def drop():
            await http.get_session("")
            return await http.drop_session("")
        self.assertEqual(asyncio.run(drop()), 1, "dropping touches only the running loop's session")

    def test_the_callbacks_that_used_to_raise_are_replaced_by_safe_ones(self):
        from curl_cffi import aio

        from app.providers import http  # noqa: F401  (the import installs them)
        self.assertTrue(getattr(aio, "_memuai_safe_callbacks", False))

    def test_a_session_that_outlives_its_loop_makes_no_callback_error(self):
        """Run in a child process that leaves at once: if the error came back, cffi's window (shown after 666 ms) never
        gets the chance to appear on a person's desktop."""
        import subprocess
        import sys
        code = (
            "import asyncio, http.server, os, sys, threading\n"
            f"sys.path.insert(0, {str(ROOT)!r})\n"
            "import app.providers.http\n"
            "from curl_cffi.requests import AsyncSession\n"
            "class H(http.server.BaseHTTPRequestHandler):\n"
            "    def do_GET(self):\n"
            "        self.send_response(200); self.send_header('Content-Length','2'); self.end_headers(); self.wfile.write(b'ok')\n"
            "    def log_message(self, *a): pass\n"
            "srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), H)\n"
            "threading.Thread(target=srv.serve_forever, daemon=True).start()\n"
            "url = 'http://127.0.0.1:%d/' % srv.server_address[1]\n"
            "s = AsyncSession()\n"
            "async def one():\n"
            "    return (await s.get(url, timeout=5)).status_code\n"
            "async def two():\n"
            "    try:\n"
            "        await asyncio.wait_for(s.get(url, timeout=5), 0.3)\n"
            "    except Exception:\n"
            "        pass\n"
            "asyncio.run(one()); asyncio.run(two())\n"
            "sys.stderr.flush(); os._exit(0)\n")
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr[-500:])
        self.assertNotIn("Event loop is closed", done.stderr)
        self.assertNotIn("Exception ignored from cffi callback", done.stderr)
