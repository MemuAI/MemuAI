from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import app.admin_api as admin_api
from app.config import config_store
from app.main import create_app
from app.state import Account

ROOT = Path(__file__).resolve().parents[1]

class ProxyAdminApiContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.host_patch = patch.object(config_store, "_runtime_host", "127.0.0.1")
        self.port_patch = patch.object(config_store, "_runtime_port", 8317)
        self.host_patch.start()
        self.port_patch.start()
        self.client = TestClient(create_app(), base_url="http://127.0.0.1:8317")

    def tearDown(self) -> None:
        self.client.close()
        self.port_patch.stop()
        self.host_patch.stop()

    def test_management_endpoints_reject_non_object_and_wrong_field_types(self) -> None:
        for path in ("/api/admin/settings", "/api/admin/accounts", "/api/admin/models"):
            response = self.client.post(path, content=b"[]", headers={"Content-Type": "application/json"})
            self.assertEqual(response.status_code, 400, path)
            self.assertIn("JSON-объект", response.json()["error"])

        response = self.client.post("/api/admin/settings", json={"settings": []})
        self.assertEqual(response.status_code, 400)
        response = self.client.post("/api/admin/accounts", json={"provider": "grok", "fields": []})
        self.assertEqual(response.status_code, 400)
        response = self.client.post("/api/admin/models", json={
            "prefix": "grok", "model": "test", "enabled": "false",
        })
        self.assertEqual(response.status_code, 400)

    def test_oversized_or_invalid_content_length_is_rejected(self) -> None:
        oversized = b'{"password":"' + (b"x" * 70_000) + b'"}'
        response = self.client.post("/api/admin/login", content=oversized,
                                    headers={"Content-Type": "application/json"})
        self.assertEqual(response.status_code, 413)

        response = self.client.post("/api/admin/login", content=b"{}", headers={
            "Content-Type": "application/json", "Content-Length": "invalid",
        })
        self.assertEqual(response.status_code, 400)

    def test_new_account_priority_is_preserved(self) -> None:
        provider = Mock()
        provider.validate_fields.return_value = None
        provider.seed_models.return_value = []
        account = Account("grok", {"cookie": "very-secret-token-value"}, label="Work")
        with patch.object(admin_api, "get_provider", return_value=provider), \
                patch.object(admin_api.state, "add_account", return_value=account) as add_account, \
                patch.object(admin_api.state, "save_accounts") as save_accounts, \
                patch.object(admin_api.state, "seed_provider_models"):
            response = self.client.post("/api/admin/accounts", json={
                "provider": "grok",
                "fields": {"cookie": "very-secret-token-value"},
                "label": "Work",
                "priority": "reserve",
            })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(account.priority, "reserve")
        self.assertEqual(response.json()["account"]["priority"], "reserve")
        self.assertEqual(response.json()["account"]["fields"]["cookie"], "[REDACTED]")
        add_account.assert_called_once()
        save_accounts.assert_called_once()

    def test_account_update_rejects_string_boolean_without_mutation(self) -> None:
        account = Account("grok", {"cookie": "original-secret"}, label="Original")
        with patch.object(admin_api.state, "get_account", return_value=account), \
                patch.object(admin_api.state, "save_accounts") as save_accounts:
            response = self.client.put(f"/api/admin/accounts/{account.id}", json={
                "enabled": "false", "label": "Changed",
            })
        self.assertEqual(response.status_code, 400)
        self.assertTrue(account.enabled)
        self.assertEqual(account.label, "Original")
        save_accounts.assert_not_called()

    def test_panel_rejects_cross_origin_logout_and_sets_browser_security_headers(self) -> None:
        denied = self.client.post("/api/admin/logout", headers={"Origin": "https://evil.example"})
        self.assertEqual(denied.status_code, 403)

        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.headers["x-frame-options"], "DENY")
        self.assertEqual(page.headers["x-content-type-options"], "nosniff")
        self.assertIn("script-src 'self'", page.headers["content-security-policy"])
        self.assertIn("frame-ancestors 'none'", page.headers["content-security-policy"])

    def test_wildcard_bind_rejects_dns_rebinding_hostnames_but_accepts_lan_ips(self) -> None:
        with patch.object(config_store, "_runtime_host", "0.0.0.0"):
            rebound = self.client.get("/api/admin/overview", headers={
                "Host": "attacker.example:8317", "Origin": "http://attacker.example:8317",
            })
            lan = self.client.get("/api/admin/overview", headers={
                "Host": "192.168.1.10:8317", "Origin": "http://192.168.1.10:8317",
            })
        self.assertEqual(rebound.status_code, 403)
        self.assertEqual(lan.status_code, 401)

    def test_second_launch_can_activate_lan_bound_proxy_only_from_real_loopback(self) -> None:
        local = TestClient(self.client.app, base_url="http://127.0.0.1:8317",
                           client=("127.0.0.1", 53001))
        remote = TestClient(self.client.app, base_url="http://127.0.0.1:8317",
                            client=("192.168.1.25", 53002))
        try:
            with patch.object(config_store, "_runtime_host", "0.0.0.0"), \
                    patch("app.desktop.show_active_window", return_value=True) as show:
                activated = local.post("/api/admin/show_window", json={})
                spoofed = remote.post("/api/admin/show_window", json={}, headers={
                    "X-Forwarded-For": "127.0.0.1",
                })
            self.assertEqual(activated.status_code, 200)
            self.assertTrue(activated.json()["ok"])
            self.assertEqual(spoofed.status_code, 403)
            show.assert_called_once_with()
        finally:
            local.close()
            remote.close()

    def test_memuai_session_token_is_in_child_environment_not_process_arguments(self) -> None:
        def exists(path: str) -> bool:
            return str(path).replace("\\", "/").endswith("/memuai.py")

        with patch.object(admin_api, "_load_json", return_value={}), \
                patch.object(admin_api.os.path, "exists", side_effect=exists), \
                patch("subprocess.Popen") as popen:
            response = self.client.post("/api/admin/open_memuai", json={})

        self.assertEqual(response.status_code, 200)
        argv = popen.call_args.args[0]
        child_env = popen.call_args.kwargs["env"]
        self.assertNotIn("--token", argv)
        self.assertTrue(child_env["MEMUAI_TOKEN"])
        self.assertNotIn(child_env["MEMUAI_TOKEN"], argv)

class ProxyAdminUiContractTest(unittest.TestCase):
    def test_session_modal_secret_and_responsive_contracts(self) -> None:
        html = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "app" / "static" / "style.css").read_text(encoding="utf-8")
        launcher = (ROOT / "memuai.py").read_text(encoding="utf-8")

        self.assertIn('id="login-form"', html)
        self.assertIn('id="password-form"', html)
        self.assertIn('role="status" aria-live="polite"', html)
        self.assertIn('sessionStorage.getItem("oh_token")', js)
        self.assertNotIn('localStorage.getItem("oh_token")', js)
        self.assertIn('localStorage.removeItem("oh_token")', js)
        self.assertIn('$("#app").inert = true', js)
        self.assertIn('if (e.key !== "Tab") return', js)
        self.assertIn("onModalClose(stop)", js)
        self.assertIn('confirmDialog(t("logs.clear") + "?"', js)
        self.assertIn("esc(row.used", js)
        self.assertIn("maskSecret(k)", js)
        self.assertIn(".switch input:focus-visible + .slider", css)
        self.assertIn("@media (max-width: 560px)", css)
        self.assertIn("@media (prefers-reduced-motion: reduce)", css)
        self.assertIn('default=os.environ.get("MEMUAI_TOKEN", "")', launcher)

if __name__ == "__main__":
    unittest.main()
