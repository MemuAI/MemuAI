"""Real hidden Edge check of shipped module pages with fixture bridge replies."""
from __future__ import annotations

import asyncio
import base64
import http.server
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
isolated = tempfile.TemporaryDirectory(prefix="memuai-round16-ui-")
os.environ["MEMUAI_DATA_DIR"] = str(Path(isolated.name) / "data")
os.environ["APPDATA"] = str(Path(isolated.name) / "appdata")
os.environ["LOCALAPPDATA"] = str(Path(isolated.name) / "localappdata")
from app.automation_runtime import EdgeAutomation
from app import modules_host

buffer = io.BytesIO()
with wave.open(buffer, "wb") as wav:
    wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000); wav.writeframes(b"\0\0" * 24000)
clip = "data:audio/wav;base64," + base64.b64encode(buffer.getvalue()).decode()
model = {"id": "google/gemini-fixture", "name": "Google: Gemini fixture with a long model name",
         "description": "A long description that should remain below every badge and never overlap. " * 12,
         "created": 1785000000, "context_length": 1000000, "supported_parameters": ["tools", "reasoning", "response_format"],
         "architecture": {"input_modalities": ["text", "image", "audio", "video"], "output_modalities": ["text"]},
         "pricing": {"prompt": "0.000001", "completion": "0.000003"}, "top_provider": {"max_completion_tokens": 32000}}
fixture = json.dumps({"data": [model]})
shell = '''<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:0;background:#eee;overflow:hidden">
<iframe id="module" style="border:0;width:100vw;height:100vh;display:block"></iframe>
<script>
window.calls=[]; window.saved=[]; window.moduleErrors=[]; window.job={enabled:false,sources:[]};
window.addEventListener('message', async event=>{
  const m=event.data||{}; if(m.type!=='memu-module') return;
  calls.push({action:m.action,payload:m.payload}); const p=m.payload||{}; let result={ok:true};
  if(m.action==='fetch') result={text:__FIXTURE__};
  if(m.action==='models') result=[{id:'fixture-media',name:'Fixture media model',caps:{image:true,video:true,text:false}},{id:'fixture-model',name:'Fixture producer model',caps:{text:true}},{id:'fixture-music-song-v1',name:'Song v1',caps:{music:true,text:false}}];
  if(m.action==='routes') result={image:{default:'fixture-model',models:['fixture-model']},video:{default:'fixture-model',models:['fixture-model']},speech:{enabled:true},transcribe:{enabled:true}};
  if(m.action==='save') {saved.push(p);result=p;}
  if(m.action==='env') result={lang:'ru',theme:new URLSearchParams(location.search).get('theme')||'light'};
  if(m.action==='monitor') {
    if(p.action==='configure') job={...p,revision:'fixture'};
    if(p.action==='scan') job={...job,sources:job.sources.map(s=>({...s,count:2,last_success:1785000000,families_available:['google','openai','anthropic','new-vendor']}))};
    result=p.action==='events'?{cursor:1,events:[{id:1,title:'Fixture',text:'+ new model',at:1785000000}]}:job;
  }
  if(m.action==='music') {
    result=p.action==='generate'?{audio:__CLIP__,mime:'audio/wav',model:p.model}:p.action==='cancel'?{ok:true}:{models:[{id:'fixture-music-song-v1',name:'Song v1',provider:'Fixture Music',caps:{music:true,text:false},capabilities:{duration:true,tempo:'hint'}}]};
  }
  if(m.action==='request') result={text:'{"prompt":"A warm instrumental","style":"ambient piano","tempo":90,"instrumental":true}'};
  if(m.action==='log' && p.level==='error') moduleErrors.push(p);
  event.source.postMessage({type:'memu-module-result',id:m.id,result,error:''},'*');
});
const query=new URLSearchParams(location.search); document.querySelector('iframe').src='/module/'+query.get('module')+'?lang='+(query.get('lang')||'ru')+'&theme='+(query.get('theme')||'light');
</script></body></html>'''.replace("__FIXTURE__", json.dumps(fixture)).replace("__CLIP__", json.dumps(clip))

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        from urllib.parse import urlsplit, parse_qs
        url = urlsplit(self.path)
        if url.path.startswith("/module/"):
            identity = url.path.rsplit("/", 1)[1]
            package = modules_host.load_package(modules_host.default_root(), identity)
            page = modules_host.module_document(package, env={"theme": parse_qs(url.query).get("theme", ["light"])[0], "lang": parse_qs(url.query).get("lang", ["ru"])[0]})
            if identity == "studio":
                page = page.replace("<head>", "<head><script>window.qaAudio=[];const NativeAudio=window.Audio;window.Audio=function(...args){const player=new NativeAudio(...args);qaAudio.push(player);return player;};</script>")
        else:
            page = shell
        payload = page.encode("utf-8")
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)
    def log_message(self, *args):
        pass

server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
OUT = ROOT / "audit-results"

async def check():
    edge = EdgeAutomation()
    checks = []
    async def evaluate(expression):
        result = await edge.command("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        if result.get("exceptionDetails"):
            raise AssertionError(result["exceptionDetails"])
        return result.get("result", {}).get("value")
    async def wait(selector):
        for _ in range(40):
            if await evaluate(f"!!document.querySelector('iframe').contentDocument.querySelector({json.dumps(selector)})"):
                return
            await asyncio.sleep(.1)
        raise AssertionError("Missing UI: " + selector)
    async def screenshot(name):
        await asyncio.sleep(.25)
        result = await edge.command("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        (OUT / name).write_bytes(base64.b64decode(result["data"]))
    try:
        await edge.navigate(f"http://127.0.0.1:{server.server_port}/?module=catalog")
        await edge.command("Emulation.setDeviceMetricsOverride", {"width": 480, "height": 620, "deviceScaleFactor": 1, "mobile": False})
        await wait("[data-open]")
        await evaluate("document.querySelector('iframe').contentDocument.querySelector('[data-open]').click()")
        await wait(".mc-desc")
        geometry = await evaluate("JSON.stringify((()=>{const d=document.querySelector('iframe').contentDocument,card=d.querySelector('.mc-card'),b=card.querySelector('.mc-badges').getBoundingClientRect(),t=card.querySelector('.mc-desc').getBoundingClientRect();return {badgeBottom:b.bottom,descriptionTop:t.top,width:card.clientWidth,scroll:card.scrollHeight>card.clientHeight};})())")
        geometry = json.loads(geometry)
        assert geometry["descriptionTop"] >= geometry["badgeBottom"], geometry
        assert geometry["scroll"] and geometry["width"] <= 480, geometry
        checks.append({"catalog_480px": geometry})
        await screenshot("round16-catalog.png")

        for theme in ["light", "dark"]:
            for width in [1440, 800, 420]:
                await edge.command("Emulation.setDeviceMetricsOverride", {"width": width, "height": 900, "deviceScaleFactor": 1, "mobile": False})
                await edge.navigate(f"http://127.0.0.1:{server.server_port}/?module=studio&theme={theme}")
                await wait('[data-tab="music"]')
                await wait('#music-model option[value="fixture-music-song-v1"]')
                await evaluate("document.querySelector('iframe').contentDocument.querySelector('[data-tab=music]').click()")
                await evaluate("(async()=>{const d=document.querySelector('iframe').contentDocument;d.querySelector('#music-prompt').value='Calm piano for an evening by the window';d.querySelector('#music-preset').value='lofi';d.querySelector('#music-preset').dispatchEvent(new d.defaultView.Event('change'));await d.querySelector('#music-go').onclick();d.querySelector('#music-producer-input').value='Help refine this arrangement';await d.querySelector('#music-producer-form').onsubmit({preventDefault(){}});})()")
                await wait("#music-results [data-music-play]")
                result = json.loads(await evaluate("JSON.stringify((()=>{const d=document.querySelector('iframe').contentDocument,r=d.querySelector('.st-music-track').getBoundingClientRect();return {calls:window.calls.filter(c=>c.action==='music').map(c=>c.payload.action),model:window.calls.find(c=>c.action==='music'&&c.payload.action==='generate')?.payload.model,secretFields:d.querySelectorAll('#music-api-key,#music-endpoint').length,configured:window.calls.some(c=>c.action==='music'&&c.payload.action==='configure'),width:d.documentElement.clientWidth,scrollWidth:d.documentElement.scrollWidth,trackLeft:r.left,trackRight:r.right,producerDraft:!d.querySelector('#music-producer-apply').hidden,producerFields:Array.from(d.querySelectorAll('.st-music-draft dd'),n=>n.textContent),producerRawJSON:d.querySelector('#music-producer-chat').textContent.includes('{\"prompt\"'),errors:window.moduleErrors.length};})())"))
                assert "models" in result["calls"] and "generate" in result["calls"] and result["model"] == "fixture-music-song-v1", result
                assert not result["secretFields"] and not result["configured"] and not result["errors"] and result["producerDraft"], result
                assert result["producerFields"] == ["A warm instrumental", "ambient piano", "90 BPM", "Инструментал"] and not result["producerRawJSON"], result
                assert await evaluate("document.querySelector('iframe').contentDocument.querySelector('#music-producer-model').value") == "fixture-model"
                assert await evaluate("window.calls.filter(c=>c.action==='request').every(c=>c.payload.model==='fixture-model')")
                assert result["scrollWidth"] <= result["width"] + 1 and result["trackLeft"] >= 0 and result["trackRight"] <= width + 1, result
                if width == 1440:
                    for _ in range(40):
                        ready = await evaluate("document.querySelector('iframe').contentWindow.qaAudio[0]?.readyState>=1")
                        if ready:
                            break
                        await asyncio.sleep(.05)
                    assert ready, "Music audio did not decode"
                    await evaluate("document.querySelector('iframe').contentDocument.querySelector('[data-music-play]').scrollIntoView({block:'nearest',behavior:'instant'})")
                    await asyncio.sleep(.05)
                    point = await evaluate("(()=>{const d=document.querySelector('iframe').contentDocument,n=d.querySelector('[data-music-play]'),r=n.getBoundingClientRect(),x=r.left+r.width/2,y=r.top+r.height/2;return {x,y,hit:d.elementFromPoint(x,y)?.dataset.musicPlay};})()")
                    assert point.get("hit"), point
                    for kind in ["mousePressed", "mouseReleased"]:
                        await edge.command("Input.dispatchMouseEvent", {"type": kind, "x": point["x"], "y": point["y"], "button": "left", "clickCount": 1})
                    for _ in range(60):
                        playback = await evaluate("(()=>{const a=document.querySelector('iframe').contentWindow.qaAudio[0];return {time:a.currentTime,duration:a.duration,paused:a.paused,error:a.error?.code};})()")
                        if playback["time"] > 0:
                            break
                        await asyncio.sleep(.05)
                    assert playback["time"] > 0 and playback["duration"] == 1 and not playback.get("error"), playback
                    await evaluate("document.querySelector('iframe').contentWindow.qaAudio[0].pause()")
                    result["playback"] = playback
                    await evaluate("document.querySelector('iframe').contentDocument.documentElement.scrollTop=0")
                checks.append({f"studio_music_{theme}_{width}": result})
                await screenshot(f"round17-studio-music-{theme}-{width}.png")
                if theme == "light" and width == 1440:
                    await screenshot("round16-music.png")

        await edge.command("Emulation.setDeviceMetricsOverride", {"width": 1080, "height": 780, "deviceScaleFactor": 1, "mobile": False})
        await edge.navigate(f"http://127.0.0.1:{server.server_port}/?module=api-models&lang=en")
        await wait("#am-save")
        result = json.loads(await evaluate("JSON.stringify((()=>{const d=document.querySelector('iframe').contentDocument;return {sources:d.querySelectorAll('[data-select-source]').length,english:d.querySelector('h1').textContent,keyType:d.querySelector('[data-api-key]').type,errors:window.moduleErrors.length};})())"))
        assert result["sources"] == 8 and result["english"] == "New API models" and result["keyType"] == "password" and not result["errors"], result
        await evaluate("(async()=>{const d=document.querySelector('iframe').contentDocument;d.querySelector('[data-family-preset=gemini-openai]').click();d.querySelector('#am-enabled').checked=true;d.querySelector('#am-enabled').dispatchEvent(new d.defaultView.Event('change'));await d.querySelector('#am-save').onclick();await d.querySelector('#am-scan').onclick();})()")
        result["families"] = await evaluate("window.job.sources[0].families")
        assert result["families"] == ["google", "openai"], result
        checks.append({"api_models": result})
        await screenshot("round16-api-models.png")
        (OUT / "round16-browser.json").write_text(json.dumps({"passed": True, "checks": checks}, indent=2), encoding="utf-8")
        print("PASS: real Edge module bridge, responsive catalog, music audio, eight API sources and family filtering")
    finally:
        await edge.close_browser()

try:
    asyncio.run(check())
finally:
    server.shutdown(); server.server_close(); isolated.cleanup()
