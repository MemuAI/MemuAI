"""Focused offline contracts for multimodal/provider compatibility fixes."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.providers.base import messages_have_images
from app.providers.custom import CustomProvider
from app.providers.genspark import is_chat_model
from app.state import Account
from app.storage import load_json_with_recovery, save_json

class FakeResponse:
    status_code = 200
    text = ""

    def json(self):
        return {"data": [{"b64_json": "YWJj"}]}

class FakeSession:
    def __init__(self):
        self.url = ""
        self.body = {}

    async def post(self, url, **kwargs):
        self.url = url
        self.body = kwargs.get("json") or {}
        return FakeResponse()

class ProviderContracts(unittest.TestCase):
    def test_genspark_catalog_helpers_use_application_state(self):
        from app.providers.genspark import GensparkProvider

        provider = GensparkProvider()
        provider.client.models_config = AsyncMock(return_value={"models": []})

        async def run():
            with patch("app.state.state.usable_accounts", return_value=[]):
                sections = await provider.refresh_models_config()
                self.assertIsInstance(sections, dict)
                with self.assertRaises(Exception) as raised:
                    await provider.discover_models()
                self.assertNotIsInstance(raised.exception, NameError)

        asyncio.run(run())

    def test_account_public_dict_recursively_redacts_credentials(self):
        secrets = {
            "access": "access-value-7bf5",
            "refresh": "refresh-value-28c1",
            "client": "client-secret-64e9",
            "cookie": "session-cookie-901d",
            "authorization": "Bearer oauth-value-47aa",
            "credential": "nested-credential-b719",
            "session": "session-id-c2d8",
            "password": "password-21ac",
            "oauth_state": "oauth-state-1fc4",
            "session_state": "session-state-69da",
            "api_key": "api-key-f0ea",
            "field_auth": "Basic field-auth-3c8d",
            "proxy_user": "proxy-user-a51c",
            "proxy_password": "proxy-pass-f583",
        }
        account = Account(
            "kiro",
            {
                "region": "eu-central-1",
                "API-Key": secrets["api_key"],
                "headers": [{"authorization": secrets["field_auth"], "accept": "application/json"}],
            },
            proxy=(f"socks5://{secrets['proxy_user']}:{secrets['proxy_password']}"
                   "@proxy.example:1080"),
            extra={
                "tokens": {
                    "accessToken": secrets["access"],
                    "refresh_Token": secrets["refresh"],
                    "CLIENT-SECRET": secrets["client"],
                    "expiresAt": 2_000_000_000,
                    "authMethod": "builder",
                },
                "oauth": {
                    "cookie": secrets["cookie"],
                    "Authorization": secrets["authorization"],
                    "scope": ["openid", "profile"],
                },
                "profiles": [{
                    "AUTH.CREDENTIAL": secrets["credential"],
                    "Session-ID": secrets["session"],
                    "Session.State": secrets["session_state"],
                    "password": secrets["password"],
                    "display_name": "Work profile",
                }],
                "transport": [{"coo-kie": secrets["cookie"], "retry": 3}],
                "oauth_state": secrets["oauth_state"],
                "metadata": {
                    "region": "eu", "token_count": 17, "author": "Ada",
                    "auth_method": "oauth", "tokenizer": "cl100k_base",
                    "cookie_consent": True, "session_name": "interactive",
                },
            },
        )

        for mask in (True, False):
            with self.subTest(mask=mask):
                public = account.public_dict(mask=mask)
                encoded = json.dumps(public, ensure_ascii=False)
                for secret in secrets.values():
                    self.assertNotIn(secret, encoded)
                self.assertEqual(public["proxy"], "socks5://proxy.example:1080")
                self.assertEqual(public["fields"]["API-Key"], "[REDACTED]")
                self.assertEqual(public["fields"]["headers"][0]["authorization"], "[REDACTED]")
                self.assertEqual(public["fields"]["headers"][0]["accept"], "application/json")
                self.assertEqual(public["extra"]["tokens"], "[REDACTED]")
                self.assertEqual(public["extra"]["oauth"], "[REDACTED]")
                self.assertEqual(public["extra"]["oauth_state"], "[REDACTED]")
                self.assertEqual(public["extra"]["transport"],
                                 [{"coo-kie": "[REDACTED]", "retry": 3}])
                self.assertEqual(public["extra"]["profiles"][0]["Session.State"], "[REDACTED]")
                self.assertEqual(public["extra"]["profiles"][0]["display_name"], "Work profile")
                self.assertEqual(public["extra"]["metadata"],
                                 {"region": "eu", "token_count": 17, "author": "Ada",
                                  "auth_method": "oauth", "tokenizer": "cl100k_base",
                                  "cookie_consent": True, "session_name": "interactive"})

        self.assertEqual(account.extra["tokens"]["accessToken"], secrets["access"])
        self.assertEqual(account.proxy,
                         f"socks5://{secrets['proxy_user']}:{secrets['proxy_password']}"
                         "@proxy.example:1080")

    def test_image_detection(self):
        messages = [{"role": "user", "content": [
            {"type": "text", "text": "describe"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,YQ=="}},
        ]}]
        self.assertTrue(messages_have_images(messages))

    def test_genspark_fable_modes_are_not_chat_models(self):
        self.assertFalse(is_chat_model("claude-fable-5"))
        self.assertFalse(is_chat_model("claude-fable-5-1"))
        self.assertTrue(is_chat_model("claude-opus-5"))

    def test_custom_key_pool_rotation_policy(self):
        provider = CustomProvider()
        account = Account("custom", {
            "name": "pool", "base_url": "https://api.test/v1",
            "api_keys": "sk-one\nsk-two", "key_pool_mode": "failover-on-match",
            "key_error_keywords": "balance is too low",
        })
        self.assertEqual(set(provider._key_candidates(account)), {"sk-one", "sk-two"})
        self.assertTrue(provider._matches_key_failure(account, "API key balance is too low"))
        self.assertFalse(provider._matches_key_failure(account, "Too many requests, try again"))
        with patch("app.state.state.save_accounts"):
            provider._disable_key(account, "sk-one")
        self.assertEqual(provider._key_candidates(account), ["sk-two"])
        self.assertNotIn("sk-one", json.dumps(account.public_dict(), ensure_ascii=False))

    def test_storage_accepts_pathlike(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "nested" / "state.json"
            save_json(target, {"ok": True}, backup=False)
            self.assertEqual(load_json_with_recovery(target, {}), {"ok": True})

    def test_custom_images_api(self):
        session = FakeSession()
        account = Account("custom", {"name": "local", "base_url": "https://api.test/v1",
                                      "api_key": "secret"})

        async def run():
            with patch("app.providers.custom.http.get_session", return_value=session):
                result = await CustomProvider().generate_images(account, "cat", "gpt-image-2", "1:1")
            self.assertEqual(result, ["data:image/png;base64,YWJj"])
            self.assertEqual(session.url, "https://api.test/v1/images/generations")
            self.assertEqual(session.body["model"], "gpt-image-2")

        asyncio.run(run())

if __name__ == "__main__":
    unittest.main()
