"""Round 26 (second chat pass): regressions for the chat fixes.

- Local servers (MemuAI ping from the Proxy, the local llama-server, a custom TTS/STT on localhost) are
  never reached through a system/VPN proxy.
- Yandex AI Studio model URIs (``gpt://<folder>/<model>/latest``) keep their model family: the name was
  read as «latest», so vision and the context window were guessed.
- The real chat UI: the «Thinking» timer stops at the first answer text, «Save and resend» opens a new
  turn (the finished answer notifies again), Esc in the chat search clears it without stopping the reply,
  a dropped connection mid-stream gives a readable message, and a tooltip follows its control's new title.
"""
from __future__ import annotations

import ast
import asyncio
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.round25_chat_test import _chromium, sync_playwright  # noqa: E402

def _client_calls(relative: str):
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "AsyncClient":
            yield {kw.arg: kw.value for kw in node.keywords}

class LocalClientsSkipSystemProxy(unittest.TestCase):
    def test_tts_and_stt_local_servers(self):
        from app.tts_providers import trusts_env
        for base in ("http://127.0.0.1:8880/v1", "http://localhost:5002", "http://[::1]:9000/v1"):
            self.assertFalse(trusts_env(base), base)
        for base in ("https://api.openai.com/v1", "https://api.groq.com/openai/v1"):
            self.assertTrue(trusts_env(base), base)
        voice = list(_client_calls("app/voice_runtime.py"))
        self.assertTrue(voice and all("trust_env" in kw for kw in voice))
        tts = list(_client_calls("app/tts_providers.py"))
        self.assertTrue(tts and all("trust_env" in kw for kw in tts))

    def test_local_llama_server(self):
        calls = list(_client_calls("app/providers/local.py"))
        self.assertTrue(calls)
        for kw in calls:
            self.assertIsInstance(kw.get("trust_env"), ast.Constant)
            self.assertIs(kw["trust_env"].value, False)

    def test_open_memuai_ping(self):
        import httpx
        from app import admin_api
        made, seen = [], []
        real = httpx.AsyncClient

        def spy(*args, **kwargs):
            made.append(dict(kwargs))
            return real(*args, transport=httpx.MockTransport(lambda request: (seen.append(str(request.url)),
                                                                              httpx.Response(200, json={}))[1]),
                        **kwargs)

        with patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:9"}), \
                patch.object(admin_api, "_auth", lambda _request: None), \
                patch.object(admin_api, "_load_json", lambda *_a, **_k: {"url": "http://127.0.0.1:18999"}), \
                patch.object(httpx, "AsyncClient", spy):
            result = asyncio.run(admin_api.open_memuai(object()))
        self.assertEqual(result, {"ok": True, "mode": "existing"})
        self.assertEqual(seen[0], "http://127.0.0.1:18999/api/pg/ping")
        self.assertIs(made[0].get("trust_env"), False)

class YandexModelUris(unittest.TestCase):
    def test_family_survives_the_version_tag(self):
        from app import model_info
        self.assertEqual(model_info.canonical("gpt://b1g4plmmckqmvs52tqqf/deepseek-v4.1-flash/latest"),
                         "deepseek-v4.1-flash")
        self.assertEqual(model_info.canonical("gpt://b1g/qwen3.6-35b-a3b/rc"), "qwen3.6-35b-a3b")
        self.assertEqual(model_info.canonical("anthropic/Claude-Opus-5-5-20260901:thinking"), "claude-opus-5.5")
        self.assertEqual(model_info.canonical("latest"), "latest")
        self.assertIs(model_info.vision_hint("gpt://b1g/gpt-oss-120b/latest"), False)
        self.assertIs(model_info.vision_hint("gpt://b1g/qwen3.6-35b-a3b/latest"), True)
        self.assertTrue((model_info.lookup_known("gpt://b1g/deepseek-v4.1-flash/latest") or {}).get("context"))

    def test_qwen_text_models_stay_blind(self):
        from app import model_info
        for name in ("qwen3-32b", "qwen2.5-72b-instruct", "qwen3-max"):
            self.assertIs(model_info.vision_hint(name), False, name)

CHAT_STREAM = r"""(() => {
  if (window.__chatStubbed) return;
  window.__chatStubbed = true;
  const baseFetch = window.fetch;
  window.fetch = async (url, opts = {}) => {
    if (String(url) !== '/api/pg/chat') return baseFetch(url, opts);
    let ctl; const stream = new ReadableStream({ start(c) { ctl = c; } });
    const enc = new TextEncoder();
    window.__chatCtl = {
      push: (o) => ctl.enqueue(enc.encode('data: ' + (typeof o === 'string' ? o : JSON.stringify(o)) + '\n\n')),
      close: () => ctl.close(), fail: (e) => ctl.error(e) };
    return new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
  };
})()"""

@unittest.skipUnless(sync_playwright is not None and _chromium(), "Playwright/Chromium is not installed")
class ChatUi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.appearance_fixture import build_fixture
        cls._tmp = tempfile.mkdtemp(prefix="memu-r26-chat2-")
        fixture = build_fixture(Path(cls._tmp))
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])
        cls.page = cls.browser.new_page(viewport={"width": 1280, "height": 820})
        cls.errors = []
        cls.page.on("pageerror", lambda exc: cls.errors.append(str(exc)))
        cls.page.goto(fixture.as_uri(), wait_until="domcontentloaded")
        deadline = time.time() + 90
        while time.time() < deadline:
            if cls.page.evaluate("document.body.classList.contains('boot-ready') && typeof send === 'function'"):
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("MemuAI UI did not boot")
        cls.page.evaluate(CHAT_STREAM)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
        finally:
            cls._pw.stop()
            shutil.rmtree(cls._tmp, ignore_errors=True)

    def ev(self, script, arg=None):
        return self.page.evaluate(script, arg)

    def start_answer(self, text="Привет"):
        self.ev("() => { window.__chatCtl = null; newChat(); }")
        self.ev("(text) => { document.querySelector('#input').value = text; send(); }", text)
        self.page.wait_for_function("window.__chatCtl", timeout=10000)

    def finish(self):
        self.ev("__chatCtl.push('[DONE]'); __chatCtl.close()")
        self.page.wait_for_function("!generationJobs.has(S.currentId)", timeout=10000)

    def test_thinking_timer_stops_at_the_first_answer_text(self):
        self.start_answer()
        self.ev("__chatCtl.push({choices: [{delta: {reasoning_content: 'Считаю…'}}]})")
        self.page.wait_for_function("document.querySelector('[data-thought-elapsed]')", timeout=5000)
        self.page.wait_for_timeout(300)
        self.ev("__chatCtl.push({choices: [{delta: {content: 'Ответ'}}]})")
        self.page.wait_for_function("S.messages.at(-1).content === 'Ответ'", timeout=5000)
        state = self.ev("""(() => { const m = S.messages.at(-1);
          return [m.streaming, m.thoughtStartedAt, m.thoughtMs >= 250, !!document.querySelector('[data-thought-elapsed]')]; })()""")
        self.assertEqual(state, [True, 0, True, False])
        self.finish()

    def test_edit_and_resend_notifies_again(self):
        self.start_answer("Первый вопрос")
        self.ev("__chatCtl.push({choices: [{delta: {content: 'Первый ответ'}}]})")
        self.finish()

        self.ev("""(() => { const chat = currentChat(); chat._openTurn = 'turn-1'; chat._notifiedTurn = 'turn-1';
          S.messages.forEach((m) => { m._notified = true; }); window.__qa_requests.length = 0; })()""")
        self.ev("() => { window.__chatCtl = null; editMessage(0); }")
        self.ev("document.querySelector('#edit-message-text').value = 'Изменённый вопрос'; document.querySelector('#edit-resend').click()")
        self.page.wait_for_function("window.__chatCtl", timeout=10000)
        self.ev("__chatCtl.push({choices: [{delta: {content: 'Новый ответ'}}]})")
        self.page.wait_for_timeout(900)
        self.finish()
        self.page.wait_for_timeout(200)
        notified = self.ev("window.__qa_requests.filter((r) => r.path === '/api/pg/notify' && r.post).length")
        self.assertEqual(notified, 1)
        self.assertEqual(self.ev("S.messages.map((m) => m.content)"), ["Изменённый вопрос", "Новый ответ"])

    def test_escape_in_chat_search_clears_it_and_keeps_the_reply(self):
        self.start_answer()
        self.ev("__chatCtl.push({choices: [{delta: {content: 'Печатаю'}}]})")
        self.ev("document.querySelector('#chat-search').value = 'abc'")
        self.page.focus("#chat-search")
        self.page.keyboard.press("Escape")
        self.assertEqual(self.ev("[document.querySelector('#chat-search').value, generationJobs.has(S.currentId), document.activeElement.id]"),
                         ["", True, "chat-search"])
        self.page.keyboard.press("Escape")
        self.assertEqual(self.ev("[generationJobs.has(S.currentId), document.activeElement.id === 'chat-search']"), [True, False])
        self.finish()
        self.assertEqual(self.ev("[S.messages.at(-1).content, S.messages.at(-1).error || '']"), ["Печатаю", ""])

    def test_dropped_connection_is_explained(self):
        self.start_answer()
        self.ev("__chatCtl.push({choices: [{delta: {content: 'Начало'}}]})")
        self.ev("__chatCtl.fail(new TypeError('network error'))")
        self.page.wait_for_function("!generationJobs.has(S.currentId)", timeout=10000)
        message = self.ev("[S.messages.at(-1).error, S.messages.at(-1).truncated]")
        self.assertEqual(message, ["Связь с MemuAI прервалась (перезагрузка окна, перезапуск или сеть).", True])
        self.assertNotIn("network error", self.ev("document.querySelector('#messages').innerText"))

    def test_tooltip_follows_title_change(self):
        result = self.ev("""(async () => {
          const btn = document.createElement('button');
          btn.title = 'Отправить сообщение'; btn.textContent = '→';
          document.body.appendChild(btn);
          MemuTooltip.show(btn);
          const tip = document.querySelector('#memu-tooltip');
          const before = tip.textContent;
          btn.title = 'Остановить генерацию';
          await new Promise((r) => setTimeout(r, 50));
          const after = [tip.textContent, tip.hidden, btn.hasAttribute('title'), btn.dataset.tip];
          MemuTooltip.hide(); btn.remove();
          return [before, ...after];
        })()""")
        self.assertEqual(result, ["Отправить сообщение", "Остановить генерацию", False, False, "Остановить генерацию"])

    def test_delete_confirmation_takes_enter_and_escape(self):
        result = self.ev("""(async () => {
          const make = (id) => S.chats.unshift({ id, title: id, mode: 'chat', updated: Date.now(),
            messages: [{ role: 'user', content: 'q' }] });
          make('r26-del-esc'); make('r26-del-enter');
          const press = (key) => document.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
          const tick = () => new Promise((r) => setTimeout(r, 60));
          deleteChat('r26-del-esc'); await tick();
          const focused = document.activeElement.id;
          press('Escape'); await tick();
          const keptOnEsc = S.chats.some((c) => c.id === 'r26-del-esc');
          deleteChat('r26-del-enter'); await tick();
          document.activeElement.click();   // what Enter does on the focused button
          await tick();
          const goneOnEnter = !S.chats.some((c) => c.id === 'r26-del-enter');
          S.chats = S.chats.filter((c) => !c.id.startsWith('r26-del-'));
          return [focused, keptOnEsc, goneOnEnter, document.querySelector('#panel-back').classList.contains('visible')];
        })()""")
        self.assertEqual(result, ["confirm-yes", True, True, False])

    def test_settings_child_panel_returns_without_a_stale_copy(self):
        result = self.ev("""(() => {
          const saved = PG_LANG; PG_LANG = 'en';
          renderSettingsPanel();
          editPrompt(null);
          const editor = [document.querySelector('#panel h2').textContent, document.querySelector('#ep-name').placeholder,
                          document.querySelector('#ep-save').textContent];
          renderSettingsPanel();                       // «Cancel» / «Save» of the editor
          const stacked = panelBackStack.length;
          closePanel();
          const open = document.querySelector('#panel-back').classList.contains('visible');
          PG_LANG = saved;
          return [...editor, stacked, open];
        })()""")
        self.assertEqual(result, ["New prompt", "Name", "Save", 0, False])

    def test_memuai_export_round_trip_keeps_mode_and_pictures(self):
        result = self.ev("""(() => {
          const rows = parseImportedChats({ app: 'MemuAI', kind: 'memuai-chats', chats: [
            { title: 'Код', mode: 'code', messages: [{ role: 'user', content: 'fix' }, { role: 'assistant', content: 'done' }] },
            { title: 'Рисунок', mode: 'chat', messages: [{ role: 'user', content: 'нарисуй кота' },
              { role: 'assistant', content: '', images: ['data:image/png;base64,AAAA'] }] }] });
          return [rows.map((c) => c.mode), rows[1].messages.length, rows[1].messages[1].images];
        })()""")
        self.assertEqual(result, [["code", "chat"], 2, ["data:image/png;base64,AAAA"]])

    def test_no_page_errors(self):
        self.assertEqual(self.errors, [])

if __name__ == "__main__":
    unittest.main()
