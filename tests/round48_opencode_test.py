"""Free refusal is not personal quota; catalog reads never launch generation."""
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from app import opencode_runtime as runtime
from app.providers import http
from app.providers.opencode import OpenCodeProvider
from app.providers.base import ProviderAuthError, ProviderRateLimitError, ProviderServerError
from app.providers.custom import CustomProvider
from tests.round46_opencode_test import account, seed, metadata, Response


def free_account():
    acc = seed(account())
    acc.extra["opencode_catalog"]["models"]["m"]["cost"] = {"input": 0, "output": 0}
    return acc


class CatalogAndErrors(unittest.IsolatedAsyncioTestCase):
    async def test_public_catalog_does_not_start_or_install_gateway(self):
        responses = [Response({"data": [{"id": "genuine-zero"}]}), Response(metadata())]
        session = SimpleNamespace(get=AsyncMock(side_effect=responses), post=AsyncMock())
        with patch.object(http, "get_session", AsyncMock(return_value=session)), \
                patch.object(runtime.runtime, "ensure", AsyncMock()) as ensure, \
                patch.object(runtime.runtime, "install") as install:
            self.assertEqual(await OpenCodeProvider().dynamic_models(account()), ["genuine-zero"])
        ensure.assert_not_called(); install.assert_not_called(); session.post.assert_not_called()

    async def test_check_warns_catalog_is_not_generation(self):
        provider = OpenCodeProvider()
        with patch.object(provider, "dynamic_models", AsyncMock(return_value=["m"])):
            ok, text = await provider.check(account())
        self.assertTrue(ok); self.assertIn("Генерация не проверена", text); self.assertIn("анонимный", text)

    async def test_free_429_explains_scope_preserves_detail_redacts_key_and_stops_child(self):
        async def refusal(*args):
            raise ProviderRateLimitError("Free usage limit reached; local-secret")
            yield
        view = SimpleNamespace(fields={"base_url": "http://127.0.0.1:1234/v1", "api_key": "local-secret"})
        provider = OpenCodeProvider()
        with patch.object(provider, "_free_connection", AsyncMock(return_value=view)), \
                patch.object(CustomProvider, "chat_stream", refusal), patch.object(runtime.runtime, "stop") as stop:
            with self.assertRaises(ProviderRateLimitError) as caught:
                _ = [e async for e in provider.chat_stream(free_account(), "m", [], {})]
        error = caught.exception
        self.assertEqual(error.status, 429); self.assertEqual(error.retry_after, 60)
        self.assertIn("not your signed-in account", str(error)); self.assertIn("Free usage limit reached", str(error))
        self.assertIn("не доказывает", error.message_ru); self.assertNotIn("local-secret", str(error))
        stop.assert_called_once_with("round46-open")

    async def test_real_retry_after_is_preserved(self):
        async def refusal(*args):
            raise ProviderRateLimitError("wait", retry_after=120)
            yield
        provider = OpenCodeProvider()
        with patch.object(provider, "_free_connection", AsyncMock(return_value=SimpleNamespace(fields={"base_url": "http://127.0.0.1/v1", "api_key": "k"}))), \
                patch.object(CustomProvider, "chat_stream", refusal), patch.object(runtime.runtime, "stop"):
            with self.assertRaises(ProviderRateLimitError) as caught:
                _ = [e async for e in provider.chat_stream(free_account(), "m", [], {})]
        self.assertEqual(caught.exception.retry_after, 120)

    async def test_anonymous_403_is_not_a_broken_personal_key(self):
        async def refusal(*args):
            raise ProviderAuthError("The API key was rejected (HTTP 403): only within OpenCode")
            yield
        provider = OpenCodeProvider()
        with patch.object(provider, "_free_connection", AsyncMock(return_value=SimpleNamespace(fields={"base_url": "http://127.0.0.1/v1", "api_key": "k"}))), \
                patch.object(CustomProvider, "chat_stream", refusal), patch.object(runtime.runtime, "stop"):
            with self.assertRaises(ProviderRateLimitError) as caught:
                _ = [e async for e in provider.chat_stream(free_account(), "m", [], {})]
        self.assertNotIsInstance(caught.exception, ProviderAuthError)
        self.assertEqual(caught.exception.status, 403)
        self.assertIn("не ваш личный", caught.exception.message_ru)

    async def test_authenticated_429_keeps_existing_semantics(self):
        async def refusal(*args):
            raise ProviderRateLimitError("native limit", retry_after=7)
            yield
        with patch.object(CustomProvider, "chat_stream", refusal), patch.object(runtime.runtime, "stop") as stop:
            with self.assertRaisesRegex(ProviderRateLimitError, "native limit") as caught:
                _ = [e async for e in OpenCodeProvider().chat_stream(seed(account("zen")), "m", [], {})]
        self.assertEqual(caught.exception.retry_after, 7); stop.assert_not_called()


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_probe_is_explicit_free_only_and_coalesces_repeated_calls(self):
        provider = OpenCodeProvider(); acc = free_account(); calls = []
        async def stream(*args):
            calls.append(args)
            await asyncio.sleep(.01)
            yield {"text": "OK"}
        with patch.object(provider, "dynamic_models", AsyncMock(return_value=["m"])), patch.object(provider, "chat_stream", stream):
            first, second = await asyncio.gather(provider.probe_free(acc), provider.probe_free(acc))
            third = await provider.probe_free(acc)
        self.assertTrue(first["ok"]); self.assertTrue(second["cached"]); self.assertTrue(third["cached"])
        self.assertEqual(len(calls), 1); self.assertEqual(calls[0][-1]["max_tokens"], 16)
        self.assertEqual(first["scope"], "anonymous_public")

    async def test_probe_never_uses_paid_or_unpriced_model(self):
        provider = OpenCodeProvider()
        for cost in ({"input": 0, "output": 1}, {}, {"input": False, "output": 0}):
            acc = free_account(); acc.id = str(cost)
            acc.extra["opencode_catalog"]["models"]["m"]["cost"] = cost
            with patch.object(provider, "dynamic_models", AsyncMock(return_value=["m"])), patch.object(provider, "chat_stream") as stream:
                result = await provider.probe_free(acc)
            self.assertFalse(result["ok"]); stream.assert_not_called()
        with self.assertRaises(ValueError): await provider.probe_free(account("zen"))

    async def test_probe_empty_response_is_not_success(self):
        provider = OpenCodeProvider()
        async def stream(*args):
            yield {"usage": {"completion_tokens": 0}}
        with patch.object(provider, "dynamic_models", AsyncMock(return_value=["m"])), patch.object(provider, "chat_stream", stream):
            result = await provider.probe_free(free_account())
        self.assertFalse(result["ok"]); self.assertEqual(result["status"], "empty")

    async def test_probe_endpoint_requires_auth_saved_free_and_confirmation(self):
        from fastapi import FastAPI
        import httpx
        from app import admin_api
        app = FastAPI(); app.include_router(admin_api.router)
        provider = SimpleNamespace(probe_free=AsyncMock(return_value={"ok": True}))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            with patch.object(admin_api, "_auth", return_value=admin_api._err("denied", 403)):
                self.assertEqual((await client.post("/api/admin/accounts/a/opencode-probe", json={"confirmed": True})).status_code, 403)
            with patch.object(admin_api, "_auth", return_value=None), patch.object(admin_api.state, "get_account", return_value=account("zen")):
                self.assertEqual((await client.post("/api/admin/accounts/a/opencode-probe", json={})).status_code, 400)
                self.assertEqual((await client.post("/api/admin/accounts/a/opencode-probe", json={"confirmed": True})).status_code, 400)
            with patch.object(admin_api, "_auth", return_value=None), patch.object(admin_api.state, "get_account", return_value=free_account()), patch.object(admin_api, "get_provider", return_value=provider):
                self.assertTrue((await client.post("/api/admin/accounts/a/opencode-probe", json={"confirmed": True})).json()["ok"])
        provider.probe_free.assert_awaited_once()


class ManualRegistry(unittest.TestCase):
    def test_pinned_gateway_manual_registry_matches_supported_schema(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            runtime._manual_availability(path, ("m-free", "n-free"))
            target = Path(str(path) + "." + hashlib.sha256(b"https://opencode.ai/zen").hexdigest()[:16] + ".availability.json")
            data = json.loads(target.read_text("utf-8"))
            self.assertEqual(set(data), {"m-free", "n-free"})
            for name, row in data.items():
                self.assertEqual(row["model"], name); self.assertTrue(row["manual"])
                self.assertEqual(row["probe_version"], 1); self.assertFalse(row["disabled"])


if __name__ == "__main__": unittest.main()
