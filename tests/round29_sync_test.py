"""Round 29 «Синхронизация»: backups to Google Drive (mocked Drive API v3 + OAuth) and to a folder.

Everything runs offline: Google is an in-memory fake behind ``httpx.MockTransport`` (token endpoint
with PKCE verification, ``about``, ``files`` list/create/media/delete, multipart and resumable uploads,
injected 503/401 failures), and the OAuth redirect is delivered to the real loopback listener.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from unittest import mock

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-r29-sync-")

import httpx  # noqa: E402

from app import gdrive_sync as core  # noqa: E402
from app import gdrive_sync_crypto as crypto  # noqa: E402
from app import gdrive_sync_drive as drive  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def make_data(root: Path) -> None:
    (root / "memuai" / "modules" / "notes").mkdir(parents=True)
    (root / "memuai" / "profiles" / "p2").mkdir(parents=True)
    (root / "memuai" / "preferences.json").write_text(json.dumps({
        "language": "ru", "ui_design": "glass", "telegram_bot_token": "123:SECRET-BOT",
        "smtp_password": "pw-secret", "search_api_keys": {"brave": "KEY-1"}, "max_tokens": 4096}), encoding="utf-8")
    (root / "memuai" / "chats.json").write_text(json.dumps([{"id": "c1", "messages": ["hello"]}]), encoding="utf-8")
    (root / "memuai" / "memory.json").write_text(json.dumps({"items": ["likes tea"]}), encoding="utf-8")
    (root / "memuai" / "modules" / "notes" / "index.html").write_text("<p>notes</p>", encoding="utf-8")
    (root / "memuai" / "profiles" / "p2" / "chats.json").write_text("[]", encoding="utf-8")
    (root / "accounts.json").write_text(json.dumps({"genspark": {"cookie": "SESSION-COOKIE"}}), encoding="utf-8")
    (root / "config.json").write_text(json.dumps({"port": 8317, "api_keys": ["sk-local"]}), encoding="utf-8")
    for junk in ("logs/requests.jsonl", "memuai/backups/chats-1.json", "memuai/browser-profile/Default/Cookies",
                 "memuai/voice-models/stt/model.bin", "memuai/proxy.json", "memuai/pricing-cache.json"):
        path = root / junk
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("junk", encoding="utf-8")


class ClassificationTest(unittest.TestCase):
    def test_categories_and_exclusions(self) -> None:
        cases = {
            "memuai/preferences.json": "settings", "memuai/chats.json": "chats", "memuai/quick_chats.json": "chats",
            "memuai/profiles/p2/chats.json": "chats", "memuai/memory.json": "memory", "memuai/reflect.json": "memory",
            "memuai/prompts.json": "prompts", "memuai/schedules.json": "schedules", "memuai/workspaces.json": "work",
            "memuai/modules/x/index.html": "modules", "memuai/panels.json": "panels", "memuai/quick_canvas.json": "panels",
            "memuai/quick_state.json": "quick", "memuai/companion/models/a/meta.json": "companion",
            "memuai/telegram_state.json": "telegram", "config.json": "proxy", "models.json": "proxy",
            "memuai/checkpoints/a/b.json": "history", "memuai/new_feature.json": "other",
            "memuai/characters/alice.json": "characters",
            "accounts.json": "secrets", "public.json": "secrets", "memuai/mcp.json": "secrets",
            "memuai/integrations.json": "secrets", "memuai/profiles/p2/panels-secrets.json": "secrets",
        }
        for rel, expected in cases.items():
            self.assertEqual(core.classify(rel), expected, rel)
        for rel in ("logs/a.jsonl", "memuai/backups/x.json", "memuai/browser-profile/Default/Cookies",
                    "memuai/voice-models/tts/v.onnx", "memuai/wake-models/ru/am/final.mdl", "local-models/x.gguf",
                    "memuai/sync/secrets.json", "memuai/proxy.json", "memuai/memuai.json", "memuai/chats.json.lock",
                    "memuai/chats.json.tmp-1-2-3", "memuai/pricing-cache.json", "memuai/safe_mode.json",
                    "memuai/startup_guard.json", "dfy_browser/x", "memuai/modules/x/node_modules/a.js"):
            self.assertIsNone(core.classify(rel), rel)

    def test_safe_rel_rejects_traversal(self) -> None:
        for bad in ("../x", "a/../../b", "/etc/passwd", "C:/Windows/x", "a//b", "a/./b", ""):
            self.assertEqual(core._safe_rel(bad), "", bad)
        self.assertEqual(core._safe_rel("memuai\\chats.json"), "memuai/chats.json")

    def test_redaction_and_merge(self) -> None:
        data = {"language": "ru", "telegram_bot_token": "T", "smtp_password": "P", "search_api_keys": {"a": "b"},
                "max_tokens": 4096, "voice_stt_api_key": "", "nested": {"client_secret": "S", "ok": 1}}
        cleaned, removed = core.redact_json(data)
        self.assertNotIn("telegram_bot_token", cleaned)
        self.assertNotIn("smtp_password", cleaned)
        self.assertNotIn("search_api_keys", cleaned)
        self.assertEqual(cleaned["max_tokens"], 4096)       # numbers are never secrets
        self.assertEqual(cleaned["voice_stt_api_key"], "")  # empty values stay
        self.assertEqual(cleaned["nested"], {"ok": 1})
        local = {"telegram_bot_token": "LOCAL", "smtp_password": "LP", "nested": {"client_secret": "LS"}}
        merged = core._merge_redacted(cleaned, local, removed)
        self.assertEqual(merged["telegram_bot_token"], "LOCAL")
        self.assertEqual(merged["nested"]["client_secret"], "LS")
        self.assertEqual(merged["language"], "ru")


class CryptoTest(unittest.TestCase):
    def test_roundtrip_tamper_and_wrong_passphrase(self) -> None:
        keys, record = crypto.new_key_record("correct horse")
        blob = crypto.encrypt_bytes(b"x" * 200_000, keys)
        self.assertNotIn(b"xxxx", blob)
        self.assertEqual(crypto.decrypt_bytes(blob, keys), b"x" * 200_000)
        broken = bytearray(blob)
        broken[-5] ^= 1
        with self.assertRaises(crypto.CryptoError):
            crypto.decrypt_bytes(bytes(broken), keys)
        with self.assertRaises(crypto.CryptoError):
            crypto.keys_from_record("wrong horse", record)
        self.assertEqual(crypto.keys_from_record("correct horse", record).key_id, keys.key_id)

    def test_file_stream(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        try:
            keys, _ = crypto.new_key_record("passphrase-1")
            data = os.urandom(3 * crypto.BLOCK * 16 + 123)
            (tmp / "a").write_bytes(data)
            crypto.encrypt_file(tmp / "a", tmp / "b", keys)
            crypto.decrypt_file(tmp / "b", tmp / "c", keys)
            self.assertEqual((tmp / "c").read_bytes(), data)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_secret_store_never_writes_plaintext(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        try:
            store = crypto.SecretStore(tmp)
            store.set("google_refresh_token", "1//REFRESH-TOKEN-VALUE")
            self.assertNotIn("REFRESH-TOKEN-VALUE", (tmp / "secrets.json").read_text())
            self.assertEqual(crypto.SecretStore(tmp).get("google_refresh_token"), "1//REFRESH-TOKEN-VALUE")
            store.delete("google_refresh_token")
            self.assertFalse(store.has("google_refresh_token"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class FolderBackupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="r29sync-"))
        self.root = self.tmp / "data"
        self.dest = self.tmp / "dest"
        self.dest.mkdir()
        make_data(self.root)
        self.svc = core.SyncService(self.root, role="memuai")
        self.svc.save_settings({"target": "folder", "folder_path": str(self.dest), "enabled": True})
        self.repo = self.dest / "MemuAI Backups"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _manifest(self, snap_id: str) -> dict:
        name = next(p for p in os.listdir(self.repo) if p.endswith(f"-{snap_id}.json"))
        return json.loads((self.repo / name).read_text(encoding="utf-8"))

    def test_backup_content_secrets_and_exclusions(self) -> None:
        result = self.svc.backup("manual")
        manifest = self._manifest(result["id"])
        files = manifest["body"]["files"]
        self.assertIn("memuai/chats.json", files)
        self.assertIn("memuai/profiles/p2/chats.json", files)
        self.assertIn("config.json", files)
        self.assertNotIn("accounts.json", files)            # secrets are off by default
        for junk in ("logs/requests.jsonl", "memuai/proxy.json", "memuai/pricing-cache.json"):
            self.assertNotIn(junk, files)
        self.assertTrue(all(not k.startswith("memuai/sync/") for k in files))
        # Tokens are cut out of settings files: no secret byte anywhere in the repository.
        raw = b"".join(p.read_bytes() for p in self.repo.iterdir())
        packs = [p for p in self.repo.iterdir() if p.name.startswith("memuai-pack-")]
        for pack in packs:
            with zipfile.ZipFile(pack) as archive:
                raw += b"".join(archive.read(n) for n in archive.namelist())
        for secret in (b"SECRET-BOT", b"pw-secret", b"KEY-1", b"SESSION-COOKIE", b"sk-local"):
            self.assertNotIn(secret, raw)
        self.assertIn(["telegram_bot_token"], files["memuai/preferences.json"]["redacted"])
        self.assertEqual(manifest["header"]["profiles"], ["default", "p2"])

    def test_incremental_unchanged_and_retention(self) -> None:
        first = self.svc.backup("manual")
        self.assertGreater(first["new_files"], 3)
        unchanged = self.svc.backup("auto")
        self.assertTrue(unchanged["unchanged"])
        (self.root / "memuai" / "chats.json").write_text('[{"id": "c1", "messages": ["hello", "again"]}]', encoding="utf-8")
        second = self.svc.backup("auto")
        self.assertFalse(second["unchanged"])
        self.assertEqual(second["new_files"], 1)            # only the changed file is uploaded
        manifest = self._manifest(second["id"])
        self.assertEqual(set(manifest["header"]["packs"]) - {n for n in os.listdir(self.repo)}, set())
        # Retention: keep 2 versions, a pinned one survives.
        self.svc.pin(first["id"], True)
        self.svc.save_settings({"keep_versions": 1})
        for i in range(3):
            (self.root / "memuai" / "memory.json").write_text(json.dumps({"items": [i]}), encoding="utf-8")
            last = self.svc.backup("manual")
        ids = [row["id"] for row in self.svc.list_snapshots()["snapshots"]]
        self.assertEqual(set(ids), {first["id"], last["id"]})
        # Garbage collection: every pack left is referenced by a kept manifest.
        referenced = set()
        for snap in ids:
            referenced.update(self._manifest(snap)["header"]["packs"])
        on_disk = {n for n in os.listdir(self.repo) if n.startswith("memuai-pack-")}
        self.assertEqual(on_disk, referenced)
        # The pinned first snapshot is still fully restorable.
        preview = self.svc.preview_restore(first["id"])
        self.assertIn("chats", preview["categories"])

    def test_restore_safety_mirror_and_undo(self) -> None:
        snap = self.svc.backup("manual")["id"]
        prefs = self.root / "memuai" / "preferences.json"
        prefs.write_text(json.dumps({"language": "en", "telegram_bot_token": "NEW-LOCAL"}), encoding="utf-8")
        (self.root / "memuai" / "chats.json").write_text("[]", encoding="utf-8")
        (self.root / "memuai" / "modules" / "extra").mkdir()
        (self.root / "memuai" / "modules" / "extra" / "index.html").write_text("new", encoding="utf-8")
        (self.root / "memuai" / "memory.json").write_text('{"items": ["changed"]}', encoding="utf-8")
        report = self.svc.restore(snap, ["settings", "chats", "modules"], mirror=True)
        self.assertTrue(report["ok"])
        restored = json.loads(prefs.read_text(encoding="utf-8"))
        self.assertEqual(restored["language"], "ru")
        self.assertEqual(restored["telegram_bot_token"], "NEW-LOCAL")   # the local token is kept
        self.assertEqual(json.loads((self.root / "memuai" / "chats.json").read_text())[0]["id"], "c1")
        self.assertFalse((self.root / "memuai" / "modules" / "extra" / "index.html").exists())  # mirror
        self.assertEqual(json.loads((self.root / "memuai" / "memory.json").read_text())["items"], ["changed"])  # not selected
        self.assertTrue((self.svc.safety_dir() / report["safety"]).is_file())
        undo = self.svc.undo_restore(report["safety"])
        self.assertTrue(undo["ok"])
        self.assertEqual(json.loads(prefs.read_text(encoding="utf-8"))["language"], "en")
        self.assertEqual((self.root / "memuai" / "modules" / "extra" / "index.html").read_text(), "new")

    def test_tampered_manifest_cannot_escape_or_smuggle_secrets(self) -> None:
        snap = self.svc.backup("manual")["id"]
        name = next(p for p in os.listdir(self.repo) if p.endswith(f"-{snap}.json"))
        document = json.loads((self.repo / name).read_text(encoding="utf-8"))
        any_entry = next(iter(document["body"]["files"].values()))
        document["body"]["files"]["../escaped.json"] = dict(any_entry)
        document["body"]["files"]["accounts.json"] = {**any_entry, "cat": "settings"}
        document["body"]["files"]["memuai/sync/secrets.json"] = {**any_entry, "cat": "settings"}
        (self.repo / name).write_text(json.dumps(document), encoding="utf-8")
        accounts_before = (self.root / "accounts.json").read_text()
        secrets_file = self.root / "memuai" / "sync" / "secrets.json"
        secrets_before = secrets_file.read_bytes() if secrets_file.exists() else None
        self.svc.restore(snap, ["settings"])
        self.assertFalse((self.root.parent / "escaped.json").exists())
        self.assertEqual((self.root / "accounts.json").read_text(), accounts_before)
        # The sync key store is never written from a manifest: unchanged, or still absent.
        self.assertEqual(secrets_file.read_bytes() if secrets_file.exists() else None, secrets_before)

    def test_encryption_secrets_and_restore_on_another_pc(self) -> None:
        with self.assertRaises(core.SyncError):
            self.svc.save_settings({"include_secrets": True})          # secrets need encryption
        with self.assertRaises(core.SyncError):
            self.svc.save_settings({"encrypt": True})                  # encryption needs a passphrase
        self.svc.save_settings({"passphrase": "my long passphrase", "encrypt": True, "include_secrets": True})
        snap = self.svc.backup("manual")
        self.assertTrue(snap["encrypted"])
        manifest = self._manifest(snap["id"])
        self.assertNotIn("body", manifest)
        blob = b"".join(p.read_bytes() for p in self.repo.iterdir())
        for secret in (b"SESSION-COOKIE", b"hello", b"likes tea", b"SECRET-BOT"):
            self.assertNotIn(secret, blob)
        # Another PC: empty data folder, same backup folder, no stored keys.
        other_root = self.tmp / "other"
        other_root.mkdir()
        other = core.SyncService(other_root)
        other.save_settings({"target": "folder", "folder_path": str(self.dest)})
        listed = other.list_snapshots()["snapshots"]
        self.assertTrue(listed[0]["encrypted"])
        self.assertFalse(listed[0]["own"])
        with self.assertRaises(core.SyncError) as caught:
            other.restore(snap["id"], None)
        self.assertEqual(caught.exception.code, "need_passphrase")
        with self.assertRaises(core.SyncError):
            other.restore(snap["id"], None, passphrase="wrong passphrase")
        report = other.restore(snap["id"], None, passphrase="my long passphrase")
        self.assertTrue(report["ok"])
        self.assertIn("SESSION-COOKIE", (other_root / "accounts.json").read_text())
        self.assertIn("SECRET-BOT", (other_root / "memuai" / "preferences.json").read_text())

    def test_export_and_restore_from_file(self) -> None:
        snap = self.svc.backup("manual")["id"]
        target = self.tmp / "export.zip"
        exported = self.svc.export_snapshot(snap, target)
        self.assertEqual(exported["files"], len(self._manifest(snap)["body"]["files"]))
        with zipfile.ZipFile(target) as archive:
            self.assertIn("data/memuai/chats.json", archive.namelist())
        (self.root / "memuai" / "chats.json").write_text("[]", encoding="utf-8")
        info = self.svc.inspect_export(target)
        self.assertIn("chats", info["categories"])
        self.assertTrue(self.svc.restore_export(target, ["chats"])["ok"])
        self.assertIn("c1", (self.root / "memuai" / "chats.json").read_text())

    def test_folder_inside_data_dir_is_refused(self) -> None:
        with self.assertRaises(core.SyncError):
            self.svc.save_settings({"folder_path": str(self.root / "memuai")})
        with self.assertRaises(core.SyncError):
            self.svc.save_settings({"folder_path": "relative/path"})

    def test_lock_prevents_parallel_runs_and_status(self) -> None:
        other = core.SyncService(self.root, role="proxy")
        self.assertTrue(other._acquire())
        try:
            with self.assertRaises(core.SyncError) as caught:
                self.svc.backup("manual")
            self.assertEqual(caught.exception.code, "busy")
            self.assertIsNotNone(self.svc.busy())
        finally:
            other._release()
        self.svc.backup("manual")
        status = self.svc.status()
        self.assertTrue(status["last_success"])
        self.assertIsNone(status["last_error"])
        self.assertEqual(status["history"][0]["kind"], "backup")
        self.assertNotIn('"passphrase"', json.dumps(status))       # only the has_passphrase flag

    def test_failure_is_recorded_with_backoff(self) -> None:
        shutil.rmtree(self.dest)
        with self.assertRaises(core.SyncError):
            self.svc.backup("auto")
        state = self.svc.state()
        self.assertEqual(state["failures"], 1)
        self.assertEqual(state["last_error"]["code"], "folder_missing")
        due = self.svc.next_due()
        self.assertGreaterEqual(due, state["last_attempt"] + 299)


class SchedulerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="r29sched-"))
        self.root = self.tmp / "data"
        make_data(self.root)
        (self.tmp / "dest").mkdir()
        self.svc = core.SyncService(self.root)
        self.svc.save_settings({"target": "folder", "folder_path": str(self.tmp / "dest"), "enabled": True,
                                "interval_minutes": 60})

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_due_tick_request_and_exit(self) -> None:
        self.assertGreater(self.svc.next_due(), time.time())       # first run 2 minutes after enabling
        self.svc._update_state(last_success=time.time() - 3700)
        result = self.svc.tick()
        self.assertTrue(result and result["id"])
        self.assertIsNone(self.svc.tick())                         # not due again
        self.svc.request("exit")
        self.assertIsNotNone(self.svc.tick())                      # a request is served at once
        self.assertFalse((self.svc.dir / "request.json").exists())
        # On exit: with a live Proxy scheduler the backup is handed over…
        proxy = core.SyncService(self.root, role="proxy")
        proxy._write_json("heartbeat-proxy.json", {"pid": os.getpid(), "ts": time.time()})
        self.assertEqual(self.svc.on_exit(), "handed")
        self.assertTrue((self.svc.dir / "request.json").exists())
        # …the installer's quick close only leaves a request…
        self.assertEqual(self.svc.on_exit(quick=True), "queued")
        # …and without the Proxy MemuAI makes it itself.
        (self.svc.dir / "heartbeat-proxy.json").unlink()
        self.assertEqual(self.svc.on_exit(), "done")
        # Paused (safe mode): nothing happens.
        self.svc.paused_reason = lambda: "safe_mode"
        self.assertEqual(self.svc.on_exit(), "none")
        self.svc._update_state(last_success=0)
        self.assertIsNone(self.svc.tick())

    def test_daily_time_alignment(self) -> None:
        self.svc.save_settings({"interval_minutes": 1440, "at_time": "03:30"})
        self.svc._update_state(last_success=time.time() - 3600, failures=0)
        due = self.svc.next_due()
        local = time.localtime(due)
        self.assertEqual((local.tm_hour, local.tm_min), (3, 30))
        self.assertGreater(due, time.time())


class FakeGoogle:
    """In-memory Google OAuth + Drive v3 sufficient for the sync feature."""

    def __init__(self) -> None:
        self.files: dict[str, dict] = {}
        self.sessions: dict[str, dict] = {}
        self.challenge = ""
        self.access = "ya29.ACCESS-1"
        self.refresh = "1//REFRESH-xyz"
        self.revoked = False
        self.fail_next = 0          # 503 for the next N API requests
        self.expire_next = False    # 401 once
        self.requests: list[str] = []
        self.lock = threading.Lock()

    def handler(self, request: httpx.Request) -> httpx.Response:
        with self.lock:
            return self._handle(request)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        path = url.path
        self.requests.append(f"{request.method} {path}")
        if str(url).startswith(drive.TOKEN_URL):
            form = dict(urllib.parse.parse_qsl(request.content.decode()))
            if form["grant_type"] == "authorization_code":
                expected = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()).decode().rstrip("=")
                if form.get("code") != "AUTH-CODE" or expected != self.challenge or form.get("client_secret") != "GOCSPX-test":
                    return httpx.Response(400, json={"error": "invalid_grant"})
                return httpx.Response(200, json={"access_token": self.access, "refresh_token": self.refresh, "expires_in": 3599})
            if form["grant_type"] == "refresh_token":
                if self.revoked or form["refresh_token"] != self.refresh:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                self.access = "ya29.ACCESS-" + str(len(self.requests))
                return httpx.Response(200, json={"access_token": self.access, "expires_in": 3599})
        if str(url).startswith(drive.REVOKE_URL):
            self.revoked = True
            return httpx.Response(200)
        if path.startswith("/upload/session/"):
            return self._session_put(request, path.rsplit("/", 1)[1])
        auth = request.headers.get("authorization", "")
        if auth != "Bearer " + self.access:
            return httpx.Response(401, json={"error": {"code": 401, "message": "bad token"}})
        if self.expire_next:
            self.expire_next = False
            return httpx.Response(401, json={"error": {"code": 401}})
        if self.fail_next:
            self.fail_next -= 1
            return httpx.Response(503, json={"error": {"code": 503}})
        if path == "/drive/v3/about":
            return httpx.Response(200, json={"user": {"emailAddress": "user@example.com", "displayName": "User"},
                                             "storageQuota": {"limit": "16106127360", "usage": "1073741824"}})
        if path == "/drive/v3/files" and request.method == "GET":
            q = url.params.get("q", "")
            parent = re.search(r"'([^']+)' in parents", q)
            rows = []
            for fid, row in self.files.items():
                if parent and parent.group(1) not in row["parents"]:
                    continue
                if "name = '" in q and f"name = '{row['name']}'" not in q:
                    continue
                if "name contains 'memuai-'" in q and not row["name"].startswith("memuai-"):
                    continue
                if parent and parent.group(1) == "appDataFolder" and url.params.get("spaces") != "appDataFolder":
                    continue
                rows.append({"id": fid, "name": row["name"], "size": str(len(row["data"])), "modifiedTime": row["modified"]})
            page = int(url.params.get("pageToken") or 0)
            chunk = rows[page:page + 3]                 # tiny pages exercise nextPageToken
            body = {"files": chunk}
            if page + 3 < len(rows):
                body["nextPageToken"] = str(page + 3)
            return httpx.Response(200, json=body)
        if path == "/drive/v3/files" and request.method == "POST":
            meta = json.loads(request.content)
            fid = self._new(meta["name"], meta.get("parents") or ["root"], b"", meta.get("mimeType"))
            return httpx.Response(200, json={"id": fid})
        m = re.fullmatch(r"/drive/v3/files/([^/]+)", path)
        if m and request.method == "GET" and url.params.get("alt") == "media":
            row = self.files.get(m.group(1))
            return httpx.Response(200, content=row["data"]) if row else httpx.Response(404, json={"error": {"code": 404}})
        if m and request.method == "DELETE":
            return httpx.Response(204) if self.files.pop(m.group(1), None) else httpx.Response(404)
        if path == "/upload/drive/v3/files" and url.params.get("uploadType") == "multipart":
            boundary = request.headers["content-type"].split("boundary=")[1]
            parts = request.content.split(("--" + boundary).encode())
            meta = json.loads(parts[1].split(b"\r\n\r\n", 1)[1].rstrip(b"\r\n"))
            data = parts[2].split(b"\r\n\r\n", 1)[1][:-2]
            fid = self._new(meta["name"], meta["parents"], data)
            return httpx.Response(200, json={"id": fid, "size": str(len(data)), "modifiedTime": self.files[fid]["modified"]})
        if path == "/upload/drive/v3/files" and url.params.get("uploadType") == "resumable":
            meta = json.loads(request.content)
            sid = f"s{len(self.sessions)}"
            self.sessions[sid] = {"meta": meta, "data": b"", "total": int(request.headers["x-upload-content-length"]), "flaky": True}
            return httpx.Response(200, headers={"Location": f"https://www.googleapis.com/upload/session/{sid}"})
        m = re.fullmatch(r"/upload/drive/v3/files/([^/]+)", path)
        if m and request.method == "PATCH":
            row = self.files[m.group(1)]
            row["data"] = request.content
            return httpx.Response(200, json={"id": m.group(1), "size": str(len(row["data"])), "modifiedTime": row["modified"]})
        return httpx.Response(404, json={"error": {"code": 404, "message": f"unhandled {request.method} {path}"}})

    def _session_put(self, request: httpx.Request, sid: str) -> httpx.Response:
        session = self.sessions[sid]
        rng = request.headers.get("content-range", "")
        if rng.startswith("bytes */"):
            have = len(session["data"])
            return httpx.Response(308, headers={"Range": f"bytes=0-{have - 1}"} if have else {})
        if session["flaky"] and session["data"]:
            session["flaky"] = False   # drop the second chunk once: the client must ask and resume
            return httpx.Response(503)
        start, end = (int(x) for x in rng.split(" ")[1].split("/")[0].split("-"))
        assert start == len(session["data"]), (start, len(session["data"]))
        session["data"] += request.content
        if len(session["data"]) >= session["total"]:
            fid = self._new(session["meta"]["name"], session["meta"]["parents"], session["data"])
            return httpx.Response(200, json={"id": fid, "size": str(session["total"]), "modifiedTime": self.files[fid]["modified"]})
        return httpx.Response(308, headers={"Range": f"bytes=0-{len(session['data']) - 1}"})

    def _new(self, name: str, parents: list, data: bytes, mime: str | None = None) -> str:
        fid = "f" + str(len(self.files) + 1) + "x" + str(int(time.time() * 1000) % 100000)
        while fid in self.files:
            fid += "y"
        self.files[fid] = {"name": name, "parents": parents, "data": data, "mime": mime,
                           "modified": f"2026-10-09T12:{len(self.files) // 60:02d}:{len(self.files) % 60:02d}.000Z"}
        return fid


class DriveTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="r29drive-"))
        self.root = self.tmp / "data"
        make_data(self.root)
        # A file large enough for the resumable path (with the limits lowered below).
        (self.root / "memuai" / "companion").mkdir(parents=True, exist_ok=True)
        (self.root / "memuai" / "companion" / "voice.wav").write_bytes(os.urandom(700 * 1024))
        self.google = FakeGoogle()
        self.transport = httpx.MockTransport(self.google.handler)
        self.opened: list[str] = []
        self.svc = core.SyncService(self.root, transport=self.transport, open_browser=self.opened.append)
        self.patches = [mock.patch.object(drive, "SIMPLE_LIMIT", 64 * 1024), mock.patch.object(drive, "CHUNK", 256 * 1024),
                        mock.patch.object(drive.time, "sleep", lambda s: None)]
        for p in self.patches:
            p.start()

    def tearDown(self) -> None:
        for p in self.patches:
            p.stop()
        self.svc.auth.cancel_flow()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def connect(self) -> None:
        self.svc.save_settings({"client_id": "123-abc.apps.googleusercontent.com", "client_secret": "GOCSPX-test",
                                "target": "drive", "enabled": True})
        started = self.svc.auth.start_flow()
        self.assertEqual(self.opened, [started["auth_url"]])
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(started["auth_url"]).query))
        self.assertEqual(query["code_challenge_method"], "S256")
        self.assertIn(drive.SCOPE_APPDATA, query["scope"])
        self.assertNotIn("auth/drive ", query["scope"] + " ")     # never the full-Drive scope
        self.assertTrue(query["redirect_uri"].startswith("http://127.0.0.1:"))
        self.google.challenge = query["code_challenge"]
        redirect = query["redirect_uri"] + "?" + urllib.parse.urlencode({"state": query["state"], "code": "AUTH-CODE"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(redirect, timeout=10) as response:
            self.assertIn("Google Drive", response.read().decode())
        for _ in range(50):
            if self.svc.auth.flow_status()["state"] != "waiting":
                break
            time.sleep(0.05)
        self.assertEqual(self.svc.auth.flow_status()["state"], "done")

    def test_oauth_rejects_wrong_state(self) -> None:
        self.svc.save_settings({"client_id": "123-abc.apps.googleusercontent.com", "client_secret": "GOCSPX-test"})
        started = self.svc.auth.start_flow()
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(started["auth_url"]).query))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(query["redirect_uri"] + "?state=forged&code=AUTH-CODE", timeout=10):
            pass
        time.sleep(0.1)
        self.assertEqual(self.svc.auth.flow_status()["state"], "error")
        self.assertFalse(self.svc.auth.connected())

    def test_full_cycle_with_retries(self) -> None:
        self.connect()
        self.assertTrue(self.svc.auth.connected())
        stored = (self.svc.dir / "secrets.json").read_text()
        self.assertNotIn("REFRESH-xyz", stored)
        self.assertEqual(self.svc.state()["account"]["email"], "user@example.com")
        self.google.fail_next = 2          # transient 503s are retried
        result = self.svc.backup("manual")
        self.assertFalse(result["unchanged"])
        names = {row["name"] for row in self.google.files.values()}
        self.assertTrue(any(n.startswith("memuai-manifest-") for n in names))
        self.assertTrue(all(row["parents"] == ["appDataFolder"] for row in self.google.files.values()))
        # The access token expired: refreshed transparently.
        self.google.expire_next = True
        listed = self.svc.list_snapshots()
        self.assertEqual(len(listed["snapshots"]), 1)
        self.assertEqual(listed["target"], "Google Drive")
        # Restore through Drive.
        wav = (self.root / "memuai" / "companion" / "voice.wav").read_bytes()
        (self.root / "memuai" / "companion" / "voice.wav").write_bytes(b"broken")
        (self.root / "memuai" / "chats.json").write_text("[]", encoding="utf-8")
        report = self.svc.restore(result["id"], ["chats", "companion"])
        self.assertTrue(report["ok"])
        self.assertEqual((self.root / "memuai" / "companion" / "voice.wav").read_bytes(), wav)
        self.assertIn("c1", (self.root / "memuai" / "chats.json").read_text())
        # Delete the snapshot: its packs go too.
        self.svc.delete_snapshot(result["id"])
        self.assertFalse(any(row["name"].startswith(("memuai-pack-", "memuai-manifest-")) for row in self.google.files.values()))
        # Disconnect revokes the token at Google.
        self.svc.auth.disconnect()
        self.assertTrue(self.google.revoked)
        self.assertFalse(self.svc.auth.connected())
        with self.assertRaises(core.SyncError):
            self.svc.backup("manual")

    def test_visible_folder_and_revoked_access(self) -> None:
        self.connect()
        self.svc.save_settings({"drive_space": "folder"})
        self.svc.backup("manual")
        folders = [fid for fid, row in self.google.files.items() if row["mime"] == drive.FOLDER_MIME]
        self.assertEqual(len(folders), 1)
        self.assertTrue(all(folders[0] in row["parents"] for row in self.google.files.values()
                            if row["name"].startswith("memuai-")))
        # Access revoked in the Google account: a clear «connect again» error.
        self.google.revoked = True
        self.google.access = "ya29.REVOKED"
        self.svc.auth._access = ("", 0.0)
        with self.assertRaises(drive.DriveAuthError) as caught:
            self.svc.backup("manual")
        self.assertEqual(caught.exception.code, "invalid_grant")
        self.assertEqual(self.svc.state()["last_error"]["code"], "invalid_grant")


class ApiTest(unittest.TestCase):
    def test_routes(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import gdrive_sync_api
        tmp = Path(tempfile.mkdtemp(prefix="r29api-"))
        try:
            make_data(tmp / "data")
            (tmp / "dest").mkdir()
            svc = core.SyncService(tmp / "data")
            app = FastAPI()
            app.state.sync_no_scheduler = True
            gdrive_sync_api.register(app, service=svc)
            with TestClient(app) as client:
                status = client.get("/api/pg/sync/status").json()
                self.assertEqual({c["id"] for c in status["categories"]}, set(core.CATEGORY_IDS))
                self.assertEqual(client.post("/api/pg/sync/backup", json={}).status_code, 400)   # not ready
                bad = client.post("/api/pg/sync/settings", json={"include_secrets": True})
                self.assertEqual(bad.json()["code"], "secrets_need_encryption")
                # A path is never taken from the request: only a detected folder or the native dialog.
                refused = client.post("/api/pg/sync/pick-folder", json={"detected": str(tmp / "dest")})
                self.assertEqual(refused.status_code, 400)
                svc.save_settings({"target": "folder", "folder_path": str(tmp / "dest")})
                self.assertTrue(client.post("/api/pg/sync/test").json()["ok"])
                self.assertTrue(client.post("/api/pg/sync/backup", json={"label": "before update"}).json()["started"])
                for _ in range(100):
                    if not client.get("/api/pg/sync/status").json()["running"] and svc.state().get("last_success"):
                        break
                    time.sleep(0.05)
                snaps = client.get("/api/pg/sync/snapshots").json()["snapshots"]
                self.assertEqual(snaps[0]["label"], "before update")
                preview = client.post("/api/pg/sync/preview", json={"id": snaps[0]["id"]}).json()
                self.assertIn("settings", preview["categories"])
                restored = client.post("/api/pg/sync/restore", json={"id": snaps[0]["id"], "categories": ["chats"]}).json()
                self.assertTrue(restored["ok"])
                self.assertEqual(len(client.get("/api/pg/sync/safety").json()["items"]), 1)
                self.assertEqual(client.post("/api/pg/sync/restore-file", json={"token": "nope"}).status_code, 400)
                client.post("/api/pg/sync/settings", json={"client_id": "x.apps.googleusercontent.com",
                                                           "client_secret": "GOCSPX-hidden", "passphrase": "hidden passphrase"})
                text = json.dumps(client.get("/api/pg/sync/status").json())
                self.assertNotIn("GOCSPX-hidden", text)
                self.assertNotIn("hidden passphrase", text)
                self.assertTrue(client.get("/api/pg/sync/status").json()["settings"]["has_client_secret"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class ContractTest(unittest.TestCase):
    def test_hooks_docs_build_and_ui(self) -> None:
        api = (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8")
        self.assertIn("gdrive_sync_api.register(app)", api)
        self.assertLess(api.index("gdrive_sync_api.register(app)"), api.index('app.mount("/", StaticFiles'))
        self.assertIn("start_proxy_scheduler", (ROOT / "app" / "main.py").read_text(encoding="utf-8"))
        self.assertIn("on_exit(quick=", (ROOT / "memuai.py").read_text(encoding="utf-8"))
        for spec in ("MemuAI.spec", "build.py"):
            text = (ROOT / spec).read_text(encoding="utf-8")
            for module in ("app.gdrive_sync", "app.gdrive_sync_crypto", "app.gdrive_sync_drive", "app.gdrive_sync_api"):
                self.assertIn(f"'{module}'" if spec.endswith(".spec") else f'"{module}"', text, f"{spec}: {module}")
        self.assertIn("'app.gdrive_sync'", (ROOT / "MemuAIProxy.spec").read_text(encoding="utf-8"))
        index = (ROOT / "app" / "pgstatic" / "index.html").read_text(encoding="utf-8")
        self.assertIn("/sync-settings.js?v=", index)
        self.assertIn("/sync-settings.css?v=", index)
        app_js = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        self.assertIn('P("sync", "you"', app_js)
        self.assertIn('id="pane-sync"', app_js)
        docs_index = json.loads((ROOT / "app" / "docs" / "index.json").read_text(encoding="utf-8"))
        pages = [p for g in docs_index["groups"] for p in g["pages"]]
        self.assertIn("sync", pages)
        for lang in ("ru", "en"):
            page = (ROOT / "app" / "docs" / lang / "sync.md").read_text(encoding="utf-8")
            self.assertIn("console.cloud.google.com", page)
            self.assertIn("drive.appdata", page)

    def test_english_strings_have_no_cyrillic(self) -> None:
        source = (ROOT / "app" / "pgstatic" / "sync-settings.js").read_text(encoding="utf-8")
        source += (ROOT / "app" / "pgstatic" / "safe-mode.js").read_text(encoding="utf-8")
        # Every L("ru", "en") pair: the English half has no Cyrillic letter.
        for match in re.finditer(r'L\(\s*(?:"(?:[^"\\]|\\.)*"|`(?:[^`\\]|\\.)*`)\s*,\s*("(?:[^"\\]|\\.)*"|`(?:[^`\\]|\\.)*`)', source):
            english = re.sub(r"\$\{[^}]*\}", "", match.group(1))
            self.assertIsNone(re.search(r"[А-Яа-яЁё]", english), match.group(0)[:120])
        for row in core.CATEGORIES:
            self.assertIsNone(re.search(r"[А-Яа-яЁё]", row[2] + row[4]), row[0])


if __name__ == "__main__":
    unittest.main()
