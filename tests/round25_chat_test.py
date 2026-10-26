"""Round 25: chat core regressions.

Backend: the 200-chat cap keeps pinned chats, the chat SSE relay never retries
a request whose answer already started, and an HTML error page from the Proxy
reaches the chat as one readable line.
UI (the REAL interface from tests/appearance_fixture.py in headless Chromium,
skipped without Playwright/Chromium): Markdown links with parentheses, model
images shown as links, intraword ``__``, ``\\(…\\)`` formulas, the greeting
before onboarding, Stop without an unhandled rejection, Office paste, IME Enter,
Enter in the rename dialog, branches keeping the chat's own instructions.
Markdown runner (node): mid-line and unclosed code fences, italic inside bold,
task lists, <url> autolinks, XSS stays inert.
"""
from __future__ import annotations

import http.server
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def _chat(index: int, *, pinned: bool = False, updated: float | None = None) -> dict:
    return {"id": f"chat-{index}", "title": f"Chat {index}", "pinned": pinned,
            "updated": float(updated if updated is not None else 1_000 + index),
            "messages": [{"role": "user", "content": "hi"}]}

class ChatsCapKeepsPinned(unittest.TestCase):
    def setUp(self):
        from app import pgdata
        self.pgdata = pgdata
        self.profile = None
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        patcher = patch.object(pgdata, "MEMU_DIR", Path(temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def ids(self):
        return {row["id"] for row in self.pgdata.load_chats(self.profile)}

    def test_partial_merge_keeps_an_old_pinned_chat(self):
        old_pinned = _chat(0, pinned=True, updated=1)
        self.pgdata.merge_chats_partial([old_pinned], [], self.profile)
        self.pgdata.merge_chats_partial([_chat(i) for i in range(1, 260)], [], self.profile)
        ids = self.ids()
        self.assertEqual(len(ids), self.pgdata.CHATS_LIMIT)
        self.assertIn("chat-0", ids)
        self.assertIn("chat-259", ids)
        self.assertNotIn("chat-1", ids)

    def test_save_upsert_and_full_merge_keep_pinned(self):
        rows = [_chat(0, pinned=True, updated=1)] + [_chat(i) for i in range(1, 230)]
        self.pgdata.save_chats(rows, self.profile)
        self.assertIn("chat-0", self.ids())
        self.pgdata.upsert_chat(_chat(500), self.profile)
        self.assertIn("chat-0", self.ids())
        merged = self.pgdata.merge_chats([_chat(600)], [], self.profile)
        self.assertIn("chat-0", {row["id"] for row in merged})
        self.assertLessEqual(len(merged), self.pgdata.CHATS_LIMIT)

    def test_result_stays_newest_first(self):
        rows = [_chat(0, pinned=True, updated=1)] + [_chat(i) for i in range(1, 210)]
        capped = self.pgdata._cap_chats(rows)
        times = [row["updated"] for row in capped]
        self.assertEqual(times, sorted(times, reverse=True))

class _BrokenStream(http.server.BaseHTTPRequestHandler):
    """Sends one SSE chunk of a chunked answer, then drops the connection."""
    hits: list = []

    def do_POST(self):  # noqa: N802
        type(self).hits.append(self.path)
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        chunk = b'data: {"choices":[{"delta":{"content":"half"}}]}\n\n'
        self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
        self.wfile.flush()
        self.connection.shutdown(socket.SHUT_RDWR)

    def do_GET(self):  # noqa: N802
        self.send_response(503)
        self.end_headers()

    def log_message(self, *args):
        pass

class _HtmlError(_BrokenStream):
    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = b"<!DOCTYPE html><html><head><title>502 Bad Gateway</title></head><body><h1>nginx</h1></body></html>"
        self.send_response(502)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

class ChatRelay(unittest.TestCase):
    def serve(self, handler):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}"

    def client(self, url):
        from fastapi.testclient import TestClient
        from app import playground_api
        env = {"MEMUAI_PROXY_URL": url, "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
        with patch.dict(os.environ, env), \
                patch.object(playground_api, "_runtime_proxy_url", return_value=url):
            app = playground_api.create_app()
        return TestClient(app, base_url="http://127.0.0.1")

    def test_broken_answer_is_not_requested_again(self):
        _BrokenStream.hits = []
        url = self.serve(_BrokenStream)
        from app import playground_api
        with patch.object(playground_api, "_runtime_proxy_url", return_value=url):
            response = self.client(url).post("/api/pg/chat", content=b'{"model":"m","messages":[]}')
        text = response.text
        self.assertEqual(len(_BrokenStream.hits), 1, text)
        self.assertEqual(text.count("half"), 1, text)
        self.assertIn("\n\ndata: ", text)
        events = [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: {")]
        self.assertIn("message_en", events[-1]["error"])

    def test_html_error_page_becomes_its_title(self):
        url = self.serve(_HtmlError)
        response = self.client(url).post("/api/pg/chat", content=b'{"model":"m","messages":[]}')
        event = json.loads(response.text.strip()[len("data: "):])
        self.assertEqual(event["error"]["message"], "502 Bad Gateway")
        self.assertEqual(event["error"]["status"], 502)

    def test_plain_error_body(self):
        from app.playground_api import _plain_error_body
        self.assertEqual(_plain_error_body(b"upstream timeout"), "upstream timeout")
        self.assertEqual(_plain_error_body(b"<html><body>no title</body></html>"), "")
        self.assertEqual(len(_plain_error_body(b"x" * 5000)), 600)

class MarkdownRunner(unittest.TestCase):
    """The real mdRender/cappedChats from app.js in node (tests/round25_chat_runner.cjs): fences
    opened mid-line or left unclosed, italic inside bold, task lists, <url> autolinks, XSS."""

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_markdown_runner(self):
        import subprocess
        result = subprocess.run([shutil.which("node"), str(ROOT / "tests" / "round25_chat_runner.cjs"), str(ROOT)],
                                capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS:", result.stdout)

def _chromium() -> str:
    for candidate in (os.environ.get("MEMU_TEST_CHROMIUM", ""), "/usr/bin/chromium",
                      shutil.which("chromium") or "", shutil.which("chromium-browser") or "",
                      "/workspace/work/chromium/chrome"):
        if candidate and os.path.exists(candidate):
            return candidate
    return ""

try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover - optional dependency
    sync_playwright = None

@unittest.skipUnless(sync_playwright is not None and _chromium(), "Playwright/Chromium is not installed")
class ChatUi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.appearance_fixture import build_fixture
        cls._tmp = tempfile.mkdtemp(prefix="memu-r25-chat-")
        fixture = build_fixture(Path(cls._tmp))
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])
        cls.page = cls.browser.new_page(viewport={"width": 1280, "height": 820})
        cls.errors = []
        cls.page.on("pageerror", lambda exc: cls.errors.append(str(exc)))
        cls.page.goto(fixture.as_uri(), wait_until="domcontentloaded")
        deadline = time.time() + 90
        while time.time() < deadline:
            if cls.page.evaluate("document.body.classList.contains('boot-ready') && typeof md === 'function'"):
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("MemuAI UI did not boot")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
        finally:
            cls._pw.stop()
            shutil.rmtree(cls._tmp, ignore_errors=True)

    def ev(self, script, arg=None):
        return self.page.evaluate(script, arg)

    def test_links_with_parentheses(self):
        html = self.ev("md('[Вики](https://en.wikipedia.org/wiki/Foo_(bar)) и https://en.wikipedia.org/wiki/Baz_(qux). (см. https://example.com/a)')")
        self.assertIn('href="https://en.wikipedia.org/wiki/Foo_(bar)"', html)
        self.assertIn('href="https://en.wikipedia.org/wiki/Baz_(qux)"', html)
        self.assertIn('</a>. (см.', html)
        self.assertIn('href="https://example.com/a"', html)
        self.assertTrue(html.rstrip().endswith("</a>)</p>"), html)

    def test_model_image_is_a_link_not_an_image(self):
        html = self.ev("md('![схема](https://example.com/x.png) и ![x](javascript:alert(1))')")
        self.assertNotIn("<img", html)
        self.assertIn('<a href="https://example.com/x.png"', html)
        self.assertNotIn(">!<a", html)
        self.assertNotIn('href="javascript', html)

    def test_intraword_double_underscore_is_not_bold(self):
        html = self.ev("md('module/__init__.py, file__init__.py и snake__case__ и __жирный__ текст')")
        self.assertIn("/__init__.py", html)
        self.assertIn("file__init__.py", html)
        self.assertIn("snake__case__", html)
        self.assertIn("<strong>жирный</strong>", html)

    def test_latex_parenthesis_formula(self):
        html = self.ev("md('Площадь \\\\(\\\\pi r^2\\\\) круга')")
        self.assertIn("katex", html)
        self.assertNotIn("\\(", html)

    def test_greeting_skips_the_placeholder_profile_name(self):
        greeting = self.ev("""(() => {
          const saved = [S.profileState, S.preferences.profile_name];
          S.profileState = { active_id: 'default', items: [{ id: 'default', name: 'По умолчанию' }] };
          S.preferences.profile_name = '';
          renderChips();
          const text = (document.querySelector('#greeting h1') || document.querySelector('#greeting')).textContent;
          [S.profileState, S.preferences.profile_name] = saved; renderChips();
          return text;
        })()""")
        self.assertNotIn(", По", greeting)

    def test_stop_does_not_leave_an_unhandled_rejection(self):
        before = len(self.errors)
        self.ev("""(async () => {
          const controller = new AbortController();
          const response = new Response(new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode('data: 1\\n\\n')); } }));
          const body = response.body;
          const reader = readSSE(body);
          await reader.next();
          controller.abort();
          // An aborted body: cancel() rejects, exactly what Stop produced.
          const aborted = new ReadableStream({ pull(c) { c.error(new DOMException('BodyStreamBuffer was aborted', 'AbortError')); } });
          try { for await (const _ of readSSE(aborted)) {} } catch (e) {}
          await reader.return();
          await new Promise((r) => setTimeout(r, 100));
        })()""")
        self.page.wait_for_timeout(200)
        self.assertEqual(self.errors[before:], [])

    def test_send_button_follows_the_chat_on_screen(self):
        """An answer streaming in another chat must not turn this chat's button into Stop."""
        state = self.ev("""(() => {
          const btn = document.querySelector('#btn-send');
          const saved = { id: S.currentId, messages: S.messages, generating };
          S.currentId = 'r25-chat-a'; S.messages = [];
          generationJobs.set('r25-chat-b', { controller: new AbortController() });
          generating = true; setSendBusy(true);
          const otherBusy = btn.classList.contains('is-stop');
          generationJobs.set('r25-chat-a', { controller: new AbortController() });
          renderMessages();
          const ownBusy = btn.classList.contains('is-stop');
          generationJobs.delete('r25-chat-a');
          renderMessages();
          const afterOwn = btn.classList.contains('is-stop');
          generationJobs.delete('r25-chat-b');
          S.currentId = saved.id; S.messages = saved.messages; generating = saved.generating;
          renderMessages();
          return { otherBusy, ownBusy, afterOwn };
        })()""")
        self.assertEqual(state, {"otherBusy": False, "ownBusy": True, "afterOwn": False})

    def test_office_paste_inserts_text_and_plain_image_attaches(self):
        result = self.ev("""(() => {
          clearAttachments();
          const make = (withText) => {
            const dt = new DataTransfer();
            dt.items.add(new File([new Uint8Array([137, 80, 78, 71])], 'image.png', { type: 'image/png' }));
            if (withText) dt.setData('text/plain', 'A1\\tB1');
            const event = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true });
            document.querySelector('#input').dispatchEvent(event);
            return event.defaultPrevented;
          };
          return [make(true), make(false)];
        })()""")
        self.assertEqual(result, [False, True])
        self.page.wait_for_timeout(300)
        self.assertEqual(self.ev("attachments.length"), 1)
        self.ev("clearAttachments()")

    def test_enter_during_ime_composition_does_not_send(self):
        prevented = self.ev("""(() => {
          const input = document.querySelector('#input');
          input.value = 'こんにちは';
          const event = new KeyboardEvent('keydown', { key: 'Enter', isComposing: true, bubbles: true, cancelable: true });
          input.dispatchEvent(event);
          const kept = input.value;
          input.value = '';
          return [event.defaultPrevented, kept];
        })()""")
        self.assertEqual(prevented, [False, "こんにちは"])

    def test_rename_dialog_saves_on_enter_and_branch_keeps_instructions(self):
        result = self.ev("""(() => {
          const id = 'chat-r25-test';
          S.chats.unshift({ id, title: 'Old', mode: S.mode, updated: Date.now(),
            messages: [{ role: 'user', content: 'q' }, { role: 'assistant', content: 'a' }] });
          renameChat(id);
          const field = document.querySelector('#rename-chat-input');
          field.value = 'New name';
          field.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
          const title = S.chats.find((c) => c.id === id).title;
          openChat(id);
          S.chatSystem = 'Answer briefly'; S.chatCaps = { thinking: true };
          branchAt(1);
          const branch = currentChat();
          const out = [title, branch.chatSystem, branch.chatCaps.thinking, branch.id !== id];
          S.chats = S.chats.filter((c) => c.id !== id && c.id !== branch.id);
          return out;
        })()""")
        self.assertEqual(result, ["New name", "Answer briefly", True, True])

    def test_memory_fact_is_added_with_enter(self):
        result = self.ev("""(() => {
          const saved = S.memory; S.memory = { enabled: true, facts: [] };
          renderMemoryPanel();
          const field = document.querySelector('#mem-new');
          field.value = 'Любит чай';
          field.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
          const out = [S.memory.facts.slice(), document.activeElement && document.activeElement.id];
          applyAboutFacts(['Живёт в Казани']);
          out.push(S.memory.facts.every((fact) => typeof fact === 'string'));
          S.memory = saved; closePanel();
          return out;
        })()""")
        self.assertEqual(result, [["Любит чай"], "mem-new", True])

    def test_sending_scrolls_a_scrolled_up_chat_to_the_answer(self):
        source = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        send = source[source.index("async function send() {"):source.index("generate(assistant, model, raw, userMsg.attachments, source)")]
        self.assertIn("scrollBox.scrollTop = scrollBox.scrollHeight", send)

    def test_capped_chats_keeps_pinned(self):
        result = self.ev("""(() => {
          const rows = Array.from({ length: 205 }, (_, i) => ({ id: 'c' + i, pinned: i === 204 }));
          const kept = cappedChats(rows).map((c) => c.id);
          return [kept.length, kept.includes('c204'), kept.includes('c198'), kept.includes('c199')];
        })()""")
        self.assertEqual(result, [200, True, True, False])

if __name__ == "__main__":
    unittest.main()
