"""Server mode tests: auth, storage, router, sync planning, mock client.

Run from the repo root with the project venv (or /tmp/verify-B)::

    python -m unittest tests.server_mode_test -v

No live network, no real secrets: the token is a fixture value written to
a temporary directory.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.server_mode.auth import TokenStore, bearer_token_from_header, mask_token
from app.server_mode.client import (HttpxTransport, QueuedOp, ServerAuthError,
                                    ServerClient, ServerConnectionError,
                                    TransportResponse)
from app.server_mode.router import create_server_router, register_server_mode
from app.server_mode.secure_store import ClientSecretStore
from app.server_mode.settings_api import (load_settings, save_settings,
                                          validate_settings)
from app.server_mode.store import ServerStore, resolve_under, safe_rel
from app.server_mode.sync import apply_resolutions, plan_sync, resolve_conflict

FIXTURE_TOKEN = "fixture-token-for-tests-only-0123456789abcdef"


def make_app(tmp: Path, token: str = FIXTURE_TOKEN):
    tokens = TokenStore(tmp)
    tokens.path.write_text(token + "\n")
    app = FastAPI()
    app.include_router(create_server_router(data_dir=tmp, token_store=tokens,
                                            store=ServerStore(tmp / "data")))
    return app, tokens


def authed(client: TestClient, method: str, path: str, **kwargs):
    headers = dict(kwargs.pop("headers", {}) or {})
    headers["Authorization"] = "Bearer " + FIXTURE_TOKEN
    return client.request(method, path, headers=headers, **kwargs)


class AuthTest(unittest.TestCase):
    def test_generate_and_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TokenStore(tmp)
            self.assertIsNone(store.load_existing())
            token = store.get_or_create()
            self.assertTrue(token)
            self.assertEqual(store.get_or_create(), token)
            self.assertTrue(store.verify(token))
            self.assertFalse(store.verify("wrong"))
            self.assertFalse(store.verify(""))
            self.assertFalse(store.verify(None))
            mode = stat.S_IMODE(os.stat(store.path).st_mode)
            self.assertEqual(mode, 0o600)

    def test_mask_and_bearer_parsing(self):
        self.assertEqual(mask_token("abcdefghij"), "••••ghij")
        self.assertEqual(mask_token(""), "")
        self.assertEqual(bearer_token_from_header("Bearer abc"), "abc")
        self.assertIsNone(bearer_token_from_header(None))
        self.assertIsNone(bearer_token_from_header("Bearer"))
        self.assertIsNone(bearer_token_from_header("Basic abc"))
        self.assertIsNone(bearer_token_from_header("Bearer a b"))


class StoreTest(unittest.TestCase):
    def test_safe_paths(self):
        self.assertEqual(safe_rel("a/b.txt"), "a/b.txt")
        for bad in ("", "../evil", "/abs", "C:\\evil", "a/../../b", "a\x00b"):
            with self.assertRaises(ValueError, msg=bad):
                safe_rel(bad)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "files"
            root.mkdir()
            with self.assertRaises(ValueError):
                resolve_under(root, "../evil.txt")
            self.assertTrue(str(resolve_under(root, "ok.txt")).startswith(str(root.resolve())))

    def test_files_and_docs(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ServerStore(tmp)
            meta = store.put_file("notes/a.txt", b"hello")
            self.assertEqual(meta["size"], 5)
            self.assertEqual(meta["sha256"], hashlib.sha256(b"hello").hexdigest())
            self.assertEqual(store.get_file("notes/a.txt"), b"hello")
            self.assertIsNone(store.get_file("missing.txt"))
            self.assertEqual([row["rel"] for row in store.list_files()], ["notes/a.txt"])
            self.assertTrue(store.delete_file("notes/a.txt"))
            self.assertFalse(store.delete_file("notes/a.txt"))

            store.put_doc("chats", "c1", {"title": "Hi", "messages": []})
            doc = store.get_doc("chats", "c1")
            self.assertEqual(doc["title"], "Hi")
            self.assertEqual(doc["id"], "c1")
            self.assertIn("updated_ts", doc)
            self.assertEqual([row["id"] for row in store.list_docs("chats")], ["c1"])
            with self.assertRaises(ValueError):
                store.put_doc("chats", "../evil", {})
            manifest = store.manifest()
            self.assertIn("c1", manifest["chats"])

    def test_bridge_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ServerStore(tmp)
            record = store.enqueue_bridge_request({"action_class": "read", "kind": "read",
                                                   "target": "/x"})
            self.assertEqual(record["status"], "pending")
            self.assertEqual(len(store.list_bridge_requests("pending")), 1)
            done = store.set_bridge_result(record["id"], "done", {"status": "ok"})
            self.assertEqual(done["status"], "done")
            self.assertEqual(store.list_bridge_requests("pending"), [])
            self.assertIsNone(store.set_bridge_result("br_deadbeef", "done", {}))
            with self.assertRaises(ValueError):
                store.set_bridge_result(record["id"], "weird", {})


class RouterAuthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app, _ = make_app(Path(self.tmp.name))
        self.client = TestClient(self.app)

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_token_is_401_everywhere(self):
        for method, path in (("GET", "/api/server/health"),
                             ("GET", "/api/server/manifest"),
                             ("GET", "/api/server/files"),
                             ("PUT", "/api/server/files/a.txt"),
                             ("GET", "/api/server/chats"),
                             ("POST", "/api/server/bridge/requests")):
            resp = self.client.request(method, path)
            self.assertEqual(resp.status_code, 401, (method, path))
            self.assertNotIn(FIXTURE_TOKEN, resp.text)

    def test_wrong_token_is_401(self):
        resp = self.client.get("/api/server/health",
                               headers={"Authorization": "Bearer nope"})
        self.assertEqual(resp.status_code, 401)

    def test_correct_token_health_and_files(self):
        resp = authed(self.client, "GET", "/api/server/health")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])
        resp = authed(self.client, "PUT", "/api/server/files/notes/a.txt", content=b"data")
        self.assertEqual(resp.status_code, 200)
        resp = authed(self.client, "GET", "/api/server/files/notes/a.txt")
        self.assertEqual(resp.content, b"data")
        resp = authed(self.client, "GET", "/api/server/files")
        self.assertEqual([f["rel"] for f in resp.json()["files"]], ["notes/a.txt"])
        resp = authed(self.client, "GET", "/api/server/manifest")
        self.assertIn("notes/a.txt", resp.json()["files"])

    def test_docs_and_bridge_queue_over_http(self):
        resp = authed(self.client, "PUT", "/api/server/chats/c9",
                      json={"title": "T", "messages": [1]})
        self.assertEqual(resp.status_code, 200)
        resp = authed(self.client, "GET", "/api/server/chats/c9")
        self.assertEqual(resp.json()["doc"]["title"], "T")
        resp = authed(self.client, "POST", "/api/server/bridge/requests",
                      json={"action_class": "read", "kind": "read", "target": "/tmp/x"})
        self.assertEqual(resp.status_code, 201)
        req_id = resp.json()["request"]["id"]
        resp = authed(self.client, "GET", "/api/server/bridge/requests?status=pending")
        self.assertEqual(len(resp.json()["requests"]), 1)
        resp = authed(self.client, "POST",
                      f"/api/server/bridge/requests/{req_id}/result",
                      json={"status": "done", "result": {"status": "ok"}})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["request"]["status"], "done")

    def test_register_generates_token_and_mounts(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = FastAPI()
            tokens = register_server_mode(app, tmp)
            self.assertTrue(tokens.load_existing())
            self.assertEqual(stat.S_IMODE(os.stat(tokens.path).st_mode), 0o600)


class SyncPlanTest(unittest.TestCase):
    def _entry(self, sha, mtime):
        return {"sha256": sha, "size": 1, "mtime": mtime}

    def test_one_side_changes(self):
        base = {"a.txt": self._entry("1", 10)}
        plan = plan_sync({"a.txt": self._entry("2", 20)}, {"a.txt": self._entry("1", 10)}, base)
        self.assertEqual([(a.kind, a.name) for a in plan], [("push", "a.txt")])
        plan = plan_sync({"a.txt": self._entry("1", 10)}, {"a.txt": self._entry("2", 20)}, base)
        self.assertEqual([(a.kind, a.name) for a in plan], [("pull", "a.txt")])

    def test_both_changed_is_conflict(self):
        base = {"a.txt": self._entry("1", 10)}
        plan = plan_sync({"a.txt": self._entry("2", 20)}, {"a.txt": self._entry("3", 30)}, base)
        self.assertEqual([(a.kind, a.name) for a in plan], [("conflict", "a.txt")])

    def test_newer_wins_and_equal_mtime_stays(self):
        local = self._entry("2", 30)
        remote = self._entry("3", 20)
        self.assertEqual(resolve_conflict(local, remote, "newer"), "push")
        self.assertEqual(resolve_conflict(local, remote, "local"), "push")
        self.assertEqual(resolve_conflict(local, remote, "remote"), "pull")
        self.assertEqual(resolve_conflict(self._entry("2", 20), self._entry("3", 20), "newer"),
                         "unresolved")

    def test_apply_resolutions(self):
        actions = [plan_sync({"a.txt": self._entry("2", 30)},
                             {"a.txt": self._entry("3", 20)},
                             {"a.txt": self._entry("1", 10)})[0]]
        resolved = apply_resolutions(actions, {"a.txt": self._entry("2", 30)},
                                     {"a.txt": self._entry("3", 20)}, {"a.txt": "newer"})
        self.assertEqual(resolved[0].kind, "push")
        unresolved = apply_resolutions(actions, {"a.txt": self._entry("2", 30)},
                                       {"a.txt": self._entry("3", 20)}, {})
        self.assertEqual(unresolved[0].kind, "conflict")


class FakeTransport:
    """In-memory stand-in for the server, with optional failure injection."""

    def __init__(self, token: str = FIXTURE_TOKEN):
        self.token = token
        self.files: dict = {}
        self.fail = False

    def request(self, method, path, *, json=None, data=None, headers=None):
        if self.fail:
            raise ServerConnectionError("down")
        # Like HttpxTransport, this transport carries the credential itself:
        # a transport built with the wrong token is rejected by the server.
        if self.token != FIXTURE_TOKEN:
            return TransportResponse(401, {"error": "unauthorized"})
        if path == "/api/server/health":
            return TransportResponse(200, {"ok": True, "server": "memuai-server"})
        if path == "/api/server/manifest":
            files = {name: {"rel": name, "size": len(b), "mtime": 1,
                            "sha256": hashlib.sha256(b).hexdigest()}
                     for name, b in self.files.items()}
            return TransportResponse(200, {"ok": True, "files": files,
                                           "chats": {}, "projects": {}})
        if path.startswith("/api/server/files/"):
            name = path[len("/api/server/files/"):]
            if method == "PUT":
                self.files[name] = bytes(data or b"")
                return TransportResponse(200, {"ok": True, "rel": name})
            if method == "GET":
                if name not in self.files:
                    return TransportResponse(404, {"error": "not_found"})
                return TransportResponse(200, None, self.files[name])
        return TransportResponse(404, {"error": "unknown"})


class ClientSyncTest(unittest.TestCase):
    def _client(self, tmp: Path, transport=None):
        transport = transport or FakeTransport()
        local = {"a.txt": b"local-a", "b.txt": b"local-b"}
        client = ServerClient(
            transport,
            queue_path=tmp / "queue.json",
            local_files=lambda: {name: {"rel": name, "size": len(data), "mtime": 5,
                                        "sha256": hashlib.sha256(data).hexdigest()}
                                 for name, data in local.items()},
            local_file_reader=lambda name: local.get(name),
            local_file_writer=lambda name, data: local.__setitem__(name, data),
        )
        return client, transport, local

    def test_check_connection_and_auth(self):
        with tempfile.TemporaryDirectory() as tmp:
            client, _, _ = self._client(Path(tmp))
            self.assertTrue(client.check_connection()["ok"])
            bad = ServerClient(FakeTransport(token="other"))
            with self.assertRaises(ServerAuthError):
                bad.check_connection()

    def test_push_and_pull(self):
        with tempfile.TemporaryDirectory() as tmp:
            client, transport, local = self._client(Path(tmp))
            transport.files["c.txt"] = b"server-c"
            result = client.sync_files()
            self.assertEqual(result["pushed"], 2)
            self.assertEqual(result["pulled"], 1)
            self.assertEqual(local["c.txt"], b"server-c")
            self.assertEqual(transport.files["a.txt"], b"local-a")

    def test_conflict_is_reported_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            client, transport, _ = self._client(Path(tmp))
            transport.files["a.txt"] = b"server-a-changed"
            base = {"files": {"a.txt": {"rel": "a.txt", "size": 1, "mtime": 1,
                                        "sha256": hashlib.sha256(b"old").hexdigest()}}}
            result = client.sync_files(base_manifest=base)
            self.assertIn("a.txt", result["conflicts"])
            self.assertEqual(transport.files["a.txt"], b"server-a-changed")
            resolved = client.sync_files(base_manifest=base, resolutions={"a.txt": "local"})
            self.assertEqual(resolved["pushed"], 1)
            self.assertEqual(transport.files["a.txt"], b"local-a")

    def test_offline_queue_and_flush(self):
        with tempfile.TemporaryDirectory() as tmp:
            client, transport, _ = self._client(Path(tmp))
            transport.fail = True
            self.assertEqual(client.enqueue(QueuedOp("push", "a.txt")), 1)
            self.assertEqual(client.queued_count(), 1)
            transport.fail = False
            flushed = client.flush_queue()
            self.assertEqual(flushed, {"applied": 1, "remaining": 0})
            self.assertEqual(transport.files["a.txt"], b"local-a")


class SecureStoreTest(unittest.TestCase):
    def test_round_trip_mask_clear(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ClientSecretStore(Path(tmp) / "token.enc")
            self.assertFalse(store.has_token())
            store.save_token(FIXTURE_TOKEN)
            self.assertTrue(store.has_token())
            self.assertEqual(store.load_token(), FIXTURE_TOKEN)
            self.assertEqual(store.mask(), mask_token(FIXTURE_TOKEN))
            self.assertNotIn(FIXTURE_TOKEN, (Path(tmp) / "token.enc").read_bytes().decode("utf-8", "replace"))
            store.clear()
            self.assertFalse(store.has_token())


class SettingsValidationTest(unittest.TestCase):
    def test_url_and_mode_validation(self):
        clean, errors = validate_settings({"server_url": "https://user:pass@example.com"})
        self.assertIn("server_url", errors)
        clean, errors = validate_settings({"server_url": "https://memu.example.com/"})
        self.assertEqual(clean["server_url"], "https://memu.example.com")
        self.assertEqual(errors, {})
        _, errors = validate_settings({"mode": "everywhere"})
        self.assertIn("mode", errors)
        clean, _ = validate_settings({"bridge_roots": ["/tmp", "relative", ""]})
        self.assertEqual(clean["bridge_roots"], ["/tmp"])

    def test_defaults_are_local_and_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = load_settings(tmp)
            self.assertEqual(settings["mode"], "local")
            self.assertFalse(settings["sync_files"])
            self.assertFalse(settings["bridge_enabled"])
            updated = save_settings({"mode": "hybrid", "sync_files": True}, tmp)
            self.assertEqual(updated["mode"], "hybrid")
            self.assertTrue(load_settings(tmp)["sync_files"])
            with self.assertRaises(ValueError):
                save_settings({"mode": "nope"}, tmp)

    def test_httpx_transport_rejects_bad_url(self):
        with self.assertRaises(ValueError):
            HttpxTransport("example.com", "x")


if __name__ == "__main__":
    unittest.main()
