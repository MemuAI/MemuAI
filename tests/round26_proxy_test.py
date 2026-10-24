"""Round 26 — MemuAI Proxy: models of a provider without accounts (the user's Genspark report),
legacy re-import, catalogue import/manual add, Responses usage details, bilingual validation errors."""
from __future__ import annotations

import os
import tempfile

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

import json  # noqa: E402
import sys  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import patch  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app.admin_api as admin_api  # noqa: E402
import app.state as state_module  # noqa: E402
from app import responses_api, routing  # noqa: E402
from app.config import config_store  # noqa: E402
from app.main import create_app  # noqa: E402
from app.state import Account, ModelEntry, state  # noqa: E402


def _catalogue():
    rows = [ModelEntry("genspark", "genspark", "claude-opus-5"), ModelEntry("genspark", "genspark", "nano-banana-pro"),
            ModelEntry("kiro", "kiro", "claude-sonnet-4-6"), ModelEntry("local", "local", "qwen3"),
            ModelEntry("mysite", "custom", "gpt-x", account_id="c1"),
            ModelEntry("gone", "custom", "gpt-y", account_id="c-deleted")]
    return {row.id: row for row in rows}


class _StateCase(unittest.TestCase):
    def setUp(self) -> None:
        self.accounts = {"k": Account("kiro", {}, id="k"), "c1": Account("custom", {"name": "mysite"}, id="c1")}
        self.models = _catalogue()
        self.patches = [patch.object(state, "accounts", self.accounts), patch.object(state, "models", self.models),
                        patch.object(state, "_unparsed_accounts", []), patch.object(state, "save_models"),
                        patch.object(state, "save_accounts")]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()


class OrphanModelsHiddenTest(_StateCase):
    """Models of a provider whose last account is gone must not be listed anywhere, even before a prune."""

    def test_lists_show_only_models_someone_can_serve(self) -> None:
        listed = sorted(m.id for m in state.enabled_models())
        self.assertEqual(listed, ["kiro-claude-sonnet-4-6", "local-qwen3", "mysite-gpt-x"])
        self.assertEqual(sorted(row["id"] for row in state.list_models()), listed)
        self.assertEqual(sorted(m.id for m in state.catalog_models()), listed)
        self.assertIsNone(state.find_model("genspark-claude-opus-5"))
        self.assertIsNone(state.find_model("gone-gpt-y"))

    def test_short_name_does_not_route_to_a_deleted_provider(self) -> None:
        with patch.object(config_store.settings, "default_provider", ""):
            self.assertIsNone(routing.resolve("claude-opus-5"))
            resolved = routing.resolve("gpt-x")
        self.assertEqual(resolved.provider, "custom")

    def test_models_return_with_the_account_and_keep_their_settings(self) -> None:
        self.models["genspark-claude-opus-5"].enabled = False
        self.accounts["g"] = Account("genspark", {"cookie": "session_id=x"}, id="g")
        self.assertIn("genspark-claude-opus-5", [row["id"] for row in state.list_models()])
        self.assertNotIn("genspark-claude-opus-5", [m.id for m in state.enabled_models()])

    def test_an_unreadable_account_row_keeps_its_models_visible(self) -> None:
        with patch.object(state, "_unparsed_accounts", [{"provider": "custom", "fields": {"name": "gone"}}, "junk"]):
            self.assertIn("gone-gpt-y", [m.id for m in state.enabled_models()])

    def test_prune_also_removes_models_of_deleted_custom_connections(self) -> None:
        self.assertEqual(state.prune_orphan_models(), 3)
        self.assertEqual(sorted(self.models), ["kiro-claude-sonnet-4-6", "local-qwen3", "mysite-gpt-x"])


class LegacyImportTest(unittest.TestCase):
    """Deleting every account used to bring the old genspark2api account and models back on every start."""

    def make_legacy(self, root: Path) -> Path:
        legacy = root / "genspark2api" / "data"
        legacy.mkdir(parents=True)
        (legacy / "accounts.json").write_text(json.dumps([{"cookie": "abc"}]), encoding="utf-8")
        (legacy / "models.json").write_text(json.dumps([{"id": "gpt-5", "upstream": "gpt-5"}]), encoding="utf-8")
        return legacy

    def run_import(self, legacy: Path, imported_from: str, accounts=None):
        fresh = state_module.State.__new__(state_module.State)
        fresh._lock = state_module.threading.RLock()
        fresh.accounts = dict(accounts or {})
        fresh.models = {}
        fresh._unparsed_accounts = []
        fresh._unparsed_models = []
        with patch.object(state_module, "LEGACY_DIR_CANDIDATES", [str(legacy)]), \
                patch.object(config_store.settings, "imported_from", imported_from), \
                patch.object(config_store, "apply_updates"), \
                patch.object(fresh, "save_accounts", create=True), patch.object(fresh, "save_models", create=True):
            fresh._import_legacy()
        return fresh

    def test_import_happens_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            legacy = self.make_legacy(Path(tmp))
            first = self.run_import(legacy, "")
            self.assertEqual([a.provider for a in first.accounts.values()], ["genspark"])
            self.assertEqual(list(first.models), ["genspark-gpt-5"])
            again = self.run_import(legacy, str(legacy))
            self.assertEqual(again.accounts, {}, "a deleted Genspark account must not come back")
            self.assertEqual(again.models, {}, "no Genspark models without a Genspark account")

    def test_legacy_models_need_a_genspark_account(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            legacy = self.make_legacy(Path(tmp))
            kept = self.run_import(legacy, str(legacy), {"k": Account("kiro", {}, id="k")})
            self.assertEqual(kept.models, {})

    def test_the_data_folder_is_not_its_own_legacy_source(self) -> None:
        own = os.path.normcase(os.path.abspath(state_module.DATA_DIR))
        self.assertNotIn(own, [os.path.normcase(os.path.abspath(c)) for c in state_module.LEGACY_DIR_CANDIDATES])


class AdminCase(_StateCase):
    def setUp(self) -> None:
        super().setUp()
        self.host_patch = patch.object(config_store, "_runtime_host", "127.0.0.1")
        self.port_patch = patch.object(config_store, "_runtime_port", 8317)
        self.host_patch.start()
        self.port_patch.start()
        self.client = TestClient(create_app(), base_url="http://127.0.0.1:8317")

    def tearDown(self) -> None:
        self.client.close()
        self.port_patch.stop()
        self.host_patch.stop()
        super().tearDown()


class AdminCatalogueTest(AdminCase):
    def test_overview_does_not_count_models_of_a_deleted_provider(self) -> None:
        data = self.client.get("/api/admin/overview").json()
        tiles = {row["id"]: row["models"] for row in data["providers"]}
        self.assertEqual(tiles["genspark"], 0)
        self.assertEqual(data["models_total"], 3)

    def test_v1_models_hides_orphans(self) -> None:
        with patch.object(config_store.settings, "api_keys", []):
            ids = [row["id"] for row in self.client.get("/v1/models").json()["data"]]
        self.assertFalse([i for i in ids if "genspark" in i or i.startswith("gone")], ids)

    def test_cached_genspark_media_catalogue_needs_an_account(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "media_models.json"
            media.write_text(json.dumps({"image": ["gpt-image-2", "flux-2-pro"]}), encoding="utf-8")
            with patch.object(admin_api, "_memu_file", lambda name: Path(tmp) / name):
                data = self.client.get("/api/admin/pg/state").json()
                self.assertEqual(data["image_models"], [])
                self.assertFalse([m for m in data["models"] if "genspark" in m["id"]])
                self.accounts["g"] = Account("genspark", {"cookie": "session_id=x"}, id="g")
                data = self.client.get("/api/admin/pg/state").json()
                self.assertIn("genspark-image/gpt-image-2", data["image_models"])

    def test_a_broken_media_cache_does_not_break_the_state(self) -> None:
        self.accounts["g"] = Account("genspark", {"cookie": "session_id=x"}, id="g")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "media_models.json").write_text("[1, 2]", encoding="utf-8")
            with patch.object(admin_api, "_memu_file", lambda name: Path(tmp) / name):
                self.assertEqual(self.client.get("/api/admin/pg/state").status_code, 200)

    def test_manual_model_needs_an_owner(self) -> None:
        response = self.client.post("/api/admin/models", json={"prefix": "genspark", "model": "gpt-5"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error_en"], "Add an account or connection with this prefix first")
        self.assertNotIn("genspark-gpt-5", self.models)
        ok = self.client.post("/api/admin/models", json={"prefix": "mysite", "model": "gpt-z"})
        self.assertEqual(ok.status_code, 200)
        self.assertIn("mysite-gpt-z", self.models)

    def test_import_skips_rows_without_an_account_and_saves_once(self) -> None:
        rows = [{"prefix": "genspark", "model": "gpt-5"}, {"prefix": "kiro", "model": "claude-haiku-5"},
                {"prefix": "mysite", "model": "m2"}]
        with patch.object(state, "save_models") as save:
            data = self.client.post("/api/admin/models/import", json={"models": rows}).json()
        self.assertEqual((data["added"], data["skipped"]), (2, 1))
        self.assertNotIn("genspark-gpt-5", self.models)
        save.assert_called_once()

    def test_validation_errors_are_bilingual(self) -> None:
        cases = [({"provider": "custom", "fields": {"name": "", "base_url": "x"}}, "Enter a name (prefix)"),
                 ({"provider": "custom", "fields": {"name": "kiro", "base_url": "https://x"}},
                  "The prefix “kiro” belongs to a built-in provider"),
                 ({"provider": "custom", "fields": {"name": "abc", "base_url": "ftp://x"}},
                  "Base URL must start with http(s)://")]
        for body, english in cases:
            data = self.client.post("/api/admin/accounts", json=body).json()
            self.assertEqual(data["error_en"], english)
            self.assertRegex(data["error"], "[а-яА-Я]")
        grok = self.client.post("/api/admin/accounts", json={"provider": "grok", "fields": {}}).json()
        self.assertTrue(grok["error"].startswith("Заполните поле «"), grok)
        self.assertTrue(grok["error_en"].startswith("Fill in the “"), grok)
        self.assertNotRegex(grok["error_en"], "[а-яА-Я]")


class ResponsesUsageTest(unittest.TestCase):
    def test_reasoning_and_cached_tokens_reach_the_client(self) -> None:
        usage = {"prompt_tokens": 40, "completion_tokens": 50, "total_tokens": 90,
                 "prompt_tokens_details": {"cached_tokens": 7}, "completion_tokens_details": {"reasoning_tokens": 33}}
        body = responses_api._response("resp_1", "m", [], usage)["usage"]
        self.assertEqual(body["output_tokens_details"], {"reasoning_tokens": 33})
        self.assertEqual(body["input_tokens_details"], {"cached_tokens": 7})
        plain = responses_api._response("resp_1", "m", [], {"prompt_tokens": 1})["usage"]
        self.assertEqual(plain["output_tokens_details"], {"reasoning_tokens": 0})


class MetricsLastHourTest(unittest.TestCase):
    def test_requests_before_a_restart_still_count_for_the_last_hour(self) -> None:
        from app import metrics as metrics_module
        with tempfile.TemporaryDirectory() as tmp:
            first = metrics_module.Metrics(os.path.join(tmp, "metrics.json"))
            for ok in (True, True, False):
                first.record(provider="custom", model="m", ok=ok, ms=100)
            first.flush(force=True)
            restarted = metrics_module.Metrics(os.path.join(tmp, "metrics.json"))
            snap = restarted.snapshot("day")
            self.assertEqual((snap["last_hour"], snap["last_hour_failed"]), (3, 1))
            restarted.record(provider="custom", model="m", ok=True, ms=100)
            snap = restarted.snapshot("day")
            self.assertEqual((snap["last_hour"], snap["last_hour_failed"]), (4, 1), "no double counting")


class DetectorClaimTest(unittest.TestCase):
    def test_uri_schemes_and_folders_are_not_claims(self) -> None:
        from app import detector
        yandex = "yandex-gpt://b1g4plmmckqmvs52tqqf/deepseek-v4.1-flash/latest"
        self.assertEqual(detector.claimed_family(yandex), "deepseek", "Yandex's gpt:// scheme is not a GPT claim")
        self.assertNotEqual((detector.expected_model(yandex) or {}).get("id", "").split("-")[0], "gpt")
        self.assertEqual(detector.claimed_family("xkiro-qwen/qwen3-vl-plus:free"), "qwen")
        self.assertEqual(detector.claimed_family("genspark-claude-opus-5"), "claude")
        self.assertEqual(detector.claimed_family("openrouter-openai/gpt-oss-120b:free"), "gpt")

    def test_a_connection_name_is_not_a_claim(self) -> None:
        from app import detector
        entry = ModelEntry("gptproxy", "custom", "claude-sonnet-4-6")
        with patch.object(state, "models", {entry.id: entry}), \
                patch.object(state, "accounts", {"c": Account("custom", {"name": "gptproxy"}, id="c")}):
            self.assertEqual(detector.claimed_family(entry.id), "claude")


class ProxyUiTest(unittest.TestCase):
    def test_genspark_catalogue_tools_follow_the_account(self) -> None:
        text = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("function syncGensparkModelTools()", text)
        self.assertGreaterEqual(text.count("syncGensparkModelTools();"), 2)


if __name__ == "__main__":
    unittest.main()
