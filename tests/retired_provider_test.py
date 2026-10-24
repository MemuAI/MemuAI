"""A removed provider cannot route requests or erase older account data."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import config, providers, state as state_module
from app.state import Account, ModelEntry, State


class RetiredProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.accounts = [
            {"id": "retired-account", "provider": "opencode", "enabled": True,
             "fields": {"mode": "free", "api_key": "private-fixture"},
             "extra": {"models": {"old-model": {"available": True}}}, "future_field": {"keep": True}},
            {"id": "live-account", "provider": "custom", "enabled": True,
             "fields": {"name": "my-api", "base_url": "https://api.example/v1"}},
        ]
        self.models = [
            {"prefix": "opencode", "provider": "opencode", "model": "old-model", "enabled": True},
            {"prefix": "my-api", "provider": "custom", "model": "live-model", "enabled": True},
        ]
        self.account_path = self.root / "accounts.json"
        self.model_path = self.root / "models.json"
        self.account_path.write_text(json.dumps(self.accounts), encoding="utf-8")
        self.model_path.write_text(json.dumps(self.models), encoding="utf-8")
        self.patches = [patch.object(state_module, "ACCOUNTS_PATH", str(self.account_path)),
                        patch.object(state_module, "MODELS_PATH", str(self.model_path)),
                        patch.object(State, "_import_legacy"), patch.object(State, "_load_logs")]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.store = State()

    def test_old_configuration_loads_but_retired_accounts_and_models_are_inert(self):
        self.assertEqual([a["id"] for a in self.store.list_accounts()], ["live-account"])
        self.assertEqual([m["model"] for m in self.store.list_models()], ["live-model"])
        self.assertEqual(self.store.usable_accounts("opencode"), [])
        self.assertIsNone(self.store.get_account("retired-account"))
        self.assertIsNone(self.store.find_model("opencode-old-model"))
        self.assertFalse(Account("opencode", fields={}).usable())

    def test_loading_is_read_only_and_saving_preserves_retired_rows_exactly(self):
        self.assertEqual(json.loads(self.account_path.read_text()), self.accounts)
        self.assertEqual(json.loads(self.model_path.read_text()), self.models)
        self.store.accounts["live-account"].label = "Changed"
        self.store.save_accounts()
        self.store.save_models()
        accounts = json.loads(self.account_path.read_text())
        models = json.loads(self.model_path.read_text())
        self.assertEqual(next(a for a in accounts if a["id"] == "retired-account"), self.accounts[0])
        self.assertEqual(next(m for m in models if m["provider"] == "opencode"), self.models[0])
        reloaded = State()
        self.assertEqual([a["id"] for a in reloaded.list_accounts()], ["live-account"])
        self.assertEqual(reloaded.accounts["live-account"].label, "Changed")

    def test_retired_default_provider_is_cleared_without_touching_other_settings(self):
        result = config._normalized_settings({"default_provider": "opencode", "port": 9000,
                                              "provider_settings": {"opencode": {"keep": True}}})
        self.assertEqual(result["default_provider"], "")
        self.assertEqual(result["port"], 9000)
        self.assertEqual(result["provider_settings"]["opencode"], {"keep": True})

    def test_provider_cannot_be_created_or_reintroduced_through_seeding(self):
        self.assertNotIn("opencode", providers.builtin_ids())
        with self.assertRaises(ValueError):
            self.store.add_account("opencode", {})
        self.assertEqual(self.store.seed_provider_models("opencode", "opencode", [("old-model", "")]), 0)
        entry = ModelEntry("opencode", "opencode", "injected-model")
        self.store.models[entry.id] = entry
        self.assertNotIn(entry.id, [m.id for m in self.store.enabled_models()])

    def test_routing_ignores_old_provider_and_keeps_custom_api_usable(self):
        from app import routing
        with patch.object(routing, "state", self.store), \
             patch.object(config.config_store.settings, "default_provider", ""):
            self.assertNotIn("opencode", routing.known_prefixes())
            self.assertIsNone(routing.resolve("opencode-old-model"))
            self.assertEqual(routing.resolve("my-api-live-model").provider, "custom")
        with patch.object(routing, "state", self.store), \
             patch.object(config.config_store.settings, "default_provider", "my-api"):
            self.assertIsNone(routing.resolve("opencode-old-model"))
            self.assertEqual(routing.resolve("live-model").provider, "custom")

    def test_old_usage_panel_configuration_does_not_block_remaining_accounts(self):
        from app.panels_usage import clean_accounts
        rows = clean_accounts([{"id": "a_12345678", "provider": "opencode", "mode": "proxy"},
                               {"id": "a_abcdefgh", "provider": "kiro", "mode": "proxy"}])
        self.assertEqual([row["provider"] for row in rows], ["kiro"])

    def test_external_cli_import_is_still_available(self):
        from app.cli.foreign import SOURCES
        self.assertIn("opencode", SOURCES)

    def test_proxy_api_starts_with_old_data_and_obsolete_routes_are_gone(self):
        from fastapi.testclient import TestClient
        from app import admin_api, main
        with patch.object(admin_api, "state", self.store), patch.object(main, "state", self.store), \
             patch.object(admin_api, "_auth", return_value=None), patch.dict(providers._providers, {}, clear=True):
            client = TestClient(main.create_app(), base_url="http://127.0.0.1:8317")
            try:
                self.assertEqual(client.get("/health").status_code, 200)
                accounts = client.get("/api/admin/accounts")
                self.assertEqual(accounts.status_code, 200)
                self.assertEqual([a["id"] for a in accounts.json()["accounts"]], ["live-account"])
                meta = client.get("/api/admin/providers")
                self.assertEqual(meta.status_code, 200)
                self.assertNotIn("opencode", [p["id"] for p in meta.json()["providers"]])
                self.assertEqual(client.get("/api/admin/opencode/runtime").status_code, 404)
                self.assertEqual(client.post("/api/admin/accounts/retired-account/check").status_code, 404)
            finally:
                client.close()


if __name__ == "__main__":
    unittest.main()
