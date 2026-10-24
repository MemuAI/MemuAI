"""Shipped Proxy CSS and setup controller in isolated Chromium, no account/network."""
from __future__ import annotations

import json
from pathlib import Path
import pytest
from tests.round46_panels_ui_test import browser

ROOT=Path(__file__).resolve().parents[1]
STATIC=ROOT/"app/static"


@pytest.mark.parametrize("language,width",[("ru",360),("en",360),("ru",900),("en",900)])
def test_setup_modes_and_actions_fit_actual_styles(browser,language,width):
    context=browser.new_context(viewport={"width":width,"height":900},color_scheme="dark")
    page=context.new_page();errors=[];outbound=[];page.on("pageerror",lambda exc:errors.append(str(exc)))
    def route(r):
        from urllib.parse import urlsplit
        url=urlsplit(r.request.url)
        if url.hostname!="127.0.0.1":outbound.append(r.request.url);return r.abort()
        file=STATIC/url.path.lstrip("/")
        if file.is_file():return r.fulfill(content_type="text/css" if file.suffix==".css" else "application/javascript",body=file.read_bytes())
        return r.fulfill(content_type="text/html; charset=utf-8",body='''<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="/style.css"></head><body><div id="modal" class="modal show"><div id="modal-box" class="modal-box"><h3>OpenCode</h3><div class="field"><label>Connection</label><select id="af-mode"><option value='free'>free</option><option value='zen'>zen</option><option value='go'>go</option><option value='gateway'>gateway</option></select></div><div class="field"><input id="af-api_key"></div><button class="btn primary">Save</button></div></div><script src="/opencode-setup.js"></script></body></html>''')
    page.route("**/*",route);page.goto("http://127.0.0.1:18147/")
    app_source=(STATIC/"app.js").read_text("utf-8")
    wire=app_source[app_source.index("function psLabel(option)"):app_source.index("\nasync function loadSettings()")]
    page.add_script_tag(content=wire)
    page.evaluate("""lang=>{window.LANG=lang;window.calls=[];window.ready=false;window.api=async(url,options)=>{window.calls.push({url,options});if(options?.body?.action==='install')window.ready=true;return {installed:window.ready,supported:true,installing:false,version:'v1.4.3'};};for(const o of document.querySelectorAll('option'))o.textContent=psLabel(o.value);wireOpenCodeFields('af-');} """,language)
    page.wait_for_function("!document.querySelector('.oc-actions button').disabled")
    assert len(page.evaluate("calls"))==1
    assert page.locator(".oc-actions button").first.inner_text()==("Установить шлюз" if language=="ru" else "Install gateway")
    assert not page.locator("#af-api_key").is_visible()
    shots=ROOT/"build/round47-ui";shots.mkdir(parents=True,exist_ok=True)
    page.screenshot(path=str(shots/f"opencode-free-{language}-{width}.png"),full_page=True)
    page.locator(".oc-actions button").first.click();page.wait_for_function("document.querySelector('.oc-actions button').disabled")
    for mode in ("zen","go","gateway"):
        page.locator("#af-mode").select_option(mode)
        if mode=="go":assert page.locator('.oc-setup a').first.get_attribute('href')=='https://opencode.ai/auth'
        metrics=page.locator(".oc-setup").evaluate("e=>({width:e.clientWidth,scroll:e.scrollWidth,right:e.getBoundingClientRect().right,viewport:innerWidth})")
        assert metrics["scroll"]<=metrics["width"]+1 and metrics["right"]<=width, json.dumps(metrics)
        page.screenshot(path=str(shots/f"opencode-{mode}-{language}-{width}.png"),full_page=True)
    context.close();assert not errors;assert not outbound
