"""Round 33 (r33-uifix): DFY live view / takeover lifecycle, pointer drag with scrolling, glass focus rings.

* #4  — a DFY action step shows the page AFTER the action (``shot_pending`` / ``_settle_step_shot``).
* #5  — a cancelled / finished task keeps no takeover (server) and the minimized bar follows the task (client).
* #19 — memu-dragsort.js (sidebar chats / sections) and the Characters prompt manager drag with the pointer:
        the wheel works, the list scrolls by itself near / past its edges, Esc cancels, a drop is not a click.
* #14 — designs: a search field inside a container that draws its own focus ring paints no second ring.

The browser parts drive Chromium through Playwright against pages routed in memory (no app servers);
they are skipped when Playwright / Chromium are missing.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="r33-uifix-"))

from app.dfy import DFYManager  # noqa: E402

PG = ROOT / "app" / "pgstatic"
CHAR = ROOT / "app" / "module_builtins" / "characters"
CHROMIUM = next((p for p in (os.environ.get("MEMU_TEST_CHROMIUM"), os.environ.get("MEMUAI_BROWSER_EXECUTABLE"),
                             "/usr/bin/chromium", shutil.which("chromium") or "") if p and os.path.exists(p)), "")
try:
    from playwright.sync_api import sync_playwright  # noqa: F401
    HAVE_PW = bool(CHROMIUM)
except Exception:   # pragma: no cover
    HAVE_PW = False

def shot_bytes(n: int) -> str:
    """A distinct «screenshot» per snapshot (JPEG magic + the snapshot number)."""
    return base64.b64encode(b"\xff\xd8\xff\xe0" + f"page-{n}".encode()).decode()

class CountingBrowser:
    """A fake DFY browser whose every snapshot carries a new screenshot (page-1, page-2, …)."""

    def __init__(self):
        self.snaps = 0
        self.url = "about:blank"
        self.clicked = []

    async def start(self): pass
    async def close(self): pass
    async def current_url(self): return self.url

    async def snapshot(self):
        self.snaps += 1
        return {"url": self.url, "title": "T", "text": "page", "screenshot": shot_bytes(self.snaps),
                "elements": [{"id": 4, "tag": "a", "text": "Next"}]}

    async def element_info(self, element): return {"text": "Next", "tag": "a"}

    async def navigate(self, url):
        self.url = url
        return url

    async def click(self, element):
        self.clicked.append(element)
        self.url = "https://example.com/next"
        return "clicked"

    async def wait_ready(self, *a, **k): pass
    async def scroll(self, dy): return "scrolled"
    async def key(self, name): return name
    async def back(self): return None

class Scripted:
    def __init__(self, script):
        self.script = list(script)

    async def __call__(self, model, messages):
        item = self.script.pop(0) if self.script else {"action": "done", "result": "end"}
        return json.dumps(item)

class Gate:
    """A model that thinks until released (so the test can take over / cancel mid-task)."""

    def __init__(self):
        self.calls = 0
        self.release = None

    async def __call__(self, model, messages):
        self.calls += 1
        if self.release is None:
            self.release = asyncio.Event()
        await self.release.wait()
        return json.dumps({"action": "wait", "seconds": 0.1})

def manager(model, browser, data, **prefs):
    settings = {"dfy_enabled": True, "dfy_model": "vision/model", "dfy_max_steps": 20, "language": "ru"}
    settings.update(prefs)
    return DFYManager(model, lambda: dict(settings), data_root=data, vision_lookup=lambda _m: True,
                      browser_factory=lambda _v: browser)

async def wait_for(cond, timeout=5.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not cond():
        if loop.time() > end:
            raise AssertionError("condition never became true")
        await asyncio.sleep(0.01)

class DFYLiveView(unittest.TestCase):
    """#4: the step of an action carries the screenshot taken right after it."""

    def test_action_step_gets_the_page_after_the_action(self):
        async def scenario():
            data = Path(tempfile.mkdtemp(prefix="r33-uifix-shot-"))
            browser = CountingBrowser()
            mgr = manager(Scripted([{"action": "navigate", "url": "https://example.com/", "reason": "open"},
                                    {"action": "click", "element": 4, "reason": "next"},
                                    {"action": "done", "result": "ok"}]), browser, data)
            task = await mgr.create("open example", [], "chat")
            await asyncio.wait_for(task.job, 10)
            return mgr, task, browser
        mgr, task, browser = asyncio.run(scenario())
        self.assertEqual(task.status, "done")
        steps = {s["action"]: s for s in task.steps}
        nav, click = steps["navigate"], steps["click"]

        self.assertEqual(Path(mgr.screenshot_path(task.id, nav["n"])).read_bytes()[4:], b"page-2")
        self.assertEqual(Path(mgr.screenshot_path(task.id, click["n"])).read_bytes()[4:], b"page-3")
        for step in (nav, click):
            self.assertTrue(step.get("shot_after"))
            self.assertRegex(step["screenshot_url"], r"/screenshots/%d\?v=1$" % step["n"],
                             "a new ?v= busts the cached <img> of the same URL")
        self.assertIsNone(task.shot_pending)

    def test_step_limit_takes_a_final_snapshot(self):
        async def scenario():
            data = Path(tempfile.mkdtemp(prefix="r33-uifix-limit-"))
            browser = CountingBrowser()
            mgr = manager(Scripted([{"action": "navigate", "url": f"https://example.com/{i}"} for i in range(5)]),
                          browser, data, dfy_max_steps=5)
            task = await mgr.create("five steps", [], "chat")
            await asyncio.wait_for(task.job, 10)
            return mgr, task, browser
        mgr, task, browser = asyncio.run(scenario())
        self.assertEqual(task.status, "failed")
        last = [s for s in task.steps if s["action"] == "navigate"][-1]
        self.assertTrue(last.get("shot_after"), "the last action's result is shown even at the step limit")
        self.assertEqual(Path(mgr.screenshot_path(task.id, last["n"])).read_bytes()[4:], f"page-{browser.snaps}".encode())

    def test_other_step_after_the_action_is_left_alone(self):
        async def scenario():
            data = Path(tempfile.mkdtemp(prefix="r33-uifix-other-"))
            mgr = manager(Scripted([]), CountingBrowser(), data)
            task = await mgr.create("x", [], "chat")
            await asyncio.wait_for(task.job, 10)
            redact = mgr._redactor(task)
            act = await mgr._add_step(task, "click", "a", redact, shot_bytes(90))
            task.shot_pending = act
            await mgr._add_step(task, "takeover", "b", redact, shot_bytes(91))
            await mgr._settle_step_shot(task, shot_bytes(92))
            return task, act
        task, act = asyncio.run(scenario())
        self.assertFalse(act.get("shot_after"), "a later step carries its own picture; the action keeps its own")
        self.assertIsNone(task.shot_pending)

class DFYTakeoverLifecycle(unittest.TestCase):
    """#5: cancelling a task under takeover leaves no takeover behind."""

    def test_cancel_clears_the_takeover(self):
        async def scenario():
            from tests.round30_dfy_takeover_test import FakeBrowser
            data = Path(tempfile.mkdtemp(prefix="r33-uifix-tk-"))
            model = Gate()
            mgr = DFYManager(model, lambda: {"dfy_model": "vision", "language": "ru"}, data_root=data,
                             vision_lookup=lambda _m: True, browser_factory=lambda _v: FakeBrowser())
            task = await mgr.create("купи билет")
            await wait_for(lambda: model.calls >= 1)
            session = await mgr.takeover_start(task.id)
            self.assertEqual(task.public()["control"], "user")
            await mgr.cancel(task.id)
            out = {"status": task.status, "takeover": task.takeover, "last": task.last_takeover,
                   "control": task.public()["control"], "active": session.active}
            again = await mgr.takeover_end(task.id)
            out["end"] = again.status
            with self.assertRaises(RuntimeError):
                await mgr.takeover_start(task.id)
            await mgr.shutdown()
            return out
        out = asyncio.run(scenario())
        self.assertEqual(out["status"], "cancelled")
        self.assertIsNone(out["takeover"])
        self.assertIsNotNone(out["last"])
        self.assertEqual(out["control"], "ai")
        self.assertFalse(out["active"])
        self.assertEqual(out["end"], "cancelled")

    def test_finished_task_drops_the_takeover_too(self):
        src = (ROOT / "app" / "dfy.py").read_text(encoding="utf-8")
        run = src[src.index("    async def _run("):src.index("    async def _loop(")]
        self.assertIn("self._drop_takeover(task)", run[run.index("finally:"):], "the run's finally drops the takeover")
        cancel = src[src.index("    async def cancel("):src.index("    def _drop_takeover") if "    def _drop_takeover" in src else None]
        self.assertIn("self._drop_takeover(task)", cancel)

class StaticWiring(unittest.TestCase):
    def test_takeover_sync_is_wired(self):
        tk = (PG / "dfy-takeover.js").read_text(encoding="utf-8")
        self.assertRegex(tk, r"window\.MemuDFYTakeover = \{[^}]*\bsync\b")
        for path in (PG / "dfy-client.js", PG / "quick" / "quick-dfy.js"):
            text = path.read_text(encoding="utf-8")
            self.assertIn("MemuDFYTakeover.sync", text, path.name)
            self.assertIn('/dfy-takeover.js?v=3.5-r33', text, path.name)

    def test_dragsort_is_loaded_and_sidebar_is_not_native(self):
        html = (PG / "index.html").read_text(encoding="utf-8")
        self.assertIn('/memu-dragsort.js?v=', html)
        self.assertIn('/memu-dragsort.css?v=', html)
        css = (PG / "memu-dragsort.css").read_text(encoding="utf-8")
        self.assertIn(".side-group.section-drop > .chat-list-heading", css, "the sticky heading shows the «before» line")
        self.assertIn("html.memu-dragging .memu-tooltip", css, "no hover hints over the drop targets")
        app = (PG / "app.js").read_text(encoding="utf-8")
        item = re.search(r'<div class="chat-item[^`]*?data-chat-drag>', app)
        self.assertTrue(item and 'draggable="true"' not in item.group(0), "chat rows are not native-draggable")

    def test_prompt_manager_is_pointer_dragged(self):
        js = (CHAR / "main.js").read_text(encoding="utf-8")
        pm = js[js.index("function promptManager()"):js.index("function promptManager()") + 6000]
        self.assertIn("pmDragSort(list", pm)
        self.assertNotIn('draggable: "true"', pm)
        self.assertNotIn('addEventListener("dragstart"', pm)
        css = (CHAR / "style.css").read_text(encoding="utf-8")
        for sel in (".ch-pm-row.over-after", ".ch-pm-ghost", "html.ch-pm-dragging"):
            self.assertIn(sel, css)

    def test_glass_focus_ring_exception(self):
        glass = (PG / "designs" / "glass.css").read_text(encoding="utf-8")
        rule = re.search(r':root\[data-design="glass"\] :is\(([^)]*)\) input:focus-visible \{\s*outline: none; box-shadow: none;', glass)
        self.assertTrue(rule, "glass: embedded search inputs paint no second ring")
        for cls in (".plus-search", ".chat-search", ".hk-search", ".sk-search", ".mem2-search"):
            self.assertIn(cls, rule.group(1))
        for design in ("focus", "grid"):
            text = (PG / "designs" / f"{design}.css").read_text(encoding="utf-8")
            self.assertRegex(text, r'\.hk-search[ ,)][^{]*input[^{]*:focus-visible|\.hk-search input[^{]*:focus-visible', design)

    @unittest.skipUnless(shutil.which("node"), "no node")
    def test_node_check(self):
        for path in (PG / "memu-dragsort.js", PG / "dfy-takeover.js", PG / "dfy-client.js", CHAR / "main.js"):
            res = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"{path.name}: {res.stderr}")

BASE = "http://memu.test"
LIST_PAGE = """<!doctype html><html><head><meta charset=utf-8>
<link rel=stylesheet href="/memu-dragsort.css"><style>
body{margin:0;font:14px sans-serif} #box{position:absolute;left:20px;top:40px;width:260px;height:300px;overflow:auto;border:1px solid #888}
.it{height:36px;line-height:36px;padding:0 10px;border-bottom:1px solid #ddd}.drop{background:#fc0}
</style></head><body><div id=box></div><script src="/memu-dragsort.js"></script><script>
const box = document.getElementById('box');
for (let i = 0; i < 40; i++) { const d = document.createElement('div'); d.className = 'it'; d.dataset.i = i; d.textContent = 'Item ' + i; box.appendChild(d); }
window.log = { drops: [], clicks: [] };
box.addEventListener('click', (e) => { const it = e.target.closest('.it'); if (it) log.clicks.push(+it.dataset.i); });
MemuDragSort.bind(box, { item: '.it', scroller: () => box,
  target: (el) => { const it = el.closest('.it'); return it ? { el: it, cls: 'drop', i: +it.dataset.i } : null; },
  drop: (item, t) => log.drops.push([+item.dataset.i, t.i]) });
</script></body></html>"""

TK_PAGE = """<!doctype html><html><head><meta charset=utf-8></head><body><div id=chat></div><script>
window.PG_LANG = 'en'; window.toasts = []; window.toast = (t) => toasts.push(t);
window.WebSocket = class { constructor() { this.readyState = 1; } close() {} send() {} };
window.watchers = {};
window.MemuDFY = { watch(id, cb) { watchers[id] = cb; return () => { delete watchers[id]; }; } };
</script><script src="/dfy-takeover.js"></script></body></html>"""

@unittest.skipUnless(HAVE_PW, "no Playwright / Chromium")
class Browser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(executable_path=CHROMIUM, args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def page(self, html, api=None):
        ctx = self.browser.new_context(viewport={"width": 800, "height": 600})
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        def handle(route):
            url = route.request.url
            path = url[len(BASE):].split("?")[0]
            if path == "/":
                return route.fulfill(status=200, content_type="text/html", body=html)
            if path.startswith("/api/") and api:
                status, body = api(path, route.request.method)
                return route.fulfill(status=status, content_type="application/json", body=json.dumps(body))
            file = PG / path.lstrip("/")
            if file.is_file():
                ctype = "text/css" if file.suffix == ".css" else "application/javascript"
                return route.fulfill(status=200, content_type=ctype, body=file.read_bytes())
            return route.fulfill(status=404, body="")
        page.route(BASE + "/**", handle)
        page.goto(BASE + "/")
        self.addCleanup(ctx.close)
        return page, errors

    def test_dragsort_wheel_edge_drop_escape_click(self):
        page, errors = self.page(LIST_PAGE)
        top = lambda: page.evaluate("box.scrollTop")
        it = page.locator('.it[data-i="2"]').bounding_box()
        page.mouse.move(it["x"] + 40, it["y"] + 18)
        page.mouse.down()
        page.mouse.move(it["x"] + 44, it["y"] + 40, steps=4)
        page.wait_for_timeout(100)
        self.assertTrue(page.evaluate("MemuDragSort.active"))
        self.assertTrue(page.evaluate("!!document.querySelector('.memu-drag-ghost')"))
        self.assertTrue(page.evaluate("document.documentElement.classList.contains('memu-dragging')"))
        page.mouse.wheel(0, 300)
        page.wait_for_timeout(300)
        after_wheel = top()
        self.assertGreater(after_wheel, 100, "the wheel scrolls the list while dragging")

        page.mouse.move(it["x"] + 40, 40 + 300 + 30, steps=3)
        page.wait_for_function("box.scrollTop + box.clientHeight >= box.scrollHeight - 1", timeout=8000)

        last = page.locator('.it[data-i="39"]').bounding_box()
        page.mouse.move(last["x"] + 50, last["y"] + 18, steps=3)
        page.wait_for_timeout(100)
        self.assertTrue(page.evaluate("document.querySelector('.it[data-i=\"39\"]').classList.contains('drop')"))
        page.mouse.up()
        page.wait_for_timeout(100)
        self.assertEqual(page.evaluate("log.drops"), [[2, 39]])
        self.assertEqual(page.evaluate("log.clicks"), [], "a drop is not a click")
        self.assertFalse(page.evaluate("MemuDragSort.active || !!document.querySelector('.memu-drag-ghost, .drop')"))

        page.evaluate("box.scrollTop = 0")
        it = page.locator('.it[data-i="1"]').bounding_box()
        page.mouse.move(it["x"] + 40, it["y"] + 18); page.mouse.down()
        page.mouse.move(it["x"] + 40, it["y"] + 120, steps=5)
        page.keyboard.press("Escape"); page.mouse.up()
        page.wait_for_timeout(100)
        self.assertEqual(page.evaluate("log.drops.length"), 1)
        self.assertFalse(page.evaluate("MemuDragSort.active || !!document.querySelector('.memu-drag-ghost, .drop')"))

        page.wait_for_timeout(450)
        page.locator('.it[data-i="3"]').click()
        self.assertEqual(page.evaluate("log.clicks"), [3])
        self.assertEqual(errors, [])

    def test_dragsort_grab_at_the_edge_does_not_jump(self):
        page, errors = self.page(LIST_PAGE)
        page.evaluate("box.scrollTop = 200")
        it = page.evaluate("(() => { const r = box.getBoundingClientRect(); return [r.left, r.top]; })()")
        page.mouse.move(it[0] + 40, it[1] + 10); page.mouse.down()
        page.mouse.move(it[0] + 48, it[1] + 12, steps=3)
        page.wait_for_timeout(400)
        self.assertTrue(page.evaluate("MemuDragSort.active"))
        self.assertEqual(page.evaluate("box.scrollTop"), 200, "auto-scroll arms only after leaving the edge / 24 px")
        page.mouse.move(it[0] + 48, it[1] - 30, steps=3)
        page.wait_for_function("box.scrollTop < 200", timeout=4000)
        page.keyboard.press("Escape"); page.mouse.up()
        self.assertEqual(errors, [])

    def test_takeover_bar_follows_the_task(self):
        calls = []

        def api(path, method):
            calls.append(path)
            if path.endswith("/t2/takeover"):
                return 409, {"error": "The task is not running"}
            return 200, {"ok": True, "status": "running"}
        page, errors = self.page(TK_PAGE, api)
        self.assertTrue(page.evaluate("MemuDFYTakeover.open('t1')"))
        self.assertFalse(page.evaluate("document.querySelector('.dfyt-layer').hidden"))
        page.evaluate("MemuDFYTakeover.minimize()")
        pill = "(() => { const p = document.querySelector('.dfyt-pill'); return !!p && !p.hidden; })()"
        self.assertFalse(page.evaluate(pill), "no card of the task in this chat: the bar is hidden")
        page.evaluate("document.getElementById('chat').innerHTML = '<div data-dfy-card=\"t1\"></div>'")
        page.wait_for_function(pill, timeout=3000)
        page.evaluate("document.getElementById('chat').innerHTML = ''")
        page.wait_for_function("!" + pill, timeout=3000)
        page.evaluate("document.getElementById('chat').innerHTML = '<div data-dfy-card=\"t1\"></div>'")
        page.wait_for_function(pill, timeout=3000)
        self.assertTrue(page.evaluate("typeof watchers.t1 === 'function'"), "the minimized bar watches its task")
        page.evaluate("watchers.t1({ id: 't1', status: 'cancelled', control: 'ai' })")
        self.assertFalse(page.evaluate(pill))
        self.assertEqual(page.evaluate("MemuDFYTakeover.taskId"), "")
        self.assertIn("The DFY task was stopped", page.evaluate("toasts"))
        self.assertFalse(page.evaluate("'t1' in watchers"))

        self.assertTrue(page.evaluate("MemuDFYTakeover.open('t1')"))
        page.evaluate("MemuDFYTakeover.minimize()")
        page.evaluate("MemuDFYTakeover.sync({ id: 't1', status: 'running', control: 'ai' })")
        self.assertEqual(page.evaluate("MemuDFYTakeover.taskId"), "t1", "an update right after the takeover is ignored")
        page.wait_for_timeout(1600)
        page.evaluate("MemuDFYTakeover.sync({ id: 't1', status: 'running', control: 'ai' })")
        self.assertEqual(page.evaluate("MemuDFYTakeover.taskId"), "")

        page.evaluate("MemuDFYTakeover.open('t1').then(() => MemuDFYTakeover.minimize())")
        page.wait_for_timeout(100)
        page.evaluate("document.getElementById('chat').innerHTML = '<div data-dfy-card=\"t2\"></div>'")
        self.assertFalse(page.evaluate("MemuDFYTakeover.open('t2')"))
        self.assertFalse(page.evaluate(pill))
        self.assertFalse(page.evaluate("MemuDFYTakeover.minimized"))
        self.assertEqual(errors, [])

if __name__ == "__main__":
    unittest.main()
