"""Real desktop UI in isolated hidden Edge, with deterministic API/media fixtures."""
from __future__ import annotations

import asyncio
import base64
import http.server
import io
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import struct
import sys
import tempfile
import threading
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
temporary = tempfile.TemporaryDirectory(prefix="memuai-round17-surfaces-")
isolated = Path(temporary.name)
os.environ.update(MEMUAI_DATA_DIR=str(isolated / "data"), APPDATA=str(isolated / "appdata"),
                  LOCALAPPDATA=str(isolated / "localappdata"))
from app.automation_runtime import EdgeAutomation
from tests.appearance_fixture import build_fixture

html = build_fixture(isolated / "fixture").read_text(encoding="utf-8")
html = html.replace((ROOT / "app/pgstatic").as_uri(), "/assets")
html = html.replace((isolated / "fixture/themes.css").as_uri(), "/themes.css")
html = html.replace((ROOT / "memuai-icon.png").as_uri(), "/icon.png")
html = html.replace("<head>", "<head><script>window.__qa_nativeFetch=window.fetch.bind(window);window.__qa_errors=[];addEventListener('error',e=>__qa_errors.push(e.error?.stack||e.message));addEventListener('unhandledrejection',e=>__qa_errors.push(e.reason?.stack||String(e.reason)));</script>")


def wav_bytes(seconds):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000)
        wav.writeframes(b"".join(struct.pack("<h", int(math.sin(i * 2 * math.pi * 330 / 24000) * 3000))
                                 for i in range(round(seconds * 24000))))
    return stream.getvalue()


short_clip, long_clip = wav_bytes(.22), wav_bytes(4)
downloaded_files = []


class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        # The real player/file helper talks over HTTP; write only to this test's
        # temporary folder and inspect the exact PCM bytes sent by its bridge.
        if self.path != "/api/pg/download-file":
            self.send_error(404);return
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        raw = base64.b64decode(body["url"].split(",", 1)[1], validate=True)
        assert raw == short_clip and body["name"] == "MemuAI-voice.wav", body["name"]
        path = isolated / "downloads" / str(len(downloaded_files)) / body["name"]
        path.parent.mkdir(parents=True, exist_ok=True);path.write_bytes(raw)
        downloaded_files.append({"name":path.name,"bytes":len(raw),"short_pcm_matches":True})
        payload = json.dumps({"ok":True,"path":str(path),"name":path.name,"bytes":len(raw)}).encode()
        self.send_response(200);self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)));self.end_headers();self.wfile.write(payload)
    def do_GET(self):
        from urllib.parse import urlsplit, unquote
        path = unquote(urlsplit(self.path).path)
        mime, payload = "text/html; charset=utf-8", None
        if path in {"/", "/main"}:
            payload = html.encode("utf-8")
        elif path.startswith("/api/voice/clips/"):
            mime, payload = "audio/wav", short_clip if path.rsplit("/", 1)[1].startswith("a") else long_clip
        elif path == "/themes.css":
            mime, payload = "text/css", (isolated / "fixture/themes.css").read_bytes()
        elif path == "/icon.png":
            mime, payload = "image/png", (ROOT / "memuai-icon.png").read_bytes()
        elif path.startswith("/assets/"):
            root = (ROOT / "app/pgstatic").resolve()
            file = (root / path.removeprefix("/assets/")).resolve()
            if file.is_relative_to(root) and file.is_file():
                mime, payload = mimetypes.guess_type(str(file))[0] or "application/octet-stream", file.read_bytes()
        if payload is None:
            self.send_error(404); return
        length, status, content_range = len(payload), 200, None
        if mime == "audio/wav" and self.headers.get("Range", "").startswith("bytes="):
            start_text, end_text = self.headers["Range"][6:].split(",", 1)[0].split("-", 1)
            start, end = int(start_text or 0), min(int(end_text or length - 1), length - 1)
            content_range = f"bytes {start}-{end}/{length}"
            payload, status = payload[start:end + 1], 206
        self.send_response(status); self.send_header("Content-Type", mime)
        if mime == "audio/wav":
            self.send_header("Accept-Ranges", "bytes")
        if content_range:
            self.send_header("Content-Range", content_range)
        self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)
    def log_message(self, *args):
        pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
OUT = ROOT / "audit-results/round17-ui"
OUT.mkdir(parents=True, exist_ok=True)


async def main():
    edge, checks = EdgeAutomation(), []
    async def evaluate(expression):
        result = await edge.command("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        if result.get("exceptionDetails"):
            raise AssertionError(result["exceptionDetails"])
        return result.get("result", {}).get("value")
    async def wait(expression):
        for _ in range(100):
            if await evaluate(expression):
                return
            await asyncio.sleep(.05)
        diagnostics = await evaluate("(()=>{const b=document.querySelector('#btn-plus'),m=document.querySelector('.plus-menu'),r=b?.getBoundingClientRect();return {button:r?{x:r.x,y:r.y,width:r.width,height:r.height,expanded:b.getAttribute('aria-expanded'),disabled:b.disabled,topElement:document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.outerHTML.slice(0,600)}:null,menu:m?{html:m.outerHTML.slice(0,600),opacity:getComputedStyle(m).opacity,visibility:getComputedStyle(m).visibility}:null,events:window.__qa_uiEvents,errors:window.__qa_errors}})()")
        print('UI wait diagnostics:', json.dumps(diagnostics, ensure_ascii=False))
        raise AssertionError("UI condition: " + expression)
    async def click(selector):
        await settle_layout()
        box = await evaluate(f"(()=>{{const r=document.querySelector({json.dumps(selector)}).getBoundingClientRect();return {{x:r.x+r.width/2,y:r.y+r.height/2}};}})()")
        hit = await evaluate(f"(()=>{{const target=document.querySelector({json.dumps(selector)}),top=document.elementFromPoint({box['x']},{box['y']});return {{inside:target.contains(top),top:top?.outerHTML.slice(0,1000),ancestors:top?[...function*(e){{while(e){{yield e.id+' '+e.className;e=e.parentElement}}}}(top)].slice(0,6):[],panels:[...document.querySelectorAll('#panel-back,.voice-player-menu,.saved-download-toast')].map(e=>({{id:e.id,cls:e.className,style:getComputedStyle(e).pointerEvents}}))}}}})()")
        assert hit['inside'], (selector, box, hit)
        for kind in ("mousePressed", "mouseReleased"):
            await edge.command("Input.dispatchMouseEvent", {"type": kind, **box, "button": "left", "clickCount": 1})
    async def settle_layout():
        # Sidebar width transitions move the composer after the viewport has
        # changed. Await the actual finite animations before a trusted click;
        # decorative infinite animations must not block UI checks.
        await evaluate("(async()=>{await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));await Promise.allSettled(document.getAnimations().filter(a=>a.playState==='running'&&Number.isFinite(a.effect?.getComputedTiming().endTime)).map(a=>a.finished));await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));return true})()")
    async def viewport(width, height=960):
        await edge.command("Emulation.setDeviceMetricsOverride", {"width":width,"height":height,"deviceScaleFactor":1,"mobile":False})
        await wait(f"innerWidth==={width}&&innerHeight==={height}")
        # CDP's metrics acknowledgement precedes the page's rendering lifecycle.
        # Resize handlers run during rendering and deliberately close popovers.
        await settle_layout()
    async def open_tools():
        await evaluate("MemuTools.close()")
        await click("#btn-plus")
        await wait("(()=>{const m=document.querySelector('.plus-menu'),r=m?.getBoundingClientRect();return !!m&&r.width>0&&r.height>0&&getComputedStyle(m).visibility!=='hidden'&&Number(getComputedStyle(m).opacity)>.99&&document.querySelector('#btn-plus').getAttribute('aria-expanded')==='true'&&!!m.querySelector('[data-tool=research] .plus-icon')&&!!m.querySelector('[data-tool=sketch] .plus-icon')})()")
    async def tool_colors():
        # Read both states in the same DOM turn once the real menu is visible.
        return await evaluate("(()=>{const m=document.querySelector('.plus-menu');return {research:getComputedStyle(m.querySelector('[data-tool=research] .plus-icon')).color,normal:getComputedStyle(m.querySelector('[data-tool=sketch] .plus-icon')).color}})()")
    async def snapshot(name):
        await asyncio.sleep(.25)
        result = await edge.command("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        (OUT / (name + ".png")).write_bytes(base64.b64decode(result["data"]))
    try:
        await edge.navigate(f"http://127.0.0.1:{server.server_port}/main")
        await wait("document.body.classList.contains('boot-ready')")
        await evaluate("window.__qa_uiEvents=[];['pointerdown','pointerup','click','resize'].forEach(type=>addEventListener(type,e=>{__qa_uiEvents.push({type,target:e.target?.closest?.('#btn-plus')?'plus':e.target?.tagName,menu:!!document.querySelector('.plus-menu'),x:e.clientX,y:e.clientY,at:performance.now()});__qa_uiEvents=__qa_uiEvents.slice(-24)},true))")
        await viewport(1440)
        await evaluate("S.chatCaps={deep_research:false}")
        await open_tools()
        # Exact failure mechanism: a late resize removes an already-open menu.
        # Exercise that production listener, then stabilize before reopening.
        await evaluate("window.__qa_lateResizeSeen=false;setTimeout(()=>{window.dispatchEvent(new Event('resize'));__qa_lateResizeSeen=true},160)")
        await wait("__qa_lateResizeSeen&&!document.querySelector('.plus-menu')&&document.querySelector('#btn-plus').getAttribute('aria-expanded')==='false'")
        await viewport(1440)
        await open_tools()
        assert await evaluate("!!document.querySelector('.plus-menu [data-tool=research] .plus-icon')")
        checks.append({"tool_menu_delayed_resize_closes_then_reopens":True,"viewport_lifecycle_wait":True})
        await evaluate("MemuTools.close()")
        for mode in ("light", "dark"):
            await evaluate(f"setTheme({json.dumps(mode)},false)")
            await viewport(1440)
            await evaluate("window.__qa_noticeAction=false;toast('Проверка уведомления '+S.theme+': запись MemuAI-voice.wav сохранена в папку загрузок','ok',{label:'Показать в папке',run:()=>{__qa_noticeAction=true}});document.querySelector('.toast-container').lastElementChild.dataset.qaNotice=S.theme")
            await wait("!!document.querySelector('.toast.toast-show .toast-action')")
            await evaluate("S.chatCaps={deep_research:false}")
            await open_tools()
            colors = await tool_colors()
            off, normal = colors["research"], colors["normal"]
            assert off == normal, (mode, off, normal)
            await evaluate("MemuTools.setOn('research',true)")
            # setOn updates chips; reopen so the visible menu reflects state.
            await open_tools()
            on = (await tool_colors())["research"]
            assert on != off, (mode, on, off)
            await snapshot(mode + "-tools")
            await evaluate("MemuTools.close()")
            await click(f".toast[data-qa-notice={mode}] .toast-action")
            assert await evaluate("__qa_noticeAction")
            checks.append({"notification":mode,"composer_click_not_intercepted":True,"action_clickable":True})
            await evaluate("MemuTools.close();S.chatCaps={};currentSettingsTab='general';showSettings(false)")
            await wait("!!document.querySelector('.settings-tab-pane')")
            for width in (1440, 640, 420):
                await viewport(width)
                result = await evaluate("(()=>{const panel=document.querySelector('.settings-panel').getBoundingClientRect(),body=document.querySelector('.settings-body');return {left:panel.left,right:panel.right,bodyWidth:body.clientWidth,overflow:body.scrollWidth-body.clientWidth};})()")
                assert result["left"] >= -1 and result["right"] <= width + 1 and result["bodyWidth"] >= min(width - 80, 300), (mode, width, result)
                assert result["overflow"] <= 2, (mode, width, result)
                await snapshot(f"{mode}-settings-{width}")
                checks.append({"settings": mode, "width": width, **result})
            for tab in ("appearance", "voice", "prompts"):
                await evaluate(f"document.querySelector('[data-set-tab={tab}]').click()")
                await wait(f"document.querySelector('#pane-{tab}')?.classList.contains('active')")
                for width in (1440, 420):
                    await viewport(width)
                    await asyncio.sleep(.05)
                    result = await evaluate("(()=>{const b=document.querySelector('.settings-body');return {overflow:b.scrollWidth-b.clientWidth,width:b.clientWidth};})()")
                    assert result["overflow"] <= 2, (mode, tab, width, result)
                    checks.append({"settings": mode, "tab": tab, "viewport": width, **result})
                    await snapshot(f"{mode}-{tab}-{width}")
            old_language = await evaluate("PG_LANG")
            old_preferences = await evaluate("S.preferences")
            for language in ("ru", "en"):
                await evaluate("PG_LANG="+json.dumps(language)+";S.preferences={...S.preferences,voice_live_primary_personality:'warm',voice_live_secondary_personalities:['witty'],voice_live_personality_preset_id:'',voice_live_personality_presets:[{id:'persona-fixture',name:'Fixture preset',primary:'romantic',secondary:['warm','coach','teacher','romantic']}]};showLivePersonalitySettings()")
                descriptions = await evaluate("[...document.querySelectorAll('#live-persona-primary small')].map(s=>s.textContent)")
                assert len(descriptions) == 12 and all(len(s)<100 for s in descriptions), descriptions
                assert all(bool(re.search('[А-Яа-яЁё]', s)) == (language == 'ru') for s in descriptions), descriptions
                for width in (1440, 420):
                    await viewport(width)
                    assert await evaluate("(()=>{const p=document.querySelector('.live-persona-panel');return p.scrollWidth-p.clientWidth<=2})()")
                    await snapshot(f"{mode}-personality-{language}-{width}")
                await evaluate("(()=>{const p=document.querySelector('#live-persona-preset');p.value='persona-fixture';p.dispatchEvent(new Event('change'))})()")
                selected = await evaluate("({main:document.querySelector('#live-persona-primary input:checked').value,secondary:[...document.querySelectorAll('#live-persona-secondary input:checked')].map(i=>i.value),disabled:[...document.querySelectorAll('#live-persona-secondary input:disabled')].map(i=>i.value)})")
                assert selected == {"main":"romantic","secondary":["warm","coach"],"disabled":["romantic"]}, selected
                await evaluate("document.querySelector('#live-persona-secondary input[value=teacher]').click()")
                assert await evaluate("document.querySelectorAll('#live-persona-secondary input:checked').length") == 2
                assert await evaluate("!document.querySelector('#live-persona-secondary input[value=teacher]').checked")
                await evaluate("document.querySelector('#live-persona-primary input[value=coach]').click();document.querySelector('#live-persona-secondary input[value=witty]').click();document.querySelector('#live-persona-apply').click()")
                await wait("S.preferences.voice_live_primary_personality==='coach'&&S.preferences.voice_live_secondary_personalities.length===2")
                assert await evaluate("S.preferences.voice_live_secondary_personalities") == ["warm","witty"]
                await wait("!!document.querySelector('.settings-panel')")
                checks.append({"personality_settings":mode,"language":language,"short_localized_descriptions":True,"preset_secondary_sync":True,"two_accent_limit":True,"selected_values_saved":True})
            await evaluate("PG_LANG="+json.dumps(old_language)+";S.preferences="+json.dumps(old_preferences))
            await evaluate("window.fetch=((fixture)=>function(url,options){return String(url).startsWith('/api/voice/clips/')||String(url)==='/api/pg/download-file'||String(url).startsWith('blob:')?__qa_nativeFetch(url,options):fixture(url,options)})(window.fetch);closePanel('replace');S.tempChat=true;S.currentId='voice-fixture';S.messages=[{role:'user',content:'Привет.',liveTurnId:'user-short',liveRealtime:true,audio:{url:'/api/voice/clips/" + "a" * 64 + ".wav?profile_id=default',duration:.22}},{role:'assistant',content:'Привет! Как прошёл день?',liveTurnId:'assistant',liveRealtime:true,audio:{url:'/api/voice/clips/" + "b" * 64 + ".wav?profile_id=default',duration:4}}];renderMessages()")
            await wait("document.querySelectorAll('.voice-player audio').length===2 && [...document.querySelectorAll('.voice-player audio')].every(a=>a.readyState>=1)")
            for width in (1440, 800, 420):
                await viewport(width)
                result = await evaluate("[...document.querySelectorAll('.voice-player')].map(p=>{const r=p.getBoundingClientRect(),s=p.querySelector('.voice-player-seek').getBoundingClientRect(),a=p.querySelector('audio');return {width:r.width,left:r.left,right:r.right,seekWidth:s.width,duration:a.duration,error:a.error?.code||0};})")
                assert all(p["width"] >= min(260, width - 80) and p["seekWidth"] > 60 and p["error"] == 0 and p["duration"] > 0 for p in result), (mode, width, result)
                assert all(p["left"] >= -1 and p["right"] <= width + 1 for p in result), (mode, width, result)
                await snapshot(f"{mode}-voice-{width}")
                checks.append({"voice": mode, "viewport": width, "players": result})
            await click(".msg-user .voice-player-play")
            await wait("document.querySelector('.msg-user audio').currentTime>0")
            playing = await evaluate("(()=>{const a=document.querySelector('.msg-user audio'),b=document.querySelector('.msg-user .voice-player-play'),r=b.getBoundingClientRect();return {time:a.currentTime,paused:a.paused,ended:a.ended,error:a.error?.code||0,top:document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.outerHTML};})()")
            assert playing["time"] > 0, ("Short recording did not play", playing)
            await click(".msg-assistant .voice-player-more")
            await wait("!!document.querySelector('.voice-player-menu')")
            menu = await evaluate("(()=>{const r=document.querySelector('.voice-player-menu').getBoundingClientRect();return {left:r.left,right:r.right,bottom:r.bottom};})()")
            assert menu["left"] >= 0 and menu["right"] <= 420 and menu["bottom"] <= 960, menu
            await snapshot(mode + "-voice-menu")
            await evaluate("document.querySelector('.voice-player-menu [data-rate=\"1.5\"]').click()")
            assert await evaluate("document.querySelector('.msg-assistant audio').playbackRate===1.5")
            before = len(downloaded_files)
            await evaluate("lastSavedDownload=''")
            await click(".msg-user .voice-player-more")
            await wait("!!document.querySelector('.voice-player-menu')")
            await edge.command("Input.dispatchKeyEvent", {"type":"keyDown","key":"ArrowDown","code":"ArrowDown","windowsVirtualKeyCode":40})
            assert await evaluate("document.activeElement.dataset.rate==='1'")
            await edge.command("Input.dispatchKeyEvent", {"type":"keyDown","key":"Escape","code":"Escape","windowsVirtualKeyCode":27})
            assert await evaluate("!document.querySelector('.voice-player-menu')&&document.activeElement.classList.contains('voice-player-more')")
            await click(".msg-user .voice-player-more")
            await click(".voice-player-menu [data-download]")
            await wait("String(lastSavedDownload).endsWith('MemuAI-voice.wav')&&document.body.textContent.includes('MemuAI-voice.wav')")
            assert len(downloaded_files) == before + 1, downloaded_files
            checks.append({"voice_download":mode, **downloaded_files[-1], "menu_keyboard":True})
            await evaluate("window.__preservedPlayer=document.querySelector('.msg-assistant audio');__preservedPlayer.currentTime=2")
            await wait("Math.abs(__preservedPlayer.currentTime-2)<.02 && !__preservedPlayer.seeking")
            await evaluate("renderMessages()")
            retained = await evaluate("({same:document.querySelector('.msg-assistant audio')===__preservedPlayer,time:document.querySelector('.msg-assistant audio').currentTime,previous:__preservedPlayer.currentTime,ready:document.querySelector('.msg-assistant audio').readyState})")
            assert retained["same"] and abs(retained["time"] - 2) < .02, ("Playback was reset by rerender", retained)
        await evaluate("window.__qa_previousFetch=window.fetch;window.__qa_delayedStateDone=false;window.fetch=(url,options)=>String(url)==='/api/pg/state'?new Promise(resolve=>setTimeout(()=>__qa_previousFetch(url,options).then(response=>{resolve(response);__qa_delayedStateDone=true}),180)):__qa_previousFetch(url,options)")
        await evaluate("(async()=>{await showSettings(false);closePanel('replace')})()")
        await wait("window.__qa_delayedStateDone")
        await asyncio.sleep(.05)
        checks.append({"settings_closed_during_state_refresh":True})
        errors = await evaluate("window.__qa_errors")
        assert not errors, errors
        (OUT / "proof.json").write_text(json.dumps({"passed": True, "checks": checks}, ensure_ascii=False, indent=2), encoding="utf-8")
        print("PASS: real full UI, short WAV playback, custom seek/menu, player retention, tools, responsive settings, light/dark")
    finally:
        await edge.close_browser()


try:
    asyncio.run(main())
finally:
    server.shutdown(); server.server_close(); temporary.cleanup()
