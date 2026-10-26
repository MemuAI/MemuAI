"""Round 30 (DFY on machines): headless screenshots and checks of the new UI (mocked server, no network).

Not part of the unittest suite (needs Playwright + Chromium):

    python3 tests/round30_dfy_remote_shots.py OUT_DIR

For ru/en × dark/light: Settings → Do For You → «Remote machines» (list, the add form with the
one-line install command, the machine's proxy), and the chat card of a machine task (command
output, the dangerous-step confirmation with the command). Exit 1 on a failed check.
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
<link rel="stylesheet" href="/dfy-remote.css">
<style>body{{padding:20px;overflow:auto!important;height:auto!important;font-family:var(--sans)}}</style></head><body>
<div class="composer" style="max-width:680px;margin-bottom:12px"><div class="composer-tools" style="display:flex;gap:6px"></div></div>
<div id="chat" style="max-width:680px"></div><div id="chat2" style="max-width:680px"></div>
<div class="sx-shell" style="display:block;max-width:820px;margin-top:20px"><div class="settings-body" style="padding:0"><div id="settings"></div></div></div>
<script>window.PG_LANG="{lang}";</script>
<script src="/settings-kit.js"></script><script src="/dfy-remote.js"></script>
<script src="/dfy-client.js"></script><script src="/dfy-settings.js"></script>
</body></html>"""
RENDER = """([lang]) => {
  const en = lang === 'en', esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const host = document.getElementById('settings');
  host.innerHTML = MemuDFYSettings.html({ en, pref: (k, d) => (k === 'dfy_local_system' ? true : d), esc, kit: MemuSettingsKit,
    modelName: (id) => id, defaultVisionModel: () => 'gpt-4o', models: [{id: 'gpt-4o', caps: {vision: true}}] });
  MemuDFYSettings.bind({ en, pref: (k, d) => (k === 'dfy_local_system' ? true : d), esc,
    toast: (t) => { window.__toast = t; }, savePreferences: async () => {} });
}"""
MACHINES = [{"id": "m1", "name": "prod-web", "kind": "http", "host": "10.0.0.7", "port": 8765, "os": "linux",
             "os_label": "Linux", "user": "deploy", "roots": [], "agent_version": "1.0",
             "seen": 9e9, "online": True, "error": "", "created": 0, "default_cwd": "/srv"},
            {"id": "m2", "name": "backup", "kind": "ssh", "host": "backup.example.com", "port": 22, "os": "linux",
             "os_label": "Linux", "user": "root", "roots": ["/srv/data"], "agent_version": "", "seen": 1.0,
             "online": False, "error": "Нет связи с машиной: таймаут", "created": 0, "default_cwd": ""}]
STEPS = [{"n": 1, "action": "sysinfo", "detail": "Debian GNU/Linux 13 (trixie) · aarch64", "ok": True},
         {"n": 2, "action": "shell", "detail": "$ systemctl status nginx → код 0", "ok": True, "exit_code": 0,
          "output": "● nginx.service - A high performance web server\n   Active: active (running) since Fri 21:04:11 UTC\n  Process: 812 ExecStart=/usr/sbin/nginx -g daemon on; master_process on;\n   Memory: 12.4M\n"}]

async def run(out: Path, lang: str, theme: str, errors: list) -> None:
    from playwright.async_api import async_playwright
    from app import themes
    target = {"id": "m1", "name": "prod-web", "kind": "http", "os": "linux"}
    running = {"id": "aaaaaaaaaaaa", "goal": "Проверь nginx на сервере и почисти старые логи", "status": "running",
               "version": 3, "steps": STEPS, "target": target, "machine_steps": 2}
    asking = {**running, "id": "bbbbbbbbbbbb", "status": "waiting_user",
              "steps": STEPS + [{"n": 3, "action": "file_delete", "detail": "Удаление «/var/log/app/old» со всем содержимым", "ok": True}],
              "question": {"id": "q1", "kind": "confirm", "machine": "prod-web", "danger": True, "action": "file_delete",
                           "detail": "/var/log/app/old",
                           "prompt": "Удаление «/var/log/app/old» со всем содержимым\n\nУдалить «/var/log/app/old» на «prod-web» со всем содержимым?"}}

    async def route(r):
        path = "/" + r.request.url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]
        if path == "/__h.html":
            return await r.fulfill(body=HARNESS.format(lang=lang, theme=theme), content_type="text/html")
        if path == "/themes.css":
            return await r.fulfill(body=themes.theme_css(), content_type="text/css")
        if path.endswith("/events"):
            return await r.fulfill(status=500, body="")
        if path == "/api/dfy/tasks/aaaaaaaaaaaa":
            return await r.fulfill(json=running)
        if path == "/api/dfy/tasks/bbbbbbbbbbbb":
            return await r.fulfill(json=asking)
        if path == "/api/dfy/status":
            return await r.fulfill(json={"ready": True, "enabled": True, "model": "gpt-4o", "local_system": True,
                                         "machines": [{k: m[k] for k in ("id", "name", "kind", "os", "os_label", "online")} for m in MACHINES]})
        if path == "/api/dfy/remote/machines":
            return await r.fulfill(json={"machines": MACHINES, "local_system": True,
                                         "targets": [{"id": "local", "name": "Этот компьютер" if lang != "en" else "This computer",
                                                      "kind": "local", "os": "linux", "online": True}] + MACHINES
                                         + [{**MACHINES[0], "id": "m1/browser", "machine": "m1", "browser": True,
                                             "name": "prod-web — " + ("browser" if lang == "en" else "браузер")}]})
        if path == "/api/dfy/remote/install":
            from app.dfy_remote import install_command
            return await r.fulfill(json=install_command())
        if path == "/api/dfy/proxy":
            return await r.fulfill(json={"machine": "m1", "enabled": True, "scheme": "socks5", "host": "de.proxy.example",
                                         "port": 1080, "auth": True})
        if path.endswith("/proxy/check"):
            return await r.fulfill(json={"ok": True, "ip": "185.12.44.7", "ms": 318})
        if path == "/api/dfy/vault":
            return await r.fulfill(json={"scheme": "dpapi", "note": "DPAPI", "items": []})
        if path.startswith("/api/"):
            return await r.fulfill(json={"ok": True})
        file = STATIC / path.lstrip("/")
        if file.is_file():
            return await r.fulfill(path=str(file))
        return await r.fulfill(status=404, body="")

    tag = f"{lang}-{theme}"
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=CHROME or None, args=["--no-sandbox"])
        page = await browser.new_page(viewport={"width": 900, "height": 1100})
        page.on("pageerror", lambda e: errors.append(f"{tag} pageerror: {e}"))
        await page.route("**/*", route)
        await page.goto("http://127.0.0.1:9/__h.html")
        await page.wait_for_timeout(350)
        await page.evaluate("MemuDFY.refreshStatus()")
        await page.evaluate("MemuDFYRemote.refresh()")
        await page.evaluate(RENDER, [lang])
        await page.evaluate("MemuDFY.renderCard(document.getElementById('chat'), 'aaaaaaaaaaaa');"
                            "MemuDFY.renderCard(document.getElementById('chat2'), 'bbbbbbbbbbbb')")
        await page.wait_for_timeout(700)
        await page.locator("#chat").screenshot(path=str(out / f"card-machine-{tag}.png"))
        await page.locator("#chat2").screenshot(path=str(out / f"card-confirm-{tag}.png"))
        if not await page.locator("#chat .dfy-chip--target").count():
            errors.append(f"{tag}: the machine chip is missing on the card")
        if "nginx.service" not in await page.inner_text("#chat .dfy-out pre"):
            errors.append(f"{tag}: the command output is not on the card")
        if await page.locator('#chat [data-dfy="takeover"]').count():
            errors.append(f"{tag}: «Take control» must not be offered for a machine task")
        if not await page.locator("#chat2 .dfy-q--machine .dfy-cmd-plain, #chat2 .dfy-q--machine .dfy-cmd").count():
            errors.append(f"{tag}: the confirmation shows no path/command")
        picker = page.locator("#dfy-target-picker")
        if await picker.is_hidden():
            errors.append(f"{tag}: the target picker next to the input is hidden although machines exist")
        await page.select_option("#dfy-target-picker select", "m1")
        if await page.evaluate("MemuDFYRemote.parseCommand('проверь диск')") != {"goal": "проверь диск", "target": "m1"}:
            errors.append(f"{tag}: the picked target is not used")
        if (await page.evaluate("MemuDFYRemote.parseCommand('@backup очисти /tmp')"))["target"] != "m2":
            errors.append(f"{tag}: «@machine …» is not parsed")
        await picker.screenshot(path=str(out / f"composer-picker-{tag}.png"))
        options = await page.eval_on_selector_all("#dfy-target-picker option", "els => els.map(e => e.value)")
        if "m1/browser" not in options or "m2/browser" in options:
            errors.append(f"{tag}: «browser on the machine» is offered only for agent machines: {options}")
        prompt = await page.evaluate("MemuDFY.promptBlock('ru')")
        if '"m1" = prod-web' not in prompt or '"local"' not in prompt:
            errors.append(f"{tag}: the machines are missing from the model prompt")
        section = page.locator("#dfy-machines")
        await section.scroll_into_view_if_needed()
        await page.wait_for_timeout(250)
        if "prod-web" not in await section.inner_text():
            errors.append(f"{tag}: the machine list is empty")
        await section.screenshot(path=str(out / f"settings-machines-{tag}.png"))
        await page.click("#st-dfy-machine-add")
        await page.wait_for_timeout(400)
        add = page.locator("#st-dfy-machine-form")
        if "MEMU1" not in await add.inner_text():
            errors.append(f"{tag}: the add form does not explain the pairing code")
        await add.screenshot(path=str(out / f"settings-add-{tag}.png"))
        await page.click('[data-dfy-tab="ssh"]')
        await page.wait_for_timeout(200)
        await add.screenshot(path=str(out / f"settings-add-ssh-{tag}.png"))
        await page.click('.dfy-machine[data-machine="m1"] [data-dfy-machine="proxy"]')
        await page.wait_for_timeout(400)
        await page.click('.dfy-machine[data-machine="m1"] [data-proxy-act="check"]')
        await page.wait_for_timeout(400)
        result = await page.inner_text('.dfy-machine[data-machine="m1"] [data-proxy-result]')
        if "185.12.44.7" not in result:
            errors.append(f"{tag}: the machine's proxy check shows no IP: {result!r}")
        await section.screenshot(path=str(out / f"settings-machine-proxy-{tag}.png"))
        overflow = await page.evaluate("""() => [...document.querySelectorAll('#dfy-machines *, #chat *, #chat2 *')]
            .filter(e => e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflowX === 'visible'
                      && !['INPUT','SELECT','PRE'].includes(e.tagName)).map(e => e.id || e.className).slice(0, 5)""")
        if overflow:
            errors.append(f"{tag}: horizontal overflow: {overflow}")
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
    sys.exit(asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else "/workspace/work/shots/r29/r30-dfy-remote-2"))))
