"""Codex desktop app / IDE extension: MemuAI as the default model without losing the user's own keys."""
from __future__ import annotations

import os
import tempfile

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

import tomllib  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import patch  # noqa: E402

from app import cli_integrations as cli  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
USER = '''# my codex config
model = "gpt-5.3-codex"
model_reasoning_effort = "high"
profile = "work"

[profiles.work]
model = "gpt-5.3-codex"
model_provider = "openai"

[mcp_servers.docs]
command = "docs-mcp"
'''

class CodexAppModeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.home = tempfile.mkdtemp()
        self.state = os.path.join(self.home, "state.json")
        os.makedirs(os.path.join(self.home, ".codex"))
        self.path = os.path.join(self.home, ".codex", "config.toml")
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(USER)

    def apply(self, app: bool, models=("claude-opus-5-5", "gpt-6-sol"), key: str = "sek") -> dict:
        return cli.apply_codex([{"model": model} for model in models], home=self.home, state_path=self.state,
                               base_url="http://127.0.0.1:8317/v1", api_key=key, app=app)

    def doc(self) -> dict:
        with open(self.path, encoding="utf-8") as handle:
            return tomllib.loads(handle.read())

    def test_without_app_mode_the_user_default_wins_and_the_note_points_to_the_option(self) -> None:
        result = self.apply(False)
        self.assertFalse(result["default_set"])
        self.assertFalse(result["app_mode"])
        self.assertIn("приложения Codex", result["note"])
        self.assertEqual(self.doc()["model"], "gpt-5.3-codex")

    def test_app_mode_makes_memuai_the_default_and_keeps_everything_else(self) -> None:
        result = self.apply(True)
        self.assertTrue(result["default_set"] and result["app_mode"])
        self.assertEqual(result["taken_over"], ["model", "profile"])
        doc = self.doc()
        self.assertEqual((doc["model"], doc["model_provider"]), ("claude-opus-5-5", "memuai"))
        self.assertNotIn("profile", doc, "an active profile would override the default the app uses")
        self.assertEqual(doc["model_reasoning_effort"], "high")
        self.assertEqual(doc["mcp_servers"]["docs"]["command"], "docs-mcp")
        self.assertIn("work", doc["profiles"])
        self.assertEqual(doc["model_providers"]["memuai"]["experimental_bearer_token"], "sek",
                         "the saved root keys must not leak into the provider table")
        status = cli.codex_status(home=self.home, state_path=self.state)
        self.assertTrue(status["app_mode"])
        self.assertEqual(status["taken_over"], ["model", "profile"])

    def test_reapplying_keeps_the_first_saved_originals(self) -> None:
        self.apply(True)
        self.apply(True, models=("gpt-6-sol",))
        self.assertEqual(self.doc()["model"], "gpt-6-sol")
        cli.reset_codex(home=self.home, state_path=self.state)
        self.assertEqual(self.doc(), tomllib.loads(USER))

    def test_reset_puts_the_original_lines_back(self) -> None:
        self.apply(True)
        cli.reset_codex(home=self.home, state_path=self.state)
        with open(self.path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(tomllib.loads(text), tomllib.loads(USER))
        self.assertTrue(text.startswith("# my codex config\nmodel = \"gpt-5.3-codex\""), text)
        self.assertFalse(cli.codex_status(home=self.home, state_path=self.state)["app_mode"])

    def test_clearing_the_option_restores_the_user_default(self) -> None:
        self.apply(True)
        result = self.apply(False)
        self.assertFalse(result["default_set"])
        self.assertEqual(result["taken_over"], [])
        doc = self.doc()
        self.assertEqual((doc["model"], doc["profile"]), ("gpt-5.3-codex", "work"))
        self.assertIn("memuai-claude-opus-5-5", doc["profiles"])

    def test_a_file_without_own_defaults_needs_no_takeover(self) -> None:
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("[mcp_servers.docs]\ncommand = \"x\"\n")
        result = self.apply(True)
        self.assertEqual((result["app_mode"], result["taken_over"]), (True, []))
        cli.reset_codex(home=self.home, state_path=self.state)
        self.assertEqual(self.doc(), {"mcp_servers": {"docs": {"command": "x"}}})

    def test_the_app_is_detected_from_the_usual_places(self) -> None:
        app = os.path.join(self.home, "Codex.app")
        with patch.dict(os.environ, {"MEMUAI_CODEX_APP_PATHS": os.pathsep.join([os.path.join(self.home, "nope"), app])}):
            self.assertFalse(cli.codex_app_info(self.home)["found"])
            os.makedirs(app)
            self.assertEqual(cli.codex_app_info(self.home), {"found": True, "path": app})
        self.assertTrue(cli.codex_app_candidates(self.home))

class CodexAppWiringTest(unittest.TestCase):
    def test_the_endpoint_and_the_panel_pass_the_option(self) -> None:
        api = (ROOT / "app" / "proxy_tools_api.py").read_text(encoding="utf-8")
        self.assertIn('app=body.get("app") is True', api)
        js = (ROOT / "app" / "static" / "cli_tools.js").read_text(encoding="utf-8")
        for needle in ('id="cli-codex-app"', "Также для приложения Codex", "codex.app_mode", "{ profiles, default: def, app }",
                       "Перезапустите Codex CLI и приложение Codex", "cli-codex-app-found"):
            self.assertIn(needle, js)

if __name__ == "__main__":
    unittest.main()
