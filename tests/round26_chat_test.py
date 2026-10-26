"""Round 26: chat regressions found by driving the real chat against live models.

- The playground's client for the local Proxy ignores system/VPN proxies
  (httpx applied HTTP_PROXY / the Windows registry proxy even to 127.0.0.1,
  so every chat request landed on the foreign proxy: "не-JSON ответ").
- DeepSeek service markup ``<ds_safety>…</ds_safety>`` never reaches the
  answer, even when a chunk boundary splits the tag.
"""
from __future__ import annotations

import asyncio
import os
import random
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

class PlaygroundIgnoresSystemProxy(unittest.TestCase):
    def test_local_proxy_client_does_not_trust_env(self):
        import httpx
        made = []
        real = httpx.AsyncClient

        def spy(*args, **kwargs):
            made.append(kwargs)
            return real(*args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:9", "MEMUAI_PROXY_URL": "http://127.0.0.1:1"}), \
                patch.object(httpx, "AsyncClient", spy):
            from app import playground_api
            with patch.object(playground_api, "DATA_DIR", tmp):
                playground_api.create_app()
        bridge = [kw for kw in made if getattr(kw.get("timeout"), "read", None) == 600]
        self.assertTrue(bridge, made)
        self.assertIs(bridge[0].get("trust_env"), False)

class ServiceTagScrubber(unittest.TestCase):
    CASES = [
        ("Ответ 391.<ds_safety>safe</ds_safety>", "Ответ 391."),
        ('A<ds_safety level="x">bad\nstuff</ds_safety> B', "A B"),
        ("a < b and <b>x</b> <ds_safe", "a < b and <b>x</b> <ds_safe"),
        ("x<ds_safety/>y", "xy"),
        ("2<3 и <ds_safetyX>", "2<3 и <ds_safetyX>"),
        ("<ds_safety>never closed", ""),
    ]

    def test_any_chunking(self):
        from app.chat import _ServiceTagScrubber
        rnd = random.Random(26)
        for text, expected in self.CASES:
            for _ in range(300):
                cuts = sorted(rnd.sample(range(1, len(text)), min(len(text) - 1, rnd.randint(0, 8))))
                parts = [text[a:b] for a, b in zip([0] + cuts, cuts + [len(text)])]
                scrub = _ServiceTagScrubber()
                out = "".join(scrub.feed(p) for p in parts) + scrub.flush()
                self.assertEqual(out, expected, parts)

    def test_stream_never_shows_the_markup(self):
        from app.chat import ChatService

        class Provider:
            id = "custom"

            async def chat_stream(self, *_a):
                for piece in ("Привет", "!\n\n<ds_", "safety>safe", "</ds_saf", "ety>"):
                    yield {"type": "text", "text": piece}

        async def collect():
            service = ChatService.__new__(ChatService)
            account = type("A", (), {"label": "a", "id": "a"})()
            return [c async for c in service._attempt(Provider(), account, "m", "custom-m",
                                                      [{"role": "user", "content": "hi"}], {})]

        chunks = asyncio.run(collect())
        text = "".join((ch.get("delta") or {}).get("content") or ""
                       for c in chunks for ch in c.get("choices") or [])
        self.assertEqual(text, "Привет!\n\n")

class ReadableProviderErrors(unittest.TestCase):
    """A wrong key or model gives one readable line, not raw JSON or «add accounts»."""

    def test_json_body_becomes_its_message(self):
        from app.chat import CompletionError, readable_provider_error
        raw = 'Upstream error (HTTP 404): {"error":{"message":"Model \\"x\\" does not exist.","type":"not_found_error"}}'
        self.assertEqual(readable_provider_error(raw), 'Upstream error (HTTP 404): Model "x" does not exist.')
        self.assertEqual(readable_provider_error("plain {not json}"), "plain {not json}")
        err = CompletionError("fail: " + '{"detail":"bad key"}', message_en='oops {"error":{"message":"bad"}}')
        self.assertEqual((err.message, err.message_en), ("fail: bad key", "oops bad"))
        self.assertNotIn("стадиях запроса ниже", CompletionError("x", err_type="upstream_error").hint)

    def _run(self, provider, accounts, all_accounts=None):
        from app.chat import ChatService, CompletionError
        from app.config import config_store
        from app.routing import Resolved

        async def run():
            service = ChatService()
            with patch.object(config_store.settings, "proxy_enabled", True), \
                    patch("app.chat.resolve", return_value=Resolved(None, "custom", "no/model", "conn")), \
                    patch.object(service, "_provider", return_value=provider), \
                    patch("app.chat.pool.ordered_candidates", return_value=accounts), \
                    patch("app.chat.state.accounts_of", return_value=all_accounts or []), \
                    patch("app.chat.pool.report_success"), patch("app.chat.pool.report_failure"):
                try:
                    async for _ in service.stream_completion("conn-no/model", [{"role": "user", "content": "x"}], {}):
                        pass
                except CompletionError as exc:
                    return exc
            return None

        return asyncio.run(run())

    def test_unknown_model_names_the_model(self):
        from app.providers.base import ProviderServerError
        from app.state import Account

        class Provider:
            id = "custom"

            async def chat_stream(self, *_a, **_k):
                raise ProviderServerError('Upstream error (HTTP 404): {"error":{"message":"Model does not exist."}}', 404)
                yield  # pragma: no cover

        exc = self._run(Provider(), [Account("custom", {"name": "conn"}, id="a")])
        self.assertEqual(exc.err_type, "model_not_found")
        self.assertIn("«no/model»", exc.message)
        self.assertIn("Model does not exist.", exc.message)
        self.assertNotIn("{", exc.message)

    def test_rejected_account_explains_itself(self):
        from app.state import Account, ST_INVALID
        account = Account("custom", {"name": "conn"}, id="bad", label="conn · host")
        account.mark(ST_INVALID, "The API key was rejected (HTTP 401)")
        exc = self._run(object(), [], [account, Account("custom", {"name": "other"}, id="o")])
        self.assertEqual(exc.err_type, "authentication_error")
        self.assertIn("conn · host", exc.message)
        self.assertIn("HTTP 401", exc.message)
        self.assertNotIn("Добавьте их", exc.message)

if __name__ == "__main__":
    unittest.main()
