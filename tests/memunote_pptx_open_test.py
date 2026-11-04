"""MemuNote: opening a just-created .pptx that is empty / damaged.

A 0-byte .pptx (Windows Explorer «New → PowerPoint Presentation» creates
one when the Office template is missing) used to sail through ``/api/open``
with 200 and then die in the slides viewer with the raw zip.js error
«not a ZIP file». ``open_document`` now validates ZIP-based Office files
before the tab is created, so the user gets one plain-language message
instead. The same check covers .docx / .xlsx / .odt / .epub, which used
to leak the raw ``BadZipFile`` text.
"""
from __future__ import annotations

import http.client
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def _zip_bytes(parts: dict) -> bytes:
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in parts.items():
            zf.writestr(name, text)
    return buf.getvalue()


class PptxOpenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.data = Path(cls.tmp.name) / "data"
        cls.files = Path(cls.tmp.name) / "files"
        cls.files.mkdir()
        cls.env = mock.patch.dict(os.environ, {"MEMUAI_DATA_DIR": str(cls.data)})
        cls.env.start()
        from app.memunote import server
        from app.memufiles.extras import _docx, _pptx, _xlsx
        cls.server = server
        # Valid packages.
        (cls.files / "template.pptx").write_bytes(_pptx())
        (cls.files / "template.docx").write_bytes(_docx())
        (cls.files / "template.xlsx").write_bytes(_xlsx())
        # Minimal valid .odp (ZIP with the OpenDocument main part).
        (cls.files / "minimal.odp").write_bytes(_zip_bytes({
            "mimetype": "application/vnd.oasis.opendocument.presentation",
            "content.xml": '<?xml version="1.0"?><office:document-content/>',
            "META-INF/manifest.xml": '<?xml version="1.0"?><manifest:manifest/>',
        }))
        # Damaged / not-a-package files.
        (cls.files / "empty.pptx").write_bytes(b"")
        (cls.files / "empty.ppsx").write_bytes(b"")
        (cls.files / "empty.pptm").write_bytes(b"")
        (cls.files / "empty.docx").write_bytes(b"")
        (cls.files / "empty.xlsx").write_bytes(b"")
        (cls.files / "empty.odt").write_bytes(b"")
        (cls.files / "empty.epub").write_bytes(b"")
        (cls.files / "trunc.pptx").write_bytes((cls.files / "template.pptx").read_bytes()[:2000])
        (cls.files / "renamed.pptx").write_bytes(b"hello, this is plain text, not a zip\n")
        # A valid ZIP, but a Word document, not a presentation.
        (cls.files / "word-as-slides.pptx").write_bytes(_docx())
        cls.app = server.NoteApp([])
        cls.httpd, _ = server.start(cls.app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.env.stop()
        cls.tmp.cleanup()

    def open_api(self, name: str):
        conn = http.client.HTTPConnection("127.0.0.1", self.app.port, timeout=10)
        conn.request("POST", "/api/open", body=json.dumps({"path": str(self.files / name)}).encode(),
                     headers={"Content-Type": "application/json", "X-MN-Token": self.app.token,
                              "Host": f"127.0.0.1:{self.app.port}"})
        response = conn.getresponse()
        payload = response.read()
        conn.close()
        return response.status, json.loads(payload or b"{}")

    def assert_human_error(self, name: str, *words: str) -> None:
        status, body = self.open_api(name)
        self.assertEqual(status, 400, body)
        error = str(body.get("error", ""))
        self.assertNotIn("ZIP", error)
        self.assertNotIn("zip", error)
        self.assertNotIn("BadZip", error)
        for word in words:
            self.assertIn(word, error)

    def test_valid_pptx_template_opens(self) -> None:
        status, body = self.open_api("template.pptx")
        self.assertEqual((status, body.get("kind")), (200, "slides"))

    def test_valid_docx_xlsx_templates_open(self) -> None:
        status, body = self.open_api("template.docx")
        self.assertEqual((status, body.get("kind")), (200, "docx"))
        status, body = self.open_api("template.xlsx")
        self.assertEqual((status, body.get("kind")), (200, "sheet"))

    def test_valid_odp_opens(self) -> None:
        status, body = self.open_api("minimal.odp")
        self.assertEqual((status, body.get("kind")), (200, "slides"))

    def test_empty_pptx_gives_human_error(self) -> None:
        self.assert_human_error("empty.pptx", "пустой или повреждён", "презентаци")

    def test_truncated_pptx_gives_human_error(self) -> None:
        self.assert_human_error("trunc.pptx", "пустой или повреждён", "презентаци")

    def test_renamed_text_file_gives_human_error(self) -> None:
        self.assert_human_error("renamed.pptx", "пустой или повреждён", "презентаци")

    def test_zip_without_presentation_part_gives_human_error(self) -> None:
        self.assert_human_error("word-as-slides.pptx", "пустой или повреждён", "презентаци")

    def test_empty_ppsx_and_pptm_give_human_error(self) -> None:
        self.assert_human_error("empty.ppsx", "пустой или повреждён", "презентаци")
        self.assert_human_error("empty.pptm", "пустой или повреждён", "презентаци")

    def test_empty_docx_xlsx_odt_epub_give_human_errors(self) -> None:
        self.assert_human_error("empty.docx", "пустой или повреждён", "документ")
        self.assert_human_error("empty.xlsx", "пустой или повреждён", "таблица")
        self.assert_human_error("empty.odt", "пустой или повреждён", "документ")
        self.assert_human_error("empty.epub", "пустой или повреждён", "книга")

    def test_english_message_has_no_technical_terms(self) -> None:
        text = self.server._damaged_package_message("en", "slides")
        self.assertIn("empty or damaged", text)
        self.assertIn("presentation", text)
        self.assertNotIn("ZIP", text)

    def test_open_document_raises_value_error_directly(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            self.app.open_document(str(self.files / "empty.pptx"))
        self.assertIn("презентаци", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
