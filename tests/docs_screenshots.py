"""Screenshots for the documentation (app/pgstatic/docs-img/{ru,en}/*.webp).  Developer tool, not a test.

    PYTHONUTF8=1 python3 tests/docs_screenshots.py                 # both languages, every shot
    PYTHONUTF8=1 python3 tests/docs_screenshots.py --lang en --only search,memory
    PYTHONUTF8=1 python3 tests/docs_screenshots.py --list          # shot names, target files and pages
    PYTHONUTF8=1 python3 tests/docs_screenshots.py --serve         # keep the seeded servers running (for manual review)
    PYTHONUTF8=1 python3 tests/docs_screenshots.py --png /tmp/x    # also keep the uncropped PNGs there

Reproducible: real MemuAI Proxy + MemuAI servers (tests/ui_lang_scan.Servers) in a fresh temporary
MEMUAI_DATA_DIR, the offline model mock, invented but realistic seed data (chats, memory facts, panels,
planned tasks, a character, a fake Google Drive) — no keys, no personal data, no network.  Russian UI for
the ru set, English UI for the en set; one design for every picture (classic, dark — DESIGN/THEME below),
device scale factor 2, cropped to the relevant area, saved as WebP (Pillow) under ~250 KB each.

The Markdown side is written by hand: ``![alt](docs-img/<lang>/<file>.webp "caption")`` on its own line.
``--list`` prints which page each file belongs to.  After re-running, check the pictures (they are not
compared automatically) and run ``python3 -m pytest -q tests/round29_docs_ai_test.py``.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

import fastapi  # noqa: F401  (the mock's handler annotation is resolved from module globals)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
OUT = ROOT / "app" / "pgstatic" / "docs-img"
DESIGN, THEME = "classic", "dark"
VIEW = (1280, 760)
SCALE = 2
MAX_WIDTH = 1600
QUALITY = 82

DOC = "application/vnd.google-apps.document"
SHEET = "application/vnd.google-apps.spreadsheet"
SLIDES = "application/vnd.google-apps.presentation"
FOLDER = "application/vnd.google-apps.folder"

def _thumb(colors: tuple, seed: int) -> bytes:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (220, 160), colors[0])
    d = ImageDraw.Draw(img)
    d.rectangle((0, 100, 220, 160), fill=colors[1])
    d.ellipse((140 - seed * 7, 20, 190 - seed * 7, 70), fill=colors[2])
    d.polygon([(0, 110), (70, 55), (130, 110)], fill=colors[3])
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return buf.getvalue()

class FakeDrive:
    """Drive v3 (list / get / export / media / thumbnails) over httpx.MockTransport, realistic files."""

    def __init__(self, lang: str) -> None:
        en = lang == "en"
        self.mode = "ready"
        day = lambda n: time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - n * 86400))  # noqa: E731
        f = {}

        def add(fid, name, mime, parent="root", age=1.0, size=0, **extra):
            f[fid] = {"name": name, "mimeType": mime, "parents": [parent], "modifiedTime": day(age), "size": size, **extra}
        add("f_work", "Work" if en else "Работа", FOLDER, age=0.3)
        add("f_trip", "Trip to Kazan" if en else "Поездка в Казань", FOLDER, age=3)
        add("f_photo", "Photos" if en else "Фотографии", FOLDER, age=9)
        add("d_plan", "Q4 product plan" if en else "План продукта на IV квартал", DOC, age=0.1, starred=True)
        add("s_budget", "Family budget 2026" if en else "Семейный бюджет 2026", SHEET, age=0.6)
        add("p_pitch", "Pitch deck — MemuAI" if en else "Презентация для инвесторов", SLIDES, age=2)
        add("i_sunset", "sunset.jpg", "image/jpeg", age=4, size=2_412_000, thumb=1)
        add("i_lake", "lake.jpg", "image/jpeg", age=5, size=1_880_000, thumb=2)
        add("pdf_contract", "Contract_2026.pdf" if en else "Договор_аренды_2026.pdf", "application/pdf", age=12, size=486_000)
        add("t_notes", "meeting-notes.md", "text/markdown", age=1.5, size=6_200)
        add("v_big", "conference-talk.mp4", "video/mp4", age=20, size=480_000_000)
        add("d_report", "Annual report" if en else "Годовой отчёт", DOC, parent="f_work", age=0.2)
        add("s_kpi", "KPI dashboard" if en else "Показатели KPI", SHEET, parent="f_work", age=1)
        add("d_ticket", "Tickets and hotel" if en else "Билеты и гостиница", DOC, parent="f_trip", age=3)
        self.files = f
        self.palette = {1: ((250, 170, 90), (60, 40, 90), (255, 230, 120), (40, 30, 70)),
                        2: ((120, 180, 230), (40, 110, 150), (250, 250, 240), (30, 80, 60))}
        self.access = "ya29.fake-docs-screenshots"

    def handler(self, request):
        import httpx
        url = request.url
        if url.host.endswith("googleusercontent.com"):
            seed = int(url.path.strip("/").split("-")[-1] or 1)
            return httpx.Response(200, content=_thumb(self.palette[seed], seed), headers={"content-type": "image/jpeg"})
        path = url.path
        if path == "/drive/v3/about":
            return httpx.Response(200, json={"user": {"emailAddress": "alex@example.com", "displayName": "Alex"}})
        if path == "/drive/v3/files":
            return self._list(url)
        m = re.fullmatch(r"/drive/v3/files/([^/]+)(/export)?", path)
        if not m or m.group(1) not in self.files:
            return httpx.Response(404, json={"error": {"code": 404, "errors": [{"reason": "notFound"}]}})
        fid, row = m.group(1), self.files[m.group(1)]
        if m.group(2):
            return httpx.Response(200, content=f"# {row['name']}\n\nExported for the docs screenshot.\n".encode())
        if url.params.get("alt") == "media":
            if row.get("thumb"):
                return httpx.Response(200, content=_thumb(self.palette[row["thumb"]], row["thumb"]))
            return httpx.Response(200, content=b"x" * min(row.get("size") or 100, 4000))
        return httpx.Response(200, json=self._meta(fid, row))

    def _meta(self, fid, row):
        meta = {"id": fid, "name": row["name"], "mimeType": row["mimeType"], "modifiedTime": row["modifiedTime"],
                "viewedByMeTime": row["modifiedTime"], "owners": [{"displayName": "Alex", "me": True}],
                "capabilities": {"canDownload": True}, "parents": row["parents"], "starred": bool(row.get("starred"))}
        if row.get("size"):
            meta["size"] = str(row["size"])
        if row.get("thumb"):
            meta["thumbnailLink"] = f"https://lh3.googleusercontent.com/docs-shot-{row['thumb']}"
        return meta

    def _list(self, url):
        import httpx
        q = url.params.get("q", "")
        rows = []
        for fid, row in self.files.items():
            parent = re.search(r"'([^']+)' in parents", q)
            if parent and parent.group(1) not in row["parents"]:
                continue
            if "sharedWithMe = true" in q:
                continue
            if "starred = true" in q and not row.get("starred"):
                continue
            needle = re.search(r"name contains '((?:[^'\\]|\\.)*)'", q)
            if needle and needle.group(1).lower() not in row["name"].lower():
                continue
            if "mimeType != 'application/vnd.google-apps.folder'" in q and row["mimeType"] == FOLDER:
                continue
            if "mimeType contains 'image/'" in q and not row["mimeType"].startswith("image/") and row["mimeType"] != FOLDER:
                continue
            types = re.findall(r"mimeType = '([^']+)'", q)
            if types and row["mimeType"] not in types:
                continue
            rows.append(self._meta(fid, row))
        order = url.params.get("orderBy", "")
        if order.startswith("folder"):
            rows.sort(key=lambda r: (r["mimeType"] != FOLDER, r["name"].lower()))
        else:
            rows.sort(key=lambda r: r["modifiedTime"], reverse=True)
        return httpx.Response(200, json={"files": rows})

def install_fake_drive(drive: FakeDrive) -> None:
    """Every Picker the app creates talks to the fake Drive through «Integrations»-style tokens.
    ``drive.mode``: ready | need_consent | not_connected — switched by GET /api/test/gdrive-mode?mode=…"""
    import httpx
    from app import gdrive_picker as gp

    transport = httpx.MockTransport(drive.handler)
    original_init = gp.Picker.__init__

    def init(self, sync, *, transport_=None, integration=None, **kw):
        original_init(self, sync, transport=transport,
                      integration=lambda: (lambda force=False: drive.access) if drive.mode == "ready" else None)
    gp.Picker.__init__ = lambda self, sync, transport=None, integration=None: init(self, sync)
    original_status = gp.Picker.status

    def status(self):
        out = original_status(self)
        if drive.mode == "need_consent":
            out.update(state="need_consent", source="sync", has_client=True,
                       account={"email": "alex@example.com", "name": "Alex"})
        elif drive.mode == "not_connected":
            out.update(state="not_connected", source="", has_client=True)
        return out
    gp.Picker.status = status
    original_register = gp.register

    def register(app, **kw):
        picker = original_register(app, **kw)

        @app.get("/api/test/gdrive-mode")
        async def gdrive_mode(mode: str = "ready"):
            drive.mode = mode
            return {"mode": drive.mode}
        return picker
    gp.register = register

MODELS = ("gpt-5-mini", "claude-sonnet-4.5", "deepseek-v4-flash")
IMAGE_MODELS, VIDEO_MODELS = ("gpt-image-1", "flux-1.1-pro"), ("veo-3-fast",)

CANNED = [
    (r"пятниц", "**The meeting has been moved to Friday.**\n\nМягче и по-деловому: *We've rescheduled the meeting to Friday.*"),
    (r"friday", "**La reunión se ha pasado al viernes.**\n\nMore formal: *Hemos trasladado la reunión al viernes.*"),
]

def mock_models_app():
    """OpenAI-compatible mock with realistic model names; streams a short helpful answer."""
    from fastapi import FastAPI
    from fastapi.responses import StreamingResponse
    app = FastAPI()

    @app.get("/v1/models")
    async def models():
        return {"object": "list", "data": [{"id": m, "object": "model"} for m in MODELS]}

    @app.post("/v1/chat/completions")
    async def chat(request: "fastapi.Request"):
        body = await request.json()
        last = next((m.get("content") for m in reversed(body.get("messages") or []) if m.get("role") == "user"), "") or ""
        en = not re.search("[А-Яа-яЁё]", str(last))
        text = next((answer for pattern, answer in CANNED if re.search(pattern, str(last), re.I)), None) or (
            "Sure — here is a short answer. Ask me to go deeper if you need more detail." if en
            else "Конечно — вот короткий ответ. Если нужно подробнее, скажите.")
        if not body.get("stream"):
            return {"id": "c", "object": "chat.completion", "created": 1, "model": body.get("model"),
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 40, "completion_tokens": 20, "total_tokens": 60}}

        async def gen():
            for word in re.findall(r"\S+\s*", text):
                yield "data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": word}}]}, ensure_ascii=False) + "\n\n"
            yield "data: " + json.dumps({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}) + "\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")
    return app

def start_servers(lang: str, real_models: bool = False):
    import httpx
    from ui_lang_scan import Servers, free_port
    drive = FakeDrive(lang)
    install_fake_drive(drive)
    servers = Servers()
    mock = free_port()
    servers._run(mock_models_app(), mock)
    import tests.mock_custom  # noqa: F401  (Servers.start needs it importable)
    real_post = httpx.Client.post

    def post(client, url, *a, **kw):
        if url.endswith("/api/admin/accounts") and (kw.get("json") or {}).get("label") == "scan":
            kw["json"] = {"provider": "custom", "label": "openai-compatible", "fields": {
                "name": "My API", "base_url": f"http://127.0.0.1:{mock}/v1", "api_key": "sk-docs-screenshots",
                "models": ", ".join(MODELS), "image_models": ", ".join(IMAGE_MODELS), "video_models": ", ".join(VIDEO_MODELS)}}
        return real_post(client, url, *a, **kw)
    httpx.Client.post = post
    try:
        servers.start()
    finally:
        httpx.Client.post = real_post
    if real_models:
        with httpx.Client(timeout=30, trust_env=False) as client:
            for name, pre in (("Yandex", "YANDEX"),):
                if os.environ.get(pre + "_KEY"):
                    client.post(servers.proxy + "/api/admin/accounts", json={"provider": "custom", "label": name.lower(), "fields": {
                        "name": name, "base_url": os.environ[pre + "_BASE"], "api_key": os.environ[pre + "_KEY"],
                        "models": os.environ[pre + "_MODEL"]}})
    servers.drive = drive
    return servers

def T(lang: str, ru: str, en: str) -> str:
    return en if lang == "en" else ru

def seed(servers, lang: str) -> None:
    import httpx
    now = int(time.time() * 1000)
    t = lambda ru, en: T(lang, ru, en)   # noqa: E731
    model = "my-api-" + MODELS[0]
    table = t("| Город | Население, млн | Площадь, км² |\n|---|---:|---:|\n| Москва | 13,1 | 2561 |\n| Санкт-Петербург | 5,6 | 1439 |\n"
              "| Казань | 1,3 | 614 |\n| Новосибирск | 1,6 | 503 |",
              "| City | Population, M | Area, km² |\n|---|---:|---:|\n| Tokyo | 14.0 | 2194 |\n| London | 8.9 | 1572 |\n"
              "| Berlin | 3.7 | 891 |\n| Madrid | 3.3 | 604 |")
    chart = ('```memuai-chart\n{"type":"bar","title":"' + t("Выручка по кварталам", "Revenue by quarter") + '","labels":["Q1","Q2","Q3","Q4"],'
             '"series":[{"name":"2025","data":[12,15,11,19]},{"name":"2026","data":[14,18,16,23]}],'
             '"y":{"title":"' + t("Выручка", "Revenue, $M") + '","unit":"' + t("млн ₽", "$") + '"}}\n```')
    mermaid = ('```mermaid\nflowchart LR\n  A["' + t("Заявка", "Request") + '"] --> B{"' + t("Данные полные?", "Complete?") + '"}\n'
               '  B -->|' + t("да", "yes") + '| C["' + t("В работу", "Process") + '"]\n  B -->|' + t("нет", "no") + '| D["'
               + t("Уточнить", "Ask again") + '"]\n```')

    def chat(cid, title, hours_ago, turns):
        msgs = []
        for i, (role, text) in enumerate(turns):
            row = {"role": role, "content": text, "ts": (now - hours_ago * 3600_000) / 1000 + i * 20}
            if role == "assistant":
                row["model"] = model
            msgs.append(row)
        return {"id": cid, "title": title, "created": now - hours_ago * 3600_000 - 60_000, "updated": now - hours_ago * 3600_000,
                "mode": "chat", "messages": msgs}
    chats = [
        chat("c_trip", t("Маршрут на выходные в Казани", "Weekend itinerary for Lisbon"), 0.2, [
            ("user", t("Составь маршрут на два дня в Казани: музеи, еда, прогулки", "Plan two days in Lisbon: museums, food, walks")),
            ("assistant", t("**День 1.** Кремль и мечеть Кул-Шариф утром, обед на улице Баумана, вечером — набережная Казанки.\n\n"
                            "**День 2.** Старо-Татарская слобода, музей чак-чака, закат на Чаше.\n\n"
                            "Возьмите удобную обувь: пешком выйдет около 12 км в день.",
                            "**Day 1.** Belém Tower and the Jerónimos Monastery in the morning, lunch in Alfama, sunset at Miradouro da Graça.\n\n"
                            "**Day 2.** Tram 28, the LX Factory, dinner in Bairro Alto.\n\n"
                            "Bring comfortable shoes: it is about 12 km on foot each day."))]),
        chat("c_tbl", t("Города: таблица и график", "Cities: table and chart"), 3, [
            ("user", t("Сравни города в таблице и нарисуй график выручки", "Compare the cities in a table and chart the revenue")),
            ("assistant", t("Вот сравнение:\n\n", "Here is the comparison:\n\n") + table + "\n\n" + chart),
            ("user", t("И схему обработки заявки", "And a flowchart of the request flow")),
            ("assistant", mermaid)]),
        chat("c_run", t("План тренировок к полумарафону", "Half-marathon training plan"), 26, [
            ("user", t("Составь план тренировок к полумарафону на 8 недель", "Make an 8-week half-marathon plan")),
            ("assistant", t("Неделя 1: три лёгкие пробежки по 5 км и одна длинная — 8 км.", "Week 1: three easy 5 km runs and one long 8 km run."))]),
        chat("c_mail", t("Письмо арендодателю", "Email to the landlord"), 50, [
            ("user", t("Напиши вежливое письмо арендодателю о протекающем кране", "Write a polite email to my landlord about a leaking tap")),
            ("assistant", t("Здравствуйте, Ирина Петровна! На кухне подтекает кран…", "Hello Mr. Evans, the kitchen tap has started leaking…"))]),
        chat("c_py", t("Скрипт для переименования фото", "Script to rename photos"), 120, [
            ("user", t("Напиши скрипт на Python, который переименует фото по дате съёмки", "Write a Python script that renames photos by the date taken")),
            ("assistant", "```python\nfrom pathlib import Path\nfrom PIL import Image\n\nfor p in Path('.').glob('*.jpg'):\n    date = Image.open(p).getexif().get(306)\n    if date:\n        p.rename(date.replace(':', '-') + '.jpg')\n```")]),
        chat("c_book", t("Что почитать про историю науки", "Books on the history of science"), 300, [
            ("user", t("Посоветуй три книги по истории науки", "Recommend three books on the history of science")),
            ("assistant", t("1. «Краткая история почти всего на свете» — Билл Брайсон…", "1. *A Short History of Nearly Everything* — Bill Bryson…"))]),
    ]
    facts = [
        (t("Живёт в Санкт-Петербурге, часовой пояс Москвы", "Lives in Lisbon, Western European Time"), "personal", True),
        (t("Предпочитает короткие ответы со списками", "Prefers short answers with bullet lists"), "preference", True),
        (t("Готовится к полумарафону в апреле", "Training for a half marathon in April"), "goal", False),
        (t("Пишет на Python и TypeScript, редактор — VS Code", "Codes in Python and TypeScript, uses VS Code"), "work", False),
        (t("Не ест орехи (аллергия)", "Does not eat nuts (allergy)"), "health", False),
        (t("Кошку зовут Мята", "Has a cat called Mint"), "personal", False),
    ]
    with httpx.Client(timeout=60, trust_env=False) as client:
        pg = servers.pg
        client.post(pg + "/api/pg/preferences", json={"profile_onboarded": True, "profile_name": "Alex", "language": lang,
                                                      "updates_enabled": False, "default_model": model})
        client.post(pg + "/api/pg/chats", json={"chats": chats})
        for text, cat, pinned in facts:
            client.post(pg + "/api/memory/items", json={"text": text, "category": cat, "pinned": pinned})
        client.post(pg + "/api/pg/modules/configure", json={"id": "characters", "enabled": True})

        for panel in client.get(pg + "/api/pg/panels").json().get("panels", []):
            client.delete(pg + f"/api/pg/panels/{panel['id']}")
        made = {}
        for body in ({"template": "todo", "params": {}},
                     {"template": "countdown", "params": {"title": t("До полумарафона", "Half marathon"),
                                                          "target": time.strftime("%Y-%m-%dT09:00", time.localtime(time.time() + 47 * 86400))}},
                     {"template": "notes", "params": {}},
                     {"template": "clock", "params": {}}):
            try:
                made[body["template"]] = client.post(pg + "/api/pg/panels", json={**body, "wait": 5}).json().get("panel") or {}
            except Exception:  # noqa: BLE001
                pass
        todo = (made.get("todo") or {}).get("options", {}).get("lists") or []
        for title in ((t("Забрать посылку до 19:00", "Pick up the parcel before 7 pm"), 1),
                      (t("Позвонить в автосервис", "Call the garage"), 2),
                      (t("Купить подарок маме", "Buy a present for Mum"), 3)):
            if todo:
                client.post(pg + f"/api/pg/panels-todo/{todo[0]}/items", json={"title": title[0], "priority": title[1], "lang": lang})
        if made.get("notes", {}).get("id"):
            client.put(pg + f"/api/pg/panels-note/{made['notes']['id']}", json={"text": t(
                "Идеи для отпуска:\n— Калининград и Куршская коса\n— Алтай в сентябре\n— Байкал зимой", 
                "Holiday ideas:\n— Porto and the Douro valley\n— Madeira in spring\n— Iceland in winter")})
        tasks = [
            {"id": "t_news", "name": t("Утренняя сводка новостей", "Morning news digest"), "enabled": True, "kind": "chat", "model": model,
             "prompt": t("Кратко: главные новости технологий за ночь", "Briefly: the top tech news overnight"),
             "rule": {"when": [{"type": "daily", "times": ["08:30"], "days": [1, 2, 3, 4, 5]}]}},
            {"id": "t_water", "name": t("Попить воды", "Drink some water"), "enabled": True, "kind": "reminder",
             "prompt": t("Пора выпить стакан воды", "Time for a glass of water"),
             "rule": {"when": [{"type": "interval", "every": 2, "unit": "hours", "from": "10:00", "to": "19:00"}],
                      "only_if": [{"type": "idle", "minutes": 10, "value": False}]}},
            {"id": "t_game", "name": t("Напомнить о перерыве в игре", "Break reminder while gaming"), "enabled": False, "kind": "reminder",
             "prompt": t("Час в игре — сделайте перерыв", "An hour of gaming — take a break"),
             "rule": {"when": [{"type": "event", "event": "process_start", "process": "dota2.exe", "delay": 60}]}},
            {"id": "t_report", "name": t("Отчёт по расходам за месяц", "Monthly spending report"), "enabled": True, "kind": "chat", "model": model,
             "prompt": t("Собери отчёт по расходам из таблицы бюджета", "Build a spending report from the budget sheet"),
             "rule": {"when": [{"type": "monthly", "day": -1, "time": "20:00"}]}},
        ]
        client.post(pg + "/api/pg/schedules", json={"tasks": tasks})

FONTS_JS = r"""self.origin !== 'null' && document.addEventListener('DOMContentLoaded', () => {
  const f = '/designs/fonts/', face = (family, file, range, extra = '') => `@font-face { font-family: "${family}"; src: url("${f}${file}") format("woff2");
    font-weight: 100 900; font-display: block; ${range ? 'unicode-range: ' + range + ';' : ''} ${extra} }`;
  const LAT = 'U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+2000-206F, U+2074, U+20AC, U+2122, U+2190-21FF, U+2212, U+2215',
        CYR = 'U+0400-045F, U+0490-0491, U+04B0-04B1, U+2116';
  const css = ['Segoe UI Variable Text', 'Segoe UI'].map((n) => face(n, 'inter/inter-latin-wght.woff2', LAT) + face(n, 'inter/inter-cyrillic-wght.woff2', CYR)).join('')
    + ['Charter', 'Palatino Linotype'].map((n) => face(n, 'source-serif-4/SourceSerif4-Variable.woff2', '')
       + face(n, 'source-serif-4/SourceSerif4-Italic-Variable.woff2', '', 'font-style: italic;')).join('')
    + ['Cascadia Code', 'Consolas'].map((n) => face(n, 'jetbrains-mono/jetbrains-mono-latin-wght.woff2', LAT) + face(n, 'jetbrains-mono/jetbrains-mono-cyrillic-wght.woff2', CYR)).join('');
  const style = document.createElement('style'); style.dataset.docsShots = 'fonts'; style.textContent = css; document.head.appendChild(style);
});"""

DESIGN_JS = """([design, theme]) => { try { setTheme(theme, false); } catch (e) {}
  if (design === 'classic' || design === 'default') delete document.documentElement.dataset.design; else document.documentElement.dataset.design = design; }"""

def launch(play):
    return play.chromium.launch(executable_path=os.environ.get("MEMU_TEST_CHROMIUM", "/usr/bin/chromium"), args=["--no-sandbox"])

def open_app(play, servers, lang: str, design: str = DESIGN, theme: str = THEME, view=VIEW, scale: int = SCALE,
             browser=None, path: str = "/"):
    browser = browser or launch(play)
    ctx = browser.new_context(viewport={"width": view[0], "height": view[1]}, device_scale_factor=scale, bypass_csp=True,
                              locale="en-US" if lang == "en" else "ru-RU", timezone_id="Europe/Moscow")
    ctx.add_init_script(f"try {{ localStorage.setItem('memu_lang', '{lang}'); }} catch (_) {{}}")
    ctx.add_init_script(FONTS_JS)
    page = ctx.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)[:300]))
    page.on("console", lambda m: m.type == "error" and page.errors.append("console: " + m.text[:200]))
    page.goto(servers.pg + path)
    if path != "/":
        page.wait_for_timeout(1200)
        return browser, page
    page.wait_for_function("document.body.classList.contains('boot-ready')", timeout=90000)
    page.wait_for_timeout(800)
    page.evaluate(DESIGN_JS, [design, theme])
    page.wait_for_timeout(600)
    return browser, page

def _sleep(ms: int) -> str:
    return f"await new Promise((r) => setTimeout(r, {ms}));"

OPEN_PLUS = "openChat('c_trip'); " + _sleep(900) + " document.querySelector('#btn-plus').click(); " + _sleep(700)
OPEN_DRIVE = OPEN_PLUS + (" [...document.querySelectorAll('.plus-menu button, .plus-menu [role=menuitem], .plus-menu [data-tool]')]"
                          ".find((n) => /Google/.test(n.textContent)).click(); " + _sleep(2200))
CHARACTER = {"spec": "chara_card_v2", "spec_version": "2.0", "data": {}}

def character(lang: str) -> dict:
    t = lambda ru, en: T(lang, ru, en)   # noqa: E731
    card = json.loads(json.dumps(CHARACTER))
    card["data"] = {
        "name": t("Профессор Лис", "Professor Fox"),
        "description": t("{{char}} — добродушный лис-историк, читает лекции в Оксфорде и обожает чай с бергамотом.",
                         "{{char}} is a kind-hearted fox historian who lectures at Oxford and loves Earl Grey tea."),
        "personality": t("любознательный, терпеливый, шутит к месту", "curious, patient, witty"),
        "scenario": t("Вечер в кабинете профессора, за окном дождь.", "An evening in the professor's study; rain outside."),
        "first_mes": t("*поправляет очки* Добрый вечер, {{user}}! О какой эпохе поговорим сегодня?",
                       "*adjusts his glasses* Good evening, {{user}}! Which era shall we talk about tonight?"),
        "tags": [t("история", "history"), t("уютное", "cozy")], "creator": "MemuAI docs"}
    return card

def shots(lang: str) -> list[dict]:
    """name → docs page, steps (async JS bodies or ('key'|'type'|'click'|'hover'|'mouse'|'frame', …)), crop, viewport."""
    t = lambda ru, en: T(lang, ru, en)   # noqa: E731
    away = ("mouse", (1270, 750))
    return [
        {"name": "home", "page": "welcome", "steps": ["newChat();", away], "wait": 1500},
        {"name": "interface", "page": "interface", "steps": ["openChat('c_trip');", away], "wait": 1500},
        {"name": "composer-plus", "page": "composer", "view": (1280, 980),
         "steps": [OPEN_PLUS + " document.querySelector('#messages').style.visibility = 'hidden';"],
         "crop": [".plus-menu", ".composer", ".composer-hint"], "pad": 18},
        {"name": "quick-ask", "page": "quick-ask", "quick": t("Как по-английски «встреча переносится на пятницу»?",
                                                               "How do I say “the meeting is moved to Friday” in Spanish?")},
        {"name": "panels", "page": "panels", "steps": ["newChat();", away], "crop": ["#panels-home"], "pad": 14,
         "view": (1280, 900), "wait": 2500},
        {"name": "search", "page": "search", "steps": ["MemuSearch.open(" + json.dumps(t("план", "plan")) + ");", away],
         "crop": [".msx-panel"], "pad": 16, "wait": 1500},
        {"name": "memory", "page": "memory", "steps": ["MemuMemory.open({tab: 'facts'});", away], "crop": ["#panel"], "pad": 16, "wait": 1600},
        {"name": "tables", "page": "tables-diagrams",
         "steps": ["openChat('c_tbl'); " + _sleep(1200) + " document.querySelector('#messages .mt-host')?.scrollIntoView({block: 'center'});",
                   ("hover", "#messages .mt-host table")],
         "crop": ["#messages .mt-host", "#messages .mt-host .mt-bar"], "pad": 14, "wait": 900},
        {"name": "chart", "page": "tables-diagrams",
         "steps": ["openChat('c_tbl'); " + _sleep(1500) + " document.querySelector('#messages .memu-diagram')?.scrollIntoView({block: 'center'});"
                   " document.querySelectorAll('#messages .mt-host').forEach((n) => { n.style.visibility = 'hidden'; });",
                   ("hover", ("#messages .memu-diagram", (24, 24)))],
         "crop": ["#messages .memu-diagram[data-memu-diagram='chart']", "#messages .memu-diagram[data-memu-diagram='chart'] .md-bar"], "pad": 6, "wait": 1800},
        {"name": "chat-export", "page": "chat-export", "steps": ["openChat('c_tbl'); " + _sleep(900) + " MemuExport.open();", away],
         "crop": ["#panel"], "pad": 16, "wait": 3500},
        {"name": "shortcuts", "page": "shortcuts", "steps": ["newChat();", ("key", "Control+Slash"), _sleep(400) + " document.activeElement?.blur();", away], "crop": ["#panel"], "pad": 0, "wait": 1200},
        {"name": "drive-picker", "page": "sync",
         "steps": [OPEN_DRIVE, ("click", "#panel [data-gd-row='4']"), ("click", "#panel [data-gd-row='6']"),
                   "document.querySelector('#panel [data-gd-list]').scrollTop = 400;", ("click", "#panel [data-gd-row='8']"), away],
         "crop": ["#panel"], "pad": 16, "wait": 900},
        {"name": "sync", "page": "sync", "steps": ["showSettings(); openSettingsPage('sync');", away], "crop": ["#panel"], "pad": 0, "wait": 1500},
        {"name": "schedules", "page": "schedules",
         "steps": ["MemuPlan.open(); " + _sleep(1200) + " document.querySelector('#panel [data-act=help-hide]')?.click();", away],
         "crop": ["#panel"], "pad": 16, "wait": 1500, "view": (1280, 900)},
        {"name": "memunote", "page": "memunote",
         "steps": ["showSettings(); openSettingsPage('memunote'); " + _sleep(900)
                   + " const st = document.createElement('style'); st.textContent = '.mn-hero-foot { display: none !important; }'; document.head.appendChild(st);",
                   away], "crop": ["#panel"], "pad": 0, "wait": 900},
        {"name": "media", "page": "media",
         "steps": ["showSettings(); openSettingsPage('models'); " + _sleep(900)
                   + " const h = [...document.querySelectorAll('#panel h3, #panel h4, #panel .sx-group-title')].find((n) => /" + t("Изображения, видео", "Images, video") + "/.test(n.textContent));"
                   " if (h) { let el = h.parentElement; while (el && el.scrollHeight <= el.clientHeight + 1) el = el.parentElement;"
                   " if (el) { el.style.scrollBehavior = 'auto'; el.scrollTop += h.getBoundingClientRect().top - el.getBoundingClientRect().top - 20; } }", away], "crop": ["#panel"], "pad": 0, "wait": 900},
        {"name": "characters", "page": "characters", "view": (1120, 680),
         "steps": ["MemuModules.open('characters');", ("wait", 3500),
                   ("frame", ("characters", "M.importFiles([new File([" + json.dumps(json.dumps(character(lang))) + "], 'fox.json', {type: 'application/json'})])"
                              "; " + _sleep(1500) + " [...document.querySelectorAll('.mu-btn.primary')].pop().click(); " + _sleep(1500)
                              + " await M.openCharacter(M.S.index[0].id)")), away],
         "crop": ["#module-window"], "pad": 0, "wait": 2200},
    ]

def _crop_rect(page, selectors, pad, view):
    rect = page.evaluate("""(sels) => { let box = null;
      for (const sel of sels) { for (const n of document.querySelectorAll(sel)) { const r = n.getBoundingClientRect();
        if (!r.width || !r.height) continue;
        box = box ? {l: Math.min(box.l, r.left), t: Math.min(box.t, r.top), r: Math.max(box.r, r.right), b: Math.max(box.b, r.bottom)}
                  : {l: r.left, t: r.top, r: r.right, b: r.bottom}; } }
      return box; }""", selectors)
    if not rect:
        raise RuntimeError("nothing to crop: " + ", ".join(selectors))
    x0, y0 = max(0, rect["l"] - pad), max(0, rect["t"] - pad)
    x1, y1 = min(view[0], rect["r"] + pad), min(view[1], rect["b"] + pad)
    return {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}

def save_webp(png: bytes, target: Path) -> int:
    from PIL import Image
    img = Image.open(io.BytesIO(png)).convert("RGB")
    if img.width > MAX_WIDTH:
        img = img.resize((MAX_WIDTH, round(img.height * MAX_WIDTH / img.width)), Image.LANCZOS)
    target.parent.mkdir(parents=True, exist_ok=True)
    img.save(target, "WEBP", quality=QUALITY, method=6)
    return target.stat().st_size

def run_step(page, step) -> None:
    if isinstance(step, str):
        page.evaluate("async () => { " + step + " }")
        return
    kind, arg = step
    if kind == "key":
        page.keyboard.press(arg)
    elif kind == "type":
        page.keyboard.type(arg, delay=15)
    elif kind == "click":
        page.click(arg, timeout=6000)
    elif kind == "hover":
        if isinstance(arg, tuple):
            page.hover(arg[0], position={"x": arg[1][0], "y": arg[1][1]}, timeout=6000)
        else:
            page.hover(arg, timeout=6000)
    elif kind == "mouse":
        page.mouse.move(*arg)
    elif kind == "wait":
        page.wait_for_timeout(arg)
    elif kind == "frame":
        mid, code = arg
        for _ in range(40):
            frame = next((f for f in page.frames if mid in f.url), None)
            if frame is not None:
                break
            page.wait_for_timeout(250)
        else:
            raise RuntimeError("module frame not found")
        frame.evaluate("async () => { const M = window.MemuCharacters; " + code + "; }")
    page.wait_for_timeout(350)

def quick_ask_shot(play, browser, servers, lang: str, question: str) -> bytes:
    _, page = open_app(play, servers, lang, view=(720, 640), browser=browser, path="/quick/?native=1")
    page.evaluate("QuickAsk.onShow({fresh: true, maxHeight: 640, rounded: true, corner: 'dwm', radius: 8})")
    page.wait_for_timeout(600)
    page.fill("#qa-input", question)
    page.click("#qa-send")
    page.wait_for_function("() => !document.querySelector('#qa-inner .qa-typing, #qa-inner [data-streaming]')", timeout=20000)
    page.wait_for_timeout(2500)
    height = page.evaluate("QuickAsk.render()")
    page.set_viewport_size({"width": 720, "height": max(60, int(height))})
    page.mouse.move(700, 5)
    page.wait_for_timeout(500)
    png = page.screenshot()
    page.context.close()
    return png

def run_lang(lang: str, only: set, png_dir: str) -> int:
    from playwright.sync_api import sync_playwright
    servers = start_servers(lang)
    seed(servers, lang)
    problems = 0
    total = 0
    with sync_playwright() as play:
        browser = launch(play)
        for shot in shots(lang):
            if only and shot["name"] not in only:
                continue
            target = OUT / lang / f"{shot['name']}.webp"
            try:
                if "quick" in shot:
                    png = quick_ask_shot(play, browser, servers, lang, shot["quick"])
                    errors = []
                else:
                    view = shot.get("view", VIEW)
                    _, page = open_app(play, servers, lang, view=view, browser=browser)
                    for step in shot["steps"]:
                        run_step(page, step)
                    page.wait_for_timeout(shot.get("wait", 800))

                    page.evaluate("() => { const a = document.activeElement; if (a && a.tagName === 'BUTTON') a.blur(); }")
                    clip = _crop_rect(page, shot["crop"], shot.get("pad", 12), view) if shot.get("crop") else None
                    png = page.screenshot(clip=clip) if clip else page.screenshot()
                    errors = [e for e in page.errors if "favicon" not in e]
                    page.context.close()
                if png_dir:
                    Path(png_dir).mkdir(parents=True, exist_ok=True)
                    (Path(png_dir) / f"{lang}-{shot['name']}.png").write_bytes(png)
                size = save_webp(png, target)
                total += size
                print(f"{lang}/{shot['name']}.webp {size // 1024} KB" + (f"  page errors: {errors[:3]}" if errors else ""), flush=True)
                problems += bool(errors)
            except Exception as exc:  # noqa: BLE001
                problems += 1
                print(f"{lang}/{shot['name']}: FAILED {str(exc).splitlines()[0][:300]}", flush=True)
        browser.close()
    servers.stop()
    print(f"{lang}: {total // 1024} KB written", flush=True)
    return problems

def run_shots(langs: list, only: set, png_dir: str) -> int:
    if len(langs) == 1:
        return 1 if run_lang(langs[0], only, png_dir) else 0
    import subprocess
    code = 0
    for lang in langs:
        args = [sys.executable, __file__, "--lang", lang] + (["--only", ",".join(sorted(only))] if only else []) + (["--png", png_dir] if png_dir else [])
        code |= subprocess.call(args)
    return code

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lang", default="ru,en")
    ap.add_argument("--only", default="")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--real-models", action="store_true", help="--serve: also add the Yandex endpoint from env")
    ap.add_argument("--png", default="")
    a = ap.parse_args()

    os.environ["TZ"] = "Europe/Moscow"
    if hasattr(time, "tzset"):
        time.tzset()
    if a.list:
        for shot in shots("en"):
            print(f"{shot['name']:<16} {shot['page']:<16} docs-img/<lang>/{shot['name']}.webp")
        return 0
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memu-docs-shots-")
    for key in ("NO_PROXY", "no_proxy"):
        os.environ[key] = "127.0.0.1,localhost"
    langs = [x for x in a.lang.split(",") if x in ("ru", "en")]
    if a.serve:
        servers = start_servers(langs[0], real_models=a.real_models)
        seed(servers, langs[0])
        info = {"pg": servers.pg, "proxy": servers.proxy, "data": os.environ["MEMUAI_DATA_DIR"], "pid": os.getpid()}
        print(json.dumps(info), flush=True)
        if os.environ.get("DOCS_SHOTS_INFO"):
            Path(os.environ["DOCS_SHOTS_INFO"]).write_text(json.dumps(info))
        while True:
            time.sleep(3600)
    return run_shots(langs, set(filter(None, a.only.split(","))), a.png)

if __name__ == "__main__":
    sys.exit(main())
