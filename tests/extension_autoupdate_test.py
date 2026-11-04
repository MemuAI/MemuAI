"""Chrome extension auto-update: bundle hash, managed folder, adoption by
Chrome's path-derived id, reload protocol and the settings surface."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import browser_extension as ext
from app import browser_extension_update as updater

ROOT = Path(__file__).resolve().parents[1]


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="memu-ext-update-")
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.source = self.dir / "source"
        self.source.mkdir()
        (self.source / "manifest.json").write_text(json.dumps({
            "manifest_version": 3, "name": "MemuAI", "version": "5.0.0",
            "background": {"service_worker": "worker.js", "type": "module"}}), encoding="utf-8")
        (self.source / "worker.js").write_text("export const v = 1;\n", encoding="utf-8")
        (self.source / "icons").mkdir()
        (self.source / "icons" / "16.png").write_bytes(b"png")
        self.config = self.dir / "extension.json"
        for target, value in ((ext, ("_path", self.config)), (updater, ("source_root", self.source)),
                              (updater, ("default_dir", self.dir / "managed"))):
            patcher = patch.object(target, value[0], return_value=value[1])
            patcher.start()
            self.addCleanup(patcher.stop)
        updater._cache.clear()
        self.candidates = patch.object(updater, "candidates", return_value=[])
        self.candidates.start()
        self.addCleanup(self.candidates.stop)


class BundleTests(Base):
    def test_hash_is_stable_and_follows_content(self):
        first = updater.bundle_info()
        self.assertEqual(first["version"], "5.0.0")
        self.assertEqual(first, updater.bundle_info())
        (self.source / "worker.js").write_text("export const v = 2;\n", encoding="utf-8")
        self.assertNotEqual(first["hash"], updater.bundle_info()["hash"])

    def test_unpacked_id_shape_matches_chrome(self):
        value = updater.unpacked_id(self.source)
        self.assertRegex(value, r"^[a-p]{32}$")
        self.assertEqual(value, updater.unpacked_id(str(self.source)))
        self.assertNotEqual(value, updater.unpacked_id(self.dir))

    def test_write_bundle_replaces_only_its_own_files(self):
        target = self.dir / "copy"
        target.mkdir()
        (target / "notes.txt").write_text("mine", encoding="utf-8")
        (target / "old.js").write_text("stale", encoding="utf-8")
        (target / updater.BUNDLE_FILE).write_text(json.dumps({"files": ["old.js", "../outside.txt"]}), encoding="utf-8")
        (self.dir / "outside.txt").write_text("keep", encoding="utf-8")
        result = updater.write_bundle(target, "id-1")
        self.assertEqual(result["files"], 3)
        self.assertFalse((target / "old.js").exists())
        self.assertTrue((target / "notes.txt").exists())
        self.assertTrue((self.dir / "outside.txt").exists())
        meta = json.loads((target / updater.BUNDLE_FILE).read_text(encoding="utf-8"))
        self.assertEqual(meta["install_id"], "id-1")
        self.assertEqual(meta["hash"], updater.bundle_info()["hash"])
        self.assertIn("icons/16.png", meta["files"])
        with self.assertRaises(ValueError):
            updater.write_bundle(self.source, "x")

    def test_zip_carries_bundle_metadata(self):
        real = ROOT / "app" / "pgstatic" / "browser-extension"
        with patch.object(updater, "source_root", return_value=real):
            updater._cache.clear()
            archive = zipfile.ZipFile(io.BytesIO(ext.extension_archive()))
            meta = json.loads(archive.read(updater.BUNDLE_FILE))
            self.assertEqual(meta["hash"], updater.bundle_info()["hash"])
        updater._cache.clear()
        self.assertEqual(meta["install_id"], "")
        self.assertEqual(sorted(meta["files"]), sorted(name for name in archive.namelist() if name != updater.BUNDLE_FILE))


class PlanTests(Base):
    def install(self):
        result = updater.install()
        return result, ext.settings()

    def test_install_keeps_install_id_and_hides_it_from_public(self):
        result, cfg = self.install()
        self.assertTrue((Path(result["path"]) / "worker.js").is_file())
        self.assertEqual(result["extension_id"], updater.unpacked_id(self.dir / "managed"))
        again, cfg2 = self.install()
        self.assertEqual(cfg["managed_install_id"], cfg2["managed_install_id"])
        public = ext.Bridge().public()
        self.assertNotIn("managed_install_id", public)
        self.assertNotIn("last_update", public)
        self.assertEqual(public["update"]["state"], "unknown")
        self.assertTrue(public["update"]["auto"])

    def test_current_worker_needs_nothing(self):
        _, cfg = self.install()
        client = {"version": "5.0.0", "bundle": updater.bundle_info()["hash"], "install_id": cfg["managed_install_id"]}
        self.assertEqual(updater.plan(client, "a" * 32)["action"], "none")
        self.assertEqual(updater.status(client, True)["state"], "current")

    def test_managed_outdated_worker_reloads_then_stops_looping(self):
        _, cfg = self.install()
        client = {"version": "4.9.0", "bundle": "old", "install_id": cfg["managed_install_id"]}
        self.assertEqual(updater.status(client, True)["state"], "managed")
        (self.source / "worker.js").write_text("export const v = 3;\n", encoding="utf-8")
        first = updater.plan(client, "a" * 32)
        self.assertEqual(first["action"], "reload")
        self.assertIn("v = 3", (self.dir / "managed" / "worker.js").read_text(encoding="utf-8"))
        self.assertEqual(updater.plan(client, "a" * 32)["action"], "reload")
        self.assertEqual(updater.plan(client, "a" * 32), {"action": "none", "state": "stuck"})
        self.assertEqual(updater.status(client, True)["state"], "stuck")
        self.assertEqual(updater.plan(client, "a" * 32, force=True)["action"], "reload")

    def test_unmanaged_copy_is_manual_and_respects_auto_update_switch(self):
        client = {"version": "4.9.0", "bundle": "old", "install_id": ""}
        self.assertEqual(updater.plan(client, "a" * 32), {"action": "none", "state": "manual"})
        self.assertEqual(updater.status(client, True)["state"], "manual")
        _, cfg = self.install()
        ext.save_settings_patch({"auto_update": False})
        managed = {**client, "install_id": cfg["managed_install_id"]}
        self.assertEqual(updater.plan(managed, "a" * 32)["state"], "manual")
        with self.assertRaises(ValueError):
            ext.save_settings_patch({"auto_update": "yes"})

    def test_extracted_folder_is_adopted_by_its_chrome_id(self):
        folder = self.dir / "Downloads" / "MemuAI" / "MemuAI-Chrome"
        folder.mkdir(parents=True)
        for item in ("manifest.json", "worker.js"):
            (folder / item).write_bytes((self.source / item).read_bytes())
        self.candidates.stop()
        with patch.object(updater, "candidates", return_value=[folder]):
            legacy = {"version": "", "bundle": "", "install_id": ""}
            self.assertEqual(updater.plan(legacy, "b" * 32)["state"], "manual")
            decision = updater.plan(legacy, updater.unpacked_id(folder))
        self.candidates.start()
        self.assertEqual(decision["action"], "restart")
        self.assertTrue((folder / "icons" / "16.png").is_file())
        cfg = ext.settings()
        self.assertEqual(Path(cfg["managed_path"]), folder)
        meta = json.loads((folder / updater.BUNDLE_FILE).read_text(encoding="utf-8"))
        self.assertEqual(meta["install_id"], cfg["managed_install_id"])
        again = updater.plan(legacy, updater.unpacked_id(folder))
        self.assertEqual(again["state"], "restart")
        self.assertEqual(updater.status(legacy, True)["state"], "restart")

    def test_developer_copy_from_source_counts_as_current(self):
        client = {"version": "5.0.0", "bundle": "", "install_id": ""}
        self.assertEqual(updater.plan(client, updater.unpacked_id(self.source))["state"], "current")


class SocketTests(Base):
    def setUp(self):
        super().setUp()
        self.bridge = ext.Bridge()
        patcher = patch.object(ext, "bridge", self.bridge)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.ext_id = "c" * 32
        code = self.bridge.begin_pair()["code"]
        self.token = self.bridge.pair(code, self.ext_id)["token"]

    def app(self):
        app = FastAPI()
        ext.register(app, completion=AsyncMock(return_value="ok"), proxy_json=AsyncMock(return_value=(200, {})))
        return app

    def test_outdated_managed_worker_gets_reload_after_ready(self):
        updater.install()
        cfg = ext.settings()
        with TestClient(self.app(), base_url="http://127.0.0.1:8600") as client:
            with client.websocket_connect(ext.PREFIX + "/socket", headers={"origin": "chrome-extension://" + self.ext_id, "host": "127.0.0.1:8600"}) as socket:
                socket.send_json({"token": self.token, "version": "4.9.0", "bundle": "old", "install_id": cfg["managed_install_id"]})
                ready = socket.receive_json()
                self.assertEqual(ready["op"], "ready")
                self.assertEqual(ready["bundle"]["hash"], updater.bundle_info()["hash"])
                self.assertEqual(ready["update"]["state"], "reloading")
                self.assertEqual(socket.receive_json(), {"op": "reload", "reason": "update"})
                status = client.get(ext.PREFIX + "/status").json()
                self.assertEqual(status["update"]["installed"], "4.9.0")
                self.assertTrue(status["update"]["managed"])

    def test_current_worker_only_gets_ready_and_endpoints_work(self):
        with TestClient(self.app(), base_url="http://127.0.0.1:8600") as client:
            installed = client.post(ext.PREFIX + "/install", json={}).json()
            self.assertTrue(Path(installed["path"]).is_dir())
            self.assertEqual(installed["status"]["update"]["managed_path"], installed["path"])
            cfg = ext.settings()
            with client.websocket_connect(ext.PREFIX + "/socket", headers={"origin": "chrome-extension://" + self.ext_id, "host": "127.0.0.1:8600"}) as socket:
                socket.send_json({"token": self.token, "version": "5.0.0", "bundle": updater.bundle_info()["hash"], "install_id": cfg["managed_install_id"]})
                ready = socket.receive_json()
                self.assertEqual((ready["op"], ready["update"]["state"]), ("ready", "current"))
                socket.send_json({"op": "ping"})
                self.assertEqual(socket.receive_json()["op"], "pong")
                self.assertEqual(client.get(ext.PREFIX + "/status").json()["update"]["state"], "current")
                self.assertEqual(client.post(ext.PREFIX + "/update", json={}).json()["state"], "current")


class SourceTests(unittest.TestCase):
    def test_worker_reports_build_and_handles_reload(self):
        worker = (ROOT / "app/pgstatic/browser-extension/worker.js").read_text(encoding="utf-8")
        self.assertIn('chrome.runtime.getURL("bundle.json")', worker)
        self.assertIn("install_id: String(info.install_id", worker)
        self.assertIn('command.op === "reload"', worker)
        self.assertIn("chrome.runtime.reload()", worker)
        manifest = json.loads((ROOT / "app/pgstatic/browser-extension/manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "5.0.0")

    def test_side_panel_shows_update_notice(self):
        panel = (ROOT / "app/pgstatic/browser-extension/panel.js").read_text(encoding="utf-8")
        html = (ROOT / "app/pgstatic/browser-extension/panel.html").read_text(encoding="utf-8")
        worker = (ROOT / "app/pgstatic/browser-extension/worker.js").read_text(encoding="utf-8")
        self.assertIn('id="update-hint"', html)
        self.assertIn("memuai://settings/integrations", html)
        self.assertIn("async function paintUpdate", panel)
        self.assertIn("noteUpdate(command.update)", worker)
        self.assertIn('["manual", "stuck", "restart"]', worker)

    def test_settings_page_offers_folder_and_update_controls(self):
        page = (ROOT / "app/pgstatic/browser-extension-settings.js").read_text(encoding="utf-8")
        for needle in ('button("install"', '"/install"', '"/update"', '"/reveal"', 'checkbox("auto_update"', "function updateHtml"):
            self.assertIn(needle, page)


if __name__ == "__main__":
    unittest.main()
