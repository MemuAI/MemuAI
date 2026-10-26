"""Round 44 integration installation and recoverable Telegram setup errors.

Real HTTP routes and filesystem saves; no desktop windows or Telegram account.
"""
from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import browser_extension as extension
from app import chat_export_api as exports
from app import telegram_use as telegram
from app import telegram_use_api as telegram_api
from app import telegram_use_config as config

class ExtensionDownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="memuai-round44-download-")
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.directory = patch.object(exports, "downloads_dir", return_value=self.folder)
        self.directory.start()
        self.addCleanup(self.directory.stop)
        self.app = FastAPI()
        extension.register(self.app, completion=AsyncMock(), proxy_json=AsyncMock())
        self.client = TestClient(self.app, base_url="http://127.0.0.1:8600")
        self.addCleanup(self.client.close)

    def test_native_window_download_writes_real_complete_unpacked_package(self):
        response = self.client.post(extension.PREFIX + "/download", json={})
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertTrue(result["ok"])
        target = Path(result["path"])
        self.assertEqual(target.parent, self.folder)
        self.assertEqual(result["bytes"], target.stat().st_size)
        with zipfile.ZipFile(target) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["manifest_version"], 3)
            self.assertIn("worker.js", archive.namelist())
            self.assertIn(manifest["side_panel"]["default_path"], archive.namelist())
            self.assertIsNone(archive.testzip())

    def test_download_does_not_overwrite_previous_zip(self):
        original = self.folder / "MemuAI-Chrome.zip"
        original.write_bytes(b"keep my previous version")
        response = self.client.post(extension.PREFIX + "/download", json={"mode": "downloads"})
        target = Path(response.json()["path"])
        self.assertNotEqual(target, original)
        self.assertEqual(original.read_bytes(), b"keep my previous version")
        self.assertEqual(target.name, "MemuAI-Chrome (2).zip")

    def test_save_as_uses_native_dialog_and_honors_cancellation(self):
        window = SimpleNamespace(create_file_dialog=Mock(return_value=None))
        self.app.state.window = window
        with patch.dict("sys.modules", {"webview": SimpleNamespace(SAVE_DIALOG=30)}):
            response = self.client.post(extension.PREFIX + "/download", json={"mode": "ask"})
        self.assertTrue(response.json()["cancelled"])
        window.create_file_dialog.assert_called_once()
        self.assertFalse(list(self.folder.iterdir()))

    def test_save_as_writes_to_selected_path_with_zip_extension(self):
        self.app.state.window = SimpleNamespace(create_file_dialog=Mock(return_value=[str(self.folder / "custom")]))
        with patch.dict("sys.modules", {"webview": SimpleNamespace(SAVE_DIALOG=30)}):
            response = self.client.post(extension.PREFIX + "/download", json={"mode": "ask"})
        self.assertEqual(Path(response.json()["path"]).name, "custom.zip")
        self.assertTrue(zipfile.is_zipfile(self.folder / "custom.zip"))

    def test_http_browser_download_is_still_a_zip_attachment(self):
        response = self.client.get(extension.PREFIX + "/download")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertTrue(zipfile.is_zipfile(io.BytesIO(response.content)))

    def test_missing_package_and_write_failure_are_actionable(self):
        with patch.object(extension, "extension_archive", side_effect=FileNotFoundError("Reinstall MemuAI")):
            response = self.client.post(extension.PREFIX + "/download", json={})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Reinstall", response.json()["error"])
        with patch.object(exports, "save_bytes", AsyncMock(side_effect=PermissionError("Write denied"))):
            response = self.client.post(extension.PREFIX + "/download", json={})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Write denied", response.json()["error"])

    def test_nonobject_download_request_is_rejected(self):
        response = self.client.post(extension.PREFIX + "/download", json=["not a settings object"])
        self.assertEqual(response.status_code, 400)
        self.assertFalse(list(self.folder.iterdir()))

    def test_incomplete_archive_is_rejected_before_writing_a_broken_zip(self):
        app_root = self.folder / "app"
        package = app_root / "pgstatic" / "browser-extension"
        package.mkdir(parents=True)
        (package / "manifest.json").write_text(json.dumps({
            "background": {"service_worker": "worker.js"},
            "side_panel": {"default_path": "missing-panel.html"}
        }), encoding="utf-8")
        (package / "worker.js").write_text("// present", encoding="utf-8")
        with patch.object(extension, "__file__", str(app_root / "browser_extension.py")):
            response = self.client.post(extension.PREFIX + "/download", json={})
        self.assertEqual(response.status_code, 400)
        self.assertIn("incomplete", response.json()["error"])
        self.assertFalse(list(self.folder.glob("*.zip")))

class TelegramSetupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="memuai-round44-telegram-")
        self.directory = patch.object(config, "path", return_value=Path(self.tmp.name) / "telegram_use.json")
        self.directory.start()
        self.runtime = telegram.Runtime()

    async def asyncTearDown(self):
        await self.runtime._action("close", {})
        self.directory.stop()
        config._stores.clear()
        self.tmp.cleanup()

    async def test_invalid_api_id_does_not_replace_saved_credentials(self):
        config.save({"api_id": 123})
        config.secrets().set("api_hash", "a" * 32)
        for value in ("abc", -3, "123.0", "2147483648", 12.5, "１２３"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                await self.runtime._action("settings", {"api_id": value, "api_hash": "b" * 32})
            self.assertEqual(config.load()["api_id"], 123)
            self.assertEqual(config.secrets().get("api_hash"), "a" * 32)

    async def test_existing_credentials_can_be_saved_without_retyping_hash(self):
        config.save({"api_id": 123})
        config.secrets().set("api_hash", "a" * 32)
        result = await self.runtime._action("settings", {"api_id": 123, "api_hash": "", "enabled": False})
        self.assertTrue(result["configured"])
        self.assertEqual(config.secrets().get("api_hash"), "a" * 32)
        self.assertNotIn("a" * 32, json.dumps(result))

    async def test_flood_wait_duration_survives_runtime_exception_wrapping(self):
        flood = type("FloodWaitError", (Exception,), {"seconds": 123})("sensitive login payload")
        with patch.object(self.runtime, "_action", AsyncMock(side_effect=flood)):
            with self.assertRaises(telegram.TelegramUseError) as captured:
                await self.runtime.action("qr", {})
        self.assertEqual(captured.exception.retry_after, 123)
        self.assertNotIn("sensitive", str(captured.exception))
        self.assertIn("123", str(captured.exception))

    async def test_switching_api_id_does_not_reuse_another_apps_hash(self):
        config.save({"api_id": 123})
        config.secrets().set("api_hash", "a" * 32)
        config.secrets().set("session", "previous app session")
        result = await self.runtime._action("settings", {"api_id": 456, "enabled": False})
        self.assertFalse(result["configured"])
        self.assertFalse(result["has_api_hash"])
        self.assertFalse(config.secrets().has("session"))

    def test_network_and_login_errors_have_next_steps_without_secret_payloads(self):
        network = telegram.friendly(ConnectionError("proxy password secret"))
        self.assertIn("прокси", network)
        self.assertNotIn("secret", network)
        invalid = telegram.friendly(type("ApiIdInvalidError", (Exception,), {})("hidden"))
        self.assertIn("my.telegram.org/apps", invalid)
        limited = telegram.friendly(type("PhoneNumberFloodError", (Exception,), {})("hidden"))
        self.assertIn("позже", limited)

class TelegramRateLimitRouteTests(unittest.TestCase):
    def test_http_action_returns_machine_readable_retry_after_without_retrying(self):
        runtime = SimpleNamespace(submit=Mock(side_effect=telegram.TelegramUseError("Wait for Telegram", retry_after=120)))
        app = FastAPI()
        telegram_api.register(app)
        with patch.object(telegram, "start", return_value=runtime), TestClient(app, base_url="http://127.0.0.1:8600") as client:
            result = client.post("/api/pg/integrations/telegram-use", json={"action": "qr"})
        self.assertEqual(result.status_code, 429)
        self.assertEqual(result.headers["retry-after"], "120")
        self.assertEqual(result.json()["retry_after"], 120)
        runtime.submit.assert_called_once()

if __name__ == "__main__":
    unittest.main()
