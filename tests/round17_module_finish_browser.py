"""Real Edge proof for translated Design status and the narrow Translator toolbar."""
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
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
isolated = tempfile.TemporaryDirectory(prefix="memuai-round17-module-finish-")
os.environ.update(MEMUAI_DATA_DIR=str(Path(isolated.name) / "data"),
                  APPDATA=str(Path(isolated.name) / "appdata"),
                  LOCALAPPDATA=str(Path(isolated.name) / "localappdata"))
from app.automation_runtime import EdgeAutomation
from app import modules_host

SHELL = """<!doctype html><html><body style="margin:0;overflow:hidden">
<iframe style="width:100vw;height:100vh;border:0;display:block"></iframe><script>
window.errors=[];
addEventListener('message',event=>{const m=event.data||{};if(m.type!=='memu-module')return;
if(m.action==='log'&&m.payload?.level==='error')errors.push(m.payload);
const result=m.action==='models'?[{id:'custom-fixture',name:'Fixture'}]:{ok:true};
event.source.postMessage({type:'memu-module-result',id:m.id,result,error:''},'*');});
document.querySelector('iframe').src='/module'+location.search;
</script></body></html>"""

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlsplit(self.path).query)
        if urlsplit(self.path).path == "/module":
            package = modules_host.load_package(modules_host.default_root(), query["module"][0])
            page = modules_host.module_document(package, {}, env={"lang":query["lang"][0], "theme":query["theme"][0]})
        else:
            page = SHELL
        payload = page.encode("utf-8")
        self.send_response(200);self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)));self.end_headers();self.wfile.write(payload)
    def log_message(self, *args): pass

server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
OUT = ROOT / "audit-results/round17-ui"
OUT.mkdir(exist_ok=True, parents=True)

async def check():
    edge, checks = EdgeAutomation(), []
    async def ui(expression):
        result = await edge.command("Runtime.evaluate", {"expression":"(()=>{const d=document.querySelector('iframe').contentDocument;"+expression+"})()", "returnByValue":True, "awaitPromise":True})
        if result.get("exceptionDetails"): raise AssertionError(result["exceptionDetails"])
        return result.get("result", {}).get("value")
    async def wait(expression):
        for _ in range(80):
            if await ui(expression):return
            await asyncio.sleep(.05)
        raise AssertionError(expression)
    async def navigate(module, width, theme, lang):
        await edge.navigate(f"http://127.0.0.1:{server.server_port}/?module={module}&theme={theme}&lang={lang}")
        await edge.command("Emulation.setDeviceMetricsOverride", {"width":width,"height":820,"deviceScaleFactor":1,"mobile":False})
        await wait("return !!d.querySelector('#model option[value=custom-fixture]');")
    async def shot(name):
        result = await edge.command("Page.captureScreenshot", {"format":"png","captureBeyondViewport":False})
        (OUT / name).write_bytes(base64.b64decode(result["data"]))
    try:
        for width in (1440, 640, 420, 320):
            for theme in ("light", "dark"):
                await navigate("translator", width, theme, "en")
                geometry = await ui("const l=d.querySelector('.tr-langs').getBoundingClientRect(),s=d.querySelector('#settings').getBoundingClientRect(),m=d.querySelector('#mode-seg').getBoundingClientRect();return {languageTop:l.top,settingsTop:s.top,settingsBottom:s.bottom,modesTop:m.top,overflow:d.documentElement.scrollWidth>d.documentElement.clientWidth};")
                assert not geometry["overflow"], (width, theme, geometry)
                if width <= 760:
                    assert abs(geometry["languageTop"]-geometry["settingsTop"]) <= 5 and geometry["settingsBottom"] <= geometry["modesTop"], geometry
                await ui("d.querySelector('#settings').click();return true;")
                assert await ui("return !d.querySelector('#panel').hidden && d.querySelector('#settings').getAttribute('aria-pressed')==='true';")
                await ui("d.querySelector('[data-close-drawer]').click();return true;")
                assert await ui("return d.querySelector('#panel').hidden;")
                checks.append({"module":"translator","width":width,"theme":theme,**geometry})
                if width in (1440,420):await shot(f"translator-finish-{width}-{theme}.png")
        for theme in ("light", "dark"):
            await navigate("design", 1440, theme, "en")
            assert await ui("return d.querySelector('#design-status').textContent;") == "Websites from descriptions and images"
            await ui("d.defaultView.postMessage({type:'memu-module-host',action:'language',payload:{lang:'ru'}},'*');return true;")
            await wait("return d.querySelector('#design-status').textContent==='Сайт из описания и изображений';")
            await ui("d.querySelector('#make').click();return true;")
            assert await ui("return d.querySelector('#design-status').textContent;") == "Опишите задачу"
            await ui("d.defaultView.postMessage({type:'memu-module-host',action:'language',payload:{lang:'en'}},'*');return true;")
            await wait("return d.documentElement.lang==='en';")
            assert await ui("return d.querySelector('#design-status').textContent;") == "Опишите задачу"
            await navigate("design", 1440, theme, "en")
            await shot(f"design-status-finish-{theme}.png")
            checks.append({"module":"design","theme":theme,"idle_language_switch":True,"active_error_preserved":True})
        assert await ui("return window.parent.errors.length;") == 0
        (OUT/"module-finish-proof.json").write_text(json.dumps({"passed":True,"checks":checks},indent=2), encoding="utf-8")
        print("PASS: translated Design idle status/live locale switching/error preservation; Translator settings aligned, operable, no overflow at 1440/640/420/320 in both themes")
    finally:await edge.close_browser()

try:asyncio.run(check())
finally:server.shutdown();server.server_close();isolated.cleanup()
