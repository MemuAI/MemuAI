"""Tool calls survive the Custom API and both CLI-facing protocol adapters."""
from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from app.anthropic_api import messages as anthropic_messages
from app.anthropic_api import _to_internal_messages as anthropic_internal_messages
from app.chat import ChatService
from app.openai_api import chat_completions
from app.providers.custom import CustomProvider
from app.responses_api import responses
from app.responses_api import _messages as responses_messages
from app.state import Account, state
from tests.protocol_contract_test import make_request

def tool_chunks(*_args, **_kwargs):
    async def generate():
        yield {"id": "chat-1", "choices": [{"delta": {"tool_calls": [{
            "index": 0, "id": "call_1", "type": "function",
            "function": {"name": "run_command", "arguments": '{"command":'}}]},
            "finish_reason": None}]}
        yield {"id": "chat-1", "choices": [{"delta": {"tool_calls": [{
            "index": 0, "function": {"arguments": '"pwd"}'}}]},
            "finish_reason": None}]}
        yield {"id": "chat-1", "choices": [{"delta": {}, "finish_reason": "tool_calls"}],
               "usage": {"prompt_tokens": 4, "completion_tokens": 7, "total_tokens": 11}}
    return generate()

class ToolProtocolTests(unittest.TestCase):
    def test_codex_image_data_url_reaches_custom_chat_message(self):
        messages = responses_messages({"input": [{"role": "user", "content": [
            {"type": "input_text", "text": "inspect this"},
            {"type": "input_image", "image_url": "data:image/png;base64,iVBORw0KGgo=", "detail": "high"},
        ]}]})
        self.assertEqual(messages[0]["content"][1], {"type": "image_url", "image_url": {
            "url": "data:image/png;base64,iVBORw0KGgo=", "detail": "high"}})
        with self.assertRaisesRegex(ValueError, "file_id is unsupported"):
            responses_messages({"input": [{"role": "user", "content": [
                {"type": "input_image", "file_id": "file_123"}]}]})

    def test_claude_base64_image_reaches_custom_chat_message(self):
        body = {"messages": [{"role": "user", "content": [
            {"type": "text", "text": "inspect this"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                             "data": "iVBORw0KGgo="}},
        ]}]}
        messages = anthropic_internal_messages(body)
        self.assertEqual(messages[0]["content"][1], {"type": "image_url", "image_url": {
            "url": "data:image/png;base64,iVBORw0KGgo="}})

        async def run():
            bad = {"model": "custom-model", "max_tokens": 32,
                   "messages": [{"role": "user", "content": [{"type": "image",
                                 "source": {"type": "file", "file_id": "file_123"}}]}]}
            with patch("app.anthropic_api._check_auth", return_value=None):
                return await anthropic_messages(make_request(bad))

        response = asyncio.run(run())
        self.assertEqual(response.status_code, 400)
        self.assertIn("Unsupported Anthropic image source", response.body.decode())

    def test_codex_parallel_prior_calls_share_one_assistant_message(self):
        messages = responses_messages({"input": [
            {"type": "function_call", "call_id": "a", "name": "one", "arguments": "{}"},
            {"type": "custom_tool_call", "call_id": "b", "name": "apply_patch", "input": "patch"},
            {"type": "function_call_output", "call_id": "a", "output": "ok"},
            {"type": "custom_tool_call_output", "call_id": "b", "output": "done"},
        ]})
        self.assertEqual([message["role"] for message in messages], ["assistant", "tool", "tool"])
        self.assertEqual([call["id"] for call in messages[0]["tool_calls"]], ["a", "b"])

    def test_custom_provider_forwards_schema_and_streamed_call(self):
        posted = {}

        class Response:
            status_code = 200
            headers = {"content-type": "text/event-stream"}

            async def aclose(self):
                pass

        class Session:
            async def post(self, _url, **kwargs):
                posted.update(kwargs)
                return Response()

        async def lines(_response, **_kwargs):
            yield 'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"run_command","arguments":"{\\"command\\":"}}]}}]}'
            yield 'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"pwd\\"}"}}]},"finish_reason":"tool_calls"}]}'

        async def run():
            account = Account("custom", {"name": "local", "base_url": "https://api.test/v1"})
            with patch("app.providers.http.get_session", return_value=Session()), \
                    patch("app.providers.http.aiter_lines_str", new=lines):
                return [event async for event in CustomProvider().chat_stream(
                    account, "model", [{"role": "user", "content": "run pwd"}],
                    {"tools": [{"type": "function", "function": {"name": "run_command"}}],
                     "tool_choice": "required"})]

        events = asyncio.run(run())
        self.assertEqual(posted["json"]["tools"][0]["function"]["name"], "run_command")
        self.assertEqual(posted["json"]["tool_choice"], "required")
        self.assertEqual("".join(str(event.get("arguments") or "") for event in events),
                         '{"command":"pwd"}')
        self.assertEqual(events[-1], {"type": "finish", "reason": "tool_calls"})

    def test_openai_nonstream_reassembles_function_call(self):
        body = {"model": "custom-model", "messages": [{"role": "user", "content": "run pwd"}],
                "tools": [{"type": "function", "function": {"name": "run_command",
                           "parameters": {"type": "object"}}}]}

        async def run():
            with patch("app.openai_api._check_auth", return_value=None), \
                    patch("app.openai_api.tool_provider_supported", return_value=True), \
                    patch("app.openai_api.chat_service.stream_completion", new=tool_chunks), \
                    patch.object(state, "commit_trace"):
                return await chat_completions(make_request(body))

        result = asyncio.run(run())
        choice = result["choices"][0]
        self.assertEqual(choice["finish_reason"], "tool_calls")
        self.assertEqual(choice["message"]["tool_calls"][0]["function"],
                         {"name": "run_command", "arguments": '{"command":"pwd"}'})

    def test_non_custom_models_fail_before_silently_losing_tools(self):
        async def run():
            with patch("app.openai_api._check_auth", return_value=None), \
                    patch("app.openai_api.tool_provider_supported", return_value=False):
                return await chat_completions(make_request({
                    "model": "kiro-model", "messages": [{"role": "user", "content": "edit"}],
                    "tools": [{"type": "function", "function": {"name": "edit"}}]}))
        response = asyncio.run(run())
        self.assertEqual(response.status_code, 400)
        self.assertIn("Custom API", response.body.decode())

    def test_anthropic_converts_tool_use_and_result_round_trip(self):
        body = {"model": "custom-model", "max_tokens": 64,
                "messages": [{"role": "assistant", "content": [{"type": "tool_use", "id": "old-call",
                              "name": "run_command", "input": {"command": "ls"}}]},
                             {"role": "user", "content": [{"type": "tool_result",
                              "tool_use_id": "old-call", "content": "ok"}]}],
                "tools": [{"name": "run_command", "description": "Run a command",
                           "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}}}],
                "tool_choice": {"type": "tool", "name": "run_command"}}
        captured = {}

        async def fake(_model, messages, params, **_kwargs):
            captured["messages"] = messages
            captured["params"] = params
            async for item in tool_chunks():
                yield item

        async def run():
            with patch("app.anthropic_api._check_auth", return_value=None), \
                    patch("app.anthropic_api.tool_provider_supported", return_value=True), \
                    patch("app.anthropic_api.chat_service.stream_completion", new=fake), \
                    patch.object(state, "commit_trace"):
                return await anthropic_messages(make_request(body))

        result = asyncio.run(run())
        self.assertEqual(result["stop_reason"], "tool_use")
        self.assertEqual(result["content"][0]["input"], {"command": "pwd"})
        self.assertEqual(captured["messages"][-1],
                         {"role": "tool", "tool_call_id": "old-call", "content": "ok"})
        self.assertEqual(captured["params"]["tool_choice"],
                         {"type": "function", "function": {"name": "run_command"}})

    def test_anthropic_stream_emits_tool_block_and_tool_use_stop(self):
        body = {"model": "custom-model", "max_tokens": 64, "stream": True,
                "messages": [{"role": "user", "content": "run pwd"}],
                "tools": [{"name": "run_command", "input_schema": {"type": "object"}}]}

        async def run():
            with patch("app.anthropic_api._check_auth", return_value=None), \
                    patch("app.anthropic_api.tool_provider_supported", return_value=True), \
                    patch("app.anthropic_api.chat_service.stream_completion", new=tool_chunks), \
                    patch.object(state, "commit_trace"):
                response = await anthropic_messages(make_request(body))
                return b"".join([chunk.encode() if isinstance(chunk, str) else chunk
                                 async for chunk in response.body_iterator]).decode()

        data = asyncio.run(run())
        self.assertIn('"type": "tool_use"', data)
        deltas = [json.loads(line[6:]) for line in data.splitlines() if line.startswith("data: ")]
        arguments = next(item["delta"]["partial_json"] for item in deltas
                         if item["type"] == "content_block_delta")
        self.assertEqual(json.loads(arguments), {"command": "pwd"})
        self.assertIn('"stop_reason": "tool_use"', data)

    def test_codex_responses_custom_tool_stream_and_prior_output(self):
        body = {"model": "custom-model", "stream": True,
                "input": [{"type": "custom_tool_call", "call_id": "prior", "name": "apply_patch",
                           "input": "*** Begin Patch"},
                          {"type": "custom_tool_call_output", "call_id": "prior", "output": "ok"}],
                "tools": [{"type": "custom", "name": "apply_patch",
                           "format": {"type": "grammar", "syntax": "lark", "definition": "start: /./+"}}]}
        captured = {}

        async def fake(_model, messages, params, **_kwargs):
            captured["messages"] = messages
            captured["params"] = params
            yield {"id": "chat-1", "choices": [{"delta": {"tool_calls": [{"index": 0,
                   "id": "call_new", "function": {"name": "apply_patch",
                   "arguments": '{"input":"*** Begin Patch"}'}}]}, "finish_reason": None}]}
            yield {"id": "chat-1", "choices": [{"delta": {}, "finish_reason": "tool_calls"}]}

        async def run():
            with patch("app.responses_api._check_auth", return_value=None), \
                    patch("app.responses_api.tool_provider_supported", return_value=True), \
                    patch("app.responses_api.chat_service.stream_completion", new=fake), \
                    patch.object(state, "commit_trace"):
                response = await responses(make_request(body))
                data = b"".join([chunk.encode() if isinstance(chunk, str) else chunk
                                 async for chunk in response.body_iterator]).decode()
                return response, data

        response, data = asyncio.run(run())
        self.assertEqual(response.status_code, 200)
        self.assertIn("response.custom_tool_call_input.delta", data)
        self.assertIn('"type": "custom_tool_call"', data)
        self.assertEqual(captured["messages"][0]["tool_calls"][0]["function"]["arguments"],
                         '{"input": "*** Begin Patch"}')
        self.assertEqual(captured["messages"][1]["role"], "tool")
        self.assertEqual(captured["params"]["tools"][0]["function"]["name"], "apply_patch")

    def test_chat_service_accepts_tool_only_provider_output(self):
        class Provider:
            id = "custom"

            async def chat_stream(self, *_args, **_kwargs):
                yield {"type": "tool_call", "index": 0, "id": "call_1",
                       "name": "run_command", "arguments": '{"command":"pwd"}'}
                yield {"type": "finish", "reason": "tool_calls"}

        async def run():
            return [chunk async for chunk in ChatService()._attempt(
                Provider(), Account("custom", {}), "raw", "public", [], {})]

        chunks = asyncio.run(run())
        self.assertEqual(chunks[0]["choices"][0]["delta"]["tool_calls"][0]["id"], "call_1")
        self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "tool_calls")

if __name__ == "__main__":
    unittest.main()
