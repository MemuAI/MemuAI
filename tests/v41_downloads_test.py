"""Local export destinations, collisions and failure cleanup, without real Downloads."""
import base64
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import media_routing, pgdata, playground_api


class ExportFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.downloads = self.root / "redirected-downloads"
        self.downloads.mkdir()
        for item in [patch.object(pgdata, "MEMU_DIR", self.root / "data"),
                     patch("app.quick_apps.downloads_folder", return_value=str(self.downloads))]:
            item.start()
            self.addCleanup(item.stop)


class MediaExports(ExportFixture):
    def setUp(self):
        super().setUp()
        self.cache = self.root / "media"
        self.cache.mkdir()
        self.name = "a" * 64 + ".webm"
        self.source = self.cache / self.name
        self.source.write_bytes(b"\x1aE\xdf\xa3" + b"synthetic" * 100)
        item = patch.object(media_routing, "media_folder", return_value=self.cache)
        item.start()
        self.addCleanup(item.stop)
        app = FastAPI()
        media_routing.register(app, stream_lines=None)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def save(self):
        return self.client.post("/api/pg/media/save", json={
            "url": "/api/pg/media/file/" + self.name, "name": "clip"})

    def test_saved_media_uses_the_redirected_downloads_folder(self):
        response = self.save()
        self.assertEqual(response.status_code, 200, response.text)
        target = Path(response.json()["path"])
        self.assertEqual(target.parent, self.downloads / "MemuAI")
        self.assertEqual(target.read_bytes(), self.source.read_bytes())

    def test_concurrent_exports_never_overwrite_one_another(self):
        original = shutil.copyfile
        original_open = Path.open
        barrier = threading.Barrier(2)
        def delayed(*args, **kwargs):
            barrier.wait(timeout=5)
            return original(*args, **kwargs)
        def concurrent_open(path, mode="r", *args, **kwargs):
            if mode == "xb" and path.name == "clip.webm":
                barrier.wait(timeout=5)
            return original_open(path, mode, *args, **kwargs)
        with patch.object(shutil, "copyfile", side_effect=delayed), \
                patch.object(Path, "open", concurrent_open), ThreadPoolExecutor(2) as pool:
            responses = list(pool.map(lambda _: self.save(), range(2)))
        self.assertEqual([r.status_code for r in responses], [200, 200])
        targets = [Path(r.json()["path"]) for r in responses]
        self.assertNotEqual(*targets)
        for target in targets:
            self.assertEqual(target.read_bytes(), self.source.read_bytes())

    def test_copy_failure_removes_the_partial_destination(self):
        with patch.object(shutil, "copyfileobj", side_effect=OSError("disk full")):
            response = self.save()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(list((self.downloads / "MemuAI").iterdir()), [])
        self.assertTrue(self.source.is_file())

    def test_stale_profile_and_partial_media_url_cannot_be_exported(self):
        with patch.object(media_routing, "_active_profile", return_value="active"):
            stale = self.client.post("/api/pg/media/save", json={"url": "/api/pg/media/file/" + self.name + "?profile_id=old"})
            self.assertEqual(stale.status_code, 403)
            partial = self.client.post("/api/pg/media/save", json={"url": "/prefix/api/pg/media/file/" + self.name + "suffix"})
            self.assertEqual(partial.status_code, 404)
        self.assertFalse((self.downloads / "MemuAI").exists())


class FileExports(ExportFixture):
    def setUp(self):
        super().setUp()
        self.client = TestClient(playground_api.create_app(), base_url="http://127.0.0.1")
        self.addCleanup(self.client.close)

    def test_plain_export_uses_redirected_downloads_and_preserves_an_existing_file(self):
        target = self.downloads / "MemuAI" / "report.txt"
        target.parent.mkdir()
        target.write_bytes(b"original")
        response = self.client.post("/api/pg/download-file", json={"name": "report.txt", "text": "new"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(target.read_bytes(), b"original")
        self.assertEqual(Path(response.json()["path"]), target.with_name("report (2).txt"))

    def test_invalid_base64_is_rejected_without_creating_a_corrupt_file(self):
        response = self.client.post("/api/pg/download-file", json={"name": "bad.bin", "url": "data:application/octet-stream;base64,%%%%"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse((self.downloads / "MemuAI" / "bad.bin").exists())

    def test_percent_encoded_base64_is_decoded_before_saving(self):
        response = self.client.post("/api/pg/download-file", json={"name": "data.bin", "url": "data:application/octet-stream;base64,%2Fw%3D%3D"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(Path(response.json()["path"]).read_bytes(), base64.b64decode("/w=="))


if __name__ == "__main__":
    unittest.main()
