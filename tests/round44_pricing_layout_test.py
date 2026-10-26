"""Actual Proxy HTML/CSS and quota UI rendered offline in disposable headless Chromium.

No user profile, app instance, account or computer-control API is accessed.
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
SHOTS = ROOT / "build" / "round44-ui"

def chromium():
    candidates = [os.environ.get("MEMU_TEST_CHROMIUM", ""),
                  r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                  r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "/usr/bin/chromium"]
    return next((value for value in candidates if value and Path(value).is_file()), "")

def fixture():
    models = [{"model": "claude-opus-5.5", "id": "api-claude-opus-5.5", "canonical": "claude-opus-5.5",
               "label": "Claude Opus 5.5", "default_price": {"input": 4, "output": 20, "cache_read": .2, "cache_write": 5, "source": "catalog"},
               "override": {"input": 2, "output": 10, "cache_read": .1, "cache_write": 2.5, "caching": True}},
              {"model": "provider-long-name/gpt-example-model-very-long-catalogue-id", "id": "api-gpt-example", "canonical": "gpt-example",
               "label": "GPT Example с очень длинным названием для проверки переноса текста",
               "default_price": {"input": 1.2, "output": 6, "cache_read": .3, "source": "catalog"}, "override": None}]
    now = time.time()
    account = {"id": "a", "label": "Основной API", "prefix": "api", "provider": "custom", "enabled": True,
               "balance_usd": 25, "spent_usd": 7.35, "remaining_usd": 17.65, "fraction": .706, "requests": 84,
               "started_at": now - 86400, "until": now + 7 * 86400, "daily_spend_usd": 7.35,
               "daily_allowance_usd": 2.52, "days_left": 2.4, "models": models}
    second = {**account, "id": "b", "label": "Резервный ключ", "prefix": "backup", "balance_usd": 10,
              "spent_usd": 9.1, "remaining_usd": .9, "fraction": .09, "unknown_requests": 2}
    return {"now": now, "settings": {"poll_minutes": 10, "save_quota": True, "notify": True}, "accounts": [],
            "api_budgets": {"accounts": [account, second], "available": [], "catalog_age": 600}}

@pytest.fixture(scope="module")
def browser():
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    executable = chromium()
    if not executable:
        pytest.skip("No headless Chromium executable")
    with sync_playwright() as pw:
        instance = pw.chromium.launch(executable_path=executable, headless=True,
                                     args=["--no-sandbox", "--disable-background-networking"])
        yield instance
        instance.close()

def offline_page(browser, width, height, theme):
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=theme)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    doc = (STATIC / "index.html").read_text(encoding="utf-8")

    doc = re.sub(r"<script\b[^>]*>.*?</script>", "", doc, flags=re.S | re.I)
    data = json.dumps(fixture(), ensure_ascii=False).replace("</", "<\\/")
    setup = """<script>
    window.LANG='ru'; window.$=s=>document.querySelector(s);
    window.esc=value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
    window.toast=()=>{}; window.__quotaFixture=DATA;
    window.api=async()=>structuredClone(window.__quotaFixture);
    document.documentElement.dataset.theme='THEME';
    document.querySelectorAll('.tab').forEach(el=>el.classList.toggle('active',el.id==='tab-quota'));
    document.querySelectorAll('.nav-item').forEach(el=>el.classList.toggle('active',el.dataset.tab==='quota'));
    document.querySelector('#crumb-page').textContent='Планировщик квот';
    document.querySelector('#app').hidden=false;
    </script><script src='/quota-planner.js'></script><script>MemuQuota.load();</script>""".replace("DATA", data).replace("THEME", theme)

    setup = setup.replace("document.querySelector('#app').hidden=false;", "document.querySelector('#app')?.classList.add('visible');document.querySelector('#login-overlay')?.setAttribute('hidden','');")
    doc = doc.replace("</body>", setup + "</body>")

    def route(request):
        parsed = urlsplit(request.request.url)
        if parsed.hostname != "quota.local":
            request.abort()
            return
        if parsed.path == "/":
            request.fulfill(status=200, content_type="text/html", body=doc)
            return
        target = (STATIC / parsed.path.lstrip("/")).resolve()
        if target.is_relative_to(STATIC.resolve()) and target.is_file():
            request.fulfill(status=200, content_type=mimetypes.guess_type(target)[0] or "application/octet-stream", body=target.read_bytes())
        else:
            request.fulfill(status=404, body="not found")
    page.route("**/*", route)
    page.goto("http://quota.local/")
    page.locator(".qp-budget").first.wait_for(state="visible")
    return context, page, errors

@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("width,height,label", [(1440, 1000, "desktop"), (390, 844, "narrow")])
def test_quota_overview_and_price_editor_fit_actual_proxy_shell(browser, theme, width, height, label):
    context, page, errors = offline_page(browser, width, height, theme)
    try:
        SHOTS.mkdir(parents=True, exist_ok=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        cards = page.locator(".qp-budget").evaluate_all("els=>els.map(el=>({width:el.clientWidth,scroll:el.scrollWidth,rect:el.getBoundingClientRect().toJSON()}))")
        assert all(card["scroll"] <= card["width"] + 1 for card in cards), cards
        assert all(card["rect"]["right"] <= width + 1 for card in cards), cards
        page.screenshot(path=str(SHOTS / f"quota-{label}-{theme}.png"), full_page=True)
        page.locator("[data-budget-edit='a']").click()
        dialog = page.locator(".qp-budget-dialog")
        dialog.wait_for(state="visible")
        page.locator(".qp-price-model summary").first.click()
        first = page.locator(".qp-price-model").first
        first.locator("[data-rate='input']").focus()
        geometry = dialog.evaluate("""el=>({rect:el.getBoundingClientRect().toJSON(),width:el.clientWidth,scroll:el.scrollWidth,
          bodyWidth:el.querySelector('.qp-budget-form-body').clientWidth,bodyScroll:el.querySelector('.qp-budget-form-body').scrollWidth,
          focus:getComputedStyle(document.activeElement).outlineStyle,
          fields:[...el.querySelectorAll('.qp-price-fields input')].slice(0,4).map(e=>e.getBoundingClientRect().toJSON())})""")
        assert geometry["rect"]["left"] >= 0 and geometry["rect"]["right"] <= width, geometry
        assert geometry["scroll"] <= geometry["width"] + 1, geometry
        assert geometry["bodyScroll"] <= geometry["bodyWidth"] + 1, geometry
        assert geometry["focus"] != "none", geometry
        assert all(field["width"] >= 65 and field["right"] <= geometry["rect"]["right"] for field in geometry["fields"]), geometry
        page.screenshot(path=str(SHOTS / f"quota-editor-{label}-{theme}.png"))
        page.keyboard.press("Escape")
        assert page.locator(".qp-budget-dialog").count() == 0
        assert not errors, errors
    finally:
        context.close()
