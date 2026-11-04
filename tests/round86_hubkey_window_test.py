"""Hub-key lockout of the app's own window (workspaces).

Bug: with a hub API key configured (Settings → API), the window could not
create any workspace — POST /api/spaces answered 401 «Неверный API-ключ хаба.»
— and GET /api/spaces failed the same way, so the nav fell back to showing
only the built-in «standard» space and the user's agent space looked deleted
(the data itself was never touched). Cause: the UI sends no auth header, and
``authorize_api_request`` demanded the key whenever one was set, even from the
app's own window. Fix: a request from the loopback peer, for a loopback Host,
with a same-authority Origin (or none) and a non-cross-site fetch is the
window itself and passes without the key; every other caller (including the
Proxy's /v1, which never passes ``peer``) still needs the key.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import pgdata
from app.config import ConfigStore, Settings
from app.playground_api import create_app


def _store_with_keys() -> ConfigStore:
    store = ConfigStore.__new__(ConfigStore)
    store.settings = Settings(host="127.0.0.1", api_keys=["hub-secret-123"])
    return store


WINDOW = {"host": "127.0.0.1:8601", "origin": "http://127.0.0.1:8601",
          "sec-fetch-site": "same-origin"}


class AuthorizeWindowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = _store_with_keys()

    def test_window_passes_keyless_with_a_hub_key_set(self) -> None:
        self.assertTrue(self.store.authorize_api_request(
            None, host="127.0.0.1:8601", origin="http://127.0.0.1:8601",
            fetch_site="same-origin", peer="127.0.0.1"))
        # GETs from the window carry no Origin header; still the window.
        self.assertTrue(self.store.authorize_api_request(
            None, host="127.0.0.1:8601", peer="127.0.0.1"))

    def test_window_passes_even_with_a_wrong_key(self) -> None:
        self.assertTrue(self.store.authorize_api_request(
            "stale-key", host="127.0.0.1:8601", origin="http://127.0.0.1:8601",
            fetch_site="same-origin", peer="127.0.0.1"))

    def test_remote_peer_cannot_forge_the_window(self) -> None:
        # A LAN caller forging loopback Host/Origin still has a LAN peer.
        self.assertFalse(self.store.authorize_api_request(
            None, host="127.0.0.1:8601", origin="http://127.0.0.1:8601",
            fetch_site="same-origin", peer="192.168.1.5"))
        # A loopback peer with a foreign origin is not the window either.
        self.assertFalse(self.store.authorize_api_request(
            None, host="127.0.0.1:8601", origin="https://evil.example",
            fetch_site="cross-site", peer="127.0.0.1"))
        self.assertFalse(self.store.authorize_api_request(
            None, host="127.0.0.1:8601", origin="https://evil.example",
            peer="127.0.0.1"))
        # A loopback peer asking for a LAN host is not the window.
        self.assertFalse(self.store.authorize_api_request(
            None, host="192.168.1.5:8601", origin="http://192.168.1.5:8601",
            peer="127.0.0.1"))

    def test_proxy_callers_without_peer_still_need_the_key(self) -> None:
        self.assertFalse(self.store.authorize_api_request(
            None, host="127.0.0.1:8317", origin="http://127.0.0.1:8317"))
        self.assertTrue(self.store.authorize_api_request(
            "hub-secret-123", host="127.0.0.1:8317"))
        self.assertFalse(self.store.authorize_api_request(
            "wrong", host="127.0.0.1:8317"))

    def test_no_keys_window_still_passes(self) -> None:
        store = ConfigStore.__new__(ConfigStore)
        store.settings = Settings(host="127.0.0.1", api_keys=[])
        self.assertTrue(store.authorize_api_request(
            None, host="127.0.0.1:8601", origin="http://127.0.0.1:8601",
            fetch_site="same-origin", peer="127.0.0.1"))


class SpacesWindowEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._patch_dir = patch.object(pgdata, "MEMU_DIR", Path(self._tmp.name))
        self._patch_dir.start()
        from app import spaces as spaces_module
        spaces_module._CACHE.clear()
        from app.config import config_store
        self._store = config_store
        self._saved_keys = list(config_store.settings.api_keys)
        config_store.settings.api_keys = ["hub-secret-123"]
        self.client = TestClient(create_app(), raise_server_exceptions=False)

    def tearDown(self) -> None:
        from app import spaces as spaces_module
        spaces_module._CACHE.clear()
        self._store.settings.api_keys = self._saved_keys
        self._patch_dir.stop()
        self._tmp.cleanup()

    def test_window_lists_and_creates_an_agent_space_keyless(self) -> None:
        response = self.client.get("/api/spaces", headers=WINDOW)
        self.assertEqual(response.status_code, 200, response.text[:200])
        self.assertEqual([item["id"] for item in response.json()["items"]],
                         ["standard"])
        created = self.client.post("/api/spaces",
                                   json={"type": "agent", "name": "Мира"},
                                   headers=WINDOW)
        self.assertEqual(created.status_code, 200, created.text[:200])
        space = created.json()["space"]
        self.assertEqual(space["type"], "agent")
        # The space is visible afterwards: nothing was ever deleted, the list
        # simply could not load while the window got 401.
        listed = self.client.get("/api/spaces", headers=WINDOW)
        self.assertEqual(listed.status_code, 200)
        self.assertIn(space["id"], [item["id"] for item in listed.json()["items"]])

    def test_cross_site_and_lan_hosts_still_blocked(self) -> None:
        response = self.client.get("/api/spaces", headers={
            "host": "127.0.0.1:8601", "origin": "https://evil.example",
            "sec-fetch-site": "cross-site"})
        self.assertEqual(response.status_code, 403)
        response = self.client.get("/api/spaces", headers={
            "host": "192.168.1.5:8601", "origin": "http://192.168.1.5:8601",
            "sec-fetch-site": "same-origin"})
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
