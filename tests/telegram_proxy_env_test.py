# -*- coding: utf-8 -*-
"""Round 73, wave 3: ``GET /api/pg/telegram/status`` answered 500 on the
verification stand because httpx chokes while parsing a malformed proxy
entry in the environment (a bracketed IPv6 literal in NO_PROXY) when the
Telegram client is constructed. A status probe must degrade, never 500:
the client falls back to ``trust_env=False``, and a probe failure of any
kind leaves the status answer (200, degraded) or a 502 for the explicit
pair action — never an unhandled exception.
"""
from __future__ import annotations

import contextlib
import os
import unittest
from unittest import mock

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import telegram_pairing, telegram_settings_api

TOKEN = "123456789:AAH-proxyenv-proxy-env-token_abcdef"


def _app() -> FastAPI:
    app = FastAPI()
    loopback = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(404, json={})))
    telegram_settings_api.register_telegram_routes(
        app, loopback, proxy_url=lambda: "http://127.0.0.1:9", telegram_client=None)
    return app


class TelegramProxyEnvTest(unittest.IsolatedAsyncioTestCase):
    async def test_telegram_http_survives_broken_no_proxy(self):
        broken = "[fd8b:4f84:7d32:99::1]"
        with mock.patch.dict(os.environ, {"NO_PROXY": broken, "no_proxy": broken}):
            async with telegram_settings_api._telegram_http(None) as client:
                self.assertIsInstance(client, httpx.AsyncClient)
                self.assertFalse(client.trust_env)

    async def test_status_degrades_when_probe_client_fails(self):
        @contextlib.asynccontextmanager
        async def _boom(shared):
            raise RuntimeError("proxy env broken")

        with mock.patch.object(telegram_pairing, "status",
                               return_value={"phase": "offline", "bot": {}}), \
                mock.patch("app.pgdata.load_preferences",
                           return_value={"telegram_bot_token": TOKEN}), \
                mock.patch.object(telegram_settings_api, "_telegram_http", _boom):
            client = TestClient(_app(), raise_server_exceptions=False)
            response = client.get("/api/pg/telegram/status?lang=ru")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body.get("token_error"), "network")
        self.assertEqual(body["bot"], {})

    def test_pair_returns_502_not_500_when_probe_client_fails(self):
        @contextlib.asynccontextmanager
        async def _boom(shared):
            raise RuntimeError("proxy env broken")

        with mock.patch("app.pgdata.load_preferences",
                        return_value={"telegram_bot_token": TOKEN}), \
                mock.patch("app.pgdata.save_preferences", return_value=None), \
                mock.patch.object(telegram_settings_api, "_telegram_http", _boom):
            client = TestClient(_app(), raise_server_exceptions=False)
            response = client.post("/api/pg/telegram/pair", json={"token": TOKEN})
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(response.json().get("code"), "network")


if __name__ == "__main__":
    unittest.main()
