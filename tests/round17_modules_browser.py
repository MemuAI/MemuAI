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

families = ["anthropic", "google", "openai", "x-ai", "deepseek", "qwen", "meta-llama", "mistralai", "cohere", "nvidia", "moonshotai", "z-ai", "future-vendor"]
models = [{"id": family + "/fixture", "name": family + ": " + ("Gemini fixture with long name" if family == "google" else "Model fixture"),
           "description": "A clear model description with useful capabilities. " * 8,
           "created": 1785000000 + index, "context_length": 1000000,
           "supported_parameters": ["tools", "reasoning", "response_format"],
           "architecture": {"input_modalities": ["text", "image", "audio"], "output_modalities": ["text", "image"] if family == "openai" else ["text"]},
           "pricing": {"prompt": "0" if family == "meta-llama" else "0.000001", "completion": "0" if family == "meta-llama" else "0.000003"},
           "top_provider": {"max_completion_tokens": 32000}} for index, family in enumerate(families)]
fixture = json.dumps({"data": models})
shell = '''<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:0;overflow:hidden">
<iframe style="border:0;width:100vw;height:100vh;display:block"></iframe><script>
window.calls=[];window.saved=[];window.errors=[];window.configurations=0;window.modelCatalog=JSON.parse(__FIXTURE__);
window.job={enabled:false,interval:3600,sources:[{id:'openrouter',title:'OpenRouter',url:'https://openrouter.ai/api/v1/models',format:'json',items_path:'data',id_keys:['id'],label_keys:['name','id'],auth:'none',families:['google','openai'],families_available:['google','openai','anthropic','x-ai']}]};
window.addEventListener('message',async event=>{const m=event.data||{},p=m.payload||{};if(m.type!=='memu-module')return;calls.push({action:m.action,payload:p});let result={ok:true};
if(m.action==='fetch')result={text:JSON.stringify(window.modelCatalog)};
if(m.action==='models')result=[{id:'custom-fixture',name:'Fixture',model:'fixture'}];
if(m.action==='save'){saved.push(p);result=p;}
if(m.action==='monitor'){if(p.action==='configure'){job={...p,revision:'test'};configurations++;}result=p.action==='events'?{events:[],cursor:0}:new URLSearchParams(location.search).get('module')==='benchmarks'?{enabled:false,sources:[]}:job;}
if(m.action==='log'&&p.level==='error')errors.push(p);
event.source.postMessage({type:'memu-module-result',id:m.id,result,error:''},'*');});
document.querySelector('iframe').src='/module/'+new URLSearchParams(location.search).get('module')+location.search;
</script></body></html>'''.replace("__FIXTURE__", json.dumps(fixture))

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlsplit(self.path)
        if url.path.startswith("/module/"):
            identity = url.path.rsplit("/", 1)[1]
            query = parse_qs(url.query)
            saved = {"view": "monitor"} if identity == "benchmarks" else {}
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
    async def navigate(module, width=1440, height=950, theme="light", lang="ru"):
        await edge.navigate(f"http://127.0.0.1:{server.server_port}/?module={module}&theme={theme}&lang={lang}")
        await edge.command("Emulation.setDeviceMetricsOverride", {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False})
    async def shot(name):
        await asyncio.sleep(.25)
        result = await edge.command("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        (OUT / name).write_bytes(base64.b64decode(result["data"]))
    async def bounds():
        return await ui("return {overflow:d.documentElement.scrollWidth>d.documentElement.clientWidth,width:d.documentElement.clientWidth,errors:window.parent.errors.length, ...(d.documentElement.scrollWidth>d.documentElement.clientWidth?{offenders:[...d.querySelectorAll('*')].filter(e=>e.getBoundingClientRect().right>d.documentElement.clientWidth+1).map(e=>({tag:e.tagName,class:e.className,right:e.getBoundingClientRect().right,width:e.getBoundingClientRect().width})).slice(0,12)}:{})};")
    try:
        await navigate("catalog");await wait(".mc-model-description");await shot("round17-catalog-desktop.png")
        await ui("d.querySelector('[data-layout=rows]').click();")
        assert await ui("return d.querySelector('.mc-grid').classList.contains('rows');")
        await ui("d.querySelector('[data-purpose=free]').click();")
        assert await ui("return d.querySelectorAll('.mc-model').length;") == 1
        await ui("d.querySelector('[data-remove-filter=price]').click();")
        assert await ui("return d.querySelectorAll('.mc-model').length;") == len(models)
        await ui("d.querySelector('[data-layout=cards]').click();d.querySelectorAll('[data-compare]')[0].click();d.querySelectorAll('[data-compare]')[1].click();d.querySelector('[data-show-compare]').click();")
        assert await ui("return !!d.querySelector('.mc-table');")
        await ui("d.querySelector('[data-back]').click();")
        checks.append({"catalog_capability_filter_layout_compare":True,**await bounds()})
        await navigate("catalog",480,760,"dark","en");await wait(".mc-model-description");await shot("round17-catalog-narrow-dark.png")
        assert await ui("return getComputedStyle(d.querySelector('#filters')).display;") == "none"
        await ui("d.querySelector('#filters-toggle').click();")
        assert await ui("return d.querySelector('#filters-toggle').getAttribute('aria-expanded');") == "true"
        await ui("d.querySelector('#filters-toggle').click();d.querySelector('[data-open]').click();")
        await wait(".mc-desc")
        geometry = await ui("const c=d.querySelector('#card'),b=c.querySelector('.mc-badges').getBoundingClientRect(),p=c.querySelector('.mc-desc').getBoundingClientRect();return {badgeBottom:b.bottom,descriptionTop:p.top,scroll:c.scrollHeight>c.clientHeight,width:c.clientWidth};")
        assert geometry["descriptionTop"] >= geometry["badgeBottom"] and geometry["width"] <= 480 and geometry["scroll"], geometry
        checks.append({"catalog_narrow_drawer":geometry,**await bounds()});await shot("round17-catalog-detail.png")
        await navigate("api-models");await wait('[data-family="future-vendor"]')
        assert await evaluate("window.configurations") == 0
        assert await ui("return d.querySelectorAll('[data-family]').length;") == len(families)
        assert await ui("return d.querySelectorAll('[data-family]:checked').length;") == 2
        await shot("round17-api-models-desktop.png")
        await ui("const q=d.querySelector('[data-family-query]');q.value='deepseek';q.dispatchEvent(new d.defaultView.Event('input'));return 1;")
        assert await ui("return d.querySelectorAll('[data-family-option]:not([hidden])').length;") == 1
        await ui("d.querySelector('[data-family=deepseek]').click();d.querySelector('#am-save').click();")
        for _ in range(30):
            if await evaluate("window.configurations"): break
            await asyncio.sleep(.1)
        assert await evaluate("window.job.sources[0].families") == ["google","openai","deepseek"]
        checks.append({"all_openrouter_families_before_saved_scan":len(families),"selected_filter_saved":True,**await bounds()})
        await evaluate("window.modelCatalog.data.push({id:'new-arrival/model',name:'New Arrival: Model'})")
        await ui("d.querySelector('[data-refresh-families]').click();")
        await wait('[data-family="new-arrival"]')
        assert await ui("return d.querySelector('[data-family=new-arrival]').checked;") is False
        await ui("d.querySelector('[data-all-families]').click();d.querySelector('#am-save').click();")
        for _ in range(30):
            if await evaluate("window.configurations") >= 2:break
            await asyncio.sleep(.1)
        assert await evaluate("window.job.sources[0].families") == []
        checks.append({"future_family_discovered_without_losing_filter":True,"all_families_future_mode_saved":True})
        await navigate("api-models",480,760,"dark","en");await wait('[data-family="future-vendor"]');await shot("round17-api-models-narrow-dark.png")
        checks.append({"api_narrow_dark":await bounds()})
        await navigate("benchmarks",1080,800);await wait("#monitor-query")
        geometry = await ui("const l=d.querySelector('.bm-monitor-search span').getBoundingClientRect(),q=d.querySelector('#monitor-query').getBoundingClientRect(),choices=d.querySelector('#monitor-choices').getBoundingClientRect();return {labelBottom:l.bottom,inputTop:q.top,inputBottom:q.bottom,choicesTop:choices.top,inputHeight:q.height};")
        assert geometry["inputTop"] >= geometry["labelBottom"] + 7 and geometry["choicesTop"] >= geometry["inputBottom"]+18, geometry
        checks.append({"benchmark_search_spacing":geometry,**await bounds()});await shot("round17-benchmarks-monitor.png")
        await navigate("benchmarks",480,760,"dark","en");await wait("#monitor-query");checks.append({"benchmark_narrow_dark":await bounds()});await shot("round17-benchmarks-narrow-dark.png")
        for module, selector in (("catalog",".mc-model-description"),("api-models",'[data-family="future-vendor"]'),("benchmarks","#monitor-query")):
            for width in (800,420):
                for theme in ("light","dark"):
                    await navigate(module,width,820,theme,"en");await wait(selector)
                    info=await bounds();assert not info["overflow"] and not info["errors"],(module,width,theme,info)
                    if module=="benchmarks":
                        header=await ui("const h=d.querySelector('.bm-bar').getBoundingClientRect();return [...d.querySelectorAll('.bm-bar> *')].filter(e=>getComputedStyle(e).display!=='none').every(e=>{const r=e.getBoundingClientRect();return r.left>=h.left&&r.right<=h.right});")
                        assert header,(module,width,theme,"header clipped")
                    checks.append({"module":module,"viewport":width,"theme":theme,**info})
                    await shot(f"round17-{module}-{width}-{theme}.png")
        for module,selector in (("translator","#source"),("design","#brief")):
            for width,theme in ((1440,"light"),(420,"dark")):
                await navigate(module,width,820,theme,"en");await wait(selector)
                info=await bounds();checks.append({"related_module_observation":module,"viewport":width,"theme":theme,**info})
                await shot(f"round17-{module}-{width}-{theme}.png")
        assert all(not c.get("overflow") and not c.get("errors") for c in checks), checks
        (OUT / "round17-modules-browser.json").write_text(json.dumps({"passed":True,"checks":checks},indent=2),encoding="utf-8")
        print("PASS: shipped catalog interactions, all API families before configuration, saved custom selection, responsive dark/light modules, benchmark spacing")
    finally: await edge.close_browser()

try: asyncio.run(check())
finally: server.shutdown();server.server_close();isolated.cleanup()
