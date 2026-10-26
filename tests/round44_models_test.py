"""Offline discovery regression tests: union, safe removal, races and lifecycle."""
from __future__ import annotations

import asyncio
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import model_refresh as mr
from app.state import ModelEntry

class Account:
    def __init__(self, account_id, provider="kiro", prefix="", *, enabled=True, status="ok"):
        self.id, self.provider = account_id, provider
        self.fields = {"name": prefix, "api_key": "secret-token-12345"}
        self.extra, self.label = {}, account_id
        self.enabled, self.status = enabled, status

    def usable(self):
        return self.enabled and self.status != "invalid"

class FakeState:
    def __init__(self, accounts, models=()):
        self.accounts = {a.id: a for a in accounts}
        self.models = {m.id: m for m in models}
        self._lock = threading.RLock()
        self.saved = 0

    def all_accounts(self):
        return list(self.accounts.values())

    def save_models(self):
        self.saved += 1

    def prune_orphan_models(self, wanted):
        return 0

class ModelRefreshTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.accounts = [Account("one"), Account("two")]
        self.state = FakeState(self.accounts, [ModelEntry("kiro", "kiro", "retired"),
                                               ModelEntry("kiro", "kiro", "manual", source="manual"),
                                               ModelEntry("kiro", "kiro", "keep", enabled=False, note="My note")])
        self.answers = {"one": ["keep", "new-one"], "two": ["keep", "new-two"]}

        async def discover(account):
            result = self.answers.get(account.id)
            if isinstance(result, BaseException):
                raise result
            return result

        self.provider = SimpleNamespace(dynamic_models=AsyncMock(side_effect=discover), model_note=lambda model: model)
        self.settings = SimpleNamespace(model_hidden_keywords=[], model_refresh_on_start=True, model_refresh_interval=360)
        self.patches = [patch.object(mr, "state", self.state), patch.object(mr, "get_provider", return_value=self.provider),
                        patch.object(mr, "_sync_local_catalog"),
                        patch.object(mr, "config_store", SimpleNamespace(settings=self.settings))]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.service = mr.ModelRefresher(timeout=.1)

    async def test_union_retirement_and_user_preferences(self):
        result = await self.service.refresh()
        self.assertEqual((result["added"], result["removed"], result["scanned"]), (2, 1, 2))
        self.assertEqual(set(self.state.models), {"kiro-manual", "kiro-keep", "kiro-new-one", "kiro-new-two"})
        self.assertFalse(self.state.models["kiro-keep"].enabled)
        self.assertEqual(self.state.models["kiro-keep"].note, "My note")
        self.assertEqual(self.state.saved, 1)

    async def test_one_failed_account_keeps_healthy_catalogue_and_redacts_secrets(self):
        self.answers["two"] = ValueError("api_key=secret-token-12345 rejected")
        result = await self.service.refresh()
        self.assertIn("kiro-retired", self.state.models)
        self.assertIn("kiro-new-one", self.state.models)
        self.assertEqual(result["removed"], 0)
        self.assertNotIn("secret-token", str(result))
        self.assertIn("REDACTED", result["errors"][0])

    async def test_empty_or_unsupported_responses_never_empty_catalogue(self):
        for response in (None, [], {}, [None, "auto", "", "\n"]):
            self.answers.update(one=response, two=response)
            result = await self.service.refresh()
            self.assertEqual(result["added"], 0)
            self.assertIn("kiro-retired", self.state.models)

    async def test_filtered_account_scan_cannot_remove_other_accounts_models(self):
        result = await self.service.refresh(accounts=["one"])
        self.assertEqual(result["scanned"], 1)
        self.assertEqual(result["removed"], 0)
        self.assertIn("kiro-retired", self.state.models)

    async def test_explicit_no_prune_preserves_old_models(self):
        self.assertEqual((await self.service.refresh(prune=False))["pruned"], 0)
        self.assertIn("kiro-retired", self.state.models)

    async def test_disabled_invalid_local_and_provider_filter(self):
        self.accounts[0].enabled = False
        self.accounts[1].status = "invalid"
        for account in [Account("local", "local"), Account("other", "custom", "api")]:
            self.state.accounts[account.id] = account
        result = await self.service.refresh(providers=["kiro"])
        self.assertEqual(result["scanned"], 0)
        self.provider.dynamic_models.assert_not_called()

    async def test_custom_connections_are_independent(self):
        self.state.accounts = {"one": Account("one", "custom", "api-one"), "two": Account("two", "custom", "api-two")}
        self.state.models = {"api-one-old": ModelEntry("api-one", "custom", "old"),
                             "api-two-old": ModelEntry("api-two", "custom", "old")}
        self.answers["two"] = None
        await self.service.refresh()
        self.assertNotIn("api-one-old", self.state.models)
        self.assertIn("api-two-old", self.state.models)
        self.assertEqual(self.state.models["api-one-new-one"].account_id, "one")

    async def test_hidden_keywords_apply_to_new_models(self):
        self.settings.model_hidden_keywords = ["new-"]
        result = await self.service.refresh()
        self.assertEqual(result["hidden"], 2)
        self.assertFalse(self.state.models["kiro-new-one"].enabled)

    async def test_discovery_is_bounded_and_does_not_overlap(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def block(account):
            entered.set()
            await release.wait()
            return ["new"]

        self.provider.dynamic_models.side_effect = block
        self.service.timeout = 2
        task = asyncio.create_task(self.service.refresh())
        await entered.wait()
        self.assertTrue(self.service.status()["running"])
        self.assertTrue((await self.service.refresh())["busy"])
        release.set()
        await task
        self.assertFalse(self.service.status()["running"])

    async def test_timeout_preserves_catalogue(self):
        async def slow(account):
            await asyncio.sleep(1)

        self.provider.dynamic_models.side_effect = slow
        self.service.timeout = .01
        result = await self.service.refresh()
        self.assertEqual(len(result["errors"]), 2)
        self.assertIn("kiro-retired", self.state.models)

    async def test_cancel_cancels_children_without_applying_partial_results(self):
        active = []

        async def block(account):
            active.append(account.id)
            try:
                await asyncio.sleep(100)
            finally:
                active.remove(account.id)

        self.provider.dynamic_models.side_effect = block
        task = asyncio.create_task(self.service.refresh())
        for _ in range(10):
            await asyncio.sleep(0)
            if len(active) == 2:
                break
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(active, [])
        self.assertEqual(self.state.saved, 0)
        self.assertFalse(self.service.status()["running"])

    async def test_account_removed_or_renamed_during_discovery_cannot_repopulate(self):
        self.accounts[0].provider = "custom"
        self.accounts[0].fields["name"] = "before"

        async def alter(account):
            if account.id == "one":
                account.fields["name"] = "after"
            else:
                self.state.accounts.pop(account.id)
            return ["unexpected"]

        self.provider.dynamic_models.side_effect = alter
        await self.service.refresh()
        self.assertNotIn("after-unexpected", self.state.models)
        self.assertNotIn("before-unexpected", self.state.models)
        self.assertNotIn("kiro-unexpected", self.state.models)

    async def test_parallelism_is_limited(self):
        self.state.accounts = {str(i): Account(str(i)) for i in range(8)}
        active = peak = 0

        async def discover(account):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(.005)
            active -= 1
            return ["new"]

        self.provider.dynamic_models.side_effect = discover
        await self.service.refresh()
        self.assertEqual(peak, 3)

    async def test_loop_on_start_then_interval_and_cancel(self):
        clock = [0.0]
        delays = []

        async def sleep(delay):
            delays.append(delay)
            if len(delays) == 4:
                raise asyncio.CancelledError
            clock[0] += delay

        self.settings.model_refresh_interval = 0
        fake_time = SimpleNamespace(monotonic=lambda: clock[0], time=time.time, strftime=time.strftime)
        with patch.object(mr.asyncio, "sleep", side_effect=sleep), patch.object(mr, "time", fake_time):
            with self.assertRaises(asyncio.CancelledError):
                await self.service.run_loop()
        self.assertEqual(self.provider.dynamic_models.await_count, 2)
        self.assertEqual(self.service.status()["last"]["reason"], "startup")

    async def test_loop_disabled_does_not_query(self):
        self.settings.model_refresh_on_start = False
        self.settings.model_refresh_interval = 0

        async def stop(delay):
            if delay == 10:
                raise asyncio.CancelledError

        with patch.object(mr.asyncio, "sleep", side_effect=stop):
            with self.assertRaises(asyncio.CancelledError):
                await self.service.run_loop()
        self.provider.dynamic_models.assert_not_called()

    async def test_local_catalog_uses_existing_manager_and_failure_does_not_block_remote(self):
        with patch.object(mr, "_sync_local_catalog", side_effect=ValueError("local catalogue unavailable")) as sync:
            result = await self.service.refresh()
        sync.assert_called_once()
        self.assertIn("kiro-new-one", self.state.models)
        self.assertIn("local:", result["errors"][-1])

    async def test_remote_filtered_refresh_does_not_touch_local_models(self):
        with patch.object(mr, "_sync_local_catalog") as sync:
            await self.service.refresh(providers=["kiro"])
            await self.service.refresh(accounts=["one"])
        sync.assert_not_called()

    async def test_admin_refresh_and_status_use_shared_service(self):
        from fastapi import FastAPI
        import httpx
        from app import admin_api
        app = FastAPI()
        app.include_router(admin_api.router)
        mr.register(app)
        with patch.object(mr, "refresher", self.service):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
                result = await client.post("/api/admin/models/refresh", json={"providers":["kiro"],"accounts":["one"],"prune":False})
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.json()["scanned"], 1)
                status = await client.get("/api/admin/models/auto-refresh")
                self.assertEqual(status.status_code, 200)
                self.assertEqual(status.json()["last"]["added"], 1)
                self.assertFalse(status.json()["running"])

    async def test_admin_rejects_malformed_filters_and_cross_origin_without_querying(self):
        from fastapi import FastAPI
        import httpx
        from app import admin_api
        app = FastAPI()
        app.include_router(admin_api.router)
        mr.register(app)
        with patch.object(mr, "refresher", self.service):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
                for body in [{"providers":123},{"providers":"kiro"},{"accounts":{}},{"accounts":[2]},{"prune":"false"},{"prune":None}]:
                    result = await client.post("/api/admin/models/refresh",json=body)
                    self.assertEqual(result.status_code,400,(body,result.text))
                result = await client.get("/api/admin/models/auto-refresh",headers={"Origin":"https://example.com"})
                self.assertEqual(result.status_code,403)
        self.provider.dynamic_models.assert_not_called()

if __name__ == "__main__":
    unittest.main()
