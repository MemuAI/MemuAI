"""Claude Code / Codex CLI install-reset contract: merge, never clobber.
python -m unittest tests.cli_integrations_test -v
"""
from __future__ import annotations

import json
import os
import tempfile
import tomllib
import unittest
from pathlib import Path

from app import cli_integrations as cli


class _TempHome(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = self._tmp.name
        self.state_path = os.path.join(self.home, "cli_state.json")

    def tearDown(self):
        self._tmp.cleanup()


class ClaudeCodeTests(_TempHome):
    def test_apply_on_missing_file_creates_settings_with_env_block(self):
        result = cli.apply_claude({"sonnet": "genspark-claude-sonnet-4-5"}, home=self.home,
                                  state_path=self.state_path, base_url="http://127.0.0.1:8317/v1",
                                  api_key="secret-key")
        self.assertTrue(result["ok"])
        path = cli.claude_settings_path(self.home)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["env"]["ANTHROPIC_BASE_URL"], "http://127.0.0.1:8317/v1")
        self.assertEqual(data["env"]["ANTHROPIC_AUTH_TOKEN"], "secret-key")
        self.assertEqual(data["env"]["ANTHROPIC_DEFAULT_SONNET_MODEL"], "genspark-claude-sonnet-4-5")
        self.assertNotIn(path + ".memuai-backup", "")  # no backup: no original file existed
        self.assertFalse(os.path.exists(path + ".memuai-backup"))

    def test_apply_merges_and_preserves_other_keys_and_env_vars(self):
        path = cli.claude_settings_path(self.home)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"theme": "dark", "env": {"MY_OWN_VAR": "keepme", "ANTHROPIC_MODEL": "user-had-this"}}, f)

        result = cli.apply_claude({"model": "genspark-claude-opus-4-5"}, home=self.home,
                                  state_path=self.state_path, base_url="http://127.0.0.1:8317/v1",
                                  api_key="")
        self.assertTrue(result["ok"])
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["theme"], "dark")
        self.assertEqual(data["env"]["MY_OWN_VAR"], "keepme")
        self.assertEqual(data["env"]["ANTHROPIC_MODEL"], "genspark-claude-opus-4-5")
        # No API key configured on the proxy -> a non-empty placeholder token,
        # since Claude Code refuses to start with an empty token.
        self.assertTrue(data["env"]["ANTHROPIC_AUTH_TOKEN"])
        # A pristine backup of the ORIGINAL file (before any MemuAI edit).
        backup = path + ".memuai-backup"
        self.assertTrue(os.path.exists(backup))
        with open(backup, encoding="utf-8") as f:
            original = json.load(f)
        self.assertEqual(original["env"]["ANTHROPIC_MODEL"], "user-had-this")
        self.assertNotIn("ANTHROPIC_BASE_URL", original.get("env", {}))

    def test_reset_restores_prior_value_and_removes_keys_that_did_not_exist(self):
        path = cli.claude_settings_path(self.home)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"env": {"ANTHROPIC_MODEL": "user-original-model"}}, f)

        cli.apply_claude({"model": "genspark-x", "sonnet": "genspark-y"}, home=self.home,
                         state_path=self.state_path, base_url="http://127.0.0.1:8317/v1", api_key="key1")
        result = cli.reset_claude(home=self.home, state_path=self.state_path)
        self.assertTrue(result["ok"])
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["env"]["ANTHROPIC_MODEL"], "user-original-model")
        self.assertNotIn("ANTHROPIC_BASE_URL", data["env"])
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", data["env"])
        self.assertNotIn("ANTHROPIC_DEFAULT_SONNET_MODEL", data["env"])

    def test_reset_without_prior_apply_is_a_harmless_noop(self):
        result = cli.reset_claude(home=self.home, state_path=self.state_path)
        self.assertTrue(result["ok"])
        self.assertTrue(result.get("already"))

    def test_deselecting_a_slot_restores_it_without_a_full_reset(self):
        cli.apply_claude({"sonnet": "model-a", "haiku": "model-b"}, home=self.home,
                         state_path=self.state_path, base_url="http://x/v1", api_key="k")
        cli.apply_claude({"sonnet": "model-a"}, home=self.home,  # haiku deselected
                         state_path=self.state_path, base_url="http://x/v1", api_key="k")
        path = cli.claude_settings_path(self.home)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertNotIn("ANTHROPIC_DEFAULT_HAIKU_MODEL", data["env"])
        self.assertEqual(data["env"]["ANTHROPIC_DEFAULT_SONNET_MODEL"], "model-a")

    def test_invalid_json_is_refused_not_overwritten(self):
        path = cli.claude_settings_path(self.home)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{not valid json,,,")
        with self.assertRaises(cli.IntegrationError):
            cli.apply_claude({"model": "x"}, home=self.home, state_path=self.state_path,
                             base_url="http://x/v1", api_key="k")
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "{not valid json,,,")

    def test_non_object_json_is_refused(self):
        path = cli.claude_settings_path(self.home)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("[1, 2, 3]")
        with self.assertRaises(cli.IntegrationError):
            cli.apply_claude({"model": "x"}, home=self.home, state_path=self.state_path,
                             base_url="http://x/v1", api_key="k")

    def test_status_reflects_installed_state(self):
        self.assertFalse(cli.claude_status(home=self.home, state_path=self.state_path)["installed"])
        cli.apply_claude({"model": "x"}, home=self.home, state_path=self.state_path,
                         base_url="http://x/v1", api_key="k")
        status = cli.claude_status(home=self.home, state_path=self.state_path)
        self.assertTrue(status["installed"])
        self.assertEqual(status["env"]["ANTHROPIC_MODEL"], "x")

    def test_direct_connection_uses_own_endpoint_without_exposing_key_in_status(self):
        result = cli.apply_claude({"model": "own-model"}, home=self.home,
                                  state_path=self.state_path, base_url="https://own.example/v1",
                                  api_key="own-secret", connection="direct")
        data = json.loads(Path(cli.claude_settings_path(self.home)).read_text(encoding="utf-8"))
        self.assertEqual(data["env"]["ANTHROPIC_BASE_URL"], "https://own.example/v1")
        self.assertEqual(data["env"]["ANTHROPIC_AUTH_TOKEN"], "own-secret")
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", result["env"])
        status = cli.claude_status(home=self.home, state_path=self.state_path)
        self.assertEqual(status["connection"], "direct")
        self.assertTrue(status["api_key_set"])
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", status["env"])

    def test_direct_connection_without_key_does_not_claim_a_saved_key(self):
        cli.apply_claude({"model": "local-model"}, home=self.home,
                         state_path=self.state_path, base_url="http://127.0.0.1:9999/v1",
                         api_key="", connection="direct")
        status = cli.claude_status(home=self.home, state_path=self.state_path)
        self.assertFalse(status["api_key_set"])


class CodexTests(_TempHome):
    def test_apply_on_missing_file_creates_provider_and_profiles(self):
        result = cli.apply_codex([{"name": "opus", "model": "genspark-claude-opus-4-5"},
                                  {"name": "sonnet", "model": "genspark-claude-sonnet-4-5"}],
                                 default="opus", home=self.home, state_path=self.state_path,
                                 base_url="http://127.0.0.1:8317/v1", api_key="")
        self.assertTrue(result["ok"])
        path = cli.codex_config_path(self.home)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        doc = tomllib.loads(text)
        self.assertEqual(doc["model_providers"]["memuai"]["base_url"], "http://127.0.0.1:8317/v1")
        self.assertEqual(doc["model_providers"]["memuai"]["wire_api"], "responses")
        self.assertNotIn("env_key", doc["model_providers"]["memuai"])  # no api key configured
        self.assertEqual(doc["model_provider"], "memuai")
        self.assertEqual(doc["model"], "genspark-claude-opus-4-5")
        self.assertEqual(doc["profiles"]["memuai-opus"]["model"], "genspark-claude-opus-4-5")
        self.assertEqual(doc["profiles"]["memuai-sonnet"]["model"], "genspark-claude-sonnet-4-5")

    def test_apply_sets_working_token_when_proxy_requires_a_key(self):
        result = cli.apply_codex([{"model": "m1"}], home=self.home, state_path=self.state_path,
                                 base_url="http://x/v1", api_key="proxy-secret")
        self.assertTrue(result["ok"])
        doc = tomllib.loads(Path(cli.codex_config_path(self.home)).read_text(encoding="utf-8"))
        self.assertEqual(doc["model_providers"]["memuai"]["experimental_bearer_token"],
                         "proxy-secret")
        self.assertNotIn("env_key", doc["model_providers"]["memuai"])
        cli.reset_codex(home=self.home, state_path=self.state_path)
        self.assertNotIn("proxy-secret", Path(cli.codex_config_path(self.home)).read_text(encoding="utf-8"))

    def test_apply_preserves_unrelated_existing_content(self):
        path = cli.codex_config_path(self.home)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        original = (
            'approval_policy = "on-request"\n\n'
            '[model_providers.openai]\n'
            'name = "OpenAI"\n'
            'base_url = "https://api.openai.com/v1"\n'
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write(original)

        result = cli.apply_codex([{"model": "m1"}], home=self.home, state_path=self.state_path,
                                 base_url="http://x/v1", api_key="")
        self.assertTrue(result["ok"])
        text = Path(path).read_text(encoding="utf-8")
        doc = tomllib.loads(text)
        self.assertEqual(doc["approval_policy"], "on-request")
        self.assertEqual(doc["model_providers"]["openai"]["name"], "OpenAI")
        self.assertIn("model_providers", doc)
        self.assertIn("memuai", doc["model_providers"])
        # The user already had no top-level model/model_provider -> safe to set ours.
        self.assertEqual(doc["model_provider"], "memuai")

    def test_apply_does_not_clobber_existing_top_level_default(self):
        path = cli.codex_config_path(self.home)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write('model = "gpt-5"\nmodel_provider = "openai"\n\n[model_providers.openai]\nname = "OpenAI"\n')

        result = cli.apply_codex([{"model": "m1"}], home=self.home, state_path=self.state_path,
                                 base_url="http://x/v1", api_key="")
        self.assertTrue(result["ok"])
        self.assertFalse(result["default_set"])
        doc = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        self.assertEqual(doc["model"], "gpt-5")
        self.assertEqual(doc["model_provider"], "openai")
        self.assertIn("memuai-m1", doc["profiles"])

    def test_apply_refuses_when_our_own_provider_table_already_exists_unmanaged(self):
        path = cli.codex_config_path(self.home)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write('[model_providers.memuai]\nname = "hand written"\n')
        with self.assertRaises(cli.IntegrationError):
            cli.apply_codex([{"model": "m1"}], home=self.home, state_path=self.state_path,
                            base_url="http://x/v1", api_key="")

    def test_reset_removes_managed_block_and_keeps_rest(self):
        path = cli.codex_config_path(self.home)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write('approval_policy = "on-request"\n')
        cli.apply_codex([{"model": "m1"}, {"model": "m2"}], home=self.home, state_path=self.state_path,
                        base_url="http://x/v1", api_key="")
        result = cli.reset_codex(home=self.home, state_path=self.state_path)
        self.assertTrue(result["ok"])
        text = Path(path).read_text(encoding="utf-8")
        doc = tomllib.loads(text)
        self.assertEqual(doc, {"approval_policy": "on-request"})
        self.assertNotIn("MemuAI", text)

    def test_reset_without_prior_apply_is_a_harmless_noop(self):
        result = cli.reset_codex(home=self.home, state_path=self.state_path)
        self.assertTrue(result["ok"])
        self.assertTrue(result.get("already"))

    def test_reapply_replaces_rather_than_duplicates_block(self):
        cli.apply_codex([{"model": "m1"}], home=self.home, state_path=self.state_path,
                        base_url="http://x/v1", api_key="")
        cli.apply_codex([{"model": "m2"}], home=self.home, state_path=self.state_path,
                        base_url="http://x/v1", api_key="")
        text = Path(cli.codex_config_path(self.home)).read_text(encoding="utf-8")
        self.assertEqual(text.count("model_providers.memuai"), 1)
        doc = tomllib.loads(text)
        self.assertNotIn("memuai-m1", doc.get("profiles", {}))
        self.assertIn("memuai-m2", doc.get("profiles", {}))

    def test_invalid_toml_is_refused_not_overwritten(self):
        path = cli.codex_config_path(self.home)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        broken = "this = is [ not valid toml"
        with open(path, "w", encoding="utf-8") as f:
            f.write(broken)
        with self.assertRaises(cli.IntegrationError):
            cli.apply_codex([{"model": "m1"}], home=self.home, state_path=self.state_path,
                            base_url="http://x/v1", api_key="")
        self.assertEqual(Path(path).read_text(encoding="utf-8"), broken)

    def test_apply_requires_at_least_one_model(self):
        with self.assertRaises(cli.IntegrationError):
            cli.apply_codex([], home=self.home, state_path=self.state_path,
                            base_url="http://x/v1", api_key="")

    def test_profile_names_are_slugified_and_deduplicated(self):
        result = cli.apply_codex([{"name": "My Cool Model!", "model": "m1"},
                                  {"name": "My Cool Model!", "model": "m2"}],
                                 home=self.home, state_path=self.state_path,
                                 base_url="http://x/v1", api_key="")
        names = [p["name"] for p in result["profiles"]]
        self.assertEqual(len(names), len(set(names)))
        for name in names:
            self.assertTrue(name.startswith("memuai-"))

    def test_status_reflects_installed_state(self):
        self.assertFalse(cli.codex_status(home=self.home, state_path=self.state_path)["installed"])
        cli.apply_codex([{"model": "m1"}], home=self.home, state_path=self.state_path,
                        base_url="http://x/v1", api_key="")
        status = cli.codex_status(home=self.home, state_path=self.state_path)
        self.assertTrue(status["installed"])
        self.assertEqual(len(status["profiles"]), 1)

    def test_direct_connection_sets_own_responses_endpoint(self):
        cli.apply_codex([{"model": "own-model"}], home=self.home,
                        state_path=self.state_path, base_url="https://own.example/v1",
                        api_key="own-secret", connection="direct")
        document = tomllib.loads(Path(cli.codex_config_path(self.home)).read_text(encoding="utf-8"))
        provider = document["model_providers"]["memuai"]
        self.assertEqual(provider["base_url"], "https://own.example/v1")
        self.assertEqual(provider["experimental_bearer_token"], "own-secret")
        self.assertEqual(provider["name"], "Direct API")
        status = cli.codex_status(home=self.home, state_path=self.state_path)
        self.assertEqual(status["connection"], "direct")
        self.assertEqual(status["base_url"], "https://own.example/v1")


class DirectConnectionValidationTests(unittest.TestCase):
    def test_direct_connection_rejects_credentials_in_url_and_newlines_in_key(self):
        from app.proxy_tools_api import _cli_connection

        with self.assertRaises(cli.IntegrationError):
            _cli_connection({"connection": "direct", "base_url": "https://user:pass@own.example/v1"})
        with self.assertRaises(cli.IntegrationError):
            _cli_connection({"connection": "direct", "base_url": "https://own.example/v1",
                             "api_key": "good\r\nInjected: bad"})
        self.assertEqual(_cli_connection({"connection": "direct", "base_url": "https://own.example/v1/",
                                           "api_key": "secret"})["base_url"], "https://own.example/v1")


class PathHelpersTests(unittest.TestCase):
    def test_home_dir_prefers_explicit_argument_over_env_override(self):
        self.assertEqual(cli.home_dir("/explicit"), "/explicit")

    def test_home_dir_uses_env_override_when_no_explicit_home(self):
        old = os.environ.get("MEMUAI_HOME_OVERRIDE")
        os.environ["MEMUAI_HOME_OVERRIDE"] = "/env-home"
        try:
            self.assertEqual(cli.home_dir(None), "/env-home")
        finally:
            if old is None:
                os.environ.pop("MEMUAI_HOME_OVERRIDE", None)
            else:
                os.environ["MEMUAI_HOME_OVERRIDE"] = old

    def test_proxy_base_url_never_returns_bind_all_address(self):
        from unittest.mock import patch
        from app.config import config_store
        with patch.object(type(config_store), "effective_host", property(lambda self: "0.0.0.0")), \
             patch.object(type(config_store), "effective_port", property(lambda self: 8317)):
            self.assertEqual(cli.proxy_base_url(), "http://127.0.0.1:8317/v1")


if __name__ == "__main__":
    unittest.main()
