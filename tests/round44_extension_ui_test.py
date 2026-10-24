"""Offline Chromium regression harness for the unpacked extension panel.

The real HTML/CSS/ES modules run against a temporary loopback HTTP fixture,
mock Chrome APIs and generated files. No installed extension, real Chrome
profile, Telegram account or running MemuAI instance is accessed.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "app" / "pgstatic" / "browser-extension"
SHOTS = ROOT / "build" / "round44-ui"
PREFIX = "/api/pg/browser-extension/bridge/"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWQAAAABJRU5ErkJggg==")
MODEL = "api-claude-opus-5.5"
MARKDOWN = """# Готово

**Жирный текст** и *курсив*, `inline code`.

- Первый пункт
- Второй пункт

| Модель | Результат |
| --- | --- |
| Claude | 42 |

```python
print("safe <script>")
```

<img src=x onerror="window.__injected=true">
[опасная ссылка](javascript:window.__injected=true)
[Документация](https://example.com/docs)
"""


def chromium():
    return next((str(p) for p in [Path(os.environ.get("MEMU_TEST_CHROMIUM", "")),
                  Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
                  Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"), Path("/usr/bin/chromium")]
                 if p.is_file()), "")


class PanelFixture:
    def __init__(self):
        self.lock = threading.Lock()
        self.calls = []
        self.generated = None
        self.stream_events = None
        self.hold_stream = False
        self.resume_stream = threading.Event()
        self.tool_activity = []
        self.default_model = MODEL
        self.pinned_models = []
        self.settings = {"read_pages": True, "control": "ask", "dfy": False,
                         "schedules": False, "save_to_main": False}
        self.models = [{"id": MODEL, "name": "Claude Opus 5.5", "provider": "custom", "prefix": "api",
                        "enabled": True, "caps": {"text": True, "vision": True}}]
        self.models += [{"id": f"api-model-{i:03d}", "name": f"Модель для длинного каталога {i:03d}",
                         "provider": "custom", "prefix": "api", "enabled": True, "caps": {"text": True}}
                        for i in range(45)]
        self.models.append({"id":"api-image-only","name":"Image generator","caps":{"image_generation":True}})
        self.chats = [{"id":"chat-personal", "title":"Разговор из MemuAI", "model":MODEL,"updated":1700000000000,
                       "count":2,"mode":"chat","project_id":""},
                      {"id":"chat-project", "title":"Проект: важный документ", "model":MODEL,"updated":1700000001000,
                       "count":2,"mode":"chat","project_id":"project-one"}]
        self.chats += [{"id":f"history-{i}","title":f"История разговора {i:02d}","model":MODEL,
                        "updated":1700000000000-i*1000,"count":2,"mode":"chat","project_id":""}
                       for i in range(20)]

    def reply(self, method, target, body):
        parsed = urlsplit(target)
        path = parsed.path.removeprefix(PREFIX)
        with self.lock:
            self.calls.append((method,path,body))
        if path == "models":
            return {"models":self.models,"default_model":self.default_model,"pinned_models":self.pinned_models,
                    "profile_id":"default"}
        if path == "settings":
            if method == "POST":
                self.settings.update(body)
            return {"settings":self.settings,"profile_id":"default"}
        if path == "model-preferences":
            if "default_model" in body:
                self.default_model = body["default_model"]
            if "pinned_models" in body:
                self.pinned_models = body["pinned_models"]
            if "pin_model" in body:
                self.pinned_models = [v for v in self.pinned_models if v != body["pin_model"]]
                if body.get("pinned"):
                    self.pinned_models.append(body["pin_model"])
            return {"default_model":self.default_model,"pinned_models":self.pinned_models,"profile_id":"default"}
        if path == "revoke":
            return {"ok":True}
        if path == "chats":
            chat_id = parse_qs(parsed.query).get("id", [""])[0]
            if chat_id:
                chat = next((dict(c) for c in self.chats if c["id"]==chat_id),dict(self.chats[0]))
                chat["messages"] = [{"role":"user","content":"Привет из основного приложения"},
                                    {"role":"assistant","content":"Ответ из общего чата MemuAI."}]
                if chat_id == "browser-new" and self.generated:
                    chat["messages"] = [{"role":"user","content":self.generated.get("text", "")},
                                        {"role":"assistant","content":MARKDOWN,"tool_activity":self.tool_activity}]
                return {"chat":chat}
            return {"chats":self.chats,"profile_id":"default","profile_name":"Основной профиль"}
        if path == "attachment":
            name = body.get("name", "file.txt")
            is_image = name.lower().endswith((".png",".jpg",".webp"))
            return {"id":"attachment-"+name,"name":name,"size":len(body.get("data", "")),"isImg":is_image,
                    "preview":body.get("data", "") if is_image else "", "truncated":False}
        if path == "answer":
            self.resume_stream.set()
            return {"ok":True}
        if path == "pair":
            return {"token":"new-offline-token"} if body.get("code")=="ABC12345" else {"error":"Код подключения недействителен или истёк"}
        if path == "chat":
            self.generated = dict(body)
            if not any(chat["id"] == "browser-new" for chat in self.chats):
                self.chats.insert(0,{"id":"browser-new","title":body.get("text", "Новый чат"),"model":MODEL,
                                     "updated":int(time.time()*1000),"count":2,"mode":"chat","project_id":"",
                                     "browser_only":not self.settings["save_to_main"]})
        return {"ok":True}


@pytest.fixture(scope="module")
def panel_server():
    fixture = PanelFixture()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def send_bytes(self, data, content_type):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length",str(len(data)))
            self.send_header("Cache-Control","no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.startswith(PREFIX):
                data = fixture.reply("GET",self.path,{})
                self.send_bytes(json.dumps(data,ensure_ascii=False).encode(),"application/json")
                return
            relative = urlsplit(self.path).path.removeprefix("/")
            path = (EXTENSION / relative).resolve()
            if not path.is_relative_to(EXTENSION.resolve()) or not path.is_file():
                self.send_error(404)
                return
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if path.suffix == ".js":
                content_type = "text/javascript"
            self.send_bytes(path.read_bytes(),content_type)

        def do_POST(self):
            if not self.path.startswith(PREFIX):
                self.send_error(404)
                return
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length",0))) or b"{}")
            data = fixture.reply("POST",self.path,body)
            if urlsplit(self.path).path.endswith("/chat"):
                events = fixture.stream_events or [{"event":"chat","id":"browser-new"},{"event":"token","text":"Первоначальный текст"},
                          {"event":"replace","text":MARKDOWN},{"event":"done","id":"browser-new"}]
                stream = "".join("data: "+json.dumps(event,ensure_ascii=False)+"\n\n" for event in events).encode()
                # Split frames, JSON and multi-byte Cyrillic across real HTTP chunks.
                self.send_response(200)
                self.send_header("Content-Type","text/event-stream; charset=utf-8")
                self.send_header("Transfer-Encoding","chunked")
                self.send_header("Cache-Control","no-store")
                self.end_headers()
                try:
                    for start in range(0,len(stream),23):
                        chunk = stream[start:start+23]
                        self.wfile.write(f"{len(chunk):x}\r\n".encode()+chunk+b"\r\n")
                        self.wfile.flush()
                        time.sleep(.001)
                    if fixture.hold_stream:
                        fixture.resume_stream.wait(10)
                        final = ("data: "+json.dumps({"event":"tool","id":"close-batch","tool":"chrome_close_tabs",
                                 "phase":"done","title":"Закрытие вкладок GitHub","detail":"Закрыты 2 вкладки"},ensure_ascii=False)
                                 +"\n\ndata: "+json.dumps({"event":"token","text":"Готово"},ensure_ascii=False)
                                 +'\n\ndata: {"event":"done","id":"browser-new"}\n\n').encode()
                        self.wfile.write(f"{len(final):x}\r\n".encode()+final+b"\r\n")
                        self.wfile.flush()
                    self.wfile.write(b"0\r\n\r\n")
                except (BrokenPipeError,ConnectionResetError):
                    pass
                return
            self.send_bytes(json.dumps(data,ensure_ascii=False).encode(),"application/json")

        def log_message(self,*args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1",0),Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    fixture.base = f"http://127.0.0.1:{server.server_port}"
    yield fixture
    server.shutdown()
    server.server_close()
    thread.join(5)


CHROME_MOCK = """({base,model}) => {
  const storage={base,token:'offline-test-token',model,favorites:[],recentModels:[],lastChat:'',lastProfile:'default'};
  const listeners=[];
  window.__storage=storage;window.__browserCalls=[];window.__storageListeners=listeners;
  window.chrome={
    storage:{local:{get:async defaults=>({...defaults,...storage}),set:async values=>{Object.assign(storage,values);}},
      onChanged:{addListener:f=>listeners.push(f),removeListener:()=>{}}},
    runtime:{id:'abcdefghijklmnopabcdefghijklmnop',openOptionsPage:async()=>window.__browserCalls.push('options'),
      sendMessage:async payload=>{window.__browserCalls.push(payload);return {ok:true};}},
    tabs:{query:async()=>[{id:7,title:'Тестовая страница с длинным названием',url:'https://example.com/page'}],
      create:async payload=>window.__browserCalls.push(payload)},
    permissions:{contains:async()=>window.__sitesAllowed!==false,request:async()=>{window.__sitesAllowed=true;return true;},remove:async()=>{window.__sitesAllowed=false;return true;}},
    sidePanel:{getLayout:async()=>({side:'right'})}
  };
  chrome.storage.local.remove=async keys=>{for(const key of Array.isArray(keys)?keys:[keys])delete storage[key];};
  Object.defineProperty(navigator,'clipboard',{value:{writeText:async value=>{window.__copied=value;}},configurable:true});
}"""


@pytest.fixture(scope="module")
def browser():
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    executable = chromium()
    if not executable:
        pytest.skip("headless Chromium not available")
    SHOTS.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as pw:
        instance = pw.chromium.launch(executable_path=executable,headless=True,
                                      args=["--no-sandbox","--disable-background-networking"])
        yield instance
        instance.close()


@pytest.fixture
def panel(browser,panel_server):
    panel_server.default_model = MODEL
    panel_server.pinned_models = []
    panel_server.stream_events = None
    panel_server.hold_stream = False
    panel_server.resume_stream.clear()
    panel_server.tool_activity = []
    context = browser.new_context(viewport={"width":360,"height":800},color_scheme="light")
    context.add_init_script("("+CHROME_MOCK+")("+json.dumps({"base":panel_server.base,"model":MODEL})+")")
    page = context.new_page()
    page.__errors = []
    page.on("pageerror",lambda exc:page.__errors.append(str(exc)))
    page.goto(panel_server.base+"/panel.html")
    page.wait_for_function("document.querySelector('#model-label')?.textContent.includes('Claude')")
    yield page,panel_server
    context.close()


def layout(page, *, composer=True):
    result = page.evaluate("""() => {
      const rect=e=>e.getBoundingClientRect().toJSON(), c=document.querySelector('#composer'), send=document.querySelector('#send');
      return {width:innerWidth,height:innerHeight,body:rect(document.body),documentWidth:document.documentElement.scrollWidth,
        documentHeight:document.documentElement.scrollHeight, composer:rect(c), composerHidden:c.hidden,send:rect(send),
        scrollbar:getComputedStyle(document.documentElement).scrollbarWidth,
        scrollers:[...document.querySelectorAll('*')].filter(e=>{const s=getComputedStyle(e);return /(auto|scroll)/.test(s.overflow+s.overflowX+s.overflowY)}).map(e=>({id:e.id,width:getComputedStyle(e).scrollbarWidth}))};
    }""")
    assert result["documentWidth"] <= result["width"]+1,result
    assert result["documentHeight"] <= result["height"]+1,result
    assert result["scrollbar"]=="none",result
    assert all(row["width"]=="none" for row in result["scrollers"]),result
    if composer:
        assert not result["composerHidden"],result
        assert result["composer"]["y"]>=0,result
        assert result["composer"]["bottom"]<=result["height"]+1,result
        assert result["send"]["right"]<=result["width"]+1,result


@pytest.mark.parametrize("scheme",["light","dark"])
@pytest.mark.parametrize("width,height",[(280,480),(280,800),(360,480),(360,800),(520,480),(520,800)])
def test_panel_layout_in_narrow_and_short_sidebar(panel,scheme,width,height):
    page,_=panel
    page.emulate_media(color_scheme=scheme)
    page.set_viewport_size({"width":width,"height":height})
    layout(page)
    page.screenshot(path=str(SHOTS/f"extension-{scheme}-{width}x{height}.png"))


def test_model_search_favorites_and_long_catalogue(panel):
    page,_=panel
    page.locator("#model-trigger").click()
    page.locator("#model-dialog").wait_for(state="visible")
    page.locator("#model-search").fill("Claude")
    assert page.locator("#model-list .model-row").count()==1
    page.locator("#model-list .favorite").first.click()
    page.wait_for_function("window.__storage.favorites.includes('api-claude-opus-5.5')")
    assert MODEL in page.evaluate("window.__storage.favorites")
    page.locator("#model-search").fill("")
    page.locator("#favorite-models").click()
    assert page.locator("#model-list .model-row").count()==1
    page.locator("#all-models").click()
    assert page.locator("#model-list .model-row").count()>=40
    assert "Image generator" not in page.locator("#model-list").inner_text()
    layout(page)
    page.screenshot(path=str(SHOTS/"extension-model-picker.png"))
    page.locator("#model-search").fill("каталога 005")
    page.locator("#model-list .model-row").first.locator("button").first.click()
    page.wait_for_function("document.querySelector('#model-label').textContent.includes('005')")
    assert "005" in page.locator("#model-label").inner_text()
    assert "api-model-005" in page.evaluate("window.__storage.recentModels")


def test_shared_chat_history_and_project_readonly(panel):
    page,_=panel
    page.locator("#history").click()
    page.locator("#history-dialog").wait_for(state="visible")
    page.locator("#history-main").click()
    assert "Основной профиль" in page.locator("#profile-name").inner_text()
    page.locator("#chat-search").fill("Разговор из MemuAI")
    page.locator("#chat-list .chat-row").first.locator("button").first.click()
    page.get_by_text("Привет из основного приложения",exact=True).wait_for()
    assert "Ответ из общего чата MemuAI." in page.locator("#messages").inner_text()
    page.locator("#history").click()
    page.locator("#chat-search").fill("Проект:")
    page.locator("#chat-list .chat-row").first.locator("button").first.click()
    page.locator("#readonly").wait_for(state="visible")
    assert page.locator("#composer").is_hidden() or page.locator("#input").is_disabled()
    page.screenshot(path=str(SHOTS/"extension-project-history.png"))
    page.locator("#new").click()
    assert page.locator("#readonly").is_hidden()
    layout(page)


def test_files_images_streaming_and_safe_markdown(panel):
    page,fixture=panel
    page.locator("#file-input").set_input_files([
        {"name":"notes.txt","mimeType":"text/plain","buffer":"Тестовый файл".encode()},
        {"name":"picture.png","mimeType":"image/png","buffer":PNG}])
    page.wait_for_function("document.querySelectorAll('#attachments .attachment').length===2")
    page.wait_for_function("[...document.querySelectorAll('#attachments .attachment')].every(e=>!e.classList.contains('uploading'))")
    page.locator("#input").fill("Разбери вложения")
    page.locator("#send").click()
    try:
        page.locator("#messages .assistant .content h1").wait_for(timeout=10000)
    except Exception:
        page.screenshot(path=str(SHOTS/"extension-stream-failure.png"))
        with fixture.lock:
            calls=[(m,p,b.get('name') or b.get('text') or '') for m,p,b in fixture.calls]
        pytest.fail(str({"text":page.locator('body').inner_text(),"errors":page.__errors,"calls":calls}))
    assert page.locator("#messages .assistant .content strong").inner_text()=="Жирный текст"
    assert page.locator("#messages .assistant .content table").count()==1
    assert page.locator("#messages .assistant .content pre code").inner_text()=='print("safe <script>")'
    assert page.locator("#messages .assistant .content img").count()==0
    assert page.evaluate("window.__injected===undefined")
    assert page.locator('#messages .assistant .content a[href^="javascript:"]').count()==0
    link=page.locator('#messages .assistant .content a[href="https://example.com/docs"]')
    assert "noopener" in link.get_attribute("rel")
    with fixture.lock:
        request=next(row[2] for row in reversed(fixture.calls) if row[0]=="POST" and row[1]=="chat")
    assert set(request["attachments"])=={"attachment-notes.txt","attachment-picture.png"},request
    assert "Первоначальный текст" not in page.locator("#messages .assistant").inner_text()
    for width in [280,360,520]:
        page.set_viewport_size({"width":width,"height":480})
        layout(page)
    page.set_viewport_size({"width":360,"height":800})
    page.screenshot(path=str(SHOTS/"extension-chat-with-files.png"))
    assert not page.__errors,page.__errors


def test_incomplete_stream_retains_partial_answer_and_retry(panel):
    page,_=panel
    page.route("**/bridge/chat",lambda route:route.fulfill(status=200,
        content_type="text/event-stream",body='data: '+json.dumps({"event":"token","text":"Неполный ответ"},ensure_ascii=False)+'\n\n'))
    page.locator("#input").fill("Верни мой запрос")
    page.locator("#send").click()
    page.get_by_role("button",name="Вернуть запрос в поле ввода").wait_for(timeout=5000)
    assert "Неполный ответ" in page.locator("#messages .assistant").inner_text()
    assert "прерван" in page.locator("#status").inner_text().lower()
    page.get_by_role("button",name="Вернуть запрос в поле ввода").click()
    assert page.locator("#input").input_value()=="Верни мой запрос"


def test_new_chat_drops_entire_pending_upload_batch(panel):
    page,_=panel
    pending=[]
    page.route("**/bridge/attachment",lambda route:pending.append(route))
    page.locator("#file-input").set_input_files([
        {"name":"old-one.txt","mimeType":"text/plain","buffer":b"first"},
        {"name":"old-two.txt","mimeType":"text/plain","buffer":b"second"}])
    page.wait_for_timeout(50)
    assert len(pending)==1
    page.locator("#new").click()
    pending[0].fulfill(status=200,content_type="application/json",body=json.dumps({
        "id":"old-upload","name":"old-one.txt","size":5,"isImg":False}))
    page.wait_for_timeout(150)
    assert page.locator("#attachments .attachment").count()==0
    assert len(pending)==1,"A cancelled batch uploaded another file into the new chat"


def test_token_change_while_reading_active_tab_drops_old_prompt(panel):
    page,_=panel
    page.locator("#page").click()
    page.wait_for_function("document.querySelector('#page').getAttribute('aria-pressed')==='true'")
    page.evaluate("""() => {
      chrome.tabs.query=()=>new Promise(resolve=>window.__releaseTab=()=>resolve([{id:7}]));
      const realFetch=window.fetch;window.__sentPrompts=[];
      window.fetch=(url,options)=>{if(url.endsWith('/bridge/chat'))window.__sentPrompts.push(JSON.parse(options.body));return realFetch(url,options);};
    }""")
    page.locator("#input").fill("Личный вопрос прежнего подключения")
    page.locator("#send").click()
    page.wait_for_function("!!window.__releaseTab")
    page.evaluate("""() => {
      window.__storage.token='another-owner';
      for(const listener of window.__storageListeners)listener({token:{oldValue:'offline-test-token',newValue:'another-owner'}},'local');
      window.__releaseTab();
    }""")
    page.wait_for_function("document.querySelector('#stop').hidden")
    assert page.evaluate("window.__sentPrompts")==[]
    assert "Личный вопрос" not in page.locator("#messages").inner_text()


def test_delayed_chat_load_cannot_replace_new_answer(panel):
    page,_=panel
    pending=[];sends=[]
    page.route("**/bridge/chats?id=**",lambda route:pending.append(route))
    page.route("**/bridge/chat",lambda route:sends.append(route))
    page.locator("#history").click()
    page.locator("#history-main").click()
    page.locator("#chat-search").fill("Разговор из MemuAI")
    page.locator("#chat-list .chat-row button").first.click()
    page.wait_for_timeout(50)
    assert len(pending)==1
    page.keyboard.press("Escape")
    page.locator("#input").fill("Ответ в новом чате")
    page.locator("#send").click()
    page.wait_for_timeout(50)
    assert len(sends)==1
    pending[0].fulfill(status=200,content_type="application/json",body=json.dumps({"chat":{
        "id":"chat-personal","title":"Запоздалый чат","messages":[{"role":"user","content":"Запоздалый текст"}]}}))
    page.wait_for_timeout(150)
    assert "Запоздалый текст" not in page.locator("#messages").inner_text()
    assert page.locator("#chat-title").inner_text()!="Запоздалый чат"
    sends[0].fulfill(status=200,content_type="text/event-stream",body='data: {"event":"done"}\n\n')


@pytest.mark.parametrize("scheme,width",[("light",280),("dark",360),("light",520)])
def test_options_layout_and_setup_guidance(browser,panel_server,scheme,width):
    context=browser.new_context(viewport={"width":width,"height":640},color_scheme=scheme)
    context.add_init_script("("+CHROME_MOCK+")("+json.dumps({"base":panel_server.base,"model":MODEL})+")")
    page=context.new_page()
    errors=[]
    page.on("pageerror",lambda exc:errors.append(str(exc)))
    page.goto(panel_server.base+"/options.html")
    page.get_by_text("Подключено · Основной профиль",exact=False).wait_for()
    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
    assert "Панель уже расположена справа" in page.locator("#layout-status").inner_text()
    assert "Сопрячь" not in page.locator("body").inner_text()
    assert page.evaluate("getComputedStyle(document.documentElement).scrollbarWidth")=="none"
    page.screenshot(path=str(SHOTS/f"extension-options-{scheme}-{width}.png"),full_page=True)
    assert not errors,errors
    context.close()


def test_options_pairing_permissions_and_disconnect(browser,panel_server):
    context=browser.new_context(viewport={"width":700,"height":900})
    context.add_init_script("("+CHROME_MOCK+")("+json.dumps({"base":panel_server.base,"model":MODEL})+")")
    page=context.new_page()
    page.goto(panel_server.base+"/options.html")
    page.get_by_text("Подключено · Основной профиль",exact=False).wait_for()
    page.locator("#remove-sites").click()
    assert page.locator("#sites").is_enabled()
    page.locator("#sites").click()
    assert page.locator("#sites").is_disabled()
    page.locator("#right-panel").click()
    assert any(call.get("url")=="chrome://settings/appearance" for call in page.evaluate("window.__browserCalls") if isinstance(call,dict))
    page.locator(".manual-connection summary").click()
    page.locator("#code").fill("BAD")
    page.locator("#connect").click()
    page.get_by_text("Код подключения недействителен или истёк",exact=True).wait_for()
    page.locator("#code").fill("ABC1 2345")
    page.locator("#connect").click()
    page.get_by_text("Подключено · Основной профиль",exact=False).wait_for()
    assert page.evaluate("window.__storage.token")=="new-offline-token"
    assert page.locator("#code").input_value()==""
    page.locator("#disconnect").click()
    page.wait_for_function("window.__storage.token===undefined")
    assert page.evaluate("window.__storage.token===undefined")
    assert "Подключение удалено" in page.locator("#status").inner_text()
    context.close()
