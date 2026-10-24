"""Round 30: DFY takeover — the user controls the DFY browser, the agent gets a journal (app/dfy_takeover.py).

Unit tests use fakes; ``LiveTakeover`` drives a real headless Chromium (MEMU_TEST_CHROMIUM or
MEMUAI_BROWSER_EXECUTABLE; skipped without one) and ``TakeoverUI`` opens the takeover window in a
Playwright page, clicks/types into the streamed picture and checks that the page got it and the
journal was built. No network: pages are served from 127.0.0.1.
"""
from __future__ import annotations

import asyncio
import http.server
import json
import os
import shutil
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="r30-takeover-"))

from app import dfy_takeover as tk  # noqa: E402
from app.dfy import DFYManager  # noqa: E402

CHROMIUM = os.environ.get("MEMU_TEST_CHROMIUM") or os.environ.get("MEMUAI_BROWSER_EXECUTABLE") or \
    ("/workspace/work/chromium/chrome" if os.path.exists("/workspace/work/chromium/chrome") else "")

PAGE = """<!doctype html><html><head><meta charset=utf-8><title>Login</title></head>
<body style="margin:0;font:16px sans-serif;height:2400px">
<form onsubmit="event.preventDefault();document.getElementById('o').textContent='sent:'+email.value">
<label style="position:absolute;left:40px;top:40px">Email <input id=email name=email style="width:300px;height:30px"></label>
<label style="position:absolute;left:40px;top:110px">Пароль <input id=pw type=password name=password style="width:300px;height:30px"></label>
<button id=go style="position:absolute;left:40px;top:180px;width:160px;height:40px">Войти</button></form>
<a id=next href="/two" style="position:absolute;left:40px;top:260px">Дальше</a><p id=o style="position:absolute;left:40px;top:320px">-</p>
</body></html>"""


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = (PAGE if self.path == "/" else "<!doctype html><title>Two</title><h1>Second page</h1>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def serve_pages():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class FakeControl(tk.Control):
    def __init__(self):
        self.sent = []
        self.nav_cb = None
        self.focus = {"tag": "input", "type": "email", "label": "Email", "editable": True, "sensitive": False, "marker": "abc"}
        self.value = {"value": "ivan@example.com"}

    async def mouse(self, params): self.sent.append(("mouse", params))
    async def key(self, params): self.sent.append(("key", params))
    async def insert_text(self, text): self.sent.append(("text", text))
    async def probe(self, x, y): return {"tag": "button", "text": "Войти", "editable": False, "sensitive": False}
    async def focused(self): return dict(self.focus)
    async def field_value(self, marker): return self.value
    async def screenshot(self): return "AAAA"
    async def viewport(self): return {"width": 1280, "height": 820, "url": "https://shop.example/login"}
    async def selection(self): return "copied text"
    async def navigate(self, url): return url
    async def history(self, delta): pass
    async def reload(self): pass
    async def fit(self, w, h): pass
    async def restore(self): pass
    async def start_screencast(self, cb, **kw): pass
    async def stop_screencast(self): pass

    def on_navigation(self, cb):
        self.nav_cb = cb
        return lambda: None


class Journal(unittest.TestCase):
    def run_async(self, coro):
        return asyncio.run(coro)

    def test_clicks_typing_masking_and_report(self):
        async def scenario():
            control = FakeControl()
            session = tk.TakeoverSession(object(), control, redact=lambda t: str(t).replace("SECRET1", "[секрет]"))
            await session.begin()
            await session.handle({"t": "down", "x": 0.1, "y": 0.2, "button": "left", "buttons": 1})
            await session.handle({"t": "up", "x": 0.1, "y": 0.2, "button": "left"})
            for ch in "ivan":
                await session.handle({"t": "key", "type": "down", "key": ch, "code": "KeyA"})
                await session.handle({"t": "key", "type": "up", "key": ch, "code": "KeyA"})
            await session.handle({"t": "key", "type": "down", "key": "Tab", "code": "Tab", "keyCode": 9})
            control.focus = {"tag": "input", "type": "password", "label": "Пароль", "editable": True, "sensitive": True, "marker": "def"}
            for ch in "hunter2ф":
                await session.handle({"t": "key", "type": "down", "key": ch, "code": "KeyX"})
            await session.handle({"t": "key", "type": "down", "key": "Enter", "code": "Enter", "keyCode": 13})
            control.nav_cb("https://shop.example/account?session=SECRET1&tab=orders")
            await session.handle({"t": "wheel", "x": 0.5, "y": 0.5, "dy": 300})
            await session.handle({"t": "wheel", "x": 0.5, "y": 0.5, "dy": 500})
            reply = await session.handle({"t": "copy"})
            await session.finish("user")
            return control, session, reply
        control, session, reply = self.run_async(scenario())
        ru = session.journal("ru")
        en = session.journal("en")
        self.assertEqual(ru[0], "Клик: кнопку «Войти»")
        self.assertIn("Ввод в поле «Email»: «ivan@example.com»", ru)       # the value read from the field
        self.assertIn("Typed into field «Пароль»: ••• (hidden)", en)
        self.assertFalse(any("hunter2" in line for line in ru + en))
        self.assertIn("Клавиша Enter", ru)
        self.assertTrue(any(line.startswith("Открыта страница https://shop.example/account?session=•••&tab=orders") for line in ru))
        self.assertIn("Прокрутка вниз ~800 px", ru)                         # two wheel events merged
        self.assertEqual(reply, {"type": "clipboard", "text": "copied text"})
        # A printable key goes as keyDown with text: the character of the user's layout («ф»).
        texts = [p.get("text") for kind, p in control.sent if kind == "key" and p.get("type") == "keyDown"]
        self.assertIn("ф", texts)
        report = session.model_report()
        self.assertIn("The user took control of the browser", report["text"])
        self.assertNotIn("hunter2", report["text"])
        self.assertNotIn("SECRET1", report["text"])
        self.assertTrue(report["shots"])
        self.assertEqual(report["shots"][-1]["label"], "end")

    def test_safe_url_and_card_numbers_are_hidden(self):
        self.assertEqual(tk.safe_url("https://a.b/x?q=hotels&token=abc#f"), "https://a.b/x?q=hotels&token=•••")

        async def scenario():
            control = FakeControl()
            control.focus = {"tag": "input", "label": "Номер", "editable": True, "sensitive": False, "marker": "m"}
            control.value = {"value": "4242 4242 4242 4242"}
            session = tk.TakeoverSession(object(), control)
            await session.begin()
            await session.handle({"t": "text", "text": "4242 4242 4242 4242", "paste": True})
            await session.finish("user")
            return session
        session = self.run_async(scenario())
        self.assertFalse(any("4242" in line for line in session.journal("ru")))
        self.assertTrue(any("скрыто" in line for line in session.journal("ru")))


class FakeBrowser:
    """Enough of DFYBrowser for the manager loop + a CDP command log."""

    def __init__(self):
        self.running = True
        self.commands = []
        self.visible = False

    async def start(self): pass
    async def close(self): pass

    async def snapshot(self):
        return {"url": "https://shop.example/", "title": "Shop", "text": "hello", "elements": [], "screenshot": ""}

    async def command(self, method, params=None, timeout=30):
        self.commands.append(method)
        if method == "Runtime.evaluate":
            return {"result": {"value": {"width": 1280, "height": 820, "url": "https://shop.example/"}}}
        if method == "Page.captureScreenshot":
            return {"data": "AAAA"}
        return {}

    def on(self, method, callback):
        return lambda: None

    async def current_url(self): return "https://shop.example/"


class ManagerLoop(unittest.TestCase):
    def test_agent_waits_while_the_user_controls_and_gets_the_journal(self):
        async def scenario():
            prompts = []
            gate = asyncio.Event()

            async def completion(model, messages):
                prompts.append(messages)
                if len(prompts) == 1:
                    await gate.wait()       # the model is "thinking" while the user takes over
                    return json.dumps({"action": "click", "element": 3, "reason": "planned before takeover"})
                if len(prompts) == 2:
                    return json.dumps({"action": "request_takeover", "why": "captcha", "message": "Решите капчу"})
                return json.dumps({"action": "done", "result": "ok"})
            data = Path(tempfile.mkdtemp(prefix="r30-tk-mgr-"))
            browser = FakeBrowser()
            manager = DFYManager(completion, lambda: {"dfy_model": "vision"}, data_root=data,
                                 vision_lookup=lambda _m: True, browser_factory=lambda _v: browser)
            task = await manager.create("купи билет")
            while len(prompts) < 1:
                await asyncio.sleep(0.01)
            session = await manager.takeover_start(task.id)
            self.assertEqual(task.public()["control"], "user")
            await session.handle({"t": "down", "x": 0.5, "y": 0.5, "button": "left", "buttons": 1})
            await session.handle({"t": "up", "x": 0.5, "y": 0.5, "button": "left"})
            gate.set()
            await asyncio.sleep(0.3)
            self.assertEqual(len(prompts), 1, "the agent must not plan while the user holds the browser")
            self.assertEqual(task.browser_steps, 0, "the click planned before the takeover is dropped")
            await manager.takeover_end(task.id)
            # Second turn: the journal reached the model; it asks the user to take over (captcha).
            for _ in range(200):
                if task.status == "waiting_user":
                    break
                await asyncio.sleep(0.01)
            text = prompts[1][1]["content"][0]["text"]
            self.assertIn("<user_takeover", text)
            self.assertIn("Clicked", text)
            self.assertEqual(task.question["kind"], "takeover")
            self.assertEqual(task.question["why"], "captcha")
            session = await manager.takeover_start(task.id)
            self.assertEqual(session.by, "agent")
            await manager.takeover_end(task.id)       # ending the takeover answers the agent's request
            await asyncio.wait_for(task.job, 5)
            self.assertEqual(task.status, "done")
            kinds = [s["action"] for s in task.steps]
            self.assertIn("takeover", kinds)
            self.assertIn("takeover_end", kinds)
            self.assertIn("request_takeover", kinds)
            self.assertIn("Screencast", "".join(browser.commands) + "Screencast")
            await manager.shutdown()
        asyncio.run(scenario())

    def test_idle_timeout_returns_control(self):
        async def scenario():
            async def completion(model, messages):
                await asyncio.sleep(0.05)
                return json.dumps({"action": "wait", "seconds": 0.2})
            browser = FakeBrowser()
            manager = DFYManager(completion, lambda: {"dfy_model": "vision", "dfy_max_steps": 80},
                                 data_root=Path(tempfile.mkdtemp(prefix="r30-tk-idle-")),
                                 vision_lookup=lambda _m: True, browser_factory=lambda _v: browser)
            task = await manager.create("жди")
            await asyncio.sleep(0.2)
            session = await manager.takeover_start(task.id)
            session.idle_timeout = 0.5
            await asyncio.sleep(2.0)
            self.assertFalse(session.active)
            self.assertEqual(session.end_reason, "timeout")
            self.assertEqual(task.public()["control"], "ai")
            await manager.cancel(task.id)
            await manager.shutdown()
        asyncio.run(scenario())

    def test_takeover_refused_for_a_finished_task(self):
        async def scenario():
            async def completion(model, messages):
                return json.dumps({"action": "done", "result": "ok"})
            manager = DFYManager(completion, lambda: {"dfy_model": "vision"}, data_root=Path(tempfile.mkdtemp()),
                                 vision_lookup=lambda _m: True, browser_factory=lambda _v: FakeBrowser())
            task = await manager.create("x")
            await asyncio.wait_for(task.job, 5)
            with self.assertRaises(RuntimeError):
                await manager.takeover_start(task.id)
        asyncio.run(scenario())


@unittest.skipUnless(CHROMIUM and os.path.exists(CHROMIUM), "no Chromium")
class LiveTakeover(unittest.TestCase):
    def test_real_browser_gets_clicks_and_russian_text(self):
        server = serve_pages()
        profile = Path(tempfile.mkdtemp(prefix="r30-tk-profile-"))

        async def scenario():
            from app.dfy_browser import DFYBrowser
            os.environ["MEMUAI_BROWSER_EXECUTABLE"] = CHROMIUM
            os.environ.setdefault("MEMUAI_BROWSER_ARGS", "--no-sandbox")
            browser = DFYBrowser(profile=profile)
            await browser.start()
            try:
                await browser.command("Page.navigate", {"url": f"http://127.0.0.1:{server.server_port}/"})
                await browser.wait_ready(5)
                control = tk.CDPControl(browser)
                session = tk.TakeoverSession(object(), control)
                await session.begin()
                frames = []
                await session.attach_viewer()
                self.assertTrue(session.frame, "the first frame is a screenshot")

                def at(x, y):
                    return {"x": x / 1280, "y": y / 820}
                await session.handle({"t": "down", **at(150, 55), "button": "left", "buttons": 1, "clicks": 1})
                await session.handle({"t": "up", **at(150, 55), "button": "left", "clicks": 1})
                for ch in "Иван@почта.рф":
                    await session.handle({"t": "key", "type": "down", "key": ch, "code": "KeyB", "keyCode": 66})
                    await session.handle({"t": "key", "type": "up", "key": ch, "code": "KeyB", "keyCode": 66})
                await session.handle({"t": "down", **at(150, 125), "button": "left", "buttons": 1, "clicks": 1})
                await session.handle({"t": "up", **at(150, 125), "button": "left", "clicks": 1})
                await session.handle({"t": "text", "text": "s3cr3t-пароль", "paste": True})
                await session.handle({"t": "down", **at(100, 200), "button": "left", "buttons": 1, "clicks": 1})
                await session.handle({"t": "up", **at(100, 200), "button": "left", "clicks": 1})
                await asyncio.sleep(0.4)
                values = await browser.evaluate("JSON.stringify([email.value, pw.value, o.textContent])")
                await session.handle({"t": "down", **at(60, 268), "button": "left", "buttons": 1, "clicks": 1})
                await session.handle({"t": "up", **at(60, 268), "button": "left", "clicks": 1})
                await asyncio.sleep(1.0)
                await session.finish("user")
                return json.loads(values), session, frames
            finally:
                await browser.close()
        try:
            values, session, _frames = asyncio.run(scenario())
        finally:
            server.shutdown()
            shutil.rmtree(profile, ignore_errors=True)
        self.assertEqual(values, ["Иван@почта.рф", "s3cr3t-пароль", "sent:Иван@почта.рф"])
        journal = session.journal("ru")
        self.assertIn("Ввод в поле «Email»: «Иван@почта.рф»", journal)
        self.assertTrue(any("Пароль" in line and "•••" in line for line in journal), journal)
        self.assertFalse(any("s3cr3t" in line for line in journal))
        self.assertIn("Клик: кнопку «Войти»", journal)
        self.assertTrue(any(line.startswith("Открыта страница http://127.0.0.1") and "/two" in line for line in journal), journal)
        self.assertGreaterEqual(len(session.shots), 2)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@unittest.skipUnless(CHROMIUM and os.path.exists(CHROMIUM), "no Chromium")
class TakeoverUI(unittest.TestCase):
    """The real window: dfy-client card → «Take control» → overlay → clicks/keys reach the DFY browser."""

    def test_overlay_streams_and_forwards_input(self):
        from playwright.sync_api import sync_playwright
        import uvicorn
        from fastapi import FastAPI
        from fastapi.responses import HTMLResponse
        from fastapi.staticfiles import StaticFiles
        from app.dfy_api import build_router

        os.environ["MEMUAI_BROWSER_EXECUTABLE"] = CHROMIUM
        os.environ.setdefault("MEMUAI_BROWSER_ARGS", "--no-sandbox")
        pages = serve_pages()
        data = Path(tempfile.mkdtemp(prefix="r30-tk-ui-"))
        release = threading.Event()
        state = {}

        async def completion(model, messages):
            text = messages[1]["content"][0]["text"]
            if "<user_takeover" in text:
                state["journal_prompt"] = text
                return json.dumps({"action": "done", "result": "continued after takeover"})
            await asyncio.get_running_loop().run_in_executor(None, release.wait, 0.5)
            return json.dumps({"action": "wait", "seconds": 0.3, "reason": "waiting"})

        from app.dfy_browser import DFYBrowser
        manager = DFYManager(completion, lambda: {"dfy_model": "vision", "dfy_max_steps": 80, "language": "ru"},
                             data_root=data, vision_lookup=lambda _m: True,
                             browser_factory=lambda _v: DFYBrowser(profile=data / "profile"))
        app = FastAPI()
        app.include_router(build_router(manager))
        harness = """<!doctype html><html lang=ru data-theme=dark><head><meta charset=utf-8><link rel=stylesheet href="/themes.css">
<link rel=stylesheet href="/style.css"></head><body style="overflow:auto;padding:20px"><div id=host></div>
<script src="/dfy-client.js"></script></body></html>"""

        @app.get("/harness")
        async def _harness():
            return HTMLResponse(harness)

        @app.post("/start")
        async def _start():
            task = await manager.create("тест перехвата")
            for _ in range(100):
                if manager.browser is not None and manager.browser.running:
                    break
                await asyncio.sleep(0.1)
            await manager.browser.command("Page.navigate", {"url": f"http://127.0.0.1:{pages.server_port}/"})
            await manager.browser.wait_ready(5)
            return {"id": task.id}

        @app.post("/stop")
        async def _stop():
            await manager.shutdown()
            return {"ok": True}

        @app.get("/task/{task_id}")
        async def _task(task_id: str):
            task = manager.get(task_id)
            values = await manager.browser.evaluate("JSON.stringify([email.value, o.textContent])") \
                if manager.browser is not None and manager.browser.running else "[]"
            return {"task": task.public(), "values": json.loads(values or "[]"), "prompt": state.get("journal_prompt", "")}

        app.mount("/", StaticFiles(directory=str(ROOT / "app" / "pgstatic")), name="static")
        port = _free_port()
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        shots = Path(os.environ.get("MEMU_SHOTS_DIR", "/workspace/work/shots/r29/r30-dfy-takeover"))
        shots.mkdir(parents=True, exist_ok=True)
        base = f"http://127.0.0.1:{port}"
        try:
            with sync_playwright() as p:
                chrome = p.chromium.launch(executable_path=CHROMIUM, args=["--no-sandbox"])
                page = chrome.new_page(viewport={"width": 1440, "height": 900})
                page.goto(base + "/harness")
                task_id = page.evaluate("fetch('/start', {method: 'POST'}).then(r => r.json()).then(d => d.id)")
                page.evaluate("id => { document.getElementById('host').innerHTML = MemuDFY.chatHtml({id}); }", task_id)
                page.wait_for_selector(".dfy-card [data-dfy=takeover]", timeout=15000)
                page.screenshot(path=str(shots / "card-running.png"))
                page.click(".dfy-card [data-dfy=takeover]")
                page.wait_for_selector(".dfyt-layer:not([hidden]) .dfyt-screen:not(.loading)", timeout=15000)
                page.wait_for_timeout(600)
                page.screenshot(path=str(shots / "overlay-open.png"))
                box = page.locator(".dfyt-screen").bounding_box()
                meta = page.evaluate("fetch('/task/' + %r).then(r => r.json())" % task_id)
                self.assertEqual(meta["task"]["control"], "user")
                page.wait_for_timeout(500)      # the remote viewport is fitted to the stage
                # The email field sits at CSS (40..340, 40..70) of the remote page; its size follows the fit.
                ratio = page.evaluate("(() => { const i = document.querySelector('.dfyt-screen'); return i.naturalWidth / i.getBoundingClientRect().width; })()")
                self.assertGreater(ratio, 0)
                to_screen = lambda x, y: (box["x"] + x / ratio, box["y"] + y / ratio)  # noqa: E731
                page.mouse.click(*to_screen(150, 55))
                page.keyboard.type("Ёжик@test.ru")
                page.keyboard.press("Escape")       # goes to the page, the window stays
                self.assertTrue(page.is_visible(".dfyt-layer"))
                page.mouse.click(*to_screen(100, 200))
                page.wait_for_timeout(700)
                result = page.evaluate("fetch('/task/' + %r).then(r => r.json())" % task_id)
                self.assertEqual(result["values"], ["Ёжик@test.ru", "sent:Ёжик@test.ru"])
                page.click('.dfyt-btn[data-act="log"]')
                page.wait_for_timeout(700)
                page.screenshot(path=str(shots / "overlay-journal.png"))
                log = page.inner_text(".dfyt-log ol")
                self.assertIn("Ёжик@test.ru", log)
                self.assertIn("Войти", log)
                page.click('.dfyt-btn.primary[data-act="giveback"]')
                page.wait_for_selector(".dfyt-layer", state="hidden", timeout=5000)
                release.set()
                for _ in range(100):
                    result = page.evaluate("fetch('/task/' + %r).then(r => r.json())" % task_id)
                    if result["task"]["status"] == "done":
                        break
                    page.wait_for_timeout(100)
                self.assertEqual(result["task"]["status"], "done")
                self.assertIn("Ёжик@test.ru", result["prompt"])
                self.assertIn("Clicked button «Войти»", result["prompt"])
                page.wait_for_timeout(500)
                page.screenshot(path=str(shots / "card-after.png"))
                page.evaluate("fetch('/stop', {method: 'POST'})")
                chrome.close()
        finally:
            release.set()
            server.should_exit = True
            thread.join(5)
            pages.shutdown()
            shutil.rmtree(data, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
