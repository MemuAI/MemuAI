"""API balance snapshots, shared tariffs, accounting and authenticated HTTP flow.

No real account, key or network service is used. Amounts are USD / 1M tokens.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from app import api_budget as budget, pricing, quota_planner
from app.state import Account, ModelEntry, state


MODEL = "claude-opus-5.5"
RATES = {"input": 2, "output": 10, "cache_read": .1, "cache_write": 2.5, "caching": True}


class BudgetCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.target = Path(self.tmp.name) / "memuai" / "api-budgets.json"
        self.patch = patch.object(budget, "path", return_value=self.target)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        budget._cached_stamp = None
        self.account = Account("custom", {"name": "api", "api_key": "NEVER-EXPOSE-THIS-KEY"}, id="a", label="My API")
        self.other = Account("custom", {"name": "other"}, id="b", label="Other API")
        self.state_patch = patch.object(state, "accounts", {"a": self.account, "b": self.other})
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)
        self.models_patch = patch.object(state, "models", {
            "api-" + MODEL: ModelEntry("api", "custom", MODEL),
            "other-" + MODEL: ModelEntry("other", "custom", MODEL),
            "private": ModelEntry("api", "custom", "private-model", account_id="b"),
        })
        self.models_patch.start()
        self.addCleanup(self.models_patch.stop)

    def add(self, **extra):
        return budget.save_account(self.account, {"balance_usd": 10, "models": {MODEL: dict(RATES)}, **extra})


class PriceTests(BudgetCase):
    def test_account_scope_and_cli_public_alias(self):
        self.add()
        self.assertEqual(pricing.lookup("api-" + MODEL).source, "account_override")
        self.assertEqual(pricing.lookup("api-" + MODEL).input, 2)
        self.assertNotEqual(pricing.lookup("other-" + MODEL).source, "account_override")
        self.assertNotEqual(pricing.lookup(MODEL).source, "account_override")
        self.assertNotEqual(pricing.lookup("api-" + MODEL, account_id="unknown").source, "account_override")

    def test_clamp_overlapping_cache_usage_and_cache_off(self):
        self.add()
        estimate = pricing.estimate("api-" + MODEL, input_tokens=100, output_tokens=10,
                                    cache_read=150, cache_write=99, account_id="a")
        self.assertAlmostEqual(estimate.cost, .00011)
        self.assertEqual(estimate.breakdown["cache_write"], 0)
        budget.save_account(self.account, {"models": {MODEL: {**RATES, "caching": False}}})
        estimate = pricing.estimate("api-" + MODEL, input_tokens=100, output_tokens=10,
                                    cache_read=80, account_id="a")
        self.assertAlmostEqual(estimate.cost, .0003)

    def test_empty_cache_price_is_full_input_not_invented_discount(self):
        self.add(models={MODEL: {"input": 2, "output": 10}})
        result = pricing.estimate("api-" + MODEL, input_tokens=100, cache_read=100, account_id="a")
        self.assertAlmostEqual(result.cost, .0002)

    def test_nonfinite_negative_boolean_and_missing_prices_rejected(self):
        for value in (-1, float("nan"), float("inf"), True, "oops", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.add(models={MODEL: {**RATES, "input": value}})
        self.assertFalse(self.target.exists())
        for value in (-1, float("nan"), float("inf"), True):
            with self.subTest(balance=value), self.assertRaises(ValueError):
                self.add(balance_usd=value)

    def test_zero_prices_are_valid_and_future_planning_date(self):
        self.add(balance_usd=0, models={MODEL: {"input": 0, "output": 0}})
        self.assertEqual(pricing.lookup("api-" + MODEL).input, 0)
        with self.assertRaises(ValueError):
            budget.save_account(self.account, {"until": time.time() - 1})

    def test_reset_tariff_returns_to_public_prices_without_resetting_period(self):
        original = self.add()
        budget.record_usage(self.account, "api-" + MODEL, {"prompt_tokens": 100, "completion_tokens": 100})
        budget.save_account(self.account, {"models": {MODEL: None}})
        saved = budget.load()["accounts"]["a"]
        self.assertEqual(saved["requests"], 1)
        self.assertEqual(saved["started_at"], original["started_at"])
        self.assertNotEqual(pricing.lookup("api-" + MODEL).source, "account_override")

    def test_model_detection_binding_and_no_credentials(self):
        self.add()
        view = budget.view([self.account, self.other])
        model = view["accounts"][0]["models"][0]
        self.assertEqual(model["label"], "Claude Opus 5.5")
        self.assertEqual(model["canonical"], MODEL)
        self.assertEqual(len(view["accounts"][0]["models"]), 1)
        self.assertEqual(view["available"][0]["id"], "b")
        self.assertNotIn("NEVER-EXPOSE", json.dumps(view))
        self.assertNotIn("api_key", self.target.read_text())

    def test_refresh_does_not_overwrite_custom_tariff(self):
        self.add()
        with patch.object(pricing, "_catalog_rows", return_value=[{"id": "anthropic/" + MODEL,
                                                                 "price": {"input": 100, "output": 500}}]):
            self.assertEqual(pricing.lookup("api-" + MODEL).input, 2)

    def test_changed_file_reloads_without_process_restart(self):
        self.add()
        self.assertEqual(pricing.lookup("api-" + MODEL).input, 2)
        data = json.loads(self.target.read_text())
        data["accounts"]["a"]["models"][MODEL]["input"] = 1.23456
        self.target.write_text(json.dumps(data))
        self.assertEqual(pricing.lookup("api-" + MODEL).input, 1.23456)

    def test_same_prefix_conflicting_tariffs_never_become_global(self):
        self.add()
        second = Account("custom", {"name": "api"}, id="b")
        state.accounts["b"] = second
        budget.save_account(second, {"balance_usd": 2, "models": {MODEL: {**RATES, "input": 99}}})
        self.assertIsNone(budget.price_for("api-" + MODEL))
        self.assertEqual(budget.price_for("api-" + MODEL, account_id="a").input, 2)
        self.assertEqual(budget.price_for("api-" + MODEL, account_id="b").input, 99)

    def test_untracked_account_in_same_pool_prevents_global_price_leak(self):
        self.add()
        state.accounts["b"] = Account("custom", {"name": "api"}, id="b")
        self.assertIsNone(budget.price_for("api-" + MODEL))
        self.assertEqual(budget.price_for("api-" + MODEL, account_id="a").input, 2)
        state.accounts.pop("a")
        state.accounts.pop("b")
        self.assertIsNone(budget.price_for("api-" + MODEL))

    def test_renamed_connection_still_uses_account_tariff_on_actual_usage(self):
        self.add()
        self.account.fields["name"] = "renamed"
        usage = {"prompt_tokens": 1000, "completion_tokens": 100}
        budget.apply_usage_price(self.account, "renamed-" + MODEL, usage)
        budget.record_usage(self.account, "renamed-" + MODEL, usage)
        self.assertAlmostEqual(usage["cost"], .003)
        self.assertIn(MODEL, budget.load()["accounts"]["a"]["usage_models"])

    def test_direct_cli_shared_rate_overrides_reported_cost_without_touching_proxy_ledger(self):
        from app.cli.llm import Usage
        from app.cli.runtime import Stats
        self.add()
        self.assertEqual(pricing.lookup("@api:" + MODEL).source, "account_override")
        stats = Stats()
        usage = Usage(input=1000, output=100, cost=99, requests=1)
        stats.add_usage("@api:" + MODEL, usage, provider="api")
        self.assertEqual(usage.cost, 99, "do not mutate another caller's usage object")
        self.assertAlmostEqual(stats.costs()["actual"], .003)
        budget.save_account(self.account, {"models": {MODEL: {**RATES, "input": 9}}})
        self.assertAlmostEqual(stats.costs()["actual"], .003, "a price edit cannot rewrite prior spend")
        self.assertAlmostEqual(stats.costs()["api_equivalent"], .003)
        self.assertEqual(budget.load()["accounts"]["a"]["requests"], 0, "direct requests bypass Proxy; ledger is Proxy-only")

    def test_invalid_legacy_overrides_and_remote_prices_cannot_produce_nan(self):
        self.assertIsNone(pricing._price_from_row({"input": float("nan"), "output": 2}, "test", "x"))
        self.assertIsNone(pricing._price_from_row({"input": -1, "output": 2}, "test", "x"))
        row = pricing._slim_openrouter({"id": "x", "pricing": {"prompt": "NaN", "completion": "Infinity"}})
        self.assertIsNone(row["price"]["input"])
        self.assertIsNone(row["price"]["output"])


class LedgerTests(BudgetCase):
    def test_usage_override_preserves_provider_charge_and_charges_once(self):
        self.add()
        usage = {"prompt_tokens": 1000, "completion_tokens": 500,
                 "prompt_tokens_details": {"cached_tokens": 400}, "cost": 1.25}
        budget.apply_usage_price(self.account, "api-" + MODEL, usage)
        expected = (600 * 2 + 400 * .1 + 500 * 10) / 1e6
        self.assertAlmostEqual(usage["cost"], expected)
        self.assertEqual(usage["provider_cost"], 1.25)
        budget.record_usage(self.account, "api-" + MODEL, usage)
        saved = budget.load()["accounts"]["a"]
        self.assertAlmostEqual(saved["spent_usd"], expected)
        self.assertEqual(saved["requests"], 1)

    def test_new_balance_is_explicit_snapshot_and_price_edits_keep_spend(self):
        first = self.add()
        budget.record_usage(self.account, "api-" + MODEL, {"prompt_tokens": 1_000_000})
        budget.save_account(self.account, {"models": {MODEL: {**RATES, "input": 1}}})
        saved = budget.load()["accounts"]["a"]
        self.assertEqual(saved["spent_usd"], 2)
        self.assertEqual(saved["started_at"], first["started_at"])
        budget.save_account(self.account, {"balance_usd": 8})
        saved = budget.load()["accounts"]["a"]
        self.assertEqual(saved["spent_usd"], 0)
        self.assertEqual(saved["models"][MODEL]["input"], 1)

    def test_unknown_usage_is_visible_not_free_and_provider_charge_is_known(self):
        self.add()
        budget.record_usage(self.account, "api-unlisted-mystery", {"prompt_tokens": 50})
        budget.record_usage(self.account, "api-unlisted-mystery", {"prompt_tokens": 50, "cost": .25})
        saved = budget.load()["accounts"]["a"]
        self.assertEqual(saved["unknown_requests"], 1)
        self.assertEqual(saved["spent_usd"], .25)
        self.assertIn("unlisted-mystery", saved["usage_models"])

    def test_live_price_change_does_not_reprice_already_observed_spend(self):
        self.add()
        budget.record_usage(self.account, "api-" + MODEL, {"prompt_tokens": 1_000_000})
        budget.save_account(self.account, {"models": {MODEL: {**RATES, "input": .5}}})
        budget.record_usage(self.account, "api-" + MODEL, {"prompt_tokens": 1_000_000})
        self.assertEqual(budget.load()["accounts"]["a"]["spent_usd"], 2.5)

    def test_parallel_request_accounting_is_not_lost(self):
        self.add()
        def record(_):
            budget.record_usage(self.account, "api-" + MODEL, {"prompt_tokens": 1000})
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(record, range(32)))
        saved = budget.load()["accounts"]["a"]
        self.assertEqual(saved["requests"], 32)
        self.assertAlmostEqual(saved["spent_usd"], .064)
        self.assertEqual(saved["usage_models"][MODEL]["input_tokens"], 32000)

    def test_proxy_and_cli_processes_share_one_atomic_ledger(self):
        self.add()
        env = {**os.environ, "MEMUAI_DATA_DIR": str(self.target.parent.parent), "PYTHONUTF8": "1"}
        code = ("from app import api_budget as b; from app.state import Account; "
                "a=Account('custom', {'name':'api'}, id='a'); "
                "[b.record_usage(a, 'api-claude-opus-5.5', {'prompt_tokens':1000}) for _ in range(6)]")
        processes = [subprocess.Popen([sys.executable, "-c", code], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                     for _ in range(3)]
        for process in processes:
            try:
                stdout, stderr = process.communicate(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                self.fail("Budget accounting subprocess did not finish")
            self.assertEqual(process.returncode, 0, (stdout + stderr).decode("utf-8", "replace"))
        saved = budget.load()["accounts"]["a"]
        self.assertEqual(saved["requests"], 18)
        self.assertAlmostEqual(saved["spent_usd"], .036)

    def test_balance_pace_requires_time_and_data_and_removal_keeps_account(self):
        self.add(until=time.time() + 86400)
        row = budget.view([self.account])["accounts"][0]
        self.assertIsNone(row["daily_spend_usd"])
        self.assertAlmostEqual(row["daily_allowance_usd"], 10, places=3)
        for _ in range(3):
            budget.record_usage(self.account, "api-" + MODEL, {"prompt_tokens": 1_000_000})
        with patch("app.api_budget.time.time", return_value=time.time() + 86400):
            row = budget.view([self.account])["accounts"][0]
        self.assertAlmostEqual(row["remaining_usd"], 4)
        self.assertAlmostEqual(row["daily_spend_usd"], 6, places=3)
        self.assertAlmostEqual(row["days_left"], 2 / 3, places=3)
        budget.remove_account("a")
        self.assertIn("a", state.accounts)
        self.assertIsNone(budget.price_for("api-" + MODEL))


class HTTPTests(BudgetCase):
    def test_account_setup_models_save_delete_and_authentication(self):
        app = FastAPI()
        quota_planner.register(app)

        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8317") as client:
                from fastapi.responses import JSONResponse
                with patch("app.admin_api._auth", return_value=JSONResponse({"error": "denied"}, status_code=401)):
                    response = await client.post("/api/admin/quota/planner/api/accounts/a", json={"balance_usd": 10})
                    self.assertEqual(response.status_code, 401)
                    self.assertFalse(self.target.exists())
                with patch("app.admin_api._auth", return_value=None):
                    response = await client.get("/api/admin/quota/planner/api/accounts/a/models")
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["models"][0]["label"], "Claude Opus 5.5")
                    response = await client.post("/api/admin/quota/planner/api/accounts/a", json={"balance_usd": 10, "models": {MODEL: RATES}})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["accounts"][0]["remaining_usd"], 10)
                    bad = await client.post("/api/admin/quota/planner/api/accounts/a", json={"balance_usd": -1})
                    self.assertEqual(bad.status_code, 400)
                    missing = await client.post("/api/admin/quota/planner/api/accounts/missing", json={"balance_usd": 1})
                    self.assertEqual(missing.status_code, 404)
                    response = await client.delete("/api/admin/quota/planner/api/accounts/a")
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["accounts"], [])
        asyncio.run(run())


class SharedChatTests(BudgetCase):
    def test_real_chat_usage_to_cli_cost_and_ledger_with_fallback(self):
        from app.chat import ChatService
        from app.config import config_store
        from app.routing import Resolved
        from app.providers.base import ProviderNetworkError
        from app.cli.llm import Usage
        from app.cli.runtime import Stats

        self.add()
        failed = Account("custom", {"name": "api"}, id="failed")
        budget.save_account(failed, {"balance_usd": 10, "models": {MODEL: {**RATES, "input": 9}}})

        class Provider:
            id = "custom"
            async def chat_stream(self, account, *_args, **_kwargs):
                if account.id == "failed":
                    raise ProviderNetworkError("temporary transport failure")
                yield {"type": "text", "text": "Hello"}
                yield {"type": "usage", "prompt_tokens": 1000, "completion_tokens": 500,
                       "cached_tokens": 400, "cache_write_tokens": 200}

        async def run(close_on_usage=False):
            service = ChatService()
            with patch.object(config_store.settings, "proxy_enabled", True), \
                 patch.object(config_store.settings, "metrics_enabled", False), \
                 patch.object(config_store.settings, "strict_models", False), \
                 patch("app.chat.resolve", return_value=Resolved(None, "api", MODEL, "custom")), \
                 patch.object(service, "_provider", return_value=Provider()), \
                 patch("app.chat.pool.ordered_candidates", return_value=[failed, self.account]), \
                 patch("app.chat.pool.report_success"), patch("app.chat.pool.report_failure"):
                # A public alias can differ from the real upstream id. Billing
                # follows routing's selected model, not user-facing spelling.
                stream = service.stream_completion("api-friendly-alias", [{"role": "user", "content": "Hi"}], {"tools_available": True})
                chunks = []
                async for chunk in stream:
                    chunks.append(chunk)
                    if close_on_usage and "usage" in chunk:
                        await stream.aclose()
                        break
            return next(chunk["usage"] for chunk in chunks if "usage" in chunk)

        usage = asyncio.run(run())
        self.assertAlmostEqual(usage["cost"], .00634)
        self.assertEqual(usage["prompt_tokens_details"]["cache_write_tokens"], 200)
        self.assertEqual(usage["cost_source"], "account_override")
        data = budget.load()["accounts"]
        self.assertEqual(data["a"]["requests"], 1)
        self.assertEqual(data["failed"]["requests"], 0)
        stats = Stats()
        stats.add_usage("api-" + MODEL, Usage(input=1000, output=500, cache_read=400, cache_write=200, cost=usage["cost"], requests=1), provider="custom")
        self.assertAlmostEqual(stats.costs()["actual"], .00634)
        asyncio.run(run(close_on_usage=True))
        self.assertEqual(budget.load()["accounts"]["a"]["requests"], 2,
                         "closing after the final usage cannot discard an already completed charge")

    def test_cache_write_survives_custom_provider_and_proxy_cli_stream(self):
        from app.providers import http
        from app.providers.custom import CustomProvider
        from app.cli.llm import ChatRequest, ProxyBackend
        from unittest.mock import AsyncMock
        from types import SimpleNamespace

        usage = {"prompt_tokens": 1000, "completion_tokens": 20,
                 "prompt_tokens_details": {"cached_tokens": 400, "cache_write_tokens": 200}}
        obj = {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": usage}
        lines = ["data: " + json.dumps(obj), "", "data: [DONE]", ""]

        class Response:
            status_code = 200
            headers = {"content-type": "text/event-stream"}
            async def aiter_lines(self):
                for line in lines:
                    yield line
            async def aclose(self):
                pass

        async def run():
            session = SimpleNamespace(post=AsyncMock(return_value=Response()))
            with patch.object(http, "get_session", AsyncMock(return_value=session)):
                account = Account("custom", {"name": "api", "base_url": "http://127.0.0.1:1/v1"})
                events = [event async for event in CustomProvider().chat_stream(account, "m", [], {})]
            self.assertEqual(next(event for event in events if event["type"] == "usage")["cache_write_tokens"], 200)
            backend = ProxyBackend({"proxy_url": "http://127.0.0.1:1"})
            body = "\n".join(lines)
            backend._client = httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})))
            try:
                events = [event async for event in backend.stream(ChatRequest(model="m", system="", messages=[{"role": "user", "content": "hi"}]))]
                self.assertEqual(next(event.usage for event in events if event.kind == "usage").cache_write, 200)
            finally:
                await backend.close()
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
