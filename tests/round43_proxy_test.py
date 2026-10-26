"""Custom chat routes and Responses upstream: isolated protocol/transport regressions."""
from __future__ import annotations

import asyncio
import copy
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.providers import http
from app.providers.base import ProviderAuthError, ProviderRateLimitError, ProviderServerError
from app.providers.custom import CustomProvider
from app.providers.responses import StreamAdapter, request_body

def account(**fields):
    return SimpleNamespace(id="round43-custom", proxy="", extra={},
                           fields={"name": "round43", "base_url": "http://127.0.0.1:1/v1", **fields})

def response(*output, status="completed", **extra):
    return {"object": "response", "status": status, "output": list(output), **extra}

def message(text):
    return {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}

class RouteTests(unittest.TestCase):
    def test_legacy_default_and_explicit_protocol_paths(self):
        provider = CustomProvider()
        self.assertEqual(provider.chat_url(account()), "http://127.0.0.1:1/v1/chat/completions")
        self.assertEqual(provider.chat_url(account(api_protocol="responses")), "http://127.0.0.1:1/v1/responses")
        self.assertEqual(provider.chat_protocol(account(chat_endpoint="/api/v2/responses?key=1")), "responses")
        self.assertEqual(provider.chat_protocol(account(chat_endpoint="responses")), "responses")
        self.assertEqual(provider.chat_protocol(account(chat_endpoint="/api/infer", api_protocol="responses")),
                         "responses")

    def test_root_relative_and_base_relative_with_queries(self):
        self.assertEqual(CustomProvider.chat_url(account(chat_endpoint="/v1/responses?api-version=2026-01-01")),
                         "http://127.0.0.1:1/v1/responses?api-version=2026-01-01")
        self.assertEqual(CustomProvider.chat_url(account(chat_endpoint="deployments/m/chat?x=1")),
                         "http://127.0.0.1:1/v1/deployments/m/chat?x=1")

    def test_paths_cannot_redirect_account_credentials_to_another_host(self):
        for path in ("https://attacker.test/generate", "//attacker.test/x", "\\\\attacker.test/x",
                     "../responses", "/v1/../responses", "/v1/responses#fragment", "x\ny"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                CustomProvider.chat_url(account(chat_endpoint=path))

    def test_account_form_exposes_protocol_and_route_without_breaking_existing_accounts(self):
        provider = CustomProvider()
        fields = {field.key: field for field in provider.account_fields()}
        self.assertEqual(fields["api_protocol"].options, ["auto", "chat", "responses"])
        self.assertFalse(fields["chat_endpoint"].required)
        self.assertIsNone(provider.validate_fields(account().fields))
        self.assertIsNone(provider.validate_fields(account(api_protocol="responses").fields))
        self.assertIsNotNone(provider.validate_fields(account(api_protocol="unsupported").fields))
        self.assertIsNotNone(provider.validate_fields(account(chat_endpoint="https://attacker.test/x").fields))

class InputTests(unittest.TestCase):
    def test_history_tools_vision_structured_output_and_no_mutation(self):
        body = {"model": "reasoner", "stream": True, "messages": [
            {"role": "developer", "content": "Follow the user's instructions"},
            {"role": "user", "content": [{"type": "text", "text": "Describe this"},
             {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA", "detail": "low"}}]},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "call1", "type": "function",
             "function": {"name": "read", "arguments": '{"path":"a"}'}}]},
            {"role": "tool", "tool_call_id": "call1", "content": "file contents"}],
            "tools": [{"type": "function", "function": {"name": "read", "description": "read a file",
                      "parameters": {"type": "object", "properties": {}}, "strict": False}}],
            "tool_choice": {"type": "function", "function": {"name": "read"}},
            "response_format": {"type": "json_schema", "json_schema": {"name": "answer",
                                "schema": {"type": "object"}, "strict": True}},
            "reasoning_effort": "high", "max_tokens": 900, "temperature": 0.4,
            "parallel_tool_calls": False, "prompt_cache_key": "cache", "stream_options": {"include_usage": True}}
        before = copy.deepcopy(body)
        converted = request_body(body)
        self.assertEqual(body, before)
        self.assertFalse(converted["store"])
        self.assertNotIn("messages", converted)
        self.assertNotIn("stream_options", converted)
        self.assertEqual(converted["input"][1]["content"][1], {
            "type": "input_image", "image_url": "data:image/png;base64,AAAA", "detail": "low"})
        self.assertEqual(converted["input"][2], {"type": "function_call", "call_id": "call1",
                         "name": "read", "arguments": '{"path":"a"}'})
        self.assertEqual(converted["input"][3], {"type": "function_call_output", "call_id": "call1",
                                               "output": "file contents"})
        self.assertEqual(converted["tools"][0]["name"], "read")
        self.assertEqual(converted["tool_choice"], {"type": "function", "name": "read"})
        self.assertEqual(converted["text"]["format"]["name"], "answer")
        self.assertEqual(converted["max_output_tokens"], 900)
        self.assertEqual(converted["reasoning"]["effort"], "high")

    def test_files_assistant_text_and_multimodal_tool_results(self):
        converted = request_body({"model": "m", "messages": [
            {"role": "assistant", "content": [{"type": "text", "text": "I checked"}]},
            {"role": "user", "content": [{"type": "file", "file": {"file_data": "data:application/pdf;base64,A",
                                                                         "filename": "report.pdf"}}]},
            {"role": "tool", "tool_call_id": "call1", "content": [{"type": "text", "text": "result"}]}]})
        self.assertEqual(converted["input"][0]["content"][0]["type"], "output_text")
        self.assertEqual(converted["input"][1]["content"][0]["type"], "input_file")
        self.assertEqual(converted["input"][2]["output"][0], {"type": "input_text", "text": "result"})

    def test_unknown_media_is_rejected_instead_of_silently_dropped(self):
        with self.assertRaises(ProviderServerError):
            request_body({"model": "m", "messages": [{"role": "user", "content": [
                {"type": "unknown_binary", "data": "secret"}]}]})

class OutputTests(unittest.TestCase):
    def test_parallel_tool_calls_interleaved_with_text_and_final_snapshots(self):
        adapter = StreamAdapter()
        events = [
            {"type": "response.output_text.delta", "output_index": 0, "content_index": 0, "delta": "Hello "},
            {"type": "response.output_text.done", "output_index": 0, "content_index": 0, "text": "Hello world"},
            {"type": "response.output_item.added", "output_index": 2, "item": {
                "type": "function_call", "call_id": "c1", "name": "read", "arguments": ""}},
            {"type": "response.output_item.added", "output_index": 5, "item": {
                "type": "function_call", "call_id": "c2", "name": "list", "arguments": ""}},
            {"type": "response.function_call_arguments.delta", "output_index": 2, "delta": '{"p":'},
            {"type": "response.function_call_arguments.delta", "output_index": 5, "delta": "{}"},
            {"type": "response.function_call_arguments.done", "output_index": 2, "arguments": '{"p":"a"}'},
            {"type": "response.output_item.done", "output_index": 5, "item": {
                "type": "function_call", "call_id": "c2", "name": "list", "arguments": "{}"}},
            {"type": "response.completed", "response": response(usage={"input_tokens": 27, "output_tokens": 9,
                "input_tokens_details": {"cached_tokens": 20}, "output_tokens_details": {"reasoning_tokens": 4}})}]
        out = [chunk for event in events for chunk in adapter.feed(event)]
        texts = [chunk["choices"][0]["delta"].get("content", "") for chunk in out]
        self.assertEqual("".join(texts), "Hello world")
        calls = {}
        for chunk in out:
            for call in chunk["choices"][0]["delta"].get("tool_calls", []):
                calls.setdefault(call["index"], {"name": "", "arguments": "", "id": ""})
                item = calls[call["index"]]
                item["name"] += call["function"].get("name", "")
                item["arguments"] += call["function"].get("arguments", "")
                item["id"] += call.get("id", "")
        self.assertEqual(calls, {0: {"id": "c1", "name": "read", "arguments": '{"p":"a"}'},
                                 1: {"id": "c2", "name": "list", "arguments": "{}"}})
        self.assertEqual(out[-1]["choices"][0]["finish_reason"], "tool_calls")
        self.assertEqual(out[-1]["usage"]["prompt_tokens_details"]["cached_tokens"], 20)
        adapter.require_terminal()

    def test_final_json_fallback_and_truncation(self):
        out = StreamAdapter().feed(response(message("partial"), status="incomplete",
            incomplete_details={"reason": "max_output_tokens"}, usage={"input_tokens": 7, "output_tokens": 12}))
        self.assertEqual(out[0]["choices"][0]["delta"]["content"], "partial")
        self.assertEqual(out[-1]["choices"][0]["finish_reason"], "length")

    def test_text_and_reasoning_snapshots_do_not_duplicate_deltas(self):
        adapter = StreamAdapter()
        out = adapter.feed({"type": "response.reasoning_summary_text.delta", "delta": "Think", "output_index": 0})
        out += adapter.feed({"type": "response.output_text.delta", "delta": "Answer", "output_index": 1})
        out += adapter.feed({"type": "response.completed", "response": response(
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "Think"}]}, message("Answer"))})
        self.assertEqual("".join(c["choices"][0]["delta"].get("content", "") for c in out), "Answer")
        self.assertEqual("".join(c["choices"][0]["delta"].get("reasoning_content", "") for c in out), "Think")

    def test_failed_response_and_missing_terminal_are_errors(self):
        err = StreamAdapter().feed({"type": "response.failed", "response": response(status="failed",
                                   error={"code": "invalid_api_key", "message": "bad key"})})
        self.assertEqual(err[0]["error"]["message"], "bad key")
        with self.assertRaises(ProviderServerError):
            StreamAdapter().require_terminal()

class FakeResponse:
    def __init__(self, lines, *, status=200, content_type="text/event-stream"):
        self.status_code = status
        self.headers = {"content-type": content_type}
        self.lines = lines
        self.closed = False

    async def aiter_lines(self):
        for line in self.lines:
            yield line

    async def aiter_content(self):
        for line in self.lines:
            yield line.encode()

    async def aclose(self):
        self.closed = True

class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def run_provider(self, resp, *, connection=None, params=None):
        session = SimpleNamespace(post=AsyncMock(return_value=resp))
        with patch.object(http, "get_session", AsyncMock(return_value=session)):
            out = [event async for event in CustomProvider().chat_stream(connection or account(api_protocol="responses"),
                "m", [{"role": "user", "content": "hello"}], params or {})]
        return out, session

    async def test_sse_usage_and_tools_run_through_the_existing_provider_parser(self):
        resp = FakeResponse(["event: response.output_item.added", "data: " + json.dumps({
            "type": "response.output_item.added", "output_index": 1,
            "item": {"type": "function_call", "call_id": "c1", "name": "read", "arguments": ""}}),
            "data: " + json.dumps({"type": "response.function_call_arguments.delta", "output_index": 1, "delta": "{}"}),
            "data: " + json.dumps({"type": "response.completed", "response": response(
                usage={"input_tokens": 19, "output_tokens": 8, "input_tokens_details": {"cached_tokens": 7},
                       "output_tokens_details": {"reasoning_tokens": 3}})})])
        out, session = await self.run_provider(resp)
        self.assertEqual([event["type"] for event in out], ["tool_call", "tool_call", "usage", "finish"])
        self.assertEqual(out[-1]["reason"], "tool_calls")
        self.assertEqual(out[-2]["cached_tokens"], 7)
        self.assertEqual(out[-2]["reasoning_tokens"], 3)
        self.assertTrue(resp.closed)
        self.assertEqual(session.post.call_args.args[0], "http://127.0.0.1:1/v1/responses")
        self.assertIn("input", session.post.call_args.kwargs["json"])

    async def test_gateway_ignoring_stream_and_pretty_json_returns_text(self):
        resp = FakeResponse(json.dumps(response(message("Привет")), ensure_ascii=False, indent=2).splitlines(),
                            content_type="application/json; charset=utf-8")
        out, _ = await self.run_provider(resp)
        self.assertEqual(out, [{"type": "text", "text": "Привет"}])
        self.assertTrue(resp.closed)

    async def test_multiline_sse_data_fields_are_reassembled(self):
        payload = json.dumps({"type": "response.completed", "response": response(message("multiline"))}, indent=2)
        resp = FakeResponse(["event: response.completed", *["data: " + line for line in payload.splitlines()], ""])
        out, _ = await self.run_provider(resp)
        self.assertEqual(out, [{"type": "text", "text": "multiline"}])

    async def test_partial_response_does_not_turn_failed_generation_into_success(self):
        resp = FakeResponse(["data: " + json.dumps({"type": "response.output_text.delta", "delta": "part"}),
            "data: " + json.dumps({"type": "response.failed", "response": response(status="failed",
                                   error={"code": "invalid_api_key", "message": "rejected"})})])
        with self.assertRaises(ProviderAuthError):
            await self.run_provider(resp)
        self.assertTrue(resp.closed)

    async def test_sse_error_event_preserves_error_classification(self):
        resp = FakeResponse(['data: {"type":"error","code":"rate_limit_exceeded","message":"slow down"}'])
        with self.assertRaises(ProviderRateLimitError):
            await self.run_provider(resp)
        self.assertTrue(resp.closed)

    async def test_non_terminal_json_response_is_not_reported_as_success(self):
        resp = FakeResponse([json.dumps(response(status="in_progress"))], content_type="application/json")
        with self.assertRaisesRegex(ProviderServerError, "non-terminal"):
            await self.run_provider(resp)
        self.assertTrue(resp.closed)

    async def test_broken_stream_is_error_and_closes_connection(self):
        resp = FakeResponse(["data: " + json.dumps({"type": "response.output_text.delta", "delta": "partial"})])
        with self.assertRaisesRegex(ProviderServerError, "terminal"):
            await self.run_provider(resp)
        self.assertTrue(resp.closed)

    async def test_chat_endpoint_still_uses_chat_format_when_requested(self):
        resp = FakeResponse([json.dumps({"choices": [{"delta": {"content": "old route"}, "finish_reason": "stop"}]})])
        out, session = await self.run_provider(resp, connection=account(api_protocol="chat", chat_endpoint="/generate"),
                                               params={"stream_options": {"include_usage": False}})
        self.assertEqual(out, [{"type": "text", "text": "old route"}])
        self.assertEqual(session.post.call_args.args[0], "http://127.0.0.1:1/generate")
        self.assertIn("messages", session.post.call_args.kwargs["json"])
        self.assertNotIn("input", session.post.call_args.kwargs["json"])

    async def test_key_probe_uses_actual_route_and_cannot_bill_a_valid_response(self):
        session = SimpleNamespace(post=AsyncMock(return_value=SimpleNamespace(status_code=400)))
        verdict = await CustomProvider()._probe_key(account(api_protocol="responses", api_key="test", models="m"),
                                                   session, "http://127.0.0.1:1/v1", [])
        self.assertTrue(verdict[0])
        self.assertTrue(session.post.call_args.args[0].endswith("/responses"))
        self.assertIsNone(session.post.call_args.kwargs["json"]["input"])
        self.assertEqual(session.post.call_args.kwargs["json"]["max_output_tokens"], 0)

    async def test_response_only_server_without_models_and_without_key_can_be_checked(self):
        session = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(status_code=404)),
                                  post=AsyncMock(return_value=SimpleNamespace(status_code=400)))
        with patch.object(http, "get_session", AsyncMock(return_value=session)):
            verdict = await CustomProvider().check(account(api_protocol="responses", models="local-model"))
        self.assertTrue(verdict[0])
        self.assertTrue(session.post.call_args.args[0].endswith("/responses"))

    async def test_stop_aborts_open_responses_stream(self):
        class WaitingResponse(FakeResponse):
            async def aiter_lines(self):
                yield 'data: {"type":"response.output_text.delta","delta":"first"}'
                await asyncio.Event().wait()
        resp = WaitingResponse([])
        session = SimpleNamespace(post=AsyncMock(return_value=resp))
        with patch.object(http, "get_session", AsyncMock(return_value=session)):
            source = CustomProvider().chat_stream(account(api_protocol="responses"), "m", [], {})
            self.assertEqual(await anext(source), {"type": "text", "text": "first"})
            await asyncio.wait_for(source.aclose(), 2)
        self.assertTrue(resp.closed)

    async def test_real_loopback_http_roundtrip_custom_path_and_auth(self):
        received = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                received.append((self.path, payload, self.headers.get("Authorization")))
                data = ("data: " + json.dumps({"type": "response.output_text.delta", "delta": "Local test"}) + "\n\n"
                        + "data: " + json.dumps({"type": "response.completed", "response": response(
                            message("Local test"), usage={"input_tokens": 5, "output_tokens": 2})}) + "\n\n").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = account(base_url=f"http://127.0.0.1:{server.server_port}/custom/root", api_key="test-only",
                                 api_protocol="responses", chat_endpoint="/custom/infer?version=7")
            out = [event async for event in CustomProvider().chat_stream(connection, "local-model",
                   [{"role": "user", "content": "no real API"}], {"max_tokens": 64})]
            self.assertEqual(out[0], {"type": "text", "text": "Local test"})
            self.assertEqual(out[-1]["type"], "usage")
            self.assertEqual(received[0][0], "/custom/infer?version=7")
            self.assertEqual(received[0][1]["input"][0]["content"], "no real API")
            self.assertEqual(received[0][1]["max_output_tokens"], 64)
            self.assertEqual(received[0][2], "Bearer test-only")
        finally:
            await http.close_all()
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join(timeout=2)

if __name__ == "__main__":
    unittest.main()
