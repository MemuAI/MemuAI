"""Round 29 (r29-schedsend-export): the chat export (PDF / Word / HTML / Markdown / JSON / text).

* DOCX writer: headings, real tables, numbered / bullet lists, hyperlinks, coloured code, images and
  KaTeX MathML → native Office Math; the package is a valid zip with the Word parts;
* safe file names (Windows device names, forbidden characters), preferences normalization of
  chat_export_options, finalize_html inserts the KaTeX stylesheet;
* routes /api/export/{capabilities,render,open} through the real MemuAI app (Downloads mocked);
  PDF without Edge answers 501 with the print fallback; /open refuses files it did not save;
* export.js does not hydrate interactive tables into the file; wiring and English strings;
* MemuAI.spec / build.py ship app.chat_export, app.chat_export_api, app.chat_export_pdf.
"""
from __future__ import annotations

import base64
import io
import os
import re
import shutil
import struct
import subprocess
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path
from unittest import mock

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-r29-export-")

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "pgstatic"
CYR = re.compile(r"[А-Яа-яЁё]")


def tiny_png() -> str:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"\x00\xff\x00\x00"
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) \
        + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(png).decode()


SAMPLE = f"""
<h1>Report</h1>
<p>Plain <b>bold</b> <i>italic</i> <a href="https://example.com">link</a> and <code>inline</code>.</p>
<ol><li>one</li><li>two<ul><li>nested</li></ul></li></ol>
<div class="table-wrap"><table><thead><tr><th>A</th><th align="right">B</th></tr></thead>
<tbody><tr><td>1</td><td>2</td></tr></tbody></table></div>
<pre><code class="language-python"><span class="tok-kw">def</span> f(): <span class="tok-num">1</span></code></pre>
<p><span class="katex"><span class="katex-mathml"><math><semantics><mrow><mfrac><mi>a</mi><mi>b</mi></mfrac>
<msup><mi>x</mi><mn>2</mn></msup></mrow><annotation encoding="application/x-tex">\\frac{{a}}{{b}} x^2</annotation></semantics></math></span></span></p>
<p><img src="{tiny_png()}" alt="dot"></p>
"""


class DocxTest(unittest.TestCase):
    def test_docx_parts(self) -> None:
        from app import chat_export as ce
        data = ce.html_to_docx(SAMPLE, {"theme": "light", "page": "a4", "header": "Confidential", "numbers": True,
                                        "lang": "en"})
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
            self.assertIn("word/document.xml", names)
            self.assertIn("[Content_Types].xml", names)
            self.assertTrue(any(name.startswith("word/media/") for name in names), names)
            document = archive.read("word/document.xml").decode("utf-8")
        self.assertIn("<w:tbl>", document)
        self.assertIn("m:oMath", document)
        self.assertIn("m:f", document)            # the fraction stayed a native equation
        self.assertIn("w:hyperlink", document)
        self.assertIn("w:numPr", document)
        self.assertIn("Report", document)

    def test_dark_theme_and_landscape(self) -> None:
        from app import chat_export as ce
        data = ce.html_to_docx("<p>x</p>", {"theme": "dark", "orientation": "landscape", "page": "letter"})
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            document = archive.read("word/document.xml").decode("utf-8")
        self.assertIn('w:orient="landscape"', document)

    def test_helpers(self) -> None:
        from app import chat_export as ce
        self.assertEqual(ce.safe_filename("con", "pdf").lower().startswith("con.") , False)
        name = ce.safe_filename('a<b>:c?"d|e*', "docx")
        self.assertTrue(name.endswith(".docx"))
        self.assertFalse(set('<>:"|?*') & set(name))
        out = dict(ce.PREF_DEFAULTS)
        ce.normalize_prefs(out, {"chat_export_options": {"format": "docx", "theme": "neon", "size": 99,
                                                          "toc": True, "evil": "x"}})
        self.assertEqual(out["chat_export_options"].get("format"), "docx")
        self.assertNotIn("theme", out["chat_export_options"])
        self.assertNotIn("evil", out["chat_export_options"])
        self.assertEqual(out["chat_export_options"].get("size"), 20)
        html = ce.finalize_html("<style>/*MX-KATEX-CSS*/</style>", embed_katex=False)
        self.assertNotIn("MX-KATEX-CSS", html)


class RoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from fastapi.testclient import TestClient
        from app.playground_api import create_app
        cls.tmp = tempfile.mkdtemp(prefix="memuai-export-dl-")
        cls.client = TestClient(create_app(), base_url="http://127.0.0.1")

    def downloads(self):
        return mock.patch("app.chat_export_api.downloads_dir", return_value=Path(self.tmp))

    def test_capabilities(self) -> None:
        data = self.client.get("/api/export/capabilities").json()
        self.assertIn(data["pdf"], ("edge", "print"))
        self.assertEqual(set(data["formats"]), {"pdf", "docx", "html", "md", "json", "txt"})

    def test_render_formats(self) -> None:
        with self.downloads():
            response = self.client.post("/api/export/render", json={"format": "docx", "body": SAMPLE,
                                                                    "filename": "My chat", "options": {"lang": "en"}})
            self.assertEqual(response.status_code, 200, response.text)
            saved = response.json()
            self.assertTrue(saved["ok"])
            path = Path(saved["path"])
            self.assertEqual(path.parent, Path(self.tmp))
            self.assertTrue(zipfile.is_zipfile(path))
            response = self.client.post("/api/export/render", json={"format": "html", "html": "<html><style>/*MX-KATEX-CSS*/</style><body>x</body></html>"})
            self.assertEqual(response.status_code, 200, response.text)
            text = Path(response.json()["path"]).read_text(encoding="utf-8")
            self.assertNotIn("/*MX-KATEX-CSS*/", text)
            response = self.client.post("/api/export/render", json={"format": "md", "text": "# hi"})
            self.assertEqual(Path(response.json()["path"]).read_text(encoding="utf-8"), "# hi")
        response = self.client.post("/api/export/render", json={"format": "exe"})
        self.assertEqual(response.status_code, 400)

    def test_pdf_without_edge(self) -> None:
        with mock.patch("app.chat_export_pdf.available", return_value=False):
            response = self.client.post("/api/export/render", json={"format": "pdf", "html": "<p>x</p>", "options": {"lang": "en"}})
        self.assertEqual(response.status_code, 501)
        self.assertEqual(response.json().get("fallback"), "print")

    def test_open_refuses_foreign_files(self) -> None:
        target = Path(self.tmp) / "foreign.txt"
        target.write_text("x", encoding="utf-8")
        response = self.client.post("/api/export/open", json={"path": str(target)})
        self.assertEqual(response.status_code, 403)


class FrontendTest(unittest.TestCase):
    def test_wiring(self) -> None:
        index = (STATIC / "index.html").read_text(encoding="utf-8")
        for name in ("export.js", "export.css"):
            self.assertTrue((STATIC / name).is_file())
            self.assertIn(f"/{name}?v=", index)
        self.assertLess(index.index("/app.js?v="), index.index("/export.js?v="))
        app = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn("MemuExport.open(target)", app)
        api = (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8")
        self.assertIn("chat_export_api.register(app)", api)

    def test_tables_are_not_hydrated_into_files(self) -> None:
        source = (STATIC / "export.js").read_text(encoding="utf-8")
        self.assertNotIn("T.hydrate(", source)
        self.assertIn(".mt-bar", source)

    def test_english_strings(self) -> None:
        source = (STATIC / "export.js").read_text(encoding="utf-8")
        pairs = re.findall(r"\bt\(\s*\"((?:[^\"\\]|\\.)*)\"\s*,\s*\"((?:[^\"\\]|\\.)*)\"", source)
        self.assertGreater(len(pairs), 30)
        for ru, en in pairs:
            self.assertFalse(CYR.search(en), en)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_node_check(self) -> None:
        result = subprocess.run(["node", "--check", str(STATIC / "export.js")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_docs(self) -> None:
        for lang in ("ru", "en"):
            self.assertTrue((ROOT / "app" / "docs" / lang / "chat-export.md").is_file())
        self.assertFalse(CYR.search((ROOT / "app" / "docs" / "en" / "chat-export.md").read_text(encoding="utf-8")))


class BuildTest(unittest.TestCase):
    def test_spec_and_build(self) -> None:
        spec = (ROOT / "MemuAI.spec").read_text(encoding="utf-8")
        build = (ROOT / "build.py").read_text(encoding="utf-8")
        for module in ("app.chat_export", "app.chat_export_api", "app.chat_export_pdf"):
            self.assertIn(f"'{module}'", spec)
            self.assertIn(f'"{module}"', build)


if __name__ == "__main__":
    unittest.main()
