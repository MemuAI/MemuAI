"""Round 30 (r30-quick-core): Quick Ask — copying, Computer Use in the window, audit fixes.

Run: NO_PROXY=127.0.0.1,localhost MEMUAI_DATA_DIR=/tmp/r30qc python -m unittest tests.round30_quick_core_test
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import automation_runtime as ar
from tests.round26_automation_test import FakeUser32, FakeWindows, Script, _short_sleep, desktop

ROOT = Path(__file__).resolve().parents[1]
QUICK = ROOT / "app" / "pgstatic" / "quick"
NODE = shutil.which("node")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class ComputerUseSteps(unittest.TestCase):
    """The runtime keeps one row per step with a thumbnail and the target point; a rejection skips the action."""

    def run_agent(self, model, decide, **kwargs):
        user32 = FakeUser32([(0, 0, 1920, 1080)], [{"title": "Блокнот", "rect": [100, 100, 900, 700]}])
        runtime = ar.ComputerAutomation()
        seen = []

        async def scenario():
            runtime.job = asyncio.create_task(ar.run_computer_agent(runtime, model, model="vision", task="Нажми Сохранить",
                                                                    ask_each_action=True, lang="ru", **kwargs))
            while not runtime.job.done():
                await asyncio.sleep(0.01)
                if runtime.pending_action:
                    state = await runtime.public_state()
                    seen.append(state)
                    decide(runtime, len(seen))
            return seen

        with FakeWindows(user32, desktop((1920, 1080))), patch.object(ar.asyncio, "sleep", new=_short_sleep):
            asyncio.run(scenario())
        return runtime, seen

    def test_steps_thumbnails_point_and_approve_all(self):
        model = Script(['{"action":"click","monitor":1,"x":700,"y":394,"reason":"Сохранить"}',
                        '{"action":"key","keys":"ctrl+s"}',
                        '{"action":"done","report":"Сохранено"}'])
        runtime, seen = self.run_agent(model, lambda rt, n: rt.approve(True, all_steps=True))
        self.assertEqual((runtime.status, runtime.report), ("done", "Сохранено"))
        self.assertEqual(len(seen), 1, "«Разрешить всё до конца» — no second question")
        first = seen[0]
        self.assertEqual((first["step"], first["max_steps"], first["ask_each"]), (1, 12, True))
        self.assertEqual(first["pending_action"]["step"], 1)
        row = first["steps"][0]
        self.assertEqual((row["n"], row["status"], row["action"], row["shots"]), (1, "pending", "click", [1]))
        self.assertTrue(row["description"].startswith("нажать в точке (700, 394)"))
        self.assertAlmostEqual(row["point"][0], 0.5, places=2)
        self.assertEqual([r["status"] for r in runtime.steps], ["ok", "ok", "done"])
        shot = runtime.shot(1)
        self.assertTrue(shot and shot[:2] == b"\xff\xd8", "a JPEG thumbnail")
        from PIL import Image
        import io
        self.assertEqual(Image.open(io.BytesIO(shot)).width, ar.THUMB_WIDTH)
        self.assertIsNone(runtime.shot(99))
        self.assertFalse(runtime.ask_each)
        # the thumbnail bytes never reach the model's prompt
        self.assertNotIn("thumb", str(model.prompts[0][1]["content"][0]["text"]))

    def test_reject_skips_the_action_and_passes_the_hint(self):
        model = Script(['{"action":"click","monitor":1,"x":10,"y":10}',
                        '{"action":"click","monitor":1,"x":700,"y":394}',
                        '{"action":"done","report":"ok"}'])

        def decide(rt, n):
            if n == 1:
                rt.approve(False, note="кнопка справа")
            else:
                rt.approve(True)
        runtime, _seen = self.run_agent(model, decide)
        self.assertEqual(runtime.status, "done", "one rejection does not end the run")
        self.assertEqual(runtime.steps[0]["status"], "rejected")
        self.assertEqual(runtime.steps[0]["error"], "кнопка справа")
        second_prompt = model.prompts[1][1]["content"][0]["text"]
        self.assertIn("REJECTED by the user", second_prompt)
        self.assertIn("кнопка справа", second_prompt)

    def test_three_rejections_in_a_row_stop(self):
        model = Script(['{"action":"click","monitor":1,"x":10,"y":10}'] * 5)
        runtime, seen = self.run_agent(model, lambda rt, n: rt.approve(False))
        self.assertEqual(runtime.status, "cancelled")
        self.assertEqual(len(seen), ar.MAX_REJECTED_STEPS)
        self.assertIn("отклонены", runtime.error)

    def test_stop_while_waiting_is_cancel_not_reject(self):
        model = Script(['{"action":"click","monitor":1,"x":10,"y":10}'])

        def decide(rt, n):
            asyncio.get_event_loop().create_task(rt.stop())
        runtime, _ = self.run_agent(model, decide)
        self.assertEqual((runtime.status, runtime.error), ("cancelled", "Остановлено пользователем"))

    def test_capture_guard_wraps_every_screenshot(self):
        calls = []

        @contextlib.contextmanager
        def guard():
            calls.append("in")
            yield
            calls.append("out")
        model = Script(['{"action":"done","report":"ok"}'])
        user32 = FakeUser32([(0, 0, 1280, 720)])
        runtime = ar.ComputerAutomation()
        runtime.capture_guard = guard
        with FakeWindows(user32, desktop((1280, 720))), patch.object(ar.asyncio, "sleep", new=_short_sleep):
            asyncio.run(ar.run_computer_agent(runtime, model, model="v", task="x", ask_each_action=False))
        self.assertEqual(calls, ["in", "out"])

    def test_step_point(self):
        screen = {"screens": [{"monitor": 1, "width": 1000, "height": 500}, {"monitor": 2, "width": 800, "height": 600}]}
        self.assertEqual(ar._step_point({"x": 500, "y": 250}, screen), [0.5, 0.5])
        self.assertEqual(ar._step_point({"monitor": 2, "x1": 400, "y1": 150, "x2": 1, "y2": 1}, screen), [0.5, 0.25])
        self.assertIsNone(ar._step_point({"action": "type", "text": "a"}, screen))
        self.assertIsNone(ar._step_point({"x": 5000, "y": 1}, screen))

    def test_public_state_keeps_the_run_error(self):
        runtime = ar.ComputerAutomation()
        runtime.status, runtime.error = "running", "настоящая ошибка"
        with patch.object(ar.ComputerAutomation, "_monitors", side_effect=OSError("no windll")):
            state = asyncio.run(runtime.public_state())
        self.assertEqual((state["error"], state["monitors"]), ("настоящая ошибка", []))


class ComputerUseRoutes(unittest.TestCase):
    def test_action_and_shot_routes(self):
        os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="r30qc-"))
        from fastapi.testclient import TestClient
        from app.playground_api import create_app
        app = create_app()
        runtime = next(r for r in (getattr(route.endpoint, "__closure__", None) or [] for route in app.routes
                                    if getattr(route, "path", "") == "/api/work/computer/state")
                       for r in [c.cell_contents for c in r] if isinstance(r, ar.ComputerAutomation))
        client = TestClient(app, base_url="http://127.0.0.1")
        runtime._shots[3] = {1: b"\xff\xd8jpeg"}
        resp = client.get("/api/work/computer/shot/3")
        self.assertEqual((resp.status_code, resp.headers["content-type"], resp.content), (200, "image/jpeg", b"\xff\xd8jpeg"))
        self.assertEqual(client.get("/api/work/computer/shot/4").status_code, 404)
        loop = asyncio.new_event_loop()
        runtime._approval = loop.create_future()
        self.assertEqual(client.post("/api/work/computer/action", json={"action": "approve_all"}).json(), {"ok": True})
        self.assertTrue(runtime._approval.result())
        self.assertFalse(runtime.ask_each)
        runtime._approval = loop.create_future()
        client.post("/api/work/computer/action", json={"action": "reject", "note": "иначе"})
        self.assertFalse(runtime._approval.result())
        self.assertEqual(runtime._reject_note, "иначе")
        self.assertEqual(client.post("/api/work/computer/action", json={"action": "zzz", "lang": "en"}).json()["error"], "Unknown action")
        loop.close()


class QuickWindowWiring(unittest.TestCase):
    def test_index_loads_new_files_in_order(self):
        html = read(QUICK / "index.html")
        for name in ("quick-copy.css", "quick-computer.css"):
            self.assertIn(f"/quick/{name}?v=", html)
        order = [html.index(f"/quick/{n}?v=") for n in ("quick.js", "quick-copy.js", "quick-computer.js", "quick-dfy.js")]
        self.assertEqual(order, sorted(order))

    def test_strings_are_bilingual(self):
        for name in ("quick-copy.js", "quick-computer.js"):
            src = read(QUICK / name)
            for ru, en in re.findall(r't\("([^"]*[а-яё][^"]*)",\s*"([^"]*)"\)', src, flags=re.I):
                self.assertTrue(en and not re.search("[а-яё]", en, re.I), (name, ru, en))

    def test_commands_hand_computer_use_over(self):
        self.assertIn("window.QuickComputer.start(card)", read(QUICK / "plugins" / "commands.js"))
        self.assertIn("S.keepOnTop", read(QUICK / "quick.js"))


@unittest.skipUnless(NODE, "node is required")
class CopyAndMarkdownInNode(unittest.TestCase):
    """quick-md.js in node with a stub document (KaTeX absent → math renders as <code class="math-src">)."""

    def node(self, script: str) -> str:
        return subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30, check=True).stdout.strip()

    def test_math_never_eats_dollar_runs(self):
        md = str(QUICK / "quick-md.js").replace("\\", "/")
        out = self.node(f"""
globalThis.window = globalThis; globalThis.document = {{ documentElement: {{ lang: "ru" }} }};
require({md!r});
const r = (s) => (window.QuickMarkdown.render(s).match(/math-src/g) || []).length;
console.log([r("| Модель | Цена |\\n|---|---|\\n| Умная | $$$ |"), r("цена $5 и $10"), r("формула $x^2$ тут"), r("$$a+b$$")].join(","));""")
        self.assertEqual(out, "0,0,1,1")


class AnswerFollow(unittest.TestCase):
    def test_follow_is_user_driven(self):
        src = read(QUICK / "quick.js")
        self.assertIn("answerFollow.on", src)
        self.assertIn('for (const ev of ["wheel", "touchmove", "pointerdown", "keydown"])', src)
        self.assertIn("answerFollow.watch(box.lastElementChild)", src)


class PlainPreview(unittest.TestCase):
    """r30-quick-core-2: the «Недавние» preview is plain text, not Markdown source."""

    def test_markdown_marks_are_dropped(self):
        from app import quick_ask_core as core
        cases = {
            "## Итог\n\nВот **главное** по теме:\n- первый `пункт`": "Итог Вот главное по теме: первый пункт",
            "> цитата\n1. раз\n2) два": "цитата раз два",
            "<think>рассуждаю</think>Ответ [ссылка](https://x.y) и ![](a.png)": "Ответ ссылка и",
            "| A | B |\n|---|:---:|\n| 1 | 2 |": "A B 1 2",
            "```python\nprint(1)\n```": "print(1)",
            "Цена $5, формула $$x$$ ~~старое~~": "Цена 5, формула x старое",
        }
        for src, want in cases.items():
            self.assertEqual(core.plain_preview(src), want, src)
        self.assertEqual(core.plain_preview(None), "")
        self.assertEqual(len(core.plain_preview("слово " * 100)), 160)
        self.assertEqual(core.plain_preview("**a**", limit=1), "a")

    def test_chat_summary_uses_it(self):
        from app import quick_ask_core as core
        row = core.chat_summary({"id": "c1", "turns": [{"q": "вопрос", "a": "## Итог\n**да**"}]})
        self.assertEqual(row["preview"], "Итог да")
        self.assertEqual(core.chat_summary({"id": "c2", "turns": [{"q": "только *вопрос*", "a": ""}]})["preview"], "только *вопрос*")


class QuickCore2Contracts(unittest.TestCase):
    """r30-quick-core-2 audit fixes that must not come back (static contracts; the headless class below runs them)."""

    def test_panels_toggle_is_a_footer_button(self):
        canvas = read(QUICK / "plugins" / "canvas.js")
        self.assertIn("Q.addComposerButton({", canvas)
        self.assertRegex(canvas, r'id: "canvas", order: \d+, icon: UI\.grid, menu: false')
        self.assertIn("const BAR_PLUGINS = 2;", read(QUICK / "quick.js"))
        self.assertNotIn("data-empty", read(QUICK / "plugins" / "canvas.css"))

    def test_toast_sits_on_the_footer(self):
        css = read(QUICK / "quick-ux.css")
        self.assertRegex(css, r"\.qa-toast \{ bottom: 7px; max-width: calc\(100% - 24px\);[^}]*white-space: normal")
        self.assertIn('/quick/quick-ux.css?v=', read(QUICK / "index.html"))

    def test_region_mic_dfy_and_vision_texts(self):
        self.assertIn('" · " + t("область экрана", "screen region"), { page: false, prompt: row[2] }', read(QUICK / "plugins" / "features.js"))
        commands = read(QUICK / "plugins" / "commands.js")
        self.assertIn("function micError(e)", commands)
        self.assertIn("Q.toast(micError(e)", commands)
        dfy = read(QUICK / "quick-dfy.js")
        self.assertIn("D.notice = { text: notReadyText(st)", dfy)
        self.assertIn('handoff: { settings: "dfy" }', dfy)
        self.assertRegex(read(ROOT / "app" / "pgstatic" / "app.js"), r"dfy")
        settings = read(QUICK / "plugins" / "features-settings.js")
        self.assertIn("(со «Зрением»)", settings)
        self.assertNotIn("👁", settings)

    def test_plugins_loaded_after_open_see_the_window(self):
        js = read(QUICK / "quick.js")
        self.assertIn("shown: !!S.shownOnce", js)
        self.assertIn("S.shownOnce = true;", js)

    def test_appearance_extras_reach_the_window(self):
        from app import appearance_extra
        js = read(QUICK / "quick.js")
        rows = dict((key, values) for key, values in re.findall(r'\["(ui_[a-z_]+)", "[a-zA-Z]+", \[([^\]]*)\]\]', js))
        self.assertEqual(set(rows), {"ui_radius", "ui_glass", "ui_contrast", "ui_density", "ui_motion_speed"})
        for key, values in rows.items():
            allowed = set(re.findall(r'"([a-z]+)"', values))
            if key in appearance_extra.CHOICES:
                self.assertEqual(allowed, set(appearance_extra.CHOICES[key]) - {""}, key)
        self.assertIn('"ui_density": ""', read(ROOT / "app" / "pgdata.py"))
        api = read(ROOT / "app" / "quick_ask_api.py")
        for key in rows:
            self.assertIn(f'"{key}"', api, "/api/quick/config sends " + key)
        html = read(QUICK / "index.html")
        self.assertLess(html.index("/designs/quick-extra.css"), html.index("/quick/quick-appearance.css?v="), "after the designs")

    def test_selection_row_for_files(self):
        src = read(QUICK / "quick-selection.js")
        self.assertIn('const FILE_SKIP = new Set(["fix", "reply"]);', src)
        self.assertIn("if (file && FILE_SKIP.has(id)) continue;", src)


CHROME = os.environ.get("MEMU_TEST_CHROMIUM") or ("/usr/bin/chromium" if os.path.exists("/usr/bin/chromium") else "/workspace/work/chromium/chrome")


@unittest.skipUnless(os.path.exists(CHROME), "no Chromium")
class QuickWindowHeadless(unittest.TestCase):
    """The real quick/index.html over a static server with mocked /api: appearance extras, the document chip and the
    selection row for files."""

    @classmethod
    def setUpClass(cls):
        try:
            import playwright  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("no Playwright")
        import functools
        import http.server
        import threading

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

    def run_page(self, prefs: dict, show: str, script: str, width: int = 720):
        from playwright.async_api import async_playwright
        from app import themes
        config = {"prefs": {"theme": "dark", "accent_color": "violet", "ui_design": "classic", "language": "ru",
                            "quick_ask_model": "m1", **prefs},
                  "status": {"native": True, "pinned": False, "hotkey": {"ok": True, "hotkey": "Alt+Space", "code": "registered"}},
                  "ui": {"onboarded": True}, "endpoint": {"source": "proxy"}}

        async def route(route_obj, request):
            path = "/" + request.url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]
            if path == "/themes.css":
                return await route_obj.fulfill(status=200, content_type="text/css", body=themes.theme_css())
            if path == "/api/quick/config":
                return await route_obj.fulfill(json=config)
            if path == "/api/pg/state":
                return await route_obj.fulfill(json={"models": [{"id": "m1", "caps": {"vision": True}}], "image_models": []})
            if path == "/api/work/computer/state":
                return await route_obj.fulfill(json={"status": "idle", "logs": []})
            if path.startswith("/api/"):
                return await route_obj.fulfill(json={"items": [], "events": [], "seq": 0, "ok": True})
            return await route_obj.continue_()

        async def main():
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
                page = await browser.new_page(viewport={"width": width, "height": 640})
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                await page.route("**/*", route)
                await page.goto(self.base + "/quick/?native=1", wait_until="load")
                await page.wait_for_function("window.QuickAsk && document.documentElement.dataset.design")
                await page.evaluate("QuickAsk.onShow(" + show + ")")
                await page.wait_for_timeout(500)
                result = await page.evaluate(script)
                await browser.close()
                return result, errors

        return asyncio.run(main())

    SHOW = "{fresh:true, maxHeight:640, rounded:true, corner:'dwm', radius:8, pinned:false, source:'hotkey', page:null%s}"

    def test_appearance_extras_apply(self):
        probe = """(() => { const r = document.documentElement, cs = (el, p) => getComputedStyle(el).getPropertyValue(p).trim();
          const row = document.querySelector('.qa-row');
          return { attrs: [r.dataset.radius, r.dataset.glass, r.dataset.contrast, r.dataset.density, r.dataset.motionSpeed],
                   radius: cs(r, '--qa-radius'), surface: cs(r, '--qa-surface'), rowH: row ? row.getBoundingClientRect().height : 0,
                   rowR: row ? cs(row, 'border-radius') : '' }; })()"""
        on, errors = self.run_page({"ui_radius": "round", "ui_glass": "strong", "ui_contrast": "more", "ui_density": "spacious",
                                    "ui_motion_speed": "fast"}, self.SHOW % "", probe)
        self.assertEqual(errors, [])
        self.assertEqual(on["attrs"], ["round", "strong", "more", "spacious", "fast"])
        self.assertEqual(on["radius"], "24px")
        self.assertEqual(on["rowR"], "11px")
        self.assertGreaterEqual(on["rowH"], 42)
        off, _ = self.run_page({"ui_radius": "zzz", "ui_density": "compact"}, self.SHOW % "", probe)
        self.assertEqual(off["attrs"][:4], [None, None, None, "compact"])
        self.assertEqual(off["radius"], "14px")
        self.assertLess(off["rowH"], 34)
        square, _ = self.run_page({"ui_radius": "round", "ui_design": "grid"}, self.SHOW % "", probe)
        self.assertEqual(square["radius"], "4px", "square designs keep their corners")

    def test_document_chip_and_selection_row_for_files(self):
        probe = """(() => ({ name: (document.querySelector('.qa-chip-sel .tl') || {}).textContent || '',
            quote: !!document.querySelector('.qa-chip-sel q'), title: (document.querySelector('.qa-chip-sel') || {}).title || '',
            acts: [...document.querySelectorAll('.qsel-act .tx')].map((e) => e.textContent) }))()"""
        file_show = self.SHOW % ", selection: '[Файл: report.pdf]\\nДоговор поставки №7', selection_info: {source: 'file'}"
        got, errors = self.run_page({}, file_show, probe)
        self.assertEqual(errors, [])
        self.assertEqual(got["name"], "report.pdf")
        self.assertFalse(got["quote"], "no «[Файл: …] text…» quote")
        self.assertIn("Договор поставки", got["title"])
        self.assertIn("Кратко", got["acts"])
        self.assertFalse({"Исправить", "Ответить"} & set(got["acts"]), got["acts"])
        # a narrow window: the empty field grows with its placeholder (it was cut to one line after the placeholder changed)
        tall, _ = self.run_page({"language": "en", "ui_design": "terminal"}, file_show,
                                "(() => { const i = document.getElementById('qa-input'); return [i.placeholder, i.clientHeight, i.scrollHeight]; })()", width=420)
        self.assertEqual(tall[0], "What should I do with the file?")
        self.assertGreaterEqual(tall[1], tall[2], tall)
        text_show = self.SHOW % ", selection: 'Привет, как дела у проекта?', selection_info: {source: 'text'}"
        got, _ = self.run_page({}, text_show, probe)
        self.assertTrue({"Исправить", "Ответить"} <= set(got["acts"]), got["acts"])


if __name__ == "__main__":
    unittest.main()
