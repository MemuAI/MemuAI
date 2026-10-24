"""Round 36 — audit (wave 2) of the round-35 planning and chat features: bugs found by driving them in Chromium.

Python / static:
  • validate_flow names fields the way the builder labels them (not «query»), an «Ask me» step needs a question;
  • the builder keeps its draft on Esc in a field; the step test shows an inline ask as waiting and keeps opened rows;
  • the canvas sandbox puts its CSP in front of everything (a script / an image before «<head>» was not covered).
Browser (offline, tests/ui_lang_scan.Servers):
  • the canvas sandbox blocks every request, whatever the page's markup;
  • a planned task's «Ask me» form is answered right inside the Inbox and the item closes;
  • the Inbox opened from the builder's test stacks over it — closing it returns to the unsaved builder.
Run: NO_PROXY=127.0.0.1,localhost MEMUAI_DATA_DIR=/tmp/x MEMU_TEST_CHROMIUM=… python -m pytest -q tests/round36_audit2_test.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "pgstatic"


def read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


class FlowValidationTest(unittest.TestCase):
    def test_field_labels_ru_en_and_ask_question_required(self):
        from app import flow_engine as fe
        steps = fe.normalize_steps([{"type": "web_search"}, {"type": "model"}, {"type": "ask_user", "question": ""},
                                    {"type": "memucode", "project_path": "C:/p"}])
        ru = [p["text"] for p in fe.validate_flow(steps, "ru")]
        en = [p["text"] for p in fe.validate_flow(steps, "en")]
        self.assertIn("Шаг 1: заполните поле «Что искать»", ru)
        self.assertIn("Шаг 2: заполните поле «Задание для модели»", ru)
        self.assertIn("Шаг 3: заполните поле «Вопрос»", ru)
        self.assertIn("Шаг 4: заполните поле «Задание»", ru)
        self.assertIn("Step 1: fill in “Search for”", en)
        self.assertFalse([t for t in ru + en if "«query»" in t or "«prompt»" in t])


class TelegramVersionsTest(unittest.TestCase):
    def test_switching_a_version_drops_the_other_branchs_summary(self):
        import tempfile
        from unittest.mock import patch
        from app import chat_tree, pgdata, telegram_bot
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"MEMUAI_DATA_DIR": tmp}):
            chat = {"id": "c-v", "title": "V", "updated": 1, "mode": "chat",
                    "messages": [{"id": "u1", "role": "user", "content": "q"}, {"id": "a1", "role": "assistant", "content": "old"}]}
            chat_tree.begin_version(chat, 1)
            chat["messages"].append({"id": "a2", "role": "assistant", "content": "new"})
            chat["contextSummary"], chat["contextSummaryThrough"] = "about «new»", 2
            pgdata.save_chats([chat])
            saved = telegram_bot._switch_version("default", "c-v", -1)
            self.assertIsNotNone(saved)
            self.assertEqual([m["id"] for m in chat_tree.active_path(saved)], ["u1", "a1"])
            self.assertEqual(saved.get("contextSummary"), "")   # same length, other branch — the old summary must go
            self.assertEqual(saved.get("contextSummaryThrough"), 0)


class BigDocsStallTest(unittest.TestCase):
    def test_a_stalled_request_is_retried_not_waited_forever(self):
        import asyncio
        from unittest.mock import patch
        from app import bigdocs

        calls = []

        async def complete(model, messages):
            calls.append(1)
            if len(calls) == 1:
                await asyncio.sleep(30)          # a stream that never ends
            return "part summary"

        async def go():
            job = bigdocs.Job({"id": "d", "name": "x"}, "m", "p", "ru", 1000)
            return await bigdocs.call_robust(job, complete, [{"role": "user", "content": "x"}], index=0), job

        with patch.object(bigdocs, "CALL_TIMEOUT", 0.2), patch.object(bigdocs, "_RETRY_DELAYS", (0.01,)):
            text, job = asyncio.run(go())
        self.assertEqual(text, "part summary")
        self.assertEqual(len(calls), 2)
        self.assertEqual(job.retries, 1)


class StaticTest(unittest.TestCase):
    def test_builder_esc_in_field_keeps_the_draft(self):
        src = read("plan.js")
        self.assertIn('e.key === "Escape" && !e.defaultPrevented && e.target.matches("input:not([type=checkbox]), textarea")', src)

    def test_step_test_inline_ask_and_kept_rows(self):
        src = read("flows.js")
        self.assertIn('r.status === "running" && r.type === "ask_user" ? "waiting"', src)
        self.assertIn('details[data-k]', src)
        self.assertIn('MemuInbox?.open?.({ stack: true })', src)

    def test_inbox_answers_flow_forms(self):
        src = read("inbox.js")
        self.assertIn("function askFormHtml(it)", src)
        self.assertIn('ask_user: [T("Вопрос", "Question"), "chat"]', src)
        self.assertIn("root: !opts.stack", src)

    def test_sandbox_doc_policy_first(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        script = r"""
global.window = global; global.localStorage = {getItem(){return null}, setItem(){}};
global.document = {addEventListener(){}, querySelector(){return null}, querySelectorAll(){return []}, body:{classList:{add(){},remove(){}}}};
window.addEventListener = () => {};
require(process.argv[1]);
const C = window.MemuCanvas;
console.log(JSON.stringify({
  early: C._sandboxDoc("<script>fetch('x')</script><html><head></head><body>a</body></html>", "N"),
  comment: C._sandboxDoc("<!-- <head> --><img src='http://x/y.png'>", "N"),
  doctype: C._sandboxDoc("<!DOCTYPE html><html><head><title>t</title></head><body>b</body></html>", "N"),
}));
"""
        res = subprocess.run([node, "-e", script, str(STATIC / "canvas.js")], capture_output=True, text=True, timeout=30)
        self.assertEqual(res.returncode, 0, res.stderr)
        import json
        out = json.loads(res.stdout.strip().splitlines()[-1])
        self.assertLess(out["early"].index("Content-Security-Policy"), out["early"].index("fetch('x')"))
        self.assertLess(out["comment"].index("Content-Security-Policy"), out["comment"].index("<!-- <head>"))
        self.assertTrue(out["doctype"].startswith("<!DOCTYPE html><meta charset"))


@unittest.skipUnless(os.environ.get("MEMU_TEST_CHROMIUM"), "MEMU_TEST_CHROMIUM is not set")
class BrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest("playwright is not installed")
        from tests.ui_lang_scan import Servers
        from app import pgdata
        cls.servers = Servers().start()
        pgdata.save_preferences({"profile_onboarded": True, "profile_name": "Test", "language": "ru"})
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(executable_path=os.environ["MEMU_TEST_CHROMIUM"], args=["--no-sandbox"])
        cls.page = cls.browser.new_page(viewport={"width": 1280, "height": 820})
        cls.errors = []
        cls.page.on("pageerror", lambda e: cls.errors.append(str(e)))
        cls.page.goto(cls.servers.pg + "/", wait_until="load", timeout=90000)
        cls.wait(cls, "typeof S !== 'undefined' && !!S.models && S.models.length > 0 && !!window.MemuInbox && !!window.MemuPlan")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
            cls.pw.stop()
        finally:
            cls.servers.stop()

    def wait(self, code, timeout=30.0):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if self.page.evaluate(code):
                    return
            except Exception:
                pass
            self.page.wait_for_timeout(150)
        raise AssertionError(f"timed out: {code}")

    def test_canvas_sandbox_blocks_network(self):
        page, hits = self.page, []
        on = lambda r: "/r36sbx/" in r.url and hits.append(r.url)
        page.on("requestfinished", on)
        u = self.servers.proxy + "/r36sbx"
        cases = [f"<script>fetch('{u}/a').catch(() => 0)</script><html><head></head><body>x</body></html>",
                 f"<!-- <head> --><img src='{u}/b.png'>", f"<HTML><HEAD></HEAD><BODY><img src='{u}/c'></BODY></HTML>",
                 f"<!doctype html><html><head><link rel=stylesheet href='{u}/d.css'></head><body></body></html>"]
        self.wait("!!window.MemuCanvas")
        for html in cases:
            page.evaluate("""h => { const f = document.createElement('iframe'); f.setAttribute('sandbox', 'allow-scripts');
                f.className = 'r36-sbx'; f.srcdoc = MemuCanvas._sandboxDoc(h, 'n'); document.body.appendChild(f); }""", html)
        page.wait_for_timeout(1500)
        page.evaluate("document.querySelectorAll('.r36-sbx').forEach((f) => f.remove())")
        page.remove_listener("requestfinished", on)
        self.assertEqual(hits, [])

    def test_flow_form_answered_in_inbox_over_the_builder(self):
        from app import ask_user
        page = self.page
        page.evaluate("t => MemuPlan.openBuilder(t)", {"name": "R36", "kind": "flow", "steps": [{"id": "z1", "type": "notify", "text": "x"}]})
        self.wait("!!document.querySelector('#flw-root')")
        page.fill("#pln-builder .pln-name", "R36 черновик")
        page.locator("#pln-builder .pln-name").press("Escape")
        page.wait_for_timeout(200)
        self.assertTrue(page.evaluate("!!document.querySelector('#flw-root')"), "Esc in a field closed the builder")
        rec = ask_user.STORE.open({"question": "Отправить?", "fields": [{"id": "answer", "type": "choice", "label": "Ответ",
                                                                            "options": ["Да", "Нет"]}]},
                                  surface="flow", ref={"run_id": "r-x", "task_id": "", "key": "k"}, title="R36")
        page.evaluate("MemuInbox.open({ stack: true })")
        self.wait("!!document.querySelector('#ibx-list .au-card')")
        page.click("#ibx-list .au-chip >> nth=0")
        page.click("#ibx-list .au-submit")
        end = time.time() + 15
        while time.time() < end and (ask_user.STORE.get(rec["id"]) or {}).get("status") == "pending":
            time.sleep(0.2)
        state = ask_user.STORE.get(rec["id"]) or {}
        self.assertEqual(state.get("status"), "answered")
        self.assertEqual((state.get("answers") or {}).get("answer"), "Да")
        page.keyboard.press("Escape")
        self.wait("!!document.querySelector('#flw-root')")
        self.assertEqual(page.input_value("#pln-builder .pln-name"), "R36 черновик")
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
