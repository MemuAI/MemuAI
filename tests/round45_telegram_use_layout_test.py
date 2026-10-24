"""Render actual Telegram settings styles/controller in an isolated Chromium test."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import telegram_use_config as config
from tests.round44_extension_ui_test import browser

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("width", [360, 860])
def test_telegram_arguments_and_speech_layout_keyboard_and_autosave(browser, width):
    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme="dark")
    page = context.new_page()
    try:
        page.set_content('<html lang="ru"><body><main id="host"></main></body></html>')
        for name in ("style.css", "settings.css", "telegram-use.css"):
            path = ROOT / "app" / "pgstatic" / name
            if path.exists():
                page.add_style_tag(path=str(path))
        page.add_style_tag(content="""
            html,body{height:auto!important;min-height:100%;overflow:auto!important;margin:0;background:#191923;color:#f3f0fa;font-family:'Segoe UI',sans-serif}
            #host{padding:20px;max-width:900px;margin:auto}.sx-sub{display:block!important;position:static!important}
            .sx-sub-body{padding:0!important}.pinput{color:inherit;background:#252533;border:1px solid #444451;padding:10px;font:inherit}
            .pbtn{color:inherit;background:transparent;border:1px solid #444451}.sx-sub-head{margin-bottom:20px}
        """)
        page.evaluate("""state=>{
            window.PG_LANG='ru';window.__state=state;window.__saves=[];
            window.fetch=async(url,init={})=>{
                if(url.endsWith('/voice-options'))return {ok:true,json:async()=>({stt_models:[{id:'small',name:'Whisper Small',downloaded:true}],cleanup_prompt:'Correct spelling and punctuation. Preserve the original meaning.'})};
                if(init.body){const b=JSON.parse(init.body);window.__saves.push(b);Object.assign(window.__state.config,b);}
                return {ok:true,json:async()=>structuredClone(window.__state)};
            };
            window.MemuModelPicker={rows:()=>[{id:'model',name:'Model'}],kindOf:()=>'chat',name:id=>id,enhanceSelect:()=>{}};
        }""", {"connected": True, "configured": True, "has_api_hash": True, "has_session": True,
                   "has_proxy": False, "account": {"name": "Тест"}, "flow": {"phase": "connected"},
                   "last_error": "", "config": config.normalize({"api_id": 123, "enabled": True})})
        page.add_script_tag(path=str(ROOT / "app/pgstatic/settings-kit.js"))
        page.add_script_tag(path=str(ROOT / "app/pgstatic/telegram-use.js"))
        page.evaluate("document.querySelector('#host').innerHTML=MemuTelegramUse.subsHtml();MemuTelegramUse.mount()")
        page.locator('[data-tgu-field="system_prompt"]').wait_for(state="visible")
        page.locator('#tgu-command-settings').evaluate("e=>e.open=true")
        page.locator('[data-tgu-command="2"]').evaluate("e=>e.open=true")
        page.locator('[data-tgu-field="stt_source"]').evaluate("e=>e.closest('details').open=true")
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
        for selector in ('[data-tgu-field="stt_source"]', '[data-tgu-argument="0"] [data-tgu-arg-field="name"]'):
            control = page.locator(selector).first
            control.focus()
            box = control.bounding_box()
            assert box and box["x"] >= 0 and box["x"] + box["width"] <= width + 1
            assert control.evaluate("e=>getComputedStyle(e).outlineStyle") != "none"
        prompt = page.locator('[data-tgu-field="system_prompt"]')
        prompt.fill("Отвечай кратко и сохраняй смысл.")
        page.wait_for_function("window.__saves.some(s=>s.system_prompt==='Отвечай кратко и сохраняй смысл.')")
        assert prompt.input_value() == "Отвечай кратко и сохраняй смысл."
        page.locator('[data-tgu-field="stt_source"]').scroll_into_view_if_needed()
        output = ROOT / "build" / "round45-ui"
        output.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(output / f"telegram-speech-{width}.png"))
        page.locator('[data-tgu-command="2"]').scroll_into_view_if_needed()
        page.screenshot(path=str(output / f"telegram-arguments-{width}.png"))
    finally:
        context.close()
