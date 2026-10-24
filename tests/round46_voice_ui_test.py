"""Actual speech settings controller/styles in isolated headless Chromium."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.stt_providers import public_providers
from tests.round44_extension_ui_test import browser

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("width,language", [(360,"ru"),(900,"ru"),(360,"en"),(900,"en")])
def test_native_service_model_profiles_keys_test_and_local_selection(browser,width,language):
    context=browser.new_context(viewport={"width":width,"height":960},color_scheme="dark")
    page=context.new_page()
    try:
        page.set_content(f'<html lang="{language}"><body><main id="host" class="sx-shell"></main></body></html>')
        for name in ("style.css","settings.css","voice-player.css"):
            page.add_style_tag(path=str(ROOT/"app/pgstatic"/name))
        page.add_style_tag(content="""
            html,body{height:auto!important;min-height:100%;overflow:auto!important;margin:0;background:#191923;color:#f3f0fa;font-family:'Segoe UI',sans-serif}
            :root{--bg:#191923;--panel:#20202b;--panel-2:#252533;--text:#f3f0fa;--muted:#aaa8ba;--accent:#d2937d;--border:#444451;--accent-soft:#47342f}
            #host{display:block;padding:16px;max-width:860px;margin:auto}.sx-field,.sx-row,.sx-control{min-width:0}.sx-field-control{min-width:0}
            .pinput{color:inherit;background:#252533;border:1px solid #444451;padding:10px;font:inherit;max-width:100%;box-sizing:border-box}
            .pbtn{color:inherit;background:transparent;border:1px solid #444451}.sx-card{padding:14px}
        """)
        page.evaluate("""data=>{
            window.PG_LANG=data.language;window.__prefs={voice_stt_provider:'api',voice_stt_api_url:'https://api.openai.com/v1',voice_stt_api_model:'gpt-4o-mini-transcribe',voice_stt_api_key:'openai-key',voice_stt_local_model:'small',voice_tts_api_url:'https://api.elevenlabs.io',voice_tts_api_key:'existing-eleven-key'};
            window.__catalog={stt_api_providers:data.providers,stt_models:[{id:'tiny',name:'Whisper Tiny',downloaded:true,active:false},{id:'small',name:'Whisper Small',downloaded:true,active:true}]};
            window.__saves=[];window.__calls=[];
            window.__esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
            window.__ctx=()=>({en:data.language==='en',K:MemuSettingsKit,esc:__esc,pref:(k,d)=>__prefs[k]??d,catalog:__catalog,
                savePreferences:async patch=>{__saves.push(patch);Object.assign(__prefs,patch);return structuredClone(__prefs)},
                api:async(url,options)=>{__calls.push({url,...options});return {text:'Проверка записи / Test transcript'}},
                rerender:()=>draw(),applyVoicePreferencesUI:()=>{}});
            window.draw=()=>{document.querySelector('#host').innerHTML=MemuSettingsKit.section({title:data.language==='ru'?'Распознавание речи':'Speech recognition',body:MemuVoiceSettings.sttApiHtml(__ctx())});MemuVoiceSettings.bindStt(__ctx());};
        }""",{"language":language,"providers":public_providers()})
        page.add_script_tag(path=str(ROOT/"app/pgstatic/settings-kit.js"))
        page.add_script_tag(path=str(ROOT/"app/pgstatic/voice-settings.js"))
        page.evaluate("draw()")
        assert page.locator("#st-stt-model-pick").input_value()=="gpt-4o-mini-transcribe","Existing choice survives opening settings"
        page.locator("#st-stt-model-pick").select_option("gpt-transcribe")
        page.wait_for_function("__prefs.voice_stt_api_model==='gpt-transcribe'")
        page.locator("#st-stt-preset").select_option("elevenlabs")
        page.wait_for_function("__prefs.voice_stt_api_model==='scribe_v2'")
        assert page.locator("#st-stt-key").input_value()=="","A service never receives another service's key"
        page.locator("#st-stt-reuse-key").click()
        assert page.locator("#st-stt-key").input_value()=="existing-eleven-key"
        page.locator("#st-stt-model-pick").select_option("__custom")
        page.locator("#st-stt-model").fill("scribe_future");page.locator("#st-stt-model").blur()
        page.wait_for_function("__prefs.voice_stt_api_model==='scribe_future'")
        page.locator("#st-stt-preset").select_option("openai")
        page.wait_for_function("__prefs.voice_stt_api_url==='https://api.openai.com/v1'")
        assert page.locator("#st-stt-key").input_value()=="openai-key"
        assert page.locator("#st-stt-model-pick").input_value()=="gpt-transcribe"
        page.locator("#st-stt-preset").select_option("elevenlabs")
        page.wait_for_function("__prefs.voice_stt_api_model==='scribe_future'")
        assert page.locator("#st-stt-model").input_value()=="scribe_future"
        assert page.locator("#st-stt-key").input_value()=="existing-eleven-key"
        page.locator("#st-stt-test-file").set_input_files({"name":"test.wav","mimeType":"audio/wav","buffer":b"RIFF"+b"x"*60})
        page.wait_for_function("__calls.length===1")
        assert page.locator("#st-stt-test-status").text_content()=="Проверка записи / Test transcript"
        call=page.evaluate("__calls[0]");assert call["body"]["raw"] is True
        assert call["body"]["audio"].startswith("data:audio/wav;base64,")
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
        page.keyboard.press("Tab")  # use keyboard modality for :focus-visible
        page.locator("#st-stt-preset").focus()
        assert page.locator("#st-stt-preset").evaluate("e=>getComputedStyle(e).outlineStyle")!="none"
        output=ROOT/"build/round46-ui";output.mkdir(parents=True,exist_ok=True)
        page.screenshot(path=str(output/f"voice-stt-{language}-{width}.png"),full_page=True)

        # Exercise actual application's local-card rendering/selection handlers:
        # its stale catalog still marks Small active after prefs selects Tiny.
        source=(ROOT/"app/pgstatic/app.js").read_text(encoding="utf-8")
        cards=source[source.index("  const voiceModelCards ="):source.index("  const contextModel =",source.index("  const voiceModelCards ="))]
        bind=source[source.index('  $$("[data-voice-select]")'):source.index('  $$("[data-voice-download]")',source.index('  $$("[data-voice-select]")'))]
        page.evaluate("""data=>{
            const en=PG_LANG==='en', esc=__esc,pref=__ctx().pref,voiceCatalog=__catalog,icon=()=>'',voiceDescription=()=>'',voiceLanguages=()=>'',voiceSettingsContext=__ctx;
            window.$=s=>document.querySelector(s);window.$$=s=>Array.from(document.querySelectorAll(s));
            const savePreferences=__ctx().savePreferences,applyVoicePreferencesUI=()=>{},toast=()=>{};
            const render=()=>{eval(data.cards+';document.querySelector("#host").innerHTML=voiceModelCards;');eval(data.bind);};
            const rerenderSettingsPreservingScroll=render;render();
        }""",{"cards":cards,"bind":bind})
        page.locator('[data-voice-select="tiny"] [data-voice-choose]').click()
        page.wait_for_function("__prefs.voice_stt_local_model==='tiny'")
        assert page.locator('[data-voice-select="tiny"]').get_attribute("class").endswith(" active")
        assert "active" not in page.locator('[data-voice-select="small"]').get_attribute("class")
        assert page.locator('[data-voice-select="tiny"] em').count()==1
    finally:
        context.close()


def test_byte_progress_and_cancel_are_accessible_and_updates_keep_focused_input(browser):
    page=browser.new_page(viewport={"width":420,"height":500})
    try:
        page.set_content('<main><input id="focus" value="unsaved text"><div id="job"></div></main>')
        page.add_script_tag(path=str(ROOT/"app/pgstatic/voice-settings.js"))
        page.evaluate("""()=>{
            const row={id:'tiny',stage:'downloading',progress:30,bytes_downloaded:3*1048576,total_bytes:10*1048576};
            document.querySelector('#job').innerHTML=MemuVoiceSettings.downloadHtml({en:true,esc:v=>String(v)},row,'stt');
            document.querySelector('#focus').focus();
            MemuVoiceSettings.updateDownloads({stt_models:[{...row,progress:60,bytes_downloaded:6*1048576}]},true);
        }""")
        assert page.locator("progress").get_attribute("value")=="60"
        assert "6.0 / 10.0 MB" in page.locator("[data-voice-progress-label]").text_content()
        assert page.locator("[data-voice-cancel]").get_attribute("aria-label")=="Cancel download"
        assert page.evaluate("document.activeElement.id")=="focus"
        assert page.locator("#focus").input_value()=="unsaved text"
        page.evaluate("MemuVoiceSettings.updateDownloads({stt_models:[{id:'tiny',stage:'downloading',progress:null,bytes_downloaded:2097152,total_bytes:0}]},true)")
        assert page.locator("progress").get_attribute("value") is None
        assert "%" not in page.locator("[data-voice-progress-label]").text_content()
    finally:
        page.close()
