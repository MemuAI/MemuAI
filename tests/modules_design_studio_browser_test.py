"""Round 21: MemuAI Design and MemuAI Studio in headless Chromium.

The shipped module pages (modules_host.module_document) run inside a small shell
page that answers the bridge with fixtures, the same protocol MemuAI uses:
`memu-module` calls -> `memu-module-result`, streams -> `memu-module-event`.
No network, no provider, no keys.

Design: live preview while the answer streams, generation stages, version
history (undo / redo / any version, legacy migration), stop, truncated answers,
module Esc handling (preventDefault so the host does not collapse its sheet).
Studio: session history across batches, stop (abandon) for images, prompt
enhancement with undo, side panel Esc, drag & drop references.

Skipped when Playwright or a Chromium build is not available
(MEMU_TEST_CHROMIUM may point at a Chromium binary).
"""
from __future__ import annotations

import http.server
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
# Run alone (python -m unittest tests.modules_design_studio_browser_test) the
# isolation module is not loaded: never read overrides from a real profile.
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="memuai-design-studio-"))


def _chromium() -> str:
    for candidate in (os.environ.get("MEMU_TEST_CHROMIUM", ""), "/usr/bin/chromium", "/workspace/work/chromium/chrome",
                      shutil.which("chromium") or "", shutil.which("chromium-browser") or ""):
        if candidate and Path(candidate).exists():
            return candidate
    return ""


# 1x1 PNG, a real decodable image.
PNG = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")

DOC = ("<!DOCTYPE html><html lang=\"ru\"><head><meta charset=\"utf-8\"><title>{title}</title>"
       "<style>:root{{--a:#c2410c}} body{{margin:0;font:16px system-ui}} h1{{color:var(--a)}}"
       " .card{{padding:24px;border-radius:12px;background:#fff7ed}}</style></head>"
       "<body><header><h1>{title}</h1></header><main><section class=\"card\"><p>{body}</p></section>"
       "<section class=\"card\"><p>Второй блок с настоящим текстом про кофейню у моря.</p></section></main>"
       "<script>document.body.dataset.ready='1'</script></body></html>")

SHELL = """<!doctype html><html><head><meta charset="utf-8"></head>
<body style="margin:0;overflow:hidden"><iframe id="module" style="border:0;width:100vw;height:100vh;display:block"></iframe>
<script>
window.calls = []; window.saved = []; window.moduleErrors = []; window.keys = [];
window.cfg = { streamChunks: [], streamEvery: 120, imageDelay: 300, requestText: "Уточнённое описание: кофейня у моря на закате, тёплый свет" };
const streams = {};
const reply = (source, id, result, error) => source.postMessage({ type: 'memu-module-result', id, result, error: error || '' }, '*');
window.addEventListener('message', async (event) => {
  const m = event.data || {};
  if (m.type === 'memu-module-key') { keys.push(m.key); return; }
  if (m.type !== 'memu-module') return;
  const p = m.payload || {};
  calls.push({ action: m.action, payload: p, at: Date.now() });
  const src = event.source;
  if (m.action === 'stream') {
    const chunks = cfg.streamChunks.slice();
    const state = streams[m.id] = { cancelled: false };
    let text = '';
    for (const chunk of chunks) {
      await new Promise((r) => setTimeout(r, cfg.streamEvery));
      if (state.cancelled) return;
      text += chunk;
      src.postMessage({ type: 'memu-module-event', id: m.id, event: 'delta', delta: chunk }, '*');
    }
    if (state.cancelled) return;
    src.postMessage({ type: 'memu-module-event', id: m.id, event: 'done', result: { text } }, '*');
    return;
  }
  if (m.action === 'cancel') {
    const state = streams[p.id];
    if (state) { state.cancelled = true; src.postMessage({ type: 'memu-module-event', id: p.id, event: 'error', error: 'Остановлено' }, '*'); }
    return reply(src, m.id, { ok: true, cancelled: !!state });
  }
  let result = { ok: true };
  if (m.action === 'models') result = [
    { id: 'fixture-image', name: 'Fixture image', caps: { image: true, text: false } },
    { id: 'fixture-chat', name: 'Fixture chat', caps: { text: true } }];
  if (m.action === 'save') { saved.push(p); result = p; }
  if (m.action === 'image') {
    await new Promise((r) => setTimeout(r, cfg.imageDelay));
    result = { urls: [__PNG__], model: p.model };
  }
  if (m.action === 'request') result = { text: cfg.requestText };
  if (m.action === 'music') result = p.action === 'models' ? { models: [] } : { ok: true };
  if (m.action === 'log' && p.level === 'error') moduleErrors.push(p);
  reply(src, m.id, result);
});
const q = new URLSearchParams(location.search);
document.querySelector('iframe').src = '/module/' + q.get('module') + '?' + q.toString();
</script></body></html>""".replace("__PNG__", json.dumps(PNG))


class _Handler(http.server.BaseHTTPRequestHandler):
    saved_by_module: dict = {}

    def do_GET(self):  # noqa: N802
        from app import modules_host
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        if url.path.startswith("/module/"):
            identity = url.path.rsplit("/", 1)[1]
            package = modules_host.load_package(modules_host.default_root(), identity)
            saved = json.loads(query.get("saved", ["{}"])[0])
            page = modules_host.module_document(package, saved, env={
                "theme": query.get("theme", ["light"])[0], "lang": query.get("lang", ["ru"])[0]})
        else:
            page = SHELL
        payload = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


# The preview is double-buffered: the visible document is the front iframe.
FRONT = "document.querySelector('.ds-frames iframe.front')"


def _doc(title: str, body: str = "Тёплая кофейня у моря с настоящими текстами и крупной типографикой.") -> str:
    return DOC.format(title=title, body=body)


def _chunks(text: str, parts: int = 14):
    size = max(1, len(text) // parts + 1)
    return [text[i:i + size] for i in range(0, len(text), size)]


@unittest.skipUnless(_chromium(), "Chromium is not installed")
class DesignStudioBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # pragma: no cover
            raise unittest.SkipTest(f"playwright unavailable: {exc}")
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()
        cls.server.shutdown()
        cls.server.server_close()

    # ------------------------------------------------------------- helpers
    def open(self, module: str, saved=None, size=(1360, 860), theme="light"):
        from urllib.parse import urlencode
        page = self.browser.new_page(viewport={"width": size[0], "height": size[1]})
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        query = urlencode({"module": module, "theme": theme, "lang": "ru", "saved": json.dumps(saved or {})})
        page.goto(f"http://127.0.0.1:{self.server.server_port}/?{query}")
        deadline = time.time() + 20
        frame = None
        while time.time() < deadline:
            frame = next((f for f in page.frames if f"/module/{module}" in f.url), None)
            if frame and frame.evaluate("() => document.readyState === 'complete' && !!document.querySelector('#app')"):
                break
            page.wait_for_timeout(50)
        self.assertIsNotNone(frame)
        page.wait_for_timeout(300)
        self.addCleanup(page.close)
        self.addCleanup(lambda: self.assertEqual(errors + page.evaluate("moduleErrors.map(e => e.text || JSON.stringify(e))"), []))
        return page, frame

    def wait(self, frame, expression: str, timeout: float = 8.0):
        deadline = time.time() + timeout
        while not frame.evaluate(f"() => !!({expression})"):
            if time.time() > deadline:
                raise AssertionError(f"timed out: {expression}")
            time.sleep(0.04)

    # --------------------------------------------------------------- Design
    def test_design_streams_live_preview_and_records_versions(self):
        page, frame = self.open("design")
        first = _doc("Кофейня «Прибой»")
        page.evaluate("(c) => { cfg.streamChunks = c; cfg.streamEvery = 140; }", _chunks("```html\n" + first + "\n```", 16))
        frame.evaluate("() => { document.querySelector('#brief').value = 'Сайт кофейни у моря'; document.querySelector('#make').click(); }")
        # Stages and the progress pill appear at once.
        self.wait(frame, "!document.querySelector('#live-pill').hidden")
        self.wait(frame, "document.querySelector('.ds-stages li.now')")
        # The partial document reaches the preview before the stream ends.
        self.wait(frame, f"{FRONT}.srcdoc.includes('<body') && !document.querySelector('#live-badge').hidden")
        partial = frame.evaluate(f"() => {FRONT}.srcdoc")
        self.assertNotIn("</html>", partial.split("<script>document.addEventListener")[0])
        self.wait(frame, "document.querySelector('#live-pill').hidden && document.querySelector('#design-status').classList.contains('ok')")
        state = frame.evaluate("() => ({ code: document.querySelector('#code').value, label: document.querySelector('#history-label').textContent,"
                               " live: document.querySelector('#live-badge').hidden, undo: document.querySelector('#undo').disabled })")
        self.assertTrue(state["code"].startswith("<!DOCTYPE html>") and state["code"].endswith("</html>"), state)
        self.assertEqual(state["label"], "Версия 1 из 1")
        self.assertTrue(state["live"] and state["undo"], state)

        # A refinement keeps the current design on screen (no waiting card) and adds version 2.
        second = _doc("Кофейня «Прибой» — меню")
        page.evaluate("(c) => { cfg.streamChunks = c; }", _chunks(second, 8))
        frame.evaluate("() => { document.querySelector('#refine').value = 'Добавь меню'; document.querySelector('#refine-go').click(); }")
        self.wait(frame, "!document.querySelector('#live-pill').hidden")
        self.assertTrue(frame.evaluate("() => document.querySelector('#ds-empty').hidden"))
        self.wait(frame, "document.querySelector('#history-label').textContent === 'Версия 2 из 2'")
        saved = page.evaluate("saved.filter((row) => row.versions).pop()")
        self.assertEqual([row["kind"] for row in saved["versions"]], ["new", "refine"])
        self.assertEqual(saved["version_cursor"], 1)
        self.assertEqual(saved["versions"][1]["label"], "Добавь меню")

        # Undo / redo through the toolbar and the keyboard; the code follows.
        frame.evaluate("() => document.querySelector('#undo').click()")
        self.assertIn("«Прибой»</title>", frame.evaluate("() => document.querySelector('#code').value"))
        self.assertEqual(frame.evaluate("() => document.querySelector('#history-label').textContent"), "Версия 1 из 2")
        frame.evaluate("() => { document.activeElement.blur(); document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Z', code: 'KeyZ', ctrlKey: true, shiftKey: true, bubbles: true, cancelable: true })); }")
        self.assertIn("меню</title>", frame.evaluate("() => document.querySelector('#code').value"))
        # The history menu lists both, newest first; Esc closes it and is marked handled.
        frame.evaluate("() => document.querySelector('#history-toggle').click()")
        items = frame.evaluate("() => [...document.querySelectorAll('#history-list [data-version]')].map(n => [n.dataset.version, n.getAttribute('aria-checked')])")
        self.assertEqual(items, [["1", "true"], ["0", "false"]])
        prevented = frame.evaluate("() => { const e = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }); document.activeElement.dispatchEvent(e); return e.defaultPrevented; }")
        self.assertTrue(prevented)
        self.assertTrue(frame.evaluate("() => document.querySelector('#history-pop').hidden"))
        page.wait_for_timeout(100)
        self.assertEqual(page.evaluate("keys"), [], "a handled Esc must not reach the host")
        # Typing in the brief saves the light fields only, not the whole history again.
        before = page.evaluate("saved.length")
        frame.evaluate("() => { const b = document.querySelector('#brief'); b.value += ' и террасой'; b.dispatchEvent(new Event('input', { bubbles: true })); }")
        page.wait_for_timeout(900)
        light = page.evaluate("(n) => saved.slice(n)", before)
        self.assertTrue(light and all("versions" not in row and "code" not in row for row in light), light)
        self.assertTrue(light[-1]["brief"].endswith("и террасой"))

    def test_design_migrates_legacy_versions(self):
        old, current = _doc("Старая"), _doc("Текущая")
        page, frame = self.open("design", saved={"versions": [old], "code": current})
        state = frame.evaluate("() => ({ label: document.querySelector('#history-label').textContent, undo: document.querySelector('#undo').disabled,"
                               " back: document.querySelector('#version-back').hidden, code: document.querySelector('#code').value })")
        self.assertEqual(state["label"], "Версия 2 из 2")
        self.assertFalse(state["undo"] or state["back"], state)
        self.assertIn("Текущая", state["code"])
        frame.evaluate("() => document.querySelector('#version-back').click()")
        self.assertIn("Старая", frame.evaluate("() => document.querySelector('#code').value"))

    def test_design_stop_restores_the_previous_design_and_adds_no_version(self):
        current = _doc("Было")
        page, frame = self.open("design", saved={"versions": [{"code": current, "kind": "new", "at": 1}], "version_cursor": 0, "code": current})
        page.evaluate("(c) => { cfg.streamChunks = c; cfg.streamEvery = 250; }", _chunks(_doc("Стало"), 20))
        frame.evaluate("() => { document.querySelector('#refine').value = 'Сделай темнее'; document.querySelector('#refine-go').click(); }")
        self.wait(frame, "!document.querySelector('#live-badge').hidden")
        frame.evaluate("() => document.querySelector('#live-stop').click()")
        self.wait(frame, "document.querySelector('#design-status').textContent === 'Остановлено'")
        self.wait(frame, f"{FRONT}.srcdoc.includes('<title>Было</title>')")
        state = frame.evaluate(f"() => ({{ srcdoc: {FRONT}.srcdoc, label: document.querySelector('#history-label').textContent,"
                               " pill: document.querySelector('#live-pill').hidden, live: document.querySelector('#live-badge').hidden })")
        self.assertIn("<title>Было</title>", state["srcdoc"])
        self.assertEqual(state["label"], "Версия 1 из 1")
        self.assertTrue(state["pill"] and state["live"], state)

    def test_design_reports_a_truncated_answer(self):
        page, frame = self.open("design")
        truncated = _doc("Обрыв").split("<script>")[0]
        page.evaluate("(c) => { cfg.streamChunks = c; cfg.streamEvery = 20; }", _chunks(truncated, 4))
        frame.evaluate("() => { document.querySelector('#brief').value = 'Лендинг'; document.querySelector('#make').click(); }")
        self.wait(frame, "document.querySelector('#design-status').classList.contains('warn')")
        self.assertIn("оборвался", frame.evaluate("() => document.querySelector('#design-status').textContent"))
        self.assertEqual(frame.evaluate("() => document.querySelector('#history-label').textContent"), "Версия 1 из 1")

    # --------------------------------------------------------------- Studio
    def test_studio_keeps_a_session_history_and_reuses_the_raw_prompt(self):
        page, frame = self.open("studio")
        page.evaluate("() => { cfg.imageDelay = 60; }")
        frame.evaluate("() => { document.querySelector('#style').selectedIndex = 1; document.querySelector('#image-prompt').value = 'Маяк в тумане';"
                       " document.querySelector('[name=image-count][value=\"2\"]').click(); document.querySelector('#image-go').click(); }")
        self.wait(frame, "document.querySelector('#studio-status').classList.contains('ok')")
        frame.evaluate("() => { document.querySelector('#image-prompt').value = 'Второй кадр'; document.querySelector('[name=image-count][value=\"1\"]').click(); document.querySelector('#image-go').click(); }")
        self.wait(frame, "document.querySelectorAll('#image-history-row [data-history]').length === 3 && document.querySelector('#studio-status').classList.contains('ok')")
        state = frame.evaluate("() => ({ hidden: document.querySelector('#image-history').hidden, count: document.querySelector('#image-history-count').textContent,"
                               " current: document.querySelectorAll('#image-history-row .current').length })")
        self.assertEqual(state, {"hidden": False, "count": "3", "current": 1})
        # The oldest picture opens in the viewer with its prompt; reuse restores the raw text, not the composed prompt.
        frame.evaluate("() => document.querySelector('#image-history-row [data-history=\"2\"]').click()")
        caption = frame.evaluate("() => document.querySelector('#lightbox-caption').textContent")
        self.assertIn("Маяк в тумане", caption)
        self.assertEqual(frame.evaluate("() => document.querySelector('#lightbox-count').textContent"), "3 / 3")
        frame.evaluate("() => document.querySelector('#lightbox-reuse').click()")
        self.assertEqual(frame.evaluate("() => document.querySelector('#image-prompt').value"), "Маяк в тумане")
        self.assertTrue(frame.evaluate("() => document.querySelector('#lightbox').hidden"))

    def test_studio_stop_abandons_a_running_batch(self):
        page, frame = self.open("studio")
        page.evaluate("() => { cfg.imageDelay = 1200; }")
        frame.evaluate("() => { document.querySelector('#image-prompt').value = 'Долгая картинка'; document.querySelector('#image-go').click(); }")
        self.wait(frame, "document.querySelector('#image-go').classList.contains('stop')")
        self.assertEqual(frame.evaluate("() => document.querySelector('#image-go span').textContent"), "Остановить")
        frame.evaluate("() => document.querySelector('#image-go').click()")
        state = frame.evaluate("() => ({ stop: document.querySelector('#image-go').classList.contains('stop'), label: document.querySelector('#image-go span').textContent,"
                               " status: document.querySelector('#studio-status').textContent })")
        self.assertFalse(state["stop"])
        self.assertEqual(state["label"], "Создать")
        self.assertIn("Остановлено", state["status"])
        page.wait_for_timeout(1500)   # the late answer arrives and must be ignored
        self.assertTrue(frame.evaluate("() => document.querySelector('#image-history').hidden"))
        self.assertIn("Остановлено", frame.evaluate("() => document.querySelector('#studio-status').textContent"))

    def test_studio_enhances_the_prompt_with_undo(self):
        page, frame = self.open("studio")
        frame.evaluate("() => { document.querySelector('#image-prompt').value = 'кофейня у моря'; document.querySelector('#image-enhance').click(); }")
        self.wait(frame, "document.querySelector('#image-prompt').value.startsWith('Уточнённое описание')")
        request = page.evaluate("calls.filter(c => c.action === 'request').pop().payload")
        self.assertEqual(request["model"], "fixture-chat")
        self.assertEqual(request["text"], "кофейня у моря")
        self.assertFalse(request["keep_in_chat"])
        self.assertEqual(frame.evaluate("() => document.querySelector('#image-enhance span').textContent"), "Вернуть")
        frame.evaluate("() => document.querySelector('#image-enhance').click()")
        self.assertEqual(frame.evaluate("() => document.querySelector('#image-prompt').value"), "кофейня у моря")
        self.assertEqual(frame.evaluate("() => document.querySelector('#image-enhance span').textContent"), "Улучшить")

    def test_studio_side_panel_esc_is_handled_and_drop_adds_references(self):
        page, frame = self.open("studio", size=(760, 760))
        frame.evaluate("() => document.querySelector('#panel-image [data-open-side]').click()")
        self.assertEqual(frame.evaluate("() => document.querySelector('#app').dataset.side"), "open")
        prevented = frame.evaluate("() => { const e = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }); document.body.dispatchEvent(e); return e.defaultPrevented; }")
        self.assertTrue(prevented)
        self.assertEqual(frame.evaluate("() => document.querySelector('#app').dataset.side"), "")
        page.wait_for_timeout(100)
        self.assertEqual(page.evaluate("keys"), [])
        added = frame.evaluate("""async () => {
          const bytes = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='), c => c.charCodeAt(0));
          const data = new DataTransfer(); data.items.add(new File([bytes], 'ref.png', { type: 'image/png' }));
          const zone = document.querySelector('#panel-image .st-main');
          zone.dispatchEvent(new DragEvent('dragenter', { dataTransfer: data, bubbles: true, cancelable: true }));
          const shown = !document.querySelector('#image-drop').hidden;
          zone.dispatchEvent(new DragEvent('drop', { dataTransfer: data, bubbles: true, cancelable: true }));
          for (let i = 0; i < 50 && !document.querySelector('#image-refs img'); i++) await new Promise(r => setTimeout(r, 20));
          return { shown, hidden: document.querySelector('#image-drop').hidden, refs: document.querySelectorAll('#image-refs img').length };
        }""")
        self.assertEqual(added, {"shown": True, "hidden": True, "refs": 1})

    # ------------------------------------------- Round 22: drop overlays
    DRAG = """(args) => {
      const [selector, kind, name, type] = args;
      const target = document.querySelector(selector);
      const data = new DataTransfer();
      if (name) data.items.add(new File([new Uint8Array([1, 2, 3, 4])], name, { type }));
      target.dispatchEvent(new DragEvent(kind, { dataTransfer: data, bubbles: true, cancelable: true,
                                                 clientX: 400, clientY: 300 }));
    }"""

    def test_studio_transcribe_file_zone_is_a_normal_field_not_a_full_window_overlay(self):
        # Round 22 bug: the overlay class .st-drop was shared with the transcription file
        # field, so opening «Расшифровка» covered the whole module with an unclickable
        # «Выберите или перетащите файл» that accepted no files.
        page, frame = self.open("studio", size=(1200, 760))
        frame.evaluate("() => document.querySelector('[data-tab=\"transcribe\"]').click()")
        page.wait_for_timeout(150)
        geo = frame.evaluate("""() => {
          const zone = document.querySelector('#drop-zone'), cs = getComputedStyle(zone), r = zone.getBoundingClientRect();
          const hit = document.elementFromPoint(innerWidth * 0.6, innerHeight * 0.5);
          return { position: cs.position, pointer: cs.pointerEvents, width: r.width, height: r.height,
                   side: zone.closest('.st-side').getBoundingClientRect().width,
                   hitInsideZone: zone.contains(hit), hitTranscript: !!hit.closest('#transcript, .st-doc'),
                   overlays: [...document.querySelectorAll('.st-drop-overlay')].map(o => o.hidden) };
        }""")
        self.assertNotEqual(geo["position"], "absolute", geo)
        self.assertNotEqual(geo["pointer"], "none", geo)
        self.assertLessEqual(geo["width"], geo["side"] + 1, geo)
        self.assertLess(geo["height"], 300, geo)
        self.assertFalse(geo["hitInsideZone"], geo)
        self.assertTrue(geo["hitTranscript"], geo)
        self.assertEqual(geo["overlays"], [True, True], geo)
        # The field opens the file chooser on click.
        with page.expect_file_chooser(timeout=3000) as chooser:
            frame.click("#drop-zone")
        self.assertIsNotNone(chooser.value)

        # Dropping an audio file anywhere on the tab selects it; the overlay shows only during the drag.
        frame.evaluate(self.DRAG, ["#transcript", "dragenter", "talk.mp3", "audio/mpeg"])
        self.assertFalse(frame.evaluate("() => document.querySelector('#audio-drop').hidden"))
        self.assertTrue(frame.evaluate("() => document.querySelector('#drop-zone').classList.contains('over')"))
        frame.evaluate(self.DRAG, ["#transcript", "drop", "talk.mp3", "audio/mpeg"])
        state = frame.evaluate("() => ({ overlay: document.querySelector('#audio-drop').hidden,"
                               " over: document.querySelector('#drop-zone').classList.contains('over'),"
                               " source: !document.querySelector('#transcribe-source').hidden,"
                               " name: document.querySelector('#transcribe-file-name').textContent })")
        self.assertTrue(state["overlay"] and not state["over"] and state["source"], state)
        self.assertIn("talk.mp3", state["name"])
        # …and it can be removed again.
        frame.click("#transcribe-forget")
        self.assertTrue(frame.evaluate("() => document.querySelector('#transcribe-source').hidden"))
        # A non-audio file is refused with a message, not silently taken.
        frame.evaluate(self.DRAG, ["#transcript", "drop", "notes.pdf", "application/pdf"])
        self.assertTrue(frame.evaluate("() => document.querySelector('#transcribe-source').hidden"))

    def test_studio_drop_overlay_never_gets_stuck(self):
        page, frame = self.open("studio", size=(1200, 760))
        shown = "() => !document.querySelector('#image-drop').hidden"
        # A drag of plain text (not files) never shows it.
        frame.evaluate("""() => { const d = new DataTransfer(); d.setData('text/plain', 'x');
          document.querySelector('#image-stage').dispatchEvent(new DragEvent('dragenter', { dataTransfer: d, bubbles: true, cancelable: true })); }""")
        self.assertFalse(frame.evaluate(shown))
        # Enter + leave to the outside (relatedTarget null) hides it.
        frame.evaluate(self.DRAG, ["#image-stage", "dragenter", "a.png", "image/png"])
        self.assertTrue(frame.evaluate(shown))
        frame.evaluate(self.DRAG, ["#image-stage", "dragleave", "a.png", "image/png"])
        self.assertFalse(frame.evaluate(shown))
        # Enter twice (child elements) without any leave: Esc hides it and is marked handled.
        frame.evaluate(self.DRAG, ["#image-stage", "dragenter", "a.png", "image/png"])
        frame.evaluate(self.DRAG, ["#image-prompt", "dragenter", "a.png", "image/png"])
        prevented = frame.evaluate("() => { const e = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });"
                                   " document.body.dispatchEvent(e); return e.defaultPrevented; }")
        self.assertTrue(prevented)
        self.assertFalse(frame.evaluate(shown))
        page.wait_for_timeout(100)
        self.assertEqual(page.evaluate("keys"), [], "the handled Esc must not collapse the host sheet")
        # Lost dragleave: any ordinary mouse movement clears it…
        frame.evaluate(self.DRAG, ["#image-stage", "dragenter", "a.png", "image/png"])
        page.mouse.move(300, 300)
        page.mouse.move(320, 310)
        self.assertFalse(frame.evaluate(shown))
        # …and so does a drag that simply stops sending dragover (dropped outside the window).
        frame.evaluate(self.DRAG, ["#image-stage", "dragenter", "a.png", "image/png"])
        self.assertTrue(frame.evaluate(shown))
        page.wait_for_timeout(1500)
        self.assertFalse(frame.evaluate(shown))
        # A drop on the document outside any zone is swallowed (no navigation) and hides overlays.
        frame.evaluate(self.DRAG, ["#image-stage", "dragenter", "a.png", "image/png"])
        frame.evaluate(self.DRAG, [".st-tabs", "drop", "a.png", "image/png"])
        self.assertFalse(frame.evaluate(shown))
        self.assertIn("/module/studio", frame.url)
        page.screenshot(path=os.environ.get("R22_SHOT_DIR", tempfile.gettempdir()) + "/studio-image-after-drags.png")

    def test_design_drop_overlay_never_gets_stuck(self):
        page, frame = self.open("design", size=(1200, 760))
        shown = "() => !document.querySelector('#drop').hidden"
        frame.evaluate(self.DRAG, ["#brief", "dragenter", "a.png", "image/png"])
        self.assertTrue(frame.evaluate(shown))
        prevented = frame.evaluate("() => { const e = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });"
                                   " document.body.dispatchEvent(e); return e.defaultPrevented; }")
        self.assertTrue(prevented)
        self.assertFalse(frame.evaluate(shown))
        frame.evaluate(self.DRAG, ["#brief", "dragenter", "a.png", "image/png"])
        page.wait_for_timeout(1500)
        self.assertFalse(frame.evaluate(shown))
        frame.evaluate(self.DRAG, ["#brief", "dragenter", "a.png", "image/png"])
        page.mouse.move(200, 200)
        page.mouse.move(210, 220)
        self.assertFalse(frame.evaluate(shown))


if __name__ == "__main__":
    unittest.main()
