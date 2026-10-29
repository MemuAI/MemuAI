# -*- coding: utf-8 -*-
"""Контракт раздела «Интеграции» (Round 4, пункт 7).

Модуль app/integrations.py требует httpx и сети, поэтому тест проверяет контракт
по исходному коду: набор сервисов и инструментов, маскировку секретов,
регистрацию endpoint’ов, прокидывание инструментов в /api/pg/tools и гайд в UI.
"""
import ast
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def source(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as handle:
        return handle.read()

class IntegrationsModuleTest(unittest.TestCase):
    def setUp(self):
        self.text = source("app/integrations.py")
        self.tree = ast.parse(self.text)

    def test_module_parses(self):
        names = {node.name for node in ast.walk(self.tree) if isinstance(node, ast.FunctionDef)}
        for required in ("status", "save_credentials", "disconnect", "test_connection", "run_tool", "service_for_tool"):
            self.assertIn(required, names, required)

    def test_three_services(self):
        for service in ("github", "google_drive", "google_calendar", "gmail", "notion"):
            self.assertIn('"%s"' % service, self.text, service)

    def test_tools_cover_all_services(self):
        expected = (
            "github_status", "github_list_repos", "github_repo_info", "github_list_files",
            "github_read_file", "github_search_code", "github_list_commits", "github_list_issues",
            "github_create_issue", "github_write_file",
            "drive_list_files", "drive_read_file", "drive_create_file", "drive_update_file",
            "calendar_list_calendars", "calendar_list_events", "calendar_create_event", "calendar_delete_event",
        )
        for tool in expected:
            self.assertIn(tool, self.text, tool)

    def test_secrets_are_masked_and_stored_locally(self):
        self.assertIn("integrations.json", self.text)
        self.assertIn("0o600", self.text)
        self.assertIn('"secret"', self.text)

        self.assertIn('"\u2022" * 8', self.text)

    def test_google_refresh_flow(self):

        self.assertIn("oauth2.googleapis.com/token", source("app/gdrive_sync_drive.py"))
        self.assertIn("google_account.access_token", self.text)
        self.assertIn("_tokens", self.text)

class IntegrationsWiringTest(unittest.TestCase):
    def test_endpoints(self):
        api = source("app/playground_api.py")
        self.assertIn('"/api/pg/integrations"', api)
        self.assertIn('"/api/pg/integrations/test"', api)
        self.assertIn("integrations_api.save_credentials", api)
        self.assertIn("integrations_api.disconnect", api)
        self.assertIn("integrations_api.test_connection", api)

    def test_tools_dispatch(self):
        api = source("app/playground_api.py")

        from app import integrations as ig
        dispatch = "\n".join(row for row in api.splitlines() if 'if tool.startswith((' in row)
        for prefix in ig.TOOL_PREFIXES:
            self.assertIn('"%s"' % prefix, dispatch, prefix)
        self.assertIn("integrations_api.run_tool", api)
        self.assertIn("connected_services.run(tool, args, confirmed=body.get(\"confirmed\") is True)", api)

    def test_frontend_pane_and_guide(self):
        js = source("app/pgstatic/app.js")
        for marker in (

            'P("apps"', 'data-set-tab="${p.id}"', 'id="pane-apps"', "int-github-token",
            "MemuIntegrations.listHtml", "MemuIntegrations.subsHtml",
            "data-int-save", "data-int-test", "data-int-off",
        ):
            self.assertIn(marker, js, marker)
        self.assertIn("console.cloud.google.com", source("app/pgstatic/integrations-ui.js"))
        self.assertIn("Personal access tokens", js)

    def test_model_only_learns_it_may_connect(self):
        js = source("app/pgstatic/app.js")
        self.assertIn("integrationsPromptBlock", js)
        self.assertIn("<integrations>", js)
        self.assertIn("hidden memuai-tool protocol", js)

if __name__ == "__main__":
    unittest.main()
