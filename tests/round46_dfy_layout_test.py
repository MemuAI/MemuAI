"""Shared DFY browser chooser in an isolated, headless Chromium page."""
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]

@pytest.mark.parametrize("width,height", [(280,320),(360,640),(860,650)])
def test_choice_is_visible_responsive_and_cancellable(width,height):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome",headless=True)
        page = browser.new_page(viewport={"width":width,"height":height})
        page.set_content('<html lang="ru"><style>:root{--text:#e8e8ef;--panel:#22222b;--line:#555}body{background:#14141a}</style><body><button id="start">DFY</button></body></html>')
        page.evaluate("""() => {
          window.calls=[];
          window.fetch=async(url,init={})=>({ok:true,json:async()=>url==='/api/dfy/status'
            ? {ready:true,enabled:true,model:'vision',browser_choice_required:true,browser_choices:[
              {id:'isolated',name:'Отдельный браузер DFY',available:true,reason:''},
              {id:'chrome',name:'Мой Chrome',available:true,reason:''}]}
            : init.method==='POST' ? (calls.push(JSON.parse(init.body)),{id:'task',status:'queued'}) : {tasks:[]}});
        }""")
        page.add_script_tag(path=str(ROOT/"app/pgstatic/dfy-client.js"))
        page.evaluate("() => { document.getElementById('start').onclick=()=>MemuDFY.start('Проверить статус заказа').catch(e=>window.cancelled=e.code); }")
        page.locator("#start").click()
        chooser=page.locator("dialog.dfy-browser-choice")
        chooser.wait_for(state="visible")
        rect=chooser.bounding_box()
        assert rect and rect["x"]>=0 and rect["y"]>=0
        assert rect["x"]+rect["width"]<=width+1
        assert rect["y"]+rect["height"]<=height+1
        for button in chooser.locator("button").all():
            button_rect=button.bounding_box()
            assert button_rect and button_rect["y"]>=0
            assert button_rect["y"]+button_rect["height"]<=height+1
        assert page.evaluate("calls.length")==0
        shots=ROOT/"build/round46-ui";shots.mkdir(parents=True,exist_ok=True)
        page.screenshot(path=str(shots/f"dfy-choice-{width}x{height}.png"))
        page.locator("[data-cancel]").click()
        page.wait_for_function("window.cancelled==='dfy_cancelled'")
        assert page.evaluate("calls.length")==0
        page.locator("#start").click()
        page.locator('[data-browser="chrome"]').click()
        page.wait_for_function("calls.length===1")
        assert page.evaluate("calls[0].browser")=="chrome"
        browser.close()
