"""OAuth client configuration; no external login or network requests."""
import ast
import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

_data = tempfile.TemporaryDirectory(prefix="memu49-oauth-")
os.environ.setdefault("MEMUAI_DATA_DIR", _data.name)

from app import oauth_config
from app.providers import antigravity as ag

class ClientSettings(unittest.TestCase):
    def test_antigravity_environment_overrides_build_without_reusing_google(self):
        env = {"MEMUAI_ANTIGRAVITY_CLIENT_ID": "test-client",
               "MEMUAI_ANTIGRAVITY_CLIENT_SECRET": "test-secret"}
        with patch.dict(os.environ, env), patch.object(oauth_config, "_built", return_value=("built", "built-secret")):
            self.assertEqual(oauth_config.client("antigravity"), ("test-client", "test-secret", "env"))
        with patch.dict(os.environ, {"MEMUAI_GOOGLE_CLIENT_ID": "google-only"}, clear=True), \
                patch.object(oauth_config, "_built", return_value=("", "")):
            self.assertEqual(oauth_config.client("antigravity"), ("", "", ""))

    def test_private_build_fallback(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(oauth_config, "_built", return_value=("built-client", "built-secret")) as built:
            self.assertEqual(ag._oauth_client(), ("built-client", "built-secret"))
            built.assert_called_once_with("antigravity")

    def test_build_writer_keeps_antigravity_separate(self):
        source = Path(__file__).resolve().parents[1] / "build.py"
        function = next(n for n in ast.parse(source.read_text(encoding="utf-8")).body
                        if isinstance(n, ast.FunctionDef) and n.name == "write_oauth_build")
        with tempfile.TemporaryDirectory() as folder:
            local, built = Path(folder) / "local.json", Path(folder) / "built.py"
            local.write_text(json.dumps({"antigravity": {"client_id": "local-client", "client_secret": "local-secret"}}))
            scope = {"os": os, "json": json, "OAUTH_LOCAL": local, "OAUTH_BUILD": built, "say": lambda _: None}
            exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), scope)
            with patch.dict(os.environ, {"MEMUAI_ANTIGRAVITY_CLIENT_ID": "env-client"}, clear=True):
                scope["write_oauth_build"]()
            result = {}
            exec(built.read_text(encoding="utf-8"), result)
            self.assertEqual(result["ANTIGRAVITY_CLIENT_ID"], "env-client")
            self.assertEqual(result["ANTIGRAVITY_CLIENT_SECRET"], "local-secret")
            self.assertEqual(result["GOOGLE_CLIENT_ID"], "")

class OAuthFlow(unittest.IsolatedAsyncioTestCase):
    async def test_missing_credentials_fail_before_browser_or_listener(self):
        provider = ag.AntigravityProvider()
        for pair in (("", ""), ("client-only", ""), ("", "secret-only")):
            with patch.object(oauth_config, "client", return_value=(*pair, "")), \
                    patch.object(ag, "spawn_background") as spawn, patch.object(ag.webbrowser, "open") as browser:
                result = await provider.oauth_start()
                self.assertFalse(result["ok"])
                self.assertIn("MEMUAI_ANTIGRAVITY_CLIENT_ID", result["message"])
                spawn.assert_not_called()
                browser.assert_not_called()

    async def test_login_keeps_client_for_callback(self):
        provider = ag.AntigravityProvider()
        def listener(flow_id, flow, port):
            flow["bound"] = True
            return asyncio.sleep(0)
        with patch.object(ag, "_oauth_client", return_value=("login-client", "login-secret")), \
                patch.object(provider, "_run_listener", side_effect=listener), \
                patch.object(ag, "spawn_background", side_effect=lambda coro: coro.close()), \
                patch.object(ag.webbrowser, "open"):
            result = await provider.oauth_start()
        self.assertTrue(result["ok"])
        self.assertIn("client_id=login-client", result["url"])
        self.assertEqual(provider._flows[result["flow_id"]]["oauth_client"], ("login-client", "login-secret"))
        self.assertNotIn("login-secret", result["url"])

    async def test_code_exchange_uses_original_client_when_configuration_changes(self):
        provider = ag.AntigravityProvider()
        session = SimpleNamespace(post=AsyncMock(return_value=SimpleNamespace(status_code=400)))
        flow = {"oauth_client": ("original-client", "original-secret"), "port": 51121}
        with patch.object(ag, "_oauth_client", side_effect=AssertionError("must use flow snapshot")), \
                patch.object(ag.http, "get_session", AsyncMock(return_value=session)):
            await provider._exchange_code(flow, "test-code")
        payload = session.post.call_args.kwargs["data"]
        self.assertEqual((payload["client_id"], payload["client_secret"]), flow["oauth_client"])
        self.assertEqual(flow["status"], "error")
        self.assertNotIn("original-secret", flow["error"])

    async def test_refresh_uses_configured_client(self):
        provider = ag.AntigravityProvider()
        account = SimpleNamespace(id="test49", extra={"oauth": {"refresh_token": "test-refresh"}}, fields={})
        session = SimpleNamespace(post=AsyncMock(return_value=SimpleNamespace(status_code=400)))
        with patch.object(ag, "_oauth_client", return_value=("refresh-client", "refresh-secret")), \
                patch.object(provider, "proxy_for", return_value=""), \
                patch.object(ag.http, "get_session", AsyncMock(return_value=session)):
            with self.assertRaises(ag.ProviderAuthError):
                await provider._ensure_token(account)
        payload = session.post.call_args.kwargs["data"]
        self.assertEqual((payload["client_id"], payload["client_secret"]), ("refresh-client", "refresh-secret"))

    async def test_valid_access_token_does_not_require_oauth_client(self):
        provider = ag.AntigravityProvider()
        account = SimpleNamespace(extra={"oauth": {"access_token": "valid-test-token", "expires_at": time.time() + 3600}})
        with patch.object(ag, "_oauth_client", side_effect=AssertionError("unnecessary OAuth configuration lookup")):
            self.assertEqual(await provider._ensure_token(account), "valid-test-token")

if __name__ == "__main__":
    unittest.main()
