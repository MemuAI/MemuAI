"""Account picker / availability regressions; no account, network or paid API access."""
from __future__ import annotations

import asyncio
import copy
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import chat
from app import model_refresh as refresh
from app.providers import antigravity
from app.providers import antigravity_models as names
from app.state import Account, ModelEntry


def catalogue(*, disabled=True):
    return {"models": {
        "gemini-3.7-flash-tiered": {"displayName": "Gemini 3.7 Flash"},
        "gemini-pro-agent": {"displayName": "Gemini 3.1 Pro (High)"},
        "gemini-3.8-flash-tiered": {"displayName": "Gemini 3.8 Flash"},
        "argon-future-request-id": {"displayName": "Gemini 4 Argon (High)", "disabled": disabled},
        "internal-future": {"displayName": "Internal", "isInternal": True},
    }, "agentModelSorts": [{"displayName": "Recommended", "groups": [
        {"displayName": "Gemini", "modelIds": ["gemini-3.8-flash-tiered", "argon-future-request-id", "internal-future"]}]}]}


class PickerTest(unittest.TestCase):
    def setUp(self):
        names.reset_learned()
        self.addCleanup(names.reset_learned)

    def test_account_picker_excludes_named_legacy_registry_entries(self):
        rows = names.from_server(catalogue())
        self.assertNotIn("gemini-3.7-flash", [row.public for row in rows])
        self.assertNotIn("gemini-3.1-pro-high", [row.public for row in rows])
        self.assertNotIn("internal-future", [row.public for row in rows])
        self.assertIn("gemini-3.8-flash", [row.public for row in rows])

    def test_new_disabled_model_keeps_exact_server_request_id(self):
        rows = {row.public: row for row in names.from_server(catalogue())}
        argon = rows["gemini-4-argon-high"]
        self.assertFalse(argon.available)
        self.assertEqual(argon.upstream, "argon-future-request-id")
        self.assertEqual(names.upstream_id(argon.public), "argon-future-request-id")

    def test_quota_exhaustion_does_not_retire_a_model(self):
        data = catalogue(disabled=False)
        data["models"]["argon-future-request-id"]["quotaInfo"] = {"remainingFraction": 0}
        self.assertTrue(next(row for row in names.from_server(data) if row.public == "gemini-4-argon-high").available)

    def test_older_server_without_picker_still_supports_unknown_named_models(self):
        data = {"models": {"new-server-model": {"displayName": "New Frontier"}}}
        self.assertEqual(names.from_server(data)[0].public, "new-server-model")

    def test_available_duplicate_wins_over_disabled_recommended_alias(self):
        data = {"models": {"old": {"displayName": "Future (High)", "recommended": True, "disabled": True},
                           "new": {"displayName": "Future (High)", "disabled": False}}}
        row, = names.from_server(data)
        self.assertEqual(row.upstream, "new")
        self.assertTrue(row.available)

    def test_new_accounts_never_seed_historical_models(self):
        self.assertEqual(antigravity.AntigravityProvider().seed_models(), [])

    def test_new_picker_entry_without_a_display_name_is_still_listed(self):
        data = {"models": {"future-id": {}}, "agentModelSorts": [{"groups": [{"modelIds": ["future-id"]}]}]}
        row, = names.from_server(data)
        self.assertEqual((row.public, row.display), ("future-id", "future-id"))

    def test_picker_preserves_different_ids_sharing_a_title(self):
        data = {"models": {"future-a": {"displayName": "Future"}, "future-b": {"displayName": "Future"}},
                "agentModelSorts": [{"groups": [{"modelIds": ["future-a", "future-b"]}]}]}
        self.assertEqual({row.upstream for row in names.from_server(data)}, {"future-a", "future-b"})

    def test_explicit_current_request_id_wins_over_a_historical_rename(self):
        data = {"models": {"gemini-3.1-pro-high": {"displayName": "Gemini 3.1 Pro (High)"}},
                "agentModelSorts": [{"groups": [{"modelIds": ["gemini-3.1-pro-high"]}]}]}
        row, = names.from_server(data)
        self.assertEqual(names.upstream_id(row.public), "gemini-3.1-pro-high")


class DiscoveryTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        names.reset_learned()
        self.addCleanup(names.reset_learned)
        self.provider = antigravity.AntigravityProvider()
        self.account = Account("antigravity", {}, id="ag-test")
        self.session = SimpleNamespace(post=AsyncMock(return_value=SimpleNamespace(status_code=200, json=catalogue)))
        self.provider._ensure_token = AsyncMock(return_value="test-token")
        self.provider._project_id = AsyncMock(return_value="test-project")
        self.patches = [patch("app.providers.antigravity.http.get_session", AsyncMock(return_value=self.session)),
                        patch("app.state.state.save_accounts_soon"),
                        patch("app.model_info.record"), patch("app.model_capabilities.record_raw")]
        for target in self.patches:
            target.start()
            self.addCleanup(target.stop)
        antigravity._models_checked_at.pop(self.account.id, None)
        self.addCleanup(antigravity._models_checked_at.pop, self.account.id, None)

    async def test_actual_discovery_persists_account_availability_without_credentials(self):
        rows = await self.provider.dynamic_models(self.account)
        self.assertIn("gemini-4-argon-high", rows)
        snapshot = self.account.extra["antigravity_model_catalog"]
        self.assertFalse(snapshot["models"]["gemini-4-argon-high"]["available"])
        self.assertNotIn("test-token", str(snapshot))
        self.assertFalse(self.provider.model_availability("gemini-4-argon-high", [self.account])["available"])

    async def test_saved_server_request_id_survives_a_proxy_restart(self):
        await self.provider.dynamic_models(self.account)
        names.reset_learned()
        restarted = antigravity.AntigravityProvider()
        self.assertEqual(restarted._request_model(self.account, "gemini-4-argon-high"), "argon-future-request-id")
        self.assertEqual(names.upstream_id("gemini-4-argon-high"), "argon-future-request-id")

    async def test_successful_cache_returns_metadata_but_failed_fetch_does_not_poison_it(self):
        first = await self.provider._refresh_models(self.account, "test-token", "test-project", force=True)
        second = await self.provider._refresh_models(self.account, "test-token", "test-project")
        self.assertEqual(first, second)
        self.session.post.assert_awaited_once()
        old = copy.deepcopy(self.account.extra)
        self.session.post.return_value = SimpleNamespace(status_code=503, json=lambda: {})
        self.assertEqual(await self.provider._refresh_models(self.account, "test-token", "test-project", force=True), {})
        self.assertEqual(old, self.account.extra)

    async def test_transient_empty_registry_preserves_last_good_snapshot(self):
        await self.provider.dynamic_models(self.account)
        before = copy.deepcopy(self.account.extra)
        self.session.post.return_value = SimpleNamespace(status_code=200, json=lambda: {"models": {}})
        with self.assertRaises(antigravity.ProviderServerError):
            await self.provider.dynamic_models(self.account)
        self.assertEqual(before, self.account.extra)

    async def test_accounts_have_independent_entitlements_and_unknown_is_not_false(self):
        await self.provider.dynamic_models(self.account)
        second = Account("antigravity", {}, id="ag-second")
        self.assertIsNone(self.provider.model_availability("gemini-4-argon-high", [self.account, second])["available"])
        second.extra["antigravity_model_catalog"] = copy.deepcopy(self.account.extra["antigravity_model_catalog"])
        second.extra["antigravity_model_catalog"]["models"]["gemini-4-argon-high"]["available"] = True
        self.assertTrue(self.provider.model_availability("gemini-4-argon-high", [self.account, second])["available"])
        self.assertFalse(self.provider.account_model_available(self.account, "gemini-4-argon-high"))
        self.assertTrue(self.provider.account_model_available(second, "gemini-4-argon-high"))
        self.assertFalse(self.provider.account_model_available(second, "gemini-3.7-flash"))

    async def test_refresh_removes_ghosts_and_preserves_enabled_choice(self):
        await self.provider.dynamic_models(self.account)
        initial = [ModelEntry("antigravity", "antigravity", "gemini-3.7-flash"),
                   ModelEntry("antigravity", "antigravity", "gemini-3.8-flash", enabled=False),
                   ModelEntry("antigravity", "antigravity", "custom", source="manual")]
        fake = SimpleNamespace(accounts={self.account.id: self.account}, models={m.id: m for m in initial},
                               _lock=threading.RLock(), save_models=lambda: None, prune_orphan_models=lambda wanted: 0)
        ids = [row.public for row in names.from_server(catalogue())]
        with patch.object(refresh, "state", fake), patch.object(refresh, "get_provider", return_value=self.provider):
            refresh.ModelRefresher._apply([(self.account, "antigravity", "antigravity", ids, "")],
                                        [(self.account, "antigravity", "antigravity")], prune=True, wanted=set())
        self.assertNotIn("antigravity-gemini-3.7-flash", fake.models)
        self.assertFalse(fake.models["antigravity-gemini-3.8-flash"].enabled)
        self.assertIn("antigravity-custom", fake.models)
        entry = fake.models["antigravity-gemini-4-argon-high"]
        self.assertTrue(entry.enabled)
        self.assertFalse(entry.availability["available"])
        self.assertFalse(entry.public_dict()["availability"]["available"])


class RoutingTest(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_model_id_cannot_bypass_account_entitlements(self):
        account = Account("antigravity", {}, id="ag-blocked", extra={"antigravity_model_catalog": {
            "checked": time.time(), "models": {"another-model": {"available": True, "upstream": "another-model"}}}})
        resolved = SimpleNamespace(entry=None, provider="antigravity", prefix="antigravity", model="removed")
        service = chat.ChatService()
        provider = antigravity.AntigravityProvider()
        with patch.object(chat, "resolve", return_value=resolved), patch.object(service, "_provider", return_value=provider), \
                patch.object(chat.pool, "ordered_candidates", return_value=[account]), \
                patch.object(chat.config_store.settings, "proxy_enabled", True), \
                patch.object(chat.config_store.settings, "strict_models", False):
            with self.assertRaises(chat.CompletionError) as caught:
                await anext(service.stream_completion("antigravity-removed", [{"role": "user", "content": "hello"}]))
        self.assertEqual(caught.exception.err_type, "model_unavailable")

    async def test_unavailable_catalogue_model_fails_before_any_upstream_request(self):
        entry = ModelEntry("antigravity", "antigravity", "argon", availability={"available": False, "reason": "Plan restriction"})
        resolved = SimpleNamespace(entry=entry, provider="antigravity", prefix="antigravity", model="argon")
        service = chat.ChatService()
        upstream = SimpleNamespace(chat_stream=AsyncMock())
        with patch.object(chat, "resolve", return_value=resolved), patch.object(service, "_provider", return_value=upstream), \
                patch.object(chat, "config_store", SimpleNamespace(settings=SimpleNamespace(proxy_enabled=True))):
            with self.assertRaises(chat.CompletionError) as caught:
                await anext(service.stream_completion(entry.id, [{"role": "user", "content": "hello"}]))
        self.assertEqual(caught.exception.err_type, "model_unavailable")
        upstream.chat_stream.assert_not_called()

    def test_availability_round_trip_keeps_user_enabled_flag(self):
        entry = ModelEntry("antigravity", "antigravity", "argon", enabled=True, availability={"available": False})
        payload = entry.public_dict()
        payload.pop("id")
        restored = ModelEntry(**payload)
        self.assertTrue(restored.enabled)
        self.assertFalse(restored.availability["available"])


class LifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def test_antigravity_refreshes_within_fifteen_minutes_of_a_long_global_interval(self):
        clock, sleeps = [0.0], [0]
        account = Account("antigravity", {}, id="ag-periodic")
        settings = SimpleNamespace(model_refresh_on_start=False, model_refresh_interval=360)
        service = refresh.ModelRefresher()

        async def run(**kwargs):
            service._provider_finished["antigravity"] = clock[0]
            return {"ok": True}

        async def sleep(seconds):
            sleeps[0] += 1
            if sleeps[0] == 4:
                raise asyncio.CancelledError
            clock[0] += seconds if seconds == 1 else 900

        service.refresh = AsyncMock(side_effect=run)
        with patch.object(refresh, "state", SimpleNamespace(all_accounts=lambda: [account])), \
                patch.object(refresh, "config_store", SimpleNamespace(settings=settings)), \
                patch.object(refresh, "time", SimpleNamespace(monotonic=lambda: clock[0])), \
                patch.object(refresh.asyncio, "sleep", side_effect=sleep):
            with self.assertRaises(asyncio.CancelledError):
                await service.run_loop()
        self.assertEqual(service.refresh.await_count, 2)
        service.refresh.assert_awaited_with(providers=["antigravity"], reason="provider-interval")


if __name__ == "__main__":
    unittest.main()
