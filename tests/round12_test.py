"""Round 12: the Proxy no longer puts accounts on a «cooldown» after an error,
the model catalogue forgets providers without accounts, and the Antigravity
provider (real error texts, endpoint memory, model names and thinking levels,
usage with weekly limits).

Everything is offline; nothing here reaches a real provider.
"""
from __future__ import annotations

import os
import tempfile

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

import asyncio  # noqa: E402
import copy  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import patch  # noqa: E402

from app.config import config_store  # noqa: E402
from app.pool import AccountPool  # noqa: E402
from app.providers.base import (  # noqa: E402
    ProviderAuthError,
    ProviderCaptchaError,
    ProviderContentFilterError,
    ProviderModelUnavailableError,
    ProviderNetworkError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderServerError,
)
from app.state import ST_INVALID, ST_OK, ST_UNKNOWN, Account, ModelEntry, state  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

class AccountsAreNeverPausedTest(unittest.TestCase):
    """«Откат»: after an error the account stays in the rotation."""

    def failing(self, error: Exception) -> Account:
        account = Account("custom", {"name": "demo"}, status=ST_OK)
        with patch.object(state, "save_accounts"), patch.object(state, "save_accounts_soon"):
            AccountPool().report_failure(account, error, model="demo-model", ms=12)
        return account

    def test_limits_network_and_server_errors_do_not_pause_the_account(self) -> None:
        for error in (ProviderRateLimitError("HTTP 429: slow down"),
                      ProviderQuotaError("daily quota used up"),
                      ProviderNetworkError("connection reset"),
                      ProviderServerError("upstream exploded", 503),
                      ProviderServerError("bad request", 400)):
            with self.subTest(error=type(error).__name__, status=error.status):
                account = self.failing(error)
                self.assertEqual(account.status, ST_OK)
                self.assertTrue(account.usable())
                self.assertFalse(hasattr(account, "cooldown_until"))
                self.assertIn(str(error), account.status_message)
                self.assertEqual(account.stats["failures"], 1)
                self.assertIn(str(error), account.stats["last_error"])

    def test_a_captcha_request_is_only_remembered(self) -> None:
        account = self.failing(ProviderCaptchaError("captcha"))
        self.assertEqual(account.status, ST_UNKNOWN)
        self.assertTrue(account.usable())

    def test_a_rejected_sign_in_still_switches_the_account_off_until_it_is_fixed(self) -> None:
        account = self.failing(ProviderAuthError("the cookie expired"))
        self.assertEqual(account.status, ST_INVALID)
        self.assertFalse(account.usable())

    def test_a_failed_account_keeps_its_place_in_the_queue(self) -> None:
        first = Account("custom", {"name": "a"}, id="a", status=ST_OK, order=0)
        second = Account("custom", {"name": "b"}, id="b", status=ST_OK, order=1)
        pool = AccountPool()
        with patch.object(state, "accounts", {"a": first, "b": second}), \
                patch.object(state, "save_accounts"), patch.object(state, "save_accounts_soon"), \
                patch.object(config_store.settings, "balancing", "order"):
            pool.report_failure(first, ProviderQuotaError("used up"), model="m")
            order = [account.id for account in pool.ordered_candidates("custom")]
        self.assertEqual(order, ["a", "b"])

    def test_an_account_paused_by_an_older_version_is_an_ordinary_one_again(self) -> None:
        row = {"id": "old", "status": "cooldown", "status_message": "quota exhausted",
               "cooldown_until": time.time() + 86400, "last_used": 5, "stats": {"requests": 3}}
        account = Account("genspark", {"cookie": "session_id=x"}, **row)
        self.assertEqual(account.status, ST_UNKNOWN)
        self.assertEqual(account.status_message, "")
        self.assertTrue(account.usable())
        self.assertNotIn("cooldown_until", account.public_dict())
        self.assertNotIn("cooldown_until", account.__dict__)
        self.assertNotIn("cooldown_until", account._unknown)
        self.assertEqual(account.stats["requests"], 3)

    def test_manual_checks_and_successes_just_mark_the_account_working(self) -> None:
        account = Account("custom", {"name": "demo"}, status=ST_INVALID, status_message="bad")
        account.mark(ST_OK, "checked")
        self.assertEqual((account.status, account.status_message), (ST_OK, "checked"))
        self.assertTrue(account.usable())

    def test_no_interface_or_documentation_mentions_the_removed_pause(self) -> None:
        names = ["app/static/app.js", "app/static/i18n.js", "app/static/index.html", "app/static/style.css",
                 "app/companion_kb/overview.md", "app/companion_kb/errors.md", "app/companion_kb/troubleshooting.md"]
        for folder in ("ru", "en"):
            names += [f"app/docs/{folder}/proxy.md", f"app/docs/{folder}/accounts.md"]
        bad = re.compile(r"reset_cooldown|resetcd|acc\.cooldown|badge\.cooldown|Снять паузу|Clear cooldown|лимит/пауза", re.I)
        for name in names:
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIsNone(bad.search(text), f"{name} still mentions the removed cooldown")

class OrphanModelsTest(unittest.TestCase):
    def catalogue(self):
        rows = [ModelEntry("genspark", "genspark", "gpt-5"), ModelEntry("genspark", "genspark", "claude-opus-5"),
                ModelEntry("notion", "notion", "claude-sonnet"), ModelEntry("kiro", "kiro", "claude-sonnet-4-5"),
                ModelEntry("local", "local", "qwen3"), ModelEntry("mysite", "custom", "gpt-x", account_id="c1"),
                ModelEntry("zcode", "zcode", "glm"), ModelEntry("antigravity", "antigravity", "claude-sonnet-4-6")]
        return {row.id: row for row in rows}

    def prune(self, accounts, models, **kwargs):
        with patch.object(state, "accounts", accounts), patch.object(state, "models", models), \
                patch.object(state, "_unparsed_accounts", kwargs.pop("unparsed", [])), \
                patch.object(state, "save_models") as save:
            return state.prune_orphan_models(**kwargs), save, sorted(models)

    def test_models_of_providers_without_accounts_disappear(self) -> None:
        accounts = {"k": Account("kiro", {}, id="k"), "c1": Account("custom", {"name": "mysite"}, id="c1")}
        count, save, left = self.prune(accounts, self.catalogue())
        self.assertEqual(count, 5)
        save.assert_called_once()
        self.assertEqual(left, ["kiro-claude-sonnet-4-5", "local-qwen3", "mysite-gpt-x"])

    def test_local_catalogue_never_depends_on_accounts(self) -> None:

        count, _, left = self.prune({}, self.catalogue())
        self.assertEqual(left, ["local-qwen3"])
        self.assertEqual(count, 7)

    def test_only_the_named_providers_are_cleaned(self) -> None:
        count, _, left = self.prune({}, self.catalogue(), only={"genspark"})
        self.assertEqual(count, 2)
        self.assertIn("notion-claude-sonnet", left)

    def test_nothing_to_remove_means_no_disk_write(self) -> None:
        accounts = {"g": Account("genspark", {}, id="g"), "n": Account("notion", {}, id="n"),
                    "k": Account("kiro", {}, id="k"), "z": Account("zcode", {}, id="z"),
                    "a": Account("antigravity", {}, id="a"), "c1": Account("custom", {"name": "mysite"}, id="c1")}
        count, save, _ = self.prune(accounts, self.catalogue())
        self.assertEqual(count, 0)
        save.assert_not_called()

    def test_an_account_row_from_a_newer_schema_still_keeps_its_models(self) -> None:
        count, _, left = self.prune({}, self.catalogue(), unparsed=[{"provider": "kiro", "weird": 1}, "junk", None,
                                                                     {"provider": "custom", "fields": {"name": "mysite"}}])
        self.assertIn("kiro-claude-sonnet-4-5", left)
        self.assertIn("mysite-gpt-x", left)
        self.assertEqual(count, 5)

    def test_the_proxy_prunes_on_startup(self) -> None:
        text = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn("state.prune_orphan_models", text)
        self.assertLess(text.index("state.prune_orphan_models"), text.index("local_models.sync_catalog"))

from app import model_seeds  # noqa: E402
from app.providers import antigravity, antigravity_models as names, antigravity_quota as quota  # noqa: E402
from app.providers import http as provider_http  # noqa: E402

def server_models():
    """The shape of a real ``fetchAvailableModels`` answer (trimmed to what matters)."""
    def model(display=None, **extra):
        row = {"maxTokens": 1048576, "maxOutputTokens": 65536, "quotaInfo": {"remainingFraction": 1}}
        if display:
            row["displayName"] = display
            row["recommended"] = True
        row.update(extra)
        return row

    return {
        "models": {
            "chat_20706": model(), "chat_23310": model(), "tab_flash_lite_preview": model(),
            "claude-opus-4-6-thinking": model("Claude Opus 4.6 (Thinking)"),
            "claude-sonnet-4-6": model("Claude Sonnet 4.6 (Thinking)"),
            "gemini-2.5-flash": model("Gemini 3.5 Flash Lite"),
            "gemini-2.5-flash-lite": model("Gemini 3.5 Flash Lite"),
            "gemini-2.5-flash-thinking": model("Gemini 3.5 Flash Lite"),
            "gemini-2.5-pro": model("Gemini 2.5 Pro"),
            "gemini-3-flash": model("Gemini 3 Flash"),

            "gemini-3-flash-agent": model("Gemini 3.5 Flash (High)"),
            "gemini-3.1-flash-image": model("Gemini 3.1 Flash Image"),
            "gemini-3.1-flash-lite": model("Gemini 3.1 Flash Lite"),
            "gemini-3.1-pro-high": model("Gemini 3.1 Pro (High)"),
            "gemini-3.1-pro-low": model("Gemini 3.1 Pro (Low)"),
            "gemini-3.5-flash-extra-low": model("Gemini 3.5 Flash (Low)"),
            "gemini-3.5-flash-lite": model("Gemini 3.5 Flash Lite"),
            "gemini-3.5-flash-low": model("Gemini 3.5 Flash (Medium)"),
            "gemini-3.6-flash-high": model("Gemini 3.6 Flash (High)"),
            "gemini-3.6-flash-low": model("Gemini 3.6 Flash (Low)"),
            "gemini-3.6-flash-medium": model("Gemini 3.6 Flash (Medium)"),
            "gemini-3.6-flash-tiered": model(), "gemini-3.7-flash-tiered": model(), "gemini-3.8-flash-tiered": model(),
            "gemini-pro-agent": model("Gemini 3.1 Pro (High)"),
            "gpt-oss-120b-medium": model("GPT-OSS 120B (Medium)"),
        },
        "deprecatedModelIds": {"gemini-3.1-pro-high": {"newModelId": "gemini-pro-agent"}},
        "imageGenerationModelIds": ["gemini-3.1-flash-image"],
    }

class ModelNamesTest(unittest.TestCase):
    def setUp(self) -> None:
        names.reset_learned()
        self.addCleanup(names.reset_learned)

    def test_every_family_offers_its_thinking_levels_under_one_naming_scheme(self) -> None:
        public = [m.public for m in names.CURATED]
        for level in ("high", "medium", "low"):
            self.assertIn(f"gemini-3.6-flash-{level}", public)
        self.assertIn("gemini-3.1-pro-high", public)
        self.assertIn("gemini-3.1-pro-low", public)
        self.assertNotIn("gemini-3.1-pro-medium", public)

        for family in ("gemini-3.7-flash", "gemini-3.8-flash"):
            self.assertIn(family, public)
            for level in ("high", "medium", "low"):
                self.assertIn(f"{family}-{level}", public)
        self.assertFalse([name for name in public if name.startswith("gemini-3.5-flash-") and not name.endswith("lite")])

        self.assertIn("claude-opus-4-6-thinking", public)
        self.assertIn("claude-sonnet-4-6-thinking", public)
        self.assertNotIn("claude-sonnet-4-6", public)
        self.assertEqual(len(public), len(set(public)))

        for model in names.CURATED:
            self.assertTrue(model.note)

    def test_public_names_translate_back_and_old_ids_keep_working(self) -> None:
        for model in names.CURATED:
            self.assertEqual(names.upstream_id(model.public), model.upstream)
            if not model.level:
                self.assertEqual(names.public_id(model.upstream), model.public)
        self.assertEqual(names.upstream_id("gemini-3.8-flash-medium"), "gemini-3.8-flash-tiered")
        self.assertEqual(names.public_id("gemini-3.8-flash-tiered"), "gemini-3.8-flash")
        self.assertEqual(names.upstream_id("claude-sonnet-4-6"), "claude-sonnet-4-6")
        self.assertEqual(names.upstream_id("gemini-pro-agent"), "gemini-pro-agent")
        self.assertEqual(names.upstream_id("gemini-3.1-pro-high"), "gemini-pro-agent")
        self.assertEqual(names.upstream_id("gemini-3.7-flash"), "gemini-3.7-flash-tiered")
        self.assertEqual(names.upstream_id("gemini-3.7-flash-tiered"), "gemini-3.7-flash-tiered")
        self.assertEqual(names.upstream_id("totally-new-model"), "totally-new-model")
        self.assertEqual(antigravity.current_model_id("claude-sonnet-4-6-thinking"), "claude-sonnet-4-6")

    def test_server_list_becomes_a_clean_catalogue(self) -> None:
        found = names.from_server(server_models())
        self.assertEqual([item.public for item in found], [m.public for m in names.CURATED])
        by_public = {item.public: item for item in found}
        self.assertEqual(by_public["gemini-3.1-pro-high"].upstream, "gemini-pro-agent")
        self.assertEqual(by_public["gemini-3.7-flash"].upstream, "gemini-3.7-flash-tiered")
        self.assertEqual(by_public["gemini-3.7-flash"].display, "Gemini 3.7 Flash (auto thinking)")
        self.assertEqual(by_public["claude-sonnet-4-6-thinking"].upstream, "claude-sonnet-4-6")
        for gone in ("gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-flash-thinking", "gemini-3.6-flash-tiered",
                     "gemini-3.1-flash-image", "chat_20706", "tab_flash_lite_preview", "gemini-pro-agent",
                     "gemini-3-flash-agent", "gemini-3.5-flash-low", "gemini-3.5-flash-extra-low",
                     "gemini-3.5-flash-high", "gemini-3.5-flash-medium", "claude-sonnet-4-6"):
            self.assertNotIn(gone, by_public)
        self.assertEqual(names.upstream_id("gemini-3.1-pro-high"), "gemini-pro-agent")

    def test_future_models_get_an_honest_name_from_their_title(self) -> None:
        data = server_models()
        data["models"]["gemini-3.9-flash-agent"] = {"displayName": "Gemini 3.9 Flash (High)", "recommended": True}
        data["models"]["claude-haiku-4-6"] = {"displayName": "Claude Haiku 4.6 (Thinking)", "recommended": True}
        found = {item.public: item for item in names.from_server(data)}
        self.assertIn("gemini-3.9-flash-high", found)
        self.assertIn("claude-haiku-4-6-thinking", found)
        self.assertEqual(names.upstream_id("gemini-3.9-flash-high"), "gemini-3.9-flash-agent")
        self.assertEqual(names.upstream_id("claude-haiku-4-6-thinking"), "claude-haiku-4-6")
        self.assertTrue(names.is_thinking("claude-haiku-4-6"))
        self.assertFalse(names.is_thinking("gemini-3.6-flash-high"))

    def test_the_catalogue_upgrade_renames_keeps_choices_and_fills_the_gaps(self) -> None:
        def entry(model, **kw):
            return ModelEntry("antigravity", "antigravity", model, **kw)

        rows = [entry("claude-sonnet-4-6", note="Claude Sonnet 4.6 (Thinking)"), entry("claude-opus-4-6-thinking"),
                entry("gemini-pro-agent", enabled=False), entry("gemini-3.1-pro-low"),
                entry("gemini-3.6-flash-high"),
                entry("gemini-3-flash-agent", note="discovered 28.09"),
                entry("gemini-3.5-flash-low", note="discovered 28.09"),
                entry("gemini-3.5-flash-extra-low", note="discovered 28.09"),
                entry("gemini-2.5-flash", note="discovered 28.09"),
                entry("gemini-3.6-flash-tiered", note="discovered 28.09"),
                entry("gemini-3.7-flash-tiered", note="discovered 28.09"),
                entry("gemini-3.1-flash-image", source="manual"),
                ModelEntry("kiro", "kiro", "claude-sonnet-4-6")]
        models = {row.id: row for row in rows}

        def make(public, note, template):
            if template is None:
                return entry(public, note=note)
            return ModelEntry(template.prefix, template.provider, public, account_id=template.account_id,
                              enabled=template.enabled, source=template.source, note=note)

        change = names.upgrade_catalog(models, make)
        self.assertEqual(tuple(change), (3, 5, 11, 3))
        self.assertEqual(models["antigravity-claude-opus-4-6-thinking"].note, "Claude Opus 4.6 (Thinking)")
        self.assertEqual(models["antigravity-claude-sonnet-4-6-thinking"].note, "Claude Sonnet 4.6 (Thinking)")
        self.assertNotIn("antigravity-claude-sonnet-4-6", models)
        self.assertIn("antigravity-claude-sonnet-4-6-thinking", models)
        self.assertIn("kiro-claude-sonnet-4-6", models)
        self.assertFalse(models["antigravity-gemini-3.1-pro-high"].enabled)
        self.assertEqual(models["antigravity-gemini-3.7-flash"].note, "Gemini 3.7 Flash (auto thinking)")
        for gone in ("antigravity-gemini-2.5-flash", "antigravity-gemini-3.6-flash-tiered", "antigravity-gemini-pro-agent",
                     "antigravity-gemini-3-flash-agent", "antigravity-gemini-3.5-flash-low",
                     "antigravity-gemini-3.5-flash-extra-low", "antigravity-gemini-3.7-flash-tiered"):
            self.assertNotIn(gone, models)
        self.assertIn("antigravity-gemini-3.1-flash-image", models)
        for public, _note in names.seed_rows():
            self.assertIn(f"antigravity-{public}", models)
        again = names.upgrade_catalog(models, make)
        self.assertEqual(tuple(again), (0, 0, 0, 0))

    def test_a_catalogue_upgrade_does_not_reintroduce_historical_models_or_levels(self) -> None:

        kept = [public for public, _ in names.seed_rows()
                if public != "gemini-3-flash" and public not in names.level_variants()]
        models = {f"antigravity-{public}": ModelEntry("antigravity", "antigravity", public) for public in kept}
        account = Account("antigravity", {}, id="a")
        with patch.object(state, "accounts", {"a": account}), patch.object(state, "models", models), \
                patch.object(state, "save_models") as save, \
                patch.object(config_store.settings, "model_seed_version", 1), \
                patch.object(config_store, "apply_updates") as apply:
            report = model_seeds.upgrade(state)
        self.assertEqual(report["added"], 0)
        self.assertNotIn("antigravity-gemini-3-flash", models)
        for family in ("gemini-3.7-flash", "gemini-3.8-flash"):
            for level in ("high", "medium", "low"):
                self.assertNotIn(f"antigravity-{family}-{level}", models)
        save.assert_called_once()
        apply.assert_called_once_with({"model_seed_version": model_seeds.SEED_VERSION})

    def test_the_upgrade_runs_once_per_version_and_only_for_antigravity_users(self) -> None:
        models = {"antigravity-claude-sonnet-4-6": ModelEntry("antigravity", "antigravity", "claude-sonnet-4-6")}
        account = Account("antigravity", {}, id="a")
        with patch.object(state, "accounts", {"a": account}), patch.object(state, "models", models), \
                patch.object(state, "save_models") as save, \
                patch.object(config_store.settings, "model_seed_version", 0), \
                patch.object(config_store, "apply_updates") as apply:
            report = model_seeds.upgrade(state)
        self.assertEqual(report["renamed"], 1)
        self.assertEqual(report["added"], 0)
        save.assert_called_once()
        apply.assert_called_once_with({"model_seed_version": model_seeds.SEED_VERSION})

        with patch.object(state, "accounts", {"a": account}), patch.object(state, "models", models), \
                patch.object(state, "save_models") as save, \
                patch.object(config_store.settings, "model_seed_version", model_seeds.SEED_VERSION), \
                patch.object(config_store, "apply_updates") as apply:
            self.assertEqual(model_seeds.upgrade(state), {})
        save.assert_not_called()
        apply.assert_not_called()

        bare = {}
        with patch.object(state, "accounts", {}), patch.object(state, "models", bare), \
                patch.object(state, "save_models") as save, \
                patch.object(config_store.settings, "model_seed_version", 0), patch.object(config_store, "apply_updates"):
            self.assertEqual(model_seeds.upgrade(state), {})
        self.assertEqual(bare, {})
        save.assert_not_called()

    def test_a_new_account_waits_for_its_upstream_picker_instead_of_stale_seeds(self) -> None:
        seeds = dict(antigravity.AntigravityProvider().seed_models())
        self.assertEqual(seeds, {})

    def test_the_proxy_upgrades_the_catalogue_on_startup(self) -> None:
        text = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn("model_seeds.upgrade", text)
        self.assertLess(text.index("state.prune_orphan_models"), text.index("model_seeds.upgrade"))

class QuotaMathTest(unittest.TestCase):
    def test_a_bucket_without_a_fraction_but_with_a_reset_is_an_exhausted_one(self) -> None:
        self.assertEqual(quota.bucket_state({"remainingFraction": 0.25, "resetTime": "2026-09-30T10:00:00Z"})[0], 0.25)
        self.assertEqual(quota.bucket_state({"resetTime": "2026-09-30T10:00:00Z"})[0], 0.0)
        self.assertEqual(quota.bucket_state({"remainingFraction": 1}), (1.0, 0.0))
        self.assertIsNone(quota.bucket_state({}))
        self.assertIsNone(quota.bucket_state("nonsense"))
        self.assertEqual(quota.bucket_state({"remainingFraction": 7})[0], 1.0)
        self.assertEqual(quota.used_percent(0.9997), 0.03)
        self.assertEqual(quota.used_percent(0.575), 42.5)

    def test_a_window_ending_days_away_is_the_long_one(self) -> None:
        now = 1_000_000.0
        found = quota.windows([(0.4, now + 3 * 3600), (0.9, now + 2 * 3600), (0.0, now + 6 * 86400)], now)
        self.assertEqual(found["5h"], (0.4, now + 3 * 3600))
        self.assertEqual(found["week"], (0.0, now + 6 * 86400))
        self.assertNotIn("week", quota.windows([(1.0, now + 4.9 * 3600)], now))

    def test_history_adds_up_the_windows_not_their_depth(self) -> None:
        now = 10_000_000.0
        day = 86400.0
        extra = {}
        samples = [(now - 2 * day, 0.9, now - 2 * day + 3000),
                   (now - 2 * day + 7200, 0.4, now - 2 * day + 3000),
                   (now - day, 0.5, now - day + 3000),
                   (now - 100, 0.5, now + 3000)]
        for ts, remaining, reset in samples:
            self.assertTrue(quota.record_point(extra, "Gemini", remaining, reset, now=ts))
        self.assertEqual(quota.history_windows(extra, "Gemini", now), 1.6)
        self.assertIsNone(quota.history_windows(extra, "Claude / GPT", now))

    def test_old_samples_expire_and_close_ones_merge(self) -> None:
        now = 10_000_000.0
        extra = {"weekly_quota": {"Gemini": [[now - 9 * 86400, 0.0, 0], [now - 100, 0.8, now + 4000]]}}
        self.assertEqual(quota.history_windows(extra, "Gemini", now), 0.2)
        quota.record_point(extra, "Gemini", 0.7, now + 4000, now=now)
        quota.record_point(extra, "Gemini", 0.9, now + 4000, now=now + 10)
        self.assertEqual(len(extra["weekly_quota"]["Gemini"]), 1)
        self.assertEqual(quota.history_windows(extra, "Gemini", now + 20), 0.3)

    def test_a_quota_exhausted_answer_belongs_to_the_window_already_being_tracked(self) -> None:
        now = 10_000_000.0
        extra = {}
        quota.record_point(extra, "Gemini", 0.9, now + 3600, now=now - 4000)
        quota.record_point(extra, "Gemini", 0.0, None, now=now)
        self.assertEqual(quota.history_windows(extra, "Gemini", now), 1.0)

    def test_garbage_in_the_account_never_breaks_the_tracker(self) -> None:
        self.assertFalse(quota.record_point(None, "Gemini", 0.5))
        self.assertFalse(quota.record_point("junk", "Gemini", 0.5))
        bad = {"weekly_quota": {"Gemini": [["x", "y"], [1], None, [time.time(), 0.5, "z"]]}}
        self.assertIsNone(quota.history_windows(bad, "Gemini"))
        self.assertIsNone(quota.history_windows({"weekly_quota": "junk"}, "Gemini"))

from unittest.mock import AsyncMock  # noqa: E402

PROD = antigravity.ENDPOINTS[0]
DAILY = antigravity.ENDPOINTS[1]

class StreamResponse:
    """A streamed curl_cffi response: the body is unread, so ``text`` is empty."""

    def __init__(self, status=200, *, lines=(), body=""):
        self.status_code = status
        self._lines = list(lines)
        self._body = body.encode("utf-8") if isinstance(body, str) else body
        self.closed = False
        self.text = ""

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def acontent(self):
        return self._body

    async def aclose(self):
        self.closed = True

class JsonResponse:
    def __init__(self, payload, status=200):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

def sse(*events):
    lines = []
    for event in events:
        lines += [("data: " + json.dumps({"response": event})).encode("utf-8"), b""]
    return lines

def answer(text="ok", **extra):
    return sse({"candidates": [{"content": {"parts": [{"text": text}]}, **extra}]})

def google_error(code, message, status, *details):
    return json.dumps({"error": {"code": code, "message": message, "status": status, "details": list(details)}})

class Session:
    """Scripted HTTP session: ``script(url, kwargs, call_number)`` returns the response."""

    def __init__(self, script):
        self.script = script
        self.calls = []

    async def post(self, url, **kwargs):

        snapshot = dict(kwargs)
        if "json" in snapshot:
            snapshot["json"] = copy.deepcopy(snapshot["json"])
        self.calls.append((url, snapshot))
        return self.script(url, snapshot, len(self.calls))

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.script(url, kwargs, len(self.calls))

    def chats(self):
        return [(url, kwargs) for url, kwargs in self.calls if "streamGenerateContent" in url]

def make_account(**extra):
    data = {"oauth": {"access_token": "tok-1", "refresh_token": "ref-1", "expires_at": time.time() + 3600},
            "project_id": "proj-1"}
    data.update(extra)
    return Account("antigravity", {}, id="acc-x", extra=data)

class WireCase(unittest.TestCase):
    """Runs the real provider code against a scripted session."""

    def setUp(self) -> None:
        antigravity.reset_health()
        names.reset_learned()
        antigravity._quota_hits.clear()
        self.addCleanup(antigravity.reset_health)
        self.addCleanup(names.reset_learned)
        self.addCleanup(antigravity._quota_hits.clear)

    def chat(self, session, account=None, model="gemini-3.6-flash-high", *, params=None, cfg=None,
             messages=None, provider=None):
        provider = provider or antigravity.AntigravityProvider()
        account = account or make_account()

        async def run():
            events = []
            with patch("app.providers.antigravity.http.get_session", new=AsyncMock(return_value=session)), \
                    patch.object(state, "save_accounts"), patch.object(state, "save_accounts_soon"), \
                    patch.object(antigravity, "SAMPLE_AFTER_CHAT", False), \
                    patch.object(antigravity, "VERSION_CHECK", False), \
                    patch.object(antigravity, "INLINE_WAIT_PAD", 0.0), \
                    patch.dict(config_store.settings.provider_settings, {"antigravity": dict(cfg or {})}):
                async for event in provider.chat_stream(
                        account, model, messages or [{"role": "user", "content": "hi"}], params or {}):
                    events.append(event)
            return events

        return asyncio.run(run())

    def body(self, session, index=0):
        return session.chats()[index][1]["json"]

class ErrorsAndEndpointsTest(WireCase):
    def test_googles_own_words_reach_the_person(self) -> None:
        def script(url, kwargs, number):
            if url.startswith(PROD):
                return StreamResponse(429, body=google_error(429, "Resource has been exhausted (e.g. check quota).",
                                                             "RESOURCE_EXHAUSTED"))
            return StreamResponse(400, body=google_error(400, "User location is not supported for the API use.",
                                                         "FAILED_PRECONDITION"))

        session = Session(script)
        with self.assertRaises(ProviderServerError) as caught:
            self.chat(session)
        self.assertIn("User location is not supported", str(caught.exception))

        self.assertGreaterEqual(caught.exception.status, 500)
        self.assertEqual(len(session.chats()), 2)

    def test_a_generic_google_400_keeps_its_text_and_code(self) -> None:
        session = Session(lambda url, kwargs, n: StreamResponse(
            400, body=google_error(400, "Request contains an invalid argument.", "INVALID_ARGUMENT")))
        with self.assertRaises(ProviderServerError) as caught:
            self.chat(session)
        self.assertEqual(caught.exception.status, 400)
        self.assertIn("invalid argument", str(caught.exception))
        self.assertIn("gemini-3.6-flash-high", str(caught.exception))

    def test_the_host_that_answered_goes_first_next_time(self) -> None:
        def script(url, kwargs, number):
            if url.startswith(PROD):
                return StreamResponse(429, body=google_error(429, "Resource has been exhausted.", "RESOURCE_EXHAUSTED"))
            return StreamResponse(200, lines=answer("pong"))

        first, second = Session(script), Session(script)
        self.assertEqual(self.chat(first), [{"type": "text", "text": "pong"}])
        self.assertEqual([url.split("/v1internal")[0] for url, _ in first.chats()], [PROD, DAILY])
        self.chat(second)
        self.assertEqual([url.split("/v1internal")[0] for url, _ in second.chats()], [DAILY])
        antigravity.reset_health()
        third = Session(script)
        self.chat(third)
        self.assertEqual(third.chats()[0][0].split("/v1internal")[0], PROD)

    def test_the_endpoint_setting_still_pins_one_host(self) -> None:
        session = Session(lambda url, kwargs, n: StreamResponse(200, lines=answer()))
        self.chat(session, cfg={"endpoint": "daily"})
        self.assertEqual([url.split("/v1internal")[0] for url, _ in session.chats()], [DAILY])

    def test_an_unauthorized_token_is_refreshed_once_and_the_request_repeated(self) -> None:
        account = make_account()

        def script(url, kwargs, number):
            if url == antigravity.TOKEN_URL:
                return JsonResponse({"access_token": "tok-2", "expires_in": 3600})
            sent = kwargs["headers"]["Authorization"]
            return StreamResponse(200, lines=answer("ok")) if sent == "Bearer tok-2" else StreamResponse(
                401, body=google_error(401, "Request had invalid authentication credentials.", "UNAUTHENTICATED"))

        session = Session(script)
        self.assertEqual(self.chat(session, account), [{"type": "text", "text": "ok"}])
        self.assertEqual(account.extra["oauth"]["access_token"], "tok-2")
        self.assertEqual(sum(1 for url, _ in session.calls if url == antigravity.TOKEN_URL), 1)

    def test_a_token_google_keeps_rejecting_is_an_auth_error_after_one_refresh(self) -> None:
        def script(url, kwargs, number):
            if url == antigravity.TOKEN_URL:
                return JsonResponse({"access_token": "tok-2", "expires_in": 3600})
            return StreamResponse(401, body=google_error(401, "no", "UNAUTHENTICATED"))

        session = Session(script)
        with self.assertRaises(ProviderAuthError):
            self.chat(session)
        self.assertEqual(sum(1 for url, _ in session.calls if url == antigravity.TOKEN_URL), 1)

    def test_a_revoked_refresh_token_is_reported_as_an_auth_error(self) -> None:
        def script(url, kwargs, number):
            if url == antigravity.TOKEN_URL:
                return JsonResponse({"error": "invalid_grant"}, status=400)
            return StreamResponse(401, body="{}")

        with self.assertRaises(ProviderAuthError) as caught:
            self.chat(Session(script))
        self.assertIn("refresh", str(caught.exception))

    def test_a_short_wait_that_google_asks_for_is_waited_out(self) -> None:
        retry = {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "0.2s"}

        def script(url, kwargs, number):
            if number == 1:
                return StreamResponse(429, body=google_error(429, "You have exhausted your capacity on this model.",
                                                             "RESOURCE_EXHAUSTED", retry))
            return StreamResponse(200, lines=answer("later"))

        session = Session(script)
        started = time.time()
        self.assertEqual(self.chat(session), [{"type": "text", "text": "later"}])
        self.assertGreaterEqual(time.time() - started, 0.2)
        hosts = [url.split("/v1internal")[0] for url, _ in session.chats()]
        self.assertEqual(hosts, [PROD, PROD])

    def test_a_wait_google_asks_for_is_taken_once_per_host_and_never_long(self) -> None:
        long_wait = {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "40s"}
        session = Session(lambda url, kwargs, n: StreamResponse(
            429, body=google_error(429, "Capacity.", "RESOURCE_EXHAUSTED", long_wait)))
        with self.assertRaises(ProviderRateLimitError):
            self.chat(session)
        self.assertEqual(len(session.chats()), 2)

    def test_an_exhausted_quota_stops_at_once_and_is_remembered(self) -> None:
        account = make_account()
        detail = {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "QUOTA_EXHAUSTED"}
        session = Session(lambda url, kwargs, n: StreamResponse(
            429, body=google_error(429, "You have exhausted your quota. Your quota will reset after 13h3m.",
                                   "RESOURCE_EXHAUSTED", detail)))
        with self.assertRaises(ProviderQuotaError) as caught:
            self.chat(session, account, "claude-opus-4-6-thinking")
        self.assertIn("13", str(caught.exception))
        self.assertEqual(len(session.chats()), 1)
        self.assertIn("Claude / GPT", antigravity._quota_hits["acc-x"])
        self.assertEqual(quota.history_windows(account.extra, "Claude / GPT"), 1.0)

    def test_a_request_for_account_verification_is_reported_and_not_retried_as_a_project_problem(self) -> None:
        detail = {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "VALIDATION_REQUIRED",
                  "metadata": {"validation_url": "https://accounts.google.com/signin/continue?x=1"}}
        session = Session(lambda url, kwargs, n: StreamResponse(
            403, body=google_error(403, "Verify your account to continue.", "PERMISSION_DENIED", detail)))
        with self.assertRaises(ProviderModelUnavailableError) as caught:
            self.chat(session)
        self.assertIn("Verify your account", str(caught.exception))
        self.assertEqual(caught.exception.model, "")
        self.assertFalse([url for url, _ in session.calls if "loadCodeAssist" in url])

    def test_a_plain_403_refreshes_the_project_once_and_then_moves_on(self) -> None:
        def script(url, kwargs, number):
            if "loadCodeAssist" in url:
                return JsonResponse({"cloudaicompanionProject": "proj-2"})
            return StreamResponse(403, body=google_error(403, "Permission denied.", "PERMISSION_DENIED"))

        session = Session(script)
        account = make_account()
        with self.assertRaises(ProviderServerError) as caught:
            self.chat(session, account)
        self.assertEqual(caught.exception.status, 502)
        self.assertIn("HTTP 403", str(caught.exception))
        self.assertEqual(account.extra["project_id"], "proj-2")
        self.assertEqual(sum(1 for url, _ in session.calls if "loadCodeAssist" in url), 1)

    def test_a_retired_model_id_is_renamed_from_the_servers_list_and_retried(self) -> None:
        data = server_models()
        data["deprecatedModelIds"]["gemini-old-one"] = {"newModelId": "gemini-3.6-flash-high"}

        def script(url, kwargs, number):
            if "fetchAvailableModels" in url:
                return JsonResponse(data)
            if kwargs["json"]["model"] == "gemini-old-one":
                return StreamResponse(400, body=google_error(400, "Model not supported.", "INVALID_ARGUMENT"))
            return StreamResponse(200, lines=answer("renamed"))

        session = Session(script)
        self.assertEqual(self.chat(session, model="gemini-old-one"), [{"type": "text", "text": "renamed"}])
        self.assertEqual([call[1]["json"]["model"] for call in session.chats()], ["gemini-old-one", "gemini-3.6-flash-high"])

class ThinkingAndModelsTest(WireCase):
    def ok(self):
        return Session(lambda url, kwargs, n: StreamResponse(200, lines=answer()))

    def test_public_names_are_sent_as_the_ids_google_knows(self) -> None:
        for public, upstream in (("gemini-3.7-flash", "gemini-3.7-flash-tiered"),
                                 ("gemini-3.8-flash", "gemini-3.8-flash-tiered"),
                                 ("gemini-3.1-pro-high", "gemini-pro-agent"),
                                 ("gemini-pro-agent", "gemini-pro-agent"),
                                 ("claude-sonnet-4-6-thinking", "claude-sonnet-4-6"),
                                 ("claude-sonnet-4-6", "claude-sonnet-4-6"),
                                 ("gemini-3.6-flash-low", "gemini-3.6-flash-low")):
            session = self.ok()
            self.chat(session, model=public)
            self.assertEqual(self.body(session)["model"], upstream, public)

    def test_both_claude_models_think_with_a_budget_and_the_effort_changes_it(self) -> None:
        for model in ("claude-opus-4-6-thinking", "claude-sonnet-4-6-thinking", "claude-sonnet-4-6"):
            session = self.ok()
            self.chat(session, model=model)
            config = self.body(session)["request"]["generationConfig"]
            self.assertEqual(config["thinkingConfig"], {"includeThoughts": True, "thinkingBudget": 8192}, model)
            self.assertGreater(config["maxOutputTokens"], 8192)
        for effort, budget in (("low", 2048), ("medium", 8192), ("high", 16384), ("max", 32000)):
            session = self.ok()
            self.chat(session, model="claude-opus-4-6-thinking", params={"effort": effort})
            self.assertEqual(self.body(session)["request"]["generationConfig"]["thinkingConfig"]["thinkingBudget"], budget)
        session = self.ok()
        self.chat(session, model="claude-opus-4-6-thinking", params={"effort": "minimal"})
        self.assertNotIn("thinkingConfig", self.body(session)["request"]["generationConfig"])

    def test_the_thinking_budget_setting_and_a_small_answer_limit(self) -> None:
        session = self.ok()
        self.chat(session, model="claude-opus-4-6-thinking", cfg={"thinking_budget": "0"})
        self.assertNotIn("thinkingConfig", self.body(session)["request"]["generationConfig"])
        session = self.ok()
        self.chat(session, model="claude-opus-4-6-thinking", cfg={"thinking_budget": "5000", "max_output_tokens": "4096"})
        config = self.body(session)["request"]["generationConfig"]
        self.assertEqual(config["thinkingConfig"]["thinkingBudget"], 5000)
        self.assertEqual(config["maxOutputTokens"], 5000 + 2048)
        for unset in ("junk", "", "   ", None):
            session = self.ok()
            self.chat(session, model="claude-opus-4-6-thinking", cfg={"thinking_budget": unset})
            self.assertEqual(self.body(session)["request"]["generationConfig"]["thinkingConfig"]["thinkingBudget"], 8192)
        session = self.ok()
        self.chat(session, model="claude-opus-4-6-thinking", cfg={"thinking_budget": 4096})
        self.assertEqual(self.body(session)["request"]["generationConfig"]["thinkingConfig"]["thinkingBudget"], 4096)

    def test_gemini_returns_thought_summaries_unless_switched_off_and_gpt_oss_is_left_alone(self) -> None:
        session = self.ok()
        self.chat(session, model="gemini-3.6-flash-medium")
        self.assertEqual(self.body(session)["request"]["generationConfig"]["thinkingConfig"], {"includeThoughts": True})
        session = self.ok()
        self.chat(session, model="gemini-3.6-flash-medium", cfg={"show_thoughts": "off"})
        self.assertNotIn("thinkingConfig", self.body(session)["request"]["generationConfig"])
        session = self.ok()
        self.chat(session, model="gpt-oss-120b-medium")
        self.assertNotIn("thinkingConfig", self.body(session)["request"]["generationConfig"])

    def test_gemini_3_7_and_3_8_take_their_thinking_level_from_the_name_or_the_effort(self) -> None:
        for family in ("gemini-3.7-flash", "gemini-3.8-flash"):
            for level in ("high", "medium", "low"):
                session = self.ok()
                self.chat(session, model=f"{family}-{level}", params={"effort": "max"})
                body = self.body(session)
                self.assertEqual(body["model"], f"{family}-tiered")
                self.assertEqual(body["request"]["generationConfig"]["thinkingConfig"],
                                 {"thinkingLevel": level, "includeThoughts": True})

            session = self.ok()
            self.chat(session, model=family)
            self.assertEqual(self.body(session)["request"]["generationConfig"]["thinkingConfig"], {"includeThoughts": True})
            for effort, level in (("minimal", "low"), ("low", "low"), ("medium", "medium"), ("high", "high"), ("max", "high")):
                for name in (family, f"{family}-tiered"):
                    session = self.ok()
                    self.chat(session, model=name, params={"effort": effort})
                    self.assertEqual(self.body(session)["request"]["generationConfig"]["thinkingConfig"]["thinkingLevel"], level)
        session = self.ok()
        self.chat(session, model="gemini-3.8-flash-low", cfg={"show_thoughts": "off"})
        self.assertEqual(self.body(session)["request"]["generationConfig"]["thinkingConfig"], {"thinkingLevel": "low"})

        session = self.ok()
        self.chat(session, model="gemini-3.6-flash-low", params={"effort": "high"})
        self.assertNotIn("thinkingLevel", self.body(session)["request"]["generationConfig"]["thinkingConfig"])

    def test_a_level_google_refuses_is_reported_without_silent_downgrade(self) -> None:
        def script(url, kwargs, number):
            if "thinkingConfig" in kwargs["json"]["request"]["generationConfig"]:
                return StreamResponse(400, body=google_error(
                    400, "Thinking level HIGH is not supported for this model.", "INVALID_ARGUMENT"))
            return StreamResponse(200, lines=answer("plain"))

        session = Session(script)
        with self.assertRaisesRegex(ProviderServerError, "reasoning depth"):
            self.chat(session, model="gemini-3.8-flash-high")
        self.assertEqual(len(session.chats()), 1)
        self.assertEqual(self.body(session)["request"]["generationConfig"]["thinkingConfig"]["thinkingLevel"], "high")

    def test_thought_summary_rejection_preserves_explicit_high_depth(self) -> None:
        def script(url, kwargs, number):
            config = kwargs["json"]["request"]["generationConfig"]["thinkingConfig"]
            if config.get("includeThoughts"):
                return StreamResponse(400, body=google_error(400,
                    "thinkingConfig.includeThoughts is not supported", "INVALID_ARGUMENT"))
            return StreamResponse(200, lines=answer("high answer"))
        session = Session(script)
        self.assertEqual(self.chat(session, model="gemini-3.8-flash-high"), [{"type": "text", "text": "high answer"}])
        self.assertEqual(self.body(session, 1)["request"]["generationConfig"]["thinkingConfig"], {"thinkingLevel": "high"})

    def test_a_thinking_model_that_rejects_budget_does_not_return_a_plain_answer(self) -> None:
        def script(url, kwargs, number):
            if "thinkingConfig" in kwargs["json"]["request"]["generationConfig"]:
                return StreamResponse(400, body=google_error(400, "Thinking is not supported for this model.",
                                                             "INVALID_ARGUMENT"))
            return StreamResponse(200, lines=answer("plain"))

        session = Session(script)
        with self.assertRaisesRegex(ProviderServerError, "reasoning depth"):
            self.chat(session, model="claude-opus-4-6-thinking")
        self.assertEqual(len(session.chats()), 1)

    def test_thoughts_answers_a_cut_off_answer_and_refusals_are_told_apart(self) -> None:
        stream = Session(lambda url, kwargs, n: StreamResponse(200, lines=sse({"candidates": [{
            "content": {"parts": [{"text": "hmm", "thought": True}, {"text": "42"}]}, "finishReason": "MAX_TOKENS"}]})))
        self.assertEqual(self.chat(stream), [{"type": "reasoning", "text": "hmm"}, {"type": "text", "text": "42"},
                                             {"type": "finish", "reason": "length"}])
        for event in ({"candidates": [{"finishReason": "SAFETY"}]}, {"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}}):
            with self.assertRaises(ProviderContentFilterError):
                self.chat(Session(lambda url, kwargs, n, event=event: StreamResponse(200, lines=sse(event))))

        self.assertEqual(self.chat(Session(lambda url, kwargs, n: StreamResponse(
            200, lines=sse({"candidates": [{"finishReason": "STOP"}]})))), [])

    def test_the_settings_page_offers_the_new_knobs(self) -> None:
        keys = {field.key: field for field in antigravity.AntigravityProvider().settings_fields()}
        self.assertEqual(keys["thinking_budget"].default, "8192")
        self.assertEqual(keys["show_thoughts"].options, ["on", "off"])
        self.assertIn("auto", keys["endpoint"].options)

@unittest.skipUnless(shutil.which("node"), "Node.js is required for the UI runner")
class ProxyModelListSelectionTest(unittest.TestCase):
    """The model list selects like the Windows desktop: clicks, Ctrl, Shift and a drag rectangle."""

    def test_the_real_selection_code_behaves_like_the_desktop(self) -> None:
        done = subprocess.run([shutil.which("node"), str(ROOT / "tests" / "proxy_ui_selection_runner.cjs")],
                              capture_output=True, text=True, encoding="utf-8", timeout=60)
        lines = [line for line in done.stdout.splitlines() if line.strip().startswith("{")]
        self.assertTrue(lines, done.stderr[-1500:])
        out = json.loads(lines[-1])
        self.assertNotIn("error", out, out.get("error"))
        self.assertEqual(out["dragThree"], ["r0", "r1", "r2"])
        self.assertEqual(out["dragUpwards"], out["dragThree"])
        self.assertEqual(out["dragOne"], ["r1"])
        self.assertEqual(out["dragNothing"], [])
        self.assertEqual(out["dragAll"], ["r0", "r1", "r2", "r3", "r4", "r5"])
        self.assertEqual(out["ctrlAdd"], ["r1", "r4"])
        self.assertEqual(out["ctrlToggleOff"], ["r4"])
        self.assertEqual(out["click"], ["r2"])
        self.assertEqual(out["shiftRange"], ["r2", "r3", "r4"])
        self.assertEqual(out["ctrlOff"], ["r2", "r4"])
        self.assertEqual(out["ctrlOn"], ["r2", "r3", "r4"])
        self.assertEqual(out["ctrlShift"], ["r0", "r1", "r2", "r3", "r4"])
        self.assertEqual(out["plainAgain"], ["r5"])
        self.assertEqual(out["shiftBackwards"], ["r1", "r2", "r3", "r4", "r5"])

        self.assertEqual(out["clickTwice"], [])
        self.assertEqual(out["clickOneOfTwo"], ["r2"])
        self.assertEqual(out["keyboardKeeps"], ["r4"])
        self.assertEqual(out["keyboardKeepsAgain"], ["r4"])
        self.assertEqual(out["dragWhileScrolling"], ["r0", "r1", "r2", "r3", "r4", "r5"])
        self.assertEqual(out["startFollowsTheList"], 25)
        self.assertEqual(out["dragScrolledBack"], ["r0", "r1"])

class RetiredModelsAndFiltersTest(WireCase):
    def test_a_retired_model_answering_with_its_canned_phrase_is_an_error_not_an_answer(self) -> None:
        phrase = ("Gemini 3.5 Flash is no longer available. Please switch to Gemini 3.7 Flash "
                  "in the latest version of Antigravity.")
        session = Session(lambda url, kwargs, n: StreamResponse(200, lines=answer(phrase)))
        with self.assertRaises(ProviderModelUnavailableError) as caught:
            self.chat(session, model="gemini-9-fictional-flash")
        self.assertIn("no longer available", str(caught.exception))
        self.assertEqual(caught.exception.model, "")
        self.assertTrue(names.is_retired("gemini-9-fictional-flash"))

        data = {"models": {"gemini-9-fictional-flash": {"displayName": "Gemini 9 Flash", "recommended": True}}}
        self.assertEqual(names.from_server(data), [])

    def test_ordinary_answers_are_never_mistaken_for_a_retirement_notice(self) -> None:
        self.assertTrue(names.is_retired_text("X is no longer available. Please switch to Y."))
        self.assertFalse(names.is_retired_text("The old API is no longer available, but the new one works."))
        self.assertFalse(names.is_retired_text("Gemini 3.5 Flash is no longer available. " + "Please switch. " * 80))
        session = Session(lambda url, kwargs, n: StreamResponse(200, lines=answer("Fine. Sorry: it is no longer available.")))
        self.assertEqual(self.chat(session)[0]["text"], "Fine. Sorry: it is no longer available.")

    def test_a_model_that_refuses_a_safety_category_is_asked_with_fewer_and_remembered(self) -> None:
        def script(url, kwargs, number):
            sent = kwargs["json"]["request"].get("safetySettings") or []
            if len(sent) > 4:
                return StreamResponse(400, body=google_error(
                    400, "* GenerateContentRequest.safety_settings[4]: element predicate failed: $.category in (...)",
                    "INVALID_ARGUMENT"))
            return StreamResponse(200, lines=answer("fine"))

        session = Session(script)
        self.assertEqual(self.chat(session, model="gemini-3.1-flash-lite"), [{"type": "text", "text": "fine"}])
        self.assertEqual([len(call[1]["json"]["request"]["safetySettings"]) for call in session.chats()], [5, 4])
        again = Session(script)
        self.chat(again, model="gemini-3.1-flash-lite")
        self.assertEqual(len(again.chats()), 1)

    def test_a_model_that_takes_no_safety_settings_at_all_gets_none(self) -> None:
        def script(url, kwargs, number):
            if "safetySettings" in kwargs["json"]["request"]:
                return StreamResponse(400, body=google_error(400, "safety_settings are not supported", "INVALID_ARGUMENT"))
            return StreamResponse(200, lines=answer("bare"))

        session = Session(script)
        self.assertEqual(self.chat(session, model="gemini-x"), [{"type": "text", "text": "bare"}])
        self.assertEqual(len(session.chats()), 3)
        self.assertNotIn("safetySettings", session.chats()[-1][1]["json"]["request"])

class ClientVersionTest(WireCase):
    def refresh(self, script, account=None):
        provider = antigravity.AntigravityProvider()
        session = Session(script)

        async def run():
            with patch("app.providers.antigravity.http.get_session", new=AsyncMock(return_value=session)), \
                    patch.object(antigravity, "VERSION_CHECK", True):
                await provider._refresh_version(account or make_account())

        asyncio.run(run())
        return session

    def test_requests_present_the_current_antigravity_version(self) -> None:
        self.assertEqual(antigravity.user_agent(), f"antigravity/{antigravity.FALLBACK_VERSION} win32/x64")
        self.assertGreaterEqual(int(antigravity.FALLBACK_VERSION.split(".")[0]), 2)
        session = Session(lambda url, kwargs, n: StreamResponse(200, lines=answer()))
        self.chat(session)
        url, kwargs = session.chats()[0]
        self.assertEqual(kwargs["json"]["userAgent"], antigravity.user_agent())
        self.assertEqual(kwargs["headers"]["User-Agent"], antigravity.user_agent())
        self.assertTrue(kwargs["headers"]["x-goog-api-client"].endswith(antigravity.FALLBACK_VERSION))

    def test_the_version_is_taken_from_the_update_service_and_used_from_then_on(self) -> None:
        session = self.refresh(lambda url, kwargs, n: JsonResponse({"productVersion": "2.3.11", "name": "2.3.11"}))
        self.assertEqual(session.calls[0][0], antigravity.VERSION_URL)
        self.assertEqual(antigravity.client_version(), "2.3.11")
        self.refresh(lambda url, kwargs, n: self.fail("asked twice within the interval"))
        chat = Session(lambda url, kwargs, n: StreamResponse(200, lines=answer()))
        self.chat(chat)
        self.assertEqual(chat.chats()[0][1]["json"]["userAgent"], "antigravity/2.3.11 win32/x64")

    def test_a_broken_answer_keeps_the_known_version_and_is_retried_later(self) -> None:
        for payload in ({"productVersion": "2.3.11; drop table"}, {"productVersion": ""}, {}, {"productVersion": "x.y.z"}):
            antigravity.reset_health()
            self.refresh(lambda url, kwargs, n, payload=payload: JsonResponse(payload))
            self.assertEqual(antigravity.client_version(), antigravity.FALLBACK_VERSION)
        antigravity.reset_health()
        self.refresh(lambda url, kwargs, n: JsonResponse({}, status=503))
        self.assertEqual(antigravity.client_version(), antigravity.FALLBACK_VERSION)
        soon = antigravity._client_version["checked"] + antigravity.VERSION_TTL
        self.assertLess(soon - time.time(), 1000)

    def test_a_version_set_by_hand_wins_and_turns_the_check_off(self) -> None:
        with patch.dict(config_store.settings.provider_settings, {"antigravity": {"client_version": "2.9.1"}}):
            self.assertEqual(antigravity.user_agent(), "antigravity/2.9.1 win32/x64")
            self.refresh(lambda url, kwargs, n: self.fail("the update service must not be asked"))
        with patch.dict(config_store.settings.provider_settings, {"antigravity": {"client_version": "latest; evil"}}):
            self.assertEqual(antigravity.client_version(), antigravity.FALLBACK_VERSION)
        keys = {field.key for field in antigravity.AntigravityProvider().settings_fields()}
        self.assertIn("client_version", keys)

    def test_an_unreachable_update_service_changes_nothing(self) -> None:
        def script(url, kwargs, n):
            raise OSError("no route to host")

        self.refresh(script)
        self.assertEqual(antigravity.client_version(), antigravity.FALLBACK_VERSION)

class TokenRefreshTest(WireCase):
    def test_parallel_requests_share_one_token_refresh(self) -> None:
        account = make_account(oauth={"access_token": "old", "refresh_token": "ref", "expires_at": time.time() - 10})
        refreshes = []

        def script(url, kwargs, number):
            refreshes.append(url)
            return JsonResponse({"access_token": "fresh", "expires_in": 3600})

        session = Session(script)
        provider = antigravity.AntigravityProvider()

        async def run():
            with patch("app.providers.antigravity.http.get_session", new=AsyncMock(return_value=session)), \
                    patch.object(state, "save_accounts"):
                return await asyncio.gather(*(provider._ensure_token(account) for _ in range(6)))

        self.assertEqual(asyncio.run(run()), ["fresh"] * 6)
        self.assertEqual(len(refreshes), 1)

class UsageTest(WireCase):
    def usage(self, buckets, account=None, tier="Google AI Pro", **patches):
        provider = antigravity.AntigravityProvider()
        account = account or make_account()

        async def quota_buckets(_account, _token, _project):
            return buckets

        async def tier_name(_account, _token):
            return tier

        async def run():
            with patch.object(provider, "_quota_buckets", quota_buckets), patch.object(provider, "_tier_name", tier_name), \
                    patch.object(state, "save_accounts_soon"):
                return await provider.usage(account)

        return asyncio.run(run()), account

    def test_a_real_google_ai_pro_answer_gives_two_five_hour_rows_and_the_plan(self) -> None:
        reset_gemini = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 2 * 3600))
        reset_claude = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3 * 3600))
        buckets = {"chat_20706": {"tokenType": "WTUS", "remainingFraction": 1},
                   "claude-opus-4-6-thinking": {"tokenType": "WTUS", "resetTime": reset_claude, "remainingFraction": 0.5751872},
                   "claude-sonnet-4-6": {"tokenType": "WTUS", "resetTime": reset_claude, "remainingFraction": 0.5751872},
                   "gemini-3.6-flash-high": {"tokenType": "WTUS", "resetTime": reset_gemini, "remainingFraction": 1},
                   "gemini-pro-agent": {"tokenType": "WTUS", "resetTime": reset_gemini, "remainingFraction": 1},
                   "gpt-oss-120b-medium": {"tokenType": "WTUS", "resetTime": reset_claude, "remainingFraction": 0.5751872},
                   "tab_flash_lite_preview": {"tokenType": "WTUS", "remainingFraction": 1}}
        result, account = self.usage(buckets)
        rows = {row["display"]: row for row in result["rows"]}
        self.assertEqual(rows["Gemini — 5 часов"]["used"], 0)
        self.assertEqual(rows["Claude / GPT — 5 часов"]["used"], 42.5)
        self.assertEqual(rows["Claude / GPT — 5 часов"]["display_en"], "Claude / GPT — 5h")
        self.assertEqual(result["subscription"], "Google AI Pro")
        self.assertAlmostEqual(result["next_reset"], antigravity._iso_ts(reset_gemini), delta=1)
        self.assertNotIn("Gemini — неделя", rows)
        self.assertNotIn("notice", result)

        estimate = rows["Claude / GPT — за 7 дней (оценка)"]
        self.assertEqual((estimate["used"], estimate["limit"]), (0.42, None))
        self.assertEqual(estimate["unit_en"], "5-hour windows")
        self.assertEqual(rows["Gemini — за 7 дней (оценка)"]["used"], 0.0)
        self.assertIn("Claude / GPT", account.extra["weekly_quota"])

    def test_a_window_that_ends_in_days_is_shown_as_the_weekly_limit(self) -> None:
        week = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 6 * 86400))
        soon = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
        result, account = self.usage({"gemini-3.6-flash-high": {"resetTime": week},
                                      "claude-opus-4-6-thinking": {"resetTime": soon, "remainingFraction": 0.4}})
        rows = {row["display"]: row for row in result["rows"]}
        self.assertEqual(rows["Gemini — неделя"]["used"], 100.0)
        self.assertAlmostEqual(rows["Gemini — неделя"]["reset"], antigravity._iso_ts(week), delta=1)
        self.assertNotIn("Gemini — 5 часов", rows)
        self.assertNotIn("Gemini — за 7 дней (оценка)", rows)
        self.assertEqual(rows["Claude / GPT — 5 часов"]["used"], 60.0)

    def test_the_estimate_grows_window_by_window(self) -> None:
        account = make_account()
        now = time.time()
        for index in range(3):
            quota.record_point(account.extra, "Gemini", 0.6, now - (index + 1) * 86400 + 3600, now=now - (index + 1) * 86400)
        soon = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 3600))
        result, _ = self.usage({"gemini-3.6-flash-high": {"resetTime": soon, "remainingFraction": 0.5}}, account)
        estimate = {row["display"]: row for row in result["rows"]}["Gemini — за 7 дней (оценка)"]
        self.assertEqual(estimate["used"], 1.7)

    def test_google_asking_to_verify_the_account_becomes_a_notice_not_an_empty_dialog(self) -> None:
        detail = {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "VALIDATION_REQUIRED",
                  "metadata": {"validation_url": "https://accounts.google.com/signin/continue?sarp=1"}}
        reset = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))

        def script(url, kwargs, number):
            if "retrieveUserQuota" in url:
                return JsonResponse(json.loads(google_error(403, "Verify your account to continue.", "PERMISSION_DENIED", detail)),
                                    status=403)
            if "fetchAvailableModels" in url:
                return JsonResponse({"models": {"gemini-3.6-flash-high": {
                    "displayName": "Gemini 3.6 Flash (High)", "quotaInfo": {"remainingFraction": 0.8, "resetTime": reset}}}})
            return JsonResponse({"paidTier": {"name": "Google AI Pro"}})

        provider = antigravity.AntigravityProvider()
        account = make_account()

        async def run():
            with patch("app.providers.antigravity.http.get_session", new=AsyncMock(return_value=Session(script))), \
                    patch.object(state, "save_accounts"), patch.object(state, "save_accounts_soon"), \
                    patch.object(antigravity, "VERSION_CHECK", False):
                usage = await provider.usage(account)
                checked = await provider.check(account)
                return usage, checked

        usage, checked = asyncio.run(run())
        self.assertEqual({row["display"] for row in usage["rows"]}, {"Gemini — 5 часов", "Gemini — за 7 дней (оценка)"})
        self.assertIn("Verify your account", usage["notice"] + usage["notice_en"])
        self.assertTrue(usage["notice_url"].startswith("https://accounts.google.com/"))
        self.assertEqual(usage["subscription"], "Google AI Pro")
        self.assertIs(checked[0], True)
        self.assertIn("⚠", checked[1])

    def test_a_dead_sign_in_is_an_error_message_not_a_silent_empty_dialog(self) -> None:
        provider = antigravity.AntigravityProvider()
        with patch.object(provider, "_ensure_token", AsyncMock(side_effect=ProviderAuthError("Google rejected the refresh (HTTP 400)"))):
            with self.assertRaises(ProviderAuthError):
                asyncio.run(provider.usage(make_account()))

    def test_no_buckets_means_no_data(self) -> None:
        result, _ = self.usage({})
        self.assertIsNone(result)

class AfterChatSamplingTest(WireCase):
    def test_a_successful_chat_schedules_one_background_sample_per_interval(self) -> None:
        account = make_account()
        provider = antigravity.AntigravityProvider()
        soon = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))

        def script(url, kwargs, number):
            if "retrieveUserQuota" in url:
                return JsonResponse({"buckets": [{"modelId": "gemini-3.6-flash-high", "resetTime": soon,
                                                  "remainingFraction": 0.7}]})
            if "fetchAvailableModels" in url:
                return JsonResponse({"models": {}})
            return StreamResponse(200, lines=answer("hi"))

        session = Session(script)

        async def run():
            with patch("app.providers.antigravity.http.get_session", new=AsyncMock(return_value=session)), \
                    patch.object(state, "save_accounts"), patch.object(state, "save_accounts_soon"), \
                    patch.object(antigravity, "SAMPLE_AFTER_CHAT", True):
                for _ in range(2):
                    async for _event in provider.chat_stream(account, "gemini-3.6-flash-high",
                                                             [{"role": "user", "content": "x"}], {}):
                        pass
                await asyncio.gather(*list(provider._tasks))

        asyncio.run(run())
        self.assertEqual(sum(1 for url, _ in session.calls if "retrieveUserQuota" in url), 1)
        self.assertEqual(quota.history_windows(account.extra, "Gemini"), 0.3)

class ErrorBodyTest(unittest.TestCase):
    def test_a_streamed_error_body_is_read_however_the_client_spells_it(self) -> None:
        class ContentOnly:
            text = ""

            async def aiter_content(self):
                yield b'{"error": {"message": "from chunks"}}'

        class Reader:
            text = ""

            async def aread(self):
                return b"from aread"

        class Silent:
            text = "already decoded"

        class Broken:
            text = ""

            async def acontent(self):
                raise RuntimeError("socket closed")

        async def run():
            return [await provider_http.read_error_text(obj()) for obj in (StreamResponse, ContentOnly, Reader, Silent, Broken)]

        texts = asyncio.run(run())
        self.assertEqual(texts[1:], ['{"error": {"message": "from chunks"}}', "from aread", "already decoded", ""])
        self.assertEqual(texts[0], "")

    def test_google_errors_are_parsed_for_their_reason_delay_and_link(self) -> None:
        info = antigravity.parse_google_error(google_error(
            429, "You have exhausted your capacity on this model. Your quota will reset after 3s.", "RESOURCE_EXHAUSTED",
            {"reason": "RATE_LIMIT_EXCEEDED", "metadata": {"quotaResetDelay": "3.2s"}}))
        self.assertEqual((info.reason, info.status, info.retry_after), ("RATE_LIMIT_EXCEEDED", "RESOURCE_EXHAUSTED", 3.2))
        self.assertEqual(antigravity.parse_google_error("Your quota will reset after 1h2m3s").message[:4], "Your")
        text_only = antigravity.parse_google_error(google_error(429, "Your quota will reset after 1h2m3s.", "X"))
        self.assertEqual(text_only.retry_after, 3723)
        self.assertEqual(antigravity.parse_google_error("<html>502</html>").message, "<html>502</html>")
        self.assertIsInstance(antigravity.AntigravityProvider._classify_429(
            google_error(429, "Your quota will reset after 13h.", "X"), PROD), ProviderQuotaError)
        self.assertIsInstance(antigravity.AntigravityProvider._classify_429(
            google_error(429, "Your quota will reset after 3s.", "X", {"reason": "QUOTA_EXHAUSTED"}), PROD),
            ProviderRateLimitError)

class PoolReasonsTest(unittest.TestCase):
    def test_a_blocked_model_remembers_why_and_a_check_lifts_it(self) -> None:
        pool = AccountPool()
        account = Account("antigravity", {}, id="a1")
        pool.block_model(account, "Gemini-X", reason="Verify your account")
        self.assertTrue(pool.model_blocked(account, "gemini-x"))
        self.assertEqual(pool.block_reason([account], "GEMINI-X"), "Verify your account")
        self.assertEqual(pool.clear_account_blocks("a1"), 1)
        self.assertFalse(pool.model_blocked(account, "gemini-x"))
        self.assertEqual(pool.block_reason([account], "gemini-x"), "")

    def test_the_reason_reaches_the_clients_error(self) -> None:
        from app.chat import ChatService, CompletionError
        from app.routing import Resolved

        class Provider:
            id = "test"

            async def chat_stream(self, account, *_args, **_kwargs):
                raise ProviderModelUnavailableError("Google asks to verify this account", "model")
                yield  # pragma: no cover

        async def run():
            service = ChatService()
            accounts = [Account("test", {}, id="one"), Account("test", {}, id="two")]
            with patch.object(config_store.settings, "proxy_enabled", True), \
                    patch("app.chat.resolve", return_value=Resolved(None, "test", "model", "test")), \
                    patch.object(service, "_provider", return_value=Provider()), \
                    patch("app.chat.pool.ordered_candidates", return_value=accounts), \
                    patch("app.chat.pool.report_failure"):
                with self.assertRaises(CompletionError) as caught:
                    await anext(service.stream_completion("test-model", [{"role": "user", "content": "x"}], {}))
            return caught.exception

        error = asyncio.run(run())
        self.assertEqual(error.status, 404)
        self.assertIn("Google asks to verify this account", str(error))

if __name__ == "__main__":
    unittest.main()
