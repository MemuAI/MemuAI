"""Round 34 — MemuNote PDF on older Chromium / WebView2: pdf.js 6 calls APIs newer than Chromium 128
(Uint8Array#toHex/toBase64, Uint8Array.fromBase64, Map#getOrInsertComputed, Math.sumPrecise, Response#bytes, …) and
every PDF failed with "a.toHex is not a function". vendor/pdfjs/polyfills.mjs is loaded before pdf.min.mjs in the page
and first thing in the worker (pdf.worker.compat.mjs); the minified vendor files stay untouched.

Static checks run everywhere; the browser tests (real MemuNote server + Chromium) skip without Playwright/Chromium.
Run: NO_PROXY=127.0.0.1,localhost python3 -m pytest -q tests/round34_pdf_test.py"""
from __future__ import annotations

import base64
import os
import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "memunote" / "static"
PDFJS = STATIC / "vendor" / "pdfjs"
PDFEDIT = STATIC / "js" / "pdfedit.js"
CHROME = next((p for p in (os.environ.get("MEMU_TEST_CHROMIUM", ""), "/usr/bin/chromium", "/workspace/work/chromium/chrome")
               if p and os.path.exists(p)), "")

COVERED = {
    ".toHex(": 'define(Uint8Array.prototype, "toHex"',
    ".toBase64(": 'define(Uint8Array.prototype, "toBase64"',
    "Uint8Array.fromBase64": 'define(Uint8Array, "fromBase64"',
    ".getOrInsertComputed(": '"getOrInsertComputed"',
    ".getOrInsert(": '"getOrInsert"',
    "Math.sumPrecise": 'define(Math, "sumPrecise"',
    "Promise.try": 'define(Promise, "try"',
    "Promise.withResolvers": 'define(Promise, "withResolvers"',
    ".bytes()": 'define(Response.prototype, "bytes"',
    "URL.parse": 'define(URL, "parse"',
    ".transferToFixedLength(": '"transferToFixedLength"',
    "AbortSignal.any": 'define(AbortSignal, "any"',
    ".intersection(": 'define(Set.prototype, "intersection"',
    "Iterator.prototype": "G.Iterator",
    "for await": "Symbol.asyncIterator",
}

class StaticWiringTest(unittest.TestCase):
    def test_polyfills_load_before_pdfjs_in_the_page(self) -> None:
        src = PDFEDIT.read_text(encoding="utf-8")
        body = src[src.index("export async function pdfjs()"):]
        body = body[:body.index("\n}\n")]
        poly = body.index('import("../vendor/pdfjs/polyfills.mjs')
        lib = body.index('import("../vendor/pdfjs/pdf.min.mjs')
        self.assertLess(poly, lib)
        self.assertIn("await import(\"../vendor/pdfjs/polyfills.mjs", body[:lib])
        self.assertRegex(body, r'workerSrc = new URL\("\.\./vendor/pdfjs/pdf\.worker\.compat\.mjs[^"]*", import\.meta\.url\)')
        self.assertNotIn('"../vendor/pdfjs/pdf.worker.min.mjs', body)

    def test_worker_entry_imports_polyfills_first_and_reexports_worker(self) -> None:
        lines = [ln for ln in (PDFJS / "pdf.worker.compat.mjs").read_text(encoding="utf-8").splitlines()
                 if ln.strip() and not ln.lstrip().startswith("//")]
        self.assertRegex(lines[0], r'^import "\./polyfills\.mjs(\?[^"]*)?";$')
        self.assertIn('export * from "./pdf.worker.min.mjs";', lines[1:])

    def test_polyfills_cover_every_modern_api_vendor_uses(self) -> None:
        poly = (PDFJS / "polyfills.mjs").read_text(encoding="utf-8")
        self.assertNotRegex(poly, r"(?m)^\s*(import|export)\b")
        vendor = (PDFJS / "pdf.min.mjs").read_text(encoding="utf-8") + (PDFJS / "pdf.worker.min.mjs").read_text(encoding="utf-8")
        for api, marker in COVERED.items():
            with self.subTest(api=api):
                if api in vendor:
                    self.assertIn(marker, poly)

        self.assertIn('typeof target[name] !== "function"', poly)

    def test_vendor_files_untouched(self) -> None:
        for name in ("pdf.min.mjs", "pdf.worker.min.mjs"):
            text = (PDFJS / name).read_text(encoding="utf-8")
            self.assertNotIn("MemuNote", text)
            self.assertNotIn("polyfills", text)

def _tiny_pdf(pages) -> bytes:
    """A valid PDF with one Helvetica page per list of ASCII lines, xref offsets computed."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for lines in pages:
        stream = "BT /F1 18 Tf 60 760 Td " + " ".join(f"({ln}) Tj 0 -26 Td" for ln in lines) + " ET"
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents {len(objs)} 0 R /Resources << /Font << /F1 3 0 R >> >> >>")
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)

_MAKE_PDFS = """async () => {
  const L = await import('./vendor/pdflib/pdflib.js');
  const c = document.createElement('canvas'); c.width = c.height = 64; const g = c.getContext('2d');
  g.fillStyle = 'rgba(220,40,90,.8)'; g.beginPath(); g.arc(32, 32, 30, 0, 7); g.fill();
  const png = await (await new Promise((r) => c.toBlob(r, 'image/png'))).arrayBuffer();   // CSP: no fetch(data:)
  const img = await L.PDFDocument.create(); const f1 = await img.embedFont(L.StandardFonts.Helvetica);
  const p1 = img.addPage([595, 842]); p1.drawText('Image page', { x: 60, y: 780, size: 20, font: f1 });
  p1.drawImage(await img.embedPng(png), { x: 60, y: 400, width: 300, height: 300 });
  const form = await L.PDFDocument.create(); const f2 = await form.embedFont(L.StandardFonts.Helvetica);
  const p2 = form.addPage([595, 842]); p2.drawText('Form page', { x: 60, y: 780, size: 20, font: f2 });
  const tf = form.getForm().createTextField('name'); tf.setText('Ivan'); tf.addToPage(p2, { x: 60, y: 700, width: 200, height: 24 });
  form.getForm().createCheckBox('agree').addToPage(p2, { x: 60, y: 650, width: 18, height: 18 });
  const b64 = (u) => { let s = ''; for (const x of u) s += String.fromCharCode(x); return btoa(s); };
  return { 'images.pdf': b64(await img.save()), 'forms.pdf': b64(await form.save()) };
}"""

_STATE = """() => { const v = [...document.querySelectorAll('.pdf-scroll')].find((e) => e.offsetParent); if (!v) return null;
  const l = v.querySelector('.pdf-loading');
  return { loading: l ? l.textContent : null, pages: v.querySelectorAll('.pdf-page-box').length,
           canvases: v.querySelectorAll('.pdf-page-box canvas').length,
           text: [...v.querySelectorAll('.textLayer')].map((t) => t.textContent).join(' ') }; }"""

@unittest.skipUnless(CHROME, "Chromium not available")
class BrowserPdfTest(unittest.TestCase):
    def setUp(self) -> None:
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError:
            self.skipTest("playwright not installed")
        self.tmp = tempfile.TemporaryDirectory(prefix="r34pdf-")
        self.dir = Path(self.tmp.name)
        self._data = os.environ.get("MEMUAI_DATA_DIR")
        os.environ["MEMUAI_DATA_DIR"] = str(self.dir / "data")
        self.servers = []

    def tearDown(self) -> None:
        for httpd in self.servers:
            httpd.shutdown()
            httpd.server_close()
        if self._data is None:
            os.environ.pop("MEMUAI_DATA_DIR", None)
        else:
            os.environ["MEMUAI_DATA_DIR"] = self._data
        self.tmp.cleanup()

    def _serve(self, files):
        from app.memunote import server
        app = server.NoteApp([str(f) for f in files])
        httpd, _ = server.start(app)
        self.servers.append(httpd)
        return f"http://127.0.0.1:{app.port}/index.html#t={app.token}"

    def test_polyfills_in_the_page_match_reference_values(self) -> None:
        from playwright.sync_api import sync_playwright
        url = self._serve([])
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
            try:
                pg = b.new_page()
                pg.goto(url)
                r = pg.evaluate("""async () => { await import('./vendor/pdfjs/polyfills.mjs?v=r34');
                  const u = new Uint8Array([0, 1, 127, 128, 254, 255, 72, 105]); const m = new Map();
                  m.getOrInsertComputed('k', () => []).push(1); m.getOrInsertComputed('k', () => ['x']).push(2);
                  return { hex: u.toHex(), b64: u.toBase64(), back: [...Uint8Array.fromBase64(u.toBase64())],
                           url: u.toBase64({ alphabet: 'base64url', omitPadding: true }), fromHex: [...Uint8Array.fromHex('00ff10')],
                           map: m.get('k'), ins: m.getOrInsert('z', 5), sum: Math.sumPrecise([1e20, 0.1, -1e20]),
                           tri: await Promise.try((a) => a + 1, 41), bytes: [...await new Response(new Uint8Array([7, 8])).bytes()],
                           iter: new Map([[1, 'a'], [2, 'b']]).keys().filter((k) => k > 1).toArray() }; }""")
            finally:
                b.close()
        self.assertEqual(r["hex"], "00017f80feff4869")
        self.assertEqual(r["b64"], base64.b64encode(bytes([0, 1, 127, 128, 254, 255, 72, 105])).decode())
        self.assertEqual(r["back"], [0, 1, 127, 128, 254, 255, 72, 105])
        self.assertEqual(r["url"], base64.urlsafe_b64encode(bytes([0, 1, 127, 128, 254, 255, 72, 105])).decode().rstrip("="))
        self.assertEqual(r["fromHex"], [0, 255, 16])
        self.assertEqual(r["map"], [1, 2])
        self.assertEqual(r["ins"], 5)
        self.assertEqual(r["sum"], 0.1)
        self.assertEqual(r["tri"], 42)
        self.assertEqual(r["bytes"], [7, 8])
        self.assertEqual(r["iter"], [2])

    def test_pdfs_open_and_render_without_console_errors(self) -> None:
        from playwright.sync_api import sync_playwright
        (self.dir / "simple.pdf").write_bytes(_tiny_pdf([["Hello MemuNote PDF", "Second line"]]))
        (self.dir / "multipage.pdf").write_bytes(_tiny_pdf([[f"Page {n} of 6"] for n in range(1, 7)]))
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
            try:
                pg = b.new_page()
                pg.goto(self._serve([]))
                made = pg.evaluate(_MAKE_PDFS)
                pg.close()
                for name, data in made.items():
                    (self.dir / name).write_bytes(base64.b64decode(data))
                names = ["simple.pdf", "multipage.pdf", "images.pdf", "forms.pdf"]
                errors = []
                pg = b.new_page(viewport={"width": 1280, "height": 860})
                pg.on("console", lambda m: m.type == "error" and errors.append(m.text))
                pg.on("pageerror", lambda e: errors.append(str(e)))
                pg.goto(self._serve([self.dir / n for n in names]))

                pg.wait_for_selector(f".tab.active[title$='{names[-1]}']", timeout=20000)
                states = {}
                for name in names:
                    st = None
                    for _ in range(80):
                        if not pg.locator(f".tab.active[title$='{name}']").count():
                            pg.locator(f".tab[title$='{name}']").dispatch_event("pointerdown", {"button": 0})
                        else:
                            st = pg.evaluate(_STATE)
                            if st and ((st["canvases"] > 0 and st["text"].strip()) or ":" in (st["loading"] or "")):
                                break
                        pg.wait_for_timeout(250)
                    states[name] = st
            finally:
                b.close()
        for name, st in states.items():
            with self.subTest(pdf=name):
                self.assertIsNone(st["loading"], st["loading"])
                self.assertGreater(st["canvases"], 0)
        self.assertEqual(states["multipage.pdf"]["pages"], 6)
        self.assertIn("Hello MemuNote PDF", states["simple.pdf"]["text"])
        self.assertIn("Form page", states["forms.pdf"]["text"])
        self.assertFalse([e for e in errors if "toHex" in e or "is not a function" in e or "pdf" in e.lower()], errors)

if __name__ == "__main__":
    unittest.main()
