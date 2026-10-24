"""Download verification and imported-model ownership regressions."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import local_models as lm
from tests.local_models_test import gguf_bytes


class Response:
    def __init__(self, status, headers=None, chunks=(), before_chunk=None, after_body=None):
        self.status_code = status
        self.headers = headers or {}
        self.chunks = chunks
        self.before_chunk = before_chunk
        self.after_body = after_body
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def iter_bytes(self, size):
        for chunk in self.chunks:
            if self.before_chunk:
                self.before_chunk()
            yield chunk
        if self.after_body:
            self.after_body()


class Client:
    def __init__(self, response):
        self.response = response
        self.headers = None

    def stream(self, *args, headers=None):
        self.headers = headers
        return self.response


class DownloadVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="memuai-download-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.target = Path(self.temp.name) / "model.gguf"
        self.part = self.target.with_name(self.target.name + ".part")
        self.original = gguf_bytes({"general.name":"original"})
        self.downloaded = gguf_bytes({"general.name":"downloaded"})
        self.target.write_bytes(self.original)
        self.job = lm.Download("owner/model", ["model.gguf"])

    def fetch(self, response, expected_size=0):
        return lm._fetch(Client(response), "https://example.invalid/model.gguf", self.target,
                         self.job, {}, expected_size=expected_size)

    def test_416_oversized_partial_preserves_previous_model(self):
        partial = self.downloaded + b"extra"
        self.part.write_bytes(partial)
        response = Response(416, {"content-range":f"bytes */{len(self.downloaded)}"})
        with self.assertRaises(lm.LocalModelError):
            self.fetch(response, len(self.downloaded))
        self.assertEqual(self.target.read_bytes(), self.original)
        # r26: an oversized (provably corrupt) part is removed so a retry can start clean
        self.assertFalse(self.part.exists())
        self.assertTrue(response.closed)

    def test_416_missing_total_preserves_partial_and_model(self):
        self.part.write_bytes(self.downloaded)
        with self.assertRaises(lm.LocalModelError):
            self.fetch(Response(416))
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(self.part.read_bytes(), self.downloaded)

    def test_416_verified_complete_partial_is_promoted(self):
        self.part.write_bytes(self.downloaded)
        self.fetch(Response(416, {"content-range":f"bytes */{len(self.downloaded)}"}), len(self.downloaded))
        self.assertEqual(self.target.read_bytes(), self.downloaded)
        self.assertFalse(self.part.exists())

    def test_416_equal_size_invalid_gguf_preserves_model_and_partial(self):
        invalid = b"NOT-GGUF"
        self.part.write_bytes(invalid)
        response = Response(416, {"content-range":f"bytes */{len(invalid)}"})
        with self.assertRaises(lm.LocalModelError):
            self.fetch(response, len(invalid))
        self.assertEqual(self.target.read_bytes(), self.original)
        # r26: a complete-size but invalid GGUF part is removed (a retry would hit it again)
        self.assertFalse(self.part.exists())
        self.assertTrue(response.closed)

    def test_416_cancel_before_promotion_preserves_both_files(self):
        self.part.write_bytes(self.downloaded)
        self.job.cancel.set()
        with self.assertRaises(InterruptedError):
            self.fetch(Response(416, {"content-range":f"bytes */{len(self.downloaded)}"}))
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(self.part.read_bytes(), self.downloaded)

    def test_206_wrong_resume_offset_preserves_both_files(self):
        partial = self.downloaded[:30]
        self.part.write_bytes(partial)
        response = Response(206, {"content-range":f"bytes 0-{len(self.downloaded)-1}/{len(self.downloaded)}"},
                            [self.downloaded])
        with self.assertRaises(lm.LocalModelError):
            self.fetch(response)
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(self.part.read_bytes(), partial)
        self.assertTrue(response.closed)

    def test_206_verified_resume_promotes_complete_model(self):
        self.part.write_bytes(self.downloaded[:30])
        rest = self.downloaded[30:]
        response = Response(206, {"content-range":f"bytes 30-{len(self.downloaded)-1}/{len(self.downloaded)}",
                                 "content-length":str(len(rest))}, [rest])
        client = Client(response)
        lm._fetch(client, "https://example.invalid/model.gguf", self.target, self.job, {}, len(self.downloaded))
        self.assertEqual(client.headers["Range"], "bytes=30-")
        self.assertEqual(self.target.read_bytes(), self.downloaded)
        self.assertTrue(response.closed)

    def test_206_short_body_stays_partial_and_preserves_model(self):
        self.part.write_bytes(self.downloaded[:30])
        response = Response(206, {"content-range":f"bytes 30-{len(self.downloaded)-1}/{len(self.downloaded)}"},
                            [self.downloaded[30:40]])
        with self.assertRaises(lm.LocalModelError):
            self.fetch(response)
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(self.part.read_bytes(), self.downloaded[:40])
        self.assertTrue(response.closed)

    def test_206_partial_range_cannot_publish_incomplete_model(self):
        self.part.write_bytes(self.downloaded[:30])
        response = Response(206, {"content-range":f"bytes 30-39/{len(self.downloaded)}"},
                            [self.downloaded[30:40]])
        with self.assertRaises(lm.LocalModelError):
            self.fetch(response)
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(self.part.read_bytes(), self.downloaded[:40])

    def test_200_ignoring_range_restarts_and_promotes_verified_model(self):
        self.part.write_bytes(b"old partial")
        self.job.done = len(b"old partial")
        response = Response(200, {"content-length":str(len(self.downloaded))}, [self.downloaded])
        self.fetch(response, len(self.downloaded))
        self.assertEqual(self.target.read_bytes(), self.downloaded)
        self.assertEqual(self.job.done, len(self.downloaded))

    def test_200_short_chunked_body_does_not_publish_when_head_has_size(self):
        response = Response(200, chunks=[self.downloaded[:30]])
        with self.assertRaises(lm.LocalModelError):
            self.fetch(response, len(self.downloaded))
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(self.part.read_bytes(), self.downloaded[:30])

    def test_complete_non_gguf_payload_does_not_destroy_previous_model(self):
        response = Response(200, {"content-length":"8"}, [b"NOT-GGUF"])
        with self.assertRaises(lm.LocalModelError):
            self.fetch(response)
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertFalse(self.part.exists())  # r26: a corrupt complete payload is removed

    def test_cancel_during_transfer_closes_response_and_keeps_partial(self):
        self.part.write_bytes(self.downloaded[:30])
        response = Response(206, {"content-range":f"bytes 30-{len(self.downloaded)-1}/{len(self.downloaded)}"},
                            [self.downloaded[30:]], before_chunk=self.job.cancel.set)
        with self.assertRaises(InterruptedError):
            self.fetch(response)
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(self.part.read_bytes(), self.downloaded[:30])
        self.assertTrue(response.closed)

    def test_cancel_after_body_before_promotion_preserves_model_and_complete_partial(self):
        response = Response(200, {"content-length":str(len(self.downloaded))},
                            [self.downloaded], after_body=self.job.cancel.set)
        with self.assertRaises(InterruptedError):
            self.fetch(response)
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(self.part.read_bytes(), self.downloaded)
        self.assertTrue(response.closed)


class ImportOwnershipTests(unittest.TestCase):
    def test_copying_models_with_same_basename_preserves_existing_model(self):
        with tempfile.TemporaryDirectory(prefix="memuai-import-fixture-") as temp:
            root = Path(temp)
            models = {}
            files = []
            for folder in ("first", "second"):
                path = root / folder / "model.gguf"
                path.parent.mkdir()
                path.write_bytes(gguf_bytes({"general.name":folder}))
                files.append(path)
            with patch.object(lm, "models_dir", return_value=root / "managed"), \
                 patch.object(lm, "_mutate", side_effect=lambda change: change({"models":models})), \
                 patch.object(lm, "sync_catalog"), patch.object(lm, "get_model", return_value=None):
                first = lm.register_file(files[0], copy=True)
                first_path = Path(models[first["id"]]["path"])
                second = lm.register_file(files[1], copy=True)
            self.assertEqual(first_path.read_bytes(), files[0].read_bytes())
            self.assertNotEqual(first["id"], second["id"])
            self.assertEqual(Path(models[second["id"]]["path"]).read_bytes(), files[1].read_bytes())

    def test_failed_import_copy_keeps_existing_managed_file(self):
        with tempfile.TemporaryDirectory(prefix="memuai-import-fixture-") as temp:
            root = Path(temp)
            source = root / "model.gguf"
            source.write_bytes(gguf_bytes({"general.name":"new"}))
            previous = root / "managed" / "imported" / "model.gguf"
            previous.parent.mkdir(parents=True)
            previous.write_bytes(gguf_bytes({"general.name":"old"}))
            original = previous.read_bytes()
            with patch.object(lm, "models_dir", return_value=root / "managed"), \
                 patch.object(lm.shutil, "copyfileobj", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    lm.register_file(source, copy=True)
            self.assertEqual(previous.read_bytes(), original)
            self.assertEqual(list(previous.parent.glob("*.gguf")), [previous])


class DownloadIdentityRegressionTest(unittest.TestCase):
    def test_repository_names_cannot_share_download_directory(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(lm, "models_dir", return_value=Path(temp)):
            first = lm._repo_folder("a/b__c")
            second = lm._repo_folder("a__b/c")
            self.assertNotEqual(first, second)
            first.mkdir()
            second.mkdir()
            (first / "model.gguf").write_bytes(b"first")
            (second / "model.gguf").write_bytes(b"second")
            self.assertEqual((first / "model.gguf").read_bytes(), b"first")

    def test_duplicate_selected_basenames_are_rejected_before_background_download(self):
        with patch.object(lm.threading, "Thread") as worker:
            for files, projector, draft in ((["a/model.gguf", "b/model.gguf"], "", ""),
                                             (["model.gguf"], "assets/model.gguf", ""),
                                             (["model.gguf"], "", "draft/model.gguf")):
                with self.subTest(files=files, projector=projector, draft=draft):
                    with self.assertRaises(lm.LocalModelError):
                        lm.start_download("owner/repo", files, projector, draft)
            worker.assert_not_called()


if __name__ == "__main__":
    unittest.main()
