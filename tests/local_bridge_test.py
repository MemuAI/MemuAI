"""Local bridge tests: policy, execution gate, confirmation, revoke, poller.

Run from the repo root with the project venv (or /tmp/verify-B)::

    python -m unittest tests.local_bridge_test -v

No real shell commands run against the user's files: execution uses a
fake runner except for plain file read/write inside a temporary root.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.local_bridge.agent import (BridgeRequest, BridgeResult, BridgeService,
                                    redact, request_from_dict)
from app.local_bridge.policy import BridgePolicy
from app.local_bridge.poller import poll_once
from app.local_bridge.router import register as register_bridge


def policy_for(root: Path, **overrides) -> BridgePolicy:
    kwargs = dict(roots=[str(root)], enabled_classes=frozenset({"read", "write", "execute", "browser"}),
                  confirm_mode="always", enabled=True)
    kwargs.update(overrides)
    return BridgePolicy(**kwargs)


class PolicyTest(unittest.TestCase):
    def test_contains_and_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "work"
            root.mkdir()
            policy = policy_for(root)
            self.assertTrue(policy.contains(str(root / "a.txt")))
            self.assertTrue(policy.contains(str(root / "sub" / ".." / "a.txt")))
            self.assertFalse(policy.contains(str(Path(tmp) / "other.txt")))
            self.assertFalse(policy.contains("/etc/passwd"))

    def test_argv_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = policy_for(root)
            self.assertIsNone(policy.check_argv(["git", "status"]))
            self.assertIsNotNone(policy.check_argv(["git", "push"]))
            self.assertIsNotNone(policy.check_argv(["git", "commit", "-m", "x"]))
            self.assertIsNotNone(policy.check_argv(["rm", "-rf", "/"]))
            self.assertIsNone(policy.check_argv(["python", "-m", "pytest", "-q"]))
            self.assertIsNotNone(policy.check_argv(["python", "-c", "import os"]))
            self.assertIsNotNone(policy.check_argv(["python", "x.py; rm -rf /"]))
            self.assertIsNotNone(policy.check_argv(["python", "-m", "os"]))
            self.assertIsNotNone(policy.check_argv(["node", "-e", "1"]))
            self.assertIsNotNone(policy.check_argv(["curl", "http://example.com"]))
            script = root / "run.py"
            self.assertIsNone(policy.check_argv(["python", str(script)]))
            self.assertIsNotNone(policy.check_argv(["python", "/etc/evil.py"]))

    def test_url_policy(self):
        policy = policy_for(Path("/tmp"))
        self.assertIsNone(policy.check_url("https://example.com/page"))
        self.assertIsNotNone(policy.check_url("http://127.0.0.1/admin"))
        self.assertIsNotNone(policy.check_url("http://192.168.1.5/"))
        self.assertIsNotNone(policy.check_url("http://10.0.0.2/"))
        self.assertIsNotNone(policy.check_url("ftp://example.com/x"))
        self.assertIsNotNone(policy.check_url("http://localhost:8327/"))


class AgentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "work"
        self.root.mkdir()
        (self.root / "hello.txt").write_text("hi", encoding="utf-8")
        self.calls = []
        self.service = BridgeService(policy_for(self.root), runner=self._fake_runner)

    def tearDown(self):
        self.tmp.cleanup()

    def _fake_runner(self, argv, cwd=None):
        self.calls.append((argv, cwd))
        return BridgeResult("ok", output="fake: " + " ".join(argv))

    def _exec(self, request, confirm=True):
        return self.service.execute(request, confirm_callback=lambda r: confirm)

    def test_read_and_write_inside_root(self):
        result = self._exec(BridgeRequest("read", "read", str(self.root / "hello.txt")))
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.output, "hi")
        result = self._exec(BridgeRequest("write", "write", str(self.root / "new.txt"),
                                          data=b"abc"))
        self.assertEqual(result.status, "ok")
        self.assertEqual((self.root / "new.txt").read_bytes(), b"abc")

    def test_outside_root_denied(self):
        outside = Path(self.tmp.name) / "outside.txt"
        result = self._exec(BridgeRequest("read", "read", str(outside)))
        self.assertEqual(result.status, "denied")
        result = self._exec(BridgeRequest("write", "write", str(outside), data=b"x"))
        self.assertEqual(result.status, "denied")

    def test_class_switched_off_and_bridge_disabled(self):
        service = BridgeService(policy_for(self.root, enabled_classes=frozenset({"read"})))
        result = service.execute(BridgeRequest("write", "write", str(self.root / "x")),
                                 confirm_callback=lambda r: True)
        self.assertEqual(result.status, "denied")
        service = BridgeService(policy_for(self.root, enabled=False))
        result = service.execute(BridgeRequest("read", "read", str(self.root / "hello.txt")),
                                 confirm_callback=lambda r: True)
        self.assertEqual(result.status, "denied")

    def test_allowlisted_execute_via_runner_no_shell(self):
        result = self._exec(BridgeRequest("execute", "execute", argv=["git", "status"]))
        self.assertEqual(result.status, "ok")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][0], ["git", "status"])
        self.assertTrue(str(self.calls[0][1]).startswith(str(self.root)))

    def test_denied_commands_never_reach_runner(self):
        for argv in (["git", "push"], ["rm", "-rf", "/"], ["python", "-c", "x"]):
            result = self._exec(BridgeRequest("execute", "execute", argv=argv))
            self.assertEqual(result.status, "denied", argv)
        self.assertEqual(self.calls, [])

    def test_user_denial(self):
        result = self._exec(BridgeRequest("read", "read", str(self.root / "hello.txt")),
                            confirm=False)
        self.assertEqual(result.status, "denied")
        self.assertEqual(result.error, "denied by user")

    def test_pending_then_confirm(self):
        request = BridgeRequest("read", "read", str(self.root / "hello.txt"))
        result = self.service.execute(request)
        self.assertEqual(result.status, "pending")
        self.assertEqual(len(self.service.pending()), 1)
        confirmed = self.service.confirm_pending(request.id, True)
        self.assertEqual(confirmed.status, "ok")
        self.assertEqual(confirmed.output, "hi")
        self.assertIsNone(self.service.confirm_pending(request.id, True))

    def test_pending_deny(self):
        request = BridgeRequest("read", "read", str(self.root / "hello.txt"))
        self.service.execute(request)
        denied = self.service.confirm_pending(request.id, False)
        self.assertEqual(denied.status, "denied")

    def test_once_mode_remembers_approval(self):
        service = BridgeService(policy_for(self.root, confirm_mode="once"),
                                runner=self._fake_runner)
        req1 = BridgeRequest("execute", "execute", argv=["git", "status"])
        self.assertEqual(service.execute(req1).status, "pending")
        self.assertEqual(service.confirm_pending(req1.id, True).status, "ok")
        req2 = BridgeRequest("execute", "execute", argv=["git", "status"])
        self.assertEqual(service.execute(req2).status, "ok")

    def test_revoke_is_instant_and_sticky(self):
        request = BridgeRequest("read", "read", str(self.root / "hello.txt"))
        self.service.execute(request)
        self.assertEqual(len(self.service.pending()), 1)
        self.service.revoke()
        self.assertTrue(self.service.revoked)
        self.assertEqual(self.service.pending(), [])
        result = self.service.execute(BridgeRequest("read", "read", str(self.root / "hello.txt")),
                                      confirm_callback=lambda r: True)
        self.assertEqual(result.status, "denied")
        self.assertEqual(result.error, "bridge revoked")
        events = [row["event"] for row in self.service.journal()]
        self.assertIn("revoked", events)

    def test_browser_class(self):
        result = self._exec(BridgeRequest("browser", "browser", "https://example.com/"))
        self.assertEqual(result.status, "needs_host")
        result = self._exec(BridgeRequest("browser", "browser", "http://127.0.0.1/"))
        self.assertEqual(result.status, "denied")

    def test_journal_redacts_secrets(self):
        self.assertIn("••••", redact("api_key=supersecretvalue123"))
        service = BridgeService(policy_for(self.root),
                                runner=lambda argv, cwd=None: BridgeResult(
                                    "ok", output="token=supersecretvalue123"))
        service.execute(BridgeRequest("execute", "execute", argv=["git", "status"]),
                        confirm_callback=lambda r: True)
        journal = service.journal()
        self.assertTrue(journal)
        self.assertNotIn("supersecretvalue123", str(journal))

    def test_request_from_dict(self):
        req = request_from_dict({"action_class": "read", "kind": "read",
                                 "target": "/x", "id": "br_1"})
        self.assertEqual(req.id, "br_1")
        self.assertEqual(req.target, "/x")


class FakeBridgeClient:
    def __init__(self):
        self.requests = [{"id": "br_abc", "action_class": "read", "kind": "read",
                          "target": "/tmp/x"}]
        self.results = {}

    def list_bridge_requests(self, status="pending"):
        return list(self.requests)

    def post_bridge_result(self, req_id, status, result):
        self.results[req_id] = (status, result)
        return {"ok": True}


class PollerTest(unittest.TestCase):
    def test_poll_executes_and_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "x").write_text("data", encoding="utf-8")
            service = BridgeService(policy_for(root))
            client = FakeBridgeClient()
            client.requests[0]["target"] = str(root / "x")
            outcomes = poll_once(client, service, confirm_callback=lambda r: True)
            self.assertEqual(outcomes, [{"ok": True, "id": "br_abc", "status": "done"}])
            self.assertEqual(client.results["br_abc"][0], "done")

    def test_poll_without_approver_leaves_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "x").write_text("data", encoding="utf-8")
            service = BridgeService(policy_for(root))
            client = FakeBridgeClient()
            client.requests[0]["target"] = str(root / "x")
            outcomes = poll_once(client, service)
            self.assertEqual(outcomes[0]["status"], "pending")
            self.assertEqual(client.results, {})


class BridgeRouterTest(unittest.TestCase):
    def test_status_journal_revoke_confirm(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = BridgeService(policy_for(root))
            app = FastAPI()
            register_bridge(app, service_factory=lambda: service)
            client = TestClient(app)
            resp = client.get("/api/pg/bridge/status")
            self.assertEqual(resp.status_code, 200)
            self.assertFalse(resp.json()["revoked"])
            request = BridgeRequest("read", "read", str(root / "a.txt"))
            service.execute(request)
            resp = client.get("/api/pg/bridge/journal")
            self.assertEqual(len(resp.json()["pending"]), 1)
            resp = client.post("/api/pg/bridge/confirm",
                               json={"request_id": request.id, "approve": False})
            self.assertEqual(resp.json()["status"], "denied")
            resp = client.post("/api/pg/bridge/revoke", json={})
            self.assertTrue(resp.json()["revoked"])
            resp = client.get("/api/pg/bridge/status")
            self.assertTrue(resp.json()["revoked"])


if __name__ == "__main__":
    unittest.main()
