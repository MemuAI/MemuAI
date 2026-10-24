"""Kiro Builder ID: the panel's device-code button reaches a real route."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


class DeviceRouteTests(unittest.TestCase):
    def test_device_start_route_exists_and_reports_the_code(self) -> None:
        from app.main import create_app
        from app.providers.kiro import KiroProvider

        async def fake_start(self):
            return {"ok": True, "flow_id": "abc", "user_code": "WXYZ-1234",
                    "verification_uri": "https://device.sso.us-east-1.amazonaws.com/"}

        with patch.object(KiroProvider, "oauth_device_start", fake_start):
            with TestClient(create_app(), base_url="http://127.0.0.1") as client:
                response = client.post("/api/admin/oauth/kiro/device_start")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["user_code"], "WXYZ-1234")
                other = client.post("/api/admin/oauth/grok/device_start")
                self.assertEqual(other.status_code, 400)
                self.assertIn("error_en", other.json())

    def test_the_panel_calls_this_route(self) -> None:
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("/api/admin/oauth/kiro/device_start", source)


if __name__ == "__main__":
    unittest.main()
