"""Exercise the shipped modules in hidden Edge; all bridge/network replies are isolated fixtures."""
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
from urllib.parse import urlsplit, parse_qs

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
isolated = tempfile.TemporaryDirectory(prefix="memuai-round17-modules-")
os.environ["MEMUAI_DATA_DIR"] = str(Path(isolated.name) / "data")
os.environ["APPDATA"] = str(Path(isolated.name) / "appdata")
os.environ["LOCALAPPDATA"] = str(Path(isolated.name) / "localappdata")
from app.automation_runtime import EdgeAutomation
from app import modules_host

from tests.catalog_benchmarks_fixtures import source_fixtures
fixtures=source_fixtures()
fixture=fixtures['https://openrouter.ai/api/v1/models']
shell = '''<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:0;overflow:hidden">
<iframe style="border:0;width:100vw;height:100vh;display:block"></iframe><script>
window.fixtures=__SOURCES__;window.failAA=false;window.delayEQ=Number(new URLSearchParams(location.search).get('delayEQ')||0);window.calls=[];window.saved=[];window.errors=[];window.configurations=0;window.modelCatalog=JSON.parse(__FIXTURE__);
window.job={enabled:false,interval:3600,sources:[{id:'openrouter',title:'OpenRouter',url:'https://openrouter.ai/api/v1/models',format:'json',items_path:'data',id_keys:['id'],label_keys:['name','id'],auth:'none',families:['google','openai'],families_available:['google','openai','anthropic','x-ai']}]};
window.addEventListener('message',async event=>{const m=event.data||{},p=m.payload||{};if(m.type!=='memu-module')return;calls.push({action:m.action,payload:p});let result={ok:true};
if(m.action==='fetch'){if(window.delayEQ&&p.url==='https://eqbench.com/eqbench4/eqbench4_data.js')await new Promise(resolve=>setTimeout(resolve,window.delayEQ));if(window.failAA&&p.url.includes('artificialanalysis')){event.source.postMessage({type:'memu-module-result',id:m.id,error:'Temporary source unavailable'},'*');return;}result={text:window.fixtures[p.url]||''};}
if(m.action==='models')result=[{id:'custom-fixture',name:'Fixture',model:'fixture'}];
if(m.action==='save'){saved.push(p);result=p;}
if(m.action==='monitor'){if(p.action==='configure'){job={...p,revision:'test'};configurations++;}result=p.action==='events'?{events:[],cursor:0}:new URLSearchParams(location.search).get('module')==='benchmarks'?{enabled:false,sources:[]}:job;}
if(m.action==='log'&&p.level==='error')errors.push(p);
event.source.postMessage({type:'memu-module-result',id:m.id,result,error:''},'*');});
document.querySelector('iframe').src='/module/'+new URLSearchParams(location.search).get('module')+location.search;
</script></body></html>'''.replace("__FIXTURE__", json.dumps(fixture)).replace("__SOURCES__",json.dumps(fixtures).replace("</", "<\\/"))

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlsplit(self.path)
        if url.path.startswith("/module/"):
            identity = url.path.rsplit("/", 1)[1]
            query = parse_qs(url.query)
            saved = {}
            package = modules_host.load_package(modules_host.default_root(), identity)
            page = modules_host.module_document(package, saved, env={"theme": query.get("theme", ["light"])[0], "lang": query.get("lang", ["ru"])[0]})
        else:
            page = shell
        payload = page.encode("utf-8")
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)
    def log_message(self, *args): pass

server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
OUT = ROOT / "audit-results"

async def check():
    edge, checks = EdgeAutomation(), []
    async def evaluate(expression):
        result = await edge.command("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        if result.get("exceptionDetails"): raise AssertionError(result["exceptionDetails"])
        return result.get("result", {}).get("value")
    async def ui(expression):
        return await evaluate("(()=>{const d=document.querySelector('iframe').contentDocument;" + expression + "})()")
    async def wait(selector):
        for _ in range(70):
            if await ui("return !!d.querySelector("+json.dumps(selector)+");"): return
            await asyncio.sleep(.1)
        raise AssertionError("Missing UI: " + selector)
    async def navigate(module, width=1440, height=950, theme="light", lang="ru", delayEQ=0):
        await edge.navigate(f"http://127.0.0.1:{server.server_port}/?module={module}&theme={theme}&lang={lang}&delayEQ={delayEQ}")
        await edge.command("Emulation.setDeviceMetricsOverride", {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False})
    async def shot(name):
        await asyncio.sleep(.25)
        result = await edge.command("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        (OUT / name).write_bytes(base64.b64decode(result["data"]))
    async def bounds():
        return await ui("return {overflow:d.documentElement.scrollWidth>d.documentElement.clientWidth,width:d.documentElement.clientWidth,errors:window.parent.errors.length, ...(d.documentElement.scrollWidth>d.documentElement.clientWidth?{offenders:[...d.querySelectorAll('*')].filter(e=>e.getBoundingClientRect().right>d.documentElement.clientWidth+1).map(e=>({tag:e.tagName,class:e.className,right:e.getBoundingClientRect().right,width:e.getBoundingClientRect().width})).slice(0,12)}:{})};")
    try:
        await navigate("catalog",lang="en",delayEQ=3000);await wait(".mc-model-description")
        assert await ui("return d.querySelectorAll('[data-model=\"google/gemini-3.5-flash\"]').length;")==1
        await ui("d.querySelector('[data-model=\"extra/additional\"] .mc-model-description').click();")
        await wait('#card[data-model="extra/additional"]')
        assert 'Models.dev · Extra provider' in await ui("return d.querySelector('#card').textContent;")
        assert await ui("return d.querySelector('#card .mc-card-actions [data-open-url]').dataset.openUrl;")=="https://models.dev/"
        await ui("d.querySelector('[data-close]').click();")
        await ui("d.querySelector('[data-model=\"anthropic/claude-opus-5\"] .mc-model-description').click();")
        await wait('[data-benchmark-result="aa:intelligence"] .mc-bench-score')
        assert await ui("return d.querySelector('[data-benchmark-result=\"aa:intelligence\"] .mc-bench-score').textContent;")=="90.0"
        assert await ui("return d.querySelector('[data-benchmark-variant]').options.length;")==2
        await ui("const v=d.querySelector('[data-benchmark-result=\"aa:intelligence\"] select');v.value='Claude Opus 5 (low)';v.dispatchEvent(new d.defaultView.Event('change',{bubbles:true}));")
        assert await ui("return d.querySelector('[data-benchmark-result=\"aa:intelligence\"] .mc-bench-score').textContent;")=="70.0"
        await shot("round17-catalog-benchmarks-detail.png")
        await ui("d.querySelector('[data-close]').click();d.querySelector('[data-compare=\"anthropic/claude-opus-5\"]').click();d.querySelector('[data-compare=\"google/gemini-3.5-flash\"]').click();d.querySelector('[data-show-compare]').click();")
        await wait('#compare-benchmarks .mc-bench-score')
        assert await ui("return [...d.querySelectorAll('#compare-benchmarks [data-benchmark-result=\"aa:intelligence\"] .mc-bench-score')].map(e=>e.textContent);")==["70.0","80.0"]
        assert await ui("return d.querySelectorAll('#compare-benchmarks [data-benchmark-result=\"aa:intelligence\"] td.best').length;")==1
        await ui("d.querySelector('#compare-benchmarks details').open=true;d.querySelector('[data-benchmark-board=\"aa:ttft\"]').click();")
        await wait('#compare-benchmarks [data-benchmark-result="aa:ttft"] .mc-bench-score')
        assert await ui("return d.querySelector('#compare-benchmarks [data-benchmark-result=\"aa:ttft\"] td.best .mc-bench-score').textContent;")=="0.30 s"
        assert await ui("return d.querySelector('#compare-benchmarks details').open;")
        await ui("d.querySelector('#compare-benchmarks details').open=false;")
        await ui("d.querySelector('#main').scrollTop=d.querySelector('#compare-benchmarks').offsetTop-20;")
        await shot("round17-catalog-benchmarks-compare.png")
        # An early score appears before the delayed EQ job has completed. A click on
        # disabled Refresh is ignored by the browser; the old test raced that state.
        busy_before_refresh=await ui("return d.querySelector('[data-refresh-benchmarks]').disabled;")
        if busy_before_refresh:
            before_ignored=await evaluate("window.calls.filter(c=>c.action==='fetch'&&c.payload.url.includes('artificialanalysis')).length")
            ignored=await ui("const b=d.querySelector('[data-refresh-benchmarks]');if(!b.disabled)return false;b.click();return true;")
            if ignored:
                assert await evaluate("window.calls.filter(c=>c.action==='fetch'&&c.payload.url.includes('artificialanalysis')).length")==before_ignored
        await wait('#compare-benchmarks [data-refresh-benchmarks]:not([disabled])')
        aa_calls=await evaluate("window.calls.filter(c=>c.action==='fetch'&&c.payload.url.includes('artificialanalysis')).length")
        await evaluate("window.failAA=true;window.delayEQ=0")
        await ui("d.querySelector('[data-refresh-benchmarks]').click();")
        for _ in range(70):
            if await evaluate("window.calls.filter(c=>c.action==='fetch'&&c.payload.url.includes('artificialanalysis')).length")>aa_calls:break
            await asyncio.sleep(.1)
        assert await evaluate("window.calls.filter(c=>c.action==='fetch'&&c.payload.url.includes('artificialanalysis')).length")>aa_calls, 'Refresh did not dispatch a new AA fetch' 
        for _ in range(70):
            if await ui("return d.querySelector('#compare-benchmarks').textContent.includes('Temporary source unavailable')&&!d.querySelector('[data-refresh-benchmarks]').disabled;"):break
            await asyncio.sleep(.1)
        assert await ui("return [...d.querySelectorAll('#compare-benchmarks [data-benchmark-result=\"aa:intelligence\"] .mc-bench-score')].map(e=>e.textContent);")==["70.0","80.0"]
        assert await ui("return d.querySelector('#compare-benchmarks').textContent.includes('Temporary source unavailable');")
        checks.append({"whole_card_detail":True,"explicit_variant_scores":True,"same_board_best_highlight":True,"lower_is_better":True,"failed_refresh_preserves_scores":True,"refresh_waited_for_delayed_initial_source":busy_before_refresh,**await bounds()})
        for width,theme in ((1440,"light"),(800,"dark"),(420,"light"),(420,"dark")):
            await navigate("catalog",width,860,theme,"en");await wait(".mc-model-description")
            await ui("d.querySelector('[data-compare=\"anthropic/claude-opus-5\"]').click();d.querySelector('[data-compare=\"google/gemini-3.5-flash\"]').click();d.querySelector('[data-show-compare]').click();")
            await wait('#compare-benchmarks [data-benchmark-result="aa:intelligence"] .mc-bench-score')
            await ui("d.querySelector('#main').scrollTop=d.querySelector('#compare-benchmarks').offsetTop-20;")
            if width==420:
                pan=await ui("const w=d.querySelector('#compare-benchmarks .mc-table-wrap');w.scrollLeft=250;return {scroll:w.scrollLeft,boardLeft:w.querySelector('tbody th').getBoundingClientRect().left,wrapLeft:w.getBoundingClientRect().left};")
                assert pan['scroll']>0 and abs(pan['boardLeft']-pan['wrapLeft'])<2,pan
            info=await bounds();assert not info['overflow'] and not info['errors'],info
            checks.append({"comparison":True,"width":width,"theme":theme,**info})
            await shot(f"round17-catalog-benchmarks-compare-{width}-{theme}.png")
        await ui("d.querySelector('[data-back]').click();d.querySelector('[data-open=\"future/unknown\"]').click();")
        await wait('#card-benchmarks')
        assert await ui("return d.querySelectorAll('#card-benchmarks .mc-bench-score').length;")==0
        assert await ui("return d.querySelector('#card-benchmarks').textContent.includes('No result');")
        await shot("round17-catalog-benchmarks-missing.png")
        (OUT/'round17-catalog-benchmarks-browser.json').write_text(json.dumps({"passed":True,"checks":checks},indent=2),encoding='utf-8')
        print('PASS: real shared source parsers, detail variants, comparable raw scores, missing results, partial failure, responsive light/dark')
    finally: await edge.close_browser()

try: asyncio.run(check())
finally: server.shutdown();server.server_close();isolated.cleanup()
