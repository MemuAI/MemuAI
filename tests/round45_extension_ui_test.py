"""Round 45 user-facing Chrome regressions in an isolated headless fixture."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.round44_extension_ui_test import (CHROME_MOCK, MODEL, PNG, browser, layout,
                                             panel, panel_server)

SHOTS = Path(__file__).resolve().parents[1] / "build" / "round45-ui"

def shot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / name), full_page=page.url.endswith("options.html"))

@pytest.mark.parametrize("width,height", [(280,320),(360,480),(520,800)])
def test_textarea_shrinks_after_long_paste_resize_hide_and_clear(panel,width,height):
    page,_=panel
    page.set_viewport_size({"width":width,"height":height})
    page.locator("#input").fill("Очень длинная строка для проверки поля ввода\n"*650)
    assert page.locator("#input").evaluate("e=>e.getBoundingClientRect().height")<=min(168,height*.24)+1
    layout(page)
    page.locator("#input").fill("")
    assert page.locator("#input").evaluate("e=>e.getBoundingClientRect().height")<=55
    page.evaluate("document.querySelector('#input').style.height='5000px'")
    page.locator("#input").fill("Коротко")
    assert page.locator("#input").evaluate("e=>e.getBoundingClientRect().height")<=55
    page.locator("#file-input").set_input_files({"name":"picture.png","mimeType":"image/png","buffer":PNG})
    page.wait_for_function("document.querySelectorAll('#attachments .attachment').length===1")
    page.locator("#page").click()
    page.locator("#page-context").wait_for(state="visible")
    layout(page)
    shot(page,f"composer-bounded-{width}x{height}.png")

def test_models_take_desktop_pins_and_default_and_write_delta_back(panel):
    page,fixture=panel
    fixture.pinned_models=["api-model-005"]
    fixture.default_model="api-model-005"
    page.locator("#connection").click()
    page.wait_for_function("document.querySelector('#model-label').textContent.includes('005')")
    page.locator("#model-trigger").click()
    page.locator("#favorite-models").click()
    assert page.locator("#model-list .model-row").count()==1
    assert "005" in page.locator("#model-list").inner_text()
    page.locator("#all-models").click()
    page.locator("#model-search").fill("Claude")
    page.locator("#model-list .favorite").click()
    page.wait_for_function("window.__storage.favorites.includes('api-claude-opus-5.5')")
    assert set(fixture.pinned_models)=={MODEL,"api-model-005"}
    page.locator("#model-search").fill("")
    page.locator("#favorite-models").click()
    assert "005" in page.locator("#model-list .model-row").first.inner_text()
    page.locator("#all-models").click()
    page.locator("#model-search").fill("Claude")
    with fixture.lock:
        patch=next(body for method,path,body in reversed(fixture.calls) if path=="model-preferences")
    assert patch=={"pin_model":MODEL,"pinned":True,"profile_id":"default"}
    page.locator("#model-list .model-row > button").first.click()
    page.wait_for_function("!document.querySelector('#model-dialog').open")
    assert fixture.default_model==MODEL
    shot(page,"desktop-model-sync.png")

def test_unavailable_model_visible_but_cannot_be_selected(panel):
    page,fixture=panel
    row={"id":"ag-gemini-4-argon","name":"Gemini 4 Argon","provider":"antigravity",
         "caps":{"text":True},"enabled":True,"availability":{"available":False,"reason":"Недоступна для аккаунта"}}
    fixture.models.append(row)
    try:
        page.locator("#connection").click()
        page.locator("#model-trigger").click()
        page.locator("#model-search").fill("Gemini 4 Argon")
        page.wait_for_function("document.querySelector('#model-list').textContent.includes('Gemini 4 Argon')")
        assert page.locator("#model-list .model-row > button").first.is_disabled()
        assert "Недоступна для аккаунта" in page.locator("#model-list").inner_text()
        shot(page,"model-unavailable-visible.png")
    finally:
        fixture.models.remove(row)

@pytest.mark.parametrize("browser_only",[True,False])
def test_browser_history_separate_by_default_and_main_history_accessible(panel,browser_only):
    page,fixture=panel
    browser_chat={"id":"browser-isolated","title":"Личный чат браузера","browser_only":browser_only,
                  "visibility":"browser" if browser_only else "main","origin":"browser_extension",
                  "updated":1800000000000,"model":MODEL}
    fixture.chats.append(browser_chat)
    try:
        page.locator("#connection").click()
        page.locator("#history").click()
        page.get_by_text("Личный чат браузера",exact=True).wait_for()
        assert "Разговор из MemuAI" not in page.locator("#chat-list").inner_text()
        assert page.locator("#history-browser").get_attribute("aria-pressed")=="true"
        page.locator("#history-main").click()
        assert "Разговор из MemuAI" in page.locator("#chat-list").inner_text()
        assert ("Личный чат браузера" in page.locator("#chat-list").inner_text()) is not browser_only
        page.locator("#history-all").click()
        assert "Личный чат браузера" in page.locator("#chat-list").inner_text()
        shot(page,"separate-history.png")
    finally:
        fixture.chats.remove(browser_chat)

def test_one_click_summary_reads_current_page_and_sends_without_manual_submit(panel):
    page,fixture=panel
    page.locator('[data-page-action="summary"]').first.click()
    page.locator("#messages .assistant .content h1").wait_for()
    with fixture.lock:
        request=next(body for method,path,body in reversed(fixture.calls) if method=="POST" and path=="chat")
    assert request["page_context"] is True
    assert request["tab_id"]==7
    assert "перескажи" in request["text"].lower()
    assert page.locator("#page-context").is_visible()

def test_privacy_hint_describes_existing_chat_storage_not_new_chat_default(panel):
    page,fixture=panel
    fixture.settings["save_to_main"]=False
    page.locator("#history").click()
    page.locator("#history-main").click()
    page.locator("#chat-search").fill("Разговор из MemuAI")
    page.locator("#chat-list .chat-row button").first.click()
    page.get_by_text("Привет из основного приложения",exact=True).wait_for()
    assert page.locator("#composer-hint").inner_text()=="Общий чат MemuAI"
    page.locator("#new").click()
    assert page.locator("#composer-hint").inner_text()=="Чат только в браузере"

def test_tool_timeline_and_one_readable_approval_for_all_tabs(panel):
    page,fixture=panel
    tabs=[{"tab_id":101,"title":"GitHub · MemuAI","url":"https://github.com/memu/ai"},
          {"tab_id":202,"title":"GitHub · Issues","url":"https://github.com/memu/ai/issues"}]
    fixture.stream_events=[{"event":"chat","id":"browser-new"},
       {"event":"tool","id":"list-tabs","tool":"chrome_tabs","phase":"running","title":"Открытые вкладки"},
       {"event":"tool","id":"list-tabs","tool":"chrome_tabs","phase":"done","title":"Открытые вкладки","detail":"Найдены 2 вкладки GitHub"},
       {"event":"tool","id":"close-batch","tool":"chrome_close_tabs","phase":"running","title":"Закрытие вкладок GitHub"},
       {"event":"confirm","id":"approve-batch","tool":"chrome_close_tabs","title":"Закрыть 2 вкладки?",
        "description":"Будут закрыты все найденные вкладки GitHub.","args":{"tab_ids":[101,202]},"tabs":tabs}]
    fixture.tool_activity=[{"id":"list-tabs","tool":"chrome_tabs","phase":"done","title":"Открытые вкладки"},
                           {"id":"close-batch","tool":"chrome_close_tabs","phase":"done","title":"Закрытие вкладок GitHub"}]
    fixture.hold_stream=True
    page.locator("#input").fill("Закрой все вкладки GitHub")
    page.locator("#send").click()
    page.locator("#approval").wait_for(state="visible")
    assert page.locator("#approval .approval-tabs li").count()==2
    assert "GitHub · MemuAI" in page.locator("#approval").inner_text()
    assert "https://github.com/memu/ai/issues" in page.locator("#approval").inner_text()
    assert '"tab_ids"' not in page.locator("#approval").inner_text()
    assert "Закрытие вкладок GitHub" in page.locator(".tool-trace").inner_text()
    shot(page,"batch-approval-readable.png")
    with fixture.lock:
        before=sum(path=="answer" for _,path,_ in fixture.calls)
    page.get_by_role("button",name="Разрешить для всех").click()
    page.wait_for_function("document.querySelector('#stop').hidden")
    with fixture.lock:
        answers=[body for _,path,body in fixture.calls if path=="answer"]
    assert len(answers)==before+1
    assert answers[-1]=={"id":"approve-batch","approve":True}
    assert page.locator(".tool-trace .tool-step").count()==2
    shot(page,"tool-timeline-complete.png")

def options_page(browser,fixture):
    context=browser.new_context(viewport={"width":520,"height":800})
    context.add_init_script("("+CHROME_MOCK+")("+json.dumps({"base":fixture.base,"model":MODEL})+")")
    context.add_init_script("""(() => {const click=HTMLAnchorElement.prototype.click;
      HTMLAnchorElement.prototype.click=function(){if(this.href.startsWith('memuai:')){window.__deepLink=this.href;return;}return click.call(this);};})()""")
    page=context.new_page()
    page.goto(fixture.base+"/options.html")
    page.get_by_text("Подключено · Основной профиль",exact=False).wait_for()
    return context,page

def test_options_autosave_sync_and_shared_model_preferences(browser,panel_server):
    fixture=panel_server
    context,page=options_page(browser,fixture)
    try:
        checkbox=page.locator('[data-setting="save_to_main"]')
        previous=fixture.settings["save_to_main"]
        checkbox.set_checked(not previous)
        page.wait_for_function("document.querySelector('#settings-save').textContent.includes('синхронизированы')")
        assert fixture.settings["save_to_main"] is not previous
        fixture.settings["dfy"]=True
        page.locator("#check").click()
        page.wait_for_function("document.querySelector('[data-setting=dfy]').checked")
        page.locator('[data-setting="control"]').select_option("deny")
        page.wait_for_function("document.querySelector('#settings-save').textContent.includes('синхронизированы')")
        assert fixture.settings["control"]=="deny"
        page.locator("#models-search").fill("Claude")
        pin=page.locator("#options-models input[type=checkbox]")
        pin.set_checked(True)
        page.wait_for_function("window.__storage.favorites.includes('api-claude-opus-5.5')")
        assert MODEL in fixture.pinned_models
        shot(page,"all-settings-inside-extension.png")
    finally:
        fixture.settings.update(save_to_main=False,dfy=False,control="ask")
        context.close()

def test_deeplink_pairing_uses_random_nonce_and_never_puts_code_or_token_in_link(browser,panel_server):
    context,page=options_page(browser,panel_server)
    seen=[]
    def connect(route):
        seen.append(route.request.url)
        route.fulfill(status=200,content_type="application/json",body=json.dumps({
            "ready":True,"code":"ABC12345","base":panel_server.base}))
    context.route("**/bridge/connect?challenge=*",connect)
    try:
        page.locator("#connect-app").click()
        page.wait_for_function("window.__storage.token==='new-offline-token'")
        link=page.evaluate("window.__deepLink")
        assert re.match(r"^memuai://browser-extension/connect\?challenge=[0-9a-f]{32}&extension_id=[a-p]{32}$",link)
        assert "ABC12345" not in link and "offline-token" not in link and "token=" not in link
        assert seen
        page.get_by_text("Подключено. Настройки и чаты синхронизированы.",exact=True).wait_for()
        shot(page,"deeplink-connected.png")
    finally:
        context.close()

def test_cancelled_deeplink_does_not_install_a_late_pair_token(browser,panel_server):
    context,page=options_page(browser,panel_server)
    pending=[]
    context.route("**/bridge/connect?challenge=*",lambda route:route.fulfill(status=200,content_type="application/json",body=json.dumps({"ready":True,"code":"ABC12345","base":panel_server.base})))
    page.route("**/bridge/pair",lambda route:pending.append(route))
    try:
        page.locator("#connect-app").click()
        page.wait_for_timeout(100)
        assert len(pending)==1
        page.locator("#cancel-connect").click()
        pending[0].fulfill(status=200,content_type="application/json",body='{"token":"must-not-be-stored"}')
        page.wait_for_timeout(150)
        assert page.evaluate("window.__storage.token")=="offline-test-token"
        assert "отменено" in page.locator("#connect-status").inner_text()
    finally:
        context.close()

def test_invalid_manual_address_does_not_open_deeplink_or_stick_connect_button(browser,panel_server):
    context,page=options_page(browser,panel_server)
    try:
        page.locator(".manual-connection summary").click()
        page.locator("#base").fill("https://example.com")
        page.locator("#connect-app").click()
        assert page.evaluate("window.__deepLink===undefined")
        assert page.locator("#connect-app").is_enabled()
        assert "локальный" in page.locator("#status").inner_text()
    finally:
        context.close()

def test_settings_failed_autosave_restores_previous_value_and_explains_error(browser,panel_server):
    context,page=options_page(browser,panel_server)
    page.route("**/bridge/settings",lambda route:route.fulfill(status=503,content_type="application/json",body='{"error":"MemuAI temporarily unavailable"}') if route.request.method=="POST" else route.continue_())
    try:
        checkbox=page.locator('[data-setting="save_to_main"]')
        previous=checkbox.is_checked()
        checkbox.click()
        page.get_by_text("MemuAI temporarily unavailable",exact=True).wait_for()
        assert checkbox.is_checked() is previous
        assert checkbox.is_enabled()
    finally:
        context.close()

@pytest.mark.parametrize("status_code",[401,403])
def test_disconnect_with_already_invalid_token_removes_local_connection_with_correct_reason(browser,panel_server,status_code):
    context,page=options_page(browser,panel_server)
    page.route("**/bridge/revoke",lambda route:route.fulfill(status=status_code,
               content_type="application/json",body='{"detail":"Access no longer valid"}'))
    try:
        page.locator("#disconnect").click()
        page.wait_for_function("window.__storage.token===undefined")
        message=page.locator("#status").inner_text()
        assert "уже был отозван или истёк" in message
        assert "недоступен" not in message
        assert "error" not in page.locator("#status").get_attribute("class")
        assert page.locator('[data-setting="read_pages"]').is_disabled()
    finally:
        context.close()
