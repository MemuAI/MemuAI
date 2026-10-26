"""Actual panel editor with existing Proxy accounts; no browser profile or API."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from tests.panels_browser_test import FIXTURE, ORIGIN, STATIC, R22_SNAPSHOT

@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    candidates = [os.environ.get("MEMU_TEST_CHROMIUM", ""), r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                  r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"]
    executable = next((path for path in candidates if path and Path(path).is_file()), None)
    if executable is None:
        pytest.skip("Headless Chromium is unavailable")
    with sync_playwright() as pw:
        instance = pw.chromium.launch(executable_path=executable, headless=True, args=["--no-sandbox", "--disable-background-networking"])
        yield instance
        instance.close()

@pytest.mark.parametrize("width", [360, 1000])
def test_account_selection_filtering_reload_and_request_payload(browser, width):
    snapshot = copy.deepcopy(R22_SNAPSHOT)
    panel = next(row for row in snapshot["panels"] if row["id"] == "p_usage00001")
    panel["source"]["params"]["accounts"] = [{"id": "a_12345678", "provider": "kiro", "mode": "proxy", "proxy_account_id": "kiro1", "name": "Work"}]
    accounts = [{"id": "kiro1", "provider": "kiro", "label": "Work", "enabled": True, "api_budget": True},
                {"id": "go1", "provider": "custom", "label": "Go account", "quota_planner": True}]
    sent = []
    errors = []
    context = browser.new_context(viewport={"width": width, "height": 850})
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    def route(request):
        path = request.request.url.removeprefix(ORIGIN).split("?")[0]
        body = {}
        if path == "/":

            return request.fulfill(status=200, content_type="text/html", body=FIXTURE.replace("<style>", "<style>*{box-sizing:border-box}"))
        if path == "/api/pg/panels":
            body = snapshot
        elif path.endswith("/panels-usage/detect"):
            body = {"detected": {"proxy_accounts": accounts}}
        elif path.endswith("/panels-usage/test"):
            sent.append(request.request.post_data_json)
            body = {"account": {"state": "ok", "limits": [{"title": "Credits", "used": 25}], "plan": "Pro"}}
        elif path.startswith("/api/"):
            body = {"ok": True}
        else:
            file = STATIC / path.lstrip("/")
            return request.fulfill(status=200, content_type="text/css" if file.suffix == ".css" else "application/javascript", body=file.read_bytes())
        return request.fulfill(status=200, content_type="application/json", body=json.dumps(body))
    page.route(ORIGIN + "/**", route)
    page.goto(ORIGIN + "/")
    page.wait_for_selector('#panels-home [data-panel-id="p_usage00001"]')
    page.evaluate("MemuPanels._lay.openEditor('p_usage00001')")
    row = page.locator(".mp-acc-row")
    select = row.locator("[data-acc-proxy]")
    page.wait_for_function("document.querySelector('[data-acc-proxy]')?.querySelector('option[value=kiro1]')?.textContent.includes('Work')")
    assert select.input_value() == "kiro1"
    assert select.locator("option[value=go1]").count() == 0
    row.locator("[data-acc-test]").click()
    page.wait_for_function("window.__requests.some(r => r[1].endsWith('/panels-usage/test'))")
    assert sent[-1]["provider"] == "kiro"
    assert sent[-1]["proxy_account_id"] == "kiro1"
    assert "token" not in sent[-1]
    original_id = row.get_attribute("data-acc-id")
    row.locator("[data-acc-provider]").select_option("proxy")
    assert row.get_attribute("data-acc-id") != original_id, "Another provider cannot inherit a stored OAuth token"
    assert row.locator("[data-acc-proxy]").input_value() == "", "Changing provider requires choosing a compatible account"
    assert row.locator("[data-acc-proxy] option[value=go1]").count() == 1
    row.locator("[data-acc-proxy]").select_option("go1")
    row.locator("[data-acc-reload]").click()
    page.wait_for_function("!document.querySelector('[data-acc-reload]').disabled")
    assert row.locator("[data-acc-proxy]").input_value() == "go1"
    row.locator("[data-acc-provider]").select_option("api_budget")
    assert row.locator("[data-acc-proxy] option[value=kiro1]").count() == 1
    assert row.locator("[data-acc-proxy] option[value=go1]").count() <= 1
    metrics = page.locator(".mp-modal").evaluate("""e => {const b=e.querySelector('.mp-modal-body'),r=b.getBoundingClientRect();return {width:e.getBoundingClientRect().width,viewport:innerWidth,scroll:b.scrollWidth,client:b.clientWidth,
      offenders:[...b.querySelectorAll('*')].filter(n=>n.getBoundingClientRect().right>r.right+1).slice(0,5).map(n=>({tag:n.tagName,cls:n.className,right:n.getBoundingClientRect().right})),
      overflow:[...b.querySelectorAll('*')].filter(n=>n.scrollWidth>n.clientWidth+1).map(n=>({tag:n.tagName,cls:n.className,scroll:n.scrollWidth,client:n.clientWidth,text:n.textContent.slice(0,60),padding:getComputedStyle(n).padding})).slice(0,20)}}""")

    output = Path(__file__).resolve().parents[1] / "build/round46-ui"
    output.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(output / f"panels-proxy-{width}.png"))
    assert metrics["width"] <= width and metrics["scroll"] <= metrics["client"] + 1, json.dumps(metrics)
    assert not metrics["overflow"], json.dumps(metrics)
    context.close()
    assert not errors
