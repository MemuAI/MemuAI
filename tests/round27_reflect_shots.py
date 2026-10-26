"""Round 27: headless screenshots of «Рефлексия» and «Время и фокус» (run by hand, look at the PNGs).

    python tests/round27_reflect_shots.py OUT_DIR

The REAL settings UI (tests/appearance_fixture.py) talks to a REAL app/reflect.py server on a local port
(synthetic history from tests/round27_reflect_data.py, a scripted model — no network). Shots: report ru/en,
dark/light, every design, narrow window, loading, model error, empty period, few conversations, «Время и
фокус», the quiet-hours reminder before sending and the break reminder. Prints problems (page errors,
horizontal overflow, untranslated text) and exits 1 when there are any.
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA = Path(tempfile.mkdtemp(prefix="r27-reflect-shots-"))
os.environ["MEMUAI_DATA_DIR"] = str(DATA)
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")

from tests.round27_reflect_data import make_history  # noqa: E402

PORT = int(os.environ.get("REFLECT_SHOTS_PORT", "18391"))
FINAL = {
    "ru": {"headline": "Месяцы кода, испанского и одной поездки",
           "summary": "Чаще всего вы разбирались с FastAPI в проекте MemuAI: чинили ошибку сериализации, писали тесты для парсера SSE и рефакторили storage.py. Параллельно учили испанский — прошедшие времена и переводы с разбором ошибок — и спланировали пять дней в Тбилиси с поездкой в Кахетию.",
           "topics": [["Код MemuAI", "FastAPI, тесты, рефакторинг и код-ревью", 41], ["Испанский", "Грамматика прошедшего времени и переводы", 19],
                      ["Письма и тексты", "Деловые письма в вашем тоне, переводы", 16], ["Путешествия", "План на Тбилиси и Кахетию", 12],
                      ["Быт и здоровье", "Бюджет, ужины, тренировки дома", 12]],
           "skills": {"delegation": ["Рутину — тесты, переводы, письма — вы отдаёте сразу, а архитектурные решения по storage.py оставляете себе и просите объяснить выбор.", "Создайте Work-проект MemuAI, чтобы не пересказывать структуру проекта в каждом чате.", "project"],
                      "description": ["Первые сообщения подробные: трейсбек, версия Python, ограничения бюджета. Контекст MemuAI повторяется в нескольких чатах.", "Сохраните стек и правила проекта в системный промпт.", "prompt"],
                      "discernment": ["Вы ловите ошибки: «не так, ошибка в сериализации», просите проверить, что тест падает на старой версии.", "Попросите модель перечислить допущения до ответа — так ошибки видны раньше.", "none"],
                      "diligence": ["Письма вы переписываете своими словами и просите источники цен для поездки.", "Сделайте навык «проверка фактов» для планов поездок и цен.", "skill"]}},
    "en": {"headline": "Months of code, Spanish and one trip",
           "summary": "Most of your time went into FastAPI work on the MemuAI project: fixing a serialization bug, writing SSE parser tests and refactoring storage.py. Alongside, you practised Spanish past tenses with corrected translations and planned five days in Tbilisi with a day trip to Kakheti.",
           "topics": [["MemuAI code", "FastAPI, tests, refactoring and code review", 41], ["Spanish", "Past-tense grammar and translations", 19],
                      ["Emails & writing", "Business emails in your voice, translations", 16], ["Travel", "A Tbilisi and Kakheti plan", 12],
                      ["Home & health", "Budget, dinners, home workouts", 12]],
           "skills": {"delegation": ["You hand off routine work — tests, translations, emails — and keep the storage.py design decisions, asking for the reasoning.", "Start a MemuAI Work project so you don't re-explain the codebase in every chat.", "project"],
                      "description": ["Your first messages are specific: tracebacks, Python version, budget limits. The MemuAI context repeats across chats.", "Save your stack and project rules as a system prompt.", "prompt"],
                      "discernment": ["You catch mistakes — \"wrong, the bug is in serialization\" — and ask to confirm a test fails on the old code.", "Ask the model to list its assumptions before answering.", "none"],
                      "diligence": ["You rewrite emails in your own words and ask for sources behind trip prices.", "Make a fact-checking skill for trip plans and prices.", "skill"]}},
}

def final_json(lang: str) -> str:
    d = FINAL[lang]
    return json.dumps({"headline": d["headline"], "summary": d["summary"],
                       "topics": [{"name": n, "description": t, "percent": p} for n, t, p in d["topics"]],
                       "skills": {k: {"observation": o, "next_step": s, "action": a} for k, (o, s, a) in d["skills"].items()}},
                      ensure_ascii=False)

class Model:
    """Scripted model: mode ok | slow (waits for release) | error."""

    def __init__(self):
        self.mode = "ok"
        self.release = None

    async def __call__(self, model, messages):
        lang = "en" if "Write in English" in messages[0]["content"] else "ru"
        if self.mode == "error":
            raise RuntimeError("HTTP 503 upstream unavailable")
        if self.mode == "slow":
            await asyncio.sleep(3600)
        return final_json(lang)

def start_server(model: Model):
    import uvicorn
    from fastapi import FastAPI, Request
    from fastapi.middleware.cors import CORSMiddleware
    from app import pgdata, reflect

    app = FastAPI()
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    reflect.register(app, completion=model)

    @app.post("/__ctl")
    async def ctl(request: Request):
        body = await request.json()
        if "mode" in body:
            model.mode = body["mode"]
        if "prefs" in body:
            pgdata.save_preferences(body["prefs"])
        if body.get("clear"):
            reflect.clear_store()
            app.state.reflect_jobs.items.clear()
        return {"ok": True}

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    return server

def write_data():
    chats, quick = make_history()
    folder = DATA / "memuai"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "chats.json").write_text(json.dumps(chats, ensure_ascii=False), encoding="utf-8")
    (folder / "quick_chats.json").write_text(json.dumps(quick, ensure_ascii=False), encoding="utf-8")

BRIDGE = """<script>
(() => {
  const mock = window.fetch, real = window.__realFetch, base = 'http://127.0.0.1:__PORT__';
  window.fetch = (url, options) => {
    const path = String(url);
    return path.startsWith('/api/reflect') || path.startsWith('/api/focus') ? real(base + path, options) : mock(url, options);
  };
})();
</script>"""

def build(tmp: Path) -> Path:
    """The fixture mocks window.fetch; /api/reflect and /api/focus go to the real reflect server instead."""
    from tests.appearance_fixture import build_fixture
    fixture = build_fixture(tmp)
    html = fixture.read_text(encoding="utf-8")
    html = html.replace("<head><script>", "<head><script>window.__realFetch = window.fetch;</script><script>", 1)
    pos = html.index("</script>", html.index("window.__qa_requests")) + len("</script>")
    html = html[:pos] + BRIDGE.replace("__PORT__", str(PORT)) + html[pos:]
    fixture.write_text(html, encoding="utf-8")
    return fixture

def ctl(body):
    import httpx
    httpx.post(f"http://127.0.0.1:{PORT}/__ctl", json=body, timeout=10, trust_env=False)

def quick_shots(browser, out: Path, problems: list) -> None:
    """The real Quick Ask page from app/pgstatic over file-less routing: /api/focus/* answer like app/reflect.py."""
    from app import themes
    static = ROOT / "app" / "pgstatic"
    show = "{fresh:true, maxHeight:640, rounded:true, corner:'dwm', radius:8, pinned:false, source:'hotkey', page:null}"
    for lang, theme in (("ru", "dark"), ("en", "light")):
        focus = {"quiet": {"until": "07:00", "ends_at": int(time.time() * 1000) + 3_600_000}, "break_enabled": True, "due": False}
        prefs = {"theme": theme, "ui_design": "classic", "language": lang, "quick_ask_model": "demo"}

        def route(route_obj, request, focus=focus, prefs=prefs):
            path = "/" + request.url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]
            if path == "/themes.css":
                return route_obj.fulfill(status=200, content_type="text/css", body=themes.theme_css())
            if path == "/api/quick/config":
                return route_obj.fulfill(json={"prefs": prefs, "status": {"native": True, "pinned": False, "hotkey": {"ok": True, "hotkey": "Alt+Space", "code": "registered"}},
                                               "ui": {"onboarded": True}, "endpoint": {"source": "proxy"}})
            if path == "/api/pg/state":
                return route_obj.fulfill(json={"models": [{"id": "demo", "caps": {}}], "image_models": []})
            if path == "/api/focus/state":
                return route_obj.fulfill(json={"quiet": focus["quiet"], "break_enabled": focus["break_enabled"], "break_minutes": 60})
            if path == "/api/focus/activity":
                return route_obj.fulfill(json={"minutes_today": 64, "break_due": focus["due"], "threshold": 60, "quiet": focus["quiet"]})
            if path == "/api/quick/ask":
                return route_obj.fulfill(status=200, content_type="text/event-stream",
                                         body='data: {"event": "delta", "text": "OK"}\n\ndata: {"event": "done", "model": "demo"}\n\n')
            if path.startswith("/api/"):
                return route_obj.fulfill(json={"items": [], "ok": True, "events": [], "seq": 0})
            file = static / path.lstrip("/")
            if path.endswith("/"):
                file = file / "index.html"
            if file.is_file():
                kind = {".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".html": "text/html", ".png": "image/png"}.get(file.suffix, "application/octet-stream")
                return route_obj.fulfill(status=200, content_type=kind, body=file.read_bytes())
            return route_obj.fulfill(status=404, body="")

        page = browser.new_page(viewport={"width": 720, "height": 520})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("http://memu.test/**", route)
        page.goto("http://memu.test/quick/?native=1", wait_until="load")
        page.wait_for_function("!!window.MemuQuickFocus", timeout=15000)
        page.evaluate("QuickAsk.onShow(" + show + ")")
        try:
            page.wait_for_selector(".qf-card[data-kind='quiet']", timeout=5000)
        except Exception:
            problems.append(f"quick-{lang}: no quiet-hours strip on opening")
        time.sleep(0.4)
        page.screenshot(path=str(out / f"quick-quiet-{lang}-{theme}.png"))
        page.click(".qf-card [data-i='0']")
        time.sleep(0.2)
        if page.query_selector(".qf-card[data-kind='quiet']") and page.is_visible(".qf-card[data-kind='quiet']"):
            problems.append(f"quick-{lang}: «continue» did not hide the strip")
        focus["due"] = True
        page.fill("#qa-input", "Сколько будет 2+2?" if lang == "ru" else "What's 2+2?")
        page.press("#qa-input", "Enter")
        try:
            page.wait_for_selector(".qf-card[data-kind='break']", timeout=5000)
        except Exception:
            problems.append(f"quick-{lang}: no break strip after a request")
        time.sleep(0.5)
        page.screenshot(path=str(out / f"quick-break-{lang}-{theme}.png"))
        if errors:
            problems.append(f"quick-{lang}: page errors {errors[:3]}")
        page.close()

def main(out: Path) -> int:
    from playwright.sync_api import sync_playwright
    out.mkdir(parents=True, exist_ok=True)
    write_data()
    model = Model()
    start_server(model)
    import httpx
    for _ in range(100):
        try:
            if httpx.get(f"http://127.0.0.1:{PORT}/api/focus/state", timeout=2, trust_env=False).status_code == 200:
                break
        except Exception:
            time.sleep(0.1)
    tmp = Path(tempfile.mkdtemp(prefix="r27-reflect-fx-"))
    fixture = build(tmp)
    problems = []
    chromium = os.environ.get("MEMU_TEST_CHROMIUM", "/workspace/work/chromium/chrome")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chromium if Path(chromium).exists() else None, args=["--no-sandbox"])

        def page_for(lang="ru", theme="dark", design="classic", width=1360, height=900):
            ctx = browser.new_context(viewport={"width": width, "height": height})
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            prefs = {"theme": theme, "ui_design": design, "ui_motion": "off", "language": lang, "default_model": "demo-claude"}
            page.add_init_script(f"""try {{ localStorage.setItem('memu_theme', '{theme}'); localStorage.setItem('memu_design', '{design}');
              localStorage.setItem('memu_lang', '{lang}'); localStorage.setItem('qa_prefs', JSON.stringify({json.dumps(prefs)})); }} catch (_) {{}}""")
            page.goto(fixture.as_uri(), wait_until="domcontentloaded")
            page.wait_for_function("document.body.classList.contains('boot-ready') && typeof showSettings === 'function' && !!window.MemuReflect", timeout=90000)
            page.evaluate(f"document.documentElement.dataset.design = '{design}'")
            return ctx, page, errors

        def open_page(page, tab):
            page.evaluate(f"async () => {{ currentSettingsTab = '{tab}'; await showSettings(true); }}")
            page.wait_for_function(f"!!document.querySelector('#pane-{tab}.active')", timeout=10000)

        def check(page, tag, errors):
            over = page.evaluate("""() => { const b = document.querySelector('#panel .settings-body'); if (!b) return 0;
              return [...b.querySelectorAll('#pane-reflect.active *, #pane-focus.active *')].filter((n) => n.getBoundingClientRect().right > b.getBoundingClientRect().right + 2 && n.offsetParent).length; }""")
            if over:
                problems.append(f"{tag}: {over} elements overflow horizontally")
            if errors:
                problems.append(f"{tag}: page errors {errors[:3]}")

        def wait_report(page):
            page.wait_for_function("!!document.querySelector('#rf-report .rf-headline') || !!document.querySelector('#rf-report .rf-error')", timeout=20000)
            time.sleep(0.4)

        def full(page, path):
            """The settings body scrolls inside the dialog: one shot of the dialog per screenful (path-1.png, -2 …)."""
            height, client = page.evaluate("() => { const b = document.querySelector('#panel .settings-body'); return [b.scrollHeight, b.clientHeight]; }")
            step = max(200, int(client * 0.9))
            parts = min(6, max(1, -(-(height - client) // step) + 1))
            for i in range(parts):
                page.evaluate(f"document.querySelector('#panel .settings-body').scrollTop = {i * step}")
                time.sleep(0.25)
                page.locator("#panel").screenshot(path=str(path.with_name(f"{path.stem}-{i + 1}.png")))
            page.evaluate("document.querySelector('#panel .settings-body').scrollTop = 0")

        ctl({"clear": True, "mode": "ok", "prefs": {"reflect_enabled": True}})
        for lang, theme in (("ru", "dark"), ("en", "light"), ("ru", "light"), ("en", "dark")):
            ctx, page, errors = page_for(lang, theme)
            open_page(page, "reflect")
            page.select_option("#rf-period", "3m")
            wait_report(page)
            full(page, out / f"report-{lang}-{theme}.png")
            text = page.inner_text("#pane-reflect")
            if lang == "en" and any(w in text for w in ("Самый", "Ваше время", "Обсудить", "Вопрос")):
                problems.append(f"report-{lang}: Russian text in the English UI")
            if lang == "ru" and any(w in text for w in ("Most active", "Talk it through", "Peak hour")):
                problems.append(f"report-{lang}: English text in the Russian UI")
            check(page, f"report-{lang}-{theme}", errors)
            ctx.close()

        for i, design in enumerate(("aurora", "focus", "grid", "glass", "soft", "terminal", "brutal")):
            theme = "light" if i % 2 else "dark"
            ctx, page, errors = page_for("ru", theme, design)
            open_page(page, "reflect")
            page.select_option("#rf-period", "3m")
            wait_report(page)
            page.screenshot(path=str(out / f"design-{design}-{theme}.png"))
            page.evaluate("document.querySelector('#rf-skills-h')?.scrollIntoView()")
            time.sleep(0.2)
            page.screenshot(path=str(out / f"design-{design}-{theme}-skills.png"))
            check(page, f"design-{design}", errors)
            ctx.close()

        for lang in ("ru", "en"):
            ctx, page, errors = page_for(lang, "dark", width=640, height=900)
            open_page(page, "reflect")
            page.select_option("#rf-period", "3m")
            wait_report(page)
            full(page, out / f"narrow-{lang}.png")
            check(page, f"narrow-{lang}", errors)
            ctx.close()

        ctl({"clear": True, "mode": "slow"})
        ctx, page, errors = page_for("ru", "dark")
        open_page(page, "reflect")
        page.select_option("#rf-period", "6m")
        page.wait_for_function("document.querySelector('#rf-report')?.getAttribute('aria-busy') === 'true' && !!document.querySelector('#rf-report .rf-tile')", timeout=15000)
        time.sleep(0.6)
        page.screenshot(path=str(out / "loading-ru.png"))
        check(page, "loading", errors)
        ctx.close()
        ctl({"clear": True, "mode": "error"})
        for lang in ("ru", "en"):
            ctx, page, errors = page_for(lang, "light")
            open_page(page, "reflect")
            page.select_option("#rf-period", "12m")
            page.wait_for_function("!!document.querySelector('#rf-report .rf-error')", timeout=20000)
            time.sleep(0.3)
            page.screenshot(path=str(out / f"error-{lang}.png"))
            if not page.query_selector("#rf-report [data-rf-retry]"):
                problems.append(f"error-{lang}: no retry button")
            check(page, f"error-{lang}", errors)
            ctx.close()

        ctl({"clear": True, "mode": "ok"})
        folder = DATA / "memuai"
        saved = (folder / "chats.json").read_text(encoding="utf-8"), (folder / "quick_chats.json").read_text(encoding="utf-8")
        (folder / "chats.json").write_text("[]", encoding="utf-8")
        (folder / "quick_chats.json").write_text("[]", encoding="utf-8")
        for lang in ("ru", "en"):
            ctx, page, errors = page_for(lang, "dark")
            open_page(page, "reflect")
            page.wait_for_function("!!document.querySelector('#rf-report .rf-state')", timeout=15000)
            time.sleep(0.3)
            page.screenshot(path=str(out / f"empty-{lang}.png"))
            check(page, f"empty-{lang}", errors)
            ctx.close()
        now = int(time.time() * 1000)
        (folder / "chats.json").write_text(json.dumps([{"id": "c1", "title": "t", "updated": now, "messages": [
            {"role": "user", "content": "Привет", "createdAt": now - 3_600_000}]}]), encoding="utf-8")
        ctx, page, errors = page_for("ru", "light")
        open_page(page, "reflect")
        page.select_option("#rf-period", "this_month")
        page.wait_for_function("!!document.querySelector('#rf-report .rf-few')", timeout=15000)
        time.sleep(0.3)
        full(page, out / "few-ru.png")
        check(page, "few", errors)
        ctx.close()
        (folder / "chats.json").write_text(saved[0], encoding="utf-8")
        (folder / "quick_chats.json").write_text(saved[1], encoding="utf-8")

        ctx, page, errors = page_for("en", "dark")
        page.evaluate("savePreferences({reflect_enabled: false})")
        open_page(page, "reflect")
        time.sleep(0.5)
        page.screenshot(path=str(out / "off-en.png"))
        check(page, "off", errors)
        ctx.close()

        for lang, theme in (("ru", "dark"), ("en", "light")):
            ctx, page, errors = page_for(lang, theme)
            page.evaluate("savePreferences({focus_quiet_enabled: true, focus_break_enabled: true, focus_quiet_days: [0,1,2,3,4], focus_quiet_start: '22:00', focus_quiet_end: '07:00'})")
            open_page(page, "focus")
            time.sleep(0.4)
            full(page, out / f"focus-{lang}-{theme}.png")
            check(page, f"focus-{lang}", errors)
            ctx.close()
        ctx, page, errors = page_for("ru", "dark", width=600, height=900)
        page.evaluate("savePreferences({focus_quiet_enabled: true})")
        open_page(page, "focus")
        time.sleep(0.3)
        full(page, out / "focus-narrow-ru.png")
        check(page, "focus-narrow", errors)
        ctx.close()
        for lang in ("ru", "en"):
            ctx, page, errors = page_for(lang, "dark")

            page.evaluate("""() => { const d = new Date(); const pad = (n) => String(n).padStart(2, '0');
              const s = new Date(d.getTime() - 3600e3), e = new Date(d.getTime() + 3600e3);
              savePreferences({focus_quiet_enabled: true, focus_quiet_days: [0,1,2,3,4,5,6], focus_quiet_start: pad(s.getHours()) + ':00', focus_quiet_end: pad(e.getHours()) + ':' + pad(e.getMinutes())});
              sessionStorage.removeItem('memu_quiet_ack'); document.querySelector('.fx-card')?.remove(); }""")
            page.fill("#input", "Напиши план на завтра" if lang == "ru" else "Plan my tomorrow")
            page.evaluate("() => { send(); }")
            page.wait_for_selector(".fx-card[data-kind='quiet-send']", timeout=5000)
            time.sleep(0.4)
            page.screenshot(path=str(out / f"quiet-send-{lang}.png"))
            page.click(".fx-card [data-i='1']")
            if page.input_value("#input") == "":
                problems.append(f"quiet-send-{lang}: «later» lost the text")
            page.evaluate("MemuFocus._showBreak(62)")
            time.sleep(0.4)
            page.screenshot(path=str(out / f"break-{lang}.png"))
            check(page, f"reminders-{lang}", errors)
            ctx.close()

        quick_shots(browser, out, problems)
        browser.close()
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(DATA, ignore_errors=True)
    for item in problems:
        print("PROBLEM:", item)
    print(f"{len(problems)} problems, shots in {out}")
    return 1 if problems else 0

if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "/workspace/work/shots/r27/r27-reflect").resolve()))
