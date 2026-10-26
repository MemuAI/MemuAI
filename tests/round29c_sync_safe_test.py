"""Round 29 phase 2 (wave C): deep regression tests for «Синхронизация» and the safe mode.

Realistic data dir (layout of the real code paths), folder target as «Google Drive for desktop», a fake Drive
with failures (quota, network, revoked grant), two devices, encryption and retention.  Offline only.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-r29c-sync-")

import httpx  # noqa: E402

from app import gdrive_sync as core  # noqa: E402
from app import gdrive_sync_drive as drive  # noqa: E402
from tests import round29_sync_test as base  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

def put(root: Path, rel: str, data) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, (dict, list)):
        data = json.dumps(data, ensure_ascii=False)
    path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)

def make_real(root: Path) -> None:
    m = "memuai/"
    put(root, m + "preferences.json", {"language": "ru", "telegram_bot_token": "1:BOT", "pin_sidebar": True})
    put(root, m + "chats.json", [{"id": "c1", "title": "Чат"}])
    put(root, m + "profiles/work/chats.json", [{"id": "w1"}])
    put(root, m + "memory.json", {"items": ["чай"]})
    put(root, m + "modules/state.json", {"modules": {}})
    put(root, m + "modules/store/characters/index.json", [{"id": "alice"}])
    put(root, m + "modules/store/characters/chats/alice/c1.json", {"m": ["hi"]})
    put(root, m + "modules/persist/characters.json", {"last": "alice"})
    put(root, m + "modules/store/notes/index.json", {"n": 1})
    put(root, m + "generated_media/2026-10/img.png", os.urandom(4096))
    put(root, m + "profiles/work/generated_media/v.mp4", os.urandom(4096))
    put(root, m + "quick_explorer/undo/x/files/1", os.urandom(2048))
    put(root, m + "export-tmp/a.pdf", b"%PDF")
    put(root, m + "bin/cloudflared.exe", b"MZ")
    put(root, "memunote/settings.json", {"theme": "dark"})
    put(root, "memunote/recent.json", ["C:/a.txt"])
    put(root, "memunote/recovery/tab1.txt", "черновик")
    put(root, "memunote/instance.json", {"port": 1})
    put(root, "memunote/webview/EBWebView/Default/Network/Cookies", os.urandom(512))
    put(root, "config.json", {"port": 8317})

class ClassificationOfRealLayoutTest(unittest.TestCase):
    def test_real_paths_land_in_the_right_category(self) -> None:
        cases = {
            "memuai/modules/store/characters/index.json": "characters",
            "memuai/modules/store/characters/chats/alice/c1.json": "characters",
            "memuai/modules/persist/characters.json": "characters",
            "memuai/profiles/p2/modules/store/characters/index.json": "characters",
            "memuai/modules/store/notes/index.json": "modules",
            "memuai/generated_media/2026-10/img.png": "chats",
            "memuai/profiles/work/generated_media/v.mp4": "chats",
            "memunote/settings.json": "memunote",
            "memunote/recovery/tab1.txt": "memunote",
            "memunote/webview/EBWebView/Default/Network/Cookies": None,
            "memunote/instance.json": None,
            "memuai/quick_explorer/undo/x/files/1": None,
            "memuai/export-tmp/a.pdf": None,
            "memuai/bin/cloudflared.exe": None,
        }
        for rel, expected in cases.items():
            self.assertEqual(core.classify(rel), expected, rel)
        self.assertIn("memunote", core.CATEGORY_IDS)
        self.assertTrue(core.normalize_settings({})["categories"]["memunote"])

    def test_scan_prunes_excluded_trees(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="r29c-scan-"))
        try:
            make_real(tmp)
            files, _ = core.SyncService(tmp).scan(all_categories=True)
            rels = {r["rel"] for r in files}
            self.assertNotIn("memunote/webview/EBWebView/Default/Network/Cookies", rels)
            self.assertFalse(any(r.startswith(("memuai/quick_explorer/", "memuai/bin/", "memuai/export-tmp/")) for r in rels))
            self.assertIn("memunote/recovery/tab1.txt", rels)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

class FolderCycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="r29c-cycle-"))
        self.a = self.tmp / "pcA"
        make_real(self.a)
        self.cloud = self.tmp / "Google Drive"
        self.cloud.mkdir()
        self.svc = core.SyncService(self.a)
        self.svc.save_settings({"target": "folder", "folder_path": str(self.cloud), "enabled": True})

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_partial_restore_of_characters_from_an_old_style_snapshot(self) -> None:
        snap = self.svc.backup("manual")

        name = next(p for p in (self.cloud / "MemuAI Backups").iterdir() if p.name.startswith("memuai-manifest-"))
        doc = json.loads(name.read_text(encoding="utf-8"))
        for rel, entry in doc["body"]["files"].items():
            if "characters" in rel:
                entry["cat"] = "modules"
        name.write_text(json.dumps(doc), encoding="utf-8")
        put(self.a, "memuai/modules/store/characters/index.json", [])
        put(self.a, "memuai/chats.json", [])
        report = self.svc.restore(snap["id"], ["modules"])
        self.assertTrue(report["ok"])
        self.assertIn("alice", (self.a / "memuai/modules/store/characters/index.json").read_text(encoding="utf-8"))
        self.assertEqual((self.a / "memuai/chats.json").read_text(encoding="utf-8"), "[]")
        put(self.a, "memuai/modules/store/characters/index.json", [])
        self.svc.restore(snap["id"], ["characters"])
        self.assertIn("alice", (self.a / "memuai/modules/store/characters/index.json").read_text(encoding="utf-8"))

    def test_quota_like_failure_mid_run_removes_this_runs_packs(self) -> None:
        self.svc.backup("manual")
        folder = self.cloud / "MemuAI Backups"
        before = {p.name for p in folder.iterdir()}
        for i in range(3):
            put(self.a, f"memuai/generated_media/new-{i}.png", os.urandom(300 * 1024))
        real_put = core.FolderTarget.put_file
        calls = {"n": 0}

        def flaky(target, name, path, progress=None):
            calls["n"] += 1
            if calls["n"] == 2:
                raise core.SyncError("Диск заполнен", "Disk full", code="quota")
            return real_put(target, name, path, progress)
        with mock.patch.object(core, "PACK_LIMIT", 256 * 1024), mock.patch.object(core.FolderTarget, "put_file", flaky):
            with self.assertRaises(core.SyncError):
                self.svc.backup("auto")
        self.assertGreaterEqual(calls["n"], 2)
        self.assertEqual({p.name for p in folder.iterdir()}, before)
        result = self.svc.backup("auto")
        self.assertFalse(result["unchanged"])
        self.assertTrue(all(s["complete"] for s in self.svc.list_snapshots()["snapshots"]))

    def test_gc_keeps_packs_of_the_new_snapshot_even_if_the_listing_lags(self) -> None:
        snap = self.svc.backup("manual")
        target = self.svc.target()
        listing = target.list(refresh=True)
        lagging = {k: v for k, v in listing.items() if not k.startswith("memuai-manifest-")}
        packs = self.svc.preview_restore(snap["id"])["header"]["packs"]
        with mock.patch.object(core.FolderTarget, "list", lambda self, refresh=False: dict(lagging)):
            self.svc._gc(target, self.svc.device()["id"], keep_packs=packs)
        self.assertTrue(self.svc.list_snapshots()["snapshots"][0]["complete"])

    def test_wrong_and_changed_passphrase_on_a_second_pc(self) -> None:
        self.svc.save_settings({"passphrase": "first secret", "encrypt": True})
        old = self.svc.backup("manual")
        self.svc.save_settings({"passphrase": "second secret"})
        put(self.a, "memuai/memory.json", {"items": ["кофе"]})
        new = self.svc.backup("manual")
        b = self.tmp / "pcB"
        b.mkdir()
        other = core.SyncService(b)
        other.save_settings({"target": "folder", "folder_path": str(self.cloud)})
        for snap, wrong in ((old, "second secret"), (new, "first secret")):
            with self.assertRaises(core.SyncError) as caught:
                other.restore(snap["id"], passphrase=wrong)
            self.assertEqual(caught.exception.code, "need_passphrase")
        self.assertFalse((b / "memuai").exists() and any((b / "memuai").glob("*.json")))
        self.assertTrue(other.restore(new["id"], passphrase="second secret")["ok"])
        self.assertIn("кофе", (b / "memuai/memory.json").read_text(encoding="utf-8"))
        self.assertTrue(other.restore(old["id"], passphrase="first secret")["ok"])
        self.assertIn("чай", (b / "memuai/memory.json").read_text(encoding="utf-8"))

class DriveFailuresTest(base.DriveTest):
    """Reuses the fake Google of round29_sync_test (OAuth, resumable uploads, 401/503)."""

    test_oauth_rejects_wrong_state = None
    test_full_cycle_with_retries = None
    test_visible_folder_and_revoked_access = None

    def test_revoked_grant_is_forgotten_and_retries_stop(self) -> None:
        self.connect()
        self.svc.backup("manual")
        self.google.revoked = True
        self.google.access = "ya29.REVOKED"
        self.svc.auth._access = ("", 0.0)
        with self.assertRaises(drive.DriveAuthError):
            self.svc.backup("auto")
        status = self.svc.status()
        self.assertFalse(status["connected"])
        self.assertEqual(status["last_error"]["code"], "invalid_grant")
        self.assertIsNone(status["next_due"])
        self.assertIsNone(self.svc.tick())

    def test_quota_exceeded_cleans_up_uploaded_packs(self) -> None:
        self.connect()
        self.svc.backup("manual")
        before = {r["name"] for r in self.google.files.values()}
        for i in range(3):
            (self.root / "memuai" / f"chat_media_{i}.bin").write_bytes(os.urandom(200 * 1024))
        original = self.google._handle
        state = {"uploads": 0}

        def full(request: httpx.Request) -> httpx.Response:
            if request.url.path.startswith("/upload/drive/v3/files") and request.method == "POST":
                state["uploads"] += 1
                if state["uploads"] >= 2:
                    return httpx.Response(403, json={"error": {"code": 403, "errors": [{"reason": "storageQuotaExceeded"}]}})
            return original(request)
        with mock.patch.object(self.google, "_handle", full), mock.patch.object(core, "PACK_LIMIT", 150 * 1024):
            with self.assertRaises(drive.DriveError) as caught:
                self.svc.backup("auto")
        self.assertEqual(caught.exception.code, "quota")
        self.assertGreaterEqual(state["uploads"], 2)
        self.assertEqual({r["name"] for r in self.google.files.values()}, before)

    def test_network_drop_mid_resumable_upload_resumes(self) -> None:
        self.connect()
        original = self.google._session_put
        dropped = {"done": False}

        def drop(request: httpx.Request, sid: str) -> httpx.Response:
            if not dropped["done"] and request.content and self.google.sessions[sid]["data"]:
                dropped["done"] = True
                raise httpx.ConnectError("network gone")
            return original(request, sid)
        with mock.patch.object(self.google, "_session_put", drop):
            result = self.svc.backup("manual")
        self.assertTrue(dropped["done"])
        wav = (self.root / "memuai" / "companion" / "voice.wav").read_bytes()
        (self.root / "memuai" / "companion" / "voice.wav").write_bytes(b"x")
        self.svc.restore(result["id"], ["companion"])
        self.assertEqual((self.root / "memuai" / "companion" / "voice.wav").read_bytes(), wav)

    def test_two_devices_share_one_account(self) -> None:
        self.connect()
        mine = self.svc.backup("manual")
        other_root = self.tmp / "other"
        other_root.mkdir()
        (other_root / "memuai").mkdir()
        (other_root / "memuai" / "memory.json").write_text('{"items": ["B"]}', encoding="utf-8")
        other = core.SyncService(other_root, transport=self.transport, open_browser=lambda url: None)

        other.save_settings({"client_id": "123-abc.apps.googleusercontent.com", "target": "drive", "keep_versions": 1})
        other.store.set("google_refresh_token", self.svc.store.get("google_refresh_token"))
        other.backup("manual")
        (other_root / "memuai" / "memory.json").write_text('{"items": ["B2"]}', encoding="utf-8")
        other.backup("manual")
        rows = self.svc.list_snapshots()["snapshots"]
        self.assertEqual(sum(1 for r in rows if r["own"]), 1)
        self.assertEqual(sum(1 for r in rows if not r["own"]), 1)
        self.assertTrue(all(r["complete"] for r in rows))
        self.assertTrue(other.restore(mine["id"], ["memory"])["ok"])
        self.assertIn("likes tea", (other_root / "memuai" / "memory.json").read_text(encoding="utf-8"))

class SafeModeDeepTest(unittest.TestCase):
    def setUp(self) -> None:
        from app import safe_mode
        self.sm = safe_mode
        self.tmp = Path(tempfile.mkdtemp(prefix="r29c-safe-"))
        (self.tmp / "memuai").mkdir()
        self.patch = mock.patch.object(safe_mode, "_memu_dir", lambda: self.tmp / "memuai")
        self.patch.start()
        safe_mode.deactivate_for_tests()

    def tearDown(self) -> None:
        self.sm.deactivate_for_tests()
        self.patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_headless_runs_do_not_count_as_failed_starts(self) -> None:
        asked = []
        for _ in range(3):
            self.sm.decide_at_startup(False, interactive=False, ask=lambda *a: asked.append(a) or False, shift=lambda: False)
        self.assertEqual(int(self.sm._guard().get("pending") or 0), 0)
        self.sm.decide_at_startup(False, interactive=True, ask=lambda *a: asked.append(a) or False, shift=lambda: False)
        self.assertEqual(asked, [])
        self.assertFalse(self.sm.is_safe_mode())

    def test_two_unfinished_window_starts_offer_safe_mode_and_data_is_untouched(self) -> None:
        prefs = self.tmp / "memuai" / "preferences.json"
        prefs.write_text('{"language": "en", "ui_design": "glass"}', encoding="utf-8")
        before = prefs.read_bytes()
        asked = []
        for _ in range(2):
            self.sm.decide_at_startup(False, interactive=True, ask=lambda *a: asked.append(a) or False, shift=lambda: False)
        self.sm.decide_at_startup(False, interactive=True, ask=lambda *a: asked.append(a) or True, shift=lambda: False)
        self.assertEqual(len(asked), 1)
        self.assertIn("did not start correctly", asked[0][1])
        self.assertTrue(self.sm.is_safe_mode())
        self.assertEqual(self.sm.status()["source"], "crash")
        self.assertEqual(prefs.read_bytes(), before)
        self.sm.clean_exit()
        self.assertFalse((self.tmp / "memuai" / "safe_mode_session.json").exists())

    def test_proxy_scheduler_pauses_while_memuai_runs_in_safe_mode(self) -> None:
        svc = core.SyncService(self.tmp)
        with mock.patch.object(core, "_SERVICE", svc), mock.patch.object(svc, "start_scheduler"):
            self.assertIs(core.start_proxy_scheduler(), svc)
        self.assertEqual(svc._paused(), "")
        (self.tmp / "memuai" / "safe_mode_session.json").write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
        self.assertEqual(svc._paused(), "safe_mode")
        self.assertIsNone(svc.tick())

    def test_banner_follows_language_and_avoids_the_composer(self) -> None:
        js = (ROOT / "app" / "pgstatic" / "safe-mode.js").read_text(encoding="utf-8")
        self.assertIn('attributeFilter: ["lang"]', js)
        self.assertIn('document.querySelector(".composer")', js)
        css = (ROOT / "app" / "pgstatic" / "safe-mode.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"-webkit-line-clamp: [23];")
        index = (ROOT / "app" / "pgstatic" / "index.html").read_text(encoding="utf-8")
        self.assertIn("/safe-mode.js?v=1.1-r29", index)

class SyncUiContractTest(unittest.TestCase):
    def test_lost_access_hero_offers_reconnect(self) -> None:
        js = (ROOT / "app" / "pgstatic" / "sync-settings.js").read_text(encoding="utf-8")
        self.assertIn('["invalid_grant", "scope"].includes(err.code)', js)
        self.assertIn("Google Drive needs to be connected again", js)

if __name__ == "__main__":
    unittest.main()
