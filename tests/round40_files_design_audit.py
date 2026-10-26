# -*- coding: utf-8 -*-
"""MemuFiles design auditor (Round 40, r40-files-max) — modelled on round38_music_design_audit.py.

Runs the REAL MemuFiles backend over a generated folder tree (Cyrillic, long names, pictures, documents, a 400-file
folder) and the real page in headless Chromium, switches light/dark × UI scale 1/1.25/1.5 × window widths 1366/1024/800,
screenshots every screen and looks for design bugs automatically:

* ``offscreen``   — a visible interactive element sticks out of the window;
* ``clipped``     — text cut (scrollWidth > clientWidth) without an ellipsis / line clamp;
* ``covered``     — an interactive element covered by another one (hit test at its centre);
* ``contrast``    — text contrast below WCAG AA (4.5, 3 for large text) on a solid background;
* ``hscroll``     — horizontal scrolling of the page;
* ``scroll-tiny`` — a scrollbar for 1–6 px of overflow (an unnecessary scrollbar);
* ``misaligned``  — buttons of one toolbar / tabs of the tab strip not on one centre line (±1 px);
* ``junk-text``   — «null», «undefined», «NaN», an unreplaced «{n}» or a raw i18n key on screen;
* ``artifact-*``  — invisible-but-painting leftovers (scale(0) with a background, 1–4 % «ghosts»);
* ``focus-*``     — a text field without a focus ring, or with two (its own + its container's);
* ``console``     — page errors, console errors, failed requests (an aborted listing is expected).

Screens: Home, Downloads in details/list/icons/gallery, type filter, two panes, search results, context menu,
Properties (General/Details/Hashes), Settings, AI panel (hello + an «sort by meaning» plan from a fake model),
Recycle Bin, Quick Look; r40-files-2: pinned tabs (on every screen), the «Operations» flyout (running copy with the
speed graph, paused, conflict, queued, errors, finished), the status-bar indicator, the real name-conflict dialog
(an image and a text file), the drag-and-drop pill over a folder.

    python3 tests/round40_files_design_audit.py OUT_DIR [--quick] [--only=0,3]

Writes OUT_DIR/report.json and screenshots; exit code 1 when there are findings. No network.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CHROME = os.environ.get("MEMU_TEST_CHROMIUM") or "/workspace/work/chromium/chrome"

def make_tree(base: Path) -> Path:
    from PIL import Image, ImageDraw
    root = base / "root"
    dl = root / "Загрузки"
    dl.mkdir(parents=True)
    for i, (name, color, size) in enumerate([("фото_отпуск.jpg", (230, 90, 60), (900, 600)), ("screenshot 2026-10-01.png", (40, 140, 220), (1280, 720)),
                                             ("Очень длинное имя файла для проверки обрезки текста в плитках и таблице — финальная версия (2).png", (60, 180, 120), (500, 900))]):
        img = Image.new("RGB", size, color)
        ImageDraw.Draw(img).ellipse([size[0] // 4, size[1] // 4, size[0] * 3 // 4, size[1] * 3 // 4], fill=(250, 250, 250))
        img.save(dl / name)
    texts = {"Счёт №4521 ООО Ромашка.txt": "СЧЁТ НА ОПЛАТУ № 4521 от 12.09.2026\nПоставщик: ООО «Ромашка»\nИтого: 48 300,00 руб.\n",
             "invoice_acme_sept.txt": "INVOICE #A-998\nACME Corporation\nTotal due: $1,240.00\n",
             "resume_petrov.txt": "Петров Алексей Сергеевич\nРезюме. Python-разработчик\n",
             "glava1.txt": "Учебник по линейной алгебре. Глава 1.\n", "notes.md": "# Список покупок\n- молоко\n", "data.csv": "a,b\n1,2\n"}
    for name, text in texts.items():
        (dl / name).write_text(text, encoding="utf-8")
    with zipfile.ZipFile(dl / "архив проекта.zip", "w") as zf:
        zf.writestr("readme.txt", "hi")
    proj = root / "Проекты" / "memu-site" / "src"
    proj.mkdir(parents=True)
    (proj / "main.py").write_text("def hello():\n    return 'Привет'\n", encoding="utf-8")
    many = root / "Много файлов"
    many.mkdir()
    for i in range(400):
        (many / f"file_{i:04d}.{('txt', 'log', 'json', 'py')[i % 4]}").write_bytes(b"x" * (i * 37 % 5000))
    (root / "Пустая папка").mkdir()

    clash = root / "Конфликт"
    clash.mkdir()
    Image.new("RGB", (640, 480), (90, 60, 200)).save(clash / "фото_отпуск.jpg")
    (clash / "notes.md").write_text("# Старый список\n" * 40, encoding="utf-8")
    os.utime(clash / "фото_отпуск.jpg", (time.time() - 86400 * 30, time.time() - 86400 * 30))
    return root

def fake_llm(messages):
    """No network: the «sort by meaning» flow — a plan call, the classification, a closing sentence."""
    system = messages[0]["content"] if messages else ""
    if "раскладываешь" in system or "sort the user's files" in system:
        rows = [json.loads(line) for line in messages[-1]["content"].splitlines() if line.strip().startswith("{")]
        out = []
        for row in rows:
            n = row["name"].lower()
            folder = "Бухгалтерия/Счета" if ("счёт" in n or "invoice" in n) else "HR/Резюме" if "resume" in n else \
                "Книги" if "glava" in n else "Фото/2026" if n.endswith((".jpg", ".png")) else "Разное"
            out.append({"i": row["i"], "folder": folder, "name": "2026-09-" + row["name"].split(".")[0][:24], "why": "документ"})
        yield json.dumps(out, ensure_ascii=False)
        return
    if any("tool_result name=\"propose_plan\"" in m.get("content", "") for m in messages[-1:]):
        yield "Разложу документы по папкам: счета — в «Бухгалтерия», резюме — в «HR». Проверьте список и подтвердите."
        return
    yield '<tool>{"name": "propose_plan", "args": {"title": "Разложить документы по смыслу", "actions": [{"op": "classify_and_organize", "root": "."}]}}</tool>'

def fake_ops(root: str) -> list:
    now = time.time()
    dl, dst = root + "/Загрузки", root + "/Проекты"
    hist = [[i * 8, int(42e6 + 18e6 * ((i * 37) % 11) / 11 - (9e6 if 40 < i < 60 else 0))] for i in range(80)]
    base = {"rev": 1, "errors": [], "error_count": 0, "conflict": None, "undoable": False, "undone": False, "results": [], "message": "",
            "label": "", "speed_hist": [], "eta": None, "elapsed": 0, "cur_size": 0, "cur_done": 0, "skipped": 0, "retryable": False,
            "current": "", "speed": 0, "created": now, "started": now, "finished": None}
    def op(**kw):
        d = dict(base); d.update(kw); d["count"] = len(d["sources"]); d["src_dir"] = os.path.dirname(d["sources"][0]) if d["sources"] else ""; return d
    big = [dl + f"/Видео с отпуска {i:02d} — очень длинное имя файла для проверки переносов.mp4" for i in range(12)]
    return [
        op(id="fx-run", kind="copy", status="running", sources=big, dest=dst, unit="bytes", total_bytes=12 * 1024 ** 3, done_bytes=int(6.4 * 1024 ** 3),
           total_items=12, done_items=6, speed=47_300_000, speed_hist=hist, eta=128, elapsed=141, current=big[6], cur_size=1024 ** 3, cur_done=380 * 1024 ** 2, created=now - 150),
        op(id="fx-pause", kind="move", status="paused", sources=[dl + "/архив проекта.zip", dl + "/data.csv"], dest=dst, unit="bytes",
           total_bytes=820 * 1024 ** 2, done_bytes=310 * 1024 ** 2, total_items=2, done_items=0, elapsed=12, created=now - 120),
        op(id="fx-queue", kind="trash", status="queued", sources=[root + "/Много файлов"], dest="", unit="items", total_items=400, done_items=0, created=now - 20),
        op(id="fx-org", kind="organize", status="running", sources=[dl + "/glava1.txt"] * 9, dest=dl, unit="items", total_items=9, done_items=4, elapsed=3, created=now - 10),
        op(id="fx-err", kind="copy", status="done", sources=[dl + "/notes.md", dl + "/data.csv", dl + "/glava1.txt"], dest="/mnt/usb", unit="bytes",
           total_bytes=9000, done_bytes=9000, total_items=3, done_items=1, error_count=2, retryable=True, finished=now - 60, created=now - 70,
           errors=[{"path": dl + "/data.csv", "error": "no_space", "dest": "/mnt/usb", "retry": True},
                   {"path": dl + "/glava1.txt", "error": "PermissionError: [Errno 13] Отказано в доступе: '/mnt/usb/glava1.txt'", "dest": "/mnt/usb", "retry": True}]),
        op(id="fx-done", kind="copy", status="done", sources=[dl + "/фото_отпуск.jpg"], dest=dst, unit="bytes", total_bytes=48000, done_bytes=48000,
           total_items=1, done_items=1, undoable=True, finished=now - 200, created=now - 205, results=[dst + "/фото_отпуск.jpg"]),
    ]

AUDIT_JS = r"""
(opts) => {
  const out = [];
  const W = innerWidth, H = innerHeight;
  const vis = (el) => { const r = el.getBoundingClientRect(); if (r.width < 1 || r.height < 1) return null;
    const cs = getComputedStyle(el); if (cs.visibility === "hidden" || cs.display === "none" || Number(cs.opacity) === 0) return null;
    for (let p = el; p; p = p.parentElement) { const c = getComputedStyle(p); if (Number(c.opacity) < 0.05 || c.visibility === "hidden" || p.hidden) return null; }
    return r; };
  const name = (el) => (el.id ? "#" + el.id : el.tagName.toLowerCase()) + (el.className && typeof el.className === "string" ? "." + el.className.trim().split(/\s+/).slice(0, 2).join(".") : "")
    + (el.textContent ? " «" + el.textContent.trim().slice(0, 28) + "»" : "");
  const scrollParentX = (el) => { for (let p = el.parentElement; p; p = p.parentElement) { const c = getComputedStyle(p); if (/(auto|scroll)/.test(c.overflowX) && p.scrollWidth > p.clientWidth + 1) return p; } return null; };
  const clippedAway = (el, r) => { const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) { const c = getComputedStyle(p);
      if (/(hidden|clip|auto|scroll)/.test(c.overflow + c.overflowX + c.overflowY)) { const q = p.getBoundingClientRect(); if (cx < q.left || cx > q.right || cy < q.top + 1 || cy > q.bottom - 1) return true; } }
    return false; };
  const layer = opts.layer ? (document.querySelector(opts.layer) || document) : document;
  const skip = ".boot-splash, .toasts, .toast, .memu-tb-resize";
  const interactive = [...layer.querySelectorAll("button, a[href], input, select, textarea, [role=button], [role=tab], .tab")].filter((el) => !el.closest("[hidden]") && !el.closest(skip));
  // 1. offscreen
  for (const el of interactive) { const r = vis(el); if (!r || scrollParentX(el)) continue;
    if (r.right > W + 1 || r.left < -1) out.push({ kind: "offscreen", el: name(el), rect: [Math.round(r.left), Math.round(r.right)] }); }
  // 2. clipped text
  for (const el of layer.querySelectorAll("b, span, p, h1, h2, h3, button, label, div, code, small, strong")) {
    if (el.closest("[hidden]") || el.closest(skip)) continue;
    if (![...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())) continue;
    const r = vis(el); if (!r || clippedAway(el, r)) continue;
    const cs = getComputedStyle(el);
    if (cs.textOverflow === "ellipsis" || (cs.webkitLineClamp && cs.webkitLineClamp !== "none")) continue;
    if (el.scrollWidth > el.clientWidth + 1 && /(hidden|clip)/.test(cs.overflowX)) out.push({ kind: "clipped", el: name(el), sw: el.scrollWidth, cw: el.clientWidth });
    if (el.scrollHeight > el.clientHeight + 2 && /(hidden|clip)/.test(cs.overflowY) && cs.whiteSpace !== "nowrap") out.push({ kind: "clipped-v", el: name(el), sh: el.scrollHeight, ch: el.clientHeight });
  }
  // 3. covered interactive elements
  for (const el of interactive) {
    const r = vis(el); if (!r || clippedAway(el, r)) continue;
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    if (cx < 0 || cy < 0 || cx > W || cy > H) continue;
    const hit = document.elementFromPoint(cx, cy);
    if (!hit || el.contains(hit) || hit.contains(el) || hit.closest(skip)) continue;
    if (hit.closest("label") && hit.closest("label").contains(el)) continue;
    if (el.tagName === "INPUT" && /checkbox|radio|range|color/.test(el.type)) continue;
    if (getComputedStyle(el).pointerEvents === "none") continue;
    if (!opts.layer && hit.closest(".menu, .modal-back, .ops-fly")) continue;       // a layer above the window is audited on its own
    out.push({ kind: "covered", el: name(el), by: name(hit) });
  }
  // 4. contrast
  const parse = (c) => { const m = /rgba?\(([^)]+)\)/.exec(c || ""); if (!m) return null; const v = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number); return [v[0], v[1], v[2], v.length > 3 ? v[3] : 1]; };
  const blend = (top, bot) => { const a = top[3]; return [top[0] * a + bot[0] * (1 - a), top[1] * a + bot[1] * (1 - a), top[2] * a + bot[2] * (1 - a), 1]; };
  const lum = (c) => { const f = (v) => { v /= 255; return v <= .03928 ? v / 12.92 : Math.pow((v + .055) / 1.055, 2.4); }; return .2126 * f(c[0]) + .7152 * f(c[1]) + .0722 * f(c[2]); };
  const rootBg = parse(getComputedStyle(document.body).backgroundColor) || [0, 0, 0, 1];
  const bgOf = (el) => { const stack = [];
    for (let p = el; p; p = p.parentElement) { const cs = getComputedStyle(p);
      if (cs.backgroundImage && cs.backgroundImage !== "none") return null;
      if (/blur/.test(cs.backdropFilter || "")) return null;
      const c = parse(cs.backgroundColor); if (c && c[3] > 0) { stack.push(c); if (c[3] >= .99) break; } }
    let col = rootBg; for (let i = stack.length - 1; i >= 0; i--) col = blend(stack[i], col); return col; };
  const seen = new Set();
  for (const el of layer.querySelectorAll("b, span, p, h1, h2, h3, button, label, a, div, small, strong, code")) {
    if (el.closest("[hidden], [disabled], svg, .th, .pv-media, " + skip)) continue;
    if (![...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())) continue;
    const r = vis(el); if (!r || r.bottom < 0 || r.top > H || clippedAway(el, r)) continue;
    const cs = getComputedStyle(el);
    if (/^[\s·•|/—–>›-]+$/.test(el.textContent) || el.closest("[aria-hidden=true]")) continue;
    if (!opts.layer && el.closest(".menu, .modal-back, .ops-fly")) continue;
    const bg = bgOf(el); if (!bg) continue;
    let op = 1; for (let p = el; p; p = p.parentElement) op *= Number(getComputedStyle(p).opacity);
    const fg0 = parse(cs.color); if (!fg0) continue;
    const fg = blend([fg0[0], fg0[1], fg0[2], fg0[3] * op], bg);
    const L1 = lum(fg), L2 = lum(bg), ratio = (Math.max(L1, L2) + .05) / (Math.min(L1, L2) + .05);
    const size = parseFloat(cs.fontSize), bold = Number(cs.fontWeight) >= 600;
    const need = size >= 24 || (size >= 18.6 && bold) ? 3 : 4.5;
    if (ratio < need - .05) { const key = name(el); if (!seen.has(key)) { seen.add(key); out.push({ kind: "contrast", el: key, ratio: Math.round(ratio * 100) / 100, need }); } }
  }
  // 5. horizontal page scroll
  const se = document.scrollingElement; if (se.scrollWidth > W + 1) out.push({ kind: "hscroll", el: "document", sw: se.scrollWidth, w: W });
  // 6. unnecessary scrollbars (overflow of a few px)
  for (const el of layer.querySelectorAll("*")) {
    if (el.closest("[hidden]") || el.closest(skip)) continue;
    const cs = getComputedStyle(el); if (!/(auto|scroll)/.test(cs.overflowY + cs.overflowX)) continue;
    const r = vis(el); if (!r) continue;
    const dy = el.scrollHeight - el.clientHeight, dx = el.scrollWidth - el.clientWidth;
    if ((/(auto|scroll)/.test(cs.overflowY) && dy > 0 && dy <= 6) || (/(auto|scroll)/.test(cs.overflowX) && dx > 0 && dx <= 6 && !el.classList.contains("kindbar"))) out.push({ kind: "scroll-tiny", el: name(el), dx, dy });
  }
  // 7. one centre line per toolbar
  const lines = [[".cmdbar > .cmd:not([hidden])", "cmdbar"], [".navbar > button, .navbar > .icon-btn", "navbar"], [".tabbar .tab", "tabs"], [".kindbar .kchip", "kindbar"], [".statusbar .views button", "status"]];
  if (!opts.layer) for (const [sel, label] of lines) {
    const ys = [...document.querySelectorAll(sel)].map((el) => vis(el)).filter(Boolean).map((r) => r.top + r.height / 2);
    if (ys.length > 1 && Math.max(...ys) - Math.min(...ys) > 1.01) out.push({ kind: "misaligned", el: label, spread: Math.round((Math.max(...ys) - Math.min(...ys)) * 10) / 10 });
  }
  // 8. junk text: «null», «undefined», «NaN», «[object Object]», an unreplaced «{n}», a raw i18n key («plan.why.x»)
  const walker = document.createTreeWalker(layer === document ? document.body : layer, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const text = n.textContent.trim(); if (!text || !n.parentElement || n.parentElement.closest("[hidden], script, style, code, .pv-code, textarea") || !vis(n.parentElement)) continue;
    if (/^(null|undefined|NaN)$|\[object Object\]|\{[a-z_]+\}|^[a-z]+(\.[a-z0-9_]+){1,3}$/.test(text) && !/\.(txt|md|py|js|csv|json|log|png|jpg|zip|pdf|docx)$/i.test(text))
      out.push({ kind: "junk-text", el: name(n.parentElement), text: text.slice(0, 40) });
  }
  // 9. artifacts
  const mat = (tf) => { const m = /matrix\(([^)]+)\)/.exec(tf || ""); return m ? m[1].split(",").map(Number) : null; };
  for (const el of layer.querySelectorAll("*")) {
    if (el.closest("[hidden], svg") || el.closest(skip)) continue;
    const cs = getComputedStyle(el); if (cs.display === "none" || cs.visibility === "hidden") continue;
    const r = el.getBoundingClientRect(); if (r.bottom < 0 || r.top > H || r.width < 2 || r.height < 2) continue;
    const paints = cs.boxShadow !== "none" || ((parse(cs.backgroundColor) || [0, 0, 0, 0])[3] > 0) || cs.backgroundImage !== "none";
    if (!paints) continue;
    const m = mat(cs.transform);
    if (m && (Math.abs(m[0]) < 0.02 || Math.abs(m[3]) < 0.02) && !el.getAnimations().length) out.push({ kind: "artifact-degenerate", el: name(el) });
    let op = 1; for (let p = el; p; p = p.parentElement) op *= Number(getComputedStyle(p).opacity);
    if (op > 0.002 && op < 0.04) out.push({ kind: "artifact-ghost", el: name(el), opacity: Math.round(op * 1000) / 1000 });
  }
  return out;
}
"""

FOCUS_JS = r"""() => {
  const out = [], sel = 'input:not([type=checkbox]):not([type=radio]):not([type=range]):not([type=color]):not([type=file]):not([type=hidden]), textarea, select';
  const settle = () => document.getAnimations().forEach((a) => { try { a.finish(); } catch (e) {} });
  const snap = (e) => { const c = getComputedStyle(e); return { o: c.outlineStyle !== 'none' && parseFloat(c.outlineWidth) > 0 ? c.outlineWidth + c.outlineColor : '', bs: c.boxShadow, bc: c.borderTopColor, bw: parseFloat(c.borderTopWidth) }; };
  const top = document.querySelector('.modal-back:last-of-type') || document;
  for (const el of top.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect(); if (r.width < 2 || r.height < 2 || el.closest('[hidden]') || el.disabled) continue;
    const chain = [el]; for (let p = el.parentElement, k = 0; p && k < 3; p = p.parentElement, k++) chain.push(p);
    if (document.activeElement && document.activeElement !== document.body) document.activeElement.blur(); settle();
    const before = chain.map(snap); el.focus({ preventScroll: true }); settle(); const after = chain.map(snap);
    const own = after[0].o ? 'outline' : after[0].bs !== before[0].bs ? 'shadow' : after[0].bc !== before[0].bc ? 'border' : '';
    let anc = ''; for (let i = 1; i < chain.length; i++) if (after[i].bs !== before[i].bs || after[i].bc !== before[i].bc || after[i].o !== before[i].o) { anc = String(chain[i].className || chain[i].tagName); break; }
    const kind = !own && !anc ? 'focus-none' : own && anc ? 'focus-double' : '';
    if (kind) out.push({ kind, el: (el.id ? '#' + el.id : el.tagName.toLowerCase()) + '.' + String(el.className).split(' ')[0] + '[' + (el.type || '') + ']', own, anc });
    el.blur(); settle();
  }
  return out; }"""

def _server(base: Path, root: Path):
    os.environ["MEMUAI_DATA_DIR"] = str(base / "data")
    os.environ["MEMUFILES_TRASH_DIR"] = str(base / "trash")
    os.environ["NO_PROXY"] = "127.0.0.1,localhost"
    os.environ["HOME"] = str(root)
    from app.memufiles import server as srv, fsops
    from app.memunote import ai as note_ai
    fsops.open_default = lambda p: None
    note_ai.status = lambda *a, **k: {"online": True, "models": [{"id": "fake-fast", "label": "Fake model"}], "default_model": "fake-fast"}
    app = srv.FilesApp()
    app.llm_override = fake_llm
    httpd, _ = srv.start(app)
    return app, httpd

def run(out_dir: str, quick: bool = False, only=None) -> list:
    from playwright.sync_api import sync_playwright
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    base = Path(tempfile.mkdtemp(prefix="r40-files-audit-"))
    root = make_tree(base)
    app, httpd = _server(base, root)
    from app.memufiles import settings as mf_settings
    dl = str(root / "Загрузки")
    findings: list = []
    combos = [("dark", 1, 1366), ("light", 1, 1366), ("dark", 1.25, 1024), ("light", 1.5, 800),
              ("light", 1.25, 1024), ("dark", 1.5, 800), ("dark", 1, 800), ("light", 1.5, 1366)]
    if quick:
        combos = combos[:2]
    if only:
        combos = [combos[i] for i in only]
    try:
        with sync_playwright() as pw:
            for theme, scale, width in combos:
                mf_settings.save({"theme": theme, "restore_session": False})
                tag0 = f"{theme}-{int(scale * 100)}-{width}"
                browser = pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
                ctx = browser.new_context(viewport={"width": int(width / scale), "height": int(820 / scale)}, device_scale_factor=scale)
                page = ctx.new_page()
                errors: list = []
                page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
                page.on("console", lambda m: errors.append("console: " + m.text) if m.type == "error" else None)
                page.on("response", lambda r: errors.append(f"http {r.status} {r.url}") if r.status >= 400 else None)
                page.on("requestfailed", lambda r: errors.append(f"failed {r.url}") if "ERR_ABORTED" not in str(r.failure) else None)
                try:
                    page.goto(f"http://127.0.0.1:{app.port}/index.html#t={app.token}")
                    page.wait_for_selector(".tabbar .tab")
                    page.add_style_tag(content="*, *::before, *::after { transition: none !important; animation-duration: 0s !important; caret-color: transparent !important; }")

                    def screen(tag, layer=""):
                        page.wait_for_timeout(450)
                        page.screenshot(path=str(out / f"{tag0}-{tag}.png"))
                        for item in page.evaluate(AUDIT_JS, {"layer": layer}):
                            item["screen"] = f"{tag0}-{tag}"
                            findings.append(item)

                    def focus(tag):
                        for item in page.evaluate(FOCUS_JS):
                            item["screen"] = f"{tag0}-{tag}"
                            findings.append(item)

                    def nav(path, view="details"):
                        page.evaluate("([p, v]) => { const pane = MemuFiles.pane(); pane.view = v; return pane.navigate(p); }", [path, view])
                        page.wait_for_timeout(500)
                        page.evaluate("v => { const p = MemuFiles.pane(); p.view = v; p.layout(); MemuFiles.updateStatus(); }", view)

                    screen("home")
                    focus("home")

                    page.evaluate("p => { MemuFiles.newTab(p); return import('/js/feat40.js').then((m) => { m.pinTab(MemuFiles, MemuFiles.tabs.length - 1, true); MemuFiles.switchTab(MemuFiles.tabs.length - 1); }); }", str(root / "Проекты"))
                    nav(dl)
                    page.locator(".row", has_text="Договор").or_(page.locator(".row", has_text="Счёт")).first.click(position={"x": 60, "y": 12})
                    screen("details")
                    for view in ("list", "grid", "gallery"):
                        nav(dl, view)
                        page.wait_for_timeout(700)
                        screen(view)
                    nav(dl)
                    page.evaluate("() => { if (!document.querySelector('.kindbar:not([hidden])')) document.querySelector('[data-cmd=kinds]').click(); }")
                    page.click(".kchip[data-kind=image]")
                    screen("kinds")
                    page.click(".kchip[data-kind='']")
                    page.evaluate("() => document.querySelector('[data-cmd=kinds]').click()")
                    page.evaluate("() => MemuFiles.toggleSplit()")
                    page.wait_for_timeout(600)
                    screen("split")
                    page.evaluate("() => MemuFiles.toggleSplit()")
                    nav(str(root / "Много файлов"))
                    screen("many")
                    page.evaluate("() => { const i = MemuFiles.searchInput; i.focus(); i.value = 'file_00'; i.dispatchEvent(new Event('input', { bubbles: true })); MemuFiles.doSearch(); }")
                    page.wait_for_timeout(1500)
                    screen("search")
                    page.keyboard.press("Escape")
                    nav(dl)
                    page.locator(".row", has_text="фото_отпуск").first.click(button="right", position={"x": 60, "y": 12})
                    screen("menu", ".menu")
                    page.keyboard.press("Escape")
                    page.keyboard.press("Alt+Enter")
                    page.wait_for_selector(".ptabs")
                    screen("props-general", ".modal-back")
                    focus("props")
                    page.click(".ptab[data-t=details]")
                    page.wait_for_selector(".ptab-page.details .kv, .ptab-page.details .muted:not(:has(.spinner))")
                    screen("props-details", ".modal-back")
                    page.click(".ptab[data-t=hashes]")
                    page.click(".hash-top .btn")
                    page.wait_for_selector(".hash-row")
                    screen("props-hashes", ".modal-back")
                    page.keyboard.press("Escape")
                    page.evaluate("() => import('/js/dialogs.js').then((m) => m.settingsDialog(MemuFiles))")
                    page.wait_for_selector(".modal-back")
                    screen("settings", ".modal-back")
                    focus("settings")
                    page.keyboard.press("Escape")
                    page.evaluate("() => MemuFiles.setRight('ai')")
                    page.wait_for_selector(".ai-hello")
                    screen("ai-hello")
                    focus("ai")
                    page.fill(".ai-input textarea", "Разложи документы в Загрузках по смыслу")
                    page.keyboard.press("Enter")
                    page.wait_for_selector(".plan .plan-group", timeout=20000)
                    page.wait_for_timeout(400)
                    screen("ai-plan")
                    page.evaluate("() => MemuFiles.setRight(innerWidth > 760 ? 'preview' : null)")
                    page.evaluate("p => MemuFiles.pane().navigate(p)", "::trash")
                    page.wait_for_timeout(500)
                    screen("trash")
                    nav(dl)
                    page.locator(".row", has_text="фото_отпуск").first.click(position={"x": 60, "y": 12})
                    page.keyboard.press(" ")
                    page.wait_for_selector(".ql")
                    screen("quicklook", ".ql-back")
                    page.keyboard.press("Escape")

                    page.evaluate("ops => { for (const op of ops) MemuFiles.onOp(op); MemuFiles.opsUi.toggle(true); }", fake_ops(str(root)))
                    page.wait_for_selector(".ops-fly .opc")
                    page.evaluate("() => document.querySelector('.opc.fin .opc-errs [data-act=errs]') && document.querySelector('.opc.fin .opc-errs [data-act=errs]').click()")
                    screen("ops", ".ops-fly")
                    screen("ops-status")
                    page.keyboard.press("Escape")
                    page.evaluate("() => { for (const id of [...MemuFiles.ops.keys()]) if (id.startsWith('fx-')) MemuFiles.opsUi.remove(MemuFiles.ops.get(id)); }")
                    page.evaluate("([a, b, d]) => import('/js/core.js').then((c) => c.api('ops/submit', { kind: 'copy', sources: [a, b], dest: d })).then((r) => MemuFiles.trackOp(r.op))",
                                  [dl + "/фото_отпуск.jpg", dl + "/notes.md", str(root / "Конфликт")])
                    page.wait_for_selector(".cfx-cards", timeout=10000)
                    page.wait_for_timeout(300)
                    screen("conflict", ".modal-back")
                    focus("conflict")
                    page.evaluate("() => import('/js/core.js').then((c) => Promise.all([...MemuFiles.ops.values()].filter((o) => !['done', 'error', 'cancelled'].includes(o.status)).map((o) => c.api('ops/control', { action: 'cancel', id: o.id }))))")
                    page.wait_for_selector(".cfx-cards", state="detached", timeout=10000)
                    page.evaluate("() => MemuFiles.opsUi.toggle(false)")
                    nav(dl)
                    page.evaluate("""() => { const row = [...document.querySelectorAll('.row')].find((r) => r.textContent.includes('Проекты') || r.querySelector('.ic-folder')) || document.querySelector('.fv');
                        const dt = new DataTransfer(); dt.items.add(new File(['x'], 'отчёт.txt')); dt.items.add(new File(['y'], 'photo.png'));
                        const r = row.getBoundingClientRect();
                        for (let i = 0; i < 3; i++) row.dispatchEvent(new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer: dt, clientX: r.left + 80, clientY: r.top + 10 })); }""")
                    page.wait_for_timeout(120)
                    page.screenshot(path=str(out / f"{tag0}-dnd.png"))
                    for item in page.evaluate(AUDIT_JS, {"layer": ".drop-hint"}):
                        item["screen"] = f"{tag0}-dnd"
                        findings.append(item)
                    if not page.evaluate("() => !!document.querySelector('.drop-hint')"):
                        findings.append({"kind": "dnd-hint-missing", "screen": f"{tag0}-dnd", "el": ".drop-hint"})
                    page.wait_for_timeout(1300)
                    if page.evaluate("() => !!document.querySelector('.drop-hint')"):
                        findings.append({"kind": "dnd-hint-stuck", "screen": f"{tag0}-dnd", "el": ".drop-hint"})
                    findings.extend({"kind": "console", "screen": tag0, "el": e[:220]} for e in errors if "favicon" not in e)
                finally:
                    browser.close()
    finally:
        httpd.shutdown()
        shutil.rmtree(base, ignore_errors=True)
    name = "report.json" if not only else "report-" + "-".join(map(str, only)) + ".json"
    (out / name).write_text(json.dumps(findings, ensure_ascii=False, indent=1), encoding="utf-8")
    return findings

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    only = [int(x) for a in sys.argv if a.startswith("--only=") for x in a[7:].split(",")]
    t0 = time.time()
    result = run(args[0] if args else "/tmp/mf-audit", quick="--quick" in sys.argv, only=only or None)
    kinds: dict = {}
    for f in result:
        kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1
    print("findings:", len(result), kinds, f"{time.time() - t0:.0f}s")
    sys.exit(1 if result else 0)
