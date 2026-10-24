from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import code_checkpoints, pgdata, playground_api
from app.config import ConfigStore, Settings, _normalized_settings
from app.playground_api import (_read_limited_response, _require_public_response_peer,
                                _safe_media_url, create_app)
from app.routing import resolve
import app.state as state_module
from app.state import ModelEntry, State, state
from app.storage import load_json_with_recovery, save_json
from main import local_server_url, secure_bind_host


class ConfigNormalizationContractTest(unittest.TestCase):
    def test_invalid_persisted_values_fall_back_or_clamp(self) -> None:
        settings = _normalized_settings({
            "port": "not-a-port",
            "request_timeout": -20,
            "stream_idle_timeout": 999999,
            "cookie_check_interval": -4,
            "provider_settings": ["wrong-shape"],
            "api_keys": "not-a-list",
            "cors_enabled": "false",
            "balancing": "random",
        })
        self.assertEqual(settings["port"], 8317)
        self.assertEqual(settings["request_timeout"], 5)
        self.assertEqual(settings["stream_idle_timeout"], 3600)
        self.assertEqual(settings["cookie_check_interval"], 0)
        self.assertEqual(settings["provider_settings"], {})
        self.assertEqual(settings["api_keys"], [])
        self.assertFalse(settings["cors_enabled"])
        self.assertEqual(settings["balancing"], "round-robin")

    def test_keyless_api_is_allowed_only_on_loopback(self) -> None:
        store = ConfigStore.__new__(ConfigStore)
        store.settings = Settings(host="127.0.0.1", api_keys=[])
        self.assertTrue(store.check_api_key(None))
        store.settings.host = "0.0.0.0"
        self.assertFalse(store.check_api_key(None))
        store.settings.api_keys = ["secret"]
        self.assertTrue(store.check_api_key("secret"))
        self.assertFalse(store.check_api_key(None))

    def test_keyless_loopback_rejects_foreign_browser_origins(self) -> None:
        store = ConfigStore.__new__(ConfigStore)
        store.settings = Settings(host="127.0.0.1", api_keys=[])
        self.assertTrue(store.authorize_api_request(None, host="127.0.0.1:8317"))
        self.assertTrue(store.authorize_api_request(
            None, host="127.0.0.1:8317", origin="http://127.0.0.1:8317"))
        self.assertFalse(store.authorize_api_request(
            None, host="127.0.0.1:8317", origin="https://evil.example"))
        self.assertFalse(store.authorize_api_request(
            None, host="127.0.0.1:8317", fetch_site="cross-site"))

    def test_wildcard_bind_advertises_a_reachable_loopback_url(self) -> None:
        self.assertEqual(local_server_url("0.0.0.0", 8317), "http://127.0.0.1:8317")
        self.assertEqual(local_server_url("::", 8317), "http://[::1]:8317")
        self.assertEqual(local_server_url("::1", 8317), "http://[::1]:8317")

    def test_default_admin_password_cannot_bind_to_lan(self) -> None:
        self.assertEqual(secure_bind_host("0.0.0.0", True), "127.0.0.1")
        self.assertEqual(secure_bind_host("192.168.1.10", True), "127.0.0.1")
        self.assertEqual(secure_bind_host("0.0.0.0", False), "0.0.0.0")
        self.assertEqual(secure_bind_host("::1", True), "::1")


class AtomicStorageContractTest(unittest.TestCase):
    def test_parallel_writes_remain_valid_and_leave_no_shared_temp(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            threads = [
                threading.Thread(target=save_json, args=(path, {"writer": index, "text": "я" * 1000}))
                for index in range(16)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            result = load_json_with_recovery(path, None)
            self.assertIsInstance(result, dict)
            self.assertIn(result["writer"], range(16))
            json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(list(Path(temp).glob("state.json.tmp-*")), [])
            self.assertGreater(len(list((Path(temp) / "backups").glob("state-*.json"))), 1)

    def test_unknown_state_rows_survive_load_and_later_save(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            accounts_path = Path(temp) / "accounts.json"
            models_path = Path(temp) / "models.json"
            unknown_account = {"future_schema": 9, "opaque": {"keep": True}}
            unknown_model = {"future_model": True, "payload": [1, 2, 3]}
            save_json(accounts_path, [
                {"id": "known", "provider": "custom", "fields": {"name": "Local"}},
                unknown_account,
            ], backup=False)
            save_json(models_path, [
                {"prefix": "custom", "provider": "custom", "model": "known"},
                unknown_model,
            ], backup=False)

            subject = State.__new__(State)
            subject._lock = threading.RLock()
            subject.accounts = {}
            subject.models = {}
            subject._unparsed_accounts = []
            subject._unparsed_models = []
            with patch.object(state_module, "ACCOUNTS_PATH", str(accounts_path)), \
                    patch.object(state_module, "MODELS_PATH", str(models_path)):
                subject._load()
                self.assertIn("known", subject.accounts)
                self.assertIn("custom-known", subject.models)
                subject.save_accounts()
                subject.save_models()

            self.assertIn(unknown_account, json.loads(accounts_path.read_text(encoding="utf-8")))
            self.assertIn(unknown_model, json.loads(models_path.read_text(encoding="utf-8")))


class WorkBoundaryContractTest(unittest.TestCase):
    def test_session_write_requires_restorable_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = base / "project"
            project.mkdir()
            large = project / "large.txt"
            large.write_bytes(b"x" * 10)
            folder = project / "folder"
            folder.mkdir()
            (folder / "keep.txt").write_text("keep", encoding="utf-8")
            with patch.object(pgdata, "MEMU_DIR", base / "data"), \
                    patch.object(code_checkpoints, "CHECKPOINT_ROOT", base / "checkpoints"), \
                    patch.object(code_checkpoints, "MAX_FILE_BYTES", 4):
                pgdata.save_workspaces({"active_id": "p", "items": [
                    {"id": "p", "name": "P", "path": str(project), "added": 1}]})
                with TestClient(create_app(), base_url="http://127.0.0.1") as client:
                    write = client.post("/api/work/write", json={
                        "project_id": "p", "session": "s", "path": "large.txt", "text": "changed"})
                    self.assertEqual(write.status_code, 400, write.text)
                    move = client.post("/api/pg/tools", json={
                        "tool": "work_move", "project_id": "p", "session": "s", "access": "full",
                        "args": {"path": "large.txt", "to": "moved.txt"}})
                    self.assertIn("отката", move.json().get("error", ""))
                    delete = client.post("/api/pg/tools", json={
                        "tool": "work_delete", "project_id": "p", "session": "s", "access": "full",
                        "args": {"path": "folder"}})
                    # A folder is snapshotted file by file, so deleting it is undoable.
                    self.assertNotIn("error", delete.json())
                    self.assertFalse(folder.exists())
                    code_checkpoints.restore("s", project)
            self.assertEqual(large.read_bytes(), b"x" * 10)
            self.assertFalse((project / "moved.txt").exists())
            self.assertTrue((folder / "keep.txt").is_file())

    def test_mutations_are_project_bound_and_root_cannot_be_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            data_root = Path(temp) / "data"
            project_root = Path(temp) / "project"
            project_root.mkdir(parents=True)
            data_root.mkdir(parents=True)
            with patch.object(pgdata, "MEMU_DIR", data_root):
                pgdata.save_workspaces({
                    "active_id": "project-a",
                    "items": [{"id": "project-a", "name": "A", "path": str(project_root), "added": 1}],
                })
                app = create_app()
                with TestClient(app, base_url="http://127.0.0.1") as client:
                    mismatch = client.post("/api/work/write", json={
                        "project_id": "project-b", "path": "bad.txt", "text": "bad",
                    })
                    self.assertEqual(mismatch.status_code, 400)
                    self.assertFalse((project_root / "bad.txt").exists())

                    written = client.post("/api/work/write", json={
                        "project_id": "project-a", "path": "ok.txt", "text": "safe",
                    })
                    self.assertEqual(written.status_code, 200)
                    self.assertEqual((project_root / "ok.txt").read_text(encoding="utf-8"), "safe")

                    for endpoint in ("/api/work/list", "/api/work/read"):
                        missing_project = client.post(endpoint, json={"path": "ok.txt"})
                        self.assertEqual(missing_project.status_code, 400)
                        malformed = client.post(endpoint, content=b"[]", headers={
                            "Content-Type": "application/json",
                        })
                        self.assertEqual(malformed.status_code, 400)

                    listed = client.post("/api/work/list", json={"project_id": "project-a"})
                    self.assertEqual(listed.status_code, 200)
                    self.assertIn("ok.txt", [item["name"] for item in listed.json()["items"]])
                    read = client.post("/api/work/read", json={
                        "project_id": "project-a", "path": "ok.txt",
                    })
                    self.assertEqual(read.status_code, 200)
                    self.assertEqual(read.json()["text"], "safe")
                    self.assertFalse(read.json()["truncated"])

                    root_delete = client.post("/api/work/delete", json={
                        "project_id": "project-a", "path": "",
                    })
                    self.assertEqual(root_delete.status_code, 400)
                    self.assertTrue(project_root.is_dir())

                    root_write = client.post("/api/work/write", json={
                        "project_id": "project-a", "path": "", "text": "outside-temp",
                    })
                    self.assertEqual(root_write.status_code, 400)
                    self.assertEqual(list(Path(temp).glob(".project.*.tmp")), [])

                    non_text_write = client.post("/api/work/write", json={
                        "project_id": "project-a", "path": "bad-type.txt", "text": ["not", "text"],
                    })
                    self.assertEqual(non_text_write.status_code, 400)
                    self.assertFalse((project_root / "bad-type.txt").exists())

                    api_pick = client.post("/api/work/pick", json={"path": str(Path(temp))})
                    self.assertEqual(api_pick.status_code, 403)

    def test_cross_origin_and_rebinding_hosts_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(pgdata, "MEMU_DIR", Path(temp)):
                app = create_app()
                with TestClient(app, base_url="http://127.0.0.1") as client:
                    self.assertEqual(client.get("/api/work/projects", headers={
                        "Origin": "https://evil.example",
                    }).status_code, 403)
                    self.assertEqual(client.get("/api/work/projects", headers={
                        "Host": "evil.example",
                    }).status_code, 403)


class MediaBoundaryContractTest(unittest.TestCase):
    def test_proxy_runtime_discovery_uses_isolated_data_directory(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(playground_api, "DATA_DIR", tmp):
            runtime = Path(tmp) / "memuai" / "proxy.json"
            runtime.parent.mkdir(parents=True)
            runtime.write_text(json.dumps({"url": "http://127.0.0.1:9123"}), encoding="utf-8")
            self.assertEqual(playground_api._runtime_proxy_url(), "http://127.0.0.1:9123")
            runtime.write_text(json.dumps({
                "url": "http://attacker@127.0.0.1:9123/redirect",
            }), encoding="utf-8")
            self.assertEqual(playground_api._runtime_proxy_url(), "")

    def test_media_urls_reject_credentials_and_non_public_addresses(self) -> None:
        with self.assertRaises(ValueError):
            _safe_media_url("https://user:secret@example.com/image.png")
        with patch("app.playground_api.socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("127.0.0.1", 443)),
        ]):
            with self.assertRaises(ValueError):
                _safe_media_url("https://example.com/image.png")
        with patch("app.playground_api.socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("8.8.8.8", 443)),
        ]):
            self.assertEqual(_safe_media_url("https://example.com/image.png"),
                             "https://example.com/image.png")

    def test_stream_limit_applies_when_content_length_is_missing(self) -> None:
        class Response:
            headers = {}

            async def aiter_bytes(self):
                yield b"1234"
                yield b"5678"

        self.assertEqual(asyncio.run(_read_limited_response(Response(), 8)), b"12345678")
        with self.assertRaises(OverflowError):
            asyncio.run(_read_limited_response(Response(), 7))

    def test_connected_media_peer_is_checked_after_dns_resolution(self) -> None:
        class Stream:
            def __init__(self, address: str):
                self.address = address

            def get_extra_info(self, name: str):
                return (self.address, 443) if name == "server_addr" else None

        with self.assertRaises(ValueError):
            _require_public_response_peer(type("Response", (), {
                "extensions": {"network_stream": Stream("127.0.0.1")},
            })())
        _require_public_response_peer(type("Response", (), {
            "extensions": {"network_stream": Stream("8.8.8.8")},
        })())


class RoutingContractTest(unittest.TestCase):
    def test_exact_disabled_model_does_not_fall_through_to_permissive_prefix(self) -> None:
        model = ModelEntry("kiro", "kiro", "disabled-model", enabled=False)
        with patch.object(state, "find_model", return_value=model):
            routed = resolve(model.id)
        self.assertIs(routed.entry, model)
        self.assertFalse(routed.entry.enabled)


if __name__ == "__main__":
    unittest.main()
