"""Round 25 (DFY): headless checks + screenshots of the DFY forms (mocked server, no network).

Not part of the unittest suite (needs Playwright + Chromium):

    python3 tests/round25_dfy_ui_shots.py OUT_DIR [ru|en]

Checks (exit 1 on any failure):
* chat card (dfy-client.js): the one-time code form autofocuses, digits only, auto-sends a full code as
  {code} and leaves nothing in the page; phone validation (country code) + formatting, {secret:{fields}};
* Settings → Do For You: only vision models are listed; a saved non-vision model is shown with a warning
  and «Default»; no vision models → a «how to fix» note; changing the model updates the page in place
  (the same DOM — no full re-render, no white flash); vault edit opens the form for the entry;
* Quick Ask card (quick-dfy.js): all fields of a personal-data question (not only 4), the code form,
  the confirm total.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
STATIC = ROOT / "app" / "pgstatic"
CHROME = next((p for p in ("/usr/bin/chromium", os.path.expanduser("~/.cache/ms-playwright/chromium-1134/chrome-linux/chrome"),
                           "/workspace/work/chromium/chrome") if os.path.exists(p)), "")
SHOW = "QuickAsk.onShow({fresh:true, maxHeight:640, rounded:true, corner:'dwm', radius:8})"

HARNESS = """<!doctype html><html lang="{lang}" data-theme="dark"><head><meta charset="utf-8">
<link rel="stylesheet" href="/style.css"><link rel="stylesheet" href="/themes.css"><link rel="stylesheet" href="/settings.css">
<style>body{{padding:20px;overflow:auto!important;height:auto!important}}</style></head><body>
<div id="chat" style="max-width:680px"></div><div id="settings" style="max-width:820px;margin-top:20px"></div>
<script>window.PG_LANG="{lang}";</script>
<script src="/settings-kit.js"></script><script src="/dfy-client.js"></script><script src="/dfy-settings.js"></script>
</body></html>"""
QUESTIONS = {
    "code": {"id": "q1", "kind": "code", "prompt": "Введите код из SMS для подтверждения брони", "channel": "sms",
             "sent_to": "+370 •••• ••67", "length": 6, "numeric": True, "can_resend": True, "expires_in": 600, "domain": "lokys.lt"},
    "phone": {"id": "q2", "kind": "secret", "prompt": "Телефон для брони", "secret_kind": "phone", "fields": ["phone"],
              "domain": "lokys.lt", "existing": [{"id": "s1", "mask": "+7 •••• ••67"}]},
    "identity": {"id": "q3", "kind": "secret", "prompt": "Данные гостя", "secret_kind": "identity",
                 "fields": ["last_name", "first_name", "middle_name", "birth_date", "email", "phone"], "domain": "lokys.lt", "existing": []},
    "confirm": {"id": "q4", "kind": "confirm", "prompt": "Забронировать столик на 2 персоны, пт 19:00, депозит", "amount": "20 €"},
}
VAULT = {"scheme": "dpapi", "note": "Windows DPAPI", "items": [
    {"id": "s1", "kind": "phone", "label": "", "mask": "+7 •••• ••67", "persistent": True, "fields": ["phone"]},
    {"id": "s2", "kind": "address", "label": "Дом", "mask": "доставка: Вильнюс, Литва, •••", "persistent": True, "fields": ["city", "country", "street"]},
    {"id": "s3", "kind": "card", "label": "", "mask": "Visa •••• 4242", "persistent": True, "fields": ["number"]}]}
MODELS = [{"id": "gpt-4o", "caps": {"vision": True}}, {"id": "deepseek-chat", "caps": {"vision": False}},
          {"id": "claude-sonnet", "caps": {"vision": True}}]
RENDER_SETTINGS = """([models, prefs, lang]) => {
  const en = lang === 'en', esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const pref = (k, d) => (k in prefs ? prefs[k] : d);
  window.__saved = [];
  const host = document.getElementById('settings');
  host.innerHTML = MemuDFYSettings.html({ en, pref, esc, kit: MemuSettingsKit, modelName: (id) => id,
    defaultVisionModel: () => (models.find(m => m.caps.vision) || {}).id || '', models });
  MemuDFYSettings.bind({ en, pref, esc, toast: () => {}, savePreferences: async (p) => { window.__saved.push(p); } });
  window.__marker = host.firstElementChild;
}"""

async def main(out: Path, lang: str) -> int:
    from playwright.async_api import async_playwright
    from app import themes
    out.mkdir(parents=True, exist_ok=True)
    errors: list = []
    state = {"q": None, "answers": [], "version": 1}
    quick_task = {"id": "t1", "goal": "Забронируй столик", "status": "waiting_user", "version": 1, "steps": [{"n": 1, "action": "navigate", "detail": "lokys.lt"}],
                  "question": {**QUESTIONS["identity"], "fields": ["last_name", "first_name", "middle_name", "birth_date", "email", "phone"]}}

    def check(cond, message):
        if not cond:
            errors.append(message)

    async def route(r):
        path = "/" + r.request.url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]
        if path == "/__h.html":
            return await r.fulfill(body=HARNESS.format(lang=lang), content_type="text/html")
        if path == "/themes.css":
            return await r.fulfill(body=themes.theme_css(), content_type="text/css")
        if path.endswith("/events"):
            return await r.fulfill(status=500, body="")
        if path.endswith("/answer"):
            state["answers"].append(json.loads(r.request.post_data or "{}"))
            return await r.fulfill(json={"ok": True})
        if path == "/api/dfy/tasks/abcdef123456":
            return await r.fulfill(json={"id": "abcdef123456", "goal": "Забронируй столик в Lokys на 2 персоны в пятницу 19:00",
                                         "status": "waiting_user", "version": state["version"], "question": state["q"],
                                         "steps": [{"n": 1, "action": "navigate", "detail": "Открыт https://lokys.lt", "ok": True}]})
        if path == "/api/dfy/tasks/t1":
            return await r.fulfill(json=quick_task)
        if path == "/api/dfy/status":
            return await r.fulfill(json={"ready": True, "enabled": True, "model": "gpt-4o", "model_has_vision": True})
        if path == "/api/dfy/vault":
            return await r.fulfill(json=VAULT)
        if path == "/api/quick/config":
            return await r.fulfill(json={"prefs": {"quick_ask_model": "m1", "language": lang}, "status": {"native": True},
                                         "ui": {"onboarded": True}, "endpoint": {"source": "proxy"}})
        if path == "/api/quick/history":
            return await r.fulfill(json={"items": [], "enabled": True})
        if path.startswith("/api/"):
            return await r.fulfill(json={"ok": True})
        file = STATIC / path.lstrip("/")
        if path.endswith("/"):
            file = file / "index.html"
        if file.is_file():
            return await r.fulfill(path=str(file))
        return await r.fulfill(status=404, body="")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=CHROME or None, args=["--no-sandbox"])
        page = await browser.new_page(viewport={"width": 900, "height": 900})
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        await page.route("**/*", route)
        await page.goto("http://127.0.0.1:9/__h.html")
        await page.wait_for_timeout(300)
        await page.evaluate("MemuDFY.renderCard(document.getElementById('chat'), 'abcdef123456')")

        async def ask(name):
            state["q"] = QUESTIONS[name]
            state["version"] += 1
            await page.wait_for_timeout(1900)
            await page.locator("#chat").screenshot(path=str(out / f"card-{name}-{lang}.png"))

        await ask("code")
        check(await page.evaluate("!!document.activeElement && document.activeElement.hasAttribute('data-code')"), "the code field gets focus")
        await page.keyboard.type("48 29 1a3")
        await page.wait_for_timeout(300)
        check(state["answers"][-1:] == [{"code": "482913"}], f"a full code is sent as {{code}}: {state['answers']}")
        check("482913" not in await page.content(), "the code is not left in the page")

        await ask("phone")
        await page.fill("[data-field=phone]", "861234567")
        await page.click("[data-dfy-secret] button[type=submit]")
        await page.wait_for_timeout(200)
        check(bool((await page.inner_text("[data-dfy-secret] .dfy-err")).strip()), "a phone without the country code is refused")
        await page.locator("#chat").screenshot(path=str(out / f"card-phone-error-{lang}.png"))
        await page.fill("[data-field=phone]", "")
        await page.type("[data-field=phone]", "+79161234567")
        check((await page.input_value("[data-field=phone]")).startswith("+7 916"), "the phone is formatted while typing")
        await page.click("[data-dfy-secret] button[type=submit]")
        await page.wait_for_timeout(300)
        sent = (state["answers"][-1] or {}).get("secret") or {}
        check(sent.get("fields") == {"phone": "+79161234567"} and sent.get("kind") == "phone", f"the phone goes to the vault: {sent}")

        await ask("identity")
        check(await page.locator("[data-dfy-secret] [data-field]").count() == 6, "every requested field is shown")
        await ask("confirm")
        check("20 €" in await page.inner_text("#chat"), "the confirm shows the total")

        await page.evaluate(RENDER_SETTINGS, [MODELS, {"dfy_model": "deepseek-chat"}, lang])
        await page.wait_for_timeout(400)
        values = await page.eval_on_selector_all("#st-dfy-model option", "os => os.map(o => o.value)")
        check(values == ["", "deepseek-chat", "claude-sonnet", "gpt-4o"], f"only vision models (+ the stale saved one): {values}")
        check(await page.locator("#st-dfy-model-reset").count() == 1, "the stale model has a «Default» fix")
        await page.locator("#settings").screenshot(path=str(out / f"settings-stale-{lang}.png"))
        await page.select_option("#st-dfy-model", "claude-sonnet")
        await page.wait_for_timeout(200)
        check(await page.evaluate("window.__marker === document.getElementById('settings').firstElementChild"), "changed in place (no re-render)")
        check(await page.evaluate("getComputedStyle(document.getElementById('st-dfy-model-note')).display") == "none", "the warning goes away")
        check(await page.evaluate("window.__saved") == [{"dfy_model": "claude-sonnet"}], "the model is saved")
        check(await page.eval_on_selector_all("#st-dfy-model option", "os => os.map(o => o.value)") == ["", "claude-sonnet", "gpt-4o"], "the stale entry is gone")
        await page.click("[data-dfy-vault-edit=s2]")
        await page.wait_for_timeout(200)
        check(await page.locator("#st-dfy-vault-fields [data-field=street]").count() == 1, "editing opens the address form")
        await page.locator("#dfy-vault").screenshot(path=str(out / f"settings-vault-edit-{lang}.png"))
        await page.evaluate(RENDER_SETTINGS, [[m for m in MODELS if not m["caps"]["vision"]], {"dfy_model": ""}, lang])
        await page.wait_for_timeout(300)
        check(await page.eval_on_selector_all("#st-dfy-model option", "os => os.length") == 1, "no non-vision models listed")
        check("Proxy" in await page.inner_text("#st-dfy-model-note"), "how to fix is explained")
        await page.locator("#dfy-main").screenshot(path=str(out / f"settings-none-{lang}.png"))

        quick = await browser.new_page(viewport={"width": 680, "height": 640}, device_scale_factor=1)
        quick.on("pageerror", lambda e: errors.append(f"quick pageerror: {e}"))
        await quick.route("**/*", route)
        await quick.add_init_script("localStorage.setItem('memu_quick_dfy_tasks', JSON.stringify(['t1']))")
        await quick.goto("http://127.0.0.1:9/quick/?native=1")
        await quick.wait_for_timeout(600)
        await quick.evaluate(SHOW)
        await quick.wait_for_timeout(1500)
        check(await quick.locator(".qdfy-card [data-field]").count() == 6, "quick: all fields of the question")
        await quick.screenshot(path=str(out / f"quick-identity-{lang}.png"))
        quick_task.update(question={**QUESTIONS["code"]}, version=2)
        await quick.wait_for_timeout(2200)
        check(await quick.locator(".qdfy-card [data-code]").count() == 1, "quick: the code form")
        await quick.screenshot(path=str(out / f"quick-code-{lang}.png"))
        quick_task.update(question={**QUESTIONS["confirm"]}, version=3)
        await quick.wait_for_timeout(2200)
        check("20 €" in await quick.inner_text(".qdfy-card"), "quick: the confirm total")
        await browser.close()
    for message in errors:
        print("FAIL:", message)
    print(f"{len(errors)} problem(s)")
    return 1 if errors else 0

if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/workspace/work/shots/r25/dfy")
    sys.exit(asyncio.run(main(target, sys.argv[2] if len(sys.argv) > 2 else "ru")))
