"""Browser checks for round 30 (r30-misc): /image /video chip in a message box, zoom of schemes.

Runs headless Chromium over the real static files (served from disk by a Playwright route, no server):
    python tests/round30_misc_browser.py [--shots DIR]
Prints one JSON line: {"cmd": {...}, "zoom": {...}} — read by round30_misc_test.py.
"""
from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "pgstatic"
ORIGIN = "http://memu.test"

COMPOSER = """<!doctype html><html lang="ru" data-theme="dark"><head><meta charset="utf-8">
<link rel="stylesheet" href="/style.css"><link rel="stylesheet" href="/cmd-highlight.css">
<style>body{padding:40px;background:var(--bg)} .composer{max-width:700px}</style></head><body>
<div class="composer"><div class="attach-row"></div><div class="composer-main"><textarea id="input" rows="1"></textarea></div></div>
<script>
  var PG_LANG = "ru";
  const field = document.getElementById("input");
  window.heights = [];
  // the page's own autosize, like app.js: it must see the indent already applied
  field.addEventListener("input", () => { field.style.height = "auto"; field.style.height = Math.min(200, field.scrollHeight) + "px"; heights.push(field.style.height); });
</script>
<script src="/cmd-highlight.js"></script></body></html>"""

SCHEME = """flowchart TD
    A[Вечер выходного дня] --> B{Есть энергия?}
    B -->|Да| C{Хочется компании?}
    B -->|Нет| D{Хочется думать?}
    C -->|Да| E{Бюджет есть?}
    C -->|Нет| F{Настроение?}
    E -->|Да| G[Ресторан или бар]
    E -->|Нет| H[Настолки дома с друзьями]
    F -->|Творческое| I[Рисование или музыка]
    F -->|Спокойное| J[Прогулка по городу]
    D -->|Да| K{Формат?}
    D -->|Нет| L[Сериал под плед]
    K -->|Читать| M[Книга]
    K -->|Смотреть| N[Документалка]
    K -->|Играть| O[Стратегия или головоломка]"""

DIAGRAMS = """<!doctype html><html lang="ru" data-theme="light"><head><meta charset="utf-8">
<link rel="stylesheet" href="/style.css"><link rel="stylesheet" href="/tables.css"><link rel="stylesheet" href="/diagrams.css">
<style>body{padding:24px;background:var(--bg)} #box{max-width:760px;margin:0 auto}</style></head><body><div id="box"></div>
<script>var PG_LANG = "ru";</script>
<script src="/tables.js"></script><script src="/diagram-zoom.js"></script><script src="/diagrams.js"></script></body></html>"""

async def serve(route):
    path = route.request.url[len(ORIGIN):].split("?")[0]
    if path in ("/composer.html", "/diagrams.html"):
        await route.fulfill(body=COMPOSER if path == "/composer.html" else DIAGRAMS, content_type="text/html; charset=utf-8")
        return
    file = (STATIC / path.lstrip("/")).resolve()
    if STATIC.resolve() not in file.parents or not file.is_file():
        await route.fulfill(status=404, body="")
        return
    await route.fulfill(body=file.read_bytes(), content_type=mimetypes.guess_type(file.name)[0] or "application/octet-stream")

async def composer_checks(browser, shots):
    page = await browser.new_page(viewport={"width": 900, "height": 400})
    await page.route(f"{ORIGIN}/**", serve)
    await page.goto(f"{ORIGIN}/composer.html")
    field = page.locator("#input")
    await field.focus()
    out = {}
    state = lambda: page.evaluate("""() => { const f = document.getElementById('input'), c = document.querySelector('.cmdhl-chip'),
        g = document.querySelector('.cmdhl-ghost'), n = document.querySelector('.cmdhl-note');
        return { value: f.value, caret: f.selectionStart, end: f.selectionEnd, indent: f.style.textIndent, chip: !c.hidden && c.dataset.kind || '',
          ghost: !g.hidden, note: !n.hidden && n.textContent, height: f.style.height, chipLeft: parseFloat(c.style.left), chipW: parseFloat(c.style.width) }; }""")
    await page.keyboard.type("/image ")
    out["bare"] = await state()
    if shots:
        await page.screenshot(path=os.path.join(shots, "cmd_test_bare.png"))
    await page.keyboard.type("кот на луне")
    out["typed"] = await state()

    await page.keyboard.press("Home")
    await page.keyboard.press("ArrowRight")
    out["caret_home"] = await state()
    await page.keyboard.press("End")

    await page.keyboard.press("Control+a")
    await page.keyboard.press("Delete")
    out["cleared"] = await state()
    await page.keyboard.press("Control+z")
    out["undo"] = await state()

    await page.evaluate("() => { const f = document.getElementById('input'); f.value = ''; }")
    out["programmatic_clear"] = await state()
    await page.evaluate("() => document.execCommand('insertText', false, '/video волны на закате')")
    out["paste"] = await state()

    cdp = await page.context.new_cdp_session(page)
    await page.keyboard.press("End")
    await cdp.send("Input.imeSetComposition", {"text": "ка", "selectionStart": 2, "selectionEnd": 2})
    out["ime_composing"] = await state()
    await cdp.send("Input.insertText", {"text": "камера"})
    out["ime_done"] = await state()

    await page.keyboard.type(" " + "очень длинное описание сцены " * 8)
    out["long"] = await state()
    out["heights_grew"] = len(set(await page.evaluate("heights"))) > 1

    await page.evaluate("() => { const f = document.getElementById('input'); f.value = ''; f.dispatchEvent(new Event('input')); }")
    await page.keyboard.type("/imagine не команда")
    out["not_command"] = await state()
    if shots:
        await page.evaluate("() => { const f = document.getElementById('input'); f.value = ''; f.dispatchEvent(new Event('input')); }")
        await page.keyboard.type("/image кот на луне, акварель")
        await page.screenshot(path=os.path.join(shots, "cmd_test_typed.png"))
    await page.close()
    return out

async def zoom_checks(browser, shots):
    page = await browser.new_page(viewport={"width": 1366, "height": 800})
    await page.route(f"{ORIGIN}/**", serve)
    await page.goto(f"{ORIGIN}/diagrams.html")
    await page.evaluate("(code) => { const box = document.getElementById('box'); box.innerHTML = MemuDiagrams.block('mermaid', code, false); return MemuDiagrams.hydrate(box); }", SCHEME)
    await page.wait_for_selector(".mdz-ready .mdz-scroll svg", timeout=20000)
    state = lambda: page.evaluate("""() => { const s = document.querySelector('.mdz-scroll'), svg = s.querySelector('svg');
        const sizes = [...svg.querySelectorAll('text')].map((n) => parseFloat(getComputedStyle(n).fontSize));
        const scale = svg.getBoundingClientRect().width / svg.viewBox.baseVal.width;
        return { scale, pct: document.querySelector('.mdz-pct').textContent, text: Math.min(...sizes) * scale,
          wide: s.scrollWidth > s.clientWidth, cardWidth: document.querySelector('.memu-diagram').getBoundingClientRect().width,
          left: s.scrollLeft, top: s.scrollTop, height: s.clientHeight }; }""")
    out = {"initial": await state()}
    if shots:
        await page.screenshot(path=os.path.join(shots, "zoom_test_initial.png"))
    box = await page.locator(".mdz-scroll").bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    await page.mouse.move(cx, cy)
    await page.keyboard.down("Control")
    await page.mouse.wheel(0, -240)
    await page.keyboard.up("Control")
    out["ctrl_wheel_in"] = await state()
    await page.mouse.down()
    await page.mouse.move(cx - 150, cy - 60, steps=6)
    await page.mouse.up()
    out["dragged"] = await state()
    await page.click('[data-z="fit"]')
    out["fit"] = await state()
    await page.click('[data-z="one"]')
    out["one"] = await state()
    await page.click('[data-z="reset"]')
    out["reset"] = await state()
    await page.mouse.dblclick(cx, cy)
    out["dblclick"] = await state()
    await page.evaluate("document.documentElement.dataset.theme = 'dark'")
    await page.wait_for_timeout(1500)
    await page.wait_for_selector(".mdz-ready .mdz-scroll svg")
    out["after_theme"] = await state()
    if shots:
        await page.screenshot(path=os.path.join(shots, "zoom_test_dark.png"))
    await page.close()
    return out

async def main(shots):
    from playwright.async_api import async_playwright
    exe = os.environ.get("MEMU_TEST_CHROMIUM") or "/workspace/work/chromium/chrome"
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=exe if os.path.exists(exe) else None, args=["--no-sandbox"])
        try:
            result = {"cmd": await composer_checks(browser, shots), "zoom": await zoom_checks(browser, shots)}
        finally:
            await browser.close()
    print(json.dumps(result, ensure_ascii=False))

if __name__ == "__main__":
    shots_dir = sys.argv[sys.argv.index("--shots") + 1] if "--shots" in sys.argv else ""
    if shots_dir:
        os.makedirs(shots_dir, exist_ok=True)
    asyncio.run(main(shots_dir))
