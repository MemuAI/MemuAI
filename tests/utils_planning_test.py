"""MemuUtils planning actions: drive space, the results folder (report, checksums), the settings backup and the AI access report."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import module_actions as ma  # noqa: E402
from app import utils_kit  # noqa: E402
from app.module_integration import normalize_contributes, validate_contributes  # noqa: E402

GB = 1024 ** 3


def run(coro):
    return asyncio.run(coro)


class UtilsPlanningTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root, self.out, self.results = base / "modules", base / "out", base / "Downloads" / "MemuAI" / "MemuUtils"
        self.results.mkdir(parents=True)
        self.env = mock.patch.dict(os.environ, {ma.OUTPUT_ROOT_ENV: str(self.out), "MEMUAI_DATA_DIR": str(base / "data")})
        self.env.start()
        self.folder = mock.patch.object(utils_kit, "save_folder", lambda: self.results)
        self.folder.start()

    def tearDown(self) -> None:
        from app.learn.store import reset_stores
        from app.music.library import _LIBS, data_root
        reset_stores()
        library = _LIBS.pop(str(data_root()), None)
        if library:
            library.close()
        self.folder.stop()
        self.env.stop()
        self.tmp.cleanup()

    def act(self, name: str, *, dry: bool = False, lang: str = "ru", **settings):
        return run(ma.run_module_action("utils", name, root=self.root, dry=dry, lang=lang, settings=settings))

    def put(self, rel: str, data: bytes, age_days: float = 0) -> Path:
        path = self.results / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if age_days:
            moment = time.time() - age_days * 86400
            os.utime(path, (moment, moment))
        return path

    def written(self) -> list:
        return sorted(p.relative_to(self.out).as_posix() for p in self.out.rglob("*") if p.is_file()) if self.out.exists() else []

    # -- manifest -------------------------------------------------------------------------------------------------------
    def test_manifest_declares_off_by_default_actions_with_real_handlers(self) -> None:
        data = json.loads((ROOT / "app" / "module_builtins" / "utils" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(validate_contributes(data["contributes"], builtin=True), [])
        actions = normalize_contributes(data["contributes"], builtin=True)["planning"]
        self.assertTrue(5 <= len(actions) <= 12)
        handlers = {a["action"]["handler"] for a in actions}
        self.assertEqual(handlers, {"utils.disk_report", "utils.results_report", "utils.results_checksums",
                                    "utils.settings_backup", "utils.ai_access_report"})
        for action in actions:
            self.assertIs(action["enabled"], False)
            self.assertTrue(ma.known(action["action"]["handler"], "utils"), action["id"])
        grew = next(a for a in actions if a["id"] == "results-grew")
        self.assertEqual(grew["trigger"], {"kind": "threshold", "metric": "bytes", "threshold": GB, "scope": "any"})
        self.assertEqual(ma.resolve_action("utils", "results-checksums"), ("utils.results_checksums", {"keep": 10, "max_mb": 2048}))
        for name in handlers:
            meta = ma.HANDLERS[name]
            self.assertFalse(meta["ai"], name)
            self.assertIn(meta["writes"], ("none", "files"), name)

    def test_dry_runs_write_nothing(self) -> None:
        self.put("a.txt", b"hello")
        with mock.patch.object(ma, "utils_disks", lambda: [{"mount": "/", "fs": "ext4", "total": 100 * GB, "used": 50 * GB,
                                                             "free": 50 * GB, "free_pct": 50.0}]):
            for name in ("utils.disk_report", "utils.results_report", "utils.results_checksums", "utils.settings_backup",
                         "utils.ai_access_report"):
                result = self.act(name, dry=True)
                self.assertTrue(result["ok"], (name, result["text"]))
                self.assertEqual(result["files"], [], name)
        self.assertEqual(self.written(), [])
        self.assertFalse((Path(os.environ["MEMUAI_DATA_DIR"]) / "utils" / "settings.json").exists())

    # -- drives ---------------------------------------------------------------------------------------------------------
    def test_disk_report_warns_below_either_limit(self) -> None:
        disks = [{"mount": "/", "fs": "ext4", "total": 100 * GB, "used": 97 * GB, "free": 3 * GB, "free_pct": 3.0},
                 {"mount": "/data", "fs": "xfs", "total": 2000 * GB, "used": 1900 * GB, "free": 100 * GB, "free_pct": 5.0},
                 {"mount": "/home", "fs": "ext4", "total": 500 * GB, "used": 100 * GB, "free": 400 * GB, "free_pct": 80.0}]
        with mock.patch.object(ma, "utils_disks", lambda: disks):
            result = self.act("utils.disk_report")
            self.assertTrue(result["ok"])
            self.assertEqual(result["data"]["low"], 2)
            self.assertIn("⚠ /:", result["text"])
            self.assertIn("⚠ /data:", result["text"])
            self.assertIn("• /home:", result["text"])
            self.assertIn("Большие файлы", result["text"])
            only_gb = self.act("utils.disk_report", min_free_pct=0, min_free_gb=5)
            self.assertEqual(only_gb["data"]["low"], 1)
            quiet = self.act("utils.disk_report", min_free_pct=0, min_free_gb=0, lang="en")
            self.assertEqual(quiet["data"]["low"], 0)
            self.assertIn("Enough space on every drive (3)", quiet["text"])
        self.assertEqual(self.written(), [])

    def test_russian_counts(self) -> None:
        self.assertEqual([ma._ru_n(n, "файл", "файла", "файлов") for n in (1, 2, 5, 11, 21, 104, 112)],
                         ["1 файл", "2 файла", "5 файлов", "11 файлов", "21 файл", "104 файла", "112 файлов"])

    def test_real_drives_are_read(self) -> None:
        disks = ma.utils_disks()
        for disk in disks:
            self.assertGreater(disk["total"], 0)
            self.assertLessEqual(disk["free"], disk["total"])
            self.assertTrue(0 <= disk["free_pct"] <= 100)
        self.assertEqual(len({(d["mount"]) for d in disks}), len(disks))

    # -- results folder -------------------------------------------------------------------------------------------------
    def test_results_report_and_counters(self) -> None:
        empty = self.act("utils.results_report")
        self.assertTrue(empty["ok"])
        self.assertIn("пусто", empty["text"])
        self.put("photo.png", b"p" * 3000, age_days=45)
        self.put("docs/report.pdf", b"d" * 2000)
        self.put("notes.txt", b"n" * 10)
        self.put(".hidden", b"x" * 99999)
        result = self.act("utils.results_report", older_days=30)
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["items"], 3)
        self.assertEqual(result["data"]["bytes"], 5010)
        self.assertEqual(result["data"]["old"], 1)
        self.assertIn("photo.png", result["text"])
        self.assertIn("docs/report.pdf", result["text"])
        self.assertIn("Старше 30 дн.: 1 файл, 2,9 КБ", result["text"])
        self.assertEqual(self.written(), [])
        stats = ma.counters(self.root)
        self.assertEqual(stats["utils"], {"bytes": 5010, "items": 3})

    def test_checksums_compare_with_the_previous_list(self) -> None:
        first = self.put("a.txt", b"alpha")
        self.put("sub/b.bin", b"\x00" * 4096)
        gone = self.put("c.txt", b"gamma")
        one = self.act("utils.results_checksums")
        self.assertTrue(one["ok"], one["text"])
        self.assertEqual(len(one["files"]), 1)
        sums = Path(one["files"][0])
        self.assertEqual(sums.parent, self.out / "MemuUtils" / "Checksums")
        lines = sums.read_text(encoding="utf-8").splitlines()
        self.assertIn(f"{hashlib.sha256(b'alpha').hexdigest()}  a.txt", lines)
        self.assertIn(f"{hashlib.sha256(bytes(4096)).hexdigest()}  sub/b.bin", lines)
        self.assertIn("Первый список", one["text"])
        first.write_bytes(b"alpha-2")
        gone.unlink()
        self.put("new.txt", b"new")
        time.sleep(0.02)
        two = self.act("utils.results_checksums")
        self.assertEqual((two["data"]["changed"], two["data"]["missing"], two["data"]["added"]), (1, 1, 1))
        self.assertIn("~ a.txt", two["text"])
        self.assertIn("− c.txt", two["text"])
        self.assertIn("+ new.txt", two["text"])
        time.sleep(0.02)
        three = self.act("utils.results_checksums", keep=2)
        self.assertEqual((three["data"]["changed"], three["data"]["missing"], three["data"]["added"]), (0, 0, 0))
        self.assertEqual(len(list((self.out / "MemuUtils" / "Checksums").glob("SHA256SUMS-*.txt"))), 2)
        self.assertEqual(self.results.joinpath("a.txt").read_bytes(), b"alpha-2", "the results are only read")

    def test_checksums_skip_files_over_the_limit(self) -> None:
        self.put("small.txt", b"s")
        self.put("big.bin", b"b" * (2 * 1024 * 1024))
        result = self.act("utils.results_checksums", max_mb=1)
        self.assertEqual(result["data"]["files"], 1)
        self.assertEqual(result["data"]["skipped"], 1)
        self.assertNotIn("big.bin", Path(result["files"][0]).read_text(encoding="utf-8"))

    # -- settings -------------------------------------------------------------------------------------------------------
    def test_settings_backup_only_when_changed(self) -> None:
        some = [u.id for u in utils_kit.REGISTRY.values()][:3]
        utils_kit.save_settings({"favorites": some, "model": "test-model"})
        one = self.act("utils.settings_backup")
        self.assertTrue(one["ok"], one["text"])
        self.assertEqual(len(one["files"]), 1)
        doc = json.loads(Path(one["files"][0]).read_text(encoding="utf-8"))
        self.assertEqual(doc["settings"]["favorites"], some)
        self.assertEqual(doc["settings"]["model"], "test-model")
        self.assertEqual(doc["ai_tools_effective"], utils_kit.ai_tools())
        self.assertIn("app_version", doc)
        same = self.act("utils.settings_backup")
        self.assertEqual(same["files"], [])
        self.assertIn("не менялись", same["text"])
        utils_kit.save_settings({"ai_enabled": False})
        time.sleep(0.02)
        two = self.act("utils.settings_backup")
        self.assertEqual(len(two["files"]), 1)
        self.assertEqual(len(list((self.out / "MemuUtils" / "Backups").glob("memuutils-settings-*.json"))), 2)

    # -- AI access ------------------------------------------------------------------------------------------------------
    def test_ai_access_report(self) -> None:
        result = self.act("utils.ai_access_report")
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["tools"], 0)  # nothing is allowed out of the box
        self.assertEqual(utils_kit.ai_tools(), [])
        self.assertIn("ни одна", result["text"])
        self.assertIn("Рекомендуемые", result["text"])
        utils_kit.save_settings({"ai_tools": utils_kit.recommended_tools()})
        starter = self.act("utils.ai_access_report")
        self.assertEqual(starter["data"]["tools"], len(utils_kit.recommended_tools()))
        self.assertGreater(starter["data"]["tokens"], 100)
        self.assertIn("свой выбор", starter["text"])
        self.assertIn("только к запросам", starter["text"])
        chosen = [u.id for u in utils_kit.REGISTRY.values() if u.ai and utils_kit.platform_ok(u)][:2]
        utils_kit.save_settings({"ai_tools": chosen})
        custom = self.act("utils.ai_access_report", lang="en")
        self.assertEqual(custom["data"]["tools"], 2)
        self.assertIn("custom choice", custom["text"])
        utils_kit.save_settings({"ai_enabled": False})
        off = self.act("utils.ai_access_report")
        self.assertIs(off["data"]["enabled"], False)
        self.assertIn("выключен", off["text"])
        self.assertEqual(self.written(), [])


if __name__ == "__main__":
    unittest.main()
