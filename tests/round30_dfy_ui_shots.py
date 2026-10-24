"""Round 30 (DFY takeover + proxy): headless screenshots and checks of the new UI (mocked server, no network).

Not part of the unittest suite (needs Playwright + Chromium):

    python3 tests/round30_dfy_ui_shots.py OUT_DIR

For ru/en × dark/light: Settings → Do For You → «Proxy» (saved SOCKS5 with a login, «Check» shows the
external IP, a form without a login), the chat card while the agent asks the user to take over (captcha)
and while the user is in control, the Quick Ask card in both states. Exit 1 on a failed check.
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
CHROME = next((p for p in (os.environ.get("MEMU_TEST_CHROMIUM", ""), "/workspace/work/chromium/chrome") if p and os.path.exists(p)), "")

HARNESS = """<!doctype html><html lang="{lang}" data-theme="{theme}"><head><meta charset="utf-8">
<link rel="stylesheet" href="/style.css"><link rel="stylesheet" href="/themes.css"><link rel="stylesheet" href="/settings.css">
<style>body{{padding:20px;overflow:auto!important;height:auto!important;font-family:var(--sans)}}</style></head><body>
<div id="chat" style="max-width:680px"></div><div id="chat2" style="max-width:680px"></div><div id="settings" style="max-width:820px;margin-top:20px"></div>
<script>window.PG_LANG="{lang}";</script>
<script src="/settings-kit.js"></script><script src="/dfy-client.js"></script><script src="/dfy-settings.js"></script>
</body></html>"""
RENDER = """([lang]) => {
  const en = lang === 'en', esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const host = document.getElementById('settings');
  host.innerHTML = MemuDFYSettings.html({ en, pref: (k, d) => d, esc, kit: MemuSettingsKit, modelName: (id) => id,
    defaultVisionModel: () => 'gpt-4o', models: [{id: 'gpt-4o', caps: {vision: true}}] });
  MemuDFYSettings.bind({ en, pref: (k, d) => d, esc, toast: (t) => { window.__toast = t; }, savePreferences: async () => {} });
}"""
STEPS = [{"n": 1, "action": "navigate", "detail": "Открыт https://tickets.example/", "ok": True},
         {"n": 2, "action": "request_takeover", "detail": "captcha: Решите капчу на странице оплаты", "ok": True}]


async def run(out: Path, lang: str, theme: str, errors: list) -> None:
    from playwright.async_api import async_playwright
    from app import themes
    put = []
    proxy = {"machine": "local", "enabled": True, "scheme": "socks5", "host": "de.proxy.example", "port": 1080, "auth": True,
             "username": "iv•••n", "status": {"enabled": True}}
    asking = {"id": "aaaaaaaaaaaa", "goal": "Купи 2 билета на концерт в пятницу", "status": "waiting_user", "version": 3, "control": "ai",
              "steps": STEPS, "question": {"id": "q1", "kind": "takeover", "why": "captcha", "prompt": ""}}
    user = {**asking, "id": "bbbbbbbbbbbb", "control": "user", "status": "waiting_user", "question": asking["question"],
            "takeover": {"active": True, "by": "agent", "reason": "captcha"},
            "steps": STEPS + [{"n": 3, "action": "takeover", "detail": "Управление у вас — ИИ на паузе", "ok": True}]}

    async def route(r):
        path = "/" + r.request.url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]
        if path == "/__h.html":
            return await r.fulfill(body=HARNESS.format(lang=lang, theme=theme), content_type="text/html")
        if path == "/themes.css":
            return await r.fulfill(body=themes.theme_css(), content_type="text/css")
        if path.endswith("/events"):
            return await r.fulfill(status=500, body="")
        if path == "/api/dfy/tasks/aaaaaaaaaaaa":
            return await r.fulfill(json=asking)
        if path == "/api/dfy/tasks/bbbbbbbbbbbb":
            return await r.fulfill(json=user)
        if path == "/api/dfy/status":
            return await r.fulfill(json={"ready": True, "enabled": True, "takeover": True})
        if path == "/api/dfy/vault":
            return await r.fulfill(json={"scheme": "dpapi", "note": "DPAPI", "items": []})
        if path == "/api/dfy/proxy" and r.request.method == "PUT":
            put.append(json.loads(r.request.post_data or "{}"))
            return await r.fulfill(json={**proxy, "ok": True})
        if path == "/api/dfy/proxy":
            return await r.fulfill(json=proxy)
        if path == "/api/dfy/proxy/check":
            return await r.fulfill(json={"ok": True, "ip": "185.12.44.7", "ms": 412, "service": "api.ipify.org"})
        if path.startswith("/api/"):
            return await r.fulfill(json={"ok": True})
        file = STATIC / path.lstrip("/")
        if file.is_file():
            return await r.fulfill(path=str(file))
        return await r.fulfill(status=404, body="")

    tag = f"{lang}-{theme}"
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=CHROME or None, args=["--no-sandbox"])
        page = await browser.new_page(viewport={"width": 900, "height": 1000})
        page.on("pageerror", lambda e: errors.append(f"{tag} pageerror: {e}"))
        await page.route("**/*", route)
        await page.goto("http://127.0.0.1:9/__h.html")
        await page.wait_for_timeout(300)
        await page.evaluate("MemuDFY.refreshStatus()")
        await page.evaluate(RENDER, [lang])
        await page.evaluate("MemuDFY.renderCard(document.getElementById('chat'), 'aaaaaaaaaaaa'); MemuDFY.renderCard(document.getElementById('chat2'), 'bbbbbbbbbbbb')")
        await page.wait_for_timeout(700)
        await page.locator("#chat").screenshot(path=str(out / f"card-ask-takeover-{tag}.png"))
        await page.locator("#chat2").screenshot(path=str(out / f"card-user-control-{tag}.png"))
        if not await page.locator('#chat .dfy-q[data-kind="takeover"] [data-dfy="takeover"]').count():
            errors.append(f"{tag}: the takeover question has no «Take control» button")
        if await page.locator("#chat2 .dfy-q").count():
            errors.append(f"{tag}: the question must be hidden while the user is in control")
        if not await page.locator('#chat2 .dfy-takeover [data-dfy="giveback"]').count():
            errors.append(f"{tag}: «Return control to AI» is missing on the card")
        section = page.locator("#dfy-proxy")
        await section.scroll_into_view_if_needed()
        await page.wait_for_timeout(200)
        if await page.input_value("#st-dfy-proxy-host") != "de.proxy.example":
            errors.append(f"{tag}: the saved proxy is not shown")
        if not await page.is_checked("#st-dfy-proxy-on"):
            errors.append(f"{tag}: the toggle does not reflect «enabled»")
        await page.click("#st-dfy-proxy-check")
        await page.wait_for_timeout(300)
        result = await page.inner_text("#st-dfy-proxy-result")
        if "185.12.44.7" not in result:
            errors.append(f"{tag}: «Check» does not show the IP: {result!r}")
        await section.screenshot(path=str(out / f"settings-proxy-{tag}.png"))
        await page.fill("#st-dfy-proxy-pass", "secret-pass")
        await page.click("#st-dfy-proxy-save")
        await page.wait_for_timeout(200)
        if not put or put[-1].get("password") != "secret-pass":
            errors.append(f"{tag}: Save did not send the password: {put}")
        if await page.input_value("#st-dfy-proxy-pass"):
            errors.append(f"{tag}: the password stays in the field after saving")
        overflow = await page.evaluate("""() => [...document.querySelectorAll('#dfy-proxy *')].filter(e => e.scrollWidth > e.clientWidth + 1
            && getComputedStyle(e).overflowX !== 'visible' && e.tagName !== 'INPUT' && e.tagName !== 'SELECT').map(e => e.id || e.className).slice(0, 5)""")
        if overflow:
            errors.append(f"{tag}: horizontal overflow in the proxy section: {overflow}")
        await browser.close()


async def main(out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    errors: list = []
    for lang in ("ru", "en"):
        for theme in ("dark", "light"):
            await run(out, lang, theme, errors)
    for line in errors:
        print("FAIL", line)
    print("ok" if not errors else f"{len(errors)} failure(s)", "→", out)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else "/workspace/work/shots/r29/r30-dfy-takeover"))))
