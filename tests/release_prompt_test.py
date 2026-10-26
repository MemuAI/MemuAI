"""Prompt view/copy data contract, protocol capture and bounded log rotation."""
import asyncio
from collections import deque
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from starlette.requests import Request
from app import admin_api, anthropic_api, chat, openai_api, responses_api
from app import prompt_logging, state as storage

def request(body, path="/v1/chat/completions"):
    async def receive():
        return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}
    return Request({"type": "http", "method": "POST", "path": path, "headers": []}, receive)

class PromptSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.enabled = patch.object(storage, "_log_payloads_enabled", return_value=True)
        self.enabled.start()
        self.addCleanup(self.enabled.stop)

    def test_snapshot_is_detached_and_first_input_wins(self):
        messages = [{"role": "system", "content": "Rules"}, {"role": "user", "content": "Original"}]
        tools = [{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}]
        entry = storage.LogEntry()
        entry.capture_prompt(messages, tools=tools)
        messages[1]["content"] = "Rewritten"
        tools[0]["function"]["name"] = "Rewritten"
        entry.capture_prompt(messages)
        self.assertEqual(entry.prompt["messages"][1]["content"], "Original")
        self.assertEqual(entry.prompt["tools"][0]["function"]["name"], "read")

    def test_media_is_omitted_before_serialization(self):
        media = "data:image/png;base64," + "BINARY_SENTINEL" * 200000
        messages = [{"role": "user", "content": [{"type": "text", "text": "Describe"},
                    {"type": "image_url", "image_url": {"url": media}},
                    {"type": "input_audio", "input_audio": {"data": media}}]}]
        snapshot = prompt_logging.snapshot_prompt(messages)
        text = json.dumps(snapshot)
        self.assertIn("Describe", text)
        self.assertNotIn("BINARY_SENTINEL", text)
        self.assertNotIn("base64", text)
        self.assertTrue(snapshot["omitted_media"])
        self.assertFalse(snapshot["truncated"])

    def test_large_unicode_prompt_preserves_every_character_and_tail(self):
        text = "🐈\n\"\\" * 100000 + "END_SENTINEL"
        snapshot = prompt_logging.snapshot_prompt([{"role": "user", "content": text}])
        encoded = json.dumps(snapshot, ensure_ascii=False).encode("utf-8")
        self.assertGreater(len(encoded), 64 * 1024)
        self.assertEqual(json.loads(encoded)["messages"][0]["content"], text)
        self.assertFalse(snapshot["truncated"])

    def test_message_block_call_and_schema_counts_do_not_cut_prompt(self):
        arguments = {"tail": "TOOL_TAIL"}
        for _ in range(40):
            arguments = {"child": arguments}
        message = {"role": "assistant", "content": [{"type": "text", "text": str(i)} for i in range(150)],
                   "tool_calls": [{"id": str(i), "type": "function", "function": {
                       "name": "test", "arguments": json.dumps(arguments)}} for i in range(70)]}
        tools = [{"type": "function", "function": {"name": "test", "parameters": {
            "properties": {"field" + str(i): {"type": "string"} for i in range(1100)}}}}]
        snapshot = prompt_logging.snapshot_prompt([message] + [{"role": "user", "content": str(i)} for i in range(600)], tools=tools)
        self.assertEqual(len(snapshot["messages"]), 601)
        self.assertEqual(len(snapshot["messages"][0]["content"]), 150)
        self.assertEqual(len(snapshot["messages"][0]["tool_calls"]), 70)
        self.assertIn("TOOL_TAIL", json.dumps(snapshot["messages"][0]["tool_calls"][-1]))
        self.assertEqual(snapshot["tools"], tools)
        self.assertFalse(snapshot["truncated"])

    def test_long_prompt_survives_persistence_reload_and_detail_endpoint(self):
        text = "Полный контекст\n" * 100000 + "END_SENTINEL"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "requests.jsonl"
            owner = object.__new__(storage.State)
            owner._lock = threading.RLock()
            owner.logs = deque(maxlen=storage.LOG_MEMORY_LIMIT)
            entry = storage.LogEntry(status=200)
            entry.capture_prompt([{"role": "user", "content": text}])
            with patch.object(storage, "LOGS_PATH", str(path)), patch.object(storage, "LOGS_DIR", temporary), \
                 patch.object(storage, "_log_persist_enabled", return_value=True):
                owner.persist_log(entry)
                owner._load_logs()
                self.assertEqual(owner.logs[0].prompt["messages"][0]["content"], text)
                with patch.object(admin_api, "state", owner), patch.object(admin_api, "_auth", return_value=None):
                    detail = asyncio.run(admin_api.get_log_prompt(request({}), entry.request_id))
                    self.assertEqual(detail["prompt"]["messages"][0]["content"], text)
                    exported = asyncio.run(admin_api.export_logs(request({}), fmt="json"))
                    self.assertEqual(json.loads(exported.body)["logs"][0]["prompt"]["messages"][0]["content"], text)

    def test_memory_retention_evicts_whole_old_records_without_cutting_latest(self):
        owner = object.__new__(storage.State)
        owner._lock = threading.RLock()
        owner.logs = deque(maxlen=3000)
        old = storage.LogEntry(status=200)
        old.capture_prompt([{"role": "user", "content": "old" * 300}])
        latest = storage.LogEntry(status=200)
        text = "new" * 1000 + "END_SENTINEL"
        latest.capture_prompt([{"role": "user", "content": text}])
        active = storage.LogEntry()
        active.capture_prompt([{"role": "user", "content": "active" * 300}])
        owner.logs.extend([active, latest, old])
        with patch.object(storage, "LOG_FILE_MAX_BYTES", 1024), patch.object(owner, "persist_log"):
            owner.commit_trace(latest)
        self.assertEqual(list(owner.logs), [active, latest])
        self.assertEqual(latest.prompt["messages"][0]["content"], text)

    def test_non_json_cycle_is_rejected_without_cutting_shared_valid_subtrees(self):
        cycle = {}
        cycle["self"] = cycle
        child = {"tail": "preserved"}
        snapshot = prompt_logging.snapshot_prompt([], tools=[cycle, {"a": child, "b": child}])
        self.assertIsNone(snapshot["tools"][0]["self"])
        self.assertEqual(snapshot["tools"][1], {"a": child, "b": child})
        self.assertTrue(snapshot["truncated"])

    def test_invalid_tool_calls_and_credentials_are_sanitized(self):
        arguments = {"api_key": "UNKNOWN_CREDENTIAL", "query": "ordinary"}
        nested = {}
        for _ in range(40):
            nested = {"child": nested}
        messages = [{"role": "assistant", "content": "Authorization: Bearer SECRET\n" +
                     "known-transport-secret\nsk-abcdefghijklmnopqrstuvwxyz123456",
                     "tool_calls": [{"function": {"name": "lookup", "arguments": json.dumps(arguments)}}, nested]}]
        snapshot = prompt_logging.snapshot_prompt(messages, secrets=["known-transport-secret"],
                                                   is_secret=storage._is_secret_key)
        encoded = json.dumps(snapshot)
        for secret in ("UNKNOWN_CREDENTIAL", "known-transport-secret", "Bearer SECRET", "sk-abcdefghijklmnopqrstuvwxyz123456"):
            self.assertNotIn(secret, encoded)
        self.assertIn("ordinary", encoded)
        self.assertIn("REDACTED", encoded)
        self.assertTrue(snapshot["truncated"])

    def test_known_account_and_api_credentials_are_masked(self):
        account = storage.Account("custom", {"api_key": "account-key-sentinel"})
        with patch.object(storage.state, "accounts", {account.id: account}), \
             patch.object(storage.config_store.settings, "api_keys", ["gateway-key-sentinel"]):
            entry = storage.LogEntry()
            entry.capture_prompt([{"role": "user", "content": "account-key-sentinel gateway-key-sentinel ordinary"}])
        self.assertEqual(entry.prompt["messages"][0]["content"], "[REDACTED] [REDACTED] ordinary")

    def test_custom_pool_individual_and_trimmed_credentials_are_masked(self):
        account = storage.Account("custom", {
            "api_keys": " pool-first-credential\n pool-second-credential; short ",
            "api_key": " legacy-credential "})
        with patch.object(storage.state, "accounts", {account.id: account}):
            entry = storage.LogEntry()
            entry.capture_prompt([{"role": "user", "content":
                "pool-first-credential pool-second-credential short legacy-credential ordinary"}])
        self.assertEqual(entry.prompt["messages"][0]["content"],
                         "[REDACTED] [REDACTED] [REDACTED] [REDACTED] ordinary")

    def test_tool_calls_whitelist_fields_and_omit_media_inside_arguments(self):
        call = {"id":"call", "type":"function", "private_asset":"https://files.test/private?sig=SECRET_URL",
                "image_url":{"url":"data:image/png;base64,RAW_MEDIA_SENTINEL"},
                "function":{"name":"read", "arguments":json.dumps({"image_url":{"url":"data:image/png;base64,RAW_MEDIA_SENTINEL"},
                    "target":"https://user:pass@files.test/file?X-Amz-Signature=SECRET_URL", "query":"ordinary"})}}
        snapshot = prompt_logging.snapshot_prompt([{"role":"assistant", "tool_calls":[call]}], is_secret=storage._is_secret_key)
        text = json.dumps(snapshot)
        for secret in ("RAW_MEDIA_SENTINEL", "SECRET_URL", "user:pass"):
            self.assertNotIn(secret, text)
        self.assertIn("ordinary", text)
        self.assertTrue(snapshot["omitted_media"])

    def test_large_tool_argument_json_stays_complete_and_masks_credentials(self):
        arguments = json.dumps({"api_key":"UNKNOWN_CREDENTIAL_SENTINEL", "padding":"x" * 100000})
        snapshot = prompt_logging.snapshot_prompt([{"role":"assistant", "function_call":{"name":"test", "arguments":arguments}}],
                                                   is_secret=storage._is_secret_key)
        self.assertNotIn("UNKNOWN_CREDENTIAL_SENTINEL", json.dumps(snapshot))
        self.assertEqual(snapshot["messages"][0]["function_call"]["arguments"]["padding"], "x" * 100000)
        self.assertFalse(snapshot["truncated"])

    def test_secret_classification_uses_original_long_key(self):
        key = "x" * 170 + "api_key"
        snapshot = prompt_logging.snapshot_prompt([{"role":"assistant", "function_call":{"name":"test",
            "arguments":json.dumps({key:"UNKNOWN_CREDENTIAL_SENTINEL"})}}], is_secret=storage._is_secret_key)
        self.assertNotIn("UNKNOWN_CREDENTIAL_SENTINEL", json.dumps(snapshot))
        self.assertFalse(snapshot["truncated"])

    def test_lone_surrogate_and_union_schema_do_not_break_requests(self):
        snapshot = prompt_logging.snapshot_prompt([{"role":"user", "content":"valid\ud800tail"}],
            tools=[{"type":"function", "function":{"name":"test", "parameters":{"type":["object", "null"]}}}])
        text = json.dumps(snapshot, ensure_ascii=False).encode("utf-8")
        self.assertIn(b"valid?tail", text)
        self.assertTrue(snapshot["truncated"])
        self.assertEqual(snapshot["tools"][0]["function"]["parameters"]["type"], ["object", "null"])

    def test_short_known_credentials_use_token_boundaries(self):
        snapshot = prompt_logging.snapshot_prompt([{"role":"user", "content":"abc1234 hub1234 myabc1234suffix"}],
            secrets=["abc1234", "hub1234"])
        self.assertEqual(snapshot["messages"][0]["content"], "[REDACTED] [REDACTED] myabc1234suffix")

    def test_payload_setting_blocks_capture_and_hides_existing_snapshot(self):
        entry = storage.LogEntry()
        entry.capture_prompt([{"role": "user", "content": "private"}])
        with patch.object(storage, "_log_payloads_enabled", return_value=False):
            other = storage.LogEntry()
            other.capture_prompt([{"role": "user", "content": "private"}])
            self.assertIsNone(other.prompt)
            self.assertIsNone(entry.public_dict()["prompt"])
        self.assertEqual(entry.public_dict()["prompt"]["messages"][0]["content"], "private")

    def test_legacy_and_persisted_snapshots_load_without_losing_flags(self):
        self.assertIsNone(storage.LogEntry(stages=[]).public_dict()["prompt"])
        entry = storage.LogEntry()
        entry.capture_prompt([{"role": "user", "content": "hello"}])
        entry.prompt["omitted_media"] = True
        entry.prompt["truncated"] = True
        loaded = storage.LogEntry(**json.loads(json.dumps(entry.public_dict())))
        self.assertEqual(loaded.prompt, entry.prompt)
        malformed = storage.LogEntry(prompt={"messages": "malformed"})
        self.assertIsNone(malformed.prompt)

    def test_disabled_proxy_still_records_input(self):
        async def run():
            entry = storage.LogEntry()
            with patch.object(chat.config_store.settings, "proxy_enabled", False):
                source = chat.chat_service.stream_completion("missing", [{"role": "user", "content": "hello"}], trace=entry)
                with self.assertRaises(chat.CompletionError):
                    await anext(source)
            self.assertEqual(entry.prompt["messages"][0]["content"], "hello")
        asyncio.run(run())

    def test_all_protocols_capture_before_provider_or_response_iteration(self):
        async def run():
            text = "hello 🐈\n" * 20000 + "PROTOCOL_END_SENTINEL"
            for module, handler, body, auth in (
                (openai_api, openai_api.chat_completions, {"model":"m", "stream":True,
                    "messages":[{"role":"system", "content":"rules"}, {"role":"user", "content":text}]}, "_check_auth"),
                (anthropic_api, anthropic_api.messages, {"model":"m", "stream":True, "max_tokens":2,
                    "system":"rules", "messages":[{"role":"user", "content":text}]}, "_check_auth"),
                (responses_api, responses_api.responses, {"model":"m", "stream":True,
                    "instructions":"rules", "input":text}, "_check_auth"),
                (admin_api, admin_api.pg_chat, {"model":"m", "messages":[{"role":"user", "content":text}]}, "_auth"),
            ):
                with self.subTest(module=module.__name__):
                    entry = storage.LogEntry()
                    async def source(*args, **kwargs):
                        yield {"choices": []}
                    with patch.object(module, auth, return_value=None), \
                         patch.object(storage.state, "open_trace", return_value=entry), \
                         patch.object(storage.state, "commit_trace"), \
                         patch.object(chat.chat_service, "stream_completion", source):
                        response = await handler(request(body))
                        self.assertIsNotNone(entry.prompt)
                        self.assertIn("hello", json.dumps(entry.prompt))
                        self.assertIn("PROTOCOL_END_SENTINEL", json.dumps(entry.prompt))
                        self.assertFalse(entry.prompt["truncated"])
                        if module != admin_api:
                            self.assertIn("rules", json.dumps(entry.prompt))
                        await response.aclose()
        asyncio.run(run())

    def test_search_and_text_export_include_prompt(self):
        entry = storage.LogEntry(status=200)
        entry.capture_prompt([{"role":"user", "content":"UNIQUE_PROMPT_NEEDLE"}])
        with patch.object(storage.state, "logs", deque([entry])), patch.object(admin_api, "_auth", return_value=None):
            self.assertEqual(storage.state.list_logs(query="unique_prompt_needle")["total"], 1)
            result = asyncio.run(admin_api.export_logs(request({}), fmt="txt"))
            self.assertIn(b"UNIQUE_PROMPT_NEEDLE", result.body)
            with patch.object(storage, "_log_payloads_enabled", return_value=False):
                self.assertEqual(storage.state.list_logs(query="unique_prompt_needle")["total"], 0)

    def test_list_is_lightweight_and_detail_is_authenticated_and_respects_setting(self):
        entry = storage.LogEntry(status=200)
        entry.capture_prompt([{"role":"user", "content":"private prompt"}])
        with patch.object(storage.state, "logs", deque([entry])), patch.object(admin_api, "_auth", return_value=None):
            listing = asyncio.run(admin_api.get_logs(request({})))
            self.assertTrue(listing["logs"][0]["prompt"]["available"])
            self.assertNotIn("private prompt", json.dumps(listing))
            detail = asyncio.run(admin_api.get_log_prompt(request({}), entry.request_id))
            self.assertEqual(detail["prompt"], entry.prompt)
            with patch.object(storage, "_log_payloads_enabled", return_value=False):
                self.assertEqual(asyncio.run(admin_api.get_log_prompt(request({}), entry.request_id)).status_code, 404)
            self.assertEqual(asyncio.run(admin_api.get_log_prompt(request({}), "missing")).status_code, 404)
            deny = admin_api.JSONResponse({"error":"unauthorized"}, 401)
            with patch.object(admin_api, "_auth", return_value=deny):
                self.assertIs(asyncio.run(admin_api.get_log_prompt(request({}), entry.request_id)), deny)

    def test_rotation_keeps_recent_rows_below_byte_and_row_limits(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "requests.jsonl"
            with patch.object(storage, "LOGS_PATH", str(path)), \
                 patch.object(storage, "LOG_FILE_MAX_BYTES", 2048), \
                 patch.object(storage, "LOG_MEMORY_LIMIT", 4):
                rows = [json.dumps({"prompt":"x" * 700, "id":i}).encode() + b"\n" for i in range(5)]
                path.write_bytes(b"".join(rows))
                storage.state._rotate_logs()
                self.assertLessEqual(path.stat().st_size, 1024)
                self.assertEqual(json.loads(path.read_bytes())["id"], 4)
                storage.state._rotate_logs()
                self.assertLessEqual(path.stat().st_size, 1024)
                path.write_bytes(rows[0] + b"x" * 3000 + b"\n" + rows[-1])
                storage.state._rotate_logs()
                self.assertEqual(json.loads(path.read_bytes())["id"], 4)

if __name__ == "__main__":
    unittest.main()
