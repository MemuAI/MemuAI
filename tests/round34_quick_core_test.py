"""Round 34 (r34-quick-core): the core of Quick Ask — rendering of long answers, the hotkey race, reasoning levels,
pictures in follow-ups, big pictures, typing outside the field, Markdown details. No network: the page runs over a
static server with mocked /api, the endpoint over httpx.MockTransport.

    python -m pytest -q tests/round34_quick_core_test.py
"""
import asyncio
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="r34qc-"))
CHROME = "/workspace/work/chromium/chrome"
QUICK = ROOT / "app" / "pgstatic" / "quick"

def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)

class ReasoningLevelServer(unittest.TestCase):
    def test_user_effort_accepts_only_real_choices(self):
        from app.quick_ask_api import user_effort
        self.assertEqual(user_effort("off"), "off")
        self.assertEqual(user_effort("none"), "off")
        self.assertEqual(user_effort("high"), "high")
        for value in ("auto", "", None, "turbo", 3, {"x": 1}):
            self.assertEqual(user_effort(value), "")

    def test_ui_state_keeps_the_level_per_model(self):
        from app import quick_ask_api as api
        with tempfile.TemporaryDirectory() as tmp:

            with mock.patch.object(api, "_ui_file", lambda: Path(tmp) / "quick_ui.json"):
                self.assertEqual(api.load_ui_state()["efforts"], {})
                api.save_ui_state({"effort": {"model": "qwen3.8-flash", "level": "off"}})
                api.save_ui_state({"effort": {"model": "glm-5.3", "level": "max"}})
                self.assertEqual(api.load_ui_state()["efforts"], {"qwen3.8-flash": "off", "glm-5.3": "max"})
                api.save_ui_state({"effort": {"model": "glm-5.3", "level": "auto"}})
                api.save_ui_state({"effort": {"model": "x", "level": "nonsense"}})
                self.assertEqual(api.load_ui_state()["efforts"], {"qwen3.8-flash": "off"})
                self.assertTrue(api.load_ui_state()["onboarded"] in (True, False))

    def test_custom_endpoint_gets_the_picked_level_in_its_dialect(self):
        from app import quick_ask_endpoint as endpoint
        base = {"model": "qwen3.8-flash", "messages": [{"role": "user", "content": "hi"}], "thinking": False}
        plain = endpoint.openai_body(dict(base, effort="low"), stream=True, url="https://api.example.com/v1")
        self.assertEqual(set(plain), {"model", "messages", "stream"}, "no level picked → nothing extra (as before)")
        off = endpoint.openai_body(dict(base, effort="off", effort_user=True), stream=True, url="https://api.example.com/v1")
        self.assertIs(off.get("enable_thinking"), False)
        high = endpoint.openai_body(dict(base, model="gpt-6.1-sol", effort="high", effort_user=True), stream=True,
                                    url="https://api.example.com/v1")
        self.assertEqual(high.get("reasoning_effort"), "high")
        unknown = endpoint.openai_body(dict(base, model="my-local-llm", effort="high", effort_user=True), stream=True)
        self.assertEqual(set(unknown), {"model", "messages", "stream"}, "a model without levels gets no fields")

    def test_rejected_level_is_retried_without_the_fields(self):
        import httpx
        from app import quick_ask_endpoint as endpoint
        seen = []

        def handler(request):
            body = json.loads(request.content)
            seen.append(body)
            if "enable_thinking" in body:
                return httpx.Response(400, json={"error": {"message": "unknown field enable_thinking"}})
            sse = 'data: {"choices":[{"delta":{"content":"OK"}}]}\n\ndata: [DONE]\n\n'
            return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

        original = endpoint._client
        endpoint._client = lambda *a, **k: httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            payload = json.dumps({"model": "qwen3.8-flash", "messages": [], "effort": "off", "effort_user": True}).encode()

            async def collect():
                return [line async for line in endpoint.stream_lines("https://api.example.com/v1", "k", payload)]
            lines = run(collect())
        finally:
            endpoint._client = original
        self.assertEqual(len(seen), 2)
        self.assertNotIn("enable_thinking", seen[1])
        self.assertTrue(any('"OK"' in line for line in lines), lines)
        self.assertFalse(any('"error"' in line for line in lines), lines)

    def test_endpoint_rows_carry_reasoning_levels_and_the_chosen_models(self):
        from app import quick_ask_endpoint as endpoint
        rows = {r["id"]: r["caps"] for r in endpoint.model_rows(["qwen3.8-flash", "my-local-llm"])}
        self.assertIn("off", rows["qwen3.8-flash"]["reasoning_profile"]["options"])
        self.assertNotIn("reasoning_profile", rows["my-local-llm"])
        prefs = {"quick_ask_source": "custom", "quick_ask_endpoint_url": "https://nowhere.example/v1",
                 "quick_ask_endpoint_model": "qwen3.8-flash", "quick_ask_endpoint_advanced_model": "glm-5.3"}
        ids = [r["id"] for r in endpoint.public_config(prefs).get("models", [])]
        if endpoint.active(prefs):
            self.assertEqual(ids[:2], ["qwen3.8-flash", "glm-5.3"])

    def test_answer_loop_sends_the_picked_level(self):
        from app import quick_ask_api as api
        sent = []

        async def stream_lines(_path, payload):
            sent.append(json.loads(payload))
            yield 'data: {"choices":[{"delta":{"content":"Hi"}}]}'
            yield "data: [DONE]"

        async def nothing(*a, **k):
            return {}

        async def collect(settings):
            out = []
            async for item in api.ask_events(text="hello", images=[], selection="", model="m", history=[], vision=True,
                                             native_search=False, settings=settings, lang="ru", stream_lines=stream_lines,
                                             run_tool=nothing, rich_search=nothing, rich_payload=lambda d: {},
                                             capture=nothing):
                out.append(item)
            return out
        run(collect({"quick_ask_web_search": False}))
        self.assertEqual(sent[-1]["effort"], "low")
        self.assertNotIn("effort_user", sent[-1])
        run(collect({"quick_ask_web_search": False, "_quick_effort": "high"}))
        self.assertEqual(sent[-1]["effort"], "high")
        self.assertTrue(sent[-1]["effort_user"])

class HistoryPictures(unittest.TestCase):
    PIC = "data:image/png;base64,iVBORw0KGgo="

    def test_follow_up_keeps_the_picture_for_a_seeing_model(self):
        from app.quick_ask_api import build_messages
        history = [{"role": "user", "content": "что на картинке?", "images": [self.PIC]},
                   {"role": "assistant", "content": "MEMU 42"}]
        msgs = build_messages(text="а какого цвета?", history=history, images=[], system="s")
        self.assertIsInstance(msgs[1]["content"], list)
        self.assertEqual(msgs[1]["content"][1]["image_url"]["url"], self.PIC)
        self.assertEqual([m["role"] for m in msgs], ["system", "user", "assistant", "user"])
        blind = build_messages(text="а какого цвета?", history=history, images=[], system="s", history_images=False)
        self.assertEqual(blind[1]["content"], "что на картинке?")

    def test_picture_budget_and_junk(self):
        from app.quick_ask_api import build_messages, MAX_IMAGES
        history = [{"role": "user", "content": "q", "images": [self.PIC] * 6 + ["javascript:alert(1)", 5]},
                   {"role": "assistant", "content": "a"}]
        msgs = build_messages(text="next", history=history, images=[self.PIC], system="s")
        pics = [p for p in msgs[1]["content"] if p.get("type") == "image_url"]
        self.assertEqual(len(pics), MAX_IMAGES - 1)
        self.assertTrue(all(p["image_url"]["url"].startswith("data:image/") for p in pics))

class HotkeyRace(unittest.TestCase):
    def test_second_press_waits_for_the_first_and_closes(self):
        from app.quick_ask import QuickAskManager
        m = QuickAskManager.__new__(QuickAskManager)
        m.visible = False
        m.api = None
        calls = []

        def show(source="hotkey"):
            calls.append("show")
            time.sleep(0.3)
            m.visible = True
            return True

        def hide(restore_focus=True):
            calls.append("hide")
            m.visible = False
            return True
        m.show, m.hide = show, hide
        threads = [threading.Thread(target=m.toggle) for _ in range(2)]
        threads[0].start()
        time.sleep(0.05)
        threads[1].start()
        for t in threads:
            t.join(3)
        self.assertEqual(calls, ["show", "hide"])
        self.assertFalse(m.visible)

class StaticContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = (QUICK / "quick.js").read_text("utf-8")
        cls.html = (QUICK / "index.html").read_text("utf-8")

    def test_files_are_wired(self):
        self.assertIn('id="qa-effort"', self.html)
        self.assertIn("function patchHtml", self.js)
        self.assertIn("function syncTurns", self.js)
        self.assertIn("const mdCache = new WeakMap()", self.js)
        self.assertIn("effort: effortOf(", self.js)

    def test_pin_toast_follows_the_leave_setting(self):
        self.assertIn("Откреплено — Esc в любой программе закроет окно", self.js)
        self.assertIn("Unpinned — Escape in any program closes it", self.js)

    def test_cache_versions_are_bumped(self):
        html = (ROOT / "app" / "pgstatic" / "quick" / "index.html").read_text("utf-8")
        import re
        for name in ("quick.js", "quick-md.js", "quick-ux.css", "quick-models.css"):
            self.assertRegex(html, r'/quick/' + re.escape(name) + r'\?v=[\w.]+-r(?:3[4-9]|4\d)"')

    def test_tables_signature_has_no_forced_layout(self):
        tables = (ROOT / "app" / "pgstatic" / "tables.js").read_text("utf-8")
        start = tables.index("function signature(table)")
        self.assertNotIn("innerText", tables[start:tables.index("function stateOf", start)])

class QuickPageHeadless(unittest.TestCase):
    """The real quick/index.html over a static server with mocked /api."""

    @classmethod
    def setUpClass(cls):
        if not Path(CHROME).exists():
            raise unittest.SkipTest("no Chromium")
        try:
            import playwright  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("no Playwright")
        import functools
        import http.server

        class Quiet(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass

        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(ROOT / "app" / "pgstatic")))
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def run_page(self, script: str, answer: str = "Ответ **готов**.", ask_status: int = 200, ask_error: str = ""):
        from playwright.async_api import async_playwright
        from app import themes
        profile = {"kind": "levels", "levels": ["low", "medium", "high"], "can_off": True, "default": "high",
                   "options": ["auto", "off", "low", "medium", "high"]}
        config = {"prefs": {"theme": "dark", "ui_design": "classic", "language": "ru", "quick_ask_model": "m1"},
                  "status": {"native": True, "pinned": False}, "ui": {"onboarded": True, "efforts": {}}, "endpoint": {"source": "proxy"}}
        bodies = []

        async def route(route_obj, request):
            path = "/" + request.url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]
            if path == "/themes.css":
                return await route_obj.fulfill(status=200, content_type="text/css", body=themes.theme_css())
            if path == "/api/quick/config":
                return await route_obj.fulfill(json=config)
            if path == "/api/pg/state":
                return await route_obj.fulfill(json={"models": [{"id": "m1", "caps": {"vision": True, "reasoning_profile": profile}}], "image_models": []})
            if path == "/api/work/computer/state":
                return await route_obj.fulfill(json={"status": "idle", "logs": []})
            if path == "/api/quick/ask":
                bodies.append(json.loads(request.post_data or "{}"))
                if ask_error:
                    sse = "data: " + json.dumps({"event": "error", "error": ask_error, "code": "model"}) + "\n\n"
                else:
                    sse = "".join("data: " + json.dumps(e, ensure_ascii=False) + "\n\n" for e in (
                        {"event": "delta", "text": answer}, {"event": "done", "model": "m1", "tier": "fast", "ms": 10}))
                return await route_obj.fulfill(status=ask_status, content_type="text/event-stream", body=sse)
            if path == "/api/assistant/run":
                return await route_obj.fulfill(json={"card": {"fallback": True}})
            if path.startswith("/api/"):
                return await route_obj.fulfill(json={"items": [], "events": [], "seq": 0, "ok": True})
            return await route_obj.continue_()

        async def main():
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
                page = await browser.new_page(viewport={"width": 720, "height": 640})
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                await page.route("**/*", route)
                await page.goto(self.base + "/quick/?native=1", wait_until="load")
                await page.wait_for_function("window.QuickAsk && window.MemuQuick")
                await page.evaluate("QuickAsk.onShow({fresh: true, maxHeight: 640})")
                await page.wait_for_timeout(600)
                result = await page.evaluate(script)
                await browser.close()
                return result, errors
        result, errors = run(main())
        self.assertEqual(errors, [])
        return result, bodies

    ASK = """async (text) => { const n = QuickAsk.state().turns.length; const i = document.getElementById('qa-input'); i.value = text;
      i.dispatchEvent(new Event('input')); i.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
      for (let k = 0; k < 60 && (QuickAsk.state().busy || QuickAsk.state().turns.length === n); k++) await new Promise((r) => setTimeout(r, 50)); }"""

    def test_answer_dom_is_patched_not_replaced(self):
        script = """(async () => { const ask = %s; await ask('первый');
          const first = document.querySelector('.qa-turn[data-turn="0"] .qa-md p');
          const S = QuickAsk.state();
          S.turns.push({q: 'второй', a: 'Часть ответа', done: false, model: 'm1', tier: 'fast', sources: [], shots: [], images: [], status: null, startedAt: performance.now()});
          QuickAsk.render();
          const kept1 = document.querySelector('.qa-turn[data-turn="0"] .qa-md p') === first;
          const live = document.querySelector('.qa-turn[data-turn="1"] .qa-md');
          S.turns[1].a += '\\n\\nВторой абзац'; QuickAsk.render();
          const keptLive = document.querySelector('.qa-turn[data-turn="1"] .qa-md') === live;
          const carets = document.querySelectorAll('.qa-caret').length;
          const busyWhileStreaming = document.getElementById('qa-answer').getAttribute('aria-busy');
          S.turns[1].done = true; QuickAsk.render();
          return {kept1, keptLive, carets, paras: live.querySelectorAll('p').length, streaming: live.hasAttribute('data-streaming'),
                  busy: busyWhileStreaming + '/' + document.getElementById('qa-answer').getAttribute('aria-busy'),
                  stillFirst: document.querySelector('.qa-turn[data-turn="0"] .qa-md p') === first}; })()""" % self.ASK
        result, _ = self.run_page(script)
        self.assertEqual(result, {"kept1": True, "keptLive": True, "carets": 1, "paras": 2, "streaming": False, "busy": "true/false", "stillFirst": True})

    def test_reasoning_level_is_picked_shown_and_sent(self):
        script = """(async () => { const ask = %s;
          document.dispatchEvent(new KeyboardEvent('keydown', {key: 'm', code: 'KeyM', altKey: true, bubbles: true}));
          await new Promise((r) => setTimeout(r, 200));
          const levels = Array.from(document.querySelectorAll('#qa-effort-row [data-effort]')).map((b) => b.dataset.effort);
          document.querySelector('[data-effort="off"]').click();
          const on = document.querySelector('[data-effort].on').dataset.effort;
          document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
          await new Promise((r) => setTimeout(r, 100));
          const badge = document.getElementById('qa-effort'); await ask('вопрос');
          return {levels, on, badge: badge.hidden ? '' : badge.textContent}; })()""" % self.ASK
        result, bodies = self.run_page(script)
        self.assertEqual(result["levels"], ["auto", "off", "low", "medium", "high"])
        self.assertEqual(result["on"], "off")
        self.assertEqual(result["badge"], "без размышлений")
        self.assertEqual(bodies[-1]["effort"], "off")

    def test_follow_up_sends_the_picture_and_shows_it(self):
        script = """(async () => { const ask = %s; const S = QuickAsk.state();
          S.attachments.push({data: 'data:image/png;base64,iVBORw0KGgo=', label: 'x'}); await ask('что на картинке?');
          const thumbs = document.querySelectorAll('.qa-q-pics img').length; await ask('а какого цвета?'); return thumbs; })()""" % self.ASK
        thumbs, bodies = self.run_page(script)
        self.assertEqual(thumbs, 1)
        self.assertEqual(bodies[-1]["history"][0]["images"], ["data:image/png;base64,iVBORw0KGgo="])
        self.assertNotIn("images", bodies[-1]["history"][1])
        self.assertNotIn("turn", bodies[-1]["history"][0])

    def test_typing_and_paste_outside_the_field(self):
        script = """(async () => { const ask = %s; await ask('привет');
          const input = document.getElementById('qa-input'); input.blur(); document.body.focus();
          const ev = new KeyboardEvent('keydown', {key: 'z', bubbles: true, cancelable: true}); document.body.dispatchEvent(ev);
          const focused = document.activeElement === input; input.blur();
          const dt = new DataTransfer(); dt.setData('text/plain', 'вставка');
          document.body.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
          return {focused, value: input.value}; })()""" % self.ASK
        result, _ = self.run_page(script)
        self.assertEqual(result, {"focused": True, "value": "вставка"})

    def test_big_picture_is_scaled_down(self):
        script = """(async () => { const c = document.createElement('canvas'); c.width = 4200; c.height = 2400;
          const x = c.getContext('2d'); for (let i = 0; i < 3000; i++) { x.fillStyle = `hsl(${i % 360},80%,50%)`; x.fillRect(Math.random()*4200, Math.random()*2400, 80, 80); }
          const blob = await new Promise((r) => c.toBlob(r, 'image/png'));
          const dt = new DataTransfer(); dt.items.add(new File([blob], 'big.png', {type: 'image/png'}));
          document.getElementById('qa-input').dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
          for (let k = 0; k < 60 && !QuickAsk.state().attachments.length; k++) await new Promise((r) => setTimeout(r, 100));
          const data = QuickAsk.state().attachments[0].data; const img = new Image(); img.src = data; await img.decode();
          return {scaled: /^data:image\/(jpeg|png)/.test(data), w: img.naturalWidth, h: img.naturalHeight}; })()"""
        result, _ = self.run_page(script)

        self.assertEqual(result, {"scaled": True, "w": 2048, "h": 1170})

    def test_dropped_stream_says_so(self):
        """Round 34 (r34-quick-core-2): a stream that ends without «done» or dies on the network is not a complete answer."""
        script = """(async () => { const ask = %s; const orig = window.fetch; let mode = '';
          const enc = new TextEncoder();
          window.fetch = (u, o) => {
            if (!String(u).includes('/api/quick/ask')) return orig(u, o);
            if (mode === 'offline') return Promise.reject(new TypeError('Failed to fetch'));
            return Promise.resolve(new Response(new ReadableStream({ start(c) {
              c.enqueue(enc.encode('data: {"event":"delta","text":"Половина ответа"}\\n\\n'));
              if (mode === 'closed') c.close(); else setTimeout(() => c.error(new TypeError('network error')), 60); } }),
              { headers: { 'Content-Type': 'text/event-stream' } }));
          };
          const out = {}; const text = () => document.getElementById('qa-answer').textContent + document.getElementById('qa-actions').textContent;
          mode = 'closed'; await ask('один'); out.closed = text().includes('ответ оборвался') && text().includes('Половина ответа');
          mode = 'reset'; await ask('два'); out.reset = text().includes('ответ оборвался') && !text().includes('network error');
          mode = 'offline'; await ask('три'); out.offline = text().includes('Связь с MemuAI прервалась') && !text().includes('Failed to fetch');
          return out; })()""" % self.ASK
        result, _ = self.run_page(script)
        self.assertEqual(result, {"closed": True, "reset": True, "offline": True})

    def test_long_paste_becomes_the_text_chip(self):
        script = r"""(async () => { const i = document.getElementById('qa-input'); i.focus();
          const paste = (text) => { const dt = new DataTransfer(); dt.setData('text/plain', text);
            i.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true})); };
          paste('короткий'); const short = QuickAsk.state().selection.length;
          paste('Строка документа 🙂 مرحبا. '.repeat(3000)); await new Promise((r) => setTimeout(r, 100));
          return {short, field: i.value.length, chip: QuickAsk.state().selection.length > 60000,
                  toast: document.getElementById('qa-toast').textContent.includes('середина будет сокращена')}; })()"""
        result, _ = self.run_page(script)
        self.assertEqual(result, {"short": 0, "field": 0, "chip": True, "toast": True})

    def test_open_in_memuai_keeps_picture_types_and_the_answering_model(self):
        script = r"""(async () => { const S = QuickAsk.state(); let sent = null; const orig = window.fetch;
          window.fetch = (u, o) => { if (String(u).includes('/api/quick/window')) { sent = JSON.parse(o.body); return Promise.resolve(new Response('{"ok":true}')); } return orig(u, o); };
          S.turns.push({q: 'что тут?', a: 'Кот', done: true, model: 'm2', tier: 'advanced', sources: [], shots: [], ms: 5,
                        images: ['data:image/png;base64,iVBORw0KGgo=', 'data:image/jpeg;base64,/9j/4AA=']});
          QuickAsk.render();
          document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', ctrlKey: true, shiftKey: true, bubbles: true}));
          for (let k = 0; k < 40 && !sent; k++) await new Promise((r) => setTimeout(r, 50));
          const h = sent && sent.handoff; const a = h ? h.messages[0].attachments : [];
          return {action: sent && sent.action, model: h && h.model, att: a.map((x) => x.name + ' ' + x.type)}; })()"""
        result, _ = self.run_page(script)
        self.assertEqual(result, {"action": "handoff", "model": "m2", "att": ["image-1.png image/png", "image-2.jpg image/jpeg"]})

    def test_stopped_and_key_errors_say_so(self):
        script = """(async () => { const S = QuickAsk.state();
          S.turns.push({q: 'q', a: 'полови', done: true, stopped: true, model: 'm1', tier: 'fast', sources: [], shots: [], images: [], ms: 900});
          S.turns.push({q: 'q2', a: '', done: true, error: 'Эндпоинт не принял API-ключ (HTTP 401).', errorCode: 'model', model: 'm1', sources: [], shots: [], images: []});
          QuickAsk.render();
          return {stopped: document.getElementById('qa-answer').textContent.includes('остановлено'), settings: !!document.querySelector('[data-err="settings"]')}; })()"""
        result, _ = self.run_page(script)
        self.assertEqual(result, {"stopped": True, "settings": True})

    def test_markdown_details(self):
        script = r"""(() => { const md = QuickMarkdown.render;
          const fence = md('1. Шаг:\n   ```bash\n   pip install x\n   ```\n2. Дальше');
          const box = document.createElement('div'); box.innerHTML = fence;
          const code = box.querySelector('pre code').textContent;
          const link = md('[док](https://e.com/a_(b)) и https://e.com/p_(x).');
          const tasks = md('- [ ] надо\n- [x] сделано');
          return {code, link: (link.match(/href="([^"]+)"/g) || []), tasks: (tasks.match(/md-task/g) || []).length, box: tasks.includes('☐') && tasks.includes('☑')}; })()"""
        result, _ = self.run_page(script)
        self.assertEqual(result["code"], "pip install x")
        self.assertEqual(result["link"], ['href="https://e.com/a_(b)"', 'href="https://e.com/p_(x)"'])
        self.assertEqual(result["tasks"], 2)
        self.assertTrue(result["box"])

if __name__ == "__main__":
    unittest.main()
