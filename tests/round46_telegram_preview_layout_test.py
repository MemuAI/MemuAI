"""Actual Telegram preview controller, formatter and shipped CSS in offline Chromium."""
from __future__ import annotations

import json
import mimetypes
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from app.telegram_format import markdown_to_telegram_html, split_telegram_html
from app.telegram_use import caption_text, parse_html
from tests.round44_extension_ui_test import browser

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "pgstatic"
SHOTS = ROOT / "build" / "round46-ui"

@pytest.mark.parametrize("width,language", [(360, "ru"), (900, "ru"), (360, "en"), (900, "en")])
def test_formatted_preview_layout_safety_caption_and_multipart(browser, width, language):
    from app.themes import theme_css

    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme="dark", reduced_motion="reduce")
    page = context.new_page()
    failures, outbound = [], []
    page.on("pageerror", lambda error: failures.append(str(error)))
    document = (STATIC / "index.html").read_text("utf-8")
    document = re.sub(r"<script\b[^>]*>.*?</script>", "", document, flags=re.S | re.I)

    def route(request_route):
        request = request_route.request
        url = urlsplit(request.url)
        if url.hostname != "127.0.0.1":
            outbound.append(request.url)
            return request_route.abort()
        if url.path == "/api/pg/integrations/telegram-use/preview":
            body = request.post_data_json
            original = body["text"]
            text = caption_text(original) if body.get("kind") == "caption" else original
            parts = split_telegram_html(markdown_to_telegram_html(text))
            return request_route.fulfill(content_type="application/json", body=json.dumps({
                "parts": parts, "plain": [parse_html(part)[0] for part in parts], "truncated": text != original}))
        if url.path == "/":
            return request_route.fulfill(content_type="text/html", body=document)
        if url.path == "/themes.css":
            return request_route.fulfill(content_type="text/css", body=theme_css())
        target = (STATIC / url.path.lstrip("/")).resolve()
        if STATIC.resolve() in target.parents and target.is_file():
            return request_route.fulfill(content_type=mimetypes.guess_type(target.name)[0] or "application/octet-stream", body=target.read_bytes())
        return request_route.fulfill(status=404, body="")

    context.route("**/*", route)
    try:
        page.goto("http://127.0.0.1/")
        page.evaluate("""language => {
            window.PG_LANG=language; window.__result=null; window.__injected=false;
            document.documentElement.dataset.theme='dark';document.documentElement.dataset.design='classic';document.documentElement.dataset.motion='off';
            document.querySelector('#startup-cover')?.remove();document.body.classList.remove('booting');document.body.classList.add('boot-ready');
            window.panel=(html,kind,options)=>{
                const dialog=document.querySelector('#panel');dialog.className='panel '+kind;
                dialog.innerHTML='<button class="panel-close" aria-label="Close">×</button>'+html;
                dialog.setAttribute('role','dialog');dialog.setAttribute('aria-modal','true');window.__close=options.onClose;
                const back=document.querySelector('#panel-back');back.classList.add('visible');back.setAttribute('aria-hidden','false');
            };
            window.closePanel=()=>{document.querySelector('#panel-back').classList.remove('visible');};
        }""", language)
        page.add_script_tag(path=str(STATIC / "integrations-ui.js"))
        text = ("**Отчёт / Report**\n\n*Курсив / Italic* и `literal \\n`\n"
                "> Цитата / Quoted text\n\n[Документ / Document](https://example.test/document)\n\n"
                "```python\nprint('line 1')\nprint('line 2')\n```\n\n"
                '<img src="https://attacker.test/pixel" onerror="window.__injected=true">\n'
                '<script>window.__injected=true</script>\n[unsafe](javascript:window.__injected=true)\n')
        args = {"chat": "@ivan_" + "long_username_" * 10, "reply_to": 45, "silent": True, "text": text}
        page.evaluate("args=>{window.__pending=MemuIntegrations.confirm('telegram_use_send',args).then(value=>window.__result=value);}", args)
        page.wait_for_function("!document.querySelector('#gi-confirm-yes').disabled")
        assert page.locator("#gi-confirm-body b").first.text_content() == "Отчёт / Report"
        assert page.locator("#gi-confirm-body i").first.evaluate("node=>getComputedStyle(node).fontStyle") == "italic"
        assert "print('line 1')\nprint('line 2')" in page.locator("#gi-confirm-body pre").text_content()
        assert "literal \\n" in page.locator("#gi-confirm-body").text_content()
        assert "Report\n\n" in page.locator("#gi-confirm-body").text_content()
        assert page.locator("#gi-confirm-body img, #gi-confirm-body script, #gi-confirm-body [onerror], #gi-confirm-body a[href^='javascript:']").count() == 0
        assert page.evaluate("window.__injected") is False
        assert page.locator("#gi-confirm-body a[href='https://example.test/document']").get_attribute("rel") == "noopener noreferrer"
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        assert page.locator("#panel").evaluate("node=>node.scrollWidth<=node.clientWidth+1")
        assert page.locator("#gi-confirm-body").evaluate("node=>node.scrollWidth<=node.clientWidth+1")
        assert page.locator("#gi-confirm-yes").is_visible()
        assert page.locator("#gi-confirm-no").is_visible()
        assert page.locator(".gi-confirm-head .gi-brand svg circle").count() == 1
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / f"telegram-preview-{language}-{width}.png"), full_page=True)
        page.locator("#gi-confirm-no").click()
        page.wait_for_function("window.__result===false")

        caption = "**Фото / Photo**\n" + "🧠" * 700
        page.evaluate("caption=>{window.__result=null;window.__pending=MemuIntegrations.confirm('telegram_use_send_file',{chat:'Ivan',path:'report.pdf',caption}).then(value=>window.__result=value);}", caption)
        page.wait_for_function("!document.querySelector('#gi-confirm-yes').disabled")
        assert page.locator("#gi-confirm-body").text_content() == parse_html(markdown_to_telegram_html(caption_text(caption)))[0]
        assert ("shortened" if language == "en" else "сокращена") in page.locator("#gi-preview-status").text_content()
        page.locator("#gi-confirm-no").click()

        page.evaluate("()=>{window.__result=null;window.__pending=MemuIntegrations.confirm('telegram_use_send_file',{chat:'Ivan',path:'report.pdf',caption:' \\n\\t '}).then(value=>window.__result=value);}")
        assert page.locator("#gi-confirm-body").count() == 0
        assert page.locator("#gi-preview-status").count() == 0
        assert page.locator("#gi-confirm-yes").is_enabled()
        page.locator("#gi-confirm-no").click()

        long_text = "**Начало / Start**\n" + "длинная строка long line " * 600
        expected = split_telegram_html(markdown_to_telegram_html(long_text))
        assert len(expected) > 1
        page.evaluate("text=>{window.__pending=MemuIntegrations.confirm('telegram_use_send',{chat:'Ivan',text});}", long_text)
        page.wait_for_function("!document.querySelector('#gi-confirm-yes').disabled")
        assert page.locator(".gi-tg-part").count() == len(expected) - 1
        assert str(len(expected)) in page.locator("#gi-preview-status").text_content()
        page.locator("#gi-confirm-body").evaluate("node=>node.scrollTop=node.scrollHeight")
        assert page.locator("#gi-confirm-body").evaluate("node=>node.scrollWidth<=node.clientWidth+1")
        page.locator("#gi-confirm-no").click()
        assert outbound == []
        assert failures == []
    finally:
        context.close()
