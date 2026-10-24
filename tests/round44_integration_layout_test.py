"""Render actual integration CSS in the real MemuAI settings shell, offline.

Only unrelated application JavaScript and server responses are replaced. The
document, stylesheet order, generated theme tokens, SettingsKit and both
integration settings modules are the same files that ship in the application.
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "pgstatic"
SHOTS = ROOT / "build" / "round44-ui"


@pytest.fixture(scope="module")
def browser():
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright

    candidates = [os.environ.get("MEMU_TEST_CHROMIUM", ""),
                  r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                  r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                  "/usr/bin/chromium"]
    executable = next((p for p in candidates if p and Path(p).is_file()), "")
    if not executable:
        pytest.skip("No headless Chromium executable")
    with sync_playwright() as pw:
        instance = pw.chromium.launch(executable_path=executable, headless=True,
                                     args=["--no-sandbox", "--disable-background-networking"])
        yield instance
        instance.close()


def offline_page(browser, feature, theme):
    from app.themes import theme_css

    context = browser.new_context(viewport={"width": 900, "height": 900}, color_scheme=theme,
                                  reduced_motion="reduce")
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    doc = (STATIC / "index.html").read_text("utf-8")
    doc = re.sub(r"<script\b[^>]*>.*?</script>", "", doc, flags=re.S | re.I)
    state = {"configured": False, "connected": False, "has_api_hash": False,
             "has_session": False, "has_proxy": False, "account": {},
             "flow": {"phase": "idle"}, "last_error": "", "config": {
                 "api_id": 0, "model": "", "enabled": False, "allow_writes": True,
                 "schedule_writes": False, "commands_enabled": False,
                 "max_media_mb": 20, "context_chars": 32000, "max_parallel": 2, "commands": []}}
    setup = """<script>
    window.PG_LANG='ru'; window.toast=()=>{};
    window.__tgu=TELEGRAM;
    window.api=async(path,opts)=>path.endsWith('/pairing')?{code:'ABCDEF123456',expires_in:300}:
      {connected:false,paired:false,read_pages:true,control:'ask',dfy:false,schedules:false};
    window.fetch=async()=>({ok:true,json:async()=>structuredClone(window.__tgu)});
    window.MemuModelPicker={rows:()=>[],kindOf:()=>'chat',name:id=>id,enhanceSelect:()=>{}};
    document.documentElement.dataset.theme='THEME';
    document.documentElement.dataset.design='classic';
    document.documentElement.dataset.motion='off';
    document.body.classList.remove('booting');document.body.classList.add('boot-ready');
    document.querySelector('#startup-cover')?.remove();
    </script><script src='/settings-kit.js'></script>
    <script src='/telegram-use.js'></script><script src='/browser-extension-settings.js'></script>
    <script>
    const K=window.MemuSettingsKit;
    const panel=document.querySelector('#panel');
    panel.className='panel settings-panel sx-panel';
    // This is the shell markup produced by app.js/renderSettingsPanel. The
    // integration links and sub-pages themselves are produced by the real kit.
    panel.innerHTML=`<button class="panel-close" aria-label="Закрыть">×</button>
      <div class="sx-shell" data-page="integrations"><aside class="sx-side">
        <div class="sx-side-head"><h2 class="sx-title">Настройки</h2>
          <label class="sx-search">${K.icons.search}<input id="sx-search" type="search" placeholder="Поиск настроек"><kbd>Ctrl F</kbd></label></div>
        <nav class="sx-nav"><div class="sx-nav-group"><div class="sx-nav-label">Приложение</div>
          <button class="settings-tab-btn"><span class="sx-nav-text">Общие</span></button>
          <button class="settings-tab-btn"><span class="sx-nav-text">Внешний вид</span></button>
          <button class="settings-tab-btn active" data-set-tab="integrations"><span class="sx-nav-text">Интеграции</span></button></div></nav>
        <div class="sx-side-foot">${K.icons.check}<span>Изменения сохраняются сразу</span></div>
      </aside><main class="sx-main"><header class="sx-topbar">
        <button class="sx-nav-toggle" aria-label="Все разделы">${K.icons.back}</button>
        <nav class="sx-crumbs" aria-label="Путь"></nav><span class="sx-saved"><span>Сохранено</span></span></header>
        <div class="sx-results" hidden></div><div class="settings-body sx-body">
          <div class="settings-tab-pane active" id="pane-integrations" data-page="integrations">
            <div class="sx-page-head"><div><h2 class="sx-page-title">Интеграции</h2></div></div>
            ${K.section({title:'Подключения',body:MemuBrowserExtension.listHtml()+MemuTelegramUse.listHtml()})}
            ${MemuBrowserExtension.subHtml()}${MemuTelegramUse.subsHtml()}
          </div></div></main></div>`;
    const back=document.querySelector('#panel-back');back.classList.add('visible');back.setAttribute('aria-hidden','false');
    K.configure({pages:[{id:'integrations',title:'Интеграции'}]});K.mount({page:'integrations'});
    MemuBrowserExtension.mount();MemuTelegramUse.mount();
    K.openSub('FEATURE'==='chrome'?'int-chrome':'int-telegram-use',{focus:false,animate:false});
    </script>""".replace("TELEGRAM", json.dumps(state, ensure_ascii=False)).replace("THEME", theme).replace("FEATURE", feature)
    doc = doc.replace("</body>", setup + "</body>")

    def route(request):
        parsed = urlsplit(request.request.url)
        if parsed.hostname != "integrations.local":
            request.abort()
        elif parsed.path == "/":
            request.fulfill(status=200, content_type="text/html", body=doc)
        elif parsed.path == "/themes.css":
            request.fulfill(status=200, content_type="text/css", body=theme_css())
        else:
            target = (STATIC / parsed.path.lstrip("/")).resolve()
            if target.is_relative_to(STATIC.resolve()) and target.is_file():
                request.fulfill(status=200, content_type=mimetypes.guess_type(target)[0] or "application/octet-stream",
                                body=target.read_bytes())
            else:
                request.fulfill(status=404, body="not found")

    page.route("**/*", route)
    page.goto("http://integrations.local/")
    page.locator(".bext-steps" if feature == "chrome" else ".tgu-setup-step").first.wait_for(state="attached")
    return context, page, errors


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("feature", ["chrome", "telegram"])
def test_integration_spacing_and_controls_in_actual_settings_shell(browser, feature, theme):
    context, page, errors = offline_page(browser, feature, theme)
    SHOTS.mkdir(parents=True, exist_ok=True)
    try:
        for width in (600, 900):
            page.set_viewport_size({"width": width, "height": 900})
            page.locator(".settings-body").evaluate("el=>el.scrollTop=0")
            root = page.locator("#bext-root" if feature == "chrome" else "#tgu-root")
            root.wait_for(state="visible")
            geometry = root.evaluate("""el=>{
              const body=el.closest('.settings-body'), r=el.getBoundingClientRect(), b=body.getBoundingClientRect();
              return {root:r.toJSON(),body:b.toJSON(),width:body.clientWidth,scrollWidth:body.scrollWidth,
                height:body.clientHeight,scrollHeight:body.scrollHeight,gap:parseFloat(getComputedStyle(el).rowGap)};
            }""")
            assert geometry["scrollWidth"] <= geometry["width"] + 1, geometry
            assert geometry["scrollHeight"] > geometry["height"], geometry
            assert geometry["root"]["left"] >= geometry["body"]["left"] + 16, geometry
            assert geometry["root"]["right"] <= geometry["body"]["right"] - 16, geometry
            assert geometry["gap"] >= 18, geometry
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), geometry
            buttons = root.locator("button:visible").evaluate_all("""els=>els.map(el=>({
              text:el.textContent,rect:el.getBoundingClientRect().toJSON(),
              body:el.closest('.settings-body').getBoundingClientRect().toJSON()}))""")
            assert all(b["rect"]["left"] >= b["body"]["left"] and
                       b["rect"]["right"] <= b["body"]["right"] and
                       b["rect"]["height"] >= 36 for b in buttons), buttons
            page.screenshot(path=str(SHOTS / f"integrations-{feature}-{width}-{theme}.png"))

            if feature == "chrome":
                download = page.locator('[data-bext="download"]')
                assert download.is_visible()
                assert download.bounding_box()["y"] < 850
                steps = page.locator(".bext-steps>li").evaluate_all("""els=>els.map(el=>{
                  const r=el.getBoundingClientRect(),text=el.querySelector('h4').getBoundingClientRect();
                  return {padding:parseFloat(getComputedStyle(el).paddingLeft),inset:text.left-r.left};})""")
                assert all(s["padding"] >= 16 and s["inset"] >= 50 for s in steps), steps
                for line in page.locator(".bext-copyline").all():
                    line.scroll_into_view_if_needed()
                    bounds = line.evaluate("""el=>{let r=el.getBoundingClientRect();return {rect:r.toJSON(),children:[...el.children].map(c=>c.getBoundingClientRect().toJSON())}}""")
                    assert all(c["left"] >= bounds["rect"]["left"] and c["right"] <= bounds["rect"]["right"] + 1 for c in bounds["children"]), bounds
                    a, b = bounds["children"]
                    assert a["right"] <= b["left"] or a["bottom"] <= b["top"], bounds
                page.locator('[data-bext-key="control"]').scroll_into_view_if_needed()
            else:
                steps = page.locator(".tgu-step-body").evaluate_all("els=>els.map(el=>({padding:parseFloat(getComputedStyle(el).paddingLeft),gap:parseFloat(getComputedStyle(el).rowGap)}))")
                assert all(s["padding"] >= 16 and s["gap"] >= 18 for s in steps), steps
                qr = page.locator('[data-tgu="qr"]')
                qr.scroll_into_view_if_needed()
                assert qr.is_visible() and qr.is_enabled()
                qr.focus()
                assert qr.evaluate("el=>getComputedStyle(el).outlineStyle") != "none"
                page.screenshot(path=str(SHOTS / f"integrations-{feature}-signin-{width}-{theme}.png"))
                page.locator('[data-tgu="save"]').scroll_into_view_if_needed()

            # Controls remain completely within the scrollport horizontally;
            # the end of the long page remains reachable by real scrolling.
            control = page.locator('[data-bext-key="control"]' if feature == "chrome" else '[data-tgu="save"]')
            bounds = control.evaluate("""el=>({control:el.getBoundingClientRect().toJSON(),body:el.closest('.settings-body').getBoundingClientRect().toJSON(),width:el.clientWidth,scroll:el.scrollWidth})""")
            assert bounds["control"]["left"] >= bounds["body"]["left"], bounds
            assert bounds["control"]["right"] <= bounds["body"]["right"], bounds
            assert bounds["control"]["bottom"] <= bounds["body"]["bottom"], bounds
            assert bounds["control"]["height"] >= 36, bounds
            page.screenshot(path=str(SHOTS / f"integrations-{feature}-controls-{width}-{theme}.png"))
        assert not errors, errors
    finally:
        context.close()
