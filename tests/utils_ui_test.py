"""MemuUtils window in headless Chromium: the real module document and bridge, answered by the real
``/api/pg/utils/call`` handler. Catalog, search, a text tool, a file tool, the AI access view and the jobs view;
screenshots go to build/utils-ui/ (dark, light, narrow)."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="memu-utils-ui-"))

from app import modules_host  # noqa: E402
from app.utils_kit import api  # noqa: E402

HOST = """<script>window.addEventListener('message',async event=>{const m=event.data;if(m?.type!=='memu-module')return;
try{const result=await window.__host(m);if(m.action==='stream'){window.postMessage({type:'memu-module-event',id:m.id,event:'delta',delta:result.text},'*');
window.postMessage({type:'memu-module-event',id:m.id,event:'done',result},'*');}
else window.postMessage({type:'memu-module-result',id:m.id,result},'*');}catch(e){window.postMessage({type:'memu-module-result',id:m.id,error:String(e)},'*');}});</script>"""


def _png_b64() -> str:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (240, 160), (30, 90, 200))
    ImageDraw.Draw(img).ellipse([60, 30, 180, 130], fill=(250, 200, 40))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


class MemuUtilsUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest("playwright unavailable")
        chrome = Path(os.environ.get("MEMU_TEST_CHROMIUM", "/usr/local/bin/chromium"))
        if not chrome.is_file():
            raise unittest.SkipTest("Chromium unavailable")
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(executable_path=str(chrome), headless=True, args=["--no-sandbox"])
        cls.output = ROOT / "build" / "utils-ui"
        cls.output.mkdir(parents=True, exist_ok=True)
        cls.pool = ThreadPoolExecutor(max_workers=2)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.pool.shutdown(wait=False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.errors, self.calls, self.saved, self.streams = [], [], {}, []

    def tearDown(self):
        self.temp.cleanup()
        self.assertEqual(self.errors, [])

    def host(self, message):
        action, payload = message["action"], message.get("payload") or {}
        if action == "utils_call":
            self.calls.append(payload.get("op"))
            try:
                op, args = payload.get("op"), payload.get("args") or {}
                return self.pool.submit(lambda: asyncio.run(api.call_async(op, args, lang=self.lang))).result(timeout=120)
            except Exception as exc:  # noqa: BLE001
                raise ValueError(api.error_payload(exc, self.lang)[0]["error"])
        if action == "models":
            return [{"id": "test-chat", "name": "Test chat", "kind": "chat", "provider_label": "Local", "default": True}]
        if action == "stream":
            self.streams.append(payload)
            return {"text": "^\\d{3}-\\d{2}-\\d{2}$"}
        if action == "save":
            self.saved.update(payload)
            return {"ok": True}
        return {"ok": True}

    def open(self, *, theme="dark", lang="ru", width=1180, height=780):
        self.lang = lang
        self.context = self.browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce")
        self.addCleanup(self.context.close)
        page = self.context.new_page()
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and self.errors.append(m.text))
        page.route("http://**/*", lambda route: route.abort())
        page.route("https://**/*", lambda route: route.abort())
        page.expose_function("__host", self.host)
        package = modules_host.load_package(Path(self.temp.name) / "modules", "utils")
        html = modules_host.module_document(package, env={"lang": lang, "theme": theme})
        page.set_content(html.replace("<head>", "<head>" + HOST, 1))
        page.wait_for_selector(".ut-card", timeout=20000)
        return page

    def wait_text(self, page, selector, check, timeout=20.0):
        """Poll in Python: the module CSP forbids the eval behind page.wait_for_function."""
        import time
        end = time.monotonic() + timeout
        text = ""
        while time.monotonic() < end:
            text = page.locator(selector).inner_text()
            if check(text):
                return text
            page.wait_for_timeout(150)
        self.fail(f"{selector}: {text[:300]!r}")

    def shot(self, page, name):
        page.wait_for_timeout(250)
        page.screenshot(path=str(self.output / f"{name}.png"))

    def test_catalog_search_and_text_tool(self):
        page = self.open()
        cards = page.locator(".ut-card").count()
        self.assertGreater(cards, 150)
        self.assertGreaterEqual(page.locator("#cats [data-cat]").count(), 10)
        self.shot(page, "01-list-dark")
        page.fill("#q", "регуляр")
        page.wait_for_timeout(300)
        self.assertLess(page.locator(".ut-card").count(), cards)
        self.shot(page, "02-search")
        page.fill("#q", "")
        page.locator('[data-open="text.case"]').first.click()
        page.wait_for_selector("#view-tool:not([hidden])")
        page.fill("#text", "hello memu utils")
        page.click("#run")
        self.wait_text(page, "#out", lambda text: "HELLO" in text)
        self.shot(page, "03-text-tool")
        self.assertIn("run", self.calls)

    def test_file_tool_and_views(self):
        page = self.open(theme="light")
        page.locator('[data-open="image.info"]').first.click()
        page.wait_for_selector("#view-tool:not([hidden])")
        page.set_input_files("#file-input", files=[{"name": "photo.png", "mimeType": "image/png", "buffer": base64.b64decode(_png_b64())}])
        page.wait_for_selector("#flist :text('photo.png')", timeout=10000)
        page.click("#run")
        self.wait_text(page, "#out", lambda text: "240" in text)
        self.shot(page, "04-file-tool-light")
        page.locator('[data-view-btn="ai"]').first.click() if page.locator('[data-view-btn="ai"]').count() else page.click("#go-ai")
        page.wait_for_selector("#view-ai:not([hidden])")
        page.wait_for_selector("#ai-groups input[type=checkbox]")
        self.shot(page, "05-ai-access-light")
        page.click("#go-jobs")
        page.wait_for_selector("#view-jobs:not([hidden])")
        self.shot(page, "06-jobs-light")

    def test_ai_utility_and_narrow_window(self):
        page = self.open(width=720, height=720, lang="en")
        self.shot(page, "07-narrow-en")
        page.locator('[data-open="ai.regex"]').first.click()
        page.wait_for_selector("#view-tool:not([hidden])")
        page.fill("#text", "phone numbers like 123-45-67")
        page.click("#run")
        self.wait_text(page, "#out", lambda text: "d{3}" in text)
        self.assertTrue(self.streams)
        self.shot(page, "08-ai-tool-narrow")


if __name__ == "__main__":
    unittest.main()
