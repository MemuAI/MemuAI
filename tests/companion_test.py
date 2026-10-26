"""The Proxy companion: knowledge selection, a secret-free snapshot and the chat route."""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import companion
from app.providers import builtin_ids

class KnowledgeTests(unittest.TestCase):
    def test_every_provider_has_a_knowledge_file(self) -> None:
        files = companion.kb_files()
        for provider in builtin_ids():
            self.assertIn(f"provider-{provider}", files, f"add app/companion_kb/provider-{provider}.md")
        for name in ("overview", "errors", "api", "settings", "local", "detector", "troubleshooting"):
            self.assertIn(name, files)

    def test_only_relevant_files_are_picked(self) -> None:
        picked = companion.pick_kb("Kiro пишет 401 после входа", ["genspark"])
        self.assertIn("overview", picked)
        self.assertIn("errors", picked)
        self.assertIn("provider-kiro", picked)
        self.assertIn("provider-genspark", picked)
        self.assertNotIn("provider-notion", picked)
        self.assertNotIn("README", picked)
        self.assertIn("local", companion.pick_kb("как скачать gguf модель", []))

class SnapshotTests(unittest.TestCase):
    def test_snapshot_never_carries_secrets(self) -> None:
        from app.state import state
        account = state.add_account("custom", {"name": "leaktest", "base_url": "https://x/v1",
                                               "api_key": "sk-SECRET-VALUE-123"}, label="Leak test")
        try:
            text = json.dumps(companion.snapshot(), ensure_ascii=False)
            self.assertIn("Leak test", text)
            self.assertNotIn("sk-SECRET-VALUE-123", text)
            messages = companion.build_messages([], "почему ошибка", error={"message": "HTTP 500"})
            self.assertEqual(messages[0]["role"], "system")
            self.assertIn("<knowledge_base>", messages[0]["content"])
            self.assertNotIn("sk-SECRET-VALUE-123", messages[0]["content"])
            self.assertIn("<error_details>", messages[-1]["content"])
        finally:
            state.remove_account(account.id)

class RouteTests(unittest.TestCase):
    def test_chat_streams_answer_and_real_errors(self) -> None:
        from app.main import create_app

        async def fake_stream(model, messages, params=None):
            yield {"type": "text", "text": "Обновите "}
            yield {"type": "text", "text": "куку."}

        async def broken_stream(model, messages, params=None):
            raise RuntimeError("Модель 'x' не маршрутизируется")
            yield {}

        with patch("app.companion.default_model", lambda: "some-model"):
            with TestClient(create_app(), base_url="http://127.0.0.1") as client:
                with patch("app.model_call.stream", fake_stream):
                    response = client.post("/api/admin/companion/chat", json={"message": "что делать?"})
                events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
                self.assertEqual(events[0], {"type": "meta", "model": "some-model"})
                self.assertEqual("".join(e.get("text", "") for e in events), "Обновите куку.")
                self.assertEqual(events[-1]["type"], "done")
                with patch("app.model_call.stream", broken_stream):
                    response = client.post("/api/admin/companion/chat", json={"message": "?"})
                events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
                self.assertEqual(events[-1]["type"], "error")
                self.assertIn("не маршрутизируется", events[-1]["error"])

if __name__ == "__main__":
    unittest.main()
