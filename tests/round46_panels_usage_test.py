"""Proxy-backed usage panels never copy tokens or invent subscription limits."""
from __future__ import annotations

import json
import unittest

from app import panels_usage as usage
from app import panels_proxy_usage as source


class ProxyUsage(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.state = {"accounts": [{"id": "kiro1", "provider": "kiro", "label": "Work", "enabled": True, "secret": "must-not-leak"},
                                   {"id": "go1", "provider": "opencode", "label": "OpenCode Go"}]}
        self.planner = {"api_budgets": {"accounts": [{"id": "kiro1", "balance_usd": 20, "spent_usd": 5, "until": 2000000000, "unknown_requests": 2}]},
                        "accounts": [{"id": "go1", "windows": [{"key": "weekly", "remaining": 0.6, "reset": 2000000000}]}]}
        self.data = {"provider": "kiro", "usage": {"subscription": "Pro", "rows": [{"display": "Credits", "used": 25, "limit": 100, "unit": "credits"}], "next_reset": 2000000000, "email": "must-not-publish@example.test"}}
        async def proxy(method, path):
            self.requests.append((method, path))
            if path.endswith("/pg/state"):
                return 200, self.state
            if path.endswith("/quota/planner"):
                return 200, self.planner
            return 200, self.data
        self.old = source._proxy
        source.configure(proxy)

    async def asyncTearDown(self):
        source.configure(self.old)

    def account(self, provider="kiro", target="kiro1"):
        return {"id": "a_12345678", "provider": provider, "mode": "proxy", "proxy_account_id": target}

    async def test_discovery_only_publishes_safe_account_metadata(self):
        result = await source.discover()
        self.assertTrue(result[0]["api_budget"])
        self.assertTrue(result[1]["quota_planner"])
        self.assertNotIn("must-not-leak", json.dumps(result))

    async def test_kiro_credit_fraction_and_no_account_email(self):
        result = await source.fetch(self.account(), None, "ru", 1900000000)
        self.assertEqual(result["limits"][0]["used"], 25)
        self.assertIn("25 / 100", result["limits"][0]["detail"])
        self.assertNotIn("email", json.dumps(result))

    async def test_opencode_uses_windows_and_reset_metadata(self):
        self.data = {"provider": "opencode", "usage": {"plan": "go", "windows": [{"id": "weekly", "used_percent": 37.5, "label": "Неделя", "label_en": "Week", "reset_at": 2000000000}]}}
        result = await source.fetch(self.account("opencode", "go1"), None, "en", 1900000000)
        self.assertEqual(result["limits"][0]["used"], 37.5)
        self.assertEqual(result["limits"][0]["title"], "Week")
        self.assertEqual(result["limits"][0]["resets_at"], 2000000000)

    async def test_api_balance_is_marked_estimated_and_keeps_dollars(self):
        result = await source.fetch(self.account("api_budget"), None, "ru", 1900000000)
        self.assertEqual(result["limits"][0]["used"], 25)
        self.assertIn("$15.0000", result["limits"][0]["detail"])
        self.assertIn("Оценка", result["notice"])
        self.assertIn("2", result["notice"])
        self.assertIsNone(result["limits"][0]["resets_at"], "A planning deadline does not refill the balance")

    async def test_planner_remaining_is_fraction_not_percentage(self):
        result = await source.fetch(self.account("quota_planner", "go1"), None, "en", 1900000000)
        self.assertEqual(result["limits"][0]["used"], 40)

    async def test_unexposed_limits_are_honest_walls(self):
        self.data = {"provider": "opencode", "usage": {"windows": [], "note_en": "No quota API"}}
        with self.assertRaisesRegex(usage.UsageWall, "No quota API"):
            await source.fetch(self.account("opencode", "go1"), None, "en", 1900000000)

    async def test_missing_deleted_and_zero_balance_accounts_are_not_selected(self):
        for account in (self.account(target=""), self.account("api_budget", "deleted"), self.account(target="../escape")):
            with self.subTest(account=account), self.assertRaises(usage.UsageWall):
                await source.fetch(account, None, "ru", 1900000000)
        self.planner["api_budgets"]["accounts"][0]["balance_usd"] = 0
        with self.assertRaises(usage.UsageWall):
            await source.fetch(self.account("api_budget"), None, "ru", 1900000000)

    async def test_provider_reassignment_does_not_show_other_account_data(self):
        self.data["provider"] = "custom"
        with self.assertRaises(usage.UsageWall):
            await source.fetch(self.account(), None, "en", 1900000000)

    async def test_invalid_numbers_do_not_become_zero_percent(self):
        self.data["usage"]["rows"][0]["used"] = float("nan")
        with self.assertRaises(usage.UsageWall):
            await source.fetch(self.account(), None, "en", 1900000000)

    async def test_standard_panel_refresh_and_config_preserve_proxy_selection(self):
        accounts = usage.clean_accounts([self.account()])
        self.assertEqual(accounts[0]["proxy_account_id"], "kiro1")
        self.assertEqual(accounts[0]["mode"], "proxy")
        result = await usage.refresh_accounts(accounts, [], lang="en", now=1900000000)
        self.assertEqual(result["ok"], 1)
        self.assertEqual(result["top_pct"], 25)

    async def test_source_change_discards_other_account_limits_and_cooldown(self):
        first = await usage.refresh_accounts([self.account()], [], lang="en", now=1900000000)
        old = first["accounts"][0]
        old["cooldown_until"] = 1900000500
        self.data = {"provider": "kiro", "usage": {"rows": []}}
        changed = await usage.refresh_accounts([self.account(target="another")], [old], lang="en", now=1900000001)
        self.assertEqual(changed["accounts"][0]["state"], "wall")
        self.assertEqual(changed["accounts"][0]["limits"], [])
        self.assertIsNone(changed["accounts"][0]["cooldown_until"])
        self.assertEqual(self.requests[-1][1], "/api/admin/accounts/another/usage")

    async def test_matching_source_keeps_last_limits_when_temporarily_unavailable(self):
        first = await usage.refresh_accounts([self.account()], [], lang="en", now=1900000000)
        async def unavailable(*_): return 503, {}
        source.configure(unavailable)
        again = await usage.refresh_accounts([self.account()], first["accounts"], lang="en", now=1900000100)
        self.assertEqual(again["accounts"][0]["state"], "stale")
        self.assertEqual(again["accounts"][0]["limits"], first["accounts"][0]["limits"])

    async def test_repeated_cached_reads_keep_spacing_timestamp(self):
        accounts = [self.account()]
        first = await usage.refresh_accounts(accounts, [], lang="en", now=1900000000)
        count = len(self.requests)
        for delta in (5, 10, 15, 20):
            first = await usage.refresh_accounts(accounts, first["accounts"], lang="en", now=1900000000 + delta)
        self.assertEqual(len(self.requests), count, "Returning a cached snapshot must not discard checked time")

    async def test_planner_staleness_and_original_observation_time_survive_refresh(self):
        row = self.planner["accounts"][0]
        row["windows"][0].update(stale=True, last=1899990000, title="Неделя", title_en="Weekly")
        accounts = [self.account("quota_planner", "go1")]
        result = await usage.refresh_accounts(accounts, [], lang="en", now=1900000000)
        value = result["accounts"][0]
        self.assertEqual(value["state"], "stale")
        self.assertEqual(value["fetched"], 1899990000)
        self.assertEqual(value["limits"][0]["title"], "Weekly")
        self.assertIn("outdated", value["notice"])
        self.assertEqual(result["ok"], 0)

    async def test_invalid_planner_fraction_does_not_become_zero_usage(self):
        for fraction in (-0.5, 75, True, float("inf")):
            self.planner["accounts"][0]["windows"][0]["remaining"] = fraction
            with self.subTest(fraction=fraction):
                result = await usage.refresh_accounts([self.account("quota_planner", "go1")], [], lang="en", now=1900000000)
                self.assertEqual(result["accounts"][0]["state"], "wall")
                self.assertEqual(result["accounts"][0]["limits"], [])

    async def test_legacy_unidentified_snapshot_is_refreshed_before_reuse(self):
        previous = [{"id": "a_12345678", "provider": "kiro", "state": "ok", "checked": 1900000000,
                     "limits": [{"title": "Old account", "used": 95}], "cooldown_until": 2000000000}]
        result = await usage.refresh_accounts([self.account()], previous, lang="en", now=1900000001)
        self.assertEqual(result["accounts"][0]["limits"][0]["used"], 25)
        self.assertNotIn("Old account", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
