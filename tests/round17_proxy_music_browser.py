"""Real Proxy account editor with offline metadata and isolated hidden Edge."""
from __future__ import annotations

import asyncio
import base64
import http.server
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
isolated = tempfile.TemporaryDirectory(prefix="memuai-proxy-music-ui-")
for key, leaf in [("APPDATA", "appdata"), ("LOCALAPPDATA", "localappdata"), ("MEMUAI_DATA_DIR", "data")]:
    os.environ[key] = str(Path(isolated.name) / leaf)
from app.automation_runtime import EdgeAutomation
from app import music_runtime
from app.providers import init_registry, meta_list

init_registry()
metadata = meta_list()
STATIC = ROOT / "app" / "static"
OUT = ROOT / "audit-results"
requests = []

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        requests.append(path)
        if path.startswith("/api/"):
            data = {"ok": True}
            if path == "/api/admin/providers":
                data = {"providers": metadata}
            elif path == "/api/admin/accounts":
                data = {"accounts": []}
            elif path == "/api/admin/music/presets":
                data = {"presets": music_runtime.PRESETS, "legacy_available": False}
            elif path == "/api/admin/providers/presets":
                data = {"presets": []}
            elif path == "/api/admin/models":
                data = {"models": [{"id": "fixture-chat", "enabled": True},
                                   {"id": "fixture-song", "enabled": True, "caps": {"music": True, "text": False}},
                                   {"id": "fixture-song-other", "enabled": True, "capabilities": {"music": True, "text": False}},
                                   {"id": "fixture-hybrid", "enabled": True, "capabilities": {"music": True, "text": True}}]}
            elif path == "/api/admin/companion":
                data = {"model": "fixture-chat", "saved": "fixture-song"}
            elif path == "/api/admin/overview":
                data = {"host": "127.0.0.1", "port": 8317, "version": "2.0", "providers": [], "proxy_enabled": True}
            elif path == "/api/admin/logs":
                data = {"records": [], "total": 0, "limit": 100, "offset": 0}
            content, mime = json.dumps(data).encode(), "application/json"
        else:
            target = (STATIC / path.lstrip("/") if path != "/" else STATIC / "index.html").resolve()
            if not target.is_relative_to(STATIC.resolve()) or not target.is_file():
                self.send_error(404)
                return
            content = target.read_bytes()
            if target.suffix == ".html":
                content = content.replace(b"<head>", b"<head><script>window.qaErrors=[];addEventListener('error',e=>qaErrors.push(String(e.message)));addEventListener('unhandledrejection',e=>qaErrors.push(String(e.reason)));</script>")
            mime = {".html": "text/html", ".js": "application/javascript", ".css": "text/css", ".png": "image/png"}.get(target.suffix, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", mime + ("; charset=utf-8" if mime.startswith("text/") or mime == "application/javascript" else ""))
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *args):
        pass

server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()

async def check():
    edge = EdgeAutomation()
    checks = []

    async def evaluate(expression):
        result = await edge.command("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        if result.get("exceptionDetails"):
            raise AssertionError(result["exceptionDetails"])
        return result.get("result", {}).get("value")

    async def wait(expression):
        for _ in range(60):
            if await evaluate(expression):
                return
            await asyncio.sleep(.05)
        raise AssertionError("Missing Proxy UI: " + expression)

    try:
        await edge.navigate(f"http://127.0.0.1:{server.server_port}/")
        for language in ["ru", "en"]:
            for width in [1080, 420]:
                await edge.command("Emulation.setDeviceMetricsOverride", {"width": width, "height": 900, "deviceScaleFactor": 1, "mobile": False})
                await edge.navigate(f"http://127.0.0.1:{server.server_port}/")
                await wait("typeof showAccountForm==='function' && providersMeta.length>0")
                await evaluate(f"(async()=>{{setLang({json.dumps(language)});await fetchProviders(true);showAccountForm('custom');}})()")
                await wait("document.querySelector('#proxy-music-setup select')?.options.length===3")
                await evaluate("(()=>{const root=document.querySelector('#proxy-music-setup');root.open=true;root.querySelector('select').value='elevenlabs';root.querySelector('select').dispatchEvent(new Event('change'));root.querySelector('button').click();document.querySelector('#af-api_key').value='fixture-secret';root.scrollIntoView({block:'start'});})()")
                await asyncio.sleep(.2)
                result = await evaluate("(()=>{const root=document.querySelector('#proxy-music-setup'),modal=document.querySelector('#modal-box'),r=root.getBoundingClientRect(),m=modal.getBoundingClientRect();return {config:JSON.parse(document.querySelector('#af-music_config').value),models:document.querySelector('#af-music_models').value,base:document.querySelector('#af-base_url').value,name:document.querySelector('#af-name').value,schema:root.querySelector('select').value,rootLeft:r.left,rootRight:r.right,modalLeft:m.left,modalRight:m.right,scrollWidth:modal.scrollWidth,clientWidth:modal.clientWidth,errors:qaErrors};})()")
                assert result["schema"] == "elevenlabs" and result["base"] == "https://api.elevenlabs.io/v1", result
                assert result["config"] == music_runtime.PRESETS[0]["config"] and result["models"] == ", ".join(music_runtime.PRESETS[0]["models"]), result
                assert "fixture-secret" not in json.dumps(result) and not result["errors"], result
                assert result["modalLeft"] >= 0 and result["modalRight"] <= width and result["rootLeft"] >= result["modalLeft"] and result["rootRight"] <= result["modalRight"], result
                assert result["scrollWidth"] <= result["clientWidth"] + 1, result
                checks.append({f"proxy_music_{language}_{width}": result})
                shot = await edge.command("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
                (OUT / f"round17-proxy-music-{language}-{width}.png").write_bytes(base64.b64decode(shot["data"]))
                await evaluate("(()=>{const root=document.querySelector('#proxy-music-setup');root.querySelector('select').value='generic';root.querySelector('select').dispatchEvent(new Event('change'));root.querySelector('button').click();root.querySelector('.proxy-music-advanced').open=true;root.scrollIntoView({block:'start'});})()")
                generic = await evaluate("JSON.parse(document.querySelector('#af-music_config').value)")
                assert generic == music_runtime.PRESETS[2]["config"], generic
        await evaluate("closeModal();MemuProxyChat.load();MemuCompanion.open(false)")
        await wait("document.querySelector('#pc-models')?.options.length===2 && document.querySelector('#companion-model')?.options.length===3")
        picks = await evaluate("({chat:Array.from(document.querySelector('#pc-models').options,n=>n.value),companion:Array.from(document.querySelector('#companion-model').options,n=>n.value),automatic:document.querySelector('#companion-model').value})")
        assert picks == {"chat": ["fixture-chat", "fixture-hybrid"], "companion": ["", "fixture-chat", "fixture-hybrid"], "automatic": ""}, picks
        checks.append({"text_model_pickers": picks})
        (OUT / "round17-proxy-music-browser.json").write_text(json.dumps({"passed": True, "checks": checks}, ensure_ascii=False, indent=2), encoding="utf-8")
        print("PASS: actual Proxy music editor, ElevenLabs and generic schemas, RU/EN and 1080/420 px without network credentials")
    finally:
        await edge.close_browser()

try:
    asyncio.run(check())
finally:
    server.shutdown()
    server.server_close()
    isolated.cleanup()
