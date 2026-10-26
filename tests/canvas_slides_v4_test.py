"""Canvas serves the complete shared presentation engine, including chart workbooks.

Uses real read-only Canvas routes and an isolated headless browser, with no model calls.
"""
from __future__ import annotations

import base64
import io
import re
import socket
import threading
import time
import unittest
from pathlib import Path
from urllib.parse import urljoin
import zipfile

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response
from fastapi.testclient import TestClient

from app import canvas
from tests.round32_slides_test import _chromium, package_problems

ROOT = Path(__file__).resolve().parents[1]
ENGINE = "/api/pg/canvas-engine/slides/"

def app_for_test():
    app = FastAPI()
    canvas.register(app)

    @app.get("/")
    def index():
        return HTMLResponse('<!doctype html><html><body><div id="slides" style="width:800px"></div>'
                            '<script src="/canvas-kinds.js"></script></body></html>')

    @app.get("/canvas-kinds.js")
    def kinds():
        return Response((ROOT / "app/pgstatic/canvas-kinds.js").read_bytes(), media_type="text/javascript")

    return app

class CanvasEngineRoutes(unittest.TestCase):
    def test_transitive_module_dependencies_are_served_and_other_files_are_denied(self):
        with TestClient(app_for_test()) as client:
            queue = [ENGINE + name for name in ("pptx.js", "render.js", "templates.js")]
            seen = set()
            while queue:
                url = queue.pop()
                if url in seen:
                    continue
                seen.add(url)
                response = client.get(url)
                self.assertEqual(response.status_code, 200, url)
                self.assertIn("javascript", response.headers["content-type"])
                imports = re.findall(r'(?:from\s*|import\s*\()\s*["\'](\.[^"\']+)["\']', response.text)
                queue.extend(urljoin(url, target) for target in imports)
            self.assertIn(ENGINE + "chart-workbook.js", seen)
            self.assertIn(ENGINE + "zip.js", seen)
            self.assertEqual(client.get(ENGINE + "view.js").status_code, 404)
            self.assertEqual(client.get(ENGINE + "..%2Fcore.js").status_code, 404)
            css = client.get(ENGINE + "render.css")
            self.assertEqual(css.status_code, 200)
            self.assertIn(".sl-slide", css.text)
            self.assertNotIn(".sl-app", css.text)

    @unittest.skipUnless(_chromium(), "Chromium is unavailable")
    def test_canvas_renders_exports_and_shared_engine_embeds_chart_workbook(self):
        import uvicorn
        from playwright.sync_api import sync_playwright
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app_for_test(), log_level="error"))
        thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(.02)
            self.assertTrue(server.started)
            base = f"http://127.0.0.1:{port}"
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])
                try:
                    page = browser.new_page()
                    errors, failed = [], []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on("response", lambda response: failed.append(response.url) if response.status >= 400 and "/canvas-engine/" in response.url else None)
                    page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base + "/") else route.abort())
                    page.goto(base + "/")
                    result = page.evaluate("""async () => {
                      const text='theme: clean\\n# Canvas title\\nA useful subtitle\\n---\\n## Next step\\n- Verify the result\\nNotes: Explain the evidence.';
                      const host=document.querySelector('#slides');const failures=[];
                      await MemuCanvasKinds.slides.view(host,{text,lang:'en',art:{title:'Canvas deck'},editable:false,L:(ru,en)=>en,esc:s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),onError:e=>failures.push(e)});
                      const rendered=host.querySelector('.cv-sl-stage')?.shadowRoot?.querySelectorAll('.sl-slide').length || 0;
                      const blob=await MemuCanvasKinds.slides.export(text,'pptx',{lang:'en',title:'Canvas deck'});
                      const encode=bytes=>{let s='';for(const b of bytes)s+=String.fromCharCode(b);return btoa(s)};
                      const exported=encode(new Uint8Array(await blob.arrayBuffer()));
                      const P=await import('/api/pg/canvas-engine/slides/pptx.js');
                      const deck=await P.newDeck({lang:'en'});
                      deck.slides[0].elements.push({id:'chart',kind:'chart',name:'Chart',x:1e6,y:1e6,w:4e6,h:3e6,chart:{type:'col',cats:['First','Second'],series:[{name:'Result',vals:[2,5]}]}});
                      const chart=encode(await P.savePptx(deck));
                      return {rendered,failures,exported,chart};
                    }""")
                    self.assertEqual(result["rendered"], 2)
                    self.assertEqual(result["failures"], [])
                    for kind in ("exported", "chart"):
                        data = base64.b64decode(result[kind])
                        self.assertEqual(package_problems(data), [], kind)
                    with zipfile.ZipFile(io.BytesIO(base64.b64decode(result["chart"]))) as deck:
                        workbooks = [name for name in deck.namelist() if name.startswith("ppt/embeddings/") and name.endswith(".xlsx")]
                        self.assertEqual(len(workbooks), 1)
                        with zipfile.ZipFile(io.BytesIO(deck.read(workbooks[0]))) as workbook:
                            xml = workbook.read("xl/worksheets/sheet1.xml")
                            self.assertIn(b"Result", xml)
                            self.assertIn(b"<v>5</v>", xml)
                    self.assertEqual(errors, [])
                    self.assertEqual(failed, [])
                finally:
                    browser.close()
        finally:
            server.should_exit = True
            thread.join(5)
            sock.close()

if __name__ == "__main__":
    unittest.main()
